"""Instagram keyword search responses (xdt_fbsearch__top_serp_graphql) must parse."""
from workspace_app import sources


def _state():
    scraper = sources.load("instagram")
    return scraper, scraper._new_state()


def test_keyword_search_response_marker_registered():
    scraper, _ = _state()
    assert "xdt_fbsearch__top_serp_graphql" in scraper._NETWORK_MARKERS


def test_keyword_search_media_is_ingested():
    scraper, state = _state()
    data = {
        "data": {
            "xdt_fbsearch__top_serp_graphql": {
                "edges": [
                    {
                        "node": {
                            "items": [
                                {
                                    "__typename": "XDTMediaDict",
                                    "pk": "123456789",
                                    "code": "AbC123xyz",
                                    "media_type": 2,
                                    "taken_at": 1791999256,
                                    "like_count": 505,
                                    "comment_count": 28,
                                    "view_count": 900,
                                    "caption": {"text": "Halo kucing lucu"},
                                    "user": {
                                        "username": "catlover",
                                        "full_name": "Cat Lover",
                                        "is_verified": True,
                                    },
                                    "image_versions2": {
                                        "candidates": [
                                            {"url": "https://example.com/thumb.jpg"}
                                        ]
                                    },
                                }
                            ]
                        }
                    }
                ],
                "page_info": {"has_next_page": True, "end_cursor": "abc"},
            }
        }
    }
    comments, media, _profiles = scraper._ingest_json(data, state)
    assert (comments, media) == (0, 1)
    row = state["media"]["AbC123xyz"]
    assert row["username"] == "catlover"
    assert row["nama"] == "Cat Lover"
    assert row["caption"] == "Halo kucing lucu"
    assert row["suka"] == 505
    assert row["komentar"] == 28
    assert row["tayangan"] == 900
    assert row["thumbnail"] == "https://example.com/thumb.jpg"
    assert row["url"] == "https://www.instagram.com/p/AbC123xyz/"
    assert row["waktu"].startswith("2026-")
    assert row["media"] == "Video"
