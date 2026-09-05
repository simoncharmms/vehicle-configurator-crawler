"""Brand registry — discover and instantiate brand crawlers."""

from __future__ import annotations

from typing import Type

from crawler.base import BrandCrawler


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
    def get(cls, brand: str) -> BrandCrawler:
        """Instantiate a registered brand crawler."""
        key = brand.lower()
        if key not in cls._brands:
            available = ", ".join(sorted(cls._brands.keys()))
            raise KeyError(f"Unknown brand '{brand}'. Available: {available}")
        return cls._brands[key]()

    @classmethod
    def list_brands(cls) -> list[str]:
        """List all registered brand names."""
        return sorted(cls._brands.keys())

    @classmethod
    def all(cls) -> list[BrandCrawler]:
        """Instantiate all registered brand crawlers."""
        return [brand_cls() for brand_cls in cls._brands.values()]
