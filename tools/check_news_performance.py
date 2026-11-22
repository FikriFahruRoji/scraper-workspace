"""Measure the news scraper with a real stealth browser and repeatable pages."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sys
from threading import Thread
from time import perf_counter, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scrapers"))

from news import run_scrape
from news.core import Portal
from news.portals import PORTALS


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        sleep(0.35)
        path = self.path.split("?")[0]
        base = f"http://127.0.0.1:{self.server.server_port}"
        scope = path.split("/")[1]
        if "blocked" in path:
            status, body = 429, "Too many requests"
        elif "/tag/" in path:
            status = 200
            body = "".join(
                f'<a href="{base}/{scope}/article/{i}">Berita ekonomi</a>'
                for i in range(4)
            )
        else:
            status = 200
            body = ("<h1>Berita ekonomi</h1><article>"
                    "<p>Kegiatan ekonomi daerah berkembang secara bertahap setiap bulan.</p>"
                    "<p>Pelaku usaha menyesuaikan persediaan dengan permintaan konsumen.</p>"
                    "</article>")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def main():
    with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        keys = ["one", "two", "three", "four", "blocked"]
        for key in keys:
            PORTALS[key] = Portal(
                key=key, label=key, base=f"{base}/{key}", kind="tag",
                article_re=re.compile(rf"{re.escape(base)}/{key}/article/\d+"),
                body_selectors=["article p"], tag_page="?page={n}",
            )
        try:
            runs = {}
            for tabs in (1, 4):
                start = perf_counter()
                result = run_scrape(keys, "ekonomi", max_tabs=tabs)
                elapsed = perf_counter() - start
                articles = [article.to_dict() for article in result["articles"]]
                assert len(articles) == 16, (len(articles), result["errors"])
                assert all(article["content"] for article in articles)
                assert result["portal_stats"]["blocked"]["error"] == "blocked membalas HTTP 429"
                runs[tabs] = articles, elapsed
                print(f"{tabs} tab: {elapsed:.2f} s, {len(articles)} articles", flush=True)
            assert runs[1][0] == runs[4][0]
            print(f"Speedup: {runs[1][1] / runs[4][1]:.2f}x", flush=True)
        finally:
            server.shutdown()
            worker.join()


if __name__ == "__main__":
    main()
