"""XPeng configurator crawler.

robots.txt:  No specific block on model pages.
Strategy:    Curl fetch → individual model page scraping.
             XPeng DE has model pages at /de/model/{id} with
             "UVP ab XX.XXX €" or "ab XX.XXX €" pricing in static HTML.
             Model list discovered from homepage links.
"""

from __future__ import annotations

import logging
import re
import time

from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry
from crawler.network import fetch_html_curl

logger = logging.getLogger(__name__)

HOMEPAGE_URL = "https://www.xpeng.com/de"

# Known model pages with their paths
MODEL_PAGES = {
    "L03": "/de/model/L03",
    "G6": "/de/model/g6",
    "G9": "/de/model/g9",
    "P7+": "/de/model/p7plus",
    "X9": "/de/model/x9",
}


@BrandRegistry.register
class XPengCrawler(BrandCrawler):
    brand = "XPeng"
    base_url = "https://www.xpeng.com"
    configurator_url = HOMEPAGE_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.80,
            notes="Curl fetch + model page scraping (ab prices in static HTML).",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            # Step 1: Discover models from homepage
            model_pages = await self._discover_models()
            logger.info(f"XPeng: discovered {len(model_pages)} models")

            # Step 2: Scrape each model page
            import asyncio
            for model_name, path in model_pages.items():
                url = path if path.startswith("http") else f"{self.base_url}{path}"
                try:
                    vehicle = self._scrape_model_page(model_name, url)
                    if vehicle:
                        vehicles.append(vehicle)
                    # Rate limit
                    await asyncio.sleep(cfg.rate_limit_seconds)
                except Exception as e:
                    logger.warning(f"XPeng: failed to scrape {model_name}: {e}")
                    errors.append(f"{model_name}: {e}")

            if vehicles:
                logger.info(f"XPeng: extracted {len(vehicles)} vehicles")
            else:
                errors.append("No vehicles found on XPeng model pages")

        except Exception as e:
            logger.error(f"XPeng crawl error: {e}")
            errors.append(str(e))

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    async def _discover_models(self) -> dict[str, str]:
        """Discover model pages from homepage links.

        Falls back to hardcoded MODEL_PAGES if homepage scraping fails.
        """
        try:
            html = fetch_html_curl(HOMEPAGE_URL, timeout=20)
            soup = BeautifulSoup(html, "lxml")

            models: dict[str, str] = {}
            seen_urls: set[str] = set()
            for a in soup.find_all("a", href=re.compile(r"/de/model/\w+")):
                href = a.get("href", "")
                text = a.get_text(strip=True)
                if not text or len(text) > 30:
                    continue
                # Skip non-vehicle links
                if any(skip in text.lower() for skip in ["robot", "fleet", "business"]):
                    continue
                # Strip "Neuer " prefix for dedup
                clean_name = re.sub(r"^Neuer?\s+", "", text)
                # Store full URL or path
                if href.startswith("http"):
                    path = href
                elif href.startswith("/"):
                    path = href
                else:
                    path = f"/{href}"
                # Deduplicate by URL
                url_key = re.sub(r"https?://[^/]+", "", path)
                if url_key in seen_urls:
                    continue
                seen_urls.add(url_key)
                if clean_name not in models:
                    models[clean_name] = path

            if models:
                return models
        except Exception as e:
            logger.warning(f"XPeng: homepage discovery failed: {e}")

        return MODEL_PAGES.copy()

    def _scrape_model_page(self, model_name: str, url: str) -> VehicleData | None:
        """Scrape a single XPeng model page for pricing.

        Looks for patterns like:
            "UVP ab 35.600 €"
            "ab 43.600 €"
            "Kaufpreis bei Vertragsschluss: 46.600€"
        """
        try:
            html = fetch_html_curl(url, timeout=20)
        except Exception as e:
            logger.warning(f"XPeng: failed to fetch {url}: {e}")
            return None

        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text()

        # Extract price: prioritize "UVP ab" > "ab" > "Kaufpreis"
        price: float | None = None

        # UVP ab XX.XXX €
        uvp_match = re.search(r"UVP\s*(?:ab)?\s*([\d.]+(?:,\d+)?)\s*€", text)
        if uvp_match:
            price = BaseEngine.parse_price(uvp_match.group(1))

        # ab XX.XXX €
        if not price:
            ab_match = re.search(r"[Aa]b\s+([\d.]+(?:,\d+)?)\s*€", text)
            if ab_match:
                candidate = BaseEngine.parse_price(ab_match.group(1))
                if candidate and candidate >= 15000:
                    price = candidate

        # Kaufpreis bei Vertragsschluss: XX.XXX€
        if not price:
            kauf_match = re.search(
                r"Kaufpreis\s+(?:bei\s+Vertragsschluss)?:?\s*([\d.]+(?:,\d+)?)\s*€",
                text,
            )
            if kauf_match:
                price = BaseEngine.parse_price(kauf_match.group(1))

        if not price:
            logger.info(f"XPeng {model_name}: no price found on {url}")
            # Still return the vehicle without price
            return VehicleData(
                brand=self.brand,
                model=f"XPENG {model_name}",
                base_price=None,
                currency="EUR",
                fuel_type="electric",
                url=url,
            )

        return VehicleData(
            brand=self.brand,
            model=f"XPENG {model_name}",
            base_price=price,
            currency="EUR",
            fuel_type="electric",
            url=url,
        )
