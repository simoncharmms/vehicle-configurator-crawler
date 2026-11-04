"""Offline checks for Range Rover market routing and parser contracts."""

from __future__ import annotations

import pytest

from crawler.brands.range_rover import (
    RANGE_ROVER_SUPPORTED_MARKETS,
    RangeRoverCrawler,
    _extract_range_rover_entries,
    _parse_range_rover_price,
)


def test_range_rover_supported_markets_and_urls() -> None:
    assert RANGE_ROVER_SUPPORTED_MARKETS == ("DE", "US")
    assert RangeRoverCrawler("DE").models_url == "https://www.rangerover.com/de-de/index.html"
    assert RangeRoverCrawler("US").models_url == "https://www.rangerover.com/en-us/index.html"


def test_range_rover_rejects_unsupported_market() -> None:
    with pytest.raises(ValueError, match="Range Rover"):
        RangeRoverCrawler("CH")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$113,300*", 113300.0),
        ("Ab € 146.100^", 146100.0),
        ("Price from: $83,700", 83700.0),
    ],
)
def test_range_rover_parses_market_price_formats(raw: str, expected: float) -> None:
    assert _parse_range_rover_price(raw) == expected


def test_range_rover_extracts_us_entries() -> None:
    text = (
        "RANGE ROVER Price from: $113,300* "
        "RANGE ROVER SPORT Price from: $83,700* "
        "RANGE ROVER VELAR Price from: $62,000*"
    )
    assert _extract_range_rover_entries(text, "US") == [
        ("Range Rover", 113300.0),
        ("Range Rover Sport", 83700.0),
        ("Range Rover Velar", 62000.0),
    ]


def test_range_rover_extracts_de_entries() -> None:
    text = (
        "RANGE ROVER Ab € 146.100^ "
        "RANGE ROVER SPORT Ab € 93.500^ "
        "RANGE ROVER EVOQUE Ab € 50.900^"
    )
    assert _extract_range_rover_entries(text, "DE") == [
        ("Range Rover", 146100.0),
        ("Range Rover Sport", 93500.0),
        ("Range Rover Evoque", 50900.0),
    ]
