"""Parsing must support both the old X schema and the 2026 x-web schema."""
from workspace_app import sources


def _tweet(payload):
    return {"data": {"tweet_results": {"result": payload}}}


def test_new_web_schema_fields():
    scraper = sources.load("x")
    payload = {
        "__typename": "Tweet",
        "rest_id": "2123266805963903204",
        "legacy": {"lang": "in"},
        "core": {
            "user_results": {
                "result": {
                    "__typename": "User",
                    "core": {"name": "Mita", "screen_name": "MitdaYenti"},
                    "location": {"location": "Jakarta"},
                    "verification": {"is_blue_verified": True},
                }
            }
        },
        "details": {"full_text": "Halo Gibran", "created_at_ms": 1795061229000},
        "counts": {
            "favorite_count": 5,
            "reply_count": 2,
            "retweet_count": 1,
            "quote_count": 3,
        },
        "views": {"count": "8"},
        "reply_to_user_results": {"result": {"core": {"screen_name": "NyaiNeneng"}}},
        "reply_to_results": {"rest_id": "99"},
    }
    rows = scraper._parse_responses([_tweet(payload)], None, None)
    assert len(rows) == 1
    row = rows[0]
    assert row["text"] == "Halo Gibran"
    assert row["waktu"].startswith("2026-11-19")
    assert row["nama"] == "Mita"
    assert row["username"] == "MitdaYenti"
    assert row["VERIFIED"] is True
    assert row["USER_LOCATION"] == "Jakarta"
    assert row["FAVORITE_COUNT"] == 5
    assert row["REPLY_COUNT"] == 2
    assert row["RETWEET_COUNT"] == 1
    assert row["QUOTE_COUNT"] == 3
    assert row["VIEW_COUNT"] == 8
    assert row["IN_REPLY_TO"] == "NyaiNeneng"
    assert row["IN_REPLY_TO_URL"].endswith("/status/99")
    assert row["TWEET URL"] == "https://x.com/MitdaYenti/status/2123266805963903204"


def test_old_schema_still_parses():
    scraper = sources.load("x")
    payload = {
        "__typename": "Tweet",
        "rest_id": "111",
        "legacy": {
            "full_text": "Tweet lama",
            "created_at": "Wed Nov 12 12:34:56 +0000 2026",
            "lang": "id",
            "favorite_count": 7,
            "reply_count": 1,
            "retweet_count": 2,
            "quote_count": 3,
            "in_reply_to_screen_name": "lama",
            "in_reply_to_status_id_str": "222",
        },
        "core": {
            "user_results": {
                "result": {
                    "__typename": "User",
                    "core": {"name": "Lama", "screen_name": "lama_user"},
                    "verification": {"verified": True},
                }
            }
        },
        "views": {"count": "10"},
    }
    rows = scraper._parse_responses([_tweet(payload)], None, None)
    assert len(rows) == 1
    row = rows[0]
    assert row["text"] == "Tweet lama"
    assert row["waktu"] == "2026-11-12 19:34:56 +0700"
    assert row["username"] == "lama_user"
    assert row["VERIFIED"] is True
    assert row["FAVORITE_COUNT"] == 7
    assert row["IN_REPLY_TO"] == "lama"
    assert row["IN_REPLY_TO_URL"] == "https://x.com/lama/status/222"
