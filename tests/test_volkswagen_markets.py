"""Offline checks for Volkswagen market routing and parsing contracts."""

from __future__ import annotations

from bs4 import BeautifulSoup
import pytest

from crawler.brands.volkswagen import (
    VOLKSWAGEN_SUPPORTED_MARKETS,
    VolkswagenCrawler,
    _extract_cn_model_names,
    _extract_us_models_with_prices,
    _parse_vw_price,
)


def test_volkswagen_supported_markets_and_urls() -> None:
    assert VOLKSWAGEN_SUPPORTED_MARKETS == ("DE", "US", "CN")
    assert VolkswagenCrawler("US").models_url == "https://www.vw.com/en/models.html"
    assert VolkswagenCrawler("CN").models_url == "https://www.vw.com.cn/CarModelList.html"


def test_volkswagen_rejects_unsupported_market() -> None:
    with pytest.raises(ValueError, match="Volkswagen"):
        VolkswagenCrawler("FR")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$41,610", 41610.0),
        ("Ab € 146.100", 146100.0),
        ("59,995.00", 59995.0),
        ("31 205", 31205.0),
    ],
)
def test_volkswagen_parses_market_price_formats(raw: str, expected: float) -> None:
    assert _parse_vw_price(raw) == expected


def test_volkswagen_extracts_us_model_prices_from_text() -> None:
    text = (
        "Atlas MSRP starting at $41,610 "
        "Atlas Cross Sport MSRP starting at $38,300 "
        "SUVs MSRP starting at $999,999 "
        "ID. Buzz MSRP starting at $59,995"
    )
    rows = _extract_us_models_with_prices(text)

    assert rows == [
        ("Atlas", 41610.0),
        ("Atlas Cross Sport", 38300.0),
        ("ID. Buzz", 59995.0),
    ]


def test_volkswagen_extracts_cn_model_names_with_source_fallback() -> None:
    soup = BeautifulSoup(
        """
        <a href="https://www.vw.com.cn/BrandCarModel.html?source=unyx06">Model One NEW</a>
        <a href="https://www.vw.com.cn/BrandCarModel.html?source=idunyx">Learn more</a>
        <a href="https://www.vw.com.cn/BrandCarModel.html?source=idunyx">Learn more</a>
        """,
        "lxml",
    )

    assert _extract_cn_model_names(soup) == ["Model One", "idunyx"]
