"""Mercedes-Benz configurator crawler.

robots.txt:  Allow: /passengercars/content-pool/tool-pages/car-configurator.html*
Strategy:    Static HTML extraction from SSR data (no Playwright needed).
             Model overview pages contain embedded JSON with model names, prices,
             images, and configurator links.
Resilience:  Uses ``fetch_html_curl`` (rotating user-agents, built-in curl retries)
             wrapped with ``retry_with_backoff`` (3 retries, exponential delay).
"""

from __future__ import annotations

import json
import logging
import re
import time

from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry
from crawler.network import fetch_html_curl, retry_with_backoff

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.mercedes-benz.de/passengercars/models.html"


# Vehicle type mapping for Mercedes tags
FUEL_MAP = {
    "Elektrisch": "electric",
    "Electric": "electric",
    "Hybrid": "hybrid",
    "Plug-in": "hybrid",
    "AMG": "",
    "Neu": "",
    "New": "",
}


@BrandRegistry.register
class MercedesCrawler(BrandCrawler):
    brand = "Mercedes-Benz"
    base_url = "https://www.mercedes-benz.de"
    configurator_url = MODELS_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=2.0,
            confidence=0.9,
            notes="Static HTML extraction from SSR navigation data. No JS rendering needed.",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        """Crawl Mercedes models with automatic retry on transient failures.

        Uses retry_with_backoff (max 2 attempts) for retriable errors.
        Timeouts are handled gracefully — returns an error result rather
        than blocking the entire orchestrator run.
        """
        try:
            return await retry_with_backoff(
                self._crawl_inner, config, max_retries=1, base_delay=1.0,
            )
        except Exception as e:
            logger.warning(f"Mercedes: all retry attempts exhausted: {e}")
            return CrawlResult(
                brand=self.brand,
                errors=[f"All attempts failed: {e}"],
            )

    async def _crawl_inner(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        logger.info(f"Mercedes: fetching {MODELS_URL}")
        html = fetch_html_curl(MODELS_URL)
        soup = BeautifulSoup(html, "lxml")
        vehicles = self._extract_from_ssr(soup)

        if vehicles:
            logger.info(f"Mercedes: extracted {len(vehicles)} vehicles from SSR data")
        else:
            errors.append("No vehicles found in SSR navigation data")

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    def _extract_from_ssr(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract vehicles from SSR (server-side rendered) navigation data.

        Mercedes embeds a full navigation structure in script tags as:
            (window.ssrData ??= {})["<hash>"] = { payload: { mainNavigation: { items: [...] } } }

        The first navigation item ("Modelle") contains all model categories,
        each with individual model entries that include:
            - link.label: model name
            - link.url: overview page URL
            - link.vehicle.price: formatted price (e.g. "ab 42.982,32 €")
            - link.vehicle.images: list of image URLs
            - link.tags: list of tags (e.g. "Neu", "Elektrisch")
        """
        vehicles: list[VehicleData] = []
        seen: set[str] = set()

        for script in soup.find_all("script"):
            if not script.string or "ssrData" not in script.string:
                continue

            match = re.search(
                r'\["([a-f0-9]+)"\]\s*=\s*({.*})\s*;?\s*$',
                script.string.strip(),
                re.DOTALL,
            )
            if not match:
                continue

            try:
                data = json.loads(match.group(2))
            except json.JSONDecodeError:
                continue

            nav_items = (
                data.get("payload", {})
                .get("mainNavigation", {})
                .get("items", [])
            )
            if not nav_items:
                continue

            # First nav item = "Modelle"
            modelle = nav_items[0]
            for category in modelle.get("items", []):
                cat_label = category.get("label", "")
                if not cat_label:
                    continue

                for item in category.get("items", []):
                    link = item.get("link", {})
                    if not isinstance(link, dict):
                        continue

                    label = link.get("label", "").strip()
                    if not label or label.startswith("Alle "):
                        continue

                    vehicle_data = link.get("vehicle", {})
                    if not isinstance(vehicle_data, dict):
                        continue

                    price_text = vehicle_data.get("price", "")
                    url = link.get("url", "")
                    images = vehicle_data.get("images", [])
                    tags = link.get("tags", [])
                    tag_labels = [
                        t.get("label", "") for t in tags if isinstance(t, dict)
                    ]

                    # Determine fuel type from tags or name
                    fuel_type = ""
                    for tag in tag_labels:
                        for key, ft in FUEL_MAP.items():
                            if key.lower() in tag.lower() and ft:
                                fuel_type = ft
                                break

                    if not fuel_type:
                        if "EQ" in label or "Elektr" in label.lower():
                            fuel_type = "electric"
                        elif "Hybrid" in label.lower() or "PHEV" in label.lower():
                            fuel_type = "hybrid"

                    # Deduplicate: same model name can appear with different URLs
                    # (e.g. electric vs combustion variant)
                    dedup_key = f"{label}|{price_text}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

                    # Build variant from category
                    variant = cat_label if cat_label not in label else ""

                    vehicle = VehicleData(
                        brand=self.brand,
                        model=label,
                        variant=variant,
                        base_price=BaseEngine.parse_price(
                            price_text.replace("\xa0", " ") if price_text else ""
                        ),
                        currency="EUR",
                        fuel_type=fuel_type,
                        url=url if url.startswith("http") else f"{self.base_url}{url}",
                        image_url=images[0] if images else "",
                    )
                    vehicles.append(vehicle)

            # Only need one SSR block with navigation data
            if vehicles:
                break

        return vehicles
