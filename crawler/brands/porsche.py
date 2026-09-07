"""Porsche configurator crawler.

Strategy:    Playwright rendering with ``networkidle`` of
             https://www.porsche.com/germany/models/ which is an SPA
             listing all model families and variants.  Prices are not
             shown on the overview page so ``base_price`` stays ``None``
             unless a model page probe succeeds.
Resilience:  Uses ``retry_with_backoff`` (2 attempts, exponential delay).
"""

from __future__ import annotations

import logging
import re
import time

from bs4 import BeautifulSoup

from crawler.base import (
    BrandCrawler, CrawlConfig, CrawlResult, EngineType,
    VehicleData,
)
from crawler.brands.registry import BrandRegistry
from crawler.network import retry_with_backoff, BrowserPool

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.porsche.com/germany/models/"

# Top-level model family names to detect in headings
MODEL_FAMILIES = {"718", "911", "Taycan", "Panamera", "Macan", "Cayenne"}


@BrandRegistry.register
class PorscheCrawler(BrandCrawler):
    brand = "Porsche"
    base_url = "https://www.porsche.com"
    configurator_url = MODELS_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.PLAYWRIGHT,
            rate_limit_seconds=3.0,
            confidence=0.7,
            notes="Porsche SPA models page (networkidle); no prices on overview.",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        try:
            return await retry_with_backoff(
                self._crawl_inner, config, max_retries=1, base_delay=1.0,
            )
        except Exception as e:
            logger.warning(f"Porsche: all retry attempts exhausted: {e}")
            return CrawlResult(
                brand=self.brand,
                errors=[f"All attempts failed: {e}"],
            )

    async def _crawl_inner(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            logger.info(f"Porsche: fetching {MODELS_URL} (networkidle)")
            pool = await BrowserPool.acquire()
            html = await pool.fetch_html(
                MODELS_URL,
                wait_until="networkidle",
                timeout_ms=45_000,
            )
            soup = BeautifulSoup(html, "lxml")

            vehicles = self._extract_from_page(soup)
            if vehicles:
                logger.info(f"Porsche: extracted {len(vehicles)} vehicles")
            else:
                errors.append("No vehicles found on Porsche models page")

        except Exception as e:
            logger.error(f"Porsche crawl error: {e}")
            errors.append(str(e))
        finally:
            try:
                await BrowserPool.close()
            except Exception:
                pass

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    def _extract_from_page(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract Porsche model variants from the SPA-rendered models page.

        The page renders <h3> headings for every variant under each model
        family.  Prices are not displayed on the overview.
        """
        vehicles: list[VehicleData] = []
        seen: set[str] = set()

        for h3 in soup.find_all("h3"):
            text = h3.get_text(strip=True)
            if not text or len(text) > 80:
                continue
            # Skip "Modellvarianten" family headers
            if "Modellvarianten" in text:
                continue
            # Must start with a known model family name
            if not any(text.startswith(family) for family in MODEL_FAMILIES):
                continue
            if text in seen:
                continue
            seen.add(text)

            # Determine fuel type from variant name
            fuel_type = ""
            text_lower = text.lower()
            if "electric" in text_lower:
                fuel_type = "electric"
            elif "e-hybrid" in text_lower:
                fuel_type = "hybrid"
            elif "taycan" in text_lower:
                fuel_type = "electric"

            # Determine model family
            family = ""
            for f in MODEL_FAMILIES:
                if text.startswith(f):
                    family = f
                    break

            vehicles.append(VehicleData(
                brand=self.brand,
                model=text,
                variant=family,
                base_price=None,  # Porsche doesn't show prices on overview
                currency="EUR",
                fuel_type=fuel_type,
                url=MODELS_URL,
            ))

        return vehicles
