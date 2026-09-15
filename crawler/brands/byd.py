"""BYD configurator crawler using BYD's public CMS configuration catalogues.

Each vehicle configuration page loads a public JSON document from
``cms-api.byd.com/car/byd/{market}/{model-code}.json``.  It exposes the
sellable trim configurations in a list and their ``skuList`` combinations.
Colour, interior and wheel choices carry an exact ``price``, ``currency`` and
``code``; no browser session or dealer login is involved.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import requests

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, OptionData, VehicleData
from crawler.brands.registry import BrandRegistry
from crawler.network import retry_with_backoff

logger = logging.getLogger(__name__)

CMS_API = "https://cms-api.byd.com/car/byd"
# DE and NL were verified model-by-model from datacentre requests.  The Dutch
# catalogue currently does not offer DOLPHIN or DOLPHIN SURF; those 404s are
# treated as normal market availability, not as invented data.
MARKET_ROUTES = {"DE": "de", "NL": "nl"}
BYD_SUPPORTED_MARKETS: tuple[str, ...] = tuple(MARKET_ROUTES)

# (display name, configuration-page slug, CMS model code, fuel type)
MODELS: tuple[tuple[str, str, str, str], ...] = (
    ("BYD ATTO 2", "atto-2", "CX013030", "electric"),
    ("BYD ATTO 3 EVO", "atto-3-evo", "CX004031", "electric"),
    ("BYD DOLPHIN", "dolphin", "CX008064", "electric"),
    ("BYD DOLPHIN SURF", "dolphin-surf", "CX017047", "electric"),
    ("BYD SEAL", "seal", "CX009017", "electric"),
    ("BYD SEALION 7", "sealion-7", "CX012026", "electric"),
    ("BYD TANG", "tang", "CX006007", "electric"),
    ("BYD ATTO 2 DM-i", "atto-2-dm-i", "CX025001", "hybrid"),
    ("BYD DOLPHIN G DM-i", "dolphin-g-dm-i", "CX027001", "hybrid"),
    ("BYD SEAL U DM-i", "seal-u-dm-i", "CX007011", "hybrid"),
    ("BYD SEAL 6 DM-i Touring", "seal-6-dm-i-touring", "CX023060", "hybrid"),
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; vehicle-configurator-crawler/1.0)",
    "Accept": "application/json, text/plain, */*",
}


def _number(value: Any) -> float | None:
    """Parse a numeric API field without ever interpreting formatted locale text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _options_from_catalogue(catalogue: list[dict[str, Any]], fallback_currency: str) -> list[OptionData]:
    """Deduplicate actual positive-price SKU choices across a model's trims."""
    fields = (("exterior", "colors"), ("interior", "interior"), ("wheels", "wheels"))
    options: list[OptionData] = []
    seen: set[tuple[str, str]] = set()
    for version in catalogue:
        if not isinstance(version, dict):
            continue
        version_currency = str(version.get("currency") or fallback_currency)
        for sku in (version.get("skuList") or {}).values():
            if not isinstance(sku, dict):
                continue
            sku_currency = str(sku.get("currency") or version_currency)
            for category, field in fields:
                choice = sku.get(field) or {}
                if not isinstance(choice, dict):
                    continue
                price = _number(choice.get("price"))
                code = str(choice.get("code") or "").strip()
                name = str(choice.get("name") or choice.get("nameInLang") or "").strip()
                if price is None or price <= 0 or not code or not name:
                    continue
                key = (category, code)
                if key in seen:
                    continue
                seen.add(key)
                options.append(OptionData(
                    brand_specific_name=name,
                    price=price,
                    currency=str(choice.get("currency") or sku_currency),
                    category=category,
                    code=code,
                ))
    return options


def _base_price(catalogue: list[dict[str, Any]]) -> float | None:
    """Return the lowest public list price across a model's offered trims."""
    candidates: list[float] = []
    for version in catalogue:
        if not isinstance(version, dict):
            continue
        price = _number(version.get("productPrice"))
        if price and price > 0:
            candidates.append(price)
        # Some market records omit productPrice but provide exact SKU prices.
        for sku in (version.get("skuList") or {}).values():
            if isinstance(sku, dict):
                price = _number(sku.get("price"))
                if price and price > 0:
                    candidates.append(price)
    return min(candidates) if candidates else None


@BrandRegistry.register
class BYDCrawler(BrandCrawler):
    brand = "BYD"
    base_url = "https://www.byd.com"
    configurator_url = "https://www.byd.com/de/konfiguration/atto-2"
    SUPPORTED_MARKETS = BYD_SUPPORTED_MARKETS

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.95,
            notes=(
                "Direct public BYD CMS configuration catalogue: "
                "GET cms-api.byd.com/car/byd/{market}/{modelCode}.json; "
                "priced choices from skuList colours/interior/wheels. "
                f"Market: {self.market}."
            ),
        )

    def _catalogue_url(self, model_code: str) -> str:
        return f"{CMS_API}/{MARKET_ROUTES[self.market]}/{model_code}.json"

    def _fetch_catalogue(self, model_code: str) -> list[dict[str, Any]] | None:
        response = requests.get(self._catalogue_url(model_code), headers=HEADERS, timeout=30)
        # A 404 is BYD's public signal that the model is not sold in this market.
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("catalogue response is not a list")
        return [row for row in payload if isinstance(row, dict)]

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        try:
            return await retry_with_backoff(self._crawl_inner, config, max_retries=1, base_delay=2.0)
        except Exception as exc:
            return CrawlResult(brand=self.brand, market=self.market, errors=[f"All attempts failed: {exc}"])

    async def _crawl_inner(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        vehicles: list[VehicleData] = []
        errors: list[str] = []
        for model, slug, model_code, fuel_type in MODELS:
            try:
                catalogue = await asyncio.to_thread(self._fetch_catalogue, model_code)
                if catalogue is None:
                    logger.info("BYD [%s]: %s is not offered (404)", self.market, model)
                    continue
                # The API is the source of truth and doubles as a stable detail URL.
                vehicles.append(VehicleData(
                    brand=self.brand,
                    model=model,
                    base_price=_base_price(catalogue),
                    currency=self.currency,
                    market=self.market,
                    fuel_type=fuel_type,
                    available_options=_options_from_catalogue(catalogue, self.currency),
                    url=self._catalogue_url(model_code),
                ))
            except Exception as exc:
                logger.warning("BYD [%s] %s failed: %s", self.market, model, exc)
                errors.append(f"{model}: {exc}")
            await asyncio.sleep(cfg.rate_limit_seconds)

        priced = sum(len(v.available_options) for v in vehicles)
        if not vehicles:
            errors.append(f"No BYD vehicles returned for market {self.market}")
        elif not priced:
            errors.append(f"No priced BYD options returned for market {self.market}")
        logger.info("BYD [%s]: %d vehicles, %d priced options", self.market, len(vehicles), priced)
        return CrawlResult(
            brand=self.brand,
            market=self.market,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )
