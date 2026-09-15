"""Brand registry — discover and instantiate brand crawlers."""

from __future__ import annotations

from typing import Type

from crawler.base import BrandCrawler, DEFAULT_MARKET


class BrandRegistry:
    """Central registry for all brand crawler implementations."""

    _brands: dict[str, Type[BrandCrawler]] = {}

    @classmethod
    def register(cls, brand_cls: Type[BrandCrawler]) -> Type[BrandCrawler]:
        """Register a brand crawler class (use as decorator)."""
        name = brand_cls.brand.lower()
        if not name:
            raise ValueError(f"{brand_cls.__name__} must define a 'brand' attribute")
        cls._brands[name] = brand_cls
        return brand_cls

    @classmethod
    def get(cls, brand: str, market: str = DEFAULT_MARKET) -> BrandCrawler:
        """Instantiate a registered brand crawler for one market."""
        return cls.get_class(brand)(market=market)

    @classmethod
    def get_class(cls, brand: str) -> Type[BrandCrawler]:
        """Look up the crawler class without instantiating it."""
        key = brand.lower()
        if key not in cls._brands:
            available = ", ".join(sorted(cls._brands.keys()))
            raise KeyError(f"Unknown brand '{brand}'. Available: {available}")
        return cls._brands[key]

    @classmethod
    def markets_for(cls, brand: str) -> tuple[str, ...]:
        """Markets this brand crawler can serve."""
        return cls.get_class(brand).SUPPORTED_MARKETS

    @classmethod
    def all_markets(cls) -> list[str]:
        """Every market covered by at least one registered brand."""
        markets: set[str] = set()
        for brand_cls in cls._brands.values():
            markets.update(brand_cls.SUPPORTED_MARKETS)
        return sorted(markets)

    @classmethod
    def list_brands(cls) -> list[str]:
        """List all registered brand names."""
        return sorted(cls._brands.keys())

    @classmethod
    def all(cls, market: str = DEFAULT_MARKET) -> list[BrandCrawler]:
        """Instantiate every brand crawler that serves `market`."""
        return [
            brand_cls(market=market)
            for brand_cls in cls._brands.values()
            if market.upper() in brand_cls.SUPPORTED_MARKETS
        ]
