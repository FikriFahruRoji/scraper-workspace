from datetime import datetime
from threading import Event

import pytest

from workspace_app import sources
from workspace_app.jobs import default_project
from workspace_app.views import validate

PLACE_LINK = (
    "https://www.google.com/maps/place/Kopi+Toko+Djawa+%28Braga%29/data=!4m7!3m6!1s"
    "0x2e68e73dd7d12d7f:0x55a80015d69222be!8m2!3d-6.9174648!4d107.6091855"
    "!16s%2Fg%2F11f3jnbqts!19sChIJfy3R1z3naC4RviKS1hUAqFU?hl=id"
)


def places_module():
    return sources.load("gmaps_places")


def test_build_places_url_with_and_without_area():
    module = places_module()
    assert module.build_places_url("kopi bandung") == (
        "https://www.google.com/maps/search/kopi+bandung?hl=id&gl=id"
    )
    assert module.build_places_url("kopi", "Bandung") == (
        "https://www.google.com/maps/search/kopi+Bandung?hl=id&gl=id"
    )


def test_parse_place_card_extracts_list_information():
    module = places_module()
    row = module._parse_place_card(
        {
            "name": "Kopi Toko Djawa (Braga)",
            "rating": "4,6",
            "reviews": "(4.598)",
            "meta": "Kedai Kopi ·  · Jl. Braga No.81",
            "status": "Buka · Tutup pukul 22.00",
            "description": "Kedai kopi legendaris",
            "info": ["4,6(4.598)", "Kedai Kopi ·  · Jl. Braga No.81", "Buka · Tutup pukul 22.00"],
            "link": PLACE_LINK,
        }
    )
    assert row["nama"] == "Kopi Toko Djawa (Braga)"
    assert row["kategori"] == "Kedai Kopi"
    assert row["alamat"] == "Jl. Braga No.81"
    assert row["rating"] == pytest.approx(4.6)
    assert row["jumlah_ulasan"] == 4598
    assert row["status_buka"] == "Buka"
    assert row["jam_buka"] == "Tutup pukul 22.00"
    assert row["deskripsi"] == "Kedai kopi legendaris"
    assert row["lintang"] == pytest.approx(-6.9174648)
    assert row["bujur"] == pytest.approx(107.6091855)
    assert row["place_id"] == "0x2e68e73dd7d12d7f:0x55a80015d69222be"
    assert row["detail_diambil"] is False


def test_parse_place_card_handles_price_and_missing_coordinates():
    module = places_module()
    row = module._parse_place_card(
        {
            "name": "Warung Nasi",
            "rating": "4,2",
            "reviews": "1,2 rb",
            "meta": "Warung · Rp 25–50 rb · Jl. Merdeka No.1",
            "status": "Tutup · Buka Sen pukul 08.00",
            "link": "https://www.google.com/maps/place/Warung+Nasi",
        }
    )
    assert row["kategori"] == "Warung"
    assert row["alamat"] == "Jl. Merdeka No.1"
    assert row["rentang_harga"] == "Rp 25–50 rb"
    assert row["jumlah_ulasan"] == 1200
    assert row["status_buka"] == "Tutup"
    assert row["jam_buka"] == "Buka Sen pukul 08.00"
    assert row["lintang"] is None
    assert row["bujur"] is None


def test_parse_place_card_rejects_cards_without_a_place_link():
    module = places_module()
    assert module._parse_place_card({"name": "Tanpa link"}) is None


def test_parse_detail_and_merge_into_a_list_row():
    module = places_module()
    detail = module._parse_detail(
        {
            "nama": "Kopi Toko Djawa (Braga)",
            "kategori": "Kedai Kopi",
            "alamat": "Jl. Braga No.81, Bandung",
            "telepon": "0899-6979-748",
            "website": "http://www.instagram.com/kopitokodjawa",
            "plus_code": "3JM5+2M Braga, Kota Bandung",
            "jam_lengkap": "Senin,08.00 hingga 22.00 · Selasa,08.00 hingga 22.00",
            "rating_teks": "4,6",
            "jumlah_ulasan_teks": "4.598 ulasan",
        },
        PLACE_LINK,
    )
    row = module._parse_place_card(
        {
            "name": "Kopi Toko Djawa (Braga)",
            "rating": "4,6",
            "reviews": "(4.598)",
            "meta": "Kedai Kopi · Jl. Braga No.81",
            "status": "Buka · Tutup pukul 22.00",
            "link": PLACE_LINK,
        }
    )
    module._merge_detail(row, detail)
    assert row["telepon"] == "0899-6979-748"
    assert row["website"] == "http://www.instagram.com/kopitokodjawa"
    assert row["plus_code"] == "3JM5+2M Braga, Kota Bandung"
    assert row["jam_lengkap"].startswith("Senin,08.00")
    assert row["rating"] == pytest.approx(4.6)
    assert row["jumlah_ulasan"] == 4598
    assert row["detail_diambil"] is True


def test_parse_count_handles_indonesian_suffixes():
    module = places_module()
    assert module._parse_count("(4.598)") == 4598
    assert module._parse_count("1,2 rb") == 1200
    assert module._parse_count("3,4 jt") == 3_400_000
    assert module._parse_count("") == 0


def test_sources_execute_routes_to_search_places(tmp_path, monkeypatch):
    captured = {}

    class FakeModule:
        def search_places(self, **kwargs):
            captured.update(kwargs)
            return {"rows": [{"nama": "Kopi"}], "keyword": "kopi", "error": None, "message": ""}

        def scrape_reviews(self, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("scrape_reviews should not run for gmaps_places")

    monkeypatch.setattr(sources, "load", lambda source: FakeModule())
    rows, metadata, error = sources.execute(
        "gmaps_places",
        {
            "keyword": "kopi",
            "area": "Bandung",
            "max_places": 20,
            "detail_limit": 0,
            "headless": True,
            "max_minutes": 5,
        },
        tmp_path / "profile",
        Event(),
        lambda message: None,
        lambda rows: None,
    )
    assert rows == [{"nama": "Kopi"}]
    assert error is None
    assert captured["keyword"] == "kopi"
    assert captured["area"] == "Bandung"
    assert captured["max_places"] == 20
    assert captured["detail_limit"] == 0
    assert captured["user_data_dir"] == str(tmp_path / "profile")
    assert captured["real_chrome"] is False
    assert callable(captured["checkpoint_cb"])
    assert metadata["keyword"] == "kopi"


def test_validate_requires_a_place_keyword():
    validate("gmaps_places", {"keyword": "kopi"})
    with pytest.raises(ValueError, match="input pencarian"):
        validate("gmaps_places", {"keyword": "   "})


def test_default_project_name_for_place_search():
    when = datetime(2026, 11, 22)
    assert default_project("gmaps_places", {"keyword": "kopi bandung"}, when) == (
        "Google Maps Lokasi · kopi bandung · 22-11-2026"
    )
