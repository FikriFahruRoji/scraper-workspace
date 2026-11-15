"""One local job, durable SQLite checkpoints, and isolated platform profiles."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
from time import monotonic
import uuid

from workspace_app import sources

ACTIVE = ("running", "stopping")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=lambda v: v.to_dict() if hasattr(v, "to_dict") else str(v))


def normalize_rows(rows):
    return [{k: v for k, v in (row.to_dict() if hasattr(row, "to_dict") else row).items()
             if not k.startswith("_")} for row in rows]


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

    def start(self, source, params, *, documents=()):
        with self._lock:
            if self._active:
                raise RuntimeError("Tunggu pekerjaan aktif selesai.")
            job_id = uuid.uuid4().hex
            with self.db() as db:
                db.execute(
                    "INSERT INTO jobs (id, source, status, started, params) VALUES (?, ?, 'running', ?, ?)",
                    (job_id, source, now(), encode(params)),
                )
            try:
                self._start_thread(source, job_id, False, lambda: self._run(job_id, source, dict(params), tuple(documents)))
            except Exception as exc:
                with self.db() as db:
                    db.execute("UPDATE jobs SET status='failed', finished=?, message=? WHERE id=?",
                               (now(), str(exc), job_id))
                raise
            return job_id

    def login(self, source):
        if source not in sources.PROFILES:
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
            message = "Sesi terdeteksi pada browser." if result.get("ok") else (
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

    def _run(self, job_id, source, params, documents):
        last_saved = float("-inf")
        last_rows = None
        row_count = 0

        def checkpoint(rows, *, force=False):
            nonlocal last_saved, last_rows, row_count
            last_rows = rows
            if rows and (force or monotonic() - last_saved >= 2):
                data = normalize_rows(rows)
                with self.db() as db:
                    db.execute("UPDATE jobs SET rows=?, count=? WHERE id=?",
                               (encode(data), len(data), job_id))
                last_saved = monotonic()
                row_count = len(data)

        def log(message):
            self._log(job_id, message)

        metadata = {}
        error = None
        try:
            options = {"documents": documents} if documents else {}
            rows, metadata, error = sources.execute(
                source, params, self.profile(source), self._stop, log, checkpoint, **options,
            )
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
                status = "stopped" if self._stop.is_set() else (
                    ("partial" if row_count else "failed") if error else "completed"
                )
                try:
                    with self.db() as db:
                        db.execute("""
                            UPDATE jobs SET status=?, finished=?, metadata=?, message=? WHERE id=?
                        """, (status, now(), encode(metadata), str(error or ""), job_id))
                finally:
                    self._active = None

    def list_jobs(self, source=None, limit=200):
        with self.db() as db:
            fields = "id, source, status, started, finished, count"
            if source:
                rows = db.execute(f"SELECT {fields} FROM jobs WHERE source=? ORDER BY started DESC, rowid DESC LIMIT ?",
                                  (source, limit))
            else:
                rows = db.execute(f"SELECT {fields} FROM jobs ORDER BY started DESC, rowid DESC LIMIT ?", (limit,))
            return [dict(row) for row in rows]

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

    def delete(self, job_id=None):
        with self._lock, self.db() as db:
            if job_id:
                db.execute("DELETE FROM jobs WHERE id=? AND status NOT IN ('running', 'stopping')", (job_id,))
            else:
                db.execute("DELETE FROM jobs WHERE status NOT IN ('running', 'stopping')")
