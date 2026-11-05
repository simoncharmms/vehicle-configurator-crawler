"""Offline contracts for XPeng pricing normalization behavior."""

from __future__ import annotations

from crawler.brands.xpeng import _coerce_positive_base_price


def test_xpeng_base_price_coercion_prefers_first_positive() -> None:
    assert _coerce_positive_base_price(0, "", None, "51234") == 51234.0


def test_xpeng_base_price_coercion_ignores_non_positive_values() -> None:
    assert _coerce_positive_base_price(0, -1, "0", "-42") is None


def test_xpeng_base_price_coercion_skips_invalid_candidates() -> None:
    assert _coerce_positive_base_price("N/A", {}, [], "34990.5") == 34990.5
