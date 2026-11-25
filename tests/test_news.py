import random
from pathlib import Path
from threading import Event

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from workspace_app import families as fam
from workspace_app import news
from workspace_app import sources
from workspace_app.jobs import JobManager
from workspace_app.pick import finished

THEMES = {
    "Kompas": "anggaran pajak menteri ekonomi inflasi subsidi kebijakan",
    "Detik": "banjir korban evakuasi pengungsi longsor gempa bnpb",
    "CNBC": "saham ihsg rupiah investor emiten pasar obligasi",
}
COMMON = "indonesia hari kata pejabat warga kota"


def articles(per_portal=40, seed=1):
    random.seed(seed)
    rows = []
    for portal, words in THEMES.items():
        for n in range(per_portal):
            rows.append({
                "portal": portal, "title": f"judul {words.split()[n % 3]}",
                "content": " ".join(random.choices(words.split(), k=18) + random.choices(COMMON.split(), k=8)),
                "date_iso": f"2026-0{1 + n % 3}-1{n % 9}",
                "sentimen": random.choice(["positif", "netral", "negatif"])})
    random.shuffle(rows)
    return pd.DataFrame(rows)


def test_group_column_needs_two_values():
    frame = articles()
    assert news.group_column(frame) == "portal"
    assert news.group_column(frame[frame["portal"] == "Kompas"]) is None
    assert news.group_column(pd.DataFrame({"sumber_data": ["A", "B"], "x": [1, 2]})) == "sumber_data"


def test_distinctive_words_are_the_ones_typical_of_each_portal():
    frame = articles()
    documents = news.cleaned_rows(frame, ["title", "content"])
    results, summary = news.distinctive_words(documents, frame["portal"], top=5)
    assert set(results) == {"Kompas", "Detik", "CNBC"} and summary["dokumen"] == {"CNBC": 40, "Detik": 40, "Kompas": 40}
    assert set(results["CNBC"]["Kata"]) <= set(THEMES["CNBC"].split())
    assert results["Kompas"]["Signifikan"].all() and results["Kompas"]["Skor z"].is_monotonic_decreasing
    common = news.distinctive_words(documents, frame["portal"], top=500)[0]["Kompas"].set_index("Kata")
    assert common.loc["indonesia", "Skor z"] < 2  # shared words are not distinctive
    again = news.distinctive_words(documents, frame["portal"], top=5)[0]["Kompas"]
    assert again.equals(results["Kompas"])  # deterministic
    text = news.distinctive_method_text(summary, "data Berita")
    assert "Monroe" in text and "1,96" in text and "Kompas (40)" in text


def test_small_groups_are_skipped_and_one_group_gives_nothing():
    frame = articles()
    frame.loc[frame.index[:3], "portal"] = "Tempo"  # three rows only
    documents = news.cleaned_rows(frame, ["title", "content"])
    results, summary = news.distinctive_words(documents, frame["portal"])
    assert "Tempo" not in results and summary["dilewati"] == ["Tempo"]
    only = news.distinctive_words(documents, ["Kompas"] * len(documents))
    assert only[0] == {}


def test_sentiment_by_group_reports_shares_and_a_chi_square_test():
    frame = articles(60)
    frame.loc[frame["portal"] == "Detik", "sentimen"] = "negatif"
    table, test = news.sentiment_by_group(frame, "portal")
    detik = table.set_index("Kelompok").loc["Detik"]
    assert detik["negatif"] == 100 and detik["Skor bersih"] == -100
    assert test["df"] == 4 and test["p"] < 0.001 and 0 < test["cramers_v"] <= 1
    text = news.group_sentiment_method_text(test, "data Berita")
    assert "chi-kuadrat" in text and "p < 0,001" in text
    assert news.sentiment_by_group(frame.drop(columns="sentimen"), "portal")[0].empty


def test_npmi_coherence_is_high_for_words_that_appear_together_and_low_for_ones_that_never_do():
    docs = ["a b c"] * 20 + ["x y z"] * 20 + ["a"] * 10
    scores, mean = news.npmi_coherence([["a", "b", "c"], ["a", "x", "y"]], docs)
    assert scores[0] > 0.5 and scores[1] < 0 and mean == pytest.approx(np.mean(scores))
    assert news.npmi_coherence([["a", "b"]], [])[1] is None


def test_nmf_topics_over_time_find_the_themes_and_their_monthly_share():
    frame = articles()
    table, share, coherence, used = news.topics_over_time(frame, ["title", "content"], "date_iso", 3, "ME")
    assert used == 120 and coherence["rata_rata"] > 0.2
    assert len(share) == 3 and (share.sum(axis=1).round() == 100).all()
    top_words = {row["Kata utama"].split(", ")[0] for _, row in table.iterrows()}
    assert top_words & {w for words in THEMES.values() for w in words.split()}
    with pytest.raises(ValueError):
        news.topics_over_time(frame.head(5), ["title", "content"], "date_iso", 5)


def test_family_c_recommends_the_new_analyses():
    frame = articles()
    info = fam.profile(frame)
    assert info.has_groups
    items = {item.key: item for _, item in fam.recommendations(["C"], info)}
    assert items["log-odds"].ready and items["log-odds"].page == "pembeda"
    assert items["bertopic"].ready and items["bertopic"].page == "topik-waktu"
    single = fam.profile(frame[frame["portal"] == "Kompas"])
    items = {item.key: item for _, item in fam.recommendations(["C"], single)}
    assert items["log-odds"].blocked and "portal" in items["log-odds"].blocked


# --------------------------------------------------------------------------- #
# BERTopic (needs the optional packages, no model download: fake embeddings)
# --------------------------------------------------------------------------- #

bertopic = pytest.importorskip("bertopic", reason="BERTopic is optional")


class FakeEmbedder:
    """TF-IDF + SVD vectors instead of MiniLM, so the tests need no download."""
    revision = "fake-embed"
    device = "cpu"

    def encode(self, texts):
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectors = TruncatedSVD(20, random_state=0).fit_transform(TfidfVectorizer().fit_transform(texts))
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def four_themes():
    random.seed(3)
    themes = {"ekonomi": "anggaran pajak menteri ekonomi inflasi subsidi kebijakan",
              "bencana": "gempa banjir longsor korban evakuasi bnpb pengungsi",
              "olahraga": "liga gol pelatih pemain stadion timnas pertandingan",
              "teknologi": "aplikasi startup digital ai data pengguna platform"}
    rows = [{"portal": ["Kompas", "Detik", "CNBC"][n % 3], "title": f"judul {theme}",
             "content": " ".join(random.choices(words.split(), k=18) + random.choices(COMMON.split(), k=8)),
             "date_iso": f"2026-0{1 + n % 3}-1{n % 9}"}
            for theme, words in themes.items() for n in range(45)]
    random.shuffle(rows)
    return rows


def test_bertopic_finds_the_four_themes_with_readable_words():
    from workspace_app import bertopic_run as bt

    output, metadata = bt.run(four_themes(), ["title", "content"], {"min_topic_size": 10}, FakeEmbedder(),
                              log=lambda _: None)
    assert len(metadata["topik"]) == 4 and metadata["tanpa_topik"] == 0
    names = " ".join(item["nama"] for item in metadata["topik"])
    for word in ("pajak", "banjir", "pemain", "startup"):
        assert word in names
    assert metadata["koherensi_rata_rata"] > 0.3 and metadata["seed"] == bt.SEED
    assert metadata["versi"]["bertopic"] and metadata["parameter"]["min_topic_size"] == 10
    assert output[0]["topik_nama"] and output[0]["title"].startswith("judul")  # original columns are kept
    frame = pd.DataFrame(output)
    assert not bt.over_time(frame, "date_iso").empty
    assert len(bt.documents_of(frame, metadata["topik"][0]["id"], ["title", "content"])) == 5
    assert "Grootendorst" in bt.method_text(metadata, "data Berita")


def test_bertopic_needs_enough_texts_and_a_stop_returns_nothing():
    from workspace_app import bertopic_run as bt

    with pytest.raises(ValueError, match="minimal"):
        bt.run(four_themes()[:20], ["content"], {}, FakeEmbedder(), log=lambda _: None)
    stop = Event()
    stop.set()
    output, metadata = bt.run(four_themes(), ["content"], {}, FakeEmbedder(), stop=stop, log=lambda _: None)
    assert output == [] and metadata == {"dihentikan": True}


def wait(manager):
    if manager._thread:
        manager._thread.join(timeout=60)


def test_bertopic_job_saves_a_derived_dataset_listed_with_analysis_results(tmp_path):
    manager = JobManager(tmp_path)
    params = {"masukan": {"pekerjaan": "abc", "sumber": "news", "proyek": "Berita"}, "sumber_asal": ["news"],
              "kolom": ["title", "content"]}
    manager.start_bertopic(params, four_themes(), ["title", "content"], {"min_topic_size": 10}, project="Uji topik",
                           embedder=FakeEmbedder())
    wait(manager)
    job = manager.list_jobs("bertopic")[0]
    assert job["status"] == "completed" and job["count"] == 180
    full = manager.get(job["id"])
    assert len(full["metadata"]["topik"]) == 4 and "topik_nama" in full["rows"][0]
    assert job["id"] in {item["id"] for item in finished(manager)}
    assert fam.detect(full, pd.DataFrame(full["rows"]))[0] == ["C"]


def test_bertopic_job_without_model_and_consent_fails_with_a_reason(tmp_path, monkeypatch):
    from workspace_app import bertopic_run as bt

    monkeypatch.setattr(bt, "model_path", lambda directory, download=False: None)
    manager = JobManager(tmp_path)
    manager.start_bertopic({"unduh_model": False}, four_themes(), ["content"], {}, project="Uji")
    wait(manager)
    job = manager.get(manager.list_jobs("bertopic")[0]["id"])
    assert job["status"] == "failed" and "Setujui unduhan" in job["message"]


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

def page(tmp_path, body, name):
    script = tmp_path / f"{name}.py"
    script.write_text("import sys\n"
                      f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
                      "import streamlit as st\n" + body)
    return str(script)


def seeded(tmp_path, monkeypatch):
    rows = articles().drop(columns="sentimen").to_dict("records")
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (rows, {}, None))
    manager = JobManager(tmp_path / "data")
    manager.start("news", {"keyword": "ekonomi"}, project="Berita")
    wait(manager)
    return manager


def test_distinctive_words_and_topics_pages_render(tmp_path, monkeypatch):
    manager = seeded(tmp_path, monkeypatch)
    at = AppTest.from_file(page(tmp_path, "from workspace_app.analysis_view import page_for\n"
                                          "page_for(st.session_state['manager'], 'pembeda')()\n", "pembeda"),
                           default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Kata Pembeda"
    assert any("Monroe" in code.value for code in at.code)

    at = AppTest.from_file(page(tmp_path, "from workspace_app.topics_view import topics_page\n"
                                          "topics_page(st.session_state['manager'])\n", "topics"), default_timeout=90)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Topik dari Waktu ke Waktu"
    assert any("NMF" in code.value and "NPMI" in code.value for code in at.code)
    at.segmented_control(key="tp-part").set_value("BERTopic: jalankan").run()
    assert not at.exception, at.exception
    at.segmented_control(key="tp-part").set_value("BERTopic: hasil").run()
    assert not at.exception, at.exception
    assert at.info and "Belum ada hasil BERTopic" in at.info[0].value


def test_bertopic_results_page_shows_topics_and_method(tmp_path):
    manager = JobManager(tmp_path)
    params = {"masukan": {"pekerjaan": "abc", "sumber": "news", "proyek": "Berita"}, "sumber_asal": ["news"],
              "kolom": ["title", "content"]}
    manager.start_bertopic(params, four_themes(), ["title", "content"], {"min_topic_size": 10}, project="Uji topik",
                           embedder=FakeEmbedder())
    wait(manager)
    at = AppTest.from_file(page(tmp_path, "from workspace_app.topics_view import topics_page\n"
                                          "topics_page(st.session_state['manager'])\n", "topics-result"),
                           default_timeout=90)
    at.session_state["manager"] = manager
    at.run()
    at.segmented_control(key="tp-part").set_value("BERTopic: hasil").run()
    assert not at.exception, at.exception
    assert any("Grootendorst" in code.value for code in at.code)
    assert at.dataframe  # topic table and representative texts


class FakeSentiment:
    revision = "fake"
    device = "cpu"

    def predict(self, texts):
        return [{"positif": 0.05, "netral": 0.05, "negatif": 0.9} if any(w in t for w in ("banjir", "korban", "gempa"))
                else {"positif": 0.8, "netral": 0.1, "negatif": 0.1} for t in texts]


def test_sentiment_results_page_adds_a_per_portal_section_for_news(tmp_path):
    manager = JobManager(tmp_path)
    rows = articles().drop(columns="sentimen").to_dict("records")
    params = {"masukan": {"pekerjaan": "abc", "sumber": "news", "proyek": "Berita"}, "sumber_asal": ["news"],
              "kolom": ["title", "content"]}
    manager.start_sentiment(params, rows, ["title", "content"], {}, project="Sentimen berita",
                            classifier=FakeSentiment())
    wait(manager)
    at = AppTest.from_file(page(tmp_path, "from workspace_app.sentiment_view import sentiment_page\n"
                                          "sentiment_page(st.session_state['manager'])\n", "sentiment-portal"),
                           default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert "Per portal" in [header.value for header in at.subheader]
    assert any("chi-kuadrat" in code.value for code in at.code)
