from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from workspace_app import families as fam
from workspace_app import social as so
from workspace_app import sources
from workspace_app.jobs import JobManager


def youtube_rows():
    """Two threads: budi and cici reply to ani; ani replies back to budi; one reply's parent is missing."""
    return pd.DataFrame({
        "id_komentar": ["a", "b", "c", "d", "e", "f"],
        "id_induk": ["", "a", "a", "b", "hilang", "f"],
        "nama": ["@ani", "@budi", "@cici", "@ani", "@budi", "@dodi"],
        "komentar": ["videonya bagus sekali kak", "setuju banget sama kamu", "kurang setuju sih",
                     "terima kasih sudah mampir", "nonton lagi besok", "balas diri sendiri dulu"],
        "suka": [10, 2, 0, "5", None, 1],
    })


def test_parent_ids_become_account_edges_and_unlinked_replies_are_counted():
    edges, info = so.reply_edges(youtube_rows())
    assert set(map(tuple, edges[["dari", "ke"]].values)) == {("@budi", "@ani"), ("@cici", "@ani"), ("@ani", "@budi")}
    assert info == {"jenis": "induk", "balasan": 5, "induk_hilang": 1, "ke_diri_sendiri": 1}


def test_x_and_threads_link_replies_by_account_name():
    frame = pd.DataFrame({"username": ["a", "b", "c", "a", "a"], "IN_REPLY_TO": ["b", "@a", "a", "", "b"],
                          "text": ["isi cuitan cukup panjang"] * 5})
    edges, info = so.reply_edges(frame)
    assert info["jenis"] == "akun" and info["balasan"] == 4
    assert edges.set_index(["dari", "ke"])["balasan"].to_dict() == {("a", "b"): 2, ("b", "a"): 1, ("c", "a"): 1}


def test_network_ranks_the_most_replied_account_first_and_is_reproducible():
    edges, info = so.reply_edges(youtube_rows())
    table, metrics = so.reply_network(edges)
    assert table.iloc[0]["Akun"] == "@ani" and table.iloc[0]["Balasan diterima"] == 2
    assert metrics["akun"] == 3 and metrics["relasi"] == 3
    assert metrics["resiprositas"] == pytest.approx(2 / 3)
    assert metrics["betweenness_sampel"] is None and metrics["seed"] == so.SEED
    assert so.reply_network(edges)[0].equals(table)
    text = so.network_method_text(metrics, info, "data YouTube")
    assert "Louvain" in text and "PageRank" in text and "0,667" in text


def test_no_reply_column_means_no_edges():
    edges, info = so.reply_edges(pd.DataFrame({"username": ["a"], "text": ["halo semua apa kabar"]}))
    assert edges.empty and info["jenis"] is None


def test_active_accounts_measure_concentration():
    frame = pd.DataFrame({"username": ["a"] * 8 + ["b", "c"], "suka": [1] * 8 + [10, 0]})
    table, summary = so.active_accounts(frame, "username", "suka")
    assert table.iloc[0].to_dict()["Akun"] == "a" and table.iloc[0]["Teks"] == 8
    assert table.set_index("Akun").loc["b", "Total suka"] == 10
    assert summary["akun"] == 3 and summary["satu_kali"] == 2
    assert summary["porsi_10_persen_teratas"] == pytest.approx(80)
    assert so.gini([1, 1, 1]) == pytest.approx(0) and so.gini([0, 0, 9]) == pytest.approx(2 / 3)


def test_top_texts_are_sorted_by_likes():
    top = so.top_texts(youtube_rows(), "komentar", "suka", 2, "nama")
    assert top["Suka"].tolist() == [10, 5] and top["Akun"].tolist() == ["@ani", "@ani"]


def test_sentiment_by_likes_runs_kruskal_wallis_with_effect_size():
    frame = pd.DataFrame({"sentimen": ["positif"] * 10 + ["negatif"] * 10,
                          "suka": list(range(10)) + list(range(50, 60))})
    table, test = so.sentiment_by_likes(frame, "suka")
    assert set(table["Sentimen"]) == {"positif", "negatif"}
    assert test["p"] < 0.001 and test["df"] == 1 and 0 < test["epsilon2"] <= 1
    text = so.interaction_method_text({"akun": 2, "teks": 20, "gini": 0.1, "porsi_10_persen_teratas": 50.0},
                                      test, "data X", "suka")
    assert "Kruskal–Wallis" in text and "p < 0,001" in text
    small = pd.DataFrame({"sentimen": ["positif", "negatif"], "suka": [1, 2]})
    assert so.sentiment_by_likes(small, "suka")[1] is None


def test_near_duplicates_group_copies_that_differ_only_in_links_and_mentions():
    rows = [{"komentar": f"Promo murah klik link https://x.co/{n} sekarang @teman{n}", "username": f"u{n % 7}"}
            for n in range(30)]
    rows += [{"komentar": f"pendapat saya soal kebijakan {word} ini berbeda", "username": "z"}
             for word in ("pajak", "jalan", "sekolah", "pangan", "listrik", "air", "rumah", "pasar",
                          "kereta", "bandara", "pelabuhan", "hutan", "laut", "kota", "desa", "tambang",
                          "energi", "data", "internet", "pos")]
    frame = pd.DataFrame(rows)
    table, membership, summary = so.near_duplicates(frame, "komentar", 0.9, "username")
    assert summary["grup"] == 1 and summary["teks_dalam_grup"] == 30
    assert table.iloc[0]["Teks"] == 30 and table.iloc[0]["Akun berbeda"] == 7
    assert set(membership.index) == set(range(30))
    assert "tidak menjadi bukti akun bot" in so.duplicate_method_text(summary, "data Instagram")


def test_family_b_recommends_the_new_pages_when_columns_allow():
    info = fam.profile(youtube_rows())
    assert info.has_replies and info.has_author and info.has_likes
    items = {item.key: item for _, item in fam.recommendations(["B"], info)}
    assert items["jaringan-balasan"].ready and items["jaringan-balasan"].page == "balasan"
    assert items["interaksi"].ready
    assert items["teks-berulang"].blocked  # six texts, the check needs fifty
    assert items["emosi"].ready and items["emosi"].page == "emosi"
    bare = fam.profile(pd.DataFrame({"text": ["teks tanpa akun dan tanpa suka"] * 60}))
    items = {item.key: item for _, item in fam.recommendations(["B"], bare)}
    assert items["jaringan-balasan"].blocked and items["interaksi"].blocked and items["teks-berulang"].ready


def _page(tmp_path, key):
    script = tmp_path / f"page_{key}.py"
    script.write_text("import sys\n"
                      f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
                      "import streamlit as st\nfrom workspace_app.analysis_view import page_for\n"
                      f"page_for(st.session_state['manager'], {key!r})()\n")
    return str(script)


def test_social_pages_render_on_a_youtube_dataset(tmp_path, monkeypatch):
    base = youtube_rows().to_dict("records")
    rows = [{**row, "id_komentar": f"{row['id_komentar']}{n}",
             "id_induk": f"{row['id_induk']}{n}" if row["id_induk"] else "",
             "komentar": "spam promo murah klik sekarang juga" if n % 3 == 0 else f"{row['komentar']} {n}"}
            for n in range(12) for row in base]
    monkeypatch.setattr(sources, "execute", lambda *a, **k: (rows, {}, None))
    manager = JobManager(tmp_path / "data")
    manager.start("youtube", {"url": "https://youtu.be/x"}, project="Komentar")
    manager._thread.join(5)
    for key, title in (("balasan", "Jaringan Balasan"), ("interaksi", "Interaksi & Akun"),
                       ("berulang", "Teks Berulang")):
        at = AppTest.from_file(_page(tmp_path, key), default_timeout=60)
        at.session_state["manager"] = manager
        at.run()
        assert not at.exception, (key, at.exception)
        assert at.title[0].value == title
        assert any(code.value for code in at.code), key  # the methods paragraph
