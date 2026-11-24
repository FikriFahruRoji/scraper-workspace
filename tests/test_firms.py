import io
from datetime import date
from threading import Event

import pytest

from workspace_app import sources
from workspace_app.firms_view import check

HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight,type\n"
VIIRS = HEADER + (
    "-9.1,124.6,336.4,0.38,0.36,2024-01-01,0512,N,VIIRS,n,2,302.2,3.61,D,0\n"
    "-8.2,123.5,348.9,0.39,0.36,2024-01-01,512,N,VIIRS,l,2,307.7,10.97,N,1\n"
    "-7.0,110.1,340.0,0.4,0.4,2024-12-31,1830,N,VIIRS,h,2,300.0,5.0,N,0\n"
    "bad,row,,,,,,,,,,,,,\n")
MODIS = ("latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight,type\n"
         "-3.2,114.9,311.4,1,1,2024-01-01,0214,Terra,MODIS,59,61.03,285.7,6.9,D,0\n"
         "-1.8,115.8,305.4,1,1,2024-01-01,0214,Aqua,MODIS,12,61.03,276.5,3.7,D,2\n")


@pytest.fixture
def module():
    return sources.load("firms")


@pytest.fixture(autouse=True)
def no_pause(monkeypatch, module):
    monkeypatch.setattr(module, "PAUSE", 0)


def fake(monkeypatch, module, body):
    urls = []

    def get(url, **_kw):
        urls.append(url)
        if callable(body):
            return body(url)
        return body.encode()

    monkeypatch.setattr(module, "get", get)
    return urls


def test_rows_are_numeric_wib_and_labelled(monkeypatch, module):
    urls = fake(monkeypatch, module, VIIRS)
    result = module.collect_hotspots("Indonesia", "viirs-snpp", 2024, 2024)
    rows = result["rows"]
    assert urls == ["https://firms.modaps.eosdis.nasa.gov/data/country/viirs-snpp/2024/viirs-snpp_2024_Indonesia.csv"]
    assert len(rows) == 3 and result["error"] is None  # the malformed line is skipped
    first = rows[0]
    assert first["waktu_utc"] == "2024-01-01T05:12+00:00" and first["waktu_wib"] == "2024-01-01T12:12+07:00"
    assert (first["lintang"], first["bujur"]) == (-9.1, 124.6)  # south keeps its sign
    assert first["keyakinan"] == "nominal" and first["jenis"] == "kebakaran vegetasi" and first["siang_malam"] == "siang"
    assert rows[1]["waktu_utc"] == "2024-01-01T05:12+00:00"  # 3-digit time is zero padded
    assert rows[2]["waktu_wib"] == "2025-01-01T01:30+07:00"  # WIB crosses midnight into the next day


def test_filters_and_count(monkeypatch, module):
    fake(monkeypatch, module, VIIRS)
    assert len(module.collect_hotspots("Indonesia", "viirs-snpp", 2024, 2024, only_confident=True)["rows"]) == 2
    assert len(module.collect_hotspots("Indonesia", "viirs-snpp", 2024, 2024, only_vegetation=True)["rows"]) == 2
    both = module.collect_hotspots("Indonesia", "viirs-snpp", 2024, 2024, only_confident=True, only_vegetation=True)
    assert [row["keyakinan"] for row in both["rows"]] == ["nominal", "tinggi"]
    assert len(module.collect_hotspots("Indonesia", "viirs-snpp", 2024, 2024, count=1)["rows"]) == 1


def test_modis_confidence_is_numeric_percent(monkeypatch, module):
    fake(monkeypatch, module, MODIS)
    result = module.collect_hotspots("Indonesia", "modis", 2024, 2024, only_confident=True)
    assert [(row["keyakinan"], row["satelit"], row["kecerahan_k"]) for row in result["rows"]] == [(59, "Terra", 311.4)]


def test_missing_year_is_skipped_and_others_survive(monkeypatch, module):
    def body(url):
        if "_2023_" in url:
            raise module.ApiError("Server menjawab 404 (Not Found).")
        return VIIRS.encode()

    fake(monkeypatch, module, body)
    result = module.collect_hotspots("Indonesia", "viirs-snpp", 2023, 2024)
    assert len(result["rows"]) == 3 and result["error"] is None
    empty = module.collect_hotspots("Nowhere", "viirs-snpp", 2023, 2023)
    assert empty["rows"] == [] and "Arsip FIRMS" in empty["message"]


def test_server_failure_stops_with_error_and_stop_event_halts(monkeypatch, module):
    def body(url):
        raise module.ApiError("Tidak bisa menghubungi server.")

    fake(monkeypatch, module, body)
    assert "2024" in module.collect_hotspots("Indonesia", "modis", 2024, 2025)["error"]
    calls = fake(monkeypatch, module, VIIRS)
    stop = Event()
    stop.set()
    assert module.collect_hotspots("Indonesia", "modis", 2024, 2024, stop_event=stop)["stop_reason"] == "dihentikan"
    assert not calls


def test_country_names_normalise(module):
    assert module.normalize_country(" papua new guinea ") == "Papua_New_Guinea"
    assert module.normalize_country("Timor-Leste") == "Timor-Leste"
    for bad in ("", "../etc", "Indo?nesia"):
        with pytest.raises(ValueError):
            module.normalize_country(bad)


def test_check_validates_years_and_country(module):
    newest = module.last_year()
    base = {"country": "indonesia", "sensor": "viirs-snpp", "start_year": newest - 1, "end_year": newest}
    check(base)
    assert base["country"] == "Indonesia"
    for change in ({"start_year": newest, "end_year": newest - 1}, {"end_year": newest + 1}, {"start_year": 2005},
                   {"sensor": "x"}, {"country": ""}, {"start_year": 2012, "end_year": 2012 + 20}):
        with pytest.raises(ValueError):
            check({**base, **change})
