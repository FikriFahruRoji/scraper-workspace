"""Proses Berjalan: what the running job is doing, its log, and a Hentikan button."""
from datetime import datetime, timezone
import re

import streamlit as st

from workspace_app.i18n import tr

from workspace_app import ui
from workspace_app.sources import label

# Result page of a job kind when its key in ui.pages() differs from the job source.
PAGE_OF = {"bertopic": "topik-waktu"}

PROGRESS_RE = re.compile(r"^([\d.]+) dari ([\d.]+) baris")


def progress_from_logs(logs):
    """(done, total) from the newest 'X dari Y baris' log line, or None when no line reports it."""
    for line in reversed(logs):
        match = PROGRESS_RE.match(line)
        if match:
            done, total = (int(value.replace(".", "")) for value in match.groups())
            if total:
                return done, total
    return None


def elapsed(started):
    moment = ui.parse_time(started)
    return ui.duration(started, datetime.now(timezone.utc).isoformat()) if moment else "-"


def process_page(manager):
    ui.page_header(tr("Proses Berjalan"), tr("Pantau pekerjaan yang sedang berjalan, lengkap dengan lognya"))
    live(manager)


def stop_button(manager, stopping):
    if st.button(tr("Hentikan"), icon=":material/stop_circle:", disabled=stopping, key="process-stop"):
        manager.stop()
        st.rerun()


@st.fragment(run_every=1)
def live(manager):
    active, _ = manager.snapshot()
    if not active:
        finished(manager)
        return
    st.session_state["proc-last"] = active["id"]
    name = label(active["source"])
    if active["login"]:
        with st.container(key="panel-process"):
            st.markdown(f"**{name}**")
            if active["stopping"]:
                st.badge(tr("Sedang menghentikan"), icon=":material/pause_circle:", color="orange")
            else:
                st.badge(tr("Login di jendela browser"), icon=":material/login:", color="blue")
            stop_button(manager, active["stopping"])
        return
    job = manager.get(active["id"])
    logs = manager.logs(active["id"])
    with st.container(key="panel-process"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**{job['project'] or name}**")
            if active["stopping"]:
                st.badge(tr("Sedang menghentikan"), icon=":material/pause_circle:", color="orange")
            else:
                ui.status_badge("running")
        st.caption(tr('{0} · mulai {1}', name, ui.long_date(job['started'])))

        cols = st.columns(2)
        cols[0].metric(tr("Baris tersimpan"), ui.fmt(manager.saved_count(active["id"])), border=True)
        cols[1].metric(tr("Sudah berjalan"), elapsed(job["started"]), border=True)
        if progress := progress_from_logs(logs):
            done, total = progress
            st.progress(done / total, text=tr('{0} dari {1} baris diproses', ui.fmt(done), ui.fmt(total)))

        st.markdown(tr("**Log proses**"))
        if logs:
            st.code("\n".join(logs[-60:]), language="text", height=260)
            st.caption(tr("Menampilkan 60 pesan terakhir. Halaman ini diperbarui tiap detik."))
        else:
            st.caption(tr("Menyiapkan proses… log pertama akan muncul sebentar lagi."))
        stop_button(manager, active["stopping"])
        if active["source"] in ("preprocessing", "sentimen", "emosi", "bertopic"):
            st.caption(tr("Data yang sudah selesai diproses tetap disimpan bila proses dihentikan."))


def finished(manager):
    """The state after a job ends, or when nothing has run yet."""
    last = st.session_state.get("proc-last")
    job = manager.get(last) if last else None
    if not job:
        st.info(tr("Tidak ada proses yang berjalan sekarang."), icon=":material/check_circle:")
        st.page_link(ui.pages()["preprocessing"], label=tr("Buka Preprocessing Data"), icon=":material/filter_alt:")
        return
    with st.container(key="panel-process"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**{job['project'] or label(job['source'])}**")
            ui.status_badge(job["status"])
        st.caption(tr('{0} · {1} · durasi {2} · {3} baris tersimpan', label(job['source']), ui.long_date(job['started']), ui.duration(job['started'], job['finished']), ui.fmt(job['count'])))
        if job["message"]:
            (st.error if job["status"] == "failed" else st.warning)(ui.friendly(job["message"]),
                                                                    icon=":material/warning:")
        logs = manager.logs(last)
        with st.expander(tr("Log proses"), expanded=job["status"] in ("failed", "partial"), icon=":material/terminal:"):
            if logs:
                st.code("\n".join(logs[-60:]), language="text", height=240)
            else:
                st.caption(tr("Belum ada log tersimpan."))
        if job["source"] == "preprocessing":
            st.page_link(ui.pages()["pp-dataset"], label=tr("Buka Dataset Preprocessing"), icon=":material/filter_alt:")
        elif job["source"] in ("sentimen", "emosi", "bertopic") and ui.pages().get(PAGE_OF.get(job["source"], job["source"])):
            if job["source"] == "sentimen":
                st.page_link(ui.pages()["sentimen"], label=tr("Lihat hasil sentimen"),
                             icon=":material/sentiment_satisfied:")
            elif job["source"] == "emosi":
                st.page_link(ui.pages()["emosi"], label=tr("Lihat hasil emosi dan toksisitas"), icon=":material/mood:")
            else:
                st.page_link(ui.pages()["topik-waktu"], label=tr("Lihat hasil topik"), icon=":material/timeline:")
            if "hasil" in ui.pages():
                st.page_link(ui.pages()["hasil"], label=tr("Buka Hasil Analisis"), icon=":material/inventory_2:")
        else:
            st.page_link(ui.pages()["dataset"], label=tr("Buka Dataset"), icon=":material/folder:")
