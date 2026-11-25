"""Choosing finished datasets: shared by Gabungkan Data and the analysis pages."""
import streamlit as st

from workspace_app.i18n import tr

from workspace_app import ui
from workspace_app.jobs import ACTIVE
from workspace_app.sources import label


def finished(manager):
    """Finished jobs that saved rows (scraped, preprocessed, merged, sentiment, emotion, or topics), newest first."""
    jobs = [job for kind in (None, "preprocessing", "gabungan", "sentimen", "emosi", "bertopic") for job in manager.list_jobs(kind, limit=100_000)]
    jobs = [job for job in jobs if job["status"] not in ACTIVE and job["count"]]
    return sorted(jobs, key=lambda job: job["started"] or "", reverse=True)


def option_label(job):
    return (f"{job['project']} · {label(job['source'])} · {ui.short_datetime(job['started'])} · "
            f"{ui.fmt(job['count'])} baris")


def empty_state(message="Belum ada dataset. Ambil data di Lakukan Scraping dulu."):
    st.info(tr(message), icon=":material/inbox:")
    if "scraping" in ui.pages():
        st.page_link(ui.pages()["scraping"], label=tr("Buka Lakukan Scraping"), icon=":material/travel_explore:")
