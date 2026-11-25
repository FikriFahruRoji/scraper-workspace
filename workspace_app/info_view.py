"""Info: the analysis map (which analyses suit which kind of data), the same content as docs/ANALISIS.md."""
from pathlib import Path

import streamlit as st

from workspace_app.i18n import language, tr

from workspace_app import ui

MAP = Path(__file__).resolve().parent / "resources" / "peta_analisis.html"


def info_page():
    link = ui.pages().get("rekomendasi")
    ui.page_header(tr("Peta Analisis"), tr("Analisis yang cocok untuk tiap jenis data: metode, library gratis, dan syaratnya"),
                   actions=(lambda: st.page_link(link, label=tr("Cari analisis untuk dataset kamu"),
                                                 icon=":material/recommend:")) if link else None,
                   split=(3, 2))
    # Static file shipped with the app: styles scoped to .peta, no scripts, no external resources.
    path = MAP.with_name("analysis_map.en.html") if language() == "en" else MAP
    st.html(path.read_text(encoding="utf-8"))
