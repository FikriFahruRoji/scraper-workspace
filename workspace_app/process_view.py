"""Proses Berjalan: what the running job is doing, its log, and a Hentikan button."""
from datetime import datetime, timezone
import re

import streamlit as st

from workspace_app import ui
from workspace_app.sources import label

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
    ui.page_header("Proses Berjalan", "Pantau pekerjaan yang sedang berjalan, lengkap dengan lognya")
    live(manager)


@st.fragment(run_every=1)
def live(manager):
    active, _ = manager.snapshot()
    if not active:
        finished(manager)
        return
    st.session_state["proc-last"] = active["id"]
    job = manager.get(active["id"])
    logs = manager.logs(active["id"])
    name = label(active["source"])
    with st.container(key="panel-process"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**{job['project'] or name}**")
            if active["stopping"]:
                st.badge("Sedang menghentikan", icon=":material/pause_circle:", color="orange")
            elif active["login"]:
                st.badge("Login di jendela browser", icon=":material/login:", color="blue")
            else:
                ui.status_badge("running")
        st.caption(f"{name} · mulai {ui.long_date(job['started'])}")

        cols = st.columns(2)
        cols[0].metric("Baris tersimpan", ui.fmt(manager.saved_count(active["id"])), border=True)
        cols[1].metric("Sudah berjalan", elapsed(job["started"]), border=True)
        if progress := progress_from_logs(logs):
            done, total = progress
            st.progress(done / total, text=f"{ui.fmt(done)} dari {ui.fmt(total)} baris diproses")

        st.markdown("**Log proses**")
        if logs:
            st.code("\n".join(logs[-60:]), language="text", height=260)
            st.caption("Menampilkan 60 pesan terakhir. Halaman ini diperbarui tiap detik.")
        else:
            st.caption("Menyiapkan proses… log pertama akan muncul sebentar lagi.")
        if st.button("Hentikan", icon=":material/stop_circle:", disabled=active["stopping"], key="process-stop"):
            manager.stop()
            st.rerun()
        if active["source"] == "preprocessing":
            st.caption("Data yang sudah selesai diproses tetap disimpan bila proses dihentikan.")


def finished(manager):
    """The state after a job ends, or when nothing has run yet."""
    last = st.session_state.get("proc-last")
    job = manager.get(last) if last else None
    if not job:
        st.info("Tidak ada proses yang berjalan sekarang.", icon=":material/check_circle:")
        st.page_link(ui.PAGES["preprocessing"], label="Buka Preprocessing Data", icon=":material/filter_alt:")
        return
    with st.container(key="panel-process"):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**{job['project'] or label(job['source'])}**")
            ui.status_badge(job["status"])
        st.caption(f"{label(job['source'])} · {ui.long_date(job['started'])} · "
                   f"durasi {ui.duration(job['started'], job['finished'])} · {ui.fmt(job['count'])} baris tersimpan")
        if job["message"]:
            (st.error if job["status"] == "failed" else st.warning)(ui.friendly(job["message"]),
                                                                    icon=":material/warning:")
        logs = manager.logs(last)
        with st.expander("Log proses", expanded=job["status"] in ("failed", "partial"), icon=":material/terminal:"):
            if logs:
                st.code("\n".join(logs[-60:]), language="text", height=240)
            else:
                st.caption("Belum ada log tersimpan.")
        if job["source"] == "preprocessing":
            st.page_link(ui.PAGES["pp-dataset"], label="Buka Dataset Preprocessing", icon=":material/filter_alt:")
        else:
            st.page_link(ui.PAGES["dataset"], label="Buka Dataset", icon=":material/folder:")
