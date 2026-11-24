import json
from datetime import date, timedelta
from threading import Event

import pytest

from workspace_app import sources
from workspace_app.disaster_view import check


def gdacs_feature(kind="FL", event_id=1, start="2026-10-24T01:00:00", alert="Orange", country="Indonesia"):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [97.0, 4.4]},
            "properties": {"eventtype": kind, "eventid": event_id, "name": f"Flood in {country}",
                           "alertlevel": alert, "alertscore": 2, "country": country, "fromdate": start,
                           "todate": "2026-11-25T01:00:00", "iscurrent": "true", "source": "GLOFAS",
                           "severitydata": {"severity": 0.0, "severitytext": "Magnitude 0", "severityunit": ""},
                           "url": {"report": "https://www.gdacs.org/report.aspx?eventid=1"}}}


def eonet_event(event_id="EONET_1", category="landslides", source="NASA", closed=None):
    return {"id": event_id, "title": "Landslide in Java", "link": f"https://eonet/{event_id}", "closed": closed,
            "categories": [{"id": category}], "sources": [{"id": source}],
            "geometry": [{"date": "2026-10-02T00:00:00Z", "type": "Point", "coordinates": [110.0, -7.0]},
                         {"date": "2026-10-01T00:00:00Z", "type": "Polygon",
                          "coordinates": [[[100.0, 0.0], [102.0, 0.0], [102.0, 2.0], [100.0, 2.0]]],
                          "magnitudeValue": 35.0, "magnitudeUnit": "kts"}]}


@pytest.fixture
def module():
    return sources.load("bencana")


def fake(monkeypatch, module, gdacs=(), eonet=()):
    urls = []

    def get(url, **_kw):
        urls.append(url)
        if "gdacs" in url:
            if isinstance(gdacs, Exception):
                raise gdacs
            return json.dumps({"features": list(gdacs)}).encode() if gdacs else b""
        if isinstance(eonet, Exception):
            raise eonet
        return json.dumps({"events": list(eonet)}).encode()

    monkeypatch.setattr(module, "get", get)
    return urls


def test_rows_from_both_sources_are_wib_labelled_and_deduplicated(monkeypatch, module):
    urls = fake(monkeypatch, module, gdacs=[gdacs_feature()],
                eonet=[eonet_event(), eonet_event("EONET_2", "floods", source="GDACS")])
    result = module.collect_disasters("2026-10-01", "2026-11-24")
    rows = result["rows"]
    assert result["error"] is None and len(urls) == 4  # two monthly windows, one call per source each
    assert "country=Indonesia" in urls[0] and "eventlist=FL;TC;VO;DR;WF" in urls[0]
    assert "bbox=94.0,6.5,142.0,-11.5" in urls[1] and "landslides" in urls[1]
    assert [row["id_kejadian"] for row in rows] == ["FL1", "EONET_1"]  # repeats and GDACS copies are dropped
    flood, slide = rows
    assert flood["mulai_wib"] == "2026-10-24T08:00:00+07:00" and flood["peringatan"] == "oranye"
    assert flood["jenis"] == "Banjir" and flood["status"] == "berlangsung" and flood["lintang"] == 4.4
    assert slide["jenis"] == "Tanah longsor" and slide["mulai_utc"] == "2026-10-01T00:00:00+00:00"
    assert (slide["lintang"], slide["bujur"]) == (-7.0, 110.0)  # latest observation, not the first in the list


def test_polygon_centre_and_magnitude(module):
    event = eonet_event()
    event["geometry"] = event["geometry"][1:]
    row = module.parse_eonet(event)
    assert (row["lintang"], row["bujur"]) == (1.0, 101.0) and row["keparahan"] == "35 kts"


def test_single_provider_world_alert_and_types(monkeypatch, module):
    urls = fake(monkeypatch, module, gdacs=[gdacs_feature("VO", 7)])
    module.collect_disasters("2026-11-01", "2026-11-24", region="dunia", provider="gdacs",
                             types=["gunung_api", "longsor"], min_alert="orange")
    assert len(urls) == 1 and "country" not in urls[0]
    assert "eventlist=VO&" in urls[0] and "alertlevel=orange;red" in urls[0]
    eonet_only = fake(monkeypatch, module, eonet=[eonet_event("E", "floods", source="GDACS")])
    result = module.collect_disasters("2026-11-01", "2026-11-24", provider="eonet")
    assert all("gdacs" not in url for url in eonet_only) and len(result["rows"]) == 1  # kept without GDACS


def test_gdacs_pages_until_short_page(monkeypatch, module):
    pages = []

    def get(url, **_kw):
        pages.append(url)
        if "gdacs" not in url:
            return b'{"events": []}'
        number = len([p for p in pages if "gdacs" in p])
        size = module.PAGE_SIZE if number < 3 else 1
        return json.dumps({"features": [gdacs_feature(event_id=number * 1000 + i) for i in range(size)]}).encode()

    monkeypatch.setattr(module, "get", get)
    result = module.collect_disasters("2026-11-01", "2026-11-24")
    assert [url[-12:] for url in pages if "gdacs" in url] == ["pageNumber=1", "pageNumber=2", "pageNumber=3"]
    assert len(result["rows"]) == 2 * module.PAGE_SIZE + 1


def test_one_failing_source_keeps_the_other_and_count_and_stop(monkeypatch, module):
    fake(monkeypatch, module, gdacs=module.ApiError("Server menjawab 503."), eonet=[eonet_event()])
    result = module.collect_disasters("2026-11-01", "2026-11-24")
    assert result["error"].startswith("GDACS:") and len(result["rows"]) == 1
    fake(monkeypatch, module, gdacs=[gdacs_feature(event_id=i) for i in range(3)])
    assert len(module.collect_disasters("2026-11-01", "2026-11-24", count=2)["rows"]) == 2
    calls = fake(monkeypatch, module)
    stop = Event()
    stop.set()
    assert module.collect_disasters("2026-11-01", "2026-11-24", stop_event=stop)["stop_reason"] == "dihentikan"
    assert not calls
    empty = module.collect_disasters("2026-11-01", "2026-11-24")
    assert empty["rows"] == [] and "Tidak ada kejadian" in empty["message"]


def test_windows_cover_range_newest_first(module):
    windows = list(module.months(date(2026, 1, 1), date(2026, 3, 15)))
    assert windows[0][1] == date(2026, 3, 15) and windows[-1][0] == date(2026, 1, 1)
    assert all(later[0] - earlier[1] == timedelta(days=1) for later, earlier in zip(windows, windows[1:]))


def test_check_validates_form(module):
    today = date.today()
    base = {"region": "indonesia", "provider": "semua", "types": ["banjir"], "min_alert": "green",
            "start_date": today - timedelta(days=30), "end_date": today}
    check(dict(base))
    for change in ({"types": []}, {"types": ["gempa"]}, {"region": "mars"}, {"provider": "x"},
                   {"provider": "gdacs", "types": ["longsor"]}, {"end_date": today + timedelta(days=1)},
                   {"start_date": today, "end_date": today - timedelta(days=1)}, {"start_date": date(1999, 1, 1)},
                   {"start_date": today - timedelta(days=366 * 11)}, {"min_alert": "blue"}):
        with pytest.raises(ValueError):
            check({**base, **change})


def test_points_get_kabupaten_and_province_from_bundled_outlines(module):
    assert module.locate(-6.9175, 107.6191) == ("Kota Bandung", "Jawa Barat", 0.0)
    assert module.locate(-4.546, 136.88)[:2] == ("Kabupaten Mimika", "Papua Tengah")  # 2022 province split
    assert module.locate(-0.876, 131.25)[:2] == ("Kota Sorong", "Papua Barat Daya")
    assert module.locate(3.14, 101.69) == ("", "", None)  # Kuala Lumpur: outside Indonesia
    assert module.locate(None, 110.0) == ("", "", None)
    name, province, distance = module.locate(-6.0, 106.83)  # Jakarta Bay, just off the coast
    assert province == "DKI Jakarta" and 0 < distance <= module.NEAR_KM
    provinces = {area[1] for area in module._areas()}
    assert len(provinces) == 38 and "DKI Jakarta" in provinces


def test_collected_rows_carry_region_columns_after_country(monkeypatch, module):
    fake(monkeypatch, module, gdacs=[gdacs_feature()])
    row = module.collect_disasters("2026-11-01", "2026-11-24", provider="gdacs")["rows"][0]
    keys = list(row)
    assert keys[keys.index("negara") + 1:keys.index("negara") + 4] == ["provinsi", "kabupaten_kota", "jarak_wilayah_km"]
    assert row["provinsi"] == "Aceh" and row["kabupaten_kota"].startswith("Kabupaten ")
