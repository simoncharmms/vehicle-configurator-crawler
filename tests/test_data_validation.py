"""Validation contracts for crawl snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest

from crawler.base import (
    DEFAULT_MARKET,
    DataValidationError,
    MARKET_CURRENCY,
    CrawlResult,
    OptionData,
    VehicleData,
)


def _valid_vehicle() -> VehicleData:
    return VehicleData(
        brand="Mercedes-Benz",
        model="C-Klasse",
        variant="C 200",
        base_price=52500.0,
        market=DEFAULT_MARKET,
        currency="EUR",
        available_options=[
            OptionData(
                standardized_name="adaptive_cruise_control",
                brand_specific_name="DISTRONIC",
                price=1250.0,
                category="adas",
                code="SA-239",
                currency="EUR",
            ),
        ],
    )


def test_markets_include_united_states_and_china() -> None:
    assert MARKET_CURRENCY["US"] == "USD"
    assert MARKET_CURRENCY["CN"] == "CNY"


def test_crawl_result_validation_accepts_valid_payload(tmp_path: Path) -> None:
    result = CrawlResult(brand="Mercedes-Benz", vehicles=[_valid_vehicle()])
    assert result.validate() == []

    output = result.save(tmp_path)
    assert output.exists()


def test_crawl_result_validation_rejects_currency_mismatch() -> None:
    vehicle = _valid_vehicle()
    vehicle.currency = "USD"
    result = CrawlResult(brand="Mercedes-Benz", vehicles=[vehicle])

    errors = result.validate()
    assert any("does not match market currency" in error for error in errors)


def test_crawl_result_save_rejects_duplicate_option_identity(tmp_path: Path) -> None:
    vehicle = _valid_vehicle()
    vehicle.available_options.append(
        OptionData(
            standardized_name="adaptive_cruise_control",
            brand_specific_name="DISTRONIC duplicate",
            price=1400.0,
            category="adas",
            code="SA-239",
            currency="EUR",
        )
    )
    result = CrawlResult(brand="Mercedes-Benz", vehicles=[vehicle])

    with pytest.raises(DataValidationError):
        result.save(tmp_path)
