from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from workspace_app import bibliometrik as bib
from workspace_app import families as fam
from workspace_app import sources
from workspace_app.jobs import JobManager

ROOT = Path(__file__).resolve().parents[1]


def scholar_rows():
    return [
        {"title": "Machine learning for flood prediction in Indonesia", "authors": "A. Budi, B. Citra, C. Dewi", "year": 2021, "citations": 24},
        {"title": "Deep learning flood mapping using satellite imagery", "authors": "A. Budi, D. Eko", "year": 2022, "citations": 18},
        {"title": "Sentiment analysis of Indonesian social media texts", "authors": "B. Citra, E. Fajar", "year": 2022, "citations": 12},
        {"title": "Indonesian natural language processing with transformers", "authors": "B. Citra, F. Gina, A. Budi", "year": 2023, "citations": 15},
        {"title": "Flood susceptibility mapping in Java river basin", "authors": "D. Eko, G. Hadi", "year": 2020, "citations": 9},
        {"title": "Topic modeling on Indonesian news portals", "authors": "E. Fajar, F. Gina", "year": 2023, "citations": 6},
        {"title": "Social network analysis of online political discourse", "authors": "E. Fajar, H. Indra", "year": 2021, "citations": 11},
        {"title": "Remote sensing for peatland fire monitoring", "authors": "D. Eko, H. Indra", "year": 2022, "citations": 7},
        {"title": "Transformer models for hate speech detection", "authors": "F. Gina, B. Citra", "year": 2024, "citations": 4},
        {"title": "Urban flood risk assessment with geospatial clustering", "authors": "A. Budi, G. Hadi", "year": 2023, "citations": 5},
        {"title": "Cross-lingual embeddings for Indonesian sentiment", "authors": "F. Gina, E. Fajar", "year": 2024, "citations": 3},
        {"title": "Satellite-based fire hotspot clustering in Sumatra", "authors": "G. Hadi, D. Eko", "year": 2023, "citations": 2},
    ]


def sinta_rows():
    return [
        {"id_sinta": "1", "nama": "Dr. Budi", "afiliasi": "Univ A", "program_studi": "Informatika",
         "scopus_h_index": 8, "gscholar_h_index": 14, "scopus_citations": 210, "gscholar_citations": 540},
        {"id_sinta": "2", "nama": "Dr. Citra", "afiliasi": "Univ A", "program_studi": "Informatika",
         "scopus_h_index": 5, "gscholar_h_index": 10, "scopus_citations": 110, "gscholar_citations": 320},
        {"id_sinta": "3", "nama": "Dr. Dewi", "afiliasi": "Univ B", "program_studi": "Sistem Informasi",
         "scopus_h_index": 3, "gscholar_h_index": 6, "scopus_citations": 45, "gscholar_citations": 150},
        {"id_sinta": "4", "nama": "Dr. Eko", "afiliasi": "Univ B", "program_studi": "Teknik Sipil",
         "scopus_h_index": 6, "gscholar_h_index": 11, "scopus_citations": 130, "gscholar_citations": 380},
    ]


def test_coauthor_network_metrics_and_guard():
    frame = pd.DataFrame(scholar_rows())
    res = bib.coauthor_network(frame, min_pubs=10)
    assert res["stats"]["publikasi"] == 12
    assert res["stats"]["penulis"] >= 6
    assert res["stats"]["kolaborasi"] >= 6
    assert "NetworkX" in bib.coauthor_method_text("data Google Scholar", res)

    with pytest.raises(bib.NotEnough, match="minimal 10 publikasi"):
        bib.coauthor_network(frame.head(5), min_pubs=10)


def test_citations_by_year_and_h_index():
    frame = pd.DataFrame(scholar_rows())
    res = bib.citations_by_year(frame)
    assert res["summary"]["total_publikasi"] == 12
    assert res["summary"]["total_sitasi"] == sum(r["citations"] for r in scholar_rows())
    assert res["summary"]["h_index"] >= 5
    assert "h-index korpus" in bib.citations_method_text("data Google Scholar", res)


def test_title_topics_nmf():
    frame = pd.DataFrame(scholar_rows())
    res = bib.title_topics(frame, n_topics=3)
    assert len(res["topics"]) == 3
    assert res["by_year"] is not None
    assert "NMF" in bib.topics_method_text("data Google Scholar", res)


def test_sinta_profile_analysis_and_document_guard():
    frame = pd.DataFrame(sinta_rows())
    res = bib.sinta_profile_analysis(frame)
    assert res["n_profiles"] == 4
    assert len(res["by_prodi"]) == 3
    assert len(res["by_afiliasi"]) == 2
    assert res["corr_h"] is not None and res["corr_h"]["spearman_r"] > 0.9
    assert "Spearman" in bib.sinta_method_text("data SINTA", res)

    docs_only = pd.DataFrame([{"id_sinta": "1", "daftar": "Scopus", "judul": "Sample paper"}])
    with pytest.raises(bib.NotEnough, match="daftar dokumen sampel SINTA"):
        bib.sinta_profile_analysis(docs_only)


def _page(tmp_path, name="bib"):
    script = tmp_path / f"{name}.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "import streamlit as st\nfrom workspace_app.bibliometrik_view import bibliometrik_page\n"
        "bibliometrik_page(st.session_state['manager'])\n"
    )
    return str(script)


def _seeded(tmp_path, monkeypatch, source, rows):
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (rows, {}, None))
    manager = JobManager(tmp_path / f"data-{source}")
    manager.start(source, {"query": "uji"}, project="Bib Uji")
    manager._thread.join(60)
    return manager


def test_bibliometrik_page_renders_scholar_and_sinta(tmp_path, monkeypatch):
    scholar_mgr = _seeded(tmp_path, monkeypatch, "google_scholar", scholar_rows())
    at = AppTest.from_file(_page(tmp_path, "scholar"), default_timeout=60)
    at.session_state["manager"] = scholar_mgr
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Bibliometrik"
    for name in ("Jaringan co-author", "Sitasi per tahun", "Topik judul"):
        current = next(c for c in at.segmented_control if c.key.startswith("bib-section-"))
        current.set_value(name).run()
        assert not at.exception, (name, at.exception)

    sinta_mgr = _seeded(tmp_path, monkeypatch, "sinta", sinta_rows())
    at_s = AppTest.from_file(_page(tmp_path, "sinta"), default_timeout=60)
    at_s.session_state["manager"] = sinta_mgr
    at_s.run()
    assert not at_s.exception, at_s.exception
    current = next(c for c in at_s.segmented_control if c.key.startswith("bib-section-"))
    assert current.value == "Profil SINTA"
    current.set_value("Jaringan co-author").run()
    assert any("tidak ditawarkan" in info.value for info in at_s.info)
