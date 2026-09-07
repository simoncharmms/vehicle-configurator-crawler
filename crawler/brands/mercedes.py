"""Mercedes-Benz configurator crawler.

robots.txt:  Allow: /passengercars/content-pool/tool-pages/car-configurator.html*
Strategy:    Static HTML extraction from SSR data (no Playwright needed for models).
             Option extraction via model page API capture + HTML parsing.
Resilience:  Uses ``retry_with_backoff`` (2 attempts, exponential delay).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from bs4 import BeautifulSoup

from crawler.base import (
    BrandCrawler, CrawlConfig, CrawlResult, EngineType,
    OptionData, VehicleData,
)
from crawler.engines.base_engine import BaseEngine
from crawler.option_mappings import normalize_option_name, get_category
from crawler.brands.registry import BrandRegistry
from crawler.network import retry_with_backoff, BrowserPool

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.mercedes-benz.de/passengercars/models.html"

# Max models to probe for option data (rate-limited)
MAX_OPTION_PROBES = 5

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
            notes="Static HTML extraction from SSR navigation data. "
                  "Option extraction from model page API capture.",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
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

        try:
            logger.info(f"Mercedes: fetching {MODELS_URL}")
            pool = await BrowserPool.acquire()
            html = await pool.fetch_html(MODELS_URL)
            soup = BeautifulSoup(html, "lxml")
            vehicles = self._extract_from_ssr(soup)

            if vehicles:
                logger.info(f"Mercedes: extracted {len(vehicles)} vehicles from SSR data")
                # --- Option extraction phase ---
                try:
                    await self._enrich_options(vehicles, pool, cfg)
                except Exception as e:
                    logger.warning(f"Mercedes: option extraction failed: {e}")
                    errors.append(f"Option extraction partial/failed: {e}")
            else:
                errors.append("No vehicles found in SSR navigation data")

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
    # Model extraction (existing)
    # ------------------------------------------------------------------

    def _extract_from_ssr(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract vehicles from SSR (server-side rendered) navigation data."""
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

                    dedup_key = f"{label}|{price_text}"
                    if dedup_key in seen:
                        continue
                    seen.add(dedup_key)

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

            if vehicles:
                break

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
        """Probe individual model pages for option/equipment data.

        Picks a sample of models with known base prices and navigates to
        their overview pages.  JSON API responses and embedded page data
        are searched for equipment entries with prices.
        """
        targets = [v for v in vehicles if v.base_price and v.url][:MAX_OPTION_PROBES]
        if not targets:
            return

        for vehicle in targets:
            try:
                import asyncio
                await asyncio.sleep(config.rate_limit_seconds)

                logger.info(f"Mercedes options: probing {vehicle.model} → {vehicle.url}")
                html, api_responses = await pool.fetch_with_api_capture(
                    vehicle.url,
                    extra_wait_ms=4000,
                    timeout_ms=25_000,
                )

                options: list[OptionData] = []

                # 1) Search captured API responses
                for resp in api_responses:
                    found = _search_json_for_options(resp.get("data"), self.brand)
                    options.extend(found)

                # 2) Search embedded script data in page HTML
                if not options:
                    soup = BeautifulSoup(html, "lxml")
                    options = _extract_options_from_scripts(soup, self.brand)

                # 3) Regex fallback: price patterns near equipment keywords
                if not options:
                    options = _extract_options_from_text(html, self.brand)

                if options:
                    vehicle.available_options = _dedupe_options(options)
                    logger.info(
                        f"Mercedes options: {vehicle.model} → "
                        f"{len(vehicle.available_options)} options"
                    )

            except Exception as e:
                logger.debug(f"Mercedes options: {vehicle.model} failed: {e}")


# ------------------------------------------------------------------
# Generic option-extraction helpers (reused across brands)
# ------------------------------------------------------------------

def _search_json_for_options(
    data: Any, brand: str, *, depth: int = 0, _seen_ids: set | None = None,
) -> list[OptionData]:
    """Recursively search a JSON tree for option/equipment-like objects.

    Looks for dicts that have a name-like key **and** a price-like key
    with a sensible automotive option price (€50 – €25 000).
    """
    if depth > 12 or data is None:
        return []
    if _seen_ids is None:
        _seen_ids = set()

    results: list[OptionData] = []

    if isinstance(data, dict):
        # Check if this dict itself looks like an option entry
        name_val = _extract_name(data)
        price_val = _extract_price(data)

        if name_val and price_val is not None and 50 <= price_val <= 25_000:
            obj_id = id(data)
            if obj_id not in _seen_ids:
                _seen_ids.add(obj_id)
                std = normalize_option_name(name_val, brand)
                results.append(OptionData(
                    standardized_name=std or "",
                    brand_specific_name=name_val,
                    price=price_val,
                    category=get_category(std) if std else _guess_category(data),
                    code=str(data.get("code", data.get("id", ""))),
                ))

        # Recurse into values (prioritise equipment-like keys)
        for key in sorted(data.keys(), key=lambda k: 0 if _is_equipment_key(k) else 1):
            results.extend(
                _search_json_for_options(data[key], brand, depth=depth + 1, _seen_ids=_seen_ids)
            )

    elif isinstance(data, list):
        for item in data:
            results.extend(
                _search_json_for_options(item, brand, depth=depth + 1, _seen_ids=_seen_ids)
            )

    return results


def _extract_options_from_scripts(soup: BeautifulSoup, brand: str) -> list[OptionData]:
    """Search all <script> tags for embedded JSON containing options."""
    options: list[OptionData] = []
    for script in soup.find_all("script"):
        if not script.string:
            continue
        text = script.string.strip()

        # Try parsing as JSON
        for blob in _find_json_blobs(text):
            try:
                data = json.loads(blob)
                options.extend(_search_json_for_options(data, brand))
            except (json.JSONDecodeError, RecursionError):
                continue

    return options


def _extract_options_from_text(html: str, brand: str) -> list[OptionData]:
    """Regex-based fallback: find price patterns near known option keywords."""
    options: list[OptionData] = []

    # Pattern: option-like text followed by price  ("Lenkradheizung ... 350,00 €")
    price_pat = re.compile(
        r'([A-ZÄÖÜ][A-Za-zÄÖÜäöüß\s\-]{4,50}?)\s*'
        r'(?:ab\s+)?'
        r'([\d]{1,3}(?:\.?\d{3})*(?:,\d{2})?)\s*€',
        re.MULTILINE,
    )
    for m in price_pat.finditer(html):
        name = m.group(1).strip()
        price = BaseEngine.parse_price(m.group(2))
        if not price or price < 50 or price > 25_000:
            continue
        std = normalize_option_name(name, brand)
        if std:
            options.append(OptionData(
                standardized_name=std,
                brand_specific_name=name,
                price=price,
                category=get_category(std),
            ))

    return options


# --- Utility helpers ---

_NAME_KEYS = ("name", "label", "title", "bezeichnung", "description", "Name", "Label")
_PRICE_KEYS = (
    "price", "preis", "Price", "Preis", "grossPrice", "netPrice",
    "basePrice", "formattedPrice", "priceFormatted", "amount",
    "surcharge", "Aufpreis",
)
_EQUIP_KEYS = {
    "equipment", "equipments", "options", "extras", "ausstattung",
    "sonderausstattung", "packages", "features", "accessories",
    "sonderausstattungen", "Ausstattung", "Equipment",
}


def _extract_name(d: dict) -> str | None:
    for k in _NAME_KEYS:
        v = d.get(k)
        if isinstance(v, str) and 2 < len(v) < 120:
            return v.strip()
    return None


def _extract_price(d: dict) -> float | None:
    for k in _PRICE_KEYS:
        v = d.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
        if isinstance(v, str):
            p = BaseEngine.parse_price(v)
            if p and p > 0:
                return p
    return None


def _is_equipment_key(key: str) -> bool:
    return key.lower() in _EQUIP_KEYS


def _guess_category(d: dict) -> str:
    cat = d.get("category", d.get("gruppe", d.get("group", "")))
    if isinstance(cat, str) and cat:
        low = cat.lower()
        if any(w in low for w in ("exterior", "außen", "aussen")):
            return "exterior"
        if any(w in low for w in ("interior", "innen")):
            return "interior"
        if any(w in low for w in ("comfort", "komfort")):
            return "comfort"
        if any(w in low for w in ("safety", "sicher")):
            return "safety"
        if any(w in low for w in ("drive", "antrieb", "fahrwerk")):
            return "drivetrain"
        if any(w in low for w in ("light", "licht")):
            return "lighting"
        if any(w in low for w in ("sound", "audio", "media")):
            return "sound"
    return "other"


def _find_json_blobs(text: str) -> list[str]:
    """Heuristically extract JSON objects/arrays from script text."""
    blobs: list[str] = []
    # Look for top-level assignments containing JSON
    for m in re.finditer(r'=\s*({[\s\S]{20,}?})\s*[;\n]', text):
        blobs.append(m.group(1))
    for m in re.finditer(r'=\s*(\[[\s\S]{20,}?\])\s*[;\n]', text):
        blobs.append(m.group(1))
    return blobs


def _dedupe_options(options: list[OptionData]) -> list[OptionData]:
    """Remove duplicate options, keeping the first occurrence."""
    seen: set[str] = set()
    deduped: list[OptionData] = []
    for o in options:
        key = o.standardized_name or o.brand_specific_name.lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(o)
    return deduped
