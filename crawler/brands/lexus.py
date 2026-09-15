"""Lexus configurator crawler.

robots.txt:  No specific block on /modelle.
Strategy:    Playwright rendering of ``/modelle`` → embedded JSON state
             extraction (modelResults with prices, grades, features) and rotating
             Texus model tokens.  Public Texus JSON endpoints then return the
             published positive prices of paint, wheels, upholstery, packages,
             optional equipment, and accessories for every configuration.

The Lexus models page embeds a rich JSON state blob in a hidden element whose
``id`` ends with ``-data``.  Each model group contains
cars with grade objects that include feature lists (standard equipment)
and a ``modelMap`` with Texus product tokens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
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
from crawler.network import retry_with_backoff, BrowserPool, get_random_user_agent

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.lexus.de/modelle"
TEXUS_API_BASE = "https://texus.lexus-europe.com/v2/car"
PROMOTION_TIMEFRAME = "00000000-0000-0000-0000-000000000000"


@dataclass(frozen=True)
class LexusMarket:
    """Public Lexus site/API settings for a market verified with prices."""

    code: str
    host: str
    api_country: str
    language: str
    currency: str

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    @property
    def models_url(self) -> str:
        return f"{self.base_url}/modelle"


# Verified 2026-09-15 against the public Texus endpoints:
# DE/UX returned EUR 850 paint, EUR 2,150 Technology Pack and EUR 870
# detachable towbar; AT/UX returned EUR 930 paint.  Do not add a country
# until its public API has been observed returning a positive option price.
LEXUS_MARKETS: dict[str, LexusMarket] = {
    market.code: market for market in (
        LexusMarket("DE", "www.lexus.de", "de", "de", "EUR"),
        LexusMarket("AT", "www.lexus.at", "at", "de", "EUR"),
    )
}
LEXUS_SUPPORTED_MARKETS: tuple[str, ...] = tuple(LEXUS_MARKETS)

# The model overview currently exposes 49 individual cars.  Keep the default
# at that complete set, while allowing a small bounded live probe in CI/manual
# diagnostics.  Requests are made sequentially and each vehicle bundle is
# separated by the crawler's configured rate limit.
MAX_OPTION_PROBES = int(os.getenv("LEXUS_MAX_OPTION_PROBES", "49"))

FUEL_TYPE_MAP = {
    "HEV": "hybrid",
    "BEV": "electric",
    "PHEV": "hybrid",
    "ICEV": "petrol",
}


def get_market(market: str) -> LexusMarket:
    """Return verified public-site settings for an ISO market code."""
    code = (market or "DE").upper()
    try:
        return LEXUS_MARKETS[code]
    except KeyError:
        raise ValueError(
            f"Lexus market '{market}' not supported. "
            f"Available: {', '.join(LEXUS_SUPPORTED_MARKETS)}"
        ) from None


def _extract_model_tokens(soup: BeautifulSoup) -> dict[str, str]:
    """Extract ``{model internal code: Texus product token}`` from page state.

    Lexus injects ``window.dxp.settings.modelMap`` as JSON inside an inline
    script.  The opaque per-model token is required by the public Texus API;
    it changes with the sales catalogue, so it must never be hard-coded.
    """
    decoder = json.JSONDecoder()
    # The current page puts the JSON in a hidden ``*-data`` div (rather than
    # a script), but some Lexus/AEM versions used an inline script.
    elements = [
        *soup.find_all(id=re.compile(r".*-data$")),
        *soup.find_all("script"),
    ]
    for element in elements:
        text = element.string or element.get_text() or ""
        marker = '"modelMap":'
        pos = text.find(marker)
        if pos < 0:
            continue
        start = text.find("{", pos + len(marker))
        if start < 0:
            continue
        try:
            model_map, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(model_map, dict):
            continue
        tokens: dict[str, str] = {}
        for key, item in model_map.items():
            if not isinstance(item, dict):
                continue
            token = item.get("token")
            code = item.get("internalCode") or key
            if isinstance(code, str) and isinstance(token, str) and token:
                tokens[code.upper()] = token
        if tokens:
            return tokens
    logger.warning("Lexus: no Texus model tokens in page state")
    return {}


def _texus_option_data(
    payloads: list[tuple[str, Any]],
    *,
    brand: str,
    currency: str,
) -> list[OptionData]:
    """Convert positive-price records from Texus response payloads.

    The public API deliberately returns separate payloads for paint, wheels,
    upholstery, packs, equipment, and accessories.  These response fields use
    PascalCase (``Name``, ``Price``, ``PriceIncl``, ``PriceInfo.Currency``).
    Only a positive published cash/list price is included: zero-price
    equipment is standard/included and must not become a made-up price.
    """
    options: list[OptionData] = []
    seen: set[tuple[str, str]] = set()

    def price_of(item: dict[str, Any]) -> float | None:
        for key in ("Price", "PriceIncl", "PriceInVat"):
            value = item.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
        price_info = item.get("PriceInfo")
        if isinstance(price_info, dict):
            for key in ("ListPriceWithDiscount", "ListPrice", "NetPrice"):
                value = price_info.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return float(value)
        return None

    def emit(item: Any, source: str) -> None:
        if not isinstance(item, dict):
            return
        name = item.get("Name")
        price = price_of(item)
        if not isinstance(name, str) or not name.strip() or price is None:
            return
        code = item.get("InternalCode") or item.get("Code") or item.get("ID") or ""
        code = str(code)
        key = (code, name.strip().lower())
        if key in seen:
            return
        seen.add(key)
        standardized = normalize_option_name(name, brand)
        category = get_category(standardized) if standardized else {
            "colours": "exterior",
            "wheels": "wheels",
            "upholsteries": "interior",
            "packs": "packages",
            "equipment": "other",
            "accessories": "accessories",
        }.get(source, "other")
        price_info = item.get("PriceInfo")
        item_currency = (
            price_info.get("Currency") if isinstance(price_info, dict) else ""
        )
        if not isinstance(item_currency, str):
            item_currency = ""
        item_currency = item_currency.strip()
        options.append(OptionData(
            standardized_name=standardized,
            brand_specific_name=name.strip(),
            price=price,
            category=category,
            code=code,
            currency=item_currency or currency,
        ))

    for source, payload in payloads:
        if source == "colours" and isinstance(payload, dict):
            # The API currently returns ExteriorColours and may additionally
            # return roof/body colours for two-tone configurations.
            for key, values in payload.items():
                if "colour" in key.lower() and isinstance(values, list):
                    for item in values:
                        emit(item, source)
        elif source == "wheels" and isinstance(payload, dict):
            for item in payload.get("Wheels", []):
                emit(item, source)
        elif isinstance(payload, list):
            for item in payload:
                emit(item, source)

    return options


class LexusConfiguratorAPI:
    """Small, polite client for Lexus's public Texus pricing endpoints."""

    _METHODS: tuple[tuple[str, str, str], ...] = (
        ("getColourInfo", "", "colours"),
        ("getCarWheels", "", "wheels"),
        ("getUpholsteries", "", "upholsteries"),
        ("getPacks", "", "packs"),
        ("getOptionalEquipment", "/unfiltered", "equipment"),
        ("getOptionalAccessories", "/unfiltered", "accessories"),
    )

    def __init__(self, market: LexusMarket) -> None:
        self.market = market
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Origin": market.base_url,
            "Referer": f"{market.models_url}/",
            "User-Agent": get_random_user_agent(),
        })

    def _url(self, method: str, token: str, car_id: str, tail: str) -> str:
        return (
            f"{TEXUS_API_BASE}/{method}/lexus/{self.market.api_country}/"
            f"{self.market.language}/{token}/promotiontimeframe/"
            f"{PROMOTION_TIMEFRAME}/car/{car_id}{tail}"
        )

    def fetch_options(self, token: str, car_id: str) -> list[OptionData]:
        """Fetch all published paid choices for one Lexus configuration."""
        payloads: list[tuple[str, Any]] = []
        for method, tail, source in self._METHODS:
            url = self._url(method, token, car_id, tail)
            try:
                response = self.session.get(url, timeout=25)
                response.raise_for_status()
                payloads.append((source, response.json()))
            except (requests.RequestException, ValueError) as exc:
                # A single dead option category must not discard the other
                # real prices returned for this car.
                logger.warning(
                    "Lexus [%s]: %s for car %s failed: %s",
                    self.market.code, method, car_id, exc,
                )
        return _texus_option_data(
            payloads, brand="Lexus", currency=self.market.currency,
        )


@BrandRegistry.register
class LexusCrawler(BrandCrawler):
    brand = "Lexus"
    base_url = "https://www.lexus.de"
    configurator_url = MODELS_URL
    SUPPORTED_MARKETS = LEXUS_SUPPORTED_MARKETS

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.PLAYWRIGHT,
            rate_limit_seconds=3.0,
            confidence=0.95,
            notes="Playwright model state + public Texus JSON option-price API.",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        try:
            return await retry_with_backoff(
                self._crawl_inner, config, max_retries=1, base_delay=1.0,
            )
        except Exception as e:
            logger.warning(f"Lexus: all retry attempts exhausted: {e}")
            return CrawlResult(
                brand=self.brand,
                market=self.market,
                errors=[f"All attempts failed: {e}"],
            )

    async def _crawl_inner(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []
        market = get_market(self.market)

        try:
            logger.info(
                "Lexus [%s]: fetching %s (Playwright)", self.market, market.models_url,
            )
            pool = await BrowserPool.acquire()
            html = await pool.fetch_html(
                market.models_url,
                wait_until="networkidle",
                timeout_ms=35_000,
            )
            soup = BeautifulSoup(html, "lxml")
            tokens = _extract_model_tokens(soup)
            vehicles = self._extract_from_state(soup, tokens)

            if vehicles:
                logger.info(
                    "Lexus [%s]: extracted %d vehicles from JSON state",
                    self.market, len(vehicles),
                )
                await self._attach_priced_options(vehicles, cfg, errors)
                option_count = sum(len(v.available_options) for v in vehicles)
                priced_count = sum(
                    1 for vehicle in vehicles for option in vehicle.available_options
                    if option.price is not None and option.price > 0
                )
                logger.info(
                    "Lexus [%s]: %d option instances, %d with published prices",
                    self.market, option_count, priced_count,
                )
            else:
                errors.append("No vehicles found in Lexus JSON state data")

        except Exception as e:
            logger.error(f"Lexus crawl error: {e}")
            errors.append(str(e))
        finally:
            try:
                await BrowserPool.close()
            except Exception:
                pass

        return CrawlResult(
            brand=self.brand,
            market=self.market,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    async def _attach_priced_options(
        self,
        vehicles: list[VehicleData],
        cfg: CrawlConfig,
        errors: list[str],
    ) -> None:
        """Append positive prices returned by the public Texus API.

        The configured delay is between *vehicle bundles* (each bundle is the
        six documented endpoint categories), rather than launching a fan-out
        of requests.  This keeps a complete 49-car daily run bounded while
        retaining the existing 3-second Lexus rate limit.
        """
        api = LexusConfiguratorAPI(get_market(self.market))
        targets = [
            vehicle for vehicle in vehicles
            if vehicle.raw_data.get("_lexus_car_id")
            and vehicle.raw_data.get("_lexus_token")
        ][:MAX_OPTION_PROBES]
        if len(targets) < len(vehicles):
            errors.append(
                f"Option price probe capped at {len(targets)}/{len(vehicles)} vehicles"
            )

        try:
            for index, vehicle in enumerate(targets):
                car_id = vehicle.raw_data["_lexus_car_id"]
                token = vehicle.raw_data["_lexus_token"]
                priced = await asyncio.to_thread(api.fetch_options, token, car_id)
                # The prices describe selectable items and deliberately remain
                # separate from the grade's included/zero-price feature list.
                existing = {
                    (option.code, option.brand_specific_name.casefold())
                    for option in vehicle.available_options
                }
                for option in priced:
                    key = (option.code, option.brand_specific_name.casefold())
                    if key not in existing:
                        vehicle.available_options.append(option)
                        existing.add(key)
                if index + 1 < len(targets) and cfg.rate_limit_seconds:
                    await asyncio.sleep(cfg.rate_limit_seconds)
        finally:
            api.session.close()
            # These values are only routing metadata for the live price call,
            # not useful data for the published vehicle snapshot.
            for vehicle in vehicles:
                vehicle.raw_data.pop("_lexus_car_id", None)
                vehicle.raw_data.pop("_lexus_token", None)

    def _extract_from_state(
        self,
        soup: BeautifulSoup,
        model_tokens: dict[str, str] | None = None,
    ) -> list[VehicleData]:
        """Extract vehicles and options from the embedded JSON state element.

        The Lexus models page embeds a JSON blob in a hidden element whose
        ``id`` ends with ``-data``. The structure includes:

        - ``modelResults.results[]`` – model groups (LBX, UX, NX, RX, …)
            - ``name`` – model name
            - ``cars[]`` – individual variants
                - ``price.cash`` – base price in EUR
                - ``grade.name`` – trim level
                - ``grade.features[]`` – standard-equipment feature strings
                - ``grade.featuresText`` – optional prose features
                - ``engine.name`` – engine description
                - ``engine.transmission.name`` – transmission type
                - ``filterValues.fuelType[]`` – HEV/BEV/PHEV

        Texus product tokens are parsed separately from the same page and
        retained temporarily in ``raw_data`` for the price-API phase.
        """
        vehicles: list[VehicleData] = []
        seen: set[str] = set()

        # Find the state element (ID ending with '-data')
        state_el = soup.find(id=re.compile(r".*-data$"))
        if not state_el or not state_el.string:
            logger.warning("Lexus: no JSON state element found")
            return vehicles

        try:
            data = json.loads(state_el.string)
        except json.JSONDecodeError as e:
            logger.error(f"Lexus: failed to parse JSON state: {e}")
            return vehicles

        model_results = data.get("modelResults", {}).get("results", [])
        if not model_results:
            logger.warning("Lexus: no modelResults in JSON state")
            return vehicles

        for model_group in model_results:
            model_name = model_group.get("name", "")
            if not model_name:
                continue

            for car in model_group.get("cars", []):
                if not car.get("show", True):
                    continue

                # Price
                price_data = car.get("price", {})
                base_price = price_data.get("cash")
                if base_price is not None:
                    try:
                        base_price = float(base_price)
                    except (ValueError, TypeError):
                        base_price = None

                # Grade/variant
                grade = car.get("grade", {})
                grade_name = grade.get("name", "").strip()
                grade_category = grade.get("category", "").strip()
                variant = grade_name or grade_category

                # Fuel type from filterValues
                fuel_types = car.get("filterValues", {}).get("fuelType", [])
                fuel_type = ""
                for ft in fuel_types:
                    if ft in FUEL_TYPE_MAP:
                        fuel_type = FUEL_TYPE_MAP[ft]
                        break
                # Fallback: ecoTag
                if not fuel_type:
                    eco_tag = grade.get("ecoTag", "")
                    if eco_tag:
                        fuel_type = FUEL_TYPE_MAP.get(eco_tag.upper(), eco_tag.lower())

                # Engine
                engine = car.get("engine", {})
                engine_name = engine.get("name", "")

                # Dedup key
                dedup_key = f"{model_name}|{variant}|{base_price}"
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                # Build model display name
                display_model = model_name
                if variant and variant not in model_name:
                    display_model = f"{model_name} {variant}"

                # URL
                model_code = car.get("model", {}).get("code", "")
                market = get_market(self.market)
                url = (
                    f"{market.base_url}/modelle/{model_code}"
                    if model_code else market.models_url
                )

                # --- Option extraction from grade features ---
                options = _extract_options_from_grade(grade, engine, car, self.brand)

                vehicles.append(VehicleData(
                    brand=self.brand,
                    model=display_model,
                    variant=engine_name,
                    base_price=base_price,
                    currency=self.currency,
                    market=self.market,
                    fuel_type=fuel_type,
                    url=url,
                    available_options=options,
                    raw_data={
                        "_lexus_car_id": car.get("id", ""),
                        "_lexus_token": (model_tokens or {}).get(
                            str(model_code).upper(), ""
                        ),
                    },
                ))

        return vehicles


# ------------------------------------------------------------------
# Lexus option extraction helpers
# ------------------------------------------------------------------

def _extract_options_from_grade(
    grade: dict[str, Any],
    engine: dict[str, Any],
    car: dict[str, Any],
    brand: str,
) -> list[OptionData]:
    """Extract equipment options from Lexus grade data.

    Sources:
    1. ``grade.features[]`` – list of feature name strings
    2. ``grade.featuresText`` – optional prose features (parsed for keywords)
    3. ``engine`` data – transmission, drivetrain info
    4. ``car.filterValues`` – fuel type, body type signals
    """
    options: list[OptionData] = []
    seen: set[str] = set()

    # 1) Grade features list
    features = grade.get("features", [])
    for feature_name in features:
        if not isinstance(feature_name, str) or len(feature_name) < 3:
            continue

        opt = _feature_to_option(feature_name, brand)
        if opt:
            key = opt.standardized_name or opt.brand_specific_name.lower()
            if key not in seen:
                seen.add(key)
                options.append(opt)

    # 2) Features text (prose)
    features_text = grade.get("featuresText", "")
    if isinstance(features_text, str) and features_text:
        # Split on common separators
        for part in re.split(r'[,;\n•·–—]', features_text):
            part = part.strip()
            if len(part) < 4:
                continue
            opt = _feature_to_option(part, brand)
            if opt:
                key = opt.standardized_name or opt.brand_specific_name.lower()
                if key not in seen:
                    seen.add(key)
                    options.append(opt)

    # 3) Transmission from engine data
    transmission = engine.get("transmission", {})
    trans_name = ""
    if isinstance(transmission, dict):
        trans_name = transmission.get("name", "")
    elif isinstance(transmission, str):
        trans_name = transmission

    if trans_name and len(trans_name) > 3:
        std = normalize_option_name(trans_name, brand)
        if std and std not in seen:
            seen.add(std)
            options.append(OptionData(
                standardized_name=std,
                brand_specific_name=trans_name,
                price=None,
                category=get_category(std),
            ))

    # 4) Drivetrain hints from filterValues
    drive_types = car.get("filterValues", {}).get("driveType", [])
    for dt in drive_types:
        if isinstance(dt, str) and any(kw in dt.lower() for kw in ("awd", "4wd", "allrad", "four")):
            if "allrad" not in seen:
                seen.add("allrad")
                options.append(OptionData(
                    standardized_name="allrad",
                    brand_specific_name=dt,
                    price=None,
                    category="drivetrain",
                ))

    return options


def _feature_to_option(feature_name: str, brand: str) -> OptionData | None:
    """Convert a single Lexus feature string to an OptionData.

    Attempts to normalize the name to a standard key; falls back to
    brand-specific-only when the feature is recognizable as automotive
    equipment but not in the mapping.
    """
    std = normalize_option_name(feature_name, brand)
    cat = get_category(std) if std else _guess_category_from_feature(feature_name)

    # Skip features that are just wheel size specs without standardization
    if not std and _is_wheel_size_only(feature_name):
        # Still record as alloy_wheels if it mentions Leichtmetall
        low = feature_name.lower()
        if "leichtmetall" in low or "aluminium" in low:
            return OptionData(
                standardized_name="alloy_wheels",
                brand_specific_name=feature_name,
                price=None,
                category="wheels",
            )
        return None

    if std:
        return OptionData(
            standardized_name=std,
            brand_specific_name=feature_name,
            price=None,
            category=cat,
        )

    # Keep unmapped features that are recognizable equipment
    if _is_automotive_equipment(feature_name):
        return OptionData(
            standardized_name="",
            brand_specific_name=feature_name,
            price=None,
            category=cat,
        )

    return None


def _is_wheel_size_only(text: str) -> bool:
    """Check if text is purely a wheel size specification."""
    return bool(re.match(
        r"^[\d]+['\"]?\s*(Zoll|zoll)?\s*(Leichtmetall|Aluminium|Aluminiumfelgen|Felgen)?",
        text.strip(),
    )) and len(text) < 15


def _is_automotive_equipment(text: str) -> bool:
    """Check if a feature string describes recognizable automotive equipment."""
    low = text.lower()
    # Skip very generic or short items
    if len(text) < 5 or len(text) > 200:
        return False

    equipment_keywords = [
        "kamera", "sensor", "assistent", "heizung", "klimat", "licht",
        "led", "display", "audio", "sound", "leder", "stoff",
        "sitz", "dach", "spiegel", "schlüssel", "navigation",
        "bluetooth", "usb", "radar", "parksen", "airbag",
        "tempomat", "regensensor", "scheibenwisch", "diebstahl",
        "alarm", "rückspiegel", "heckklappe", "anhäng",
        "privacy", "tönung", "elektr", "monitor", "massage",
        "belüftung", "ionisierung", "geräuschdämpfung", "anc",
        "smart key", "velours", "einstieg", "badge",
        "kühlergrill", "design",
    ]

    return any(kw in low for kw in equipment_keywords)


def _guess_category_from_feature(text: str) -> str:
    """Heuristic category guess from a Lexus feature string."""
    low = text.lower()
    if any(w in low for w in ("sound", "audio", "lautsprecher", "mark levinson")):
        return "sound"
    if any(w in low for w in ("display", "head-up", "digital", "monitor", "navigation")):
        return "technology"
    if any(w in low for w in (
        "sitz", "lenkrad", "heizung", "klima", "komfort", "einstieg",
        "massage", "geräusch", "anc",
    )):
        return "comfort"
    if any(w in low for w in ("led", "licht", "scheinwerfer", "nebel", "leuchtweite")):
        return "lighting"
    if any(w in low for w in ("kamera", "assistent", "airbag", "brems", "diebstahl", "alarm", "sensor")):
        return "safety"
    if any(w in low for w in ("fahrwerk", "lenkung", "bremse", "antrieb", "4matic", "getriebe")):
        return "drivetrain"
    if any(w in low for w in ("dach", "panoram", "anhäng", "glas", "akustik", "spiegel", "privacy", "grill")):
        return "exterior"
    if any(w in low for w in ("ambient", "innenraum", "leder", "velours", "stoff")):
        return "interior"
    if any(w in low for w in ("felgen", "räder", "reifen", "rad")):
        return "wheels"
    if any(w in low for w in ("ladeanschluss", "laden", "charger")):
        return "technology"
    return "other"
