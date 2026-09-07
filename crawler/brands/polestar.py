"""Polestar configurator crawler.

robots.txt:  No specific block on model pages.
Strategy:    Curl fetch → individual model page scraping.
             Polestar DE has model pages at /de/{model}/ with
             pricing in the page text ("ab XX.XXX €" or direct price).
             Models: Polestar 2, 3, 4 (Coupé + SUV), 5.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry
from crawler.network import BrowserPool, retry_with_backoff, fetch_html_curl

logger = logging.getLogger(__name__)

HOMEPAGE_URL = "https://www.polestar.com/de/"

# Known model pages
MODEL_PAGES = {
    "Polestar 2": "/de/polestar-2/",
    "Polestar 3": "/de/polestar-3/",
    "Polestar 4 Coupé": "/de/polestar-4/",
    "Polestar 4 SUV": "/de/polestar-4-suv/",
    "Polestar 5": "/de/polestar-5/",
}


@BrandRegistry.register
class PolestarCrawler(BrandCrawler):
    brand = "Polestar"
    base_url = "https://www.polestar.com"
    configurator_url = HOMEPAGE_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.PLAYWRIGHT,
            rate_limit_seconds=3.0,
            confidence=0.85,
            notes="Playwright fetch + model page text extraction (curl blocked by CDN).",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            # Discover models from homepage + scrape each
            model_pages = await self._discover_models()
            logger.info(f"Polestar: found {len(model_pages)} models")

            for model_name, path in model_pages.items():
                url = path if path.startswith("http") else f"{self.base_url}{path}"
                try:
                    vehicle = await self._scrape_model_page_async(model_name, url)
                    if vehicle:
                        vehicles.append(vehicle)
                    await asyncio.sleep(cfg.rate_limit_seconds)
                except Exception as e:
                    logger.warning(f"Polestar: failed to scrape {model_name}: {e}")
                    errors.append(f"{model_name}: {e}")

            if vehicles:
                logger.info(f"Polestar: extracted {len(vehicles)} vehicles")
            else:
                errors.append("No vehicles found on Polestar model pages")

        except Exception as e:
            logger.error(f"Polestar crawl error: {e}")
            errors.append(str(e))

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    async def _fetch_html(self, url: str) -> str:
        """Fetch a URL using Playwright (curl fails on Polestar's CDN)."""
        async def _pw_fetch() -> str:
            pool = await BrowserPool.acquire()
            return await pool.fetch_html(url, timeout_ms=30_000)

        return await retry_with_backoff(
            _pw_fetch, max_retries=2, base_delay=2.0, multiplier=2.0,
        )

    async def _discover_models(self) -> dict[str, str]:
        """Discover model pages from homepage navigation.

        The homepage has links like:
            /de/polestar-2/
            /de/polestar-3/
            /de/polestar-4/
            /de/polestar-5/
        """
        try:
            html = await self._fetch_html(HOMEPAGE_URL)
            soup = BeautifulSoup(html, "lxml")

            models: dict[str, str] = {}
            for a in soup.find_all("a", href=re.compile(r"/de/polestar-\d")):
                href = a.get("href", "")
                text = a.get_text(strip=True)

                # Skip preconfigured, pre-owned links
                if "preconfigured" in href or "pre-owned" in href:
                    continue

                # Match: /de/polestar-N/ or /de/polestar-4-models/polestar-4-X/
                slug_match = re.search(r"/(polestar-\d[^/]*)/$", href)
                if not slug_match:
                    continue

                slug = slug_match.group(1)
                # Skip discontinued models and the 'models' overview
                if slug == "polestar-1" or slug.endswith("-models"):
                    continue
                # Build display name
                name = slug.replace("-", " ").title()
                # Fix: "Polestar 4 Suv" → "Polestar 4 SUV"
                name = name.replace(" Suv", " SUV")

                # Keep absolute URLs as-is; make relative paths absolute
                if href.startswith("http"):
                    path = href
                elif href.startswith("/"):
                    path = href
                else:
                    path = f"/{href}"
                if name not in models:
                    models[name] = path

            if models:
                return models
        except Exception as e:
            logger.warning(f"Polestar: homepage discovery failed: {e}")

        return MODEL_PAGES.copy()

    async def _scrape_model_page_async(self, model_name: str, url: str) -> VehicleData | None:
        """Scrape a single Polestar model page for pricing.

        Prices appear as:
            "ab 57.690 €"
            "78.900 €"
            "Ab 63.200 €"
        in the page text (Playwright-rendered).
        """
        try:
            html = await self._fetch_html(url)
        except Exception as e:
            logger.warning(f"Polestar: failed to fetch {url}: {e}")
            return None

        if len(html) < 1000:
            logger.warning(f"Polestar: minimal response for {url}")
            return None

        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text()

        # Extract price
        price: float | None = None

        # "ab XX.XXX €"
        ab_match = re.search(r"[Aa]b\s+([\d.]+(?:,\d+)?)\s*€", text)
        if ab_match:
            candidate = BaseEngine.parse_price(ab_match.group(1))
            if candidate and candidate >= 30000:
                price = candidate

        # Direct price: "XX.XXX €" (first significant one)
        if not price:
            for m in re.finditer(r"([\d.]+(?:,\d+)?)\s*€", text[:15000]):
                candidate = BaseEngine.parse_price(m.group(1))
                if candidate and candidate >= 30000:
                    price = candidate
                    break

        # JSON-LD check
        if not price:
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or "")
                    if isinstance(data, dict):
                        offers = data.get("offers", {})
                        if isinstance(offers, dict) and "price" in offers:
                            price = BaseEngine.parse_price(str(offers["price"]))
                except (json.JSONDecodeError, TypeError):
                    continue

        if not price:
            logger.info(f"Polestar {model_name}: no price found on {url}")

        return VehicleData(
            brand=self.brand,
            model=model_name,
            base_price=price,
            currency="EUR",
            fuel_type="electric",
            url=url,
        )
