"""Porsche configurator crawler.

Strategy:    Playwright rendering with ``networkidle`` of
             https://www.porsche.com/germany/models/ which is an SPA
             listing all model families and variants.  Option extraction
             via per-model page probing with API capture (MPI compare
             API and embedded script data).
Resilience:  Uses ``retry_with_backoff`` (2 attempts, exponential delay).
"""

from __future__ import annotations

import asyncio
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
from crawler.brands.mercedes import (
    _search_json_for_options,
    _extract_options_from_scripts,
    _extract_options_from_text,
    _dedupe_options,
)
from crawler.network import retry_with_backoff, BrowserPool

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.porsche.com/germany/models/"

# Top-level model family names to detect in headings
MODEL_FAMILIES = {"718", "911", "Taycan", "Panamera", "Macan", "Cayenne"}

# Max models to probe for option data
MAX_OPTION_PROBES = 5

# Slug mapping for model family detail pages
_PORSCHE_FAMILY_SLUGS: dict[str, str] = {
    "718": "718",
    "911": "911",
    "taycan": "taycan",
    "panamera": "panamera",
    "macan": "macan",
    "cayenne": "cayenne",
}


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

    # ------------------------------------------------------------------
    # Option extraction
    # ------------------------------------------------------------------

    async def _enrich_options(
        self,
        vehicles: list[VehicleData],
        pool: BrowserPool,
        config: CrawlConfig,
    ) -> None:
        """Probe Porsche model family pages for option/equipment data.

        Navigates to per-family detail pages (e.g. ``/germany/models/911/``)
        and captures:
        1. MPI compare API responses with technical data / option info
        2. Leasing data API with pricing per model code
        3. Embedded script data with feature / equipment details
        """
        # Deduplicate by model family — only probe each family once
        probed_families: set[str] = set()
        probe_count = 0

        for vehicle in vehicles:
            if probe_count >= MAX_OPTION_PROBES:
                break

            family = (vehicle.variant or "").lower()
            if not family or family in probed_families:
                continue

            slug = _PORSCHE_FAMILY_SLUGS.get(family)
            if not slug:
                continue

            probed_families.add(family)
            probe_count += 1

            try:
                await asyncio.sleep(config.rate_limit_seconds)

                probe_url = f"https://www.porsche.com/germany/models/{slug}/"
                logger.info(f"Porsche options: probing family {family} → {probe_url}")

                html, api_responses = await pool.fetch_with_api_capture(
                    probe_url,
                    extra_wait_ms=6000,
                    timeout_ms=40_000,
                )

                # Build a family-wide option list
                family_options: list[OptionData] = []
                family_prices: dict[str, float] = {}  # model_code -> price

                for resp in api_responses:
                    api_url = resp.get("url", "")
                    data = resp.get("data")

                    # --- MPI compare API: technical data with options ---
                    if "pccompare" in api_url and isinstance(data, dict):
                        mpi_opts = _extract_porsche_mpi_options(
                            data, self.brand,
                        )
                        family_options.extend(mpi_opts)

                    # --- Leasing data API: prices per model code ---
                    if "leasing-data" in api_url and isinstance(data, dict):
                        for code, info in data.items():
                            if isinstance(info, dict):
                                # Extract total/base price from summary
                                for item in info.get("summary", {}).get("items", []):
                                    label = item.get("label", "")
                                    if "Listenpreis" in label or "Grundpreis" in label:
                                        p = BaseEngine.parse_price(
                                            item.get("value", "")
                                        )
                                        if p:
                                            family_prices[code] = p

                    # --- Generic JSON option search ---
                    found = _search_json_for_options(data, self.brand)
                    family_options.extend(found)

                # Script-based extraction from page HTML
                if not family_options:
                    soup = BeautifulSoup(html, "lxml")
                    family_options = _extract_options_from_scripts(
                        soup, self.brand,
                    )
                    if not family_options:
                        family_options = _extract_porsche_features_from_html(
                            soup, self.brand,
                        )

                # Text regex fallback
                if not family_options:
                    family_options = _extract_options_from_text(
                        html, self.brand,
                    )

                # Apply collected options to all vehicles in this family
                if family_options:
                    deduped = _dedupe_options(family_options)
                    family_vehicles = [
                        v for v in vehicles
                        if (v.variant or "").lower() == family
                    ]
                    for v in family_vehicles:
                        v.available_options = list(deduped)

                    logger.info(
                        f"Porsche options: family {family} → "
                        f"{len(deduped)} options applied to "
                        f"{len(family_vehicles)} vehicles"
                    )

            except Exception as e:
                logger.debug(f"Porsche options: family {family} failed: {e}")


# ------------------------------------------------------------------
# Porsche-specific extraction helpers
# ------------------------------------------------------------------

def _extract_porsche_mpi_options(
    data: dict, brand: str,
) -> list[OptionData]:
    """Extract option data from the MPI compare API response.

    The response at ``mpi.pccompare.aws.porsche-preview.cloud`` contains:
    - ``models`` – list of model dicts with specs, interior options,
      color groups, and drivetrain info
    - ``technicalData`` – list of dicts with ``options`` (transmission,
      battery, drivetrain variants) and ``technicalSpecification``

    Returns recognizable options mapped to standard names.
    """
    options: list[OptionData] = []
    seen: set[str] = set()

    # --- From models list ---
    for model in data.get("models", []):
        if not isinstance(model, dict):
            continue

        # Generic equipment-like lists
        for key in ("features", "equipment", "standardEquipment", "options"):
            items = model.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        name = item.get("name", item.get("label", ""))
                        price = item.get("price", item.get("surcharge"))
                        if name:
                            std = normalize_option_name(name, brand)
                            opt_key = std or name.lower()
                            if opt_key in seen:
                                continue
                            seen.add(opt_key)
                            options.append(OptionData(
                                standardized_name=std or "",
                                brand_specific_name=name,
                                price=float(price) if price else None,
                                category=get_category(std) if std else _categorize_porsche_option(name),
                                code=str(item.get("id", "")),
                            ))

        # Interior design options (leather, Race-Tex, etc.)
        for ido in model.get("interiorDesignOptions", []):
            if isinstance(ido, dict):
                mat_name = ido.get("name", "")
                std = normalize_option_name(mat_name, brand)
                opt_key = std or mat_name.lower()
                if opt_key and opt_key not in seen:
                    seen.add(opt_key)
                    options.append(OptionData(
                        standardized_name=std or "",
                        brand_specific_name=mat_name,
                        price=None,
                        category=get_category(std) if std else "interior",
                    ))

        # Drivetrain info from model metadata
        wd = model.get("wheelDrive", "")
        if wd and "all" in wd.lower():
            if "allrad" not in seen:
                seen.add("allrad")
                options.append(OptionData(
                    standardized_name="allrad",
                    brand_specific_name=wd,
                    price=None,
                    category="drivetrain",
                ))

    # --- From technicalData list ---
    for td in data.get("technicalData", []):
        if not isinstance(td, dict):
            continue
        for opt in td.get("options", []):
            if isinstance(opt, dict):
                name = opt.get("name", "")
                if name:
                    std = normalize_option_name(name, brand)
                    opt_key = std or name.lower()
                    if opt_key in seen:
                        continue
                    seen.add(opt_key)
                    options.append(OptionData(
                        standardized_name=std or "",
                        brand_specific_name=name,
                        price=None,
                        category=get_category(std) if std else _categorize_porsche_option(name),
                        code=str(opt.get("id", "")),
                    ))

    return options


def _categorize_porsche_option(name: str) -> str:
    """Categorize a Porsche option by name when no standard mapping exists."""
    low = name.lower()
    if any(w in low for w in ("transmission", "doppelkupplung", "pdk", "manual", "getriebe")):
        return "drivetrain"
    if any(w in low for w in ("battery", "batterie", "akku")):
        return "drivetrain"
    if any(w in low for w in ("leather", "leder", "race-tex", "alcantara", "interior")):
        return "interior"
    if any(w in low for w in ("sport", "chrono", "pasm", "suspension", "fahrwerk")):
        return "drivetrain"
    return "other"


def _extract_porsche_features_from_html(
    soup: BeautifulSoup, brand: str,
) -> list[OptionData]:
    """Extract feature/option references from Porsche model page HTML.

    Porsche model pages list feature highlights in structured sections.
    Extracts recognizable option names and normalizes them.
    """
    options: list[OptionData] = []
    seen: set[str] = set()

    # Look for feature/highlight text in the page
    # Porsche pages use specific CSS classes for feature sections
    for el in soup.find_all(
        ["h3", "h4", "p", "span", "div"],
        string=re.compile(
            r"(?i)(PASM|PDK|Bose|Sport Chrono|LED Matrix|Panoramadach|"
            r"Head-Up|Sitzheizung|Adaptive|Luftfederung|Park Assist|"
            r"Surround View|Burmester|Lenkradheizung)"
        ),
    ):
        text = el.get_text(strip=True)
        if len(text) < 3 or len(text) > 120:
            continue

        std = normalize_option_name(text, brand)
        if not std:
            continue
        if std in seen:
            continue
        seen.add(std)

        options.append(OptionData(
            standardized_name=std,
            brand_specific_name=text,
            price=None,
            category=get_category(std),
        ))

    return options
