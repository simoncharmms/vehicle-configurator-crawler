"""BYD configurator crawler.

robots.txt:  No specific block on model pages.
Strategy:    Playwright fetch → DOM extraction of model cards with prices.
             BYD DE homepage renders model cards via JavaScript with
             "Ab XX,XXX €" pricing per model. Static HTML only has
             LD+JSON catalog (model names, no prices). Playwright is
             required to get the rendered price data.
"""

from __future__ import annotations

import logging
import re
import time

from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry
from crawler.network import BrowserPool, retry_with_backoff

logger = logging.getLogger(__name__)

HOMEPAGE_URL = "https://www.byd.com/de"


@BrandRegistry.register
class BYDCrawler(BrandCrawler):
    brand = "BYD"
    base_url = "https://www.byd.com"
    configurator_url = HOMEPAGE_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.PLAYWRIGHT,
            rate_limit_seconds=3.0,
            confidence=0.85,
            notes="Playwright fetch + DOM model card extraction (prices JS-rendered).",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            html = await self._fetch_with_playwright()
            soup = BeautifulSoup(html, "lxml")
            vehicles = self._extract_from_rendered(soup)

            if vehicles:
                logger.info(f"BYD: extracted {len(vehicles)} vehicles from rendered page")
            else:
                errors.append("No vehicles found on BYD homepage")
        except Exception as e:
            logger.error(f"BYD crawl error: {e}")
            errors.append(str(e))

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    async def _fetch_with_playwright(self) -> str:
        """Fetch BYD homepage with Playwright (JS rendering required)."""
        async def _pw_fetch() -> str:
            pool = await BrowserPool.acquire()
            return await pool.fetch_html(
                HOMEPAGE_URL,
                timeout_ms=30_000,
                wait_selector=".price",
            )

        logger.info(f"BYD: fetching {HOMEPAGE_URL} (Playwright)")
        return await retry_with_backoff(
            _pw_fetch, max_retries=3, base_delay=2.0, multiplier=2.0,
        )

    @staticmethod
    def _parse_byd_price(text: str) -> float | None:
        """Parse BYD price format: '22,990 €' or 'Ab22,990 €'.

        BYD uses comma as thousands separator (non-standard German):
            22,990 = 22990
            75,000 = 75000
        """
        if not text:
            return None
        cleaned = re.sub(r'[€\s\u00a0Ab]', '', text.strip(), flags=re.I)
        # Handle comma-as-thousands: X,YYY where YYY is exactly 3 digits
        match = re.match(r'^(\d{1,3}),(\d{3})$', cleaned)
        if match:
            return float(f"{match.group(1)}{match.group(2)}")
        # Handle dot-as-thousands: X.YYY
        match = re.match(r'^(\d{1,3})\.(\d{3})$', cleaned)
        if match:
            return float(f"{match.group(1)}{match.group(2)}")
        return BaseEngine.parse_price(cleaned)

    def _extract_from_rendered(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract vehicles from Playwright-rendered homepage.

        The BYD DE homepage renders model cards with patterns like:
            "BYD DOLPHIN SURF Ab 22,990 €"
            "BYD SEAL Ab 47,990 €"
        
        We extract model names + prices from the page text by matching
        the "BYD <MODEL> ... Ab <price> €" pattern.
        """
        vehicles: list[VehicleData] = []
        seen: set[str] = set()

        text = soup.get_text(separator="\n")

        # Strategy 1: Find price elements with class 'price' near model names
        price_els = soup.find_all(class_=re.compile(r"price", re.I))
        for price_el in price_els:
            price_text = price_el.get_text(strip=True)
            # BYD uses comma as thousands separator: "22,990 €" = 22990
            price = self._parse_byd_price(price_text)
            if not price or price < 10000:
                continue

            # Walk up to find model name (excluding the price text itself)
            parent = price_el.parent
            model_name = ""
            for _ in range(5):
                if parent is None:
                    break
                # Get text of siblings before the price element
                model_parts = []
                for child in parent.children:
                    if child is price_el or child == price_el:
                        break
                    t = child.get_text(strip=True) if hasattr(child, 'get_text') else str(child).strip()
                    if t:
                        model_parts.append(t)
                parent_text = " ".join(model_parts) if model_parts else parent.get_text(separator=" ", strip=True)
                byd_match = re.search(
                    r"BYD\s+((?:DOLPHIN|SEALION|SEAL|ATTO|TANG|HAN|SONG|YUAN)"
                    r"(?:\s+(?:SURF|EVO|TOURING|G|U|\d+))*(?:\s+DM-i)?(?:\s+(?:SURF|EVO|TOURING|G|U|\d+))*)",
                    parent_text, re.I,
                )
                if byd_match:
                    model_name = byd_match.group(1).strip()
                    break
                parent = parent.parent

            if model_name and model_name not in seen:
                seen.add(model_name)
                # Determine fuel type from name
                fuel_type = "electric"
                if "DM-i" in model_name or "DM" in model_name:
                    fuel_type = "hybrid"

                vehicles.append(VehicleData(
                    brand=self.brand,
                    model=f"BYD {model_name}",
                    base_price=price,
                    currency="EUR",
                    fuel_type=fuel_type,
                    url=HOMEPAGE_URL,
                ))

        # Strategy 2: Regex fallback on full page text
        if not vehicles:
            pattern = re.compile(
                r"BYD\s+((?:DOLPHIN|SEALION|SEAL|ATTO|TANG|HAN|SONG|YUAN)"
                r"(?:\s+(?:SURF|EVO|TOURING|G|U|\d+))*(?:\s+DM-i)?(?:\s+(?:SURF|EVO|TOURING|G|U|\d+))*)"
                r"[\s\S]{0,50}?[Aa]b\s*([\d.,]+)\s*€",
            )
            for match in pattern.finditer(text):
                model_name = match.group(1).strip()
                price_str = match.group(2)
                price = self._parse_byd_price(price_str)

                if not price or price < 10000:
                    continue
                if model_name in seen:
                    continue
                seen.add(model_name)

                fuel_type = "electric"
                if "DM-i" in model_name or "DM" in model_name:
                    fuel_type = "hybrid"

                vehicles.append(VehicleData(
                    brand=self.brand,
                    model=f"BYD {model_name}",
                    base_price=price,
                    currency="EUR",
                    fuel_type=fuel_type,
                    url=HOMEPAGE_URL,
                ))

        return vehicles
