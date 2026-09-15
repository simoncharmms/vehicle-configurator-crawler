"""XPeng configurator crawler using the public store configurator API.

The public ``store.xpeng.com/{locale}/configurator/{series}`` pages embed the
initial configuration as a Next.js RSC payload.  Each version is subsequently
enriched through the browser's own JSON endpoint::

    POST https://store.xpeng.com/api/carSpecificationGroup/list
    {"carVersionId": "2786", "configId": ""}

Both endpoints were verified from an unauthenticated datacentre HTTP client on
2026-09-15.  No rendered browser or session cookie is required in CI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from crawler.base import (
    BrandCrawler, CrawlConfig, CrawlResult, EngineType, OptionData, VehicleData,
)
from crawler.brands.registry import BrandRegistry
from crawler.option_mappings import get_category, normalize_option_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class XPengMarket:
    """A store locale whose models and paid option prices were verified."""

    code: str
    locale: str
    language: str


XPENG_MARKETS: dict[str, XPengMarket] = {
    market.code: market for market in (
        XPengMarket("DE", "de", "de-DE"),
        XPengMarket("AT", "at", "de-AT"),
        XPengMarket("BE", "be", "nl-BE"),
        XPengMarket("DK", "dk", "da-DK"),
        XPengMarket("FR", "fr", "fr-FR"),
        XPengMarket("NL", "nl", "nl-NL"),
        XPengMarket("NO", "no", "no-NO"),
        XPengMarket("SE", "se", "sv-SE"),
    )
}

# URLs are product identifiers exposed by XPeng's German model pages, not
# inferred API paths.  A product unavailable in a market returns no vehicle.
CONFIGURATOR_SERIES: tuple[str, ...] = ("L03", "NEW_G6", "NEW_G9", "P7+", "X9")
STORE_BASE_URL = "https://store.xpeng.com"

# carSpecificationGroupType values observed in the API payload.
GROUP_CATEGORIES = {1: "accessories", 2: "exterior", 3: "interior", 4: "wheels"}


class XPengConfiguratorAPI:
    """Small client for the unauthenticated XPeng configuration endpoints."""

    def __init__(self, market: XPengMarket, timeout: float = 35.0) -> None:
        self.market = market
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; vehicle-configurator-crawler/1.0)",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": STORE_BASE_URL,
            "Referer": f"{STORE_BASE_URL}/{market.locale}/",
            # These are the public storefront's normal request headers.
            "country": market.code,
            "language": market.language,
            "languageCode": market.language,
            "Client-Type": "1",
        })

    def page_url(self, series: str) -> str:
        return f"{STORE_BASE_URL}/{self.market.locale}/configurator/{series}"

    def initial_state(self, series: str) -> dict[str, Any] | None:
        response = self.session.get(self.page_url(series), timeout=self.timeout)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _parse_rsc_state(response.text)

    def option_groups(self, version_id: str | int) -> list[dict[str, Any]]:
        response = self.session.post(
            f"{STORE_BASE_URL}/api/carSpecificationGroup/list",
            json={"carVersionId": str(version_id), "configId": ""},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 2000 or not isinstance(payload.get("data"), list):
            raise ValueError(f"unexpected option response: {payload.get('code')!r}")
        return payload["data"]


def _parse_rsc_state(html: str) -> dict[str, Any] | None:
    """Extract the JSON ``states`` object from a Next.js RSC script.

    ``self.__next_f.push`` passes a JSON-encoded string, so decoding that
    argument first is substantially less fragile than regexing individual
    prices from the HTML.  ``raw_decode`` handles the nested state object.
    """
    for encoded in re.findall(r"self\.__next_f\.push\((\[.*?\])\)</script>", html):
        try:
            event = json.loads(encoded)
        except json.JSONDecodeError:
            continue
        if len(event) < 2 or not isinstance(event[1], str):
            continue
        text = event[1]
        marker = '"states":'
        if marker not in text or "carVersionListData" not in text:
            continue
        try:
            state, _ = json.JSONDecoder().raw_decode(text[text.index(marker) + len(marker):])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(state, dict):
            return state
    return None


def _options_from_groups(groups: list[dict[str, Any]], brand: str) -> list[OptionData]:
    options: list[OptionData] = []
    seen: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            continue
        category = GROUP_CATEGORIES.get(group.get("carSpecificationGroupType"), "accessories")
        currency = str(group.get("currencySign") or "EUR")
        for item in group.get("carSpecificationVoList") or []:
            if not isinstance(item, dict):
                continue
            try:
                price = float(item.get("carSpecificationPrice"))
            except (TypeError, ValueError):
                continue
            name = str(item.get("carSpecificationName") or "").strip()
            # ``carSpecificationCode`` is the manufacturer option identifier;
            # the UUID is only retained as a fallback for incomplete records.
            code = str(item.get("carSpecificationCode") or item.get("id") or "")
            if not name or not code or price <= 0:
                continue
            if code in seen:
                continue
            seen.add(code)
            standardized = normalize_option_name(name, brand)
            options.append(OptionData(
                standardized_name=standardized or "",
                brand_specific_name=name,
                price=price,
                currency=currency,
                category=get_category(standardized) if standardized else category,
                code=code,
            ))
    return options


@BrandRegistry.register
class XPengCrawler(BrandCrawler):
    brand = "XPeng"
    base_url = "https://www.xpeng.com"
    configurator_url = f"{STORE_BASE_URL}/de/configurator/NEW_G6"
    SUPPORTED_MARKETS = tuple(XPENG_MARKETS)

    @property
    def market_config(self) -> XPengMarket:
        return XPENG_MARKETS[self.market]

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.95,
            notes=(
                "Direct XPeng store API: Next.js configurator state (versions/base "
                "prices) → /api/carSpecificationGroup/list (priced options). "
                f"Market: {self.market}."
            ),
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        started = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []
        api = XPengConfiguratorAPI(self.market_config)

        for series in CONFIGURATOR_SERIES:
            try:
                state = await asyncio.to_thread(api.initial_state, series)
                if not state:
                    logger.info("XPeng [%s]: %s is not offered", self.market, series)
                    continue
                versions = state.get("carVersionListData") or []
                if not isinstance(versions, list):
                    raise ValueError("configurator state has no version list")

                for version in versions:
                    if not isinstance(version, dict) or not version.get("id"):
                        continue
                    options = await asyncio.to_thread(api.option_groups, version["id"])
                    parsed_options = _options_from_groups(options, self.brand)
                    price = version.get("msrpAfterDiscount")
                    if price is None:
                        price = version.get("msrpBeforeSubsidy")
                    try:
                        base_price = float(price) if price is not None else None
                    except (TypeError, ValueError):
                        base_price = None
                    model_name = str(version.get("carSeriesName") or series).strip()
                    variant = str(version.get("carVersionName") or "").strip()
                    vehicles.append(VehicleData(
                        brand=self.brand,
                        model=f"XPENG {model_name}",
                        variant=variant,
                        base_price=base_price,
                        currency=str(version.get("currencySign") or self.currency),
                        market=self.market,
                        fuel_type="electric",
                        available_options=parsed_options,
                        url=api.page_url(series),
                        raw_data={
                            "series": series,
                            "car_version_id": str(version["id"]),
                            "car_version_code": version.get("carVersionCode", ""),
                        },
                    ))
                await asyncio.sleep(cfg.rate_limit_seconds)
            except Exception as exc:
                logger.warning("XPeng [%s]: %s failed: %s", self.market, series, exc)
                errors.append(f"{series}: {exc}")

        priced = sum(1 for vehicle in vehicles for option in vehicle.available_options if option.price and option.price > 0)
        if not vehicles:
            errors.append(f"No XPeng versions returned for market {self.market}")
        elif not priced:
            errors.append(f"No priced XPeng options returned for market {self.market}")
        return CrawlResult(
            brand=self.brand,
            market=self.market,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - started,
        )
