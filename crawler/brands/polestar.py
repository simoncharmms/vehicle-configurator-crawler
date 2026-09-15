"""Polestar configurator crawler using Polestar's public configuration API.

The website page is server rendered and contains the initial configuration's
``state``, ``change`` and ``partner`` values.  Those values are used in a POST to
``pc-api.polestar.com/.../configuration``.  The response contains the exact
customer-facing choices in ``configuration.features[*].data.featureGroups``;
priced choices have ``unformattedPrice`` in the market currency.

This intentionally does not use the graphical configurator at crawl time.  The
API has been verified to return HTTP 200 from a datacentre IP with requests.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import requests

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, OptionData, VehicleData
from crawler.brands.registry import BrandRegistry
from crawler.network import retry_with_backoff

logger = logging.getLogger(__name__)

API_BASE = "https://pc-api.polestar.com/eu-north-1/car-configurator-back"
# The Swedish API route and prices were independently verified alongside DE.
MARKET_ROUTES = {"DE": "de", "SE": "se"}
POLESTAR_SUPPORTED_MARKETS: tuple[str, ...] = tuple(MARKET_ROUTES)

# Current public configurator inventory.  Polestar 4 is a single Coupé model;
# the old /polestar-4-suv/ marketing URL is no longer a configurator vehicle.
MODELS: tuple[tuple[str, str], ...] = (
    ("Polestar 2", "polestar-2"),
    ("Polestar 3", "polestar-3"),
    ("Polestar 4 Coupé", "polestar-4-coupe"),
    ("Polestar 5", "polestar-5"),
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; vehicle-configurator-crawler/1.0)",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://www.polestar.com",
    "Referer": "https://www.polestar.com/",
    "X-Consumer-Name": "configurator-front",
    "X-Consumer-Version": "192",
}


def _page_value(html: str, key: str) -> str | None:
    """Read one quoted metadata value from Polestar's RSC page payload."""
    match = re.search(rf"(?<![A-Za-z]){re.escape(key)}:\\?\"([^\"\\]+)", html)
    return match.group(1) if match else None


def _option_data(configuration: dict[str, Any], currency: str) -> list[OptionData]:
    """Extract available, non-zero-priced choices from the configuration JSON."""
    options: list[OptionData] = []
    seen: set[tuple[str, str]] = set()
    for section in configuration.get("features", []):
        if not isinstance(section, dict):
            continue
        data = section.get("data") or {}
        category = str(data.get("id") or "options")
        for group in data.get("featureGroups", []):
            if not isinstance(group, dict):
                continue
            for feature in group.get("features", []):
                if not isinstance(feature, dict):
                    continue
                # "Excluded" choices conflict with the currently selected
                # drivetrain/configuration and are not orderable from it.
                if feature.get("selectedState") not in {"Available", "Selected"}:
                    continue
                price = feature.get("unformattedPrice")
                if not isinstance(price, (int, float)) or price <= 0:
                    continue
                name = str(feature.get("name") or "").strip()
                code = str(feature.get("code") or "").strip()
                if not name or not code or (category, code) in seen:
                    continue
                seen.add((category, code))
                options.append(OptionData(
                    brand_specific_name=name,
                    price=float(price),
                    currency=currency,
                    category=category,
                    code=code,
                ))
    return options


def _base_price(configuration: dict[str, Any]) -> float | None:
    """Return the public incl.-VAT 'starting from' price, not delivery total."""
    prices = configuration.get("prices") or {}
    value = ((prices.get("carStartingFrom") or {}).get("priceInclVAT"))
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    # The same value is also exposed in the detailed CPS breakdown.
    value = (((configuration.get("cpsPriceBreakdown") or {}).get("carStartingFrom") or {})
             .get("basicPriceInclVAT") or {}).get("value")
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


@BrandRegistry.register
class PolestarCrawler(BrandCrawler):
    brand = "Polestar"
    base_url = "https://www.polestar.com"
    configurator_url = "https://www.polestar.com/de/configure/polestar-2"
    SUPPORTED_MARKETS = POLESTAR_SUPPORTED_MARKETS

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.95,
            notes=(
                "Direct public Polestar configuration API: RSC page metadata → "
                "POST /car-configurator-back/{market}/{model}/configurator/api/v2/configuration. "
                f"Market: {self.market}."
            ),
        )

    def _configuration_url(self, slug: str) -> str:
        return f"{self.base_url}/{MARKET_ROUTES[self.market]}/configure/{slug}"

    def _fetch_configuration(self, slug: str) -> dict[str, Any]:
        """Fetch page metadata then reproduce the public configuration request."""
        page_url = self._configuration_url(slug)
        page = requests.get(page_url, headers=HEADERS, timeout=30)
        page.raise_for_status()
        state = _page_value(page.text, "state")
        change = _page_value(page.text, "change")
        partner = _page_value(page.text, "partner")
        if not all((state, change, partner)):
            raise ValueError("configuration metadata (state/change/partner) missing")

        api_url = (
            f"{API_BASE}/{MARKET_ROUTES[self.market]}/{slug}/configurator/api/v2/configuration"
        )
        response = requests.post(
            api_url,
            params={
                "change": change,
                "state": state,
                "markdown": "true",
                "partner": partner,
                "excludePrice": "false",
            },
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        configuration = payload.get("configuration")
        if not isinstance(configuration, dict):
            raise ValueError("configuration missing from API response")
        return configuration

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
        for model, slug in MODELS:
            try:
                configuration = await asyncio.to_thread(self._fetch_configuration, slug)
                vehicle = VehicleData(
                    brand=self.brand,
                    model=model,
                    base_price=_base_price(configuration),
                    currency=self.currency,
                    market=self.market,
                    fuel_type="electric",
                    available_options=_option_data(configuration, self.currency),
                    url=self._configuration_url(slug),
                )
                vehicles.append(vehicle)
            except Exception as exc:
                logger.warning("Polestar [%s] %s failed: %s", self.market, model, exc)
                errors.append(f"{model}: {exc}")
            await asyncio.sleep(cfg.rate_limit_seconds)

        priced = sum(len(v.available_options) for v in vehicles)
        if not vehicles:
            errors.append(f"No Polestar vehicles returned for market {self.market}")
        elif not priced:
            errors.append(f"No priced Polestar options returned for market {self.market}")
        logger.info("Polestar [%s]: %d vehicles, %d priced options", self.market, len(vehicles), priced)
        return CrawlResult(
            brand=self.brand,
            market=self.market,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )
