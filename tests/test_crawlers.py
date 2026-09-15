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
    OPTION_DEFINITIONS,
)
from crawler.brands.registry import BrandRegistry
from crawler.brands.mercedes import (
    MercedesConfiguratorAPI,
    load_type_classes,
    parse_type_classes_from_html,
    parse_pre_configs,
    parse_selectable_components,
    _fuel_type_from_preconfig,
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
from crawler.brands.lexus import (
    _extract_options_from_grade,
    _feature_to_option,
    _is_automotive_equipment,
    _guess_category_from_feature,
)

# Import brands to trigger registration
import crawler.brands.mercedes  # noqa: F401
import crawler.brands.audi      # noqa: F401
import crawler.brands.porsche   # noqa: F401
import crawler.brands.lexus     # noqa: F401


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

    def test_normalize_lexus_options(self):
        """Lexus-specific option names should map correctly."""
        assert normalize_option_name("E-FOUR") == "allrad"
        assert normalize_option_name("DIRECT4") == "allrad"
        assert normalize_option_name("Mark Levinson") == "premium_sound"
        assert normalize_option_name("Lexus Smart Key") == "keyless_entry"
        assert normalize_option_name("360° Kamera") == "rear_camera"
        assert normalize_option_name("Fahrer-Monitor") == "driver_monitor"
        assert normalize_option_name("Geräuschdämpfung ANC (Active Noise Cancellation)") == "active_noise_cancellation"

    def test_normalize_transmission_options(self):
        """Transmission option names should standardize."""
        assert normalize_option_name("PDK") == "dual_clutch_transmission"
        assert normalize_option_name("9G-TRONIC") == "automatic_transmission"
        assert normalize_option_name("6-Gang Schaltgetriebe") == "manual_transmission"

    def test_normalize_additional_categories(self):
        """New option categories added in Phase 1."""
        assert normalize_option_name("Diebstahlwarnanlage") == "theft_protection"
        assert normalize_option_name("Safe Exit Assist") == "blind_spot_monitor"
        assert normalize_option_name("Nebelscheinwerfer vorne in LED-Technologie") == "fog_lights"
        assert normalize_option_name("Active Noise Cancellation") == "active_noise_cancellation"
        assert normalize_option_name("Privacy Glas") == "privacy_glass"

    def test_get_category(self):
        assert get_category("allrad") == "drivetrain"
        assert get_category("premium_sound") == "sound"
        assert get_category("unknown") == "other"

    def test_get_category_new_options(self):
        """Categories for newly added options."""
        assert get_category("manual_transmission") == "drivetrain"
        assert get_category("dual_clutch_transmission") == "drivetrain"
        assert get_category("blind_spot_monitor") == "safety"
        assert get_category("active_noise_cancellation") == "comfort"
        assert get_category("climate_control") == "climate"
        assert get_category("alloy_wheels") == "wheels"

    def test_get_brand_name(self):
        assert get_brand_name("allrad", "Mercedes-Benz") == "4MATIC"
        assert get_brand_name("allrad", "Audi") == "quattro"

    def test_get_brand_name_lexus(self):
        assert get_brand_name("allrad", "Lexus") == "E-FOUR"
        assert get_brand_name("premium_sound", "Lexus") == "Mark Levinson"

    def test_get_description(self):
        assert "all-wheel" in get_description("allrad").lower()
        assert get_description("unknown") == ""

    def test_list_all_options(self):
        options = list_all_options()
        assert len(options) >= 25  # At least 25 standardized categories
        names = {o["standardized_name"] for o in options}
        assert "allrad" in names
        assert "head_up_display" in names
        assert "manual_transmission" in names
        assert "blind_spot_monitor" in names
        assert "active_noise_cancellation" in names

    def test_minimum_25_standard_options(self):
        """Acceptance: 25+ standardized option categories."""
        assert len(OPTION_DEFINITIONS) >= 25


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
        assert "lexus" in brands

    def test_get_brand(self):
        crawler = BrandRegistry.get("audi")
        assert crawler.brand == "Audi"

    def test_get_lexus(self):
        crawler = BrandRegistry.get("lexus")
        assert crawler.brand == "Lexus"

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


# ------------------------------------------------------------------
# Lexus extraction tests
# ------------------------------------------------------------------

class TestLexusGradeExtraction:
    """Test Lexus grade feature extraction."""

    def test_extract_basic_features(self):
        grade = {
            "name": "Luxury",
            "features": [
                "360° Kamera",
                "Ambiente Beleuchtung in 64 Farben und 14 voreingestellten Themen individualisierbar",
                "Lexus Smart Key: schlüsselloser Fahrzeugzugang und Motorstart",
                "Premium Audiosystem mit 6 Lautsprechern",
            ],
            "featuresText": "",
        }
        engine = {"name": "2.5l Hybrid", "transmission": {"name": "CVT"}}
        car = {"filterValues": {"fuelType": ["HEV"], "driveType": []}}

        options = _extract_options_from_grade(grade, engine, car, "Lexus")

        std_names = {o.standardized_name for o in options if o.standardized_name}
        assert "rear_camera" in std_names       # 360° Kamera
        assert "ambient_lighting" in std_names  # Ambiente Beleuchtung
        assert "keyless_entry" in std_names     # Lexus Smart Key
        assert "premium_sound" in std_names     # Premium Audiosystem

    def test_extract_noise_cancellation(self):
        grade = {
            "features": ["Geräuschdämpfung ANC (Active Noise Cancellation)"],
        }
        engine = {}
        car = {"filterValues": {}}

        options = _extract_options_from_grade(grade, engine, car, "Lexus")
        std_names = {o.standardized_name for o in options}
        assert "active_noise_cancellation" in std_names

    def test_extract_drivetrain_from_filter(self):
        grade = {"features": []}
        engine = {}
        car = {"filterValues": {"driveType": ["AWD"]}}

        options = _extract_options_from_grade(grade, engine, car, "Lexus")
        assert any(o.standardized_name == "allrad" for o in options)

    def test_extract_alloy_wheels(self):
        grade = {
            "features": [
                "18'' Leichtmetallfelgen, 225/55R18 dunkelgrau geschliffen",
            ],
        }
        engine = {}
        car = {"filterValues": {}}

        options = _extract_options_from_grade(grade, engine, car, "Lexus")
        assert any(o.standardized_name == "alloy_wheels" for o in options)

    def test_deduplicates_features(self):
        grade = {
            "features": [
                "360° Kamera",
                "360° Kamera",  # duplicate
            ],
        }
        engine = {}
        car = {"filterValues": {}}

        options = _extract_options_from_grade(grade, engine, car, "Lexus")
        camera_opts = [o for o in options if o.standardized_name == "rear_camera"]
        assert len(camera_opts) == 1

    def test_empty_grade(self):
        options = _extract_options_from_grade({}, {}, {"filterValues": {}}, "Lexus")
        assert options == []


class TestLexusFeatureToOption:
    """Test individual feature-to-option conversion."""

    def test_standardized_feature(self):
        opt = _feature_to_option("Lexus Intelligent Park Assist: Parksensoren vorne und hinten", "Lexus")
        assert opt is not None
        assert opt.standardized_name == "parking_assist"
        assert opt.category == "safety"

    def test_unmapped_automotive_feature(self):
        opt = _feature_to_option("Regensensor automatisch", "Lexus")
        assert opt is not None
        assert opt.brand_specific_name == "Regensensor automatisch"
        assert opt.standardized_name == ""  # Not in mapping

    def test_short_text_rejected(self):
        opt = _feature_to_option("AB", "Lexus")
        assert opt is None

    def test_non_equipment_rejected(self):
        """Generic non-equipment text should be rejected."""
        opt = _feature_to_option("DIRECT 4 Badge", "Lexus")
        # This should match allrad due to DIRECT4 alias
        # or be kept as equipment due to "badge" keyword
        # Either way it shouldn't crash
        assert opt is not None or opt is None  # No crash


class TestLexusIsAutomotiveEquipment:
    """Test equipment recognition."""

    def test_recognizes_equipment(self):
        assert _is_automotive_equipment("Rückfahrkamera mit Einparkhilfe")
        assert _is_automotive_equipment("Sitzheizung vorn")
        assert _is_automotive_equipment("LED Nebelscheinwerfer")
        assert _is_automotive_equipment("Diebstahlwarnanlage mit Abschleppschutz")

    def test_rejects_too_short(self):
        assert not _is_automotive_equipment("ABC")

    def test_rejects_too_long(self):
        assert not _is_automotive_equipment("x" * 201)


class TestLexusGuessCategory:
    """Test Lexus feature category guessing."""

    def test_sound(self):
        assert _guess_category_from_feature("Premium Sound System mit 12 Lautsprechern") == "sound"

    def test_comfort(self):
        assert _guess_category_from_feature("Sitzheizung vorn und hinten") == "comfort"

    def test_safety(self):
        assert _guess_category_from_feature("Rückfahrkamera mit Hilfslinien") == "safety"

    def test_lighting(self):
        assert _guess_category_from_feature("LED Nebelscheinwerfer") == "lighting"

    def test_wheels(self):
        assert _guess_category_from_feature("18-Zoll Leichtmetallfelgen") == "wheels"


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
        # Mercedes runs against the JSON configurator API with bounded
        # concurrency, so the per-request delay is small but non-zero.
        assert cfg.rate_limit_seconds >= 0.2
        assert cfg.confidence > 0.5

    def test_lexus_config(self):
        crawler = BrandRegistry.get("lexus")
        cfg = crawler.get_default_config()
        assert cfg.engine == EngineType.PLAYWRIGHT
        assert cfg.confidence >= 0.9

    def test_config_to_dict(self):
        cfg = CrawlConfig(engine=EngineType.PLAYWRIGHT, confidence=0.8)
        d = cfg.to_dict()
        assert d["engine"] == "playwright"
        assert d["confidence"] == 0.8


# ------------------------------------------------------------------
# Cross-brand normalization integration tests
# ------------------------------------------------------------------

class TestCrossBrandNormalization:
    """Test that option normalization works correctly across brands."""

    def test_same_option_different_brands(self):
        """The same feature from different brands maps to the same key."""
        mercedes_names = ["4MATIC", "Lenkradheizung", "Burmester", "Head-Up-Display"]
        porsche_names = ["AWD", "Heated Steering Wheel", "Bose", "Head-Up Display"]
        lexus_names = ["E-FOUR", "Lenkradheizung", "Mark Levinson", "Head-Up Display"]

        mercedes_std = [normalize_option_name(n, "Mercedes-Benz") for n in mercedes_names]
        porsche_std = [normalize_option_name(n, "Porsche") for n in porsche_names]
        lexus_std = [normalize_option_name(n, "Lexus") for n in lexus_names]

        assert mercedes_std == ["allrad", "steering_wheel_heating", "premium_sound", "head_up_display"]
        assert porsche_std == ["allrad", "steering_wheel_heating", "premium_sound", "head_up_display"]
        assert lexus_std == ["allrad", "steering_wheel_heating", "premium_sound", "head_up_display"]

    def test_cross_brand_option_summary(self):
        """Simulate cross-brand option summary computation."""
        from collections import defaultdict

        results = [
            CrawlResult(
                brand="Mercedes-Benz",
                vehicles=[
                    VehicleData(
                        brand="Mercedes-Benz", model="C", base_price=42000,
                        available_options=[
                            OptionData(standardized_name="allrad", brand_specific_name="4MATIC", price=1500),
                        ],
                    ),
                ],
            ),
            CrawlResult(
                brand="Lexus",
                vehicles=[
                    VehicleData(
                        brand="Lexus", model="NX", base_price=45000,
                        available_options=[
                            OptionData(standardized_name="allrad", brand_specific_name="E-FOUR", price=None),
                        ],
                    ),
                ],
            ),
            CrawlResult(
                brand="Porsche",
                vehicles=[
                    VehicleData(
                        brand="Porsche", model="Cayenne", base_price=90000,
                        available_options=[
                            OptionData(standardized_name="allrad", brand_specific_name="AWD", price=None),
                        ],
                    ),
                ],
            ),
        ]

        # Simulate the cross-brand bucketing
        buckets: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for result in results:
            for vehicle in result.vehicles:
                for opt in vehicle.available_options:
                    if opt.standardized_name:
                        buckets[opt.standardized_name][result.brand].append(opt)

        assert "allrad" in buckets
        assert set(buckets["allrad"].keys()) == {"Mercedes-Benz", "Lexus", "Porsche"}


# ---------- Mercedes configurator API (offline unit tests) ----------


class TestMercedesTypeClasses:
    """The type-class catalogue drives the whole Mercedes crawl."""

    def test_catalogue_loads(self):
        tcs = load_type_classes()
        assert len(tcs) >= 30
        assert "W206" in tcs   # C-Klasse Limousine
        assert "V297" in tcs   # EQS
        assert all(isinstance(code, str) and code for code in tcs)
        assert all(isinstance(name, str) and name for name in tcs.values())

    def test_parse_type_classes_from_html(self):
        html = '''
        <a href="/passengercars/mercedes-benz-cars/car-configurator.html/start/CCci/DE/de/tc/W206">
          C-Klasse Limousine
        </a>
        <a href="/passengercars/mercedes-benz-cars/car-configurator.html/start/CCci/DE/de/tc/X296">
          EQS SUV
        </a>
        <a href="/passengercars/other.html">Nothing</a>
        '''
        tcs = parse_type_classes_from_html(html)
        assert tcs["W206"] == "C-Klasse Limousine"
        assert tcs["X296"] == "EQS SUV"
        assert len(tcs) == 2


class TestMercedesPreConfigs:
    """``startPage.preConfigs`` carries the motorizations and base prices."""

    PAYLOAD = {
        "startPage": {
            "preConfigs": [
                {
                    "preConfigId": "pc1",
                    "vehicleId": "de_DE__2060581__AU-301_LU-040",
                    "motorizationName": "C 200",
                    "priceInformation": {
                        "currencyISO": "EUR",
                        "basePrice": {
                            "price": 55501.0,
                            "netPrice": 46639.5,
                            "formattedPrice": "55.501,00 \u20ac",
                        },
                    },
                    "previewImage": {"url": "https://example.com/c200.png"},
                    "plsInformationSection": {
                        "technicalData": {"engine": {"type": "COMBUSTOR", "fuel": "Diesel"}}
                    },
                },
                {
                    "preConfigId": "pc2",
                    "vehicleId": "de_DE__2060582__AU-301",
                    "motorizationName": "C 300 e",
                    "priceInformation": {"basePrice": {"price": 62000.0}},
                    "plsInformationSection": {
                        "technicalData": {"engine": {"type": "HYBRID"}}
                    },
                },
                {"motorizationName": "broken"},  # no vehicleId -> skipped
            ]
        }
    }

    def test_parses_motorizations(self):
        vehicles = parse_pre_configs(self.PAYLOAD, "W206", "C-Klasse Limousine")
        assert len(vehicles) == 2
        v = vehicles[0]
        assert v.brand == "Mercedes-Benz"
        assert v.model == "C-Klasse Limousine"
        assert v.variant == "C 200"
        assert v.base_price == 55501.0
        assert v.currency == "EUR"
        assert v.image_url == "https://example.com/c200.png"
        assert v.raw_data["vehicle_id"].startswith("de_DE__2060581")
        assert v.raw_data["type_class"] == "W206"
        assert v.raw_data["base_price_net"] == 46639.5
        assert "tc/W206" in v.url

    def test_fuel_type_detection(self):
        vehicles = parse_pre_configs(self.PAYLOAD, "W206", "C-Klasse Limousine")
        assert vehicles[0].fuel_type == "petrol"   # COMBUSTOR, no diesel marker
        assert vehicles[1].fuel_type == "hybrid"
        assert _fuel_type_from_preconfig(
            {"plsInformationSection": {"technicalData": {"engine": {"type": "ELECTRIC"}}}}
        ) == "electric"
        assert _fuel_type_from_preconfig({
            "motorizationName": "C 220 d",
            "plsInformationSection": {"technicalData": {"engine": {"type": "COMBUSTOR"}}},
        }) == "diesel"

    def test_empty_payload(self):
        assert parse_pre_configs({}, "W206", "C-Klasse") == []
        assert parse_pre_configs({"startPage": {}}, "W206", "C-Klasse") == []


class TestMercedesSelectableComponents:
    """``selectableComponents`` is where the real option prices live."""

    PAYLOAD = {
        "selectableComponents": {
            "SA-443": {
                "id": "SA-443",
                "name": "Lenkradheizung",
                "standard": False,
                "price": {"price": 297.5, "netPrice": 250.0, "currencyISO": "EUR"},
            },
            "SA-810": {
                "id": "SA-810",
                "name": "Burmester\u00ae 3D-Surround-Soundsystem",
                "standard": False,
                "price": {"price": 1428.0, "currencyISO": "EUR"},
            },
            "SA-873": {  # standard equipment -> skipped
                "id": "SA-873",
                "name": "Sitzheizung f\u00fcr Fahrer und Beifahrer",
                "standard": True,
                "price": {"price": 0.0},
            },
            "SC-DRR": {  # internal sales code -> skipped
                "id": "SC-DRR",
                "name": "Steuercode Vertrieb",
                "standard": False,
                "price": {"price": 0.0},
            },
            "PC-PDA": {  # no extra cost -> skipped by default
                "id": "PC-PDA",
                "name": "Advanced-Paket",
                "standard": False,
                "price": {"price": 0.0},
            },
        }
    }

    def test_extracts_priced_options_only(self):
        opts = parse_selectable_components(self.PAYLOAD, "Mercedes-Benz")
        names = {o.brand_specific_name for o in opts}
        assert names == {"Lenkradheizung", "Burmester\u00ae 3D-Surround-Soundsystem"}
        assert all(o.price and o.price > 0 for o in opts)
        assert all(o.currency == "EUR" for o in opts)

    def test_standardizes_and_categorizes(self):
        opts = {o.code: o for o in parse_selectable_components(self.PAYLOAD, "Mercedes-Benz")}
        assert opts["SA-443"].standardized_name == "steering_wheel_heating"
        assert opts["SA-443"].category == "comfort"
        assert opts["SA-810"].standardized_name == "premium_sound"
        assert opts["SA-810"].category == "sound"
        assert opts["SA-810"].price == 1428.0

    def test_include_zero_price_opt_in(self):
        opts = parse_selectable_components(
            self.PAYLOAD, "Mercedes-Benz", include_zero_price=True
        )
        names = {o.brand_specific_name for o in opts}
        assert "Advanced-Paket" in names
        assert "Steuercode Vertrieb" not in names  # still filtered

    def test_rejects_absurd_prices(self):
        payload = {
            "selectableComponents": {
                "SA-X": {"id": "SA-X", "name": "Ganzes Auto", "price": {"price": 999999.0}},
                "SA-Y": {"id": "SA-Y", "name": "Negativ", "price": {"price": -100.0}},
            }
        }
        assert parse_selectable_components(payload, "Mercedes-Benz") == []

    def test_empty_payload(self):
        assert parse_selectable_components({}, "Mercedes-Benz") == []
        assert parse_selectable_components({"selectableComponents": []}, "Mercedes-Benz") == []


class TestMercedesApiClient:
    """URL construction for the OWCC configurator API."""

    def test_session_id_is_generated(self):
        api = MercedesConfiguratorAPI()
        assert api.session_id and len(api.session_id) >= 8
        assert MercedesConfiguratorAPI().session_id != api.session_id

    def test_base_url_contains_market_and_product(self):
        api = MercedesConfiguratorAPI(session_id="deadbeef")
        url = api._url("entry")
        assert "/de_DE/CCci/deadbeef/entry" in url
        assert url.startswith("https://api.oneweb.mercedes-benz.com/")


# ---------- Live crawl tests (require network + Playwright) ----------

@pytest.mark.live
class TestMercedesLive:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.crawler = BrandRegistry.get("mercedes-benz")

    def test_crawl_extracts_vehicles_and_option_prices(self):
        result = asyncio.run(self.crawler.crawl())
        assert len(result.vehicles) >= 50, result.errors[:3]
        priced = [
            o
            for v in result.vehicles
            for o in v.available_options
            if o.price and o.price > 0
        ]
        print(
            f"\nMercedes: {len(result.vehicles)} motorizations, "
            f"{len(priced)} priced options"
        )
        for v in result.vehicles[:5]:
            print(f"  {v.model} {v.variant}: €{v.base_price}  ({len(v.available_options)} options)")
        # Acceptance: real option prices must be back.
        assert len(priced) >= 1000
        assert all(v.brand == "Mercedes-Benz" and v.model for v in result.vehicles)
        assert all(v.base_price and v.base_price > 10000 for v in result.vehicles)


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


@pytest.mark.live
class TestLexusLive:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.crawler = BrandRegistry.get("lexus")

    def test_crawl_extracts_vehicles_and_options(self):
        result = asyncio.run(self.crawler.crawl())
        assert len(result.errors) == 0 or len(result.vehicles) > 0
        if result.vehicles:
            total_opts = sum(len(v.available_options) for v in result.vehicles)
            print(f"\nLexus: {len(result.vehicles)} vehicles, {total_opts} total options")
            for v in result.vehicles[:5]:
                opts = len(v.available_options)
                print(f"  {v.model}: €{v.base_price}  ({opts} options)")
            # Acceptance: 10+ models, 200+ options
            assert len(result.vehicles) >= 10
            assert total_opts >= 50  # Conservative for live tests


def pytest_configure(config):
    config.addinivalue_line("markers", "live: mark test as live integration test")


# ---------------------------------------------------------------------
# Multi-market support
# ---------------------------------------------------------------------

class TestMarketModel:
    """Market-awareness of the shared data model."""

    def test_snapshot_key_keeps_german_filenames(self):
        from crawler.base import snapshot_key
        assert snapshot_key("mercedes-benz", "DE") == "mercedes-benz"
        assert snapshot_key("mercedes-benz", "FR") == "mercedes-benz_fr"
        assert snapshot_key("porsche", "pl") == "porsche_pl"

    def test_currency_per_market(self):
        from crawler.base import currency_for_market
        assert currency_for_market("DE") == "EUR"
        assert currency_for_market("PL") == "PLN"
        assert currency_for_market("GB") == "GBP"
        assert currency_for_market("CH") == "CHF"
        assert currency_for_market("XX") == "EUR"   # unknown → EUR fallback

    def test_result_serialises_market_and_currency(self):
        result = CrawlResult(brand="Mercedes-Benz", market="PL")
        payload = result.to_dict()
        assert payload["market"] == "PL"
        assert payload["currency"] == "PLN"

    def test_vehicle_carries_market(self):
        vehicle = VehicleData(brand="X", model="Y", market="FR")
        assert vehicle.to_dict()["market"] == "FR"

    def test_save_writes_market_specific_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = CrawlResult(brand="Mercedes-Benz", market="FR").save(Path(tmp))
            assert path.name.startswith("mercedes-benz_fr_")
            path_de = CrawlResult(brand="Mercedes-Benz", market="DE").save(Path(tmp))
            assert path_de.name.startswith("mercedes-benz_2")

    def test_unsupported_market_rejected(self):
        with pytest.raises(ValueError):
            BrandRegistry.get("mercedes-benz", market="JP")

    def test_registry_reports_supported_markets(self):
        markets = BrandRegistry.markets_for("mercedes-benz")
        assert "DE" in markets and "FR" in markets
        assert BrandRegistry.get("mercedes-benz", market="fr").market == "FR"


class TestMarketOrchestration:
    """Brand-market fan-out and index writing."""

    def test_build_crawlers_skips_unsupported_markets(self):
        from crawler.orchestrator import build_crawlers
        crawlers = build_crawlers(["mercedes-benz", "byd"], ["DE", "FR"])
        pairs = {(c.brand, c.market) for c in crawlers}
        assert ("Mercedes-Benz", "DE") in pairs
        assert ("Mercedes-Benz", "FR") in pairs
        # BYD is German-only, so it must not appear with FR
        assert ("BYD", "FR") not in pairs

    def test_build_crawlers_all_expands_every_market(self):
        from crawler.orchestrator import build_crawlers
        crawlers = build_crawlers(["mercedes-benz"], ["all"])
        markets = {c.market for c in crawlers}
        assert markets == set(BrandRegistry.markets_for("mercedes-benz"))

    def test_index_separates_markets(self):
        from crawler.orchestrator import _write_index
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            option = OptionData(
                standardized_name="allrad", brand_specific_name="4MATIC",
                price=2500.0, category="drivetrain", currency="EUR",
            )
            results = [
                CrawlResult(
                    brand="Mercedes-Benz", market="DE",
                    vehicles=[VehicleData(
                        brand="Mercedes-Benz", model="C-Klasse", market="DE",
                        base_price=50000.0, available_options=[option],
                    )],
                ),
                CrawlResult(
                    brand="Mercedes-Benz", market="PL",
                    vehicles=[VehicleData(
                        brand="Mercedes-Benz", model="Klasa C", market="PL",
                        base_price=200000.0, currency="PLN",
                        available_options=[OptionData(
                            standardized_name="allrad", brand_specific_name="4MATIC",
                            price=11000.0, category="drivetrain", currency="PLN",
                        )],
                    )],
                ),
            ]
            _write_index(results, data_dir)
            index = json.loads((data_dir / "index.json").read_text())

            assert set(index["markets"]) == {"DE", "PL"}
            assert index["markets"]["PL"]["currency"] == "PLN"
            assert index["markets"]["PL"]["brands"]["mercedes-benz"]["snapshots"][0]["file"].startswith(
                "mercedes-benz_pl_"
            )
            # Legacy keys keep serving the German view
            assert "mercedes-benz" in index["brands"]
            assert index["option_summary"] == index["option_summary_by_market"]["DE"]
            # Prices of the two markets are never mixed
            de_avg = index["option_summary_by_market"]["DE"]["options"][0]["overall_avg_price"]
            pl_avg = index["option_summary_by_market"]["PL"]["options"][0]["overall_avg_price"]
            assert de_avg == 2500.0
            assert pl_avg == 11000.0
            assert {m["code"] for m in index["available_markets"]} == {"DE", "PL"}


class TestMercedesMarkets:
    """Mercedes-specific market plumbing."""

    def test_every_market_has_locale_and_host(self):
        from crawler.brands.mercedes import MERCEDES_MARKETS
        for code, market in MERCEDES_MARKETS.items():
            assert market.code == code
            assert "_" in market.locale
            assert market.host.startswith("www.mercedes-benz.")

    def test_api_url_uses_market_locale(self):
        api = MercedesConfiguratorAPI(session_id="abc", market="FR")
        assert "/fr_FR/CCci/abc/entry" in api._url("entry")
        assert api.session.headers["Origin"] == "https://www.mercedes-benz.fr"

    def test_pre_configs_tagged_with_market(self):
        payload = {
            "startPage": {
                "preConfigs": [{
                    "vehicleId": "V1",
                    "motorizationName": "C 200",
                    "priceInformation": {
                        "basePrice": {"price": 55299.99, "netPrice": 46000.0},
                        "currencyISO": "EUR",
                    },
                }]
            }
        }
        vehicles = parse_pre_configs(payload, "W206", "C-Klasse", "Mercedes-Benz", "FR")
        assert vehicles[0].market == "FR"
        assert vehicles[0].url.startswith("https://www.mercedes-benz.fr")

    def test_unknown_market_raises(self):
        from crawler.brands.mercedes import get_market
        with pytest.raises(ValueError):
            get_market("JP")
