"""Polestar configurator crawler."""

from __future__ import annotations

import json
import logging
import time

from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry
from crawler.network import retry_with_backoff, BrowserPool

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.polestar.com/de/models/"


@BrandRegistry.register
class PolestarCrawler(BrandCrawler):
    brand = "Polestar"
    base_url = "https://www.polestar.com/de"
    configurator_url = MODELS_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.PLAYWRIGHT,
            rate_limit_seconds=2.0,
            confidence=0.7,
            notes="Polestar models page with Playwright rendering",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        """Crawl Polestar models."""
        try:
            return await retry_with_backoff(
                self._crawl_inner, config, max_retries=1, base_delay=1.0,
            )
        except Exception as e:
            logger.warning(f"Polestar: all retry attempts exhausted: {e}")
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
            logger.info(f"Polestar: fetching {MODELS_URL}")
            pool = await BrowserPool.acquire()
            html = await pool.fetch_html(MODELS_URL)
            soup = BeautifulSoup(html, "lxml")
            vehicles = self._extract_models(soup)

            if vehicles:
                logger.info(f"Polestar: extracted {len(vehicles)} vehicles")
            else:
                errors.append("No vehicles found on Polestar models page")

        except Exception as e:
            logger.error(f"Polestar crawl error: {e}")
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

    def _extract_models(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract Polestar models from the page."""
        vehicles: list[VehicleData] = []

        # Look for model cards or links
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            text = link.get_text(strip=True)

            # Look for Polestar model names (e.g., Polestar 1, 2, 3, etc.)
            if any(model in text.upper() for model in ["POLESTAR 1", "POLESTAR 2", "POLESTAR 3", "POLESTAR 4", "POLESTAR"]):
                vehicle = VehicleData(
                    brand=self.brand,
                    model=text,
                    variant="",
                    base_price=None,
                    currency="EUR",
                    fuel_type="",
                    url=href if href.startswith("http") else f"{self.base_url}{href}",
                    image_url="",
                )
                vehicles.append(vehicle)

        # Deduplicate by model name
        seen = set()
        unique = []
        for v in vehicles:
            if v.model not in seen:
                seen.add(v.model)
                unique.append(v)

        return unique
