"""Base engine interface for crawl engines."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

from crawler.base import CrawlConfig, VehicleData


class BaseEngine(ABC):
    """Abstract base for crawl engines (Playwright / BeautifulSoup)."""

    @abstractmethod
    async def extract_vehicles(
        self, url: str, config: CrawlConfig
    ) -> list[VehicleData]:
        """Navigate to URL and extract vehicle data using the given config."""
        ...

    @abstractmethod
    async def get_page_html(self, url: str, config: CrawlConfig) -> str:
        """Fetch the full rendered HTML of a page."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up resources."""
        ...

    @staticmethod
    def parse_price(text: str) -> float | None:
        """Extract a numeric price from text like '€ 42.350,00' or '42350'."""
        if not text:
            return None
        # Remove currency symbols, whitespace, non-breaking spaces
        cleaned = re.sub(r'[€$£\s\u00a0\u202f]', '', text.strip())
        # Handle German format: 42.350,00 → 42350.00
        if ',' in cleaned and '.' in cleaned:
            cleaned = cleaned.replace('.', '').replace(',', '.')
        elif ',' in cleaned:
            cleaned = cleaned.replace(',', '.')
        elif '.' in cleaned:
            # Dots only: check if it's a thousands separator (e.g. 42.350)
            # If dot is followed by exactly 3 digits (and optionally more dot-groups), it's thousands
            if re.match(r'^\d{1,3}(\.\d{3})+$', re.sub(r'[^\d.]', '', cleaned)):
                cleaned = cleaned.replace('.', '')
        # Remove any remaining non-numeric chars except dots
        cleaned = re.sub(r'[^\d.]', '', cleaned)
        try:
            return float(cleaned)
        except ValueError:
            return None

    @staticmethod
    def clean_text(text: str) -> str:
        """Normalize whitespace in extracted text."""
        if not text:
            return ""
        return re.sub(r'\s+', ' ', text).strip()
