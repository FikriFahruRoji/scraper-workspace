from pathlib import Path
from threading import Event

import pandas as pd
from streamlit.testing.v1 import AppTest

from workspace_app import emotion as em
from workspace_app import families as fam
from workspace_app.jobs import JobManager
from workspace_app.pick import finished


class FakeEmotion:
    """Keyword rules instead of RoBERTa, so tests run without torch or a download."""
    revision = "fake-emosi"
    device = "cpu"

    def predict(self, texts):
        result = []
        for text in texts:
            if "marah" in text or "kesal" in text:
                result.append({"marah": 0.8, "senang": 0.05, "cinta": 0.05, "sedih": 0.05, "takut": 0.05})
            elif "senang" in text:
                result.append({"marah": 0.05, "senang": 0.8, "cinta": 0.05, "sedih": 0.05, "takut": 0.05})
            else:  # no clear emotion: the top probability stays below the threshold
                result.append({"marah": 0.2, "senang": 0.2, "cinta": 0.2, "sedih": 0.25, "takut": 0.15})
        return result


class FakeDetector:
    revision = "fake-toksik"
    device = "cpu"

    def __init__(self, word):
        self.word = word

    def predict(self, texts):
        return [{"tidak": 0.1, "ya": 0.9} if self.word in text else {"tidak": 0.95, "ya": 0.05} for text in texts]


def fakes():
    return {"emosi": FakeEmotion(), "ujaran_kebencian": FakeDetector("benci"), "hinaan": FakeDetector("bodoh")}


def comments(count=12):
    texts = ["aku senang sekali hari ini", "kesal banget sama kamu bodoh", "aku benci kalian semua",
             "biasa saja"]
    return [{"komentar": texts[n % len(texts)], "username": f"u{n % 5}", "waktu": f"2026-0{1 + n % 3}-1{n % 9}"}
            for n in range(count)]


def test_run_adds_label_and_score_columns_and_keeps_rows_without_text():
    rows = [*comments(4), {"komentar": "", "username": "kosong"}]
    output, metadata = em.run(rows, ["komentar"], list(em.SPECS), fakes(), log=lambda _: None)
    assert [row["emosi"] for row in output] == ["senang", "marah", "sedih", "sedih", ""]
    assert [row["ujaran_kebencian"] for row in output] == ["tidak", "tidak", "ya", "tidak", ""]
    assert [row["hinaan"] for row in output] == ["tidak", "ya", "tidak", "tidak", ""]
    assert output[2]["skor_ujaran_kebencian"] == 0.9 and output[4]["skor_emosi"] is None
    assert output[0]["komentar"] == "aku senang sekali hari ini"  # the original text is kept
    assert metadata["teks"] == 4 and metadata["seed"] == em.SEED
    assert metadata["model"]["hinaan"]["id"] == "Exqrch/IndoBERTweet-Insult"
    assert metadata["model"]["hinaan"]["tokenizer"] == em.TOKENIZER_ID
    assert metadata["ringkasan"]["ujaran_kebencian"] == {"tidak": 3, "ya": 1}
    assert metadata["ringkasan"]["emosi_yakin"] == {"senang": 1, "marah": 1}


def test_only_the_chosen_models_run():
    output, metadata = em.run(comments(4), ["komentar"], ["hinaan"], {"hinaan": FakeDetector("bodoh")},
                              log=lambda _: None)
    assert "emosi" not in output[0] and output[1]["hinaan"] == "ya"
    assert metadata["analisis"] == ["hinaan"] and "Emosi" not in em.method_text(metadata, "data X")


def test_stopping_keeps_a_complete_prefix_of_rows():
    stop = Event()

    class Stopper(FakeEmotion):
        def predict(self, texts):
            stop.set()
            return super().predict(texts)

    output, metadata = em.run(comments(80), ["komentar"], ["emosi"], {"emosi": Stopper()}, stop=stop,
                              log=lambda _: None)
    assert len(output) == em.BATCH and all(row["emosi"] for row in output)
    assert metadata["dihentikan"] and metadata["teks"] == em.BATCH


def test_summaries_count_unclear_emotions_and_flagged_texts():
    output, _ = em.run(comments(8), ["komentar"], list(em.SPECS), fakes(), log=lambda _: None)
    frame = pd.DataFrame(output)
    table = em.emotion_table(frame).set_index("Emosi")
    assert table.loc["senang", "Jumlah"] == 2 and table.loc["tidak jelas", "Jumlah"] == 4
    assert em.emotion_table(frame, 0.2).set_index("Emosi").loc["tidak jelas", "Jumlah"] == 0
    toxic = em.toxic_table(frame).set_index("Deteksi")
    assert toxic.loc["Ujaran kebencian", "Terdeteksi"] == 2 and toxic.loc["Hinaan", "Persen"] == 25.0
    flagged = em.flagged_rows(frame, "hinaan", ["komentar"])
    assert list(flagged.columns) == ["Akun", "Skor", "Teks"] and len(flagged) == 2
    monthly = em.over_time(frame, "emosi", "waktu", "ME")
    assert len(monthly) == 3 and (monthly.sum(axis=1).round() == 100).all()


def test_method_text_names_models_thresholds_and_caveats():
    _, metadata = em.run(comments(4), ["komentar"], list(em.SPECS), fakes(), log=lambda _: None)
    text = em.method_text(metadata, "komentar Instagram")
    for part in ("StevenLimcorn/indonesian-roberta-base-emotion-classifier", "Exqrch/IndoBERTweet-HateSpeech",
                 "IndoToxic2024", "CC BY-SA 4.0", "tidak memiliki kelas netral", "0,5", "fake-emosi"):
        assert part in text, part


def wait(manager):
    if manager._thread:
        manager._thread.join(timeout=30)


def test_emotion_job_saves_a_derived_dataset_listed_with_analysis_results(tmp_path):
    manager = JobManager(tmp_path)
    params = {"masukan": {"pekerjaan": "abc", "sumber": "instagram", "proyek": "Komentar"},
              "sumber_asal": ["instagram"], "kolom": ["komentar"], "analisis": list(em.SPECS)}
    manager.start_emotion(params, comments(10), ["komentar"], list(em.SPECS), project="Uji emosi",
                          classifiers=fakes())
    wait(manager)
    job = manager.list_jobs("emosi")[0]
    assert job["status"] == "completed" and job["count"] == 10
    full = manager.get(job["id"])
    assert full["rows"][1]["hinaan"] == "ya" and full["metadata"]["analisis"] == list(em.SPECS)
    assert job["id"] in {item["id"] for item in finished(manager)}
    assert fam.detect(full, pd.DataFrame(full["rows"]))[0] == ["B"]


def test_emotion_job_without_model_and_consent_fails_with_a_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(em, "model_paths", lambda directory, key, download=False: None)
    manager = JobManager(tmp_path)
    manager.start_emotion({"unduh_model": False}, comments(2), ["komentar"], ["emosi"], project="Uji")
    wait(manager)
    job = manager.get(manager.list_jobs("emosi")[0]["id"])
    assert job["status"] == "failed"
    assert "Setujui unduhan" in job["message"]


def _page(tmp_path):
    script = tmp_path / "emotion_page.py"
    script.write_text("import sys\n"
                      f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
                      "import streamlit as st\nfrom workspace_app.emotion_view import emotion_page\n"
                      "emotion_page(st.session_state['manager'])\n")
    return str(script)


def test_emotion_page_shows_results_and_the_run_form(tmp_path, monkeypatch):
    from workspace_app import sources
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (comments(12), {}, None))
    manager = JobManager(tmp_path)
    manager.start("instagram", {"post_input": "https://www.instagram.com/p/x/"}, project="Komentar")
    wait(manager)
    params = {"masukan": {"pekerjaan": "abc", "sumber": "instagram", "proyek": "Komentar"},
              "sumber_asal": ["instagram"], "kolom": ["komentar"], "analisis": list(em.SPECS)}
    manager.start_emotion(params, comments(12), ["komentar"], list(em.SPECS), project="Uji emosi",
                          classifiers=fakes())
    wait(manager)
    at = AppTest.from_file(_page(tmp_path), default_timeout=60)
    at.session_state["manager"] = manager
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Emosi & Toksisitas"
    assert any("IndoToxic2024" in code.value for code in at.code)
    at.segmented_control(key="em-part").set_value("Jalankan analisis").run()
    assert not at.exception, at.exception
    from workspace_app import optional
    if optional.missing("transformer"):  # the run form asks for the optional packages first
        assert any(optional.INSTALL_COMMAND in code.value for code in at.code)
    else:
        assert any(button.label == "Jalankan analisis emosi dan toksisitas" for button in at.button)
