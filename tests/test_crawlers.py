"""Tests for the vehicle configurator crawler."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

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
from crawler.brands.mercedes import (
    _search_json_for_options,
    _extract_options_from_scripts,
    _extract_options_from_text,
    _extract_equipment_from_ssr,
    _dedupe_options,
    _find_equipment_items,
    _guess_category_from_title,
)
from crawler.brands.porsche import (
    _extract_porsche_mpi_options,
    _extract_porsche_features_from_html,
)

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


class TestMercedesEquipmentExtraction:
    """Test Mercedes ssrData equipment extraction."""

    def _make_ssr_html(self, equipment_items: list[dict]) -> str:
        """Build minimal HTML with ssrData script containing equipment."""
        payload = {
            "payload": {
                "highlights": {
                    "sections": [
                        {"items": equipment_items},
                    ]
                }
            }
        }
        import json
        blob = json.dumps(payload)
        return (
            f'<html><body>'
            f'<script>window.ssrData["abc123"] = {blob};</script>'
            f'</body></html>'
        )

    def test_extract_non_included_equipment(self):
        html = self._make_ssr_html([
            {
                "equipmentId": "SA-443",
                "title": "Lenkradheizung",
                "isIncluded": False,
                "highlight": True,
                "description": "Heated steering wheel.",
            },
            {
                "equipmentId": "SA-873",
                "title": "Sitzheizung für Fahrer und Beifahrer",
                "isIncluded": True,  # standard -> should be skipped
                "highlight": True,
            },
            {
                "equipmentId": "SA-444",
                "title": "Head-up-Display",
                "isIncluded": False,
                "highlight": True,
            },
        ])
        soup = BeautifulSoup(html, "lxml")
        options = _extract_equipment_from_ssr(soup, "Mercedes-Benz")

        assert len(options) == 2  # Only non-included
        names = {o.brand_specific_name for o in options}
        assert "Lenkradheizung" in names
        assert "Head-up-Display" in names
        # Standard equipment should be skipped
        assert "Sitzheizung für Fahrer und Beifahrer" not in names

    def test_extract_normalizes_names(self):
        html = self._make_ssr_html([
            {
                "equipmentId": "SA-810",
                "title": "Burmester® 3D-Surround-Soundsystem",
                "isIncluded": False,
            },
        ])
        soup = BeautifulSoup(html, "lxml")
        options = _extract_equipment_from_ssr(soup, "Mercedes-Benz")

        assert len(options) == 1
        assert options[0].standardized_name == "premium_sound"
        assert options[0].category == "sound"
        assert options[0].code == "SA-810"
        assert options[0].price is None  # Prices not on model pages

    def test_extract_equipment_empty_page(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        options = _extract_equipment_from_ssr(soup, "Mercedes-Benz")
        assert options == []

    def test_extract_equipment_no_equipment_script(self):
        html = '<html><body><script>window.ssrData["abc"] = {"other": true};</script></body></html>'
        soup = BeautifulSoup(html, "lxml")
        options = _extract_equipment_from_ssr(soup, "Mercedes-Benz")
        assert options == []

    def test_extract_equipment_malformed_json(self):
        html = '<html><body><script>window.ssrData["abc"] = {invalid json with equipmentId};</script></body></html>'
        soup = BeautifulSoup(html, "lxml")
        # Should not crash
        options = _extract_equipment_from_ssr(soup, "Mercedes-Benz")
        assert options == []

    def test_extract_equipment_short_title_skipped(self):
        html = self._make_ssr_html([
            {"equipmentId": "SA-001", "title": "AB", "isIncluded": False},
        ])
        soup = BeautifulSoup(html, "lxml")
        options = _extract_equipment_from_ssr(soup, "Mercedes-Benz")
        assert options == []  # Title too short (< 3 chars)


class TestGuessCategory:
    """Test category guessing from equipment titles."""

    def test_sound_category(self):
        assert _guess_category_from_title("Burmester® Soundsystem") == "sound"

    def test_technology_category(self):
        assert _guess_category_from_title("Head-up-Display") == "technology"

    def test_comfort_category(self):
        assert _guess_category_from_title("Sitzheizung vorn") == "comfort"

    def test_safety_category(self):
        assert _guess_category_from_title("Rückfahrkamera") == "safety"

    def test_lighting_category(self):
        assert _guess_category_from_title("LED Scheinwerfer") == "lighting"

    def test_unknown_category(self):
        assert _guess_category_from_title("Something Unknown") == "other"


class TestFindEquipmentItems:
    """Test recursive equipment item finder."""

    def test_find_nested(self):
        data = {
            "sections": [
                {
                    "items": [
                        {"equipmentId": "SA-1", "title": "Option A"},
                        {"equipmentId": "SA-2", "title": "Option B"},
                    ]
                }
            ]
        }
        items = _find_equipment_items(data)
        assert len(items) == 2
        assert items[0]["equipmentId"] == "SA-1"

    def test_empty_data(self):
        assert _find_equipment_items(None) == []
        assert _find_equipment_items({}) == []
        assert _find_equipment_items([]) == []

    def test_depth_limit(self):
        # Build deeply nested data
        data: dict = {"equipmentId": "SA-deep", "title": "Deep"}
        for _ in range(15):
            data = {"nested": data}
        # Depth limit should prevent finding the deeply nested item
        items = _find_equipment_items(data)
        assert len(items) == 0


class TestSearchJsonForOptions:
    """Test generic JSON option search."""

    def test_finds_options_with_name_and_price(self):
        data = {
            "equipment": [
                {"name": "Lenkradheizung", "price": 350},
                {"name": "Panoramadach", "price": 1200},
            ]
        }
        options = _search_json_for_options(data, "Mercedes-Benz")
        assert len(options) >= 2
        names = {o.brand_specific_name for o in options}
        assert "Lenkradheizung" in names
        assert "Panoramadach" in names

    def test_ignores_out_of_range_prices(self):
        data = [
            {"name": "Cheap widget", "price": 5},       # Below 50
            {"name": "Whole car", "price": 50000},       # Above 25000
            {"name": "Valid option", "price": 500},       # In range
        ]
        options = _search_json_for_options(data, "Test")
        assert len(options) == 1
        assert options[0].brand_specific_name == "Valid option"

    def test_none_data(self):
        assert _search_json_for_options(None, "Test") == []

    def test_empty_dict(self):
        assert _search_json_for_options({}, "Test") == []


class TestExtractOptionsFromText:
    """Test regex-based HTML text extraction."""

    def test_finds_price_pattern(self):
        html = '<div>Lenkradheizung ab 350,00 €</div>'
        options = _extract_options_from_text(html, "Mercedes-Benz")
        assert len(options) >= 1
        assert options[0].standardized_name == "steering_wheel_heating"
        assert options[0].price == 350.0

    def test_ignores_unrecognized_names(self):
        html = '<div>Unknown Widget 500,00 €</div>'
        options = _extract_options_from_text(html, "Mercedes-Benz")
        # Unknown names should not produce results
        assert len(options) == 0

    def test_malformed_price(self):
        """Non-parseable or extreme prices should be skipped."""
        html = '<div>Lenkradheizung abc €</div>'
        options = _extract_options_from_text(html, "Mercedes-Benz")
        assert len(options) == 0


class TestDedupeOptions:
    """Test option deduplication."""

    def test_deduplicates_by_standardized_name(self):
        options = [
            OptionData(standardized_name="allrad", brand_specific_name="4MATIC", price=1500),
            OptionData(standardized_name="allrad", brand_specific_name="4MATIC+", price=2000),
        ]
        result = _dedupe_options(options)
        assert len(result) == 1
        assert result[0].brand_specific_name == "4MATIC"  # First wins

    def test_deduplicates_by_brand_name_fallback(self):
        options = [
            OptionData(standardized_name="", brand_specific_name="Custom Option", price=500),
            OptionData(standardized_name="", brand_specific_name="Custom Option", price=600),
        ]
        result = _dedupe_options(options)
        assert len(result) == 1

    def test_keeps_different_options(self):
        options = [
            OptionData(standardized_name="allrad", brand_specific_name="4MATIC", price=1500),
            OptionData(standardized_name="head_up_display", brand_specific_name="HUD", price=800),
        ]
        result = _dedupe_options(options)
        assert len(result) == 2


class TestPorscheMpiExtraction:
    """Test Porsche MPI compare API extraction."""

    def test_extracts_transmission_options(self):
        data = {
            "models": [],
            "technicalData": [
                {
                    "modelType": "992142",
                    "options": [
                        {"id": "250", "name": "Porsche Doppelkupplung (PDK)"},
                        {"id": "251", "name": "Porsche Active Suspension Management"},
                    ],
                },
            ],
        }
        options = _extract_porsche_mpi_options(data, "Porsche")
        assert len(options) >= 1
        names = {o.brand_specific_name for o in options}
        assert "Porsche Active Suspension Management" in names

    def test_extracts_awd_from_model(self):
        data = {
            "models": [
                {
                    "modelType": "992342",
                    "modelName": "911 Carrera 4",
                    "wheelDrive": "All-Wheel Drive",
                },
            ],
            "technicalData": [],
        }
        options = _extract_porsche_mpi_options(data, "Porsche")
        allrad = [o for o in options if o.standardized_name == "allrad"]
        assert len(allrad) == 1

    def test_empty_data(self):
        options = _extract_porsche_mpi_options({"models": [], "technicalData": []}, "Porsche")
        assert options == []


class TestPorscheHtmlExtraction:
    """Test Porsche feature extraction from HTML."""

    def test_finds_known_features(self):
        html = '''
        <html><body>
        <h3>PASM – Porsche Active Suspension Management</h3>
        <p>Bose® Surround Sound System delivers immersive audio.</p>
        <span>Panoramadach für mehr Licht im Innenraum</span>
        <div>Something not a feature</div>
        </body></html>
        '''
        soup = BeautifulSoup(html, "lxml")
        options = _extract_porsche_features_from_html(soup, "Porsche")
        std_names = {o.standardized_name for o in options}
        assert "sport_suspension" in std_names  # PASM
        assert "premium_sound" in std_names      # Bose
        assert "panoramic_roof" in std_names      # Panoramadach

    def test_empty_page(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        options = _extract_porsche_features_from_html(soup, "Porsche")
        assert options == []


class TestGracefulHttpErrors:
    """Test that HTTP 403 and other errors are handled gracefully."""

    def test_crawl_result_with_errors_still_valid(self):
        result = CrawlResult(
            brand="TestBrand",
            vehicles=[
                VehicleData(brand="TestBrand", model="Model A", base_price=40000),
            ],
            errors=["Option extraction partial/failed: HTTP 403"],
        )
        d = result.to_dict()
        assert d["vehicle_count"] == 1
        assert len(d["errors"]) == 1
        assert "403" in d["errors"][0]

    def test_vehicle_with_none_price_options(self):
        """Options with price=None (from model page extraction) are valid."""
        v = VehicleData(
            brand="Mercedes-Benz",
            model="C-Klasse",
            base_price=42000,
            available_options=[
                OptionData(
                    standardized_name="steering_wheel_heating",
                    brand_specific_name="Lenkradheizung",
                    price=None,
                    category="comfort",
                    code="SA-443",
                ),
            ],
        )
        d = v.to_dict()
        assert len(d["available_options"]) == 1
        assert d["available_options"][0]["standardized_name"] == "steering_wheel_heating"
        # price=None should be excluded from to_dict()
        assert "price" not in d["available_options"][0]

    def test_empty_options_after_timeout(self):
        """Vehicles may have empty options after timeouts — that's OK."""
        v = VehicleData(
            brand="Audi",
            model="A4",
            base_price=38000,
            available_options=[],
        )
        d = v.to_dict()
        assert d["available_options"] == []


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
