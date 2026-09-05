"""Tests for the vehicle configurator crawler."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from crawler.base import VehicleData, VehicleOption, CrawlConfig, CrawlResult, EngineType
from crawler.engines.base_engine import BaseEngine
from crawler.brands.registry import BrandRegistry

# Import brands to trigger registration
import crawler.brands.mercedes  # noqa: F401
import crawler.brands.audi      # noqa: F401
import crawler.brands.porsche   # noqa: F401


class TestBaseEngine:
    """Test the price parser and text cleaner."""

    def test_parse_price_german_format(self):
        assert BaseEngine.parse_price("42.350,00 €") == 42350.00

    def test_parse_price_simple(self):
        assert BaseEngine.parse_price("42350") == 42350.0

    def test_parse_price_euro_prefix(self):
        assert BaseEngine.parse_price("€ 42.350") == 42350.0

    def test_parse_price_with_spaces(self):
        assert BaseEngine.parse_price("42 350,00 €") == 42350.0

    def test_parse_price_comma_decimal(self):
        assert BaseEngine.parse_price("42350,50") == 42350.5

    def test_parse_price_empty(self):
        assert BaseEngine.parse_price("") is None

    def test_parse_price_none(self):
        assert BaseEngine.parse_price(None) is None

    def test_parse_price_ab(self):
        assert BaseEngine.parse_price("ab 35.900,00 €") == 35900.0

    def test_clean_text(self):
        assert BaseEngine.clean_text("  Hello   World  ") == "Hello World"

    def test_clean_text_newlines(self):
        assert BaseEngine.clean_text("Hello\n  World") == "Hello World"


class TestVehicleData:
    """Test data models."""

    def test_to_dict(self):
        v = VehicleData(
            brand="Mercedes-Benz",
            model="A-Klasse",
            variant="A 180",
            base_price=35900.0,
            fuel_type="petrol",
        )
        d = v.to_dict()
        assert d["brand"] == "Mercedes-Benz"
        assert d["model"] == "A-Klasse"
        assert d["base_price"] == 35900.0
        assert "raw_data" not in d

    def test_option_to_dict(self):
        o = VehicleOption(name="AMG Line", category="Packages", price=3500.0)
        d = o.to_dict()
        assert d["name"] == "AMG Line"
        assert d["price"] == 3500.0


class TestCrawlResult:
    """Test crawl result saving."""

    def test_save_json(self):
        result = CrawlResult(
            brand="TestBrand",
            vehicles=[
                VehicleData(brand="TestBrand", model="Model A", base_price=40000),
                VehicleData(brand="TestBrand", model="Model B", base_price=55000),
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = result.save(Path(tmpdir))
            assert filepath.exists()
            with open(filepath) as f:
                data = json.load(f)
            assert data["vehicle_count"] == 2
            assert len(data["vehicles"]) == 2

    def test_save_appends_to_existing(self):
        result1 = CrawlResult(brand="TestBrand", vehicles=[
            VehicleData(brand="TestBrand", model="Model A", base_price=40000),
        ])
        result2 = CrawlResult(brand="TestBrand", vehicles=[
            VehicleData(brand="TestBrand", model="Model B", base_price=50000),
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            result1.save(Path(tmpdir))
            result2.save(Path(tmpdir))
            # Both results should be in the file
            filepath = list(Path(tmpdir).glob("*.json"))[0]
            with open(filepath) as f:
                data = json.load(f)
            assert isinstance(data, list)
            assert len(data) == 2


class TestBrandRegistry:
    """Test brand discovery."""

    def test_list_brands(self):
        brands = BrandRegistry.list_brands()
        assert "audi" in brands
        assert "mercedes-benz" in brands
        assert "porsche" in brands

    def test_get_brand(self):
        crawler = BrandRegistry.get("audi")
        assert crawler.brand == "Audi"

    def test_unknown_brand_raises(self):
        with pytest.raises(KeyError):
            BrandRegistry.get("nonexistent")


class TestCrawlConfig:
    """Test crawl config."""

    def test_default_config(self):
        crawler = BrandRegistry.get("mercedes-benz")
        cfg = crawler.get_default_config()
        assert cfg.engine in (EngineType.PLAYWRIGHT, EngineType.BEAUTIFULSOUP)
        assert cfg.rate_limit_seconds >= 2.0
        assert cfg.confidence > 0.5

    def test_config_to_dict(self):
        cfg = CrawlConfig(engine=EngineType.PLAYWRIGHT, confidence=0.8)
        d = cfg.to_dict()
        assert d["engine"] == "playwright"
        assert d["confidence"] == 0.8


# ---------- Live crawl tests (require network + Playwright) ----------
# Run with: pytest tests/ -m live -v

@pytest.mark.live
class TestMercedesLive:
    """Live integration test for Mercedes crawler."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.crawler = BrandRegistry.get("mercedes-benz")

    def test_crawl_extracts_vehicles(self):
        result = asyncio.run(self.crawler.crawl())
        assert len(result.errors) == 0 or len(result.vehicles) > 0
        if result.vehicles:
            for v in result.vehicles:
                assert v.brand == "Mercedes-Benz"
                assert v.model
            print(f"\nMercedes: {len(result.vehicles)} vehicles extracted")
            for v in result.vehicles[:5]:
                print(f"  {v.model}: €{v.base_price}")


@pytest.mark.live
class TestAudiLive:
    """Live integration test for Audi crawler."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.crawler = BrandRegistry.get("audi")

    def test_crawl_extracts_vehicles(self):
        result = asyncio.run(self.crawler.crawl())
        assert len(result.errors) == 0 or len(result.vehicles) > 0
        if result.vehicles:
            for v in result.vehicles:
                assert v.brand == "Audi"
                assert v.model
            print(f"\nAudi: {len(result.vehicles)} vehicles extracted")
            for v in result.vehicles[:5]:
                print(f"  {v.model}: €{v.base_price}")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: mark test as live integration test")
