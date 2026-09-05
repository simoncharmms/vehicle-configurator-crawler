"""Audi configurator crawler.

robots.txt:  Only /userinfo/ disallowed — configurator paths are allowed.
Strategy:    Static HTML extraction from Apollo GraphQL cache.
             The Audi model overview page embeds a full GraphQL response with
             all carline (model) data, vehicle types, and IDs.
             Prices are fetched from individual model pages where available.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.audi.de/de/brand/de/neuwagen.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

def _fetch_html(url: str, timeout: int = 30) -> str:
    """Fetch HTML using curl (handles TLS edge cases)."""
    result = subprocess.run(
        [
            "curl", "-sL", "--compressed", "--http1.1",
            "--max-time", str(timeout),
            "--retry", "2", "--retry-delay", "3",
            "-H", f"User-Agent: {HEADERS['User-Agent']}",
            "-H", f"Accept-Language: {HEADERS['Accept-Language']}",
            "-H", f"Accept: {HEADERS['Accept']}",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed (code {result.returncode}): {result.stderr[:200]}")
    if not result.stdout:
        raise RuntimeError("curl returned empty response")
    return result.stdout


VEHICLE_TYPE_MAP = {
    "ICEV": "petrol",
    "BEV": "electric",
    "PHEV": "hybrid",
}


@BrandRegistry.register
class AudiCrawler(BrandCrawler):
    brand = "Audi"
    base_url = "https://www.audi.de"
    configurator_url = MODELS_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=2.0,
            confidence=0.85,
            notes="Static HTML extraction from Apollo GraphQL cache. No JS rendering needed.",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            logger.info(f"Audi: fetching {MODELS_URL}")
            html = _fetch_html(MODELS_URL)
            soup = BeautifulSoup(html, "lxml")

            # Strategy 1: Extract from Apollo GraphQL cache
            vehicles = self._extract_from_graphql_cache(soup)
            if vehicles:
                logger.info(f"Audi: extracted {len(vehicles)} vehicles from GraphQL cache")
            else:
                # Strategy 2: Extract model names from links
                vehicles = self._extract_from_links(soup)
                if vehicles:
                    logger.info(f"Audi: extracted {len(vehicles)} vehicles from page links")

            if not vehicles:
                errors.append("No vehicles found in Audi page data")

            # Try to get prices from individual model pages (rate limited)
            if vehicles:
                vehicles_with_prices = await self._enrich_prices(vehicles, cfg)
                if vehicles_with_prices:
                    vehicles = vehicles_with_prices

        except Exception as e:
            logger.error(f"Audi crawl error: {e}")
            errors.append(str(e))

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    def _extract_from_graphql_cache(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract vehicles from the embedded Apollo/GraphQL cache.

        Audi pages contain a script tag with JSON starting with {"ROOT_QUERY":...}
        that includes a carlineStructure query result with all model groups.
        """
        vehicles: list[VehicleData] = []

        for script in soup.find_all("script"):
            if not script.string:
                continue
            text = script.string.strip()
            if not text.startswith('{"ROOT_QUERY"'):
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue

            root_query = data.get("ROOT_QUERY", {})
            cs_key = next(
                (k for k in root_query if "carlineStructure" in k), None
            )
            if not cs_key:
                continue

            structure = root_query[cs_key]
            groups = structure.get("carlineGroups", [])

            for group in groups:
                group_name = group.get("name", "")
                carlines = group.get("carlines", [])

                for carline in carlines:
                    name = carline.get("name", "")
                    cl_id = carline.get("identifier", {}).get("id", "")
                    vehicle_type = carline.get("vehicleType", "")
                    is_fake = carline.get("isFake", False)

                    # Skip "fake" entries (discontinued / placeholder)
                    if is_fake:
                        continue

                    if not name:
                        continue

                    fuel_type = VEHICLE_TYPE_MAP.get(vehicle_type, "")

                    # Build configurator URL from carline ID
                    konfig_url = (
                        f"https://www.audi.de/de/brand/de/neuwagen/konfigurator.html"
                        f"#{cl_id}"
                    )

                    vehicle = VehicleData(
                        brand=self.brand,
                        model=name,
                        variant=group_name,
                        base_price=None,  # Prices loaded via JS, enriched below
                        currency="EUR",
                        fuel_type=fuel_type,
                        url=konfig_url,
                    )
                    vehicles.append(vehicle)

            # Only need one GraphQL cache block
            if vehicles:
                break

        return vehicles

    def _extract_from_links(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Fallback: extract model names from page links."""
        vehicles: list[VehicleData] = []
        seen: set[str] = set()

        links = soup.find_all("a", href=re.compile(r"/neuwagen/[^/]+"))
        for a in links:
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if text and len(text) < 60 and text not in seen:
                seen.add(text)
                vehicles.append(VehicleData(
                    brand=self.brand,
                    model=text,
                    url=f"{self.base_url}{href}" if not href.startswith("http") else href,
                ))

        return vehicles

    async def _enrich_prices(
        self, vehicles: list[VehicleData], config: CrawlConfig
    ) -> list[VehicleData]:
        """Try to get prices from individual model overview pages.

        Rate limited to respect the server. Only fetches a sample of pages.
        """
        # Group by model family to avoid redundant fetches
        model_families: dict[str, list[VehicleData]] = {}
        for v in vehicles:
            # Extract family from variant (e.g. "Audi A5" → "a5")
            family = v.variant.lower().replace("audi ", "").replace(" ", "")
            if family not in model_families:
                model_families[family] = []
            model_families[family].append(v)

        # Fetch a sample of model overview pages for prices
        session = requests.Session()
        session.headers.update(HEADERS)

        fetched_count = 0
        max_fetches = 10  # Limit to avoid excessive requests

        for family, family_vehicles in model_families.items():
            if fetched_count >= max_fetches:
                break

            # Construct model page URL
            url_map = {
                "audia1": "a1",
                "audia3": "a3",
                "audia5": "a5",
                "audia6": "a6",
                "audia6e-tron": "a6-e-tron",
                "audia8": "a8",
                "audiq3": "q3",
                "audiq4e-tron": "q4-e-tron",
                "audiq5": "q5",
                "audiq6e-tron": "q6-e-tron",
                "audiq8e-tron": "q8-e-tron",
                "audiq8": "q8",
                "auditt": "tt",
                "audir8": "r8",
                "audie-trongt": "e-tron-gt",
            }

            slug = url_map.get(family, family)
            page_url = f"https://www.audi.de/de/brand/de/neuwagen/{slug}.html"

            try:
                time.sleep(config.rate_limit_seconds)
                resp = session.get(page_url, timeout=15)
                if resp.status_code != 200:
                    continue

                fetched_count += 1
                prices = self._extract_prices_from_page(resp.text)

                if prices:
                    logger.info(f"Audi: found {len(prices)} prices for {family}")
                    # Match prices to vehicles in this family
                    for v in family_vehicles:
                        # Try exact name match first, then partial
                        price = prices.get(v.model)
                        if price is None:
                            # Try matching by removing common prefixes
                            short_name = v.model.replace("Audi ", "")
                            price = prices.get(short_name)
                        if price is not None:
                            v.base_price = price

            except requests.RequestException as e:
                logger.debug(f"Audi price fetch failed for {family}: {e}")

        session.close()
        return vehicles

    def _extract_prices_from_page(self, html: str) -> dict[str, float]:
        """Extract model → price mapping from an Audi model page."""
        prices: dict[str, float] = {}

        # Look for price patterns in the HTML
        # Audi pages sometimes have structured data or price annotations
        soup = BeautifulSoup(html, "lxml")

        # Check for JSON-LD product data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict):
                    name = data.get("name", "")
                    offers = data.get("offers", {})
                    if isinstance(offers, dict) and "price" in offers:
                        price = BaseEngine.parse_price(str(offers["price"]))
                        if name and price:
                            prices[name] = price
            except (json.JSONDecodeError, TypeError):
                continue

        # Check for price patterns in text
        price_pattern = re.compile(
            r'ab\s+([\d.]+(?:,\d{2})?)\s*€', re.IGNORECASE
        )
        for match in price_pattern.finditer(html):
            price = BaseEngine.parse_price(match.group(1))
            if price and price > 15000:  # Sanity check for car prices
                # Try to find the associated model name nearby
                context_start = max(0, match.start() - 200)
                context = html[context_start:match.start()]
                # Look for model names in context
                name_match = re.search(
                    r'(?:Audi\s+)?([A-Z][A-Za-z0-9\s-]{2,30}?)(?:\s*(?:<|"|$))',
                    context[-100:],
                )
                if name_match:
                    name = name_match.group(1).strip()
                    if name and name not in prices:
                        prices[name] = price

        return prices
