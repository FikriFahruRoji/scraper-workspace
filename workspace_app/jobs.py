"""One local job, durable SQLite checkpoints, and isolated platform profiles."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import unquote, urlparse
import re
import uuid

from workspace_app import sources

ACTIVE = ("running", "stopping")
LOGIN_DETECTED = "Sesi terdeteksi pada browser."


def _fmt(number):
    return f"{int(number):,}".replace(",", ".")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=lambda v: v.to_dict() if hasattr(v, "to_dict") else str(v))


def normalize_rows(rows, *, strip_internal=True):
    return [{str(k): v for k, v in (row.to_dict() if hasattr(row, "to_dict") else row).items()
             if not strip_internal or not str(k).startswith("_")} for row in rows]


PROJECT_MAX = 80
# Parameter keys that name what a job collected, in priority order.
SUBJECT_KEYS = ("keyword", "symbol", "url_input", "post_input", "query", "app_id",
                "username", "tweet_url")


KNOWN = tuple(sources.LABELS)
PREPROCESSING = "preprocessing"
MERGED = "gabungan"
# Merged datasets behave like scraped ones for input purposes (preprocessing, analysis).
INPUTS = (*KNOWN, MERGED)


def _marks(values):
    return ",".join("?" * len(values))


def clean_project(name):
    return " ".join(str(name or "").split())[:PROJECT_MAX].strip()


def _subject(value):
    value = " ".join(str(value).split())
    if value.startswith(("http://", "https://")):
        url = urlparse(value)
        place = re.search(r"/place/([^/]+)", url.path)
        parts = [unquote(part).replace("+", " ") for part in url.path.split("/") if part]
        value = unquote(place.group(1)).replace("+", " ") if place else (
            parts[-1] if parts else url.netloc)
    return value[:40].strip()


def default_project(source, params, when=None):
    """Readable automatic name: source · what was collected · date."""
    subject = next((_subject(params[key]) for key in SUBJECT_KEYS
                    if isinstance(params.get(key), str) and params[key].strip()), "")
    day = (when or datetime.now()).strftime("%d-%m-%Y")
    return clean_project(" · ".join(part for part in (sources.LABELS[source], subject, day) if part))


def local_directory():
    if override := os.environ.get("SCRAPER_WORKSPACE_DATA"):
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "ScraperWorkspace"
    return Path.home() / "Library" / "Application Support" / "ScraperWorkspace"


def lock_directory(path):
    """The OS releases this lock even after an unclean process exit."""
    handle = path.open("a+b")
    handle.seek(0)
    if not handle.read(1):
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError("Aplikasi sudah menggunakan folder riwayat ini.") from exc
    return handle


class JobManager:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._instance = lock_directory(self.directory / "instance.lock")
        self._lock = Lock()
        self._active = None
        self._stop = Event()
        self._thread = None
        self._login_status = {}
        try:
            with self.db() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL,
                        started TEXT NOT NULL, finished TEXT, params TEXT NOT NULL,
                        rows TEXT NOT NULL DEFAULT '[]', count INTEGER NOT NULL DEFAULT 0,
                        metadata TEXT NOT NULL DEFAULT '{}', message TEXT NOT NULL DEFAULT ''
                    )
                """)
                columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
                if "project" not in columns:
                    db.execute("ALTER TABLE jobs ADD COLUMN project TEXT NOT NULL DEFAULT ''")
                db.execute("CREATE INDEX IF NOT EXISTS jobs_project ON jobs(project)")
                for row in db.execute("SELECT id, source, params, started FROM jobs WHERE project=''").fetchall():
                    try:
                        name = default_project(row["source"], json.loads(row["params"]),
                                               datetime.fromisoformat(row["started"]).astimezone())
                    except (KeyError, ValueError, TypeError):
                        name = row["source"]
                    db.execute("UPDATE jobs SET project=? WHERE id=?", (name, row["id"]))
                db.execute("""
                    CREATE TABLE IF NOT EXISTS job_logs (
                        sequence INTEGER PRIMARY KEY,
                        job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                        message TEXT NOT NULL
                    )
                """)
                db.execute("CREATE INDEX IF NOT EXISTS job_logs_job ON job_logs(job_id, sequence)")
                db.execute("""
                    UPDATE jobs SET status='interrupted', finished=?, message=?
                    WHERE status IN ('running', 'stopping')
                """, (now(), "Aplikasi ditutup saat pekerjaan berlangsung."))
        except BaseException:
            self._instance.close()
            raise

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.directory / "history.sqlite3", timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def profile(self, source):
        return self.directory / "profiles" / source

    def snapshot(self):
        with self._lock:
            return (dict(self._active) if self._active else None, dict(self._login_status))

    def _log(self, job_id, message):
        with self.db() as db:
            db.execute("INSERT INTO job_logs(job_id, message) VALUES (?, ?)", (job_id, str(message)))

    def logs(self, job_id, limit=300):
        with self.db() as db:
            rows = db.execute(
                "SELECT message FROM job_logs WHERE job_id=? ORDER BY sequence DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [row["message"] for row in reversed(rows)]

    def _start_thread(self, source, job_id, login, target):
        self._stop = Event()
        self._active = {"source": source, "id": job_id, "login": login, "stopping": False}
        self._thread = Thread(target=target, daemon=True, name=f"scraper-{source}")
        try:
            self._thread.start()
        except Exception:
            self._active = None
            raise

    def start(self, source, params, *, project="", work=None):
        """Start one background job. `work(stop, log, checkpoint)` returns (rows, metadata, error);
        by default it runs the scraper for `source`."""
        project = clean_project(project) or default_project(source, params)
        if work is None:
            work = partial(self._scrape, source, dict(params))
        with self._lock:
            if self._active:
                raise RuntimeError("Tunggu pekerjaan aktif selesai.")
            job_id = uuid.uuid4().hex
            with self.db() as db:
                db.execute(
                    "INSERT INTO jobs (id, source, status, started, params, project) "
                    "VALUES (?, ?, 'running', ?, ?, ?)",
                    (job_id, source, now(), encode(params), project),
                )
            try:
                self._start_thread(source, job_id, False, lambda: self._run(job_id, work))
            except Exception as exc:
                with self.db() as db:
                    db.execute("UPDATE jobs SET status='failed', finished=?, message=? WHERE id=?",
                               (now(), str(exc), job_id))
                raise
            return job_id

    def login(self, source):
        if source not in sources.LOGINS:
            raise ValueError("Sumber ini tidak memakai profil login.")
        with self._lock:
            if self._active:
                raise RuntimeError("Tunggu pekerjaan aktif selesai.")
            self._login_status.pop(source, None)
            self._start_thread(source, None, True, lambda: self._run_login(source))

    def _run_login(self, source):
        try:
            result = sources.load(source).login(
                user_data_dir=str(self.profile(source)), real_chrome=False, stop_event=self._stop,
            )
            message = LOGIN_DETECTED if result.get("ok") else (
                result.get("message") or "Sesi belum terdeteksi."
            )
        except Exception as exc:
            message = str(exc)
        finally:
            with self._lock:
                self._login_status[source] = "Login dihentikan." if self._stop.is_set() else message
                self._active = None

    def stop(self):
        with self._lock:
            if self._active and not self._active["stopping"]:
                if not self._active["login"]:
                    with self.db() as db:
                        db.execute("UPDATE jobs SET status='stopping' WHERE id=?", (self._active["id"],))
                self._active["stopping"] = True
                self._stop.set()

    def _scrape(self, source, params, stop, log, checkpoint):
        return sources.execute(source, params, self.profile(source), stop, log, checkpoint)

    def start_preprocessing(self, params, rows, columns, config, resources, *, project):
        """Preprocess `rows` in the background and save the result as a job."""
        if not clean_project(project):
            raise ValueError("Isi nama proyek terlebih dahulu.")
        from workspace_app import preprocess

        def work(stop, log, checkpoint):
            log(f"Memproses {_fmt(len(rows))} baris dari kolom {', '.join(columns)}.")

            def progress(done, total, output):
                log(f"{_fmt(done)} dari {_fmt(total)} baris diproses.")
                checkpoint(output)

            output, report = preprocess.run(rows, columns, config, resources, progress=progress, stop=stop)
            log(f"Selesai: {_fmt(report['baris_keluar'])} baris disimpan.")
            return output, {"laporan": report}, None

        return self.start(PREPROCESSING, params, project=project, work=work)

    def start_merge(self, params, job_ids, *, project, tag_source=True, drop_duplicates=False):
        """Combine the rows of finished jobs into one new dataset, saved in the background."""
        if not clean_project(project):
            raise ValueError("Isi nama proyek terlebih dahulu.")
        if len(job_ids) < 2:
            raise ValueError("Pilih minimal dua dataset untuk digabungkan.")
        with self.db() as db:
            jobs = db.execute(f"SELECT id, source, params FROM jobs WHERE id IN ({_marks(job_ids)})",
                              list(job_ids)).fetchall()
        names = {job["id"]: sources.label(job["source"]) for job in jobs}
        tagged_merges = {job["id"] for job in jobs
                         if job["source"] == MERGED and json.loads(job["params"]).get("tandai", True)}

        def work(stop, log, checkpoint):
            merged, seen, dropped = [], set(), 0
            for number, job_id in enumerate(job_ids, 1):
                if stop.is_set():
                    break
                rows = self.input_rows([job_id])
                log(f"Dataset {number} dari {len(job_ids)}: {_fmt(len(rows))} baris.")
                for row in rows:
                    row = dict(row)
                    origin = row.pop("pekerjaan_asal", job_id[:8])
                    if drop_duplicates:
                        values = row
                        if job_id in tagged_merges:
                            values = {key: value for key, value in row.items() if key != "sumber_data"}
                        key = json.dumps(values, sort_keys=True, ensure_ascii=False, default=str)
                        if key in seen:
                            dropped += 1
                            continue
                        seen.add(key)
                    if tag_source:
                        row = {"sumber_data": names.get(job_id, ""), "pekerjaan_asal": origin, **row}
                    merged.append(row)
                checkpoint(merged)
            log(f"Selesai: {_fmt(len(merged))} baris digabungkan"
                + (f", {_fmt(dropped)} duplikat dibuang." if drop_duplicates else "."))
            return merged, {"duplikat_dibuang": dropped, "dataset": len(job_ids)}, None

        params = {**params, "tandai": tag_source, "buang_duplikat": drop_duplicates}
        return self.start(MERGED, params, project=project, work=work)

    def input_rows(self, job_ids, limit=None):
        """Rows of the given jobs, newest job first, tagged with `pekerjaan_asal`.

        With `limit`, only that many rows are decoded (for previews).
        """
        result = []
        with self.db() as db:
            jobs = db.execute(f"SELECT id FROM jobs WHERE id IN ({_marks(job_ids)}) "
                              "ORDER BY started DESC, rowid DESC", list(job_ids)).fetchall()
            for job in jobs:
                if limit is not None and len(result) >= limit:
                    break
                if limit is None:
                    raw = db.execute("SELECT rows FROM jobs WHERE id=?", (job["id"],)).fetchone()["rows"]
                    found = json.loads(raw)
                else:
                    found = [json.loads(row["value"]) for row in db.execute(
                        "SELECT value FROM json_each((SELECT rows FROM jobs WHERE id=?)) LIMIT ?",
                        (job["id"], limit - len(result)))]
                result.extend({"pekerjaan_asal": job["id"][:8], **row} for row in found)
        return result

    def input_text_columns(self, job_ids):
        """Find text columns across the selected jobs without loading all rows into Python."""
        with self.db() as db:
            return [row["key"] for row in db.execute(
                f"""SELECT DISTINCT field.key
                    FROM jobs, json_each(jobs.rows) AS record, json_each(record.value) AS field
                    WHERE jobs.id IN ({_marks(job_ids)}) AND field.type='text'
                        AND field.key != 'pekerjaan_asal'
                    ORDER BY field.key""", list(job_ids))]

    def _run(self, job_id, work):
        strip_internal = self._active["source"] not in (PREPROCESSING, MERGED)
        last_saved = float("-inf")
        last_rows = None
        row_count = 0

        def checkpoint(rows, *, force=False, metadata=None):
            nonlocal last_saved, last_rows, row_count
            last_rows = rows
            if (rows or metadata is not None) and (force or monotonic() - last_saved >= 2):
                data = normalize_rows(rows, strip_internal=strip_internal)
                with self.db() as db:
                    db.execute("UPDATE jobs SET rows=?, count=?, metadata=COALESCE(?, metadata) WHERE id=?",
                               (encode(data), len(data), encode(metadata) if metadata is not None else None, job_id))
                last_saved = monotonic()
                row_count = len(data)

        def log(message):
            self._log(job_id, message)

        metadata = None
        error = None
        try:
            rows, metadata, error = work(self._stop, log, checkpoint)
            if rows:
                checkpoint(rows, force=True)
            elif last_rows:
                checkpoint(last_rows, force=True)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            log(error)
            if last_rows:
                try:
                    checkpoint(last_rows, force=True)
                except Exception as write_error:
                    log(write_error)
        finally:
            with self._lock:
                if str(error) in {"login_required", "not_logged_in", "logged_out_limited"}:
                    source = self._active["source"]
                    if source in sources.LOGINS:
                        self._login_status[source] = "Sesi belum terdeteksi."
                status = "stopped" if self._stop.is_set() else (
                    ("partial" if row_count else "failed") if error else "completed"
                )
                try:
                    with self.db() as db:
                        db.execute("""
                            UPDATE jobs SET status=?, finished=?, metadata=COALESCE(?, metadata), message=? WHERE id=?
                        """, (status, now(), encode(metadata) if metadata is not None else None, str(error or ""), job_id))
                finally:
                    self._active = None

    def list_jobs(self, source=None, limit=200, project=None):
        # Without a source: scraping jobs only. Jobs of sources that no longer
        # exist stay in the database but are not listed.
        where, values = ([f"source IN ({_marks(KNOWN)})"], list(KNOWN)) if not source else (["source=?"], [source])
        if project is not None:
            where.append("project=?")
            values.append(project)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self.db() as db:
            rows = db.execute(
                f"SELECT id, source, status, started, finished, count, project FROM jobs {clause} "
                "ORDER BY started DESC, rowid DESC LIMIT ?", (*values, limit),
            )
            return [dict(row) for row in rows]

    def projects(self, source=None):
        """Project names, most recently used first."""
        sources_ = [source] if source else list(KNOWN)
        with self.db() as db:
            rows = db.execute(f"SELECT project FROM jobs WHERE project != '' AND source IN ({_marks(sources_)}) "
                              "GROUP BY project ORDER BY MAX(started) DESC", sources_)
            return [row["project"] for row in rows]

    def _inventory_filter(self, projects, sources_):
        """Finished jobs that saved rows, limited to the chosen sources and projects."""
        known = [name for name in (sources_ or INPUTS) if name in INPUTS]
        where = [f"status NOT IN ({_marks(ACTIVE)})", "count > 0", f"source IN ({_marks(known)})"]
        values = [*ACTIVE, *known]
        if projects:
            where.append(f"project IN ({_marks(projects)})")
            values.extend(projects)
        return " AND ".join(where), values

    def inventory_options(self, sources_=()):
        """Sources and projects that have data; projects follow the chosen sources."""
        clause, values = self._inventory_filter((), ())
        with self.db() as db:
            present = {row["source"] for row in db.execute(f"SELECT DISTINCT source FROM jobs WHERE {clause}", values)}
            clause, values = self._inventory_filter((), sources_)
            names = [row["project"] for row in db.execute(
                f"SELECT project FROM jobs WHERE {clause} GROUP BY project ORDER BY MAX(started) DESC", values)]
        return [name for name in INPUTS if name in present], names

    def inventory(self, projects=(), sources_=()):
        """Per-source totals: jobs, saved rows, projects, and the latest job."""
        clause, values = self._inventory_filter(projects, sources_)
        with self.db() as db:
            rows = db.execute(
                f"SELECT source, COUNT(*) AS jobs, SUM(count) AS rows, COUNT(DISTINCT project) AS projects, "
                f"MAX(started) AS latest FROM jobs WHERE {clause} GROUP BY source", values,
            ).fetchall()
        found = {row["source"]: dict(row) for row in rows}
        return [found[name] for name in INPUTS if name in found]

    def sample_rows(self, source, projects=(), limit=10):
        """First rows of the newest jobs, each tagged with its project, job, and status.

        Only the rows needed are decoded, so a 100k-row job is never loaded whole.
        """
        clause, values = self._inventory_filter(projects, [source])
        result = []
        with self.db() as db:
            jobs = db.execute(f"SELECT id, project, started, status, count FROM jobs WHERE {clause} "
                              "ORDER BY started DESC, rowid DESC", values).fetchall()
            for job in jobs:
                if len(result) >= limit:
                    break
                found = db.execute(
                    "SELECT value FROM json_each((SELECT rows FROM jobs WHERE id=?)) LIMIT ?",
                    (job["id"], limit - len(result)),
                ).fetchall()
                result.extend({"Proyek": job["project"], "Pekerjaan": job["id"][:8], "Dimulai": job["started"],
                               "Status pekerjaan": job["status"], **json.loads(row["value"])} for row in found)
        return result

    def rename(self, job_id, project):
        project = clean_project(project)
        if not project:
            raise ValueError("Nama proyek tidak boleh kosong.")
        with self.db() as db:
            db.execute("UPDATE jobs SET project=? WHERE id=?", (project, job_id))

    def saved_count(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT count FROM jobs WHERE id=?", (job_id,)).fetchone()
        return row["count"] if row else 0

    def get(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("params", "rows", "metadata"):
            result[key] = json.loads(result[key])
        return result

    def delete(self, job_id=None, sources_=None):
        """Delete one finished job, or all finished jobs (optionally only of `sources_`)."""
        with self._lock, self.db() as db:
            if job_id:
                db.execute("DELETE FROM jobs WHERE id=? AND status NOT IN ('running', 'stopping')", (job_id,))
            elif sources_:
                db.execute(f"DELETE FROM jobs WHERE status NOT IN ('running', 'stopping') "
                           f"AND source IN ({_marks(sources_)})", list(sources_))
            else:
                db.execute("DELETE FROM jobs WHERE status NOT IN ('running', 'stopping')")
