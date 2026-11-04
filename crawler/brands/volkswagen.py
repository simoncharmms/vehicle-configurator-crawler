"""Volkswagen model crawler for DE, US and CN public catalogue pages.

Extraction policy:
- Only publish values that are explicitly present on public pages.
- Never infer prices from option bundles or marketing claims.
- CN currently exposes public model names but no stable list-price fields;
  those vehicles are emitted without base prices and with a warning.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.brands.registry import BrandRegistry
from crawler.network import get_random_user_agent, retry_with_backoff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VolkswagenMarket:
    """Public Volkswagen market route metadata."""

    code: str
    models_url: str
    accept_language: str


VOLKSWAGEN_MARKETS: dict[str, VolkswagenMarket] = {
    market.code: market
    for market in (
        VolkswagenMarket("DE", "https://www.volkswagen.de/de/modelle.html", "de-DE,de;q=0.9,en;q=0.8"),
        VolkswagenMarket("US", "https://www.vw.com/en/models.html", "en-US,en;q=0.9"),
        VolkswagenMarket("CN", "https://www.vw.com.cn/CarModelList.html", "zh-CN,zh;q=0.9,en;q=0.6"),
    )
}
VOLKSWAGEN_SUPPORTED_MARKETS: tuple[str, ...] = tuple(VOLKSWAGEN_MARKETS)

_GENERIC_CN_LINK_TEXT = {
    "learn more",
    "more",
    "details",
    "了解更多",
    "了解",
}

_IGNORE_US_HEADINGS = {
    "SUVS",
    "SEDANS",
    "COMPACTS",
    "ELECTRIC VEHICLES",
    "MODELS",
    "PERFORMANCE",
    "ELECTRIC",
}


def get_market(market: str) -> VolkswagenMarket:
    """Look up a supported Volkswagen market by ISO code."""
    code = (market or "DE").upper()
    if code not in VOLKSWAGEN_MARKETS:
        raise ValueError(
            f"Volkswagen market '{market}' not supported. "
            f"Available: {', '.join(VOLKSWAGEN_SUPPORTED_MARKETS)}"
        )
    return VOLKSWAGEN_MARKETS[code]


def _parse_vw_price(raw: str) -> float | None:
    """Parse a market-formatted public list price string into float."""
    token = re.sub(r"[^\d,\.\s]", "", raw or "")
    token = re.sub(r"\s+", "", token)
    if not token:
        return None

    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif "," in token:
        if re.fullmatch(r"\d{1,3}(,\d{3})+", token):
            token = token.replace(",", "")
        else:
            token = token.replace(",", ".")
    elif "." in token and re.fullmatch(r"\d{1,3}(\.\d{3})+", token):
        token = token.replace(".", "")

    try:
        return float(token)
    except ValueError:
        return None


def _extract_us_models_with_prices(text: str) -> list[tuple[str, float]]:
    """Extract `(model, msrp)` tuples from the VW US models page text."""
    normalized = re.sub(r"\s+", " ", text or "")
    pattern = re.compile(
        r"(?P<model>[A-Za-z0-9][A-Za-z0-9\.\- ]{1,40}?)\s+"
        r"MSRP starting at\s+\$(?P<price>[0-9][0-9,\.]*)",
        re.IGNORECASE,
    )

    rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    for match in pattern.finditer(normalized):
        model = " ".join(match.group("model").split())
        if not model or model.upper() in _IGNORE_US_HEADINGS:
            continue
        if model.lower() in seen:
            continue
        price = _parse_vw_price(match.group("price"))
        if price is None or price <= 0:
            continue
        seen.add(model.lower())
        rows.append((model, price))

    return rows


def _extract_de_models_with_prices(text: str) -> list[tuple[str, float]]:
    """Extract `(model, list price)` tuples from the VW Germany page text."""
    normalized = re.sub(r"\s+", " ", text or "")
    pattern = re.compile(
        r"(?P<model>[A-Za-z0-9A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\.\- ]{2,40}?)\s+"
        r"(?:Ab|ab)\s+\u20ac\s*(?P<price>[0-9][0-9\.,]*)"
    )

    rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    for match in pattern.finditer(normalized):
        model = " ".join(match.group("model").split())
        if len(model) < 2 or len(model) > 40:
            continue
        model_key = model.lower()
        if model_key in seen:
            continue
        price = _parse_vw_price(match.group("price"))
        if price is None or price <= 0:
            continue
        seen.add(model_key)
        rows.append((model, price))

    return rows


def _extract_cn_model_names(soup: BeautifulSoup) -> list[str]:
    """Extract model labels from VW CN model links.

    The CN catalogue page currently exposes stable model links but not stable
    list-price fields in the static payload.
    """
    names: list[str] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=re.compile(r"BrandCarModel\.html\?source=")):
        href = str(link.get("href") or "")
        source_match = re.search(r"[?&]source=([^&]+)", href)
        source = unquote(source_match.group(1)).strip() if source_match else ""

        label = " ".join(link.get_text(" ", strip=True).split())
        label = re.sub(r"\s+NEW$", "", label, flags=re.IGNORECASE)
        if not label or label.lower() in _GENERIC_CN_LINK_TEXT:
            label = source

        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(label)

    return names


def _fuel_type_from_model_name(model: str) -> str:
    lower = model.lower()
    if "id." in lower or "electric" in lower or "ev" in lower:
        return "electric"
    if "hybrid" in lower or "phev" in lower:
        return "hybrid"
    return ""


def _vehicles_from_rows(
    brand: str,
    market: str,
    currency: str,
    url: str,
    rows: Iterable[tuple[str, float | None]],
) -> list[VehicleData]:
    vehicles: list[VehicleData] = []
    for model, price in rows:
        vehicles.append(
            VehicleData(
                brand=brand,
                model=model,
                base_price=price,
                currency=currency,
                market=market,
                fuel_type=_fuel_type_from_model_name(model),
                url=url,
            )
        )
    return vehicles


@BrandRegistry.register
class VolkswagenCrawler(BrandCrawler):
    brand = "Volkswagen"
    base_url = "https://www.volkswagen.de"
    configurator_url = "https://www.volkswagen.de/de/modelle.html"
    SUPPORTED_MARKETS = VOLKSWAGEN_SUPPORTED_MARKETS

    @property
    def market_config(self) -> VolkswagenMarket:
        return get_market(self.market)

    @property
    def models_url(self) -> str:
        return self.market_config.models_url

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=2.5,
            confidence=0.7,
            notes=(
                "Public model catalogue extraction (market-specific model list and "
                "public list price snippets where present)."
            ),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": self.market_config.accept_language,
            "Referer": self.base_url,
        }

    def _fetch_models_page(self) -> str:
        response = requests.get(self.models_url, headers=self._headers(), timeout=35)
        response.raise_for_status()
        return response.text

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        try:
            return await retry_with_backoff(self._crawl_inner, config, max_retries=1, base_delay=1.5)
        except Exception as exc:
            return CrawlResult(
                brand=self.brand,
                market=self.market,
                errors=[f"All attempts failed: {exc}"],
            )

    async def _crawl_inner(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []

        try:
            html = await asyncio.to_thread(self._fetch_models_page)
            soup = BeautifulSoup(html, "lxml")
            text = soup.get_text(" ", strip=True)

            if self.market == "US":
                rows = _extract_us_models_with_prices(text)
                vehicles = _vehicles_from_rows(self.brand, self.market, self.currency, self.models_url, rows)
            elif self.market == "DE":
                rows = _extract_de_models_with_prices(text)
                vehicles = _vehicles_from_rows(self.brand, self.market, self.currency, self.models_url, rows)
            else:
                names = _extract_cn_model_names(soup)
                rows = [(name, None) for name in names]
                vehicles = _vehicles_from_rows(self.brand, self.market, self.currency, self.models_url, rows)

            if not vehicles:
                errors.append(f"No Volkswagen vehicles extracted for market {self.market}")
            elif self.market == "CN" and not any(v.base_price for v in vehicles):
                errors.append(
                    "Volkswagen CN public catalogue exposes model names, "
                    "but no stable list prices in static payload."
                )

            return CrawlResult(
                brand=self.brand,
                market=self.market,
                vehicles=vehicles,
                errors=errors,
                strategy_used=cfg,
                duration_seconds=time.time() - start_time,
            )
        except Exception as exc:
            logger.error("Volkswagen [%s] crawl failed: %s", self.market, exc)
            return CrawlResult(
                brand=self.brand,
                market=self.market,
                errors=[str(exc)],
                strategy_used=cfg,
                duration_seconds=time.time() - start_time,
            )
