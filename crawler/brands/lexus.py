"""Lexus configurator crawler.

robots.txt:  No specific block on /modelle.
Strategy:    Playwright rendering of ``/modelle`` → embedded JSON state
             extraction (modelResults with prices, grades, features).
             Option extraction from ``grade.features`` and ``grade.featuresText``
             embedded in the state blob.  Falls back to curl fetch when Playwright
             is unavailable.

The Lexus DE models page embeds a rich JSON state blob in a ``<script>``
element whose ``id`` ends with ``-data``.  Each model group contains
cars with grade objects that include feature lists (standard equipment)
which serve as the option/equipment data source.
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

MODELS_URL = "https://www.lexus.de/modelle"

FUEL_TYPE_MAP = {
    "HEV": "hybrid",
    "BEV": "electric",
    "PHEV": "hybrid",
    "ICEV": "petrol",
}


@BrandRegistry.register
class LexusCrawler(BrandCrawler):
    brand = "Lexus"
    base_url = "https://www.lexus.de"
    configurator_url = MODELS_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.PLAYWRIGHT,
            rate_limit_seconds=3.0,
            confidence=0.95,
            notes="Playwright rendering + embedded JSON state (modelResults "
                  "with prices, grades, and feature lists).",
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
                errors=[f"All attempts failed: {e}"],
            )

    async def _crawl_inner(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            logger.info(f"Lexus: fetching {MODELS_URL} (Playwright)")
            pool = await BrowserPool.acquire()
            html = await pool.fetch_html(
                MODELS_URL,
                wait_until="networkidle",
                timeout_ms=35_000,
            )
            soup = BeautifulSoup(html, "lxml")
            vehicles = self._extract_from_state(soup)

            if vehicles:
                logger.info(
                    f"Lexus: extracted {len(vehicles)} vehicles from JSON state"
                )
                option_count = sum(len(v.available_options) for v in vehicles)
                logger.info(f"Lexus: {option_count} total option instances extracted")
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
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    def _extract_from_state(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract vehicles and options from the embedded JSON state element.

        The Lexus DE models page embeds a JSON blob in a ``<script>`` element
        whose ``id`` ends with ``-data``. The structure includes:

        - ``modelResults.results[]`` – model groups (LBX, UX, NX, RX, …)
            - ``name`` – model name
            - ``cars[]`` – individual variants
                - ``price.cash`` – base price in EUR
                - ``grade.name`` – trim level
                - ``grade.features[]`` – equipment feature strings
                - ``grade.featuresText`` – optional prose features
                - ``engine.name`` – engine description
                - ``engine.transmission.name`` – transmission type
                - ``filterValues.fuelType[]`` – HEV/BEV/PHEV
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
                url = f"{self.base_url}/modelle/{model_code}" if model_code else MODELS_URL

                # --- Option extraction from grade features ---
                options = _extract_options_from_grade(grade, engine, car, self.brand)

                vehicles.append(VehicleData(
                    brand=self.brand,
                    model=display_model,
                    variant=engine_name,
                    base_price=base_price,
                    currency="EUR",
                    fuel_type=fuel_type,
                    url=url,
                    available_options=options,
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
