"""BeautifulSoup-based engine for static/server-rendered pages."""

from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup, Tag

from crawler.base import CrawlConfig, VehicleData
from crawler.engines.base_engine import BaseEngine

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class BeautifulSoupEngine(BaseEngine):
    """Crawl engine using requests + BeautifulSoup for static pages."""

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update(HEADERS)

    async def get_page_html(self, url: str, config: CrawlConfig) -> str:
        """Fetch raw HTML (no JS rendering)."""
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.text

    async def extract_vehicles(
        self, url: str, config: CrawlConfig
    ) -> list[VehicleData]:
        """Parse static HTML and extract vehicle data."""
        html = await self.get_page_html(url, config)
        soup = BeautifulSoup(html, "lxml")
        vehicles: list[VehicleData] = []

        card_sel = config.selectors.get("vehicle_card", "")
        if not card_sel:
            logger.warning("No vehicle_card selector for BeautifulSoup")
            return self._generic_extract(soup, url)

        cards = soup.select(card_sel)
        logger.info(f"BS4: found {len(cards)} cards with {card_sel!r}")

        for card in cards:
            vehicle = self._extract_from_card(card, url, config)
            if vehicle and vehicle.model:
                vehicles.append(vehicle)

        return vehicles

    def _extract_from_card(
        self, card: Tag, page_url: str, config: CrawlConfig
    ) -> VehicleData | None:
        selectors = config.selectors

        model = self._select_text(card, selectors.get("model_name", ""))
        if not model:
            # Fallback
            for tag in ["h2", "h3", "h4"]:
                el = card.find(tag)
                if el:
                    model = self.clean_text(el.get_text())
                    if model:
                        break

        price_text = self._select_text(card, selectors.get("price", ""))
        if not price_text:
            for cls in ["price", "cost", "amount"]:
                el = card.find(class_=lambda c: c and cls in c.lower()) if card else None
                if el:
                    price_text = el.get_text()
                    break

        variant = self._select_text(card, selectors.get("variant", ""))
        fuel_type = self._select_text(card, selectors.get("fuel_type", ""))

        image_url = ""
        if sel := selectors.get("image"):
            img = card.select_one(sel)
            if img:
                image_url = img.get("src", "") or img.get("data-src", "")

        link = ""
        if sel := selectors.get("options_link"):
            a = card.select_one(sel)
            if a:
                link = a.get("href", "")

        return VehicleData(
            brand="",
            model=model,
            variant=variant,
            base_price=self.parse_price(price_text),
            fuel_type=fuel_type,
            url=link or page_url,
            image_url=str(image_url),
        )

    def _select_text(self, card: Tag, selector: str) -> str:
        if not selector:
            return ""
        el = card.select_one(selector)
        return self.clean_text(el.get_text()) if el else ""

    def _generic_extract(self, soup: BeautifulSoup, url: str) -> list[VehicleData]:
        """Fallback: find vehicle-like data without specific selectors."""
        vehicles: list[VehicleData] = []
        # Look for structured data (JSON-LD)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                import json
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("@type") in ("Product", "Vehicle", "Car"):
                    vehicles.append(VehicleData(
                        brand=data.get("brand", {}).get("name", "") if isinstance(data.get("brand"), dict) else str(data.get("brand", "")),
                        model=data.get("name", ""),
                        base_price=float(data["offers"]["price"]) if "offers" in data and "price" in data.get("offers", {}) else None,
                        url=url,
                    ))
            except Exception:
                continue
        return vehicles

    async def close(self) -> None:
        self.session.close()
