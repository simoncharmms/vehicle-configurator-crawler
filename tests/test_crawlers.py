"""Tests for the vehicle configurator crawler."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from crawler.base import VehicleData, OptionData, VehicleOption, CrawlConfig, CrawlResult, EngineType
from crawler.engines.base_engine import BaseEngine
from crawler.option_mappings import (
    normalize_option_name,
    get_category,
    get_brand_name,
    get_description,
    list_all_options,
)
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


class TestOptionMappings:
    """Test option name standardization."""

    def test_normalize_exact_match(self):
        assert normalize_option_name("4MATIC") == "allrad"
        assert normalize_option_name("quattro") == "allrad"
        assert normalize_option_name("xDrive") == "allrad"

    def test_normalize_case_insensitive(self):
        assert normalize_option_name("4matic") == "allrad"
        assert normalize_option_name("QUATTRO") == "allrad"

    def test_normalize_substring(self):
        assert normalize_option_name("Burmester Surround-Soundsystem Premium") == "premium_sound"

    def test_normalize_unknown_returns_none(self):
        assert normalize_option_name("Unknown Widget XYZ") is None

    def test_normalize_various_options(self):
        assert normalize_option_name("Lenkradheizung") == "steering_wheel_heating"
        assert normalize_option_name("Head-Up-Display") == "head_up_display"
        assert normalize_option_name("Panoramadach") == "panoramic_roof"
        assert normalize_option_name("DISTRONIC") == "adaptive_cruise_control"

    def test_get_category(self):
        assert get_category("allrad") == "drivetrain"
        assert get_category("premium_sound") == "sound"
        assert get_category("unknown") == "other"

    def test_get_brand_name(self):
        assert get_brand_name("allrad", "Mercedes-Benz") == "4MATIC"
        assert get_brand_name("allrad", "Audi") == "quattro"

    def test_get_description(self):
        assert "all-wheel" in get_description("allrad").lower()
        assert get_description("unknown") == ""

    def test_list_all_options(self):
        options = list_all_options()
        assert len(options) >= 10
        names = {o["standardized_name"] for o in options}
        assert "allrad" in names
        assert "head_up_display" in names


class TestOptionData:
    """Test the OptionData dataclass."""

    def test_to_dict(self):
        o = OptionData(
            standardized_name="allrad",
            brand_specific_name="4MATIC",
            price=1500.0,
            category="drivetrain",
        )
        d = o.to_dict()
        assert d["standardized_name"] == "allrad"
        assert d["brand_specific_name"] == "4MATIC"
        assert d["price"] == 1500.0
        assert d["category"] == "drivetrain"

    def test_to_dict_strips_empty(self):
        o = OptionData(standardized_name="test", brand_specific_name="Test")
        d = o.to_dict()
        assert "code" not in d  # empty string excluded
        assert "price" not in d  # None excluded

    def test_vehicle_option_alias(self):
        """VehicleOption should be an alias for OptionData."""
        assert VehicleOption is OptionData
        o = VehicleOption(standardized_name="x", brand_specific_name="X", price=100)
        assert isinstance(o, OptionData)


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
        assert d["available_options"] == []

    def test_to_dict_with_options(self):
        v = VehicleData(
            brand="Mercedes-Benz",
            model="C-Klasse",
            base_price=42000.0,
            available_options=[
                OptionData(
                    standardized_name="allrad",
                    brand_specific_name="4MATIC",
                    price=1500.0,
                    category="drivetrain",
                ),
                OptionData(
                    standardized_name="head_up_display",
                    brand_specific_name="Head-Up-Display",
                    price=800.0,
                    category="technology",
                ),
            ],
        )
        d = v.to_dict()
        assert len(d["available_options"]) == 2
        assert d["available_options"][0]["standardized_name"] == "allrad"
        assert d["available_options"][1]["price"] == 800.0


class TestCrawlResult:
    """Test crawl result saving and option summary."""

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
            filepath = list(Path(tmpdir).glob("*.json"))[0]
            with open(filepath) as f:
                data = json.load(f)
            assert isinstance(data, list)
            assert len(data) == 2

    def test_option_summary(self):
        result = CrawlResult(
            brand="Mercedes-Benz",
            vehicles=[
                VehicleData(
                    brand="Mercedes-Benz",
                    model="C-Klasse",
                    base_price=42000,
                    available_options=[
                        OptionData(
                            standardized_name="allrad",
                            brand_specific_name="4MATIC",
                            price=1500,
                            category="drivetrain",
                        ),
                        OptionData(
                            standardized_name="head_up_display",
                            brand_specific_name="HUD",
                            price=800,
                            category="technology",
                        ),
                    ],
                ),
                VehicleData(
                    brand="Mercedes-Benz",
                    model="E-Klasse",
                    base_price=52000,
                    available_options=[
                        OptionData(
                            standardized_name="allrad",
                            brand_specific_name="4MATIC",
                            price=1800,
                            category="drivetrain",
                        ),
                    ],
                ),
            ],
        )

        summary = result.option_summary()
        assert len(summary) == 2
        # allrad should be first (2 models > 1 model)
        allrad = summary[0]
        assert allrad["standardized_name"] == "allrad"
        assert allrad["model_count"] == 2
        assert allrad["avg_price"] == 1650.0
        assert allrad["min_price"] == 1500
        assert allrad["max_price"] == 1800

    def test_to_dict_includes_option_summary(self):
        result = CrawlResult(
            brand="TestBrand",
            vehicles=[
                VehicleData(
                    brand="TestBrand",
                    model="M1",
                    available_options=[
                        OptionData(
                            standardized_name="allrad",
                            brand_specific_name="AWD",
                            price=2000,
                            category="drivetrain",
                        ),
                    ],
                ),
            ],
        )
        d = result.to_dict()
        assert "option_summary" in d
        assert len(d["option_summary"]) == 1
        assert d["option_summary"][0]["standardized_name"] == "allrad"


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

@pytest.mark.live
class TestMercedesLive:
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
                opts = len(v.available_options)
                print(f"  {v.model}: €{v.base_price}  ({opts} options)")


@pytest.mark.live
class TestAudiLive:
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
                opts = len(v.available_options)
                print(f"  {v.model}: €{v.base_price}  ({opts} options)")


def pytest_configure(config):
    config.addinivalue_line("markers", "live: mark test as live integration test")
