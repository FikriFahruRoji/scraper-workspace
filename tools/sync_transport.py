"""Keep each standalone project's browser transport identical to shared source."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
source = (root / 'shared/stealth_transport.py').read_bytes()
for name in ('saham-scraper', 'playstore-reviews-scraper', 'youtube-comments-scraper'):
    (root / name / 'stealth_transport.py').write_bytes(source)
