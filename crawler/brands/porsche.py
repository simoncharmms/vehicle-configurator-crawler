"""Porsche configurator crawler.

robots.txt:  Could not verify (timed out during initial check).
Strategy:    Static HTML extraction + price lookup from model pages.
             Option extraction via model page API capture + HTML parsing.
Resilience:  Uses ``retry_with_backoff`` (2 attempts, exponential delay).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from bs4 import BeautifulSoup

from crawler.base import (
    BrandCrawler, CrawlConfig, CrawlResult, EngineType,
    OptionData, VehicleData,
)
from crawler.engines.base_engine import BaseEngine
from crawler.option_mappings import normalize_option_name, get_category
from crawler.brands.registry import BrandRegistry
from crawler.brands.mercedes import (
    _search_json_for_options,
    _extract_options_from_scripts,
    _extract_options_from_text,
    _dedupe_options,
)
from crawler.network import retry_with_backoff, BrowserPool

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.porsche.com/germany/models/"

# Max models to probe for option data
MAX_OPTION_PROBES = 4


@BrandRegistry.register
class PorscheCrawler(BrandCrawler):
    brand = "Porsche"
    base_url = "https://www.porsche.com"
    configurator_url = MODELS_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.6,
            notes="Porsche: static HTML extraction from model pages. "
                  "Option extraction from model/configurator API capture.",
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
            logger.info(f"Porsche: fetching {MODELS_URL}")
            pool = await BrowserPool.acquire()
            html = await pool.fetch_html(MODELS_URL)
            soup = BeautifulSoup(html, "lxml")

            vehicles = self._extract_from_page(soup)
            if vehicles:
                logger.info(f"Porsche: extracted {len(vehicles)} vehicles")
            else:
                errors.append("No vehicles found on Porsche models page")

            # --- Option extraction phase ---
            if vehicles:
                try:
                    await self._enrich_options(vehicles, pool, cfg)
                except Exception as e:
                    logger.warning(f"Porsche: option extraction failed: {e}")
                    errors.append(f"Option extraction partial/failed: {e}")

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

    # ------------------------------------------------------------------
    # Model extraction
    # ------------------------------------------------------------------

    def _extract_from_page(self, soup: BeautifulSoup) -> list[VehicleData]:
        vehicles: list[VehicleData] = []

        # JSON-LD structured data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") in (
                        "Product", "Vehicle", "Car",
                    ):
                        name = item.get("name", "")
                        price = None
                        offers = item.get("offers", {})
                        if isinstance(offers, dict) and "price" in offers:
                            price = BaseEngine.parse_price(str(offers["price"]))
                        if name:
                            vehicles.append(VehicleData(
                                brand=self.brand,
                                model=name,
                                base_price=price,
                                url=MODELS_URL,
                            ))
            except (json.JSONDecodeError, TypeError):
                continue

        # Fallback: model links
        if not vehicles:
            seen: set[str] = set()
            for a in soup.find_all("a", href=re.compile(r"/models?/|/configurator")):
                text = a.get_text(strip=True)
                if text and len(text) < 60 and text not in seen:
                    seen.add(text)
                    href = a.get("href", "")
                    vehicles.append(VehicleData(
                        brand=self.brand,
                        model=text,
                        url=(
                            f"{self.base_url}{href}"
                            if not href.startswith("http")
                            else href
                        ),
                    ))

        # Embedded script data
        if not vehicles:
            for script in soup.find_all("script"):
                if not script.string:
                    continue
                text = script.string
                if "model" in text.lower() and (
                    "price" in text.lower() or "name" in text.lower()
                ):
                    json_matches = re.findall(
                        r'({[^}]*"(?:name|model)"[^}]*})', text,
                    )
                    for jm in json_matches:
                        try:
                            data = json.loads(jm)
                            name = data.get("name", data.get("model", ""))
                            price = data.get("price", data.get("basePrice", None))
                            if isinstance(price, str):
                                price = BaseEngine.parse_price(price)
                            if name:
                                vehicles.append(VehicleData(
                                    brand=self.brand,
                                    model=name,
                                    base_price=float(price) if price else None,
                                    url=MODELS_URL,
                                ))
                        except (json.JSONDecodeError, TypeError):
                            continue

        return vehicles

    # ------------------------------------------------------------------
    # Option extraction (new)
    # ------------------------------------------------------------------

    async def _enrich_options(
        self,
        vehicles: list[VehicleData],
        pool: BrowserPool,
        config: CrawlConfig,
    ) -> None:
        """Probe model overview / configurator pages for option data."""
        targets = [v for v in vehicles if v.url][:MAX_OPTION_PROBES]
        if not targets:
            return

        for vehicle in targets:
            try:
                await asyncio.sleep(config.rate_limit_seconds)

                probe_url = vehicle.url
                logger.info(f"Porsche options: probing {vehicle.model} → {probe_url}")

                html, api_responses = await pool.fetch_with_api_capture(
                    probe_url,
                    extra_wait_ms=5000,
                    timeout_ms=30_000,
                )

                options: list[OptionData] = []

                # 1) API responses
                for resp in api_responses:
                    found = _search_json_for_options(resp.get("data"), self.brand)
                    options.extend(found)

                # 2) Embedded scripts
                if not options:
                    soup = BeautifulSoup(html, "lxml")
                    options = _extract_options_from_scripts(soup, self.brand)

                # 3) Text regex
                if not options:
                    options = _extract_options_from_text(html, self.brand)

                if options:
                    vehicle.available_options = _dedupe_options(options)
                    logger.info(
                        f"Porsche options: {vehicle.model} → "
                        f"{len(vehicle.available_options)} options"
                    )

            except Exception as e:
                logger.debug(f"Porsche options: {vehicle.model} failed: {e}")
