"""Entrypoint for the localhost-only Scraper Workspace."""
from functools import partial
from pathlib import Path

import streamlit as st

from workspace_app.i18n import language_selector, tr

from workspace_app import ui
from workspace_app.activity import activity
from workspace_app.analysis_view import EXPLORATION, PAGES as ANALYSES, page_for
from workspace_app.jobs import JobManager, local_directory
from workspace_app.preprocess_view import preprocessing_page
from workspace_app.sources import LABELS, label
from workspace_app.merge_view import merge_page
from workspace_app.info_view import info_page
from workspace_app.process_view import process_page
from workspace_app.recommend_view import recommend_page
from workspace_app.sentiment_view import sentiment_page
from workspace_app.emotion_view import emotion_page
from workspace_app.topics_view import topics_page
from workspace_app.series_view import series_page
from workspace_app.geo_view import geo_page
from workspace_app.studio import analysis_results_page, dataset_detail_page, dataset_page, preprocessing_dataset_page, scraping_page
from workspace_app.views import source_page

RESOURCES = Path(__file__).resolve().parent / "workspace_app" / "resources"
st.set_page_config(page_title="nOCturnal Scraper", page_icon=str(RESOURCES / "icon.svg"), layout="wide")
# Style and script elements take up a row of space wherever they sit, so they live in the sidebar,
# not above the page content.
with st.sidebar:
    ui.inject_css()
    language_selector()
st.logo(str(RESOURCES / "blank.svg"), size="large")


@st.cache_resource
def manager_for(path):
    return JobManager(path)


try:
    manager = manager_for(local_directory())
except RuntimeError as exc:
    st.error(str(exc))
    st.stop()


dataset = st.Page(partial(dataset_page, manager), title=tr("Dataset"), icon=":material/folder:",
                  url_path="dataset")
detail = st.Page(partial(dataset_detail_page, manager), title=tr("Detail Dataset"), icon=":material/folder_open:",
                 url_path="dataset-detail", visibility="hidden")
scraping = st.Page(partial(scraping_page, manager), title=tr("Lakukan Scraping"), icon=":material/travel_explore:",
                   url_path="scraping", default=True)
merge = st.Page(partial(merge_page, manager), title=tr("Gabungkan Data"), icon=":material/merge:", url_path="gabungkan")
preprocessing = st.Page(partial(preprocessing_page, manager), title=tr("Preprocessing Data"),
                        icon=":material/filter_alt:", url_path="preprocessing")
_running, _ = manager.snapshot()
running = st.Page(partial(process_page, manager), icon=":material/progress_activity:", url_path="proses-berjalan",
                  title=tr("Proses Berjalan") + (" ●" if _running else ""))
pp_dataset = st.Page(partial(preprocessing_dataset_page, manager), title=tr("Dataset Preprocessing"),
                     icon=":material/dataset:", url_path="dataset-preprocessing")
analyses = {key: st.Page(page_for(manager, key), title=tr(title), icon=icon, url_path=f"analisis-{key}")
            for key, (icon, title, _) in ANALYSES.items()}
recommend = st.Page(partial(recommend_page, manager), title=tr("Rekomendasi Analisis"), icon=":material/recommend:",
                    url_path="analisis")
results = st.Page(partial(analysis_results_page, manager), title=tr("Hasil Analisis"), icon=":material/inventory_2:",
                   url_path="hasil-analisis")
info = st.Page(info_page, title=tr("Peta Analisis"), icon=":material/map:", url_path="info")
sentiment = st.Page(partial(sentiment_page, manager), title=tr("Sentimen"), icon=":material/sentiment_satisfied:",
                    url_path="analisis-sentimen")
emotion = st.Page(partial(emotion_page, manager), title=tr("Emosi & Toksisitas"), icon=":material/mood:",
                  url_path="analisis-emosi")
topics = st.Page(partial(topics_page, manager), title=tr("Topik dari Waktu ke Waktu"), icon=":material/timeline:",
                 url_path="analisis-topik-waktu")
series = st.Page(partial(series_page, manager), title=tr("Deret Waktu"), icon=":material/show_chart:",
                 url_path="analisis-deret-waktu")
geomap = st.Page(partial(geo_page, manager), title=tr("Peta Kejadian"), icon=":material/public:",
                 url_path="analisis-geospasial")
# Source forms are opened from "Lakukan Scraping", so they stay out of the menu.
forms = {key: st.Page(partial(source_page, manager, key), title=tr(name), url_path=key, visibility="hidden")
         for key, name in LABELS.items()}
st.session_state["workspace-pages"] = {}
ui.pages().update(forms, dataset=dataset, **{"dataset-detail": detail}, scraping=scraping, gabungkan=merge, preprocessing=preprocessing,
                berjalan=running)
ui.pages()["pp-dataset"] = pp_dataset
ui.pages().update({f"analisis-{key}": page for key, page in analyses.items()}, rekomendasi=recommend, sentimen=sentiment,
                emosi=emotion, **{"topik-waktu": topics, "deret-waktu": series, "geospasial": geomap}, hasil=results)

with st.sidebar:
    with st.container(key="sidebar-bottom"):
        with st.container(key="sidebar-footer"):
            st.caption(tr("Stealth Scraping by Fikri Fahru Roji"))

# Grouped by the stages of a study, each stage's action above the list of what it produced.
# Running jobs span every stage, so they sit with the reference page at the bottom.
page = st.navigation({tr("Pengumpulan"): [scraping, dataset, detail, *forms.values()],
                      tr("Persiapan"): [merge, preprocessing, pp_dataset],
                      tr("Eksplorasi"): [analyses[key] for key in EXPLORATION],
                      tr("Analisis"): [recommend, sentiment, emotion, topics, series, geomap,
                                       *(page for key, page in analyses.items() if key not in EXPLORATION), results],
                      tr("Lainnya"): [running, info]}, expanded=True)
# Source forms show the running job under their own start button; other pages show it at the top.
if page.url_path not in forms and page.url_path != "proses-berjalan":
    activity(manager)
page.run()
