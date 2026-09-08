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
MAX_OPTION_PROBES = 50  # Probe all model families for full option coverage

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

        Uses a two-phase approach:

        Phase 1 — Equipment names from model overview pages:
            Navigates to each model overview page and extracts equipment
            items from embedded ``ssrData`` scripts (with ``equipmentId``,
            ``title``, ``isIncluded`` fields).  Also discovers the
            configurator typeClass from embedded links.

        Phase 2 — Real prices from the configurator entry API:
            For each unique typeClass, calls the Mercedes ``owcc-backend``
            entry API which returns ``curatedComponents`` with real gross
            and net prices per option (SA/PC codes).
        """
        import asyncio

        targets = [v for v in vehicles if v.base_price and v.url][:MAX_OPTION_PROBES]
        if not targets:
            return

        # Phase 1: extract equipment names + discover typeClasses
        type_class_map: dict[str, str] = {}  # typeClass → first vehicle URL
        vehicle_tc: dict[str, str] = {}  # vehicle URL → typeClass

        for vehicle in targets:
            try:
                await asyncio.sleep(config.rate_limit_seconds)

                logger.info(f"Mercedes options: probing {vehicle.model} → {vehicle.url}")
                html, api_responses = await pool.fetch_with_api_capture(
                    vehicle.url,
                    extra_wait_ms=4000,
                    timeout_ms=25_000,
                )

                options: list[OptionData] = []

                # 1) Search captured API responses for priced option entries
                for resp in api_responses:
                    found = _search_json_for_options(resp.get("data"), self.brand)
                    options.extend(found)

                # 2) Extract equipment from Mercedes ssrData scripts
                soup = BeautifulSoup(html, "lxml")
                equip_options = _extract_equipment_from_ssr(soup, self.brand)
                if equip_options:
                    options.extend(equip_options)

                # 3) Search other embedded script JSON blobs
                if not options:
                    options = _extract_options_from_scripts(soup, self.brand)

                # 4) Regex fallback: price patterns near equipment keywords
                if not options:
                    options = _extract_options_from_text(html, self.brand)

                if options:
                    vehicle.available_options = _dedupe_options(options)
                    logger.info(
                        f"Mercedes options: {vehicle.model} → "
                        f"{len(vehicle.available_options)} options"
                    )

                # Discover typeClass from configurator links in the HTML
                tc = _extract_type_class(html)
                if tc:
                    vehicle_tc[vehicle.url] = tc
                    type_class_map.setdefault(tc, vehicle.url)

            except Exception as e:
                logger.debug(f"Mercedes options: {vehicle.model} failed: {e}")

        # Phase 2: fetch real prices from the configurator entry API
        if type_class_map:
            try:
                await self._apply_configurator_prices(
                    vehicles, pool, type_class_map, vehicle_tc,
                )
            except Exception as e:
                logger.warning(f"Mercedes configurator pricing failed: {e}")

    async def _apply_configurator_prices(
        self,
        vehicles: list[VehicleData],
        pool: BrowserPool,
        type_class_map: dict[str, str],
        vehicle_tc: dict[str, str],
    ) -> None:
        """Fetch real per-option prices from the configurator entry API.

        The Mercedes ``owcc-backend`` entry API returns
        ``startPage.preConfigs[].curatedComponents[]`` with per-option
        gross and net prices for each equipment item (SA/PC codes).
        """
        import asyncio

        # Build price lookup: typeClass → {sa_code: price}
        tc_prices: dict[str, dict[str, float]] = {}
        tc_price_options: dict[str, list[OptionData]] = {}

        for tc in type_class_map:
            try:
                await asyncio.sleep(1.0)
                api_url = (
                    f"https://api.oneweb.mercedes-benz.com/owcc-backend/"
                    f"api/v3/de_DE/CCci/48097edf/entry?typeClass={tc}"
                )
                logger.info(f"Mercedes prices: fetching entry API for {tc}")
                data = await pool.call_json_api(
                    api_url,
                    origin_url="https://www.mercedes-benz.de",
                    timeout_ms=30_000,
                )

                if not isinstance(data, dict):
                    logger.debug(f"Mercedes prices: no data for {tc}")
                    continue

                prices: dict[str, float] = {}
                priced_opts: list[OptionData] = []

                pre_configs = (
                    data.get("startPage", {}).get("preConfigs", [])
                )
                for pc in pre_configs:
                    for comp in pc.get("curatedComponents", []):
                        comp_id = comp.get("id", "")
                        comp_name = comp.get("name", "")
                        price_obj = comp.get("price", {})
                        if not isinstance(price_obj, dict):
                            continue
                        gross = price_obj.get("price")
                        if not gross or not isinstance(gross, (int, float)):
                            continue
                        if gross < 50 or gross > 100_000:
                            continue

                        prices[comp_id] = float(gross)

                        # Also build OptionData from the configurator
                        std = normalize_option_name(comp_name, self.brand)
                        cat = (
                            get_category(std)
                            if std
                            else _guess_category_from_title(comp_name)
                        )
                        priced_opts.append(OptionData(
                            standardized_name=std or "",
                            brand_specific_name=comp_name,
                            price=float(gross),
                            category=cat,
                            code=comp_id,
                        ))

                tc_prices[tc] = prices
                tc_price_options[tc] = priced_opts
                logger.info(
                    f"Mercedes prices: {tc} → {len(prices)} priced options"
                )

            except Exception as e:
                logger.debug(f"Mercedes prices: {tc} failed: {e}")

        if not tc_prices:
            return

        # Apply prices to vehicles
        applied_count = 0
        for vehicle in vehicles:
            tc = vehicle_tc.get(vehicle.url, "")
            if not tc or tc not in tc_prices:
                # Try matching by any typeClass that has matching SA codes
                for try_tc, try_prices in tc_prices.items():
                    if any(
                        opt.code in try_prices
                        for opt in vehicle.available_options
                        if opt.code
                    ):
                        tc = try_tc
                        break

            if not tc or tc not in tc_prices:
                # No typeClass match — still merge configurator options
                # as new options for this vehicle
                continue

            prices = tc_prices[tc]
            priced_opts = tc_price_options.get(tc, [])

            # Match existing options by SA code and set price
            matched_codes: set[str] = set()
            for opt in vehicle.available_options:
                if opt.code and opt.code in prices:
                    opt.price = prices[opt.code]
                    matched_codes.add(opt.code)
                    applied_count += 1

            # Add priced options from configurator that weren't
            # already present (new options the model page missed)
            existing_codes = {o.code for o in vehicle.available_options if o.code}
            existing_names = {
                o.standardized_name or o.brand_specific_name.lower()
                for o in vehicle.available_options
            }
            for opt in priced_opts:
                if opt.code and opt.code not in existing_codes:
                    key = opt.standardized_name or opt.brand_specific_name.lower()
                    if key not in existing_names:
                        vehicle.available_options.append(opt)
                        existing_codes.add(opt.code)
                        existing_names.add(key)
                        applied_count += 1

            vehicle.available_options = _dedupe_options(
                vehicle.available_options,
            )

        logger.info(
            f"Mercedes prices: applied {applied_count} prices "
            f"across {len(tc_prices)} typeClasses"
        )


# ------------------------------------------------------------------
# TypeClass extraction from configurator links
# ------------------------------------------------------------------

def _extract_type_class(html: str) -> str | None:
    """Extract the Mercedes typeClass from car-configurator links in HTML.

    Configurator links follow the pattern::

        car-configurator.html/start/CCci/DE/de/tc/{typeClass}

    Returns the first typeClass found (e.g. ``"W206"``) or ``None``.
    """
    match = re.search(
        r'car-configurator\.html/start/CCci/DE/de/tc/([A-Z0-9]+)',
        html,
    )
    return match.group(1) if match else None


# ------------------------------------------------------------------
# Mercedes ssrData equipment extraction
# ------------------------------------------------------------------

def _extract_equipment_from_ssr(
    soup: BeautifulSoup, brand: str,
) -> list[OptionData]:
    """Extract equipment items from Mercedes model-page ``ssrData`` scripts.

    Mercedes model overview pages embed structured equipment objects in
    large ``<script>`` tags.  Each object has ``equipmentId``, ``title``,
    ``isIncluded`` (standard vs. extra-cost), and a description.
    Items marked ``isIncluded=False`` are optional extras.

    Returns a list of :class:`OptionData` for **non-included** equipment
    (i.e. available paid options).  Prices are set to ``None`` because
    individual option pricing is only available in the configurator.
    """
    results: list[OptionData] = []

    for script in soup.find_all("script"):
        if not script.string or "equipmentId" not in script.string:
            continue

        text = script.string.strip()

        # ssrData scripts use  window.ssrData["<hash>"] = { ... };
        match = re.search(
            r'\["([a-f0-9]+)"\]\s*=\s*({.*})\s*;?\s*$',
            text,
            re.DOTALL,
        )
        if not match:
            continue

        try:
            data = json.loads(match.group(2))
        except json.JSONDecodeError:
            continue

        # Recursively find equipment-like dicts in the tree.
        equip_items = _find_equipment_items(data)
        for item in equip_items:
            title = item.get("title", "").strip()
            eid = item.get("equipmentId", "")
            is_included = item.get("isIncluded", True)

            if not title or len(title) < 3:
                continue

            # Only non-included items are selectable options
            if is_included:
                continue

            std = normalize_option_name(title, brand)
            cat = get_category(std) if std else _guess_category_from_title(title)

            results.append(OptionData(
                standardized_name=std or "",
                brand_specific_name=title,
                price=None,  # not available on model overview pages
                category=cat,
                code=eid,
            ))

        if results:
            break  # Typically only one large ssrData script

    return results


def _find_equipment_items(obj: Any, depth: int = 0) -> list[dict]:
    """Recursively collect dicts containing ``equipmentId`` and ``title``."""
    if depth > 12 or obj is None:
        return []
    results: list[dict] = []
    if isinstance(obj, dict):
        if "equipmentId" in obj and "title" in obj:
            results.append(obj)
        for v in obj.values():
            results.extend(_find_equipment_items(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_equipment_items(item, depth + 1))
    return results


def _guess_category_from_title(title: str) -> str:
    """Heuristic category guess from an equipment title string."""
    low = title.lower()
    if any(w in low for w in ("sound", "audio", "burmester", "musik")):
        return "sound"
    if any(w in low for w in ("display", "head-up", "digital", "mbux")):
        return "technology"
    if any(w in low for w in ("sitz", "lenkrad", "heizung", "klima", "komfort", "standheizung")):
        return "comfort"
    if any(w in low for w in ("led", "licht", "light", "scheinwerfer")):
        return "lighting"
    if any(w in low for w in ("kamera", "assistent", "pre-safe", "airbag", "brems")):
        return "safety"
    if any(w in low for w in ("fahrwerk", "lenkung", "bremse", "4matic", "antrieb")):
        return "drivetrain"
    if any(w in low for w in ("dach", "panoram", "anhäng", "glas", "akustik")):
        return "exterior"
    if any(w in low for w in ("ambient", "innenraum", "leder", "mittelkonsole", "sonnen")):
        return "interior"
    if any(w in low for w in ("paket",)):
        return "packages"
    return "other"


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
