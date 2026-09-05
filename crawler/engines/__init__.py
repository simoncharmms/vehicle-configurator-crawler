"""Crawl engines: Playwright (JS-heavy) and BeautifulSoup (static)."""

from crawler.engines.base_engine import BaseEngine
from crawler.engines.playwright_engine import PlaywrightEngine
from crawler.engines.beautifulsoup_engine import BeautifulSoupEngine

__all__ = ["BaseEngine", "PlaywrightEngine", "BeautifulSoupEngine"]
