"""Range Rover model crawler using public market landing pages.

Extraction policy:
- Only extract model names and base list prices printed on the public pages.
- Ignore dealer-specific totals and financing disclaimers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.brands.registry import BrandRegistry
from crawler.network import get_random_user_agent, retry_with_backoff

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RangeRoverMarket:
    """Public Range Rover market route metadata."""

    code: str
    home_url: str
    accept_language: str


RANGE_ROVER_MARKETS: dict[str, RangeRoverMarket] = {
    market.code: market
    for market in (
        RangeRoverMarket("DE", "https://www.rangerover.com/de-de/index.html", "de-DE,de;q=0.9,en;q=0.8"),
        RangeRoverMarket("US", "https://www.rangerover.com/en-us/index.html", "en-US,en;q=0.9"),
    )
}
RANGE_ROVER_SUPPORTED_MARKETS: tuple[str, ...] = tuple(RANGE_ROVER_MARKETS)

_MODEL_CANONICAL = {
    "RANGE ROVER": "Range Rover",
    "RANGE ROVER SPORT": "Range Rover Sport",
    "RANGE ROVER VELAR": "Range Rover Velar",
    "RANGE ROVER EVOQUE": "Range Rover Evoque",
}


def get_market(market: str) -> RangeRoverMarket:
    """Look up a supported Range Rover market by ISO code."""
    code = (market or "DE").upper()
    if code not in RANGE_ROVER_MARKETS:
        raise ValueError(
            f"Range Rover market '{market}' not supported. "
            f"Available: {', '.join(RANGE_ROVER_SUPPORTED_MARKETS)}"
        )
    return RANGE_ROVER_MARKETS[code]


def _parse_range_rover_price(raw: str) -> float | None:
    """Parse market-formatted base-price text into float."""
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


def _extract_range_rover_entries(text: str, market: str) -> list[tuple[str, float]]:
    """Extract `(model, base_price)` tuples from market landing-page text."""
    normalized = re.sub(r"\s+", " ", text or "")
    models_alt = r"RANGE ROVER(?: SPORT| VELAR| EVOQUE)?"

    if market.upper() == "US":
        pattern = re.compile(
            rf"(?P<model>{models_alt})\s+Price from:\s*\$(?P<price>[0-9][0-9,\.]*)",
            re.IGNORECASE,
        )
    else:
        pattern = re.compile(
            rf"(?P<model>{models_alt})\s+Ab\s+\u20ac\s*(?P<price>[0-9][0-9\.,]*)",
            re.IGNORECASE,
        )

    rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    for match in pattern.finditer(normalized):
        model_raw = " ".join(match.group("model").upper().split())
        model_name = _MODEL_CANONICAL.get(model_raw)
        if not model_name:
            continue
        if model_name in seen:
            continue

        price = _parse_range_rover_price(match.group("price"))
        if price is None or price <= 0:
            continue

        seen.add(model_name)
        rows.append((model_name, price))

    return rows


@BrandRegistry.register
class RangeRoverCrawler(BrandCrawler):
    brand = "Range Rover"
    base_url = "https://www.rangerover.com"
    configurator_url = "https://www.rangerover.com/de-de/index.html"
    SUPPORTED_MARKETS = RANGE_ROVER_SUPPORTED_MARKETS

    @property
    def market_config(self) -> RangeRoverMarket:
        return get_market(self.market)

    @property
    def models_url(self) -> str:
        return self.market_config.home_url

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=2.5,
            confidence=0.75,
            notes=(
                "Public market landing page extraction (Range Rover model cards "
                "with printed base list prices)."
            ),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": self.market_config.accept_language,
            "Referer": self.base_url,
        }

    def _fetch_home_page(self) -> str:
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

        try:
            html = await asyncio.to_thread(self._fetch_home_page)
            soup = BeautifulSoup(html, "lxml")
            text = soup.get_text(" ", strip=True)
            rows = _extract_range_rover_entries(text, self.market)

            vehicles = [
                VehicleData(
                    brand=self.brand,
                    model=model,
                    base_price=price,
                    currency=self.currency,
                    market=self.market,
                    url=self.models_url,
                )
                for model, price in rows
            ]

            errors: list[str] = []
            if not vehicles:
                errors.append(f"No Range Rover vehicles extracted for market {self.market}")

            return CrawlResult(
                brand=self.brand,
                market=self.market,
                vehicles=vehicles,
                errors=errors,
                strategy_used=cfg,
                duration_seconds=time.time() - start_time,
            )
        except Exception as exc:
            logger.error("Range Rover [%s] crawl failed: %s", self.market, exc)
            return CrawlResult(
                brand=self.brand,
                market=self.market,
                errors=[str(exc)],
                strategy_used=cfg,
                duration_seconds=time.time() - start_time,
            )
