"""Base data models and interfaces for the crawler framework."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class EngineType(str, Enum):
    PLAYWRIGHT = "playwright"
    BEAUTIFULSOUP = "beautifulsoup"


@dataclass
class VehicleOption:
    """A single configurable option (color, package, accessory, etc.)."""
    name: str
    category: str = ""          # e.g. "Exterior", "Interior", "Packages"
    price: float | None = None  # EUR, None if included
    currency: str = "EUR"
    code: str = ""              # OEM option code if available

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}


@dataclass
class VehicleData:
    """Extracted vehicle configuration data."""
    brand: str
    model: str
    variant: str = ""
    base_price: float | None = None
    currency: str = "EUR"
    fuel_type: str = ""         # "electric", "hybrid", "petrol", "diesel"
    options: list[VehicleOption] = field(default_factory=list)
    url: str = ""
    image_url: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw_data", None)
        d["options"] = [o.to_dict() for o in self.options]
        return d


@dataclass
class CrawlConfig:
    """Configuration for a crawl strategy (from AI analyzer or manual)."""
    engine: EngineType = EngineType.PLAYWRIGHT
    selectors: dict[str, str] = field(default_factory=dict)
    js_triggers: list[str] = field(default_factory=list)
    wait_selector: str = ""
    wait_timeout_ms: int = 30000
    rate_limit_seconds: float = 2.0
    confidence: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["engine"] = self.engine.value
        return d


@dataclass
class CrawlResult:
    """Result of a brand crawl."""
    brand: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    vehicles: list[VehicleData] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    strategy_used: CrawlConfig | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "brand": self.brand,
            "timestamp": self.timestamp,
            "vehicle_count": len(self.vehicles),
            "vehicles": [v.to_dict() for v in self.vehicles],
            "errors": self.errors,
            "strategy": self.strategy_used.to_dict() if self.strategy_used else None,
            "duration_seconds": round(self.duration_seconds, 2),
        }

    def save(self, data_dir: Path) -> Path:
        """Save crawl result as timestamped JSON."""
        data_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"{self.brand.lower()}_{date_str}.json"
        filepath = data_dir / filename

        # Merge with existing file if present (append to daily results)
        existing: list[dict] = []
        if filepath.exists():
            with open(filepath) as f:
                data = json.load(f)
                if isinstance(data, list):
                    existing = data
                else:
                    existing = [data]

        existing.append(self.to_dict())

        with open(filepath, "w") as f:
            json.dump(existing if len(existing) > 1 else existing[0], f, indent=2, ensure_ascii=False)

        return filepath


class BrandCrawler(ABC):
    """Base class for brand-specific crawlers."""

    brand: str = ""
    base_url: str = ""
    configurator_url: str = ""

    @abstractmethod
    async def crawl(self, config: CrawlConfig | None = None) -> CrawlResult:
        """Execute the crawl and return results."""
        ...

    @abstractmethod
    def get_default_config(self) -> CrawlConfig:
        """Return the default crawl configuration for this brand."""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} brand={self.brand}>"
