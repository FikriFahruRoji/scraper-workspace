import json
from datetime import date
from threading import Event

import pytest

from workspace_app import sources
from workspace_app.pangan_view import check

GRID = [
    {"no": "I", "name": "Beras", "level": 1, "18/11/2026": "16,550", "19/11/2026": "-"},
    {"no": 1, "name": "Beras Kualitas Bawah I", "level": 2, "18/11/2026": "15,050", "19/11/2026": "15,000"},
    {"no": "II", "name": "Telur Ayam", "level": 1, "18/11/2026": "29,000", "19/11/2026": "29,100"},
    {"no": 1, "name": "Telur Ayam Ras Segar", "level": 2, "18/11/2026": "29,000", "19/11/2026": ""},
]


@pytest.fixture
def module():
    return sources.load("pangan")


@pytest.fixture(autouse=True)
def no_pause(monkeypatch, module):
    monkeypatch.setattr(module, "PAUSE", 0)


def fake(monkeypatch, module, grid=GRID):
    urls = []

    def get(url, **_kw):
        urls.append(url)
        return json.dumps({"data": grid}).encode()

    monkeypatch.setattr(module, "get", get)
    return urls


def test_parse_price(module):
    assert module.parse_price("16,550") == 16550
    assert module.parse_price("1.250") == 1250
    assert module.parse_price("-") is None and module.parse_price("") is None and module.parse_price(None) is None


def test_windows_cover_range_without_overlap(module):
    spans = list(module.windows(date(2026, 1, 1), date(2026, 4, 1), days=60))
    assert spans[0][0] == date(2026, 1, 1) and spans[-1][1] == date(2026, 4, 1)
    for (_, last), (first, _) in zip(spans, spans[1:]):
        assert (first - last).days == 1
    assert all((b - a).days < 60 for a, b in spans)


def test_parse_grid_skips_category_rows_and_missing_prices(module):
    rows = list(module.parse_grid(GRID, "DKI Jakarta", "Pasar Tradisional"))
    assert [(r["tanggal"], r["komoditas"], r["harga"], r["kategori"]) for r in rows] == [
        ("2026-11-18", "Beras Kualitas Bawah I", 15050, "Beras"),
        ("2026-11-19", "Beras Kualitas Bawah I", 15000, "Beras"),
        ("2026-11-18", "Telur Ayam Ras Segar", 29000, "Telur Ayam"),
    ]


def test_collect_filters_by_category_and_builds_requests(monkeypatch, module):
    urls = fake(monkeypatch, module)
    result = module.collect_prices(["13", "0"], ["1", "4"], "1", date(2026, 11, 18), date(2026, 11, 19))
    assert len(urls) == 2 and "province_id=13" in urls[0] and "province_id=&" in urls[1] and "comcat_id=&" in urls[0]
    assert {r["provinsi"] for r in result["rows"]} == {"DKI Jakarta", "Nasional (rata-rata)"}
    assert result["error"] is None and len(result["rows"]) == 6


def test_single_category_is_requested_directly_and_others_filtered(monkeypatch, module):
    urls = fake(monkeypatch, module)
    result = module.collect_prices(["13"], ["1"], "1", date(2026, 11, 18), date(2026, 11, 19))
    assert "comcat_id=com_1&" in urls[0]
    assert {r["kategori"] for r in result["rows"]} == {"Beras"}


def test_one_failed_window_is_reported_but_others_survive(monkeypatch, module):
    calls = []

    def get(url, **_kw):
        calls.append(url)
        if len(calls) == 1:
            raise module.ApiError("Server menjawab 500.")
        return json.dumps({"data": GRID}).encode()

    monkeypatch.setattr(module, "get", get)
    result = module.collect_prices(["13"], [], "1", date(2026, 1, 1), date(2026, 4, 1))
    assert result["rows"] and "500" in result["error"]


def test_stop_event_and_empty_result(monkeypatch, module):
    urls = fake(monkeypatch, module, grid=[])
    result = module.collect_prices(["13"], [], "1", date(2026, 11, 21), date(2026, 11, 22))
    assert result["rows"] == [] and "PIHPS tidak mencatat" in result["message"]
    stop = Event()
    stop.set()
    assert module.collect_prices(["13"], [], "1", date(2026, 11, 1), date(2026, 11, 2), stop_event=stop)["stop_reason"] == "dihentikan"


def test_check_rejects_bad_input():
    base = {"provinces": ["13"], "categories": [], "price_type": "1", "start_date": date(2026, 1, 1),
            "end_date": date(2026, 2, 1)}
    check(base)
    for change in ({"provinces": []}, {"start_date": date(2026, 3, 1)}, {"end_date": date(2999, 1, 1)},
                   {"price_type": "9"}, {"start_date": date(2000, 1, 1)}, {"start_date": None}):
        with pytest.raises(ValueError):
            check({**base, **change})
