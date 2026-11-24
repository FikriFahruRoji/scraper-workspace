"""Info: the analysis map (which analyses suit which kind of data), the same content as docs/ANALISIS.md."""
from pathlib import Path

import streamlit as st

from workspace_app import ui

MAP = Path(__file__).resolve().parent / "resources" / "peta_analisis.html"


def info_page():
    link = ui.PAGES.get("rekomendasi")
    ui.page_header("Peta Analisis", "Analisis yang cocok untuk tiap jenis data: metode, library gratis, dan syaratnya",
                   actions=(lambda: st.page_link(link, label="Cari analisis untuk dataset kamu",
                                                 icon=":material/recommend:")) if link else None,
                   split=(3, 2))
    # Static file shipped with the app: styles scoped to .peta, no scripts, no external resources.
    st.html(MAP.read_text(encoding="utf-8"))
