"""Porsche configurator crawler (fallback brand).

robots.txt:  Could not verify (timed out during initial check).
Strategy:    Static HTML extraction + price lookup from model pages.
             Porsche's model pages embed structured data.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time

from bs4 import BeautifulSoup

from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType, VehicleData
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry

logger = logging.getLogger(__name__)

MODELS_URL = "https://www.porsche.com/germany/models/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _fetch_html(url: str, timeout: int = 30) -> str:
    """Fetch HTML using curl."""
    result = subprocess.run(
        [
            "curl", "-sL", "--compressed", "--http1.1",
            "--max-time", str(timeout),
            "--retry", "2", "--retry-delay", "3",
            "-H", f"User-Agent: {HEADERS['User-Agent']}",
            "-H", f"Accept-Language: {HEADERS['Accept-Language']}",
            "-H", f"Accept: {HEADERS['Accept']}",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl failed (code {result.returncode}): {result.stderr[:200]}")
    if not result.stdout:
        raise RuntimeError("curl returned empty response")
    return result.stdout


@BrandRegistry.register
class PorscheCrawler(BrandCrawler):
    brand = "Porsche"
    base_url = "https://www.porsche.com"
    configurator_url = MODELS_URL

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.BEAUTIFULSOUP,
            rate_limit_seconds=3.0,
            confidence=0.6,
            notes="Porsche: static HTML extraction from model pages.",
        )

    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        cfg = config or self.get_default_config()
        start_time = time.time()
        errors: list[str] = []
        vehicles: list[VehicleData] = []

        try:
            logger.info(f"Porsche: fetching {MODELS_URL}")
            html = _fetch_html(MODELS_URL)
            soup = BeautifulSoup(html, "lxml")

            # Try to extract from page data
            vehicles = self._extract_from_page(soup)
            if vehicles:
                logger.info(f"Porsche: extracted {len(vehicles)} vehicles")
            else:
                errors.append("No vehicles found on Porsche models page")

        except Exception as e:
            logger.error(f"Porsche crawl error: {e}")
            errors.append(str(e))

        return CrawlResult(
            brand=self.brand,
            vehicles=vehicles,
            errors=errors,
            strategy_used=cfg,
            duration_seconds=time.time() - start_time,
        )

    def _extract_from_page(self, soup: BeautifulSoup) -> list[VehicleData]:
        """Extract vehicles from the Porsche models page."""
        vehicles: list[VehicleData] = []

        # Check JSON-LD structured data
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") in ("Product", "Vehicle", "Car"):
                        name = item.get("name", "")
                        price = None
                        offers = item.get("offers", {})
                        if isinstance(offers, dict) and "price" in offers:
                            price = BaseEngine.parse_price(str(offers["price"]))
                        if name:
                            vehicles.append(VehicleData(
                                brand=self.brand,
                                model=name,
                                base_price=price,
                                url=MODELS_URL,
                            ))
            except (json.JSONDecodeError, TypeError):
                continue

        # Look for model links and embedded data
        if not vehicles:
            # Find all model-related links
            seen: set[str] = set()
            for a in soup.find_all("a", href=re.compile(r"/models?/|/configurator")):
                text = a.get_text(strip=True)
                if text and len(text) < 60 and text not in seen:
                    seen.add(text)
                    href = a.get("href", "")
                    vehicles.append(VehicleData(
                        brand=self.brand,
                        model=text,
                        url=f"{self.base_url}{href}" if not href.startswith("http") else href,
                    ))

        # Look for embedded script data
        if not vehicles:
            for script in soup.find_all("script"):
                if not script.string:
                    continue
                text = script.string
                if "model" in text.lower() and ("price" in text.lower() or "name" in text.lower()):
                    # Try to find JSON data
                    json_matches = re.findall(r'({[^}]*"(?:name|model)"[^}]*})', text)
                    for jm in json_matches:
                        try:
                            data = json.loads(jm)
                            name = data.get("name", data.get("model", ""))
                            price = data.get("price", data.get("basePrice", None))
                            if isinstance(price, str):
                                price = BaseEngine.parse_price(price)
                            if name:
                                vehicles.append(VehicleData(
                                    brand=self.brand,
                                    model=name,
                                    base_price=float(price) if price else None,
                                    url=MODELS_URL,
                                ))
                        except (json.JSONDecodeError, TypeError):
                            continue

        return vehicles
