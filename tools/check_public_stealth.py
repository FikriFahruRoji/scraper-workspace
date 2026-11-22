"""Limited live checks on public, non-personal data."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
checks = {
    "saham": """from scraper import scrape_prices
from datetime import date, timedelta
end = date.today() - timedelta(days=1)
out = scrape_prices('AAPL', end - timedelta(days=12), end, market='US', retries=1, timeout=12)
assert out['rows']
print('Yahoo OHLCV:', len(out['rows']))
""",
    "playstore": """from scraper import fetch_app_info
out = fetch_app_info('com.android.chrome')
assert out['nama'] != 'com.android.chrome'
print('Play Store metadata OK')
""",
    "news": """import asyncio
from news.fetchers import open_session
from news.portals import PORTALS, _fetch, _kompas_url
async def check():
    async with open_session(timeout=12000) as session:
        return await _fetch(session, _kompas_url('ekonomi', None, None, 1), PORTALS['kompas'])
out = asyncio.run(check())
assert out and out.status == 200 and out.css('.articleList .articleItem')
print('Kompas search results OK')
""",
}
failed = False
for name, code in checks.items():
    # A scraper folder is the working directory; the shared transport and the news package are on the path.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root / "scrapers"), str(root / "scrapers" / "_shared")])}
    result = subprocess.run([sys.executable, "-c", code], cwd=root / "scrapers" / name, env=env, timeout=150)
    failed |= result.returncode != 0
sys.exit(int(failed))
