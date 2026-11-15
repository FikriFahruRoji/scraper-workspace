"""Entrypoint for the localhost-only Scraper Workspace."""
from functools import partial

import streamlit as st

from workspace_app.jobs import JobManager, local_directory
from workspace_app.sources import LABELS
from workspace_app.views import history_page, source_page

st.set_page_config(page_title="Scraper Workspace", layout="wide")


@st.cache_resource
def manager_for(path):
    return JobManager(path)


try:
    manager = manager_for(local_directory())
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()


@st.fragment(run_every=1)
def activity():
    active, _ = manager.snapshot()
    active_key = (active["source"], active["id"]) if active else None
    previous = st.session_state.setdefault("workspace_active", active_key)
    if active_key != previous:
        st.session_state["workspace_active"] = active_key
        st.rerun()
    if not active:
        return
    title = LABELS[active["source"]]
    with st.container(border=True):
        info, controls = st.columns([4, 1], vertical_alignment="center")
        if active["stopping"]:
            info.warning(f"{title} · sedang menghentikan")
        elif active["login"]:
            info.info(f"{title} · login di jendela browser")
        else:
            count = manager.saved_count(active["id"])
            info.info(f"{title} · scraping berjalan · {count:,} baris tersimpan")
        if controls.button("Hentikan", key="workspace-stop", disabled=active["stopping"]):
            manager.stop()
            st.rerun()


with st.sidebar:
    st.markdown("### Scraper Workspace")
    st.caption("Mode lokal · Scrapling stealth")

activity()

pages = [st.Page(partial(source_page, manager, key), title=label, url_path=key,
                 default=key == "news") for key, label in LABELS.items()]
pages.append(st.Page(partial(history_page, manager), title="Riwayat", url_path="riwayat"))
st.navigation(pages).run()
