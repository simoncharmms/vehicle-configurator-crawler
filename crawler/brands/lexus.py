"""Lexus configurator crawler.

robots.txt:  No specific block on /modelle.
Strategy:    Curl fetch → embedded JSON state extraction.
             Lexus DE embeds a rich JSON state blob in a script element
             (id ending with '-data'), containing modelResults with
             model names, prices (cash/monthly), fuel types, engines,
             and grade info.
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
from crawler.network import fetch_html_curl

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
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.95,
            notes="Curl fetch + embedded JSON state (modelResults with prices).",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            logger.info(f"Lexus: fetching {MODELS_URL}")
            html = fetch_html_curl(MODELS_URL, timeout=30)
            soup = BeautifulSoup(html, "lxml")
            vehicles = self._extract_from_state(soup)

            if vehicles:
                logger.info(f"Lexus: extracted {len(vehicles)} vehicles from JSON state")
            else:
                errors.append("No vehicles found in Lexus JSON state data")
        except Exception as e:
            logger.error(f"Lexus crawl error: {e}")
            errors.append(str(e))

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    def _extract_from_state(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract vehicles from the embedded JSON state element.

        The Lexus DE models page embeds a JSON blob in a <script> element
        whose ID ends with '-data'. The structure includes:
        - modelResults.results[]: model groups
            - name: model name (e.g. "LBX", "NX")
            - cars[]: individual variants
                - price.cash: base price in EUR (integer)
                - grade.name: trim level
                - grade.ecoTag: fuel type hint
                - grade.features[]: feature list
                - engine.name: engine description
                - engine.transmission.name: transmission type
                - filterValues.fuelType[]: HEV/BEV/PHEV
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

                # Extract options from grade features
                options = self._extract_options_from_grade(grade, model_name)
                
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

    def _extract_options_from_grade(self, grade: dict, model_name: str) -> list:
        """Extract options from grade features array.
        
        Grade structure includes:
        - features[]: list of feature/option descriptions
        - name: trim level (e.g., "Basis", "Executive")
        - category: grade category
        """
        from crawler.base import OptionData
        from crawler.option_mappings import normalize_option_name
        
        options = []
        
        # Extract from features array
        features = grade.get('features', [])
        for feature in features:
            if not isinstance(feature, dict):
                continue
                
            feature_name = feature.get('name', '') or feature.get('title', '')
            if not feature_name:
                continue
            
            # Normalize option name
            std_name = normalize_option_name(feature_name, 'lexus')
            if not std_name:
                # If not in standard mappings, create a generic entry
                std_name = feature_name.lower().replace(' ', '_')
            
            opt = OptionData(
                standardized_name=std_name,
                brand_specific_name=feature_name,
                price=None,  # Lexus DE doesn't expose option prices
                category=feature.get('category', 'other'),
                code=feature.get('code', ''),
                currency='EUR'
            )
            options.append(opt)
        
        # Also extract grade name as an "option" (trim level)
        grade_name = grade.get('name', '').strip()
        if grade_name:
            std_grade = normalize_option_name(grade_name, 'lexus')
            if not std_grade:
                std_grade = f"trim_{grade_name.lower().replace(' ', '_')}"
            
            opt = OptionData(
                standardized_name=std_grade,
                brand_specific_name=grade_name,
                price=None,
                category='trim_level',
                code='',
                currency='EUR'
            )
            options.append(opt)
        
        return options
