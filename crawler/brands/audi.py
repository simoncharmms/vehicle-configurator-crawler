"""Audi configurator crawler.

robots.txt:  Only /userinfo/ disallowed — configurator paths are allowed.
Strategy:    Static HTML extraction from Apollo GraphQL cache.
             Option extraction via model page API capture + HTML parsing.
Resilience:  Uses ``retry_with_backoff`` (2 attempts, exponential delay).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import requests
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
    _find_equipment_items,
    _guess_category_from_title,
)
from crawler.network import retry_with_backoff, BrowserPool

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.audi.de/de/brand/de/neuwagen.html"

# Max models to probe for options
MAX_OPTION_PROBES = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

VEHICLE_TYPE_MAP = {
    "ICEV": "petrol",
    "BEV": "electric",
    "PHEV": "hybrid",
}

# Audi model slug mapping for model pages / configurator
_AUDI_SLUGS: dict[str, str] = {
    "audia1": "a1", "audia3": "a3", "audia4": "a4", "audia5": "a5",
    "audia6": "a6", "audia6e-tron": "a6-e-tron", "audia7": "a7",
    "audia8": "a8",
    "audiq2": "q2", "audiq3": "q3", "audiq4e-tron": "q4-e-tron",
    "audiq5": "q5", "audiq6e-tron": "q6-e-tron",
    "audiq8e-tron": "q8-e-tron", "audiq8": "q8",
    "auditt": "tt", "audir8": "r8", "audie-trongt": "e-tron-gt",
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
            notes="Static HTML from Apollo GraphQL cache. "
                  "Option extraction from model pages.",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        try:
            return await retry_with_backoff(
                self._crawl_inner, config, max_retries=1, base_delay=1.0,
            )
        except Exception as e:
            logger.warning(f"Audi: all retry attempts exhausted: {e}")
            return CrawlResult(
                brand=self.brand,
                errors=[f"All attempts failed: {e}"],
            )

    async def _crawl_inner(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        pool = await BrowserPool.acquire()

        try:
            # --- Fetch model listing (with 403 resilience) ---
            html = await self._fetch_with_403_recovery(
                pool, MODELS_URL, cfg,
            )
            soup = BeautifulSoup(html, "lxml")

            # Strategy 1: Apollo GraphQL cache
            vehicles = self._extract_from_graphql_cache(soup)
            if vehicles:
                logger.info(f"Audi: extracted {len(vehicles)} vehicles from GraphQL cache")
            else:
                # Strategy 2: page links fallback
                vehicles = self._extract_from_links(soup)
                if vehicles:
                    logger.info(f"Audi: extracted {len(vehicles)} vehicles from page links")

            if not vehicles:
                errors.append("No vehicles found in Audi page data")

            # Enrich with prices from individual model pages
            if vehicles:
                vehicles = await self._enrich_prices(vehicles, cfg)

            # --- Option extraction phase ---
            if vehicles:
                try:
                    await self._enrich_options(vehicles, pool, cfg)
                except Exception as e:
                    logger.warning(f"Audi: option extraction failed: {e}")
                    errors.append(f"Option extraction partial/failed: {e}")

        except RuntimeError as e:
            err_str = str(e)
            if "403" in err_str:
                logger.warning(
                    f"Audi: HTTP 403 after all retries — "
                    f"site is blocking automated access"
                )
                errors.append(
                    f"HTTP 403 — Audi is blocking automated access. "
                    f"Vehicle data unavailable this run."
                )
            else:
                logger.error(f"Audi crawl error: {e}")
                errors.append(str(e))
        except Exception as e:
            logger.error(f"Audi crawl error: {e}")
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

    async def _fetch_with_403_recovery(
        self,
        pool: BrowserPool,
        url: str,
        config: CrawlConfig,
    ) -> str:
        """Fetch *url* with user-agent rotation on HTTP 403.

        Tries up to 3 different user agents with increasing delays
        before giving up.  This mitigates Audi's bot-detection which
        blocks based on request fingerprinting.
        """
        from crawler.network import USER_AGENTS, get_random_user_agent
        import random

        # Shuffle UAs so each run looks different
        uas = list(USER_AGENTS)
        random.shuffle(uas)

        last_err: Exception | None = None
        for attempt, ua in enumerate(uas[:3]):
            try:
                if attempt > 0:
                    delay = config.rate_limit_seconds * (attempt + 1) + random.uniform(1, 3)
                    logger.info(
                        f"Audi: retrying with different User-Agent "
                        f"(attempt {attempt + 1}, delay {delay:.1f}s)"
                    )
                    await asyncio.sleep(delay)

                logger.info(f"Audi: fetching {url}")
                html = await pool.fetch_html(
                    url, user_agent=ua, timeout_ms=30_000,
                )
                return html

            except RuntimeError as e:
                last_err = e
                if "403" not in str(e):
                    raise  # Non-403 errors propagate immediately
                logger.warning(
                    f"Audi: HTTP 403 (attempt {attempt + 1}/3)"
                )

        raise last_err  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Model extraction
    # ------------------------------------------------------------------

    def _extract_from_graphql_cache(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract vehicles from the embedded Apollo/GraphQL cache."""
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
                    if is_fake or not name:
                        continue

                    fuel_type = VEHICLE_TYPE_MAP.get(vehicle_type, "")
                    konfig_url = (
                        f"https://www.audi.de/de/brand/de/neuwagen/konfigurator.html"
                        f"#{cl_id}"
                    )

                    vehicle = VehicleData(
                        brand=self.brand,
                        model=name,
                        variant=group_name,
                        base_price=None,
                        currency="EUR",
                        fuel_type=fuel_type,
                        url=konfig_url,
                    )
                    vehicles.append(vehicle)

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

    # ------------------------------------------------------------------
    # Price enrichment (existing)
    # ------------------------------------------------------------------

    async def _enrich_prices(
        self, vehicles: list[VehicleData], config: CrawlConfig
    ) -> list[VehicleData]:
        """Fetch prices from individual model overview pages."""
        model_families: dict[str, list[VehicleData]] = {}
        for v in vehicles:
            family = v.variant.lower().replace("audi ", "").replace(" ", "")
            if family not in model_families:
                model_families[family] = []
            model_families[family].append(v)

        session = requests.Session()
        session.headers.update(HEADERS)
        fetched_count = 0
        max_fetches = 10

        for family, family_vehicles in model_families.items():
            if fetched_count >= max_fetches:
                break

            slug = _AUDI_SLUGS.get(family, family)
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
                    for v in family_vehicles:
                        price = prices.get(v.model)
                        if price is None:
                            short_name = v.model.replace("Audi ", "")
                            price = prices.get(short_name)
                        if price is not None:
                            v.base_price = price

            except requests.RequestException as e:
                logger.debug(f"Audi price fetch failed for {family}: {e}")

        session.close()
        return vehicles

    def _extract_prices_from_page(self, html: str) -> dict[str, float]:
        prices: dict[str, float] = {}
        soup = BeautifulSoup(html, "lxml")

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

        price_pattern = re.compile(
            r'ab\s+([\d.]+(?:,\d{2})?)\s*€', re.IGNORECASE
        )
        for match in price_pattern.finditer(html):
            price = BaseEngine.parse_price(match.group(1))
            if price and price > 15000:
                context_start = max(0, match.start() - 200)
                context = html[context_start:match.start()]
                name_match = re.search(
                    r'(?:Audi\s+)?([A-Z][A-Za-z0-9\s-]{2,30}?)(?:\s*(?:<|"|$))',
                    context[-100:],
                )
                if name_match:
                    name = name_match.group(1).strip()
                    if name and name not in prices:
                        prices[name] = price
        return prices

    # ------------------------------------------------------------------
    # Option extraction (new)
    # ------------------------------------------------------------------

    async def _enrich_options(
        self,
        vehicles: list[VehicleData],
        pool: BrowserPool,
        config: CrawlConfig,
    ) -> None:
        """Probe model pages / configurator for option data.

        Audi model pages frequently return HTTP 403 even through
        Playwright.  The extraction pipeline:

        1. Try the model overview page (``/neuwagen/{slug}.html``)
        2. On 403 / failure, try the overview page already loaded
        3. Search captured API (GraphQL), embedded scripts, and HTML text
        4. On persistent 403, log a warning and skip gracefully
        """
        targets = [v for v in vehicles if v.url][:MAX_OPTION_PROBES]
        if not targets:
            return

        blocked_count = 0

        for vehicle in targets:
            try:
                await asyncio.sleep(config.rate_limit_seconds + 1.0)  # extra delay for Audi

                # Build a meaningful page URL from the model variant
                family = vehicle.variant.lower().replace("audi ", "").replace(" ", "")
                slug = _AUDI_SLUGS.get(family, family)
                probe_url = f"https://www.audi.de/de/brand/de/neuwagen/{slug}.html"

                logger.info(f"Audi options: probing {vehicle.model} → {probe_url}")

                options: list[OptionData] = []

                try:
                    html, api_responses = await pool.fetch_with_api_capture(
                        probe_url,
                        extra_wait_ms=5000,
                        timeout_ms=25_000,
                    )

                    # 1) API response capture (GraphQL cache, JSON-LD, etc.)
                    for resp in api_responses:
                        found = _search_json_for_options(resp.get("data"), self.brand)
                        options.extend(found)

                    # 2) Embedded script data
                    if not options:
                        soup = BeautifulSoup(html, "lxml")
                        options = _extract_options_from_scripts(soup, self.brand)

                        # 2b) Search for Audi equipment items similar to
                        #     Mercedes ssrData (structured JSON in scripts)
                        if not options:
                            options = _extract_audi_equipment_from_page(
                                soup, self.brand,
                            )

                    # 3) Text regex fallback
                    if not options:
                        options = _extract_options_from_text(html, self.brand)

                except RuntimeError as e:
                    err_str = str(e)
                    if "403" in err_str or "Forbidden" in err_str:
                        blocked_count += 1
                        logger.warning(
                            f"Audi options: HTTP 403 on {probe_url} "
                            f"(blocked {blocked_count}x) — skipping"
                        )
                        if blocked_count >= 2:
                            logger.warning(
                                "Audi options: persistent 403 blocking — "
                                "aborting option probing for remaining models"
                            )
                            return
                        continue
                    else:
                        raise

                if options:
                    vehicle.available_options = _dedupe_options(options)
                    logger.info(
                        f"Audi options: {vehicle.model} → "
                        f"{len(vehicle.available_options)} options"
                    )

            except Exception as e:
                logger.debug(f"Audi options: {vehicle.model} failed: {e}")


def _extract_audi_equipment_from_page(
    soup: BeautifulSoup, brand: str,
) -> list[OptionData]:
    """Search Audi model page for equipment data in embedded scripts.

    Audi pages embed Apollo/GraphQL caches and other JSON structures
    that may contain equipment or feature lists.
    """
    options: list[OptionData] = []

    for script in soup.find_all("script"):
        if not script.string:
            continue
        text = script.string.strip()

        # Look for Apollo cache or LD+JSON with equipment data
        if text.startswith('{"ROOT_QUERY"'):
            try:
                data = json.loads(text)
                # Search entire cache for option-like entries
                found = _search_json_for_options(data, brand)
                options.extend(found)
            except json.JSONDecodeError:
                pass

    # Also check JSON-LD structured data for feature lists
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                # Audi LD+JSON may have "additionalProperty" with equipment
                for prop in data.get("additionalProperty", []):
                    if isinstance(prop, dict):
                        name = prop.get("name", "")
                        value = prop.get("value", "")
                        std = normalize_option_name(name, brand)
                        if std:
                            price = None
                            if isinstance(value, str):
                                from crawler.engines.base_engine import BaseEngine
                                price = BaseEngine.parse_price(value)
                            options.append(OptionData(
                                standardized_name=std,
                                brand_specific_name=name,
                                price=price,
                                category=get_category(std),
                            ))
        except (json.JSONDecodeError, TypeError):
            continue

    return options
