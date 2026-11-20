from threading import Event

import pytest

from workspace_app import preprocess as pp


def config(preset, **changes):
    return {**pp.PRESETS[preset], **pp.DEFAULT_LISTS, **changes}


LIGHT = "Ringan (model transformer)"
FULL = "Lengkap (analisis kata)"


def clean(text, cfg, **uploads):
    stats = pp.new_stats()
    return pp.clean_text(text, cfg, pp.build_resources(cfg, **uploads), stats), stats


def test_light_preset_keeps_context_for_transformers():
    text, stats = clean("<b>BAGUSSS</b> bgt 😍, cek https://a.id &amp; @budi #liburan", config(LIGHT))
    assert text == "bagus banget wajah tersenyum lebar bermata hati , cek <url> & <user> liburan"
    assert (stats["url"], stats["mention"], stats["emoji"], stats["kata_gaul"]) == (1, 1, 1, 1)


def test_full_preset_strips_to_word_stems_and_keeps_negation():
    text, stats = clean("Pelayanannya tidak menyenangkan, kamarnya kotor sekali!! 2 kali", config(FULL))
    assert text == "layan tidak senang kamar kotor kali"
    assert stats["stopword"] >= 1 and stats["angka"] == 1


def test_negation_can_be_dropped_when_switched_off():
    text, _ = clean("tidak bagus", config(FULL, keep_negation=False, stemming=False))
    assert text == "bagus"


def test_slang_runs_before_numbers_and_punctuation_so_such_keys_still_match():
    text, _ = clean("b4 7an w/ kamu", config(FULL, stopwords=False, stemming=False))
    assert text == "sebelum tujuan dengan kamu"


def test_slang_is_single_pass_and_handles_phrases():
    # The bundled dictionary maps ya→iya and iya→ya; neither result is replaced again.
    text, stats = clean("ya iya au ah gak papa", config(LIGHT))
    assert text == "iya ya tidak mau tahu tidak apa-apa"
    assert stats["kata_gaul"] == 4


def test_uploaded_and_typed_slang_override_bundled_entries():
    cfg = config(LIGHT, extra_slang="gpp=tidak apa-apa\nmw=mau")
    text, _ = clean("gpp mw yg", cfg, slang_upload=[("yg", "yang itu")])
    assert text == "tidak apa-apa mau yang itu"


def test_stopword_sources_extra_words_and_exceptions():
    cfg = config(FULL, stemming=False, stopword_sources=["english"], extra_stopwords="hotel", keep_words="the")
    text, _ = clean("the hotel is near pantai", cfg)
    assert text == "the near pantai" or text == "the pantai"
    text, _ = clean("hotel ada pantai", config(FULL, stemming=False, stopword_sources=["upload"]),
                    stopword_upload=["pantai"])
    assert text == "hotel ada"


def test_punctuation_keeps_word_hyphens_and_placeholders_only():
    text, _ = clean("teman-teman - halo! <url>", config(FULL, stopwords=False, stemming=False, urls="keep"))
    assert text == "teman-teman halo"
    text, _ = clean("lihat https://a.id - ok", config(LIGHT, punctuation="remove"))
    assert text == "lihat <url> ok"


def test_parsers_accept_json_csv_and_report_dictionary_problems():
    pairs = pp.parse_slang(b'{"yg": "yang", "yg": "yg", "ya": "iya", "iya": "ya"}')
    assert dict(pairs) == {"yg": "yg", "ya": "iya", "iya": "ya"}
    report = pp.check_slang(pairs)
    assert report["kunci_ganda"] == ["yg"] and report["ke_diri_sendiri"] == ["yg"]
    assert report["saling_tukar"] == [("iya", "ya")]
    assert pp.parse_slang("slang,formal\ngk,tidak\n") == [("gk", "tidak")]
    assert pp.parse_words(b"Tidak\nyang\n\nyang\n") == ["tidak", "yang", "yang"]
    assert pp.check_words(["tidak", "yang", "yang"]) == {"entri": 2, "ganda": ["yang"], "negasi": ["tidak"]}
    with pytest.raises(ValueError):
        pp.parse_slang("tanpa pasangan")


def test_bundled_dictionaries_load_as_shipped():
    assert len(dict(pp.project_slang())) > 1000
    assert "yang" in pp.project_stopwords()
    assert ("iya", "ya") in pp.check_slang(pp.project_slang())["saling_tukar"]


def test_run_joins_columns_drops_empty_and_duplicates_and_reports():
    rows = [{"title": "Banjir", "content": "Jakarta banjir lagi"}, {"title": "Banjir", "content": "Jakarta banjir lagi"},
            {"title": "", "content": "yang dan"}, {"title": 5, "content": "Hujan"}]
    out, report = pp.run(rows, ["title", "content"], config(FULL, stemming=False), pp.build_resources(config(FULL)))
    assert [row["teks_bersih"] for row in out] == ["banjir jakarta banjir", "hujan"]
    assert out[0]["title"] == "Banjir" and out[0]["jumlah_token"] == 3
    assert (report["baris_masuk"], report["baris_keluar"]) == (4, 2)
    assert (report["baris_kosong_dibuang"], report["duplikat_dibuang"]) == (1, 1)


def test_run_reports_progress_and_stops_between_batches():
    stop, seen = Event(), []

    def progress(done, total, rows):
        seen.append(done)
        stop.set()

    rows = [{"t": f"kata {n}"} for n in range(5)]
    out, report = pp.run(rows, ["t"], config(LIGHT), pp.build_resources(config(LIGHT)),
                         progress=progress, stop=stop, batch=2)
    assert seen == [2] and len(out) == 2 and report["baris_masuk"] == 5


def test_sastrawi_dictionary_lookup_is_a_set_not_a_list_scan():
    # Sastrawi 1.0.1 scans a ~30k-word list per lookup (~40 ms per word).
    dictionary = pp._stemmer().delegatedStemmer.dictionary
    assert isinstance(dictionary.words, set) and len(dictionary.words) > 20_000
    assert pp.stem("pelayanannya") == "layan"


def test_run_preserves_columns_that_collide_with_output_names():
    cfg = config(LIGHT)
    rows = [{"caption": "kopi lokal", "teks_bersih": "kurasi manual", "jumlah_token": 99},
            {"caption": "kopi", "teks_bersih_asli": "arsip"}]
    output, report = pp.run(rows, ["caption"], cfg, pp.build_resources(cfg), batch=1)

    assert output[0] == {"caption": "kopi lokal", "teks_bersih_asli_2": "kurasi manual",
                         "jumlah_token_asli": 99, "teks_bersih": "kopi lokal", "jumlah_token": 2}
    assert output[1]["teks_bersih_asli"] == "arsip"
    assert report["total_token"] == 3
    assert rows[0]["teks_bersih"] == "kurasi manual"
