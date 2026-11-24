import os
from threading import Event

import pandas as pd
import pytest

from workspace_app import families as fam
from workspace_app import sentiment as sn
from workspace_app.jobs import JobManager
from workspace_app.pick import finished


class FakeClassifier:
    """Keyword rules instead of RoBERTa, so tests run without torch or a download."""
    revision = "fake"
    device = "cpu"

    def __init__(self):
        self.seen = []

    def predict(self, texts):
        self.seen.extend(texts)
        result = []
        for text in texts:
            if any(word in text for word in ("jelek", "error", "kecewa", "gagal")):
                result.append({"positif": 0.05, "netral": 0.05, "negatif": 0.9})
            elif any(word in text for word in ("bagus", "mantap", "suka")):
                result.append({"positif": 0.85, "netral": 0.1, "negatif": 0.05})
            else:
                result.append({"positif": 0.1, "netral": 0.8, "negatif": 0.1})
        return result


def reviews(count, start=0):
    rows = []
    for number in range(start, start + count):
        good = number % 2 == 0
        rows.append({"ulasan": (f"aplikasi bagus mantap fitur lengkap nomor{number}" if good
                                else f"aplikasi jelek sering error kecewa nomor{number}"),
                     "rating": "5" if good else "1", "tanggal": f"2026-0{1 + number % 3}-1{number % 9}",
                     "versi_aplikasi": f"1.{number % 3}"})
    return rows


def test_run_rates_text_and_keeps_rows_without_text():
    rows = [{"ulasan": "Aplikasinya bagus bgt 😍"}, {"ulasan": ""}, {"ulasan": "jelek, error terus"}]
    output, metadata = sn.run(rows, ["ulasan"], {}, FakeClassifier(), log=lambda _: None)
    assert [row["sentimen"] for row in output] == ["positif", "", "negatif"]
    assert output[0]["skor_sentimen"] == 0.85 and output[1]["skor_sentimen"] is None
    assert output[0]["ulasan"] == "Aplikasinya bagus bgt 😍"  # the original text is kept
    assert metadata["teks"] == 2 and metadata["sentimen"] == {"positif": 1, "negatif": 1}
    assert metadata["model"] == sn.MODEL_ID and metadata["seed"] == sn.SEED


def test_model_reads_lightly_normalised_text_not_stems():
    classifier = FakeClassifier()
    sn.run([{"ulasan": "Pelayanannya BAGUSSS bgt, cek https://a.id"}], ["ulasan"], {}, classifier,
           log=lambda _: None)
    assert classifier.seen == ["pelayanannya bagus banget, cek <url>"]


def test_aspects_rate_the_sentences_that_mention_them():
    rows = [{"ulasan": "Loginnya gagal terus. Tampilannya bagus sekali!"}, {"ulasan": "Biasa saja"}]
    aspects = sn.parse_aspects("login: login, loginnya\nfitur: tampilan, tampilannya\nharga: mahal")
    output, metadata = sn.run(rows, ["ulasan"], {"aspects": aspects}, FakeClassifier(), log=lambda _: None)
    assert output[0]["aspek_login"] == "negatif" and output[0]["aspek_fitur"] == "positif"
    assert output[0]["aspek_harga"] == "" and output[1]["aspek_login"] == ""
    assert metadata["aspek"] == {"login": 1, "fitur": 1, "harga": 0}


def test_parse_aspects_skips_malformed_lines_and_merges_repeats():
    text = "Login: login, OTP\n\nhanya teks\niklan:\nlogin: akun, otp"
    assert sn.parse_aspects(text) == {"login": ["login", "otp", "akun"]}
    assert sn.parse_aspects(sn.format_aspects(sn.ASPECT_PRESETS["Tempat"])) == sn.ASPECT_PRESETS["Tempat"]


def test_star_classifier_needs_enough_labelled_reviews():
    rows = reviews(40)
    output, metadata = sn.run(rows, ["ulasan"], {"rating_column": "rating", "star": True}, FakeClassifier(),
                              log=lambda _: None)
    assert "Butuh minimal 200" in metadata["classifier_bintang"]["alasan"]
    assert "sentimen_bintang" not in output[0] and output[0]["polaritas_bintang"] == "positif"


def test_star_classifier_learns_from_stars_and_reports_test_metrics():
    rows = reviews(300) + [{"ulasan": "lumayan", "rating": "3"}]
    output, metadata = sn.run(rows, ["ulasan"], {"rating_column": "rating", "star": True}, FakeClassifier(),
                              log=lambda _: None)
    report = metadata["classifier_bintang"]
    assert report["data_latih"] == 240 and report["data_uji"] == 60 and report["seed"] == sn.SEED
    assert report["akurasi"] > 0.95 and report["kelas"] == {"positif": 150, "negatif": 150}
    assert output[0]["sentimen_bintang"] == "positif" and output[1]["sentimen_bintang"] == "negatif"
    assert output[-1]["polaritas_bintang"] == "netral" and output[-1]["sentimen_bintang"] in ("positif", "negatif")


def test_stop_keeps_the_rows_rated_so_far():
    stop = Event()

    class Stopping(FakeClassifier):
        def predict(self, texts):
            stop.set()
            return super().predict(texts)

    rows = [{"ulasan": f"bagus {number}"} for number in range(sn.BATCH * 3)]
    output, metadata = sn.run(rows, ["ulasan"], {}, Stopping(), stop=stop, log=lambda _: None)
    assert len(output) == sn.BATCH and metadata["dihentikan"]
    assert all(row["sentimen"] == "positif" for row in output)


def test_summaries_of_a_sentiment_dataset():
    rows = reviews(30) + [{"ulasan": "aplikasi jelek", "rating": "5", "tanggal": "2026-03-01", "versi_aplikasi": "1.0"},
                          {"ulasan": "bagus", "rating": "1", "tanggal": "2026-03-02", "versi_aplikasi": "1.0"}]
    output, _ = sn.run(rows, ["ulasan"], {"rating_column": "rating"}, FakeClassifier(), log=lambda _: None)
    frame = pd.DataFrame(output)

    distribution = sn.distribution(frame).set_index("Sentimen")
    assert distribution.loc["positif", "Jumlah"] == 16 and distribution.loc["negatif", "Jumlah"] == 16

    wrong = sn.mismatches(frame, "rating", ["ulasan"])
    assert sorted(wrong["Teks"]) == ["aplikasi jelek", "bagus"]

    found = sn.agreement(frame, "rating")
    assert found["n"] == 32 and round(found["sesuai"], 2) == 93.75 and 0.8 < found["kappa"] < 0.9

    table = sn.by_rating(frame, "rating").set_index("Bintang")
    assert table.loc[5, "Ulasan"] == 16 and table.loc[5, "negatif"] == pytest.approx(6.2, abs=0.1)

    trend = sn.trend(frame, "tanggal", "ME")
    assert trend["Teks"].sum() == 32 and len(trend) == 3

    versions = sn.by_version(frame, date_column="tanggal")
    assert set(versions["Versi"]) == {"1.0", "1.1", "1.2"} and versions["Ulasan"].sum() == 32


def test_aspect_summary_reports_coverage_and_shares():
    frame = pd.DataFrame({"aspek_login": ["negatif", "negatif", "positif", ""], "aspek_iklan": ["", "", "", ""]})
    summary = sn.aspect_summary(frame).set_index("Aspek")
    assert summary.loc["login", "Disebut"] == 3 and summary.loc["login", "Cakupan (%)"] == 75.0
    assert summary.loc["login", "Negatif (%)"] == pytest.approx(66.7)
    assert summary.loc["iklan", "Disebut"] == 0


def test_method_text_names_model_split_and_aspects():
    _, metadata = sn.run(reviews(300), ["ulasan"], {"rating_column": "rating", "star": True,
                                                    "aspects": {"error": ["error"]}}, FakeClassifier(),
                         log=lambda _: None)
    text = sn.method_text(metadata, "ulasan Play Store")
    assert sn.MODEL_ID in text and "lisensi MIT" in text and "80:20" in text and "seed 42" in text
    assert "kamus kata kunci (error)" in text


# --------------------------------------------------------------------------- #
# Families and recommendations
# --------------------------------------------------------------------------- #

def test_detect_by_source_origin_and_merged_column():
    frame = pd.DataFrame(reviews(5))
    assert fam.detect({"source": "playstore"}, frame)[0] == ["A"]
    preprocessed = {"source": "preprocessing", "params": {"masukan": {"sumber": "youtube"}}}
    assert fam.detect(preprocessed, frame)[0] == ["B"]
    assert fam.detect({"source": "sentimen", "params": {"sumber_asal": ["gmaps"]}}, frame)[0] == ["A"]
    merged = frame.assign(sumber_data=["Berita", "Gempa (USGS)", "Berita", "Berita", "Berita"])
    assert fam.detect({"source": "gabungan"}, merged)[0] == ["C", "F", "D"]


def test_detect_by_columns_when_the_source_is_unknown():
    points = pd.DataFrame({"lintang": ["-6.2", "-7.1"], "bujur": ["106.8", "110.4"], "waktu": ["2026-01-01", "2026-01-02"],
                           "frp": ["3.5", "8.1"]})
    families, how = fam.detect({"source": "gabungan"}, points)
    assert families == ["F", "D"] and how == "dari pola kolom"
    panel = pd.DataFrame({"negara": ["Indonesia", "Malaysia"], "tahun": [2020, 2020], "nilai": [1.5, 2.5]})
    assert fam.detect({"source": "gabungan"}, panel)[0] == ["E"]
    texts = pd.DataFrame({"isi": ["ini kalimat yang cukup panjang untuk teks"] * 3})
    assert fam.detect({"source": "gabungan"}, texts)[0] == ["B"]


def test_profile_ignores_a_rating_column_outside_one_to_five():
    info = fam.profile(pd.DataFrame({"rating": ["10", "20"], "ulasan": ["teks ulasan cukup panjang sekali"] * 2}))
    assert info.rating_column is None


def test_recommendations_put_runnable_analyses_first_and_explain_the_rest():
    info = fam.profile(pd.DataFrame({"ulasan": ["ulasan yang cukup panjang untuk dihitung"] * 10}))
    items = [item for _, item in fam.recommendations(["A"], info)]
    assert items[0].key == "sentimen" and items[0].ready
    mismatch = next(item for item in items if item.key == "ketidakcocokan")
    assert not mismatch.ready and mismatch.blocked == "Tidak ada kolom rating 1–5."
    topics = next(item for item in items if item.key == "topik")
    assert "minimal 50" in topics.blocked
    planned = [item for _, item in fam.recommendations(["F"], info) if item.page is None]
    assert planned and planned[0].key == "peta"


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #

def wait(manager):
    if manager._thread:
        manager._thread.join(timeout=30)


def test_sentiment_job_saves_a_derived_dataset(tmp_path):
    manager = JobManager(tmp_path)
    params = {"masukan": {"pekerjaan": "abc", "sumber": "playstore"}, "sumber_asal": ["playstore"],
              "kolom": ["ulasan"]}
    job_id = manager.start_sentiment(params, reviews(10), ["ulasan"], {"rating_column": "rating"},
                                     project="Uji sentimen", classifier=FakeClassifier())
    wait(manager)
    job = manager.get(job_id)
    assert job["status"] == "completed" and job["source"] == "sentimen" and job["count"] == 10
    assert job["rows"][0]["sentimen"] == "positif" and job["metadata"]["model"] == sn.MODEL_ID
    assert [item["id"] for item in finished(manager)] == [job_id]
    assert fam.detect(job, pd.DataFrame(job["rows"]))[0] == ["A"]


def test_sentiment_job_without_model_or_consent_fails_with_a_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(sn, "model_path", lambda directory, download=False: None)
    manager = JobManager(tmp_path)
    job_id = manager.start_sentiment({"unduh_model": False}, reviews(2), ["ulasan"], {}, project="Uji")
    wait(manager)
    job = manager.get(job_id)
    assert job["status"] == "failed" and "Setujui unduhan model" in job["message"]


def test_sentiment_job_requires_project_and_columns(tmp_path):
    manager = JobManager(tmp_path)
    with pytest.raises(ValueError, match="nama proyek"):
        manager.start_sentiment({}, reviews(2), ["ulasan"], {}, project=" ")
    with pytest.raises(ValueError, match="kolom teks"):
        manager.start_sentiment({}, reviews(2), [], {}, project="Uji")


@pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="butuh model RoBERTa; jalankan dengan RUN_SLOW=1")
def test_real_model_rates_clear_indonesian_reviews():
    from workspace_app.jobs import local_directory

    path = sn.model_path(local_directory(), download=True)
    classifier = sn.Classifier(path)
    rated = [sn.label_of(item)[0] for item in classifier.predict(
        ["aplikasinya sangat bagus dan membantu", "aplikasi jelek, error terus dan bikin kecewa"])]
    assert rated == ["positif", "negatif"]


def test_date_columns_skip_numbers_stored_as_text():
    from workspace_app import analysis as an

    frame = pd.DataFrame({"lintang": ["-6.2", "-7.1"], "frp": ["3.5", "8.1"], "waktu": ["2026-01-01", "2026-01-02"],
                          "tahun": ["2020", "2021"]})
    assert an.date_columns(frame) == ["waktu", "tahun"]
    assert an.parse_dates(pd.Series(["-6.2"])).isna().all()
