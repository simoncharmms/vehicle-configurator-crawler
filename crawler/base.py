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
class OptionData:
    """Single option/feature available for a vehicle.

    Carries both the standardized (cross-brand) name and the original
    brand-specific label so the dashboard can display both.
    """
    standardized_name: str = ""       # e.g. "allrad"
    brand_specific_name: str = ""     # e.g. "4MATIC"
    price: float | None = None        # EUR (None if included / unknown)
    category: str = ""                # e.g. "drivetrain", "comfort"
    code: str = ""                    # OEM option code if available
    currency: str = "EUR"

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None and v != ""}


# Backward compatibility alias
VehicleOption = OptionData


@dataclass
class VehicleData:
    """Extracted vehicle configuration data."""
    brand: str
    model: str
    variant: str = ""
    base_price: float | None = None
    currency: str = "EUR"
    fuel_type: str = ""         # "electric", "hybrid", "petrol", "diesel"
    available_options: list[OptionData] = field(default_factory=list)
    url: str = ""
    image_url: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw_data", None)
        d["available_options"] = [o.to_dict() for o in self.available_options]
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

    # --- helpers ---

    def all_options(self) -> list[OptionData]:
        """Flatten available_options from every vehicle in this result."""
        opts: list[OptionData] = []
        for v in self.vehicles:
            opts.extend(v.available_options)
        return opts

    def option_summary(self) -> list[dict[str, Any]]:
        """Compute per-standardized-option stats for this brand result.

        Returns a list of dicts sorted by model_count descending:
            {standardized_name, brand_specific_name, avg_price, min_price,
             max_price, model_count, category}
        """
        from collections import defaultdict
        buckets: dict[str, list[OptionData]] = defaultdict(list)
        for opt in self.all_options():
            if opt.standardized_name:
                buckets[opt.standardized_name].append(opt)

        summary = []
        for std_name, opts in buckets.items():
            prices = [o.price for o in opts if o.price is not None and o.price > 0]
            # Use the most-frequent brand_specific_name
            names = [o.brand_specific_name for o in opts if o.brand_specific_name]
            brand_name = max(set(names), key=names.count) if names else std_name
            summary.append({
                "standardized_name": std_name,
                "brand_specific_name": brand_name,
                "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
                "min_price": min(prices) if prices else None,
                "max_price": max(prices) if prices else None,
                "model_count": len(opts),
                "category": opts[0].category if opts else "",
            })

        summary.sort(key=lambda s: s["model_count"], reverse=True)
        return summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "brand": self.brand,
            "timestamp": self.timestamp,
            "vehicle_count": len(self.vehicles),
            "vehicles": [v.to_dict() for v in self.vehicles],
            "errors": self.errors,
            "strategy": self.strategy_used.to_dict() if self.strategy_used else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "option_summary": self.option_summary(),
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
