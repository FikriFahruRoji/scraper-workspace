"""Entrypoint for the localhost-only Scraper Workspace."""
from functools import partial
from pathlib import Path

import streamlit as st

from workspace_app import settings, ui
from workspace_app.activity import activity
from workspace_app.analysis_view import PAGES as ANALYSES, page_for
from workspace_app.jobs import JobManager, local_directory
from workspace_app.preprocess_view import preprocessing_page
from workspace_app.sources import LABELS, label
from workspace_app.merge_view import merge_page
from workspace_app.process_view import process_page
from workspace_app.studio import dataset_page, preprocessing_dataset_page, scraping_page
from workspace_app.views import source_page

RESOURCES = Path(__file__).resolve().parent / "workspace_app" / "resources"
st.set_page_config(page_title="nOCturnal Scraper", page_icon=str(RESOURCES / "icon.svg"), layout="wide")
ui.inject_css()
st.logo(str(RESOURCES / "blank.svg"), size="large")


@st.cache_resource
def manager_for(path):
    return JobManager(path)


try:
    manager = manager_for(local_directory())
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()


dataset = st.Page(partial(dataset_page, manager), title="Dataset", icon=":material/folder:",
                  url_path="dataset", default=True)
scraping = st.Page(partial(scraping_page, manager), title="Lakukan Scraping", icon=":material/travel_explore:",
                   url_path="scraping")
merge = st.Page(partial(merge_page, manager), title="Gabungkan Data", icon=":material/merge:", url_path="gabungkan")
preprocessing = st.Page(partial(preprocessing_page, manager), title="Preprocessing Data",
                        icon=":material/filter_alt:", url_path="preprocessing")
_running, _ = manager.snapshot()
running = st.Page(partial(process_page, manager), icon=":material/progress_activity:", url_path="proses-berjalan",
                  title="Proses Berjalan" + (" ●" if _running else ""))
pp_dataset = st.Page(partial(preprocessing_dataset_page, manager), title="Dataset Preprocessing",
                     icon=":material/dataset:", url_path="dataset-preprocessing")
analyses = {key: st.Page(page_for(manager, key), title=title, icon=icon, url_path=f"analisis-{key}")
            for key, (icon, title, _) in ANALYSES.items()}
# Source forms are opened from "Lakukan Scraping", so they stay out of the menu.
forms = {key: st.Page(partial(source_page, manager, key), title=name, url_path=key, visibility="hidden")
         for key, name in LABELS.items()}
ui.PAGES.update(forms, dataset=dataset, scraping=scraping, gabungkan=merge, preprocessing=preprocessing,
                berjalan=running)
ui.PAGES["pp-dataset"] = pp_dataset

THEMES = {"system": "Sistem", "light": "Terang", "dark": "Gelap"}


def save_theme():
    settings.save(local_directory(), tema=st.session_state["setting-theme"])


with st.sidebar:
    with st.container(key="sidebar-bottom"):
        st.html('<div class="settings-title">Pengaturan</div>')
        saved = settings.load(local_directory())["tema"]
        st.segmented_control("Tema", list(THEMES), default=saved, required=True, key="setting-theme",
                             format_func=THEMES.get, on_change=save_theme, width="stretch",
                             help="Sistem mengikuti pengaturan terang atau gelap di komputermu.")
        with st.container(key="sidebar-footer"):
            st.caption("Stealth Scraping by Fikri Fahru Roji")
ui.apply_theme_pref(st.session_state.get("setting-theme", saved), ["/", *(f"/{page.url_path}" for page in
                    [dataset, scraping, merge, preprocessing, running, pp_dataset, *analyses.values(), *forms.values()]
                    if page.url_path)])

page = st.navigation({"Data": [dataset, scraping, *forms.values()],
               "Preprocessing": [merge, preprocessing, running, pp_dataset],
               "Analisis": list(analyses.values())})
# Source forms show the running job under their own start button; other pages show it at the top.
if page.url_path not in forms and page.url_path != "proses-berjalan":
    activity(manager)
page.run()
