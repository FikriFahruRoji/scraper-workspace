"""The running-job notice with its Hentikan button, refreshed every second."""
import streamlit as st

from workspace_app.i18n import tr

from workspace_app import ui
from workspace_app.sources import label


@st.fragment(run_every=1)
def activity(manager, framed=True):
    """Show the active job. `framed` draws it as its own card; inside a form it is plain."""
    active, _ = manager.snapshot()
    active_key = (active["source"], active["id"]) if active else None
    previous = st.session_state.setdefault("workspace_active", active_key)
    if active_key != previous:
        st.session_state["workspace_active"] = active_key
        st.rerun()
    if not active:
        return
    title = tr(label(active["source"]))
    with st.container(key="panel-activity" if framed else "activity-inline", border=False):
        info, controls = st.columns([4, 1], vertical_alignment="center")
        if active["stopping"]:
            info.warning(tr('{0} · sedang menghentikan', title), icon=":material/pause_circle:")
        elif active["login"]:
            info.info(tr('{0} · login di jendela browser', title), icon=":material/login:")
        else:
            count = ui.fmt(manager.saved_count(active["id"]))
            verb = {"preprocessing": "preprocessing berjalan", "sentimen": "analisis sentimen berjalan",
                     "emosi": "analisis emosi berjalan",
                     "bertopic": "analisis topik berjalan",
                     "ner": "analisis entitas berjalan"}.get(
                active["source"], "mengambil data")
            info.info(tr('{0} · {1} · {2} baris tersimpan', title, tr(verb), count), icon="spinner")
        if controls.button(tr("Hentikan"), key=f"workspace-stop-{'top' if framed else 'form'}",
                           disabled=active["stopping"], icon=":material/stop_circle:", width="stretch"):
            manager.stop()
            st.rerun()
