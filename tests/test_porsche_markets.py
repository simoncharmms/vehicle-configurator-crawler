"""Offline checks for Porsche's verified market configuration."""

import pytest
from bs4 import BeautifulSoup

from crawler.brands.porsche import (
    PORSCHE_SUPPORTED_MARKETS,
    PorscheCrawler,
    _extract_porsche_configurator_codes,
    _parse_porsche_price,
)


def test_porsche_supported_markets_and_urls():
    assert PORSCHE_SUPPORTED_MARKETS == (
        "DE", "AT", "CH", "FR", "IT", "ES", "NL", "BE", "PL", "GB", "SE", "NO",
    )
    assert PorscheCrawler("FR").models_url == (
        "https://www.porsche.com/france/models/"
    )
    assert PorscheCrawler("FR").configurator_url == (
        "https://www.porsche.com/france/models/"
    )
    assert PorscheCrawler("CH").models_url == (
        "https://www.porsche.com/swiss/de/models/"
    )
    assert PorscheCrawler("GB").market_config.locale == "en-GB"


def test_porsche_rejects_unverified_market():
    with pytest.raises(ValueError, match="does not support"):
        PorscheCrawler("DK")


def test_porsche_vehicle_records_carry_market_currency_and_url():
    crawler = PorscheCrawler("CH")
    vehicles = crawler._extract_from_page(
        BeautifulSoup("<h3>911 Carrera</h3>", "lxml")
    )
    assert len(vehicles) == 1
    assert vehicles[0].market == "CH"
    assert vehicles[0].currency == "CHF"
    assert vehicles[0].url == "https://www.porsche.com/swiss/de/models/"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("158.700,00 €", 158700.0),
        ("CHF 195'200.00", 195200.0),
        ("£110,105.00", 110105.0),
        ("162 500,00 €", 162500.0),
        ("€ 264.105", 264105.0),
        ("688 000 PLN", 688000.0),
        ("1 670 000 kr", 1670000.0),
    ],
)
def test_porsche_parses_verified_market_price_formats(raw, expected):
    assert _parse_porsche_price(raw) == expected


def test_porsche_extracts_locale_specific_configurator_codes():
    html = (
        "https://configurator.porsche.com/fr-FR/mode/model/982890 "
        "https://configurator.porsche.com/fr-FR/mode/model/9921B2"
    )
    assert _extract_porsche_configurator_codes(html, "fr-FR") == [
        "982890", "9921B2",
    ]
    assert _extract_porsche_configurator_codes(html, "de-DE") == []
