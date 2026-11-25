from pathlib import Path
from threading import Event

import pandas as pd
from streamlit.testing.v1 import AppTest

from workspace_app import families as fam
from workspace_app import ner
from workspace_app.jobs import JobManager
from workspace_app.pick import finished

KNOWN = {"Prabowo": "PER", "Jokowi": "PER", "KPK": "ORG", "Gerindra": "NOR", "Jakarta": "GPE", "Senin": "DAT"}


class FakeTagger:
    """Looks names up in a list instead of running NusaBERT, so tests run without torch or a download."""
    revision = "fake-ner"
    device = "cpu"

    def tag(self, texts):
        result = []
        for text in texts:
            found = []
            for word in text.replace(".", " ").replace(",", " ").split():
                kind = KNOWN.get(word.strip()) or KNOWN.get(word.strip().capitalize())
                if kind and kind in ner.TYPES:
                    found.append({"teks": word.strip(), "jenis": kind, "skor": 0.3 if word.isupper() and
                                  word == "KPK" and "ragu" in text else 0.95})
            result.append(found)
        return result


def articles():
    return [
        {"title": "Prabowo ke Jakarta", "content": "Prabowo bertemu KPK di Jakarta.", "portal": "kompas",
         "date": "2026-01-05"},
        {"title": "KPK periksa", "content": "KPK dan Gerindra. jokowi hadir.", "portal": "kompas",
         "date": "2026-01-20"},
        {"title": "Gerindra", "content": "Prabowo dan Gerindra di Jakarta.", "portal": "detik", "date": "2026-02-02"},
        {"title": "Cuaca", "content": "Hujan turun sore ini.", "portal": "detik", "date": "2026-02-10"},
        {"title": "", "content": "", "portal": "detik", "date": "2026-02-11"},
    ]


def tagged():
    output, metadata = ner.run(articles(), ["title", "content"], FakeTagger(), log=lambda _: None)
    return pd.DataFrame(output), metadata


def test_decode_joins_bio_tags_and_subwords_into_spans():
    text = "Presiden Prabowo Subianto di Jakarta"
    # tokens: "Presiden", " Prab", "owo", " Subianto", " di", " Jakarta" (BPE keeps the leading space)
    offsets = [(0, 0), (0, 8), (8, 13), (13, 16), (16, 25), (25, 28), (28, 36), (0, 0)]
    labels = ["O", "O", "B-PER", "B-PER", "I-PER", "O", "B-GPE", "O"]
    scores = [1, 1, 0.9, 0.5, 0.7, 1, 0.99, 1]
    spans = ner.decode(labels, scores, offsets, text)
    assert [(text[a:b].strip(), kind) for a, b, kind, _ in spans] == [("Prabowo Subianto", "PER"), ("Jakarta", "GPE")]
    assert round(spans[0][3], 3) == 0.7  # mean of the entity's token probabilities


def test_entities_without_a_capital_letter_and_unnamed_types_are_dropped():
    text = "the pemerintah KPK Senin"
    spans = {(0, 3): ("NOR", 0.9), (4, 14): ("ORG", 0.9), (15, 18): ("ORG", 0.9), (19, 24): ("DAT", 0.99)}
    assert ner.entities_of(text, spans) == [{"teks": "KPK", "jenis": "ORG", "skor": 0.9}]


def test_clean_span_keeps_balanced_parentheses_only():
    assert ner.clean_span(" PT Pertamina (Persero)") == "PT Pertamina (Persero)"
    assert ner.clean_span(' "KPK",') == "KPK"
    assert ner.clean_span("Universitas Indonesia (UI") == "Universitas Indonesia"


def test_run_adds_entity_columns_and_keeps_rows_without_text():
    frame, metadata = tagged()
    assert frame.loc[0, "entitas_orang"] == "Prabowo" and frame.loc[0, "entitas_tempat"] == "Jakarta"
    assert frame.loc[1, "entitas_organisasi"] == "KPK; Gerindra"
    assert frame.loc[4, "entitas"] == ""  # no text, nothing tagged
    assert "Senin" not in frame["entitas"].str.cat()  # dates are not named entities
    assert metadata["teks"] == 4 and metadata["model"] == ner.MODEL_ID and metadata["revisi"] == "fake-ner"
    assert metadata["ringkasan"]["PER"] == 4  # Prabowo ×3 (title and body), jokowi


def test_entity_table_counts_documents_and_merges_case():
    frame, _ = tagged()
    table = ner.entity_table(frame).set_index("Entitas")
    assert table.loc["Prabowo", "Dokumen"] == 2 and table.loc["Prabowo", "Sebutan"] == 3
    assert table.loc["jokowi", "Jenis"] == "Orang"
    assert table.loc["KPK", "Persen dokumen"] == 50.0  # 2 of the 4 texts the job reached
    people = ner.entity_table(frame, ["PER"])
    assert set(people["Jenis"]) == {"Orang"}


def test_group_time_and_pair_summaries():
    frame, _ = tagged()
    groups = ner.by_group(frame, "portal")
    kompas = groups[groups["Kelompok"] == "kompas"].set_index("Entitas")
    assert kompas.loc["KPK", "Persen dokumen"] == 100.0
    over = ner.over_time(frame, "date", ["prabowo", "kpk"], "ME")
    assert list(over.columns) == ["Prabowo", "KPK"] and over["Prabowo"].tolist() == [1, 1]
    pairs = ner.cooccurrence(frame, minimum=1).set_index(["Entitas A", "Entitas B"])
    assert pairs.loc[("Gerindra", "Jakarta"), "Dokumen bersama"] == 1
    strong = ner.cooccurrence(frame, minimum=2)
    assert set(map(tuple, strong[["Entitas A", "Entitas B"]].values)) == {("Jakarta", "Prabowo")}
    assert strong["Jaccard"].iloc[0] == 1.0
    assert list(ner.edges(frame).columns) == ["Source", "Target", "Weight", "Jaccard"]
    texts = ner.documents_with(frame, "gerindra", ["title", "content"])
    assert list(texts.columns) == ["Portal", "Teks"] and len(texts) == 2


def test_low_scores_are_left_out_of_summaries():
    rows = [{"title": "", "content": "KPK ragu."}]
    output, _ = ner.run(rows, ["title", "content"], FakeTagger(), log=lambda _: None)
    frame = pd.DataFrame(output)
    assert ner.parse(frame.loc[0, "entitas"])[0]["skor"] == 0.3  # kept in the data
    assert frame.loc[0, "entitas_organisasi"] == "" and ner.entity_table(frame).empty
    assert not ner.entity_table(frame, min_score=0.2).empty


def test_stopping_keeps_a_complete_prefix_of_rows():
    stop = Event()

    class Stopper(FakeTagger):
        def tag(self, texts):
            stop.set()
            return super().tag(texts)

    rows = articles()[:4] * 5
    output, metadata = ner.run(rows, ["title", "content"], Stopper(), stop=stop, log=lambda _: None)
    assert len(output) == ner.BATCH and metadata["dihentikan"] and metadata["teks"] == ner.BATCH


def test_method_text_names_model_license_note_and_rules():
    _, metadata = tagged()
    text = ner.method_text(metadata, "data Berita")
    for part in (ner.MODEL_ID, "NERGrit", "tidak mencantumkan lisensi", "fake-ner", "0,5", "Jaccard"):
        assert part in text, part


def wait(manager):
    if manager._thread:
        manager._thread.join(timeout=30)


def test_ner_job_saves_a_derived_dataset_recognised_as_news(tmp_path):
    manager = JobManager(tmp_path)
    params = {"masukan": {"pekerjaan": "abc", "sumber": "news", "proyek": "Berita"}, "sumber_asal": ["news"],
              "kolom": ["title", "content"]}
    manager.start_ner(params, articles(), ["title", "content"], project="Uji entitas", tagger=FakeTagger())
    wait(manager)
    job = manager.list_jobs("ner")[0]
    assert job["status"] == "completed" and job["count"] == 5
    full = manager.get(job["id"])
    assert full["rows"][0]["entitas_orang"] == "Prabowo"
    assert job["id"] in {item["id"] for item in finished(manager)}
    assert fam.detect(full, pd.DataFrame(full["rows"]))[0] == ["C"]


def test_ner_job_without_model_and_consent_fails_with_a_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(ner, "model_path", lambda directory, download=False: None)
    manager = JobManager(tmp_path)
    manager.start_ner({"unduh_model": False}, articles(), ["content"], project="Uji")
    wait(manager)
    job = manager.get(manager.list_jobs("ner")[0]["id"])
    assert job["status"] == "failed" and "Setujui unduhan" in job["message"]


def test_news_recommendations_offer_the_entity_page():
    frame = pd.DataFrame(articles())
    keys = {item.key: item for _, item in fam.recommendations(["C"], fam.profile(frame))}
    assert keys["entitas"].ready and keys["entitas"].page == "entitas"


def _page(tmp_path):
    script = tmp_path / "ner_page.py"
    script.write_text("import sys\n"
                      f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
                      "import streamlit as st\nfrom workspace_app.ner_view import ner_page\n"
                      "ner_page(st.session_state['manager'])\n")
    return str(script)


def test_ner_page_shows_results_and_the_run_form(tmp_path, monkeypatch):
    from workspace_app import sources
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (articles(), {}, None))
    manager = JobManager(tmp_path)
    manager.start("news", {"keyword": "prabowo"}, project="Berita")
    wait(manager)
    params = {"masukan": {"pekerjaan": "abc", "sumber": "news", "proyek": "Berita"}, "sumber_asal": ["news"],
              "kolom": ["title", "content"]}
    manager.start_ner(params, articles(), ["title", "content"], project="Uji entitas", tagger=FakeTagger())
    wait(manager)
    at = AppTest.from_file(_page(tmp_path), default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Entitas Bernama"
    assert any("NERGrit" in code.value for code in at.code)
    assert any(header.value == "Entitas teratas per portal" for header in at.subheader)
    at.segmented_control(key="ne-part").set_value("Jalankan analisis").run()
    assert not at.exception, at.exception
    from workspace_app import optional
    if optional.missing("transformer"):
        assert any(optional.INSTALL_COMMAND in code.value for code in at.code)
    else:
        assert any(button.label == "Jalankan pengenalan entitas" for button in at.button)
