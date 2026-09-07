"""Zeekr configurator crawler.

robots.txt:  No specific block on model pages.
Strategy:    Curl fetch → homepage + model page scraping.
             Zeekr EU (redirected from zeekr.de) has model cards on the
             homepage with "Ab XX 990 EUR" pricing, and individual model
             pages at /de-de/models/{id} with price details.
Note:        Zeekr uses a Nuxt-based site. Homepage prices are in the
             server-rendered HTML; model pages have prices in text.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry
from crawler.network import fetch_html_curl

logger = logging.getLogger(__name__)

HOMEPAGE_URL = "https://www.zeekr.eu/de-de/"

# Model IDs for individual pages
MODEL_IDS = ["9x", "7x", "7gt", "x", "001"]


@BrandRegistry.register
class ZeekrCrawler(BrandCrawler):
    brand = "Zeekr"
    base_url = "https://www.zeekr.eu"
    configurator_url = HOMEPAGE_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.85,
            notes="Curl fetch + homepage/model page text extraction (Ab XX EUR).",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            # Strategy 1: Extract from homepage (has all models with prices)
            vehicles = self._extract_from_homepage()

            if not vehicles:
                # Strategy 2: Scrape individual model pages
                logger.info("Zeekr: homepage extraction failed, trying model pages")
                vehicles = await self._scrape_model_pages(cfg.rate_limit_seconds)

            if vehicles:
                logger.info(f"Zeekr: extracted {len(vehicles)} vehicles")
            else:
                errors.append("No vehicles found on Zeekr pages")

        except Exception as e:
            logger.error(f"Zeekr crawl error: {e}")
            errors.append(str(e))

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    def _extract_from_homepage(self) -> list[VehicleData]:
        """Extract models + prices from homepage text.

        The homepage has patterns like:
            "Zeekr 001\nDer luxuriöse Shooting Brake\nAb 59 990 EUR"
            "Zeekr X\nDer luxuriöse Stadt-SUV\nAb 37 990 EUR"
        """
        try:
            html = fetch_html_curl(HOMEPAGE_URL, timeout=30)
        except Exception as e:
            logger.warning(f"Zeekr: homepage fetch failed: {e}")
            return []

        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text()
        vehicles: list[VehicleData] = []
        seen: set[str] = set()

        # Valid model names: short alphanumeric (001, X, 7X, 7GT, 9X, 5)
        valid_model_re = re.compile(r"^[A-Za-z0-9]{1,4}$")

        # Pattern: "Zeekr <model>\n<short desc>\nAb <price> EUR"
        # Limit match distance to 200 chars to avoid cross-section matches
        pattern = re.compile(
            r"Zeekr\s+(\w+)\s*\n"     # Model name (single word)
            r"(.{5,200}?)"             # Short description
            r"[Aa]b\s+([\d\s.]+)\s*EUR",  # Price
            re.DOTALL,
        )

        for match in pattern.finditer(text):
            model_name = match.group(1).strip()
            price_str = match.group(3).strip().replace(" ", "")

            # Filter non-model names (Deutschland, Electric, etc.)
            if not valid_model_re.match(model_name):
                continue

            price = BaseEngine.parse_price(price_str)
            if not price or price < 15000:
                continue

            # Normalize model name
            model_name = model_name.upper() if len(model_name) <= 3 else model_name
            if model_name in seen:
                continue
            seen.add(model_name)

            # Build URL
            model_slug = model_name.lower().replace(" ", "")
            url = f"{self.base_url}/de-de/models/{model_slug}"

            vehicles.append(VehicleData(
                brand=self.brand,
                model=f"Zeekr {model_name}",
                base_price=price,
                currency="EUR",
                fuel_type="electric",
                url=url,
            ))

        # Also look for "Zeekr 001" specifically (3-digit model)
        pattern_001 = re.compile(
            r"Zeekr\s+(001)\s*\n"
            r"(.{5,200}?)"
            r"[Aa]b\s+([\d\s.]+)\s*EUR",
            re.DOTALL,
        )
        for match in pattern_001.finditer(text):
            model_name = match.group(1)
            price_str = match.group(3).strip().replace(" ", "")
            price = BaseEngine.parse_price(price_str)
            if price and price >= 15000 and model_name not in seen:
                seen.add(model_name)
                vehicles.append(VehicleData(
                    brand=self.brand,
                    model=f"Zeekr {model_name}",
                    base_price=price,
                    currency="EUR",
                    fuel_type="electric",
                    url=f"{self.base_url}/de-de/models/{model_name.lower()}",
                ))

        return vehicles

    async def _scrape_model_pages(self, rate_limit: float) -> list[VehicleData]:
        """Fallback: scrape individual model pages for prices."""
        vehicles: list[VehicleData] = []

        for model_id in MODEL_IDS:
            url = f"{self.base_url}/de-de/models/{model_id}"
            try:
                html = fetch_html_curl(url, timeout=20)
                soup = BeautifulSoup(html, "lxml")
                text = soup.get_text()

                # Find price: "Ab XX 990 EUR" or "XX.990 EUR"
                price: float | None = None
                ab_match = re.search(r"[Aa]b\s+([\d\s.]+)\s*EUR", text)
                if ab_match:
                    price_str = ab_match.group(1).strip().replace(" ", "")
                    price = BaseEngine.parse_price(price_str)

                if not price:
                    eur_match = re.search(r"([\d.]+)\s*EUR", text)
                    if eur_match:
                        candidate = BaseEngine.parse_price(eur_match.group(1))
                        if candidate and candidate >= 15000:
                            price = candidate

                model_name = model_id.upper() if len(model_id) <= 3 else model_id

                vehicles.append(VehicleData(
                    brand=self.brand,
                    model=f"Zeekr {model_name}",
                    base_price=price,
                    currency="EUR",
                    fuel_type="electric",
                    url=url,
                ))

                await asyncio.sleep(rate_limit)

            except Exception as e:
                logger.warning(f"Zeekr: failed to scrape {model_id}: {e}")

        return vehicles
