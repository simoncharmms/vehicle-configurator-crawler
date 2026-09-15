"""Offline contracts for Lexus's verified multi-market pricing API."""

from __future__ import annotations

from bs4 import BeautifulSoup
import pytest

from crawler.brands.lexus import (
    LexusCrawler,
    _extract_model_tokens,
    _texus_option_data,
)


def test_lexus_market_contract() -> None:
    assert LexusCrawler("DE").market == "DE"
    assert LexusCrawler("at").market == "AT"
    assert LexusCrawler.SUPPORTED_MARKETS == ("DE", "AT")
    with pytest.raises(ValueError, match="Lexus"):
        LexusCrawler("CH")


def test_extract_model_tokens_from_hidden_aem_state() -> None:
    soup = BeautifulSoup(
        '<div id="lexus-data">{"modelMap":{"UX":{"internalCode":"ux",'
        '"token":"token-ux"},"LB":{"token":"token-lb"}}}</div>',
        "lxml",
    )
    assert _extract_model_tokens(soup) == {"UX": "token-ux", "LB": "token-lb"}


def test_texus_option_data_keeps_only_published_positive_prices() -> None:
    options = _texus_option_data(
        [
            ("colours", {
                "ExteriorColours": [
                    {"Name": "Norigrün", "Code": "6X4", "Price": 850.0,
                     "PriceInfo": {"Currency": "EUR"}},
                    {"Name": "Serie", "Code": "000", "Price": 0.0},
                ],
            }),
            ("packs", [
                {"Name": "TECHNOLOGIE PAKET", "InternalCode": "PACK1",
                 "Price": 2150.0, "PriceInfo": {"Currency": "EUR"}},
            ]),
            ("accessories", [
                {"Name": "Anhängezugvorrichtung", "InternalCode": "APM592",
                 "Price": 870.0},
            ]),
        ],
        brand="Lexus",
        currency="EUR",
    )
    assert [(o.code, o.price, o.category) for o in options] == [
        ("6X4", 850.0, "exterior"),
        ("PACK1", 2150.0, "packages"),
        ("APM592", 870.0, "accessories"),
    ]
