"""Orkestrasi scraping multi-portal dengan satu browser stealth Scrapling."""

from __future__ import annotations

import asyncio
from datetime import date
from itertools import islice
from typing import Callable

from .core import Article
from .fetchers import open_session
from .portals import PORTALS, Blocked, iter_discover, parse
from .query import describe, discovery_queries, matches, parse_expression

DISCOVER_MAX_PAGES = 1000


def run_scrape(
    portal_keys: list[str],
    keyword: str,
    start_date: str | None = None,
    end_date: str | None = None,
    headless: bool = True,
    solve_cloudflare: bool = False,
    disable_resources: bool = True,
    delay: float = 0.2,
    log_cb: Callable[[str, int], None] | None = None,
    *,
    max_tabs: int = 4,
    stop_event=None,
    checkpoint_cb=None,
) -> dict:
    """Titik masuk sinkron untuk Streamlit."""
    return asyncio.run(_run_scrape(
        portal_keys, keyword, start_date, end_date, headless, solve_cloudflare,
        disable_resources, delay, log_cb, max(1, min(max_tabs, 6)), stop_event, checkpoint_cb,
    ))


async def _run_scrape(
    portal_keys, keyword, start_date, end_date, headless, solve_cloudflare,
    disable_resources, delay, log_cb, max_tabs, stop_event, checkpoint_cb,
):
    ast = parse_expression(keyword)
    queries = discovery_queries(ast)
    portal_keys = list(dict.fromkeys(portal_keys))
    stats_by_portal = {key: {"found": 0, "kept": 0, "error": None} for key in portal_keys}
    articles_by_portal = {key: [] for key in portal_keys}
    errors = []
    seen = set()
    count = 0
    start = date.fromisoformat(start_date) if start_date else None
    end = date.fromisoformat(end_date) if end_date else None
    portal_slots = asyncio.Semaphore(max_tabs)
    tabs = asyncio.Semaphore(max_tabs)

    def in_range(value):
        if not value:
            return True
        day = date.fromisoformat(value)
        return (start is None or day >= start) and (end is None or day <= end)

    def log(message):
        if log_cb:
            log_cb(message, count)

    def stopped():
        return stop_event is not None and stop_event.is_set()

    class StopRequested(Exception):
        pass

    class JobSession:
        def __init__(self, session):
            self.session = session

        async def fetch(self, *args, **kwargs):
            async with tabs:
                if stopped():
                    raise StopRequested
                return await self.session.fetch(*args, **kwargs)

    async def collect(key, session):
        nonlocal count
        portal = PORTALS[key]
        stats = stats_by_portal[key]

        async def read(item):
            try:
                return await parse(portal, item["url"], session)
            except Exception as exc:
                return exc

        async with portal_slots:
            if stopped():
                return
            log(f"[{portal.label}] mulai · query: {', '.join(queries)}")
            try:
                # RSS feeds do not depend on the discovery query.
                for query in (queries[:1] if portal.kind == "rss" else queries):
                    page_no = 0
                    if stopped():
                        return
                    async for items in iter_discover(
                        portal, query, start_date, end_date, session, DISCOVER_MAX_PAGES
                    ):
                        if stopped():
                            return
                        page_no += 1
                        candidates = []
                        for item in items:
                            if item["url"] in seen or not in_range(item.get("date_iso")):
                                continue
                            if portal.kind == "rss" and (item.get("title") or item.get("snippet")):
                                text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
                                if not any(q.lower() in text for q in queries):
                                    continue
                            seen.add(item["url"])
                            candidates.append(item)
                        stats["found"] += len(candidates)
                        kept = 0
                        log(f"[{portal.label}] halaman {page_no} · {len(candidates)} URL baru")

                        # Satu portal mengirim maksimal dua navigasi artikel sekaligus.
                        pending = iter(candidates)
                        while batch := list(islice(pending, min(2, max_tabs))):
                            if stopped():
                                return
                            outcomes = await asyncio.gather(*(read(item) for item in batch))
                            blocked = None
                            before_batch = kept
                            for item, art in zip(batch, outcomes):
                                if isinstance(art, Blocked):
                                    blocked = art
                                    continue
                                if isinstance(art, Exception):
                                    log(f"[{portal.label}] gagal membaca artikel: {str(art)[:100]}")
                                    continue
                                if not art or not in_range(art["date_iso"]):
                                    continue
                                if not matches(ast, f"{art['title']} {art['content']}"):
                                    continue

                                articles_by_portal[key].append(Article(
                                    portal=portal.label,
                                    title=art["title"] or item.get("title", ""),
                                    url=item["url"],
                                    date_iso=art["date_iso"] or item.get("date_iso", ""),
                                    display_date=art["display_date"] or item.get("display_date", ""),
                                    category=art["category"] or item.get("category", ""),
                                    image=art["image"] or item.get("image", ""),
                                    snippet=art["snippet"] or item.get("snippet", ""),
                                    content=art["content"],
                                    paragraphs=art["paragraphs"],
                                ))
                                kept += 1
                                count += 1
                                stats["kept"] += 1

                            if checkpoint_cb and kept > before_batch:
                                checkpoint_cb([
                                    article for source in portal_keys
                                    for article in articles_by_portal[source]
                                ])
                            if stopped():
                                return
                            if blocked:
                                raise blocked
                            if delay:
                                await asyncio.sleep(delay)
                        log(f"[{portal.label}] halaman {page_no}: {kept} cocok dari {len(candidates)} URL baru")
            except StopRequested:
                return
            except Exception as exc:
                stats["error"] = str(exc) or type(exc).__name__
                errors.append(f"{portal.label}: {stats['error']}")
                log(f"[{portal.label}] berhenti: {stats['error']}")
            log(f"[{portal.label}] selesai · {stats['kept']} artikel disimpan")

    if portal_keys:
        async with open_session(
            headless=headless, solve_cloudflare=solve_cloudflare,
            disable_resources=disable_resources, max_pages=max_tabs,
        ) as session:
            job_session = JobSession(session)
            await asyncio.gather(*(collect(key, job_session) for key in portal_keys))
    return {
        "articles": [article for key in portal_keys for article in articles_by_portal[key]],
        "errors": errors,
        "portal_stats": stats_by_portal,
        "total_hint": 0,
        "query": describe(ast),
    }
