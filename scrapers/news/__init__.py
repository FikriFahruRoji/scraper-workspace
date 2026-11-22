"""Scraper berita multi-portal (Kompas, Detik, CNN, CNBC, Republika, Suara,
Tribunnews, Tempo, IDX Channel, JPNN) dengan Scrapling."""

from .core import Article
from .portals import PORTALS, PORTAL_LABELS
from .engine import run_scrape

__all__ = ["Article", "PORTALS", "PORTAL_LABELS", "run_scrape"]
