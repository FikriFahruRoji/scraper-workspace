"""Entrypoint for the localhost-only Scraper Workspace."""
from functools import partial
from pathlib import Path

import streamlit as st

from workspace_app import ui
from workspace_app.activity import activity
from workspace_app.analysis_view import PAGES as ANALYSES, page_for
from workspace_app.jobs import JobManager, local_directory
from workspace_app.preprocess_view import preprocessing_page
from workspace_app.sources import LABELS, label
from workspace_app.merge_view import merge_page
from workspace_app.info_view import info_page
from workspace_app.process_view import process_page
from workspace_app.recommend_view import recommend_page
from workspace_app.sentiment_view import sentiment_page
from workspace_app.studio import dataset_page, preprocessing_dataset_page, scraping_page
from workspace_app.views import source_page

RESOURCES = Path(__file__).resolve().parent / "workspace_app" / "resources"
st.set_page_config(page_title="nOCturnal Scraper", page_icon=str(RESOURCES / "icon.svg"), layout="wide")
# Style and script elements take up a row of space wherever they sit, so they live in the sidebar,
# not above the page content.
with st.sidebar:
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
recommend = st.Page(partial(recommend_page, manager), title="Rekomendasi Analisis", icon=":material/recommend:",
                    url_path="analisis")
info = st.Page(info_page, title="Info", icon=":material/info:", url_path="info")
sentiment = st.Page(partial(sentiment_page, manager), title="Sentimen", icon=":material/sentiment_satisfied:",
                    url_path="analisis-sentimen")
# Source forms are opened from "Lakukan Scraping", so they stay out of the menu.
forms = {key: st.Page(partial(source_page, manager, key), title=name, url_path=key, visibility="hidden")
         for key, name in LABELS.items()}
ui.PAGES.update(forms, dataset=dataset, scraping=scraping, gabungkan=merge, preprocessing=preprocessing,
                berjalan=running)
ui.PAGES["pp-dataset"] = pp_dataset
ui.PAGES.update({f"analisis-{key}": page for key, page in analyses.items()}, rekomendasi=recommend, sentimen=sentiment)

with st.sidebar:
    with st.container(key="sidebar-bottom"):
        with st.container(key="sidebar-footer"):
            st.caption("Stealth Scraping by Fikri Fahru Roji")

page = st.navigation({"Data": [dataset, scraping, *forms.values()],
               "Preprocessing": [merge, preprocessing, running, pp_dataset],
               "Analisis": [recommend, sentiment, *analyses.values()],
               "Info": [info]})
# Source forms show the running job under their own start button; other pages show it at the top.
if page.url_path not in forms and page.url_path != "proses-berjalan":
    activity(manager)
page.run()
