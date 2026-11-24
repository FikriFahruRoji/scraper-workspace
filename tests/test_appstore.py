import json
from threading import Event

import pytest

from workspace_app import sources


def entry(number, rating=5, text="bagus", header=False):
    item = {"id": {"label": str(number)}, "author": {"name": {"label": f"user{number}"}},
            "updated": {"label": "2026-11-20T10:31:45-07:00"}, "title": {"label": "Judul"},
            "content": {"label": text}, "im:version": {"label": "1.2"},
            "im:voteSum": {"label": "3"}, "im:voteCount": {"label": "4"}}
    if not header:
        item["im:rating"] = {"label": str(rating)}
    return item


@pytest.fixture
def module():
    return sources.load("appstore")


def feed(monkeypatch, module, pages):
    calls = []

    def fake_get(url, **_kw):
        if "lookup" in url:
            return json.dumps({"results": [{"trackName": "Demo", "artistName": "Dev"}]}).encode()
        calls.append(url)
        page = int(url.split("page=")[1].split("/")[0])
        return json.dumps({"feed": {"entry": pages.get(page, [])}}).encode()

    monkeypatch.setattr(module, "get", fake_get)
    return calls


@pytest.mark.parametrize("value", ["389801252", "id389801252", " https://apps.apple.com/id/app/instagram/id389801252?l=en "])
def test_normalize_accepts_id_and_links(module, value):
    assert module.normalize_app_input(value) == "389801252"


@pytest.mark.parametrize("value", ["", "com.whatsapp", "https://example.com/id389801252", "https://apps.apple.com/id/developer/x"])
def test_normalize_rejects_other_input(module, value):
    with pytest.raises(ValueError):
        module.normalize_app_input(value)


def test_rows_skip_app_header_and_duplicates_and_filters(monkeypatch, module):
    pages = {1: [entry(0, header=True), entry(1, 5), entry(2, 1, text=""), entry(3, 5)],
             2: [entry(3, 5), entry(4, 4)], 3: []}
    feed(monkeypatch, module, pages)
    result = module.scrape_reviews("1", score=None, only_with_text=True)
    assert [row["id_ulasan"] for row in result["rows"]] == ["1", "3", "4"]
    assert result["rows"][0]["aplikasi"] == "Demo" and result["rows"][0]["tanggal"] == "2026-11-20"
    assert result["error"] is None
    only_five = module.scrape_reviews("1", score=5)
    assert {row["rating"] for row in only_five["rows"]} == {5}


def test_count_limits_and_stops_early(monkeypatch, module):
    calls = feed(monkeypatch, module, {1: [entry(n) for n in range(1, 51)], 2: [entry(n) for n in range(51, 101)]})
    result = module.scrape_reviews("1", count=60)
    assert len(result["rows"]) == 60 and len(calls) == 2


def test_stop_event_halts_before_first_page(monkeypatch, module):
    calls = feed(monkeypatch, module, {1: [entry(1)]})
    stop = Event()
    stop.set()
    result = module.scrape_reviews("1", stop_event=stop)
    assert result["stop_reason"] == "dihentikan" and not calls


def test_empty_feed_explains_itself(monkeypatch, module):
    feed(monkeypatch, module, {})
    result = module.scrape_reviews("1", country="zz")
    assert result["rows"] == [] and "Tidak ada ulasan" in result["message"]
