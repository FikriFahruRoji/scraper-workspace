"""The X scraper must always go through Scrapling's stealth browser."""
from workspace_app import sources


def test_session_options_enable_stealth():
    scraper = sources.load("x")
    options = scraper._session_options(
        headless=True, real_chrome=False, user_data_dir="/tmp/x-profile"
    )
    assert options["headless"] is True
    assert options["real_chrome"] is False
    assert options["user_data_dir"] == "/tmp/x-profile"
    assert options["locale"] == "en-US"
    assert options["timezone_id"] == "Asia/Jakarta"
    assert options["hide_canvas"] is True
    assert options["block_webrtc"] is True
    assert options["allow_webgl"] is True


def test_every_x_entry_point_uses_stealth_session(tmp_path, monkeypatch):
    scraper = sources.load("x")
    recorded = []

    class FakeSession:
        def __init__(self, **kwargs):
            recorded.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def fetch(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(scraper, "StealthySession", FakeSession)
    profile = str(tmp_path / "x-profile")

    scraper.login(user_data_dir=profile, real_chrome=False, timeout_s=0)
    scraper.is_logged_in(user_data_dir=profile, real_chrome=False)
    scraper.scrape_tweets(mode="search", keyword="contoh", user_data_dir=profile)

    assert len(recorded) == 3
    for options in recorded:
        assert options["hide_canvas"] is True
        assert options["block_webrtc"] is True
        assert options["locale"] == "en-US"
        assert options["timezone_id"] == "Asia/Jakarta"
    assert recorded[0]["headless"] is False  # login must stay visible
    assert recorded[1]["headless"] is True
    assert recorded[2]["record_requests"] is True
