"""Zeekr crawler using the public signed storefront configuration API.

Zeekr's European configuration page publishes the short-lived signing material
used by its own JavaScript client.  The client below reads that runtime
configuration from the public page and reproduces its documented request shape
rather than depending on a browser session.  The relevant calls observed in
Zeekr's configurator are::

    POST /panda/api/c/item/queryCarModels
    GET  /panda/api/c/item/queryOptions?bizId=<item-spu-biz-id>

Both returned model-specific paid option data to an unauthenticated datacentre
HTTP client on 2026-09-15.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
from dataclasses import dataclass
from email.utils import formatdate
from typing import Any
from urllib.parse import quote

import requests

from crawler.base import (
    BrandCrawler, CrawlConfig, CrawlResult, EngineType, OptionData, VehicleData,
)
from crawler.brands.registry import BrandRegistry
from crawler.option_mappings import get_category, normalize_option_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ZeekrMarket:
    """A Zeekr market for which catalogue and paid options were verified."""

    code: str
    locale: str
    language: str


ZEEKR_MARKETS: dict[str, ZeekrMarket] = {
    market.code: market for market in (
        ZeekrMarket("DE", "de-DE", "de"),
        ZeekrMarket("NL", "nl-NL", "nl"),
        ZeekrMarket("SE", "sv-SE", "sv"),
    )
}

# Public storefront link codes.  9X is deliberately excluded: its European
# ordering page exposed reservation/release information but no paid options.
CONFIGURATOR_SERIES: tuple[str, ...] = ("1", "1628", "1075", "1611")
SERIES_NAMES = {"1": "001", "1628": "X", "1075": "7X", "1611": "7GT"}
SHOP_BASE_URL = "https://shop.zeekr.eu"
API_PATH_PREFIX = "/panda/api"


class ZeekrConfiguratorAPI:
    """Request signer matching the public Zeekr configuration frontend."""

    def __init__(self, market: ZeekrMarket, timeout: float = 35.0) -> None:
        self.market = market
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; vehicle-configurator-crawler/1.0)",
            "Accept": "application/json, text/plain, */*",
        })
        self._api_host: str | None = None
        self._access_key: str | None = None
        self._signing_key: str | None = None

    def page_url(self, series: str) -> str:
        return f"{SHOP_BASE_URL}/{self.market.locale}/configuration/?linkCode={series}"

    def _runtime_config(self) -> tuple[str, str, str]:
        """Read the current public frontend signing configuration once."""
        if self._api_host and self._access_key and self._signing_key:
            return self._api_host, self._access_key, self._signing_key
        # DE hosts the shared European application bundle; market is supplied to
        # every API call below, so the bundle route itself does not select data.
        response = self.session.get(
            f"{SHOP_BASE_URL}/de-DE/configuration/?linkCode=1075", timeout=self.timeout,
        )
        response.raise_for_status()
        text = response.text

        def value(name: str) -> str:
            match = re.search(rf"{name}:\\?\"([^\"]+)\"", text)
            if not match:
                raise ValueError(f"Zeekr runtime config missing {name}")
            return match.group(1).replace(r"\u002F", "/")

        api_host = value("VITE_API_HOST").rstrip("/")
        access_key = value("VITE_AK")
        signing_key = value("VITE_SK")
        if not api_host.startswith("https://gateway-pub-azure.zeekr.eu/panda/api"):
            raise ValueError("unexpected Zeekr API host in public runtime config")
        self._api_host, self._access_key, self._signing_key = api_host, access_key, signing_key
        return api_host, access_key, signing_key

    def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        api_host, access_key, signing_key = self._runtime_config()
        params = params or {}
        # The browser uses encodeURIComponent and preserves this insertion order.
        query = "&".join(
            f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
            for key, value in params.items()
        )
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) if payload else ""
        date = formatdate(usegmt=True)
        canonical = f"{method.upper()}\n{API_PATH_PREFIX}{path}\n{query}\n{access_key}\n{date}\n"
        signature = base64.b64encode(
            hmac.new(signing_key.encode(), canonical.encode(), hashlib.sha256).digest()
        ).decode()
        digest = base64.b64encode(
            hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).digest()
        ).decode()
        headers = {
            "X-DATE": date,
            "X-HMAC-ALGORITHM": "hmac-sha256",
            "X-HMAC-ACCESS-KEY": access_key,
            "X-HMAC-SIGNATURE": signature,
            "X-HMAC-DIGEST": digest,
            "country": self.market.code,
            "Accept-Language": self.market.language,
            "Content-Type": "application/json",
            "Origin": SHOP_BASE_URL,
            "Referer": self.page_url("1075"),
        }
        url = f"{api_host}{path}" + (f"?{query}" if query else "")
        response = self.session.request(
            method, url, data=body or None, headers=headers, timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("success") is not True or result.get("data") is None:
            raise ValueError(f"unexpected Zeekr API response: {result.get('message')!r}")
        return result["data"]

    def car_models(self, series: str) -> list[dict[str, Any]]:
        data = self._request("POST", "/c/item/queryCarModels", payload={
            "seriesCode": series,
            "marketCode": "EU",
            "countryCode": self.market.code,
            "multilingual": self.market.language,
        })
        if isinstance(data, list):
            return [model for model in data if isinstance(model, dict)]
        if not isinstance(data, dict):
            return []
        models = data.get("carModelVos") or data.get("itemSpuVos") or []
        return models if isinstance(models, list) else []

    def options(self, biz_id: str) -> dict[str, Any]:
        data = self._request("GET", "/c/item/queryOptions", params={"bizId": biz_id})
        if not isinstance(data, dict):
            raise ValueError("unexpected non-object Zeekr option response")
        return data


def _category(group: dict[str, Any], option: dict[str, Any], standardized: str) -> str:
    """Preserve useful configurator grouping when generic mapping has no hit."""
    mapped = get_category(standardized) if standardized else None
    if mapped:
        return mapped
    text = " ".join(str(value or "") for value in (
        group.get("propertyBizTypeCode"), group.get("propertyBizType"),
        group.get("propertyName"), option.get("propertyBizTypeCode"),
    )).lower()
    if any(word in text for word in ("hub", "wheel", "felgen", "rad")):
        return "wheels"
    if any(word in text for word in ("interior", "innen", "sit", "seat")):
        return "interior"
    if any(word in text for word in ("color", "colour", "lack", "exterior", "paint")):
        return "exterior"
    if any(word in text for word in ("package", "paket", "bundle")):
        return "packages"
    return "accessories"


def _options_from_payload(payload: dict[str, Any], brand: str) -> list[OptionData]:
    options: list[OptionData] = []
    seen: set[str] = set()
    currency = str(payload.get("currencyCode") or "EUR")
    for group in payload.get("configuratorDataVos") or []:
        if not isinstance(group, dict) or group.get("displayToCustomer") != 1:
            continue
        for item in group.get("detailVos") or []:
            if not isinstance(item, dict) or item.get("userVisible") != 1:
                continue
            try:
                price = float(item.get("basePrice"))
            except (TypeError, ValueError):
                continue
            name = str(item.get("propertyName") or "").strip()
            code = str(item.get("propertyCode") or "").strip()
            if not name or not code or price <= 0 or code in seen:
                continue
            seen.add(code)
            standardized = normalize_option_name(name, brand)
            options.append(OptionData(
                standardized_name=standardized or "",
                brand_specific_name=name,
                price=price,
                currency=currency,
                category=_category(group, item, standardized),
                code=code,
            ))
    return options


def _model_value(model: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = model.get(key)
        if value not in (None, ""):
            return value
    return None


@BrandRegistry.register
class ZeekrCrawler(BrandCrawler):
    brand = "Zeekr"
    base_url = "https://www.zeekr.eu"
    configurator_url = f"{SHOP_BASE_URL}/de-DE/configuration/?linkCode=1075"
    SUPPORTED_MARKETS = tuple(ZEEKR_MARKETS)

    @property
    def market_config(self) -> ZeekrMarket:
        return ZEEKR_MARKETS[self.market]

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.95,
            notes=(
                "Direct Zeekr signed public storefront API: queryCarModels → "
                "queryOptions. Current signing configuration is read from the "
                f"public frontend bundle. Market: {self.market}."
            ),
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        started = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []
        api = ZeekrConfiguratorAPI(self.market_config)

        for series in CONFIGURATOR_SERIES:
            try:
                models = await asyncio.to_thread(api.car_models, series)
                if not models:
                    logger.info("Zeekr [%s]: %s is not offered", self.market, series)
                    continue
                for model in models:
                    biz_id = _model_value(model, "itemSpuBizId", "bizId", "spuBizId")
                    if not biz_id:
                        continue
                    option_payload = await asyncio.to_thread(api.options, str(biz_id))
                    options = _options_from_payload(option_payload, self.brand)
                    try:
                        base_price = float(_model_value(model, "basePrice", "msrp", "price"))
                    except (TypeError, ValueError):
                        base_price = None
                    # queryCarModels returns ``modelName`` for the trim, not
                    # for the vehicle series.  The observed public link code
                    # is therefore the reliable series label.
                    model_name = _model_value(model, "carSeriesName", "seriesName") or SERIES_NAMES[series]
                    variant = _model_value(model, "carModelName", "itemSpuName", "modelName", "name")
                    vehicles.append(VehicleData(
                        brand=self.brand,
                        model=f"Zeekr {str(model_name or SERIES_NAMES[series]).strip()}",
                        variant=str(variant or "").strip(),
                        base_price=base_price,
                        currency=str(_model_value(model, "currencyCode", "currency") or self.currency),
                        market=self.market,
                        fuel_type="electric",
                        available_options=options,
                        url=api.page_url(series),
                        raw_data={
                            "series": series,
                            "item_spu_biz_id": str(biz_id),
                        },
                    ))
                await asyncio.sleep(cfg.rate_limit_seconds)
            except Exception as exc:
                logger.warning("Zeekr [%s]: %s failed: %s", self.market, series, exc)
                errors.append(f"{series}: {exc}")

        priced = sum(1 for vehicle in vehicles for option in vehicle.available_options if option.price and option.price > 0)
        if not vehicles:
            errors.append(f"No Zeekr versions returned for market {self.market}")
        elif not priced:
            errors.append(f"No priced Zeekr options returned for market {self.market}")
        return CrawlResult(
            brand=self.brand,
            market=self.market,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - started,
        )
