"""Contracts for snapshot validation CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts import validate_crawl_results as validator


def _write_snapshot(path: Path, *, currency: str = "EUR") -> None:
    payload = {
        "brand": "Mercedes-Benz",
        "market": "DE",
        "currency": currency,
        "timestamp": "2026-11-04T06:00:00+00:00",
        "vehicle_count": 1,
        "vehicles": [
            {
                "brand": "Mercedes-Benz",
                "model": "C-Klasse",
                "variant": "C 200",
                "base_price": 52000.0,
                "currency": "EUR",
                "market": "DE",
                "available_options": [
                    {
                        "standardized_name": "adaptive_cruise_control",
                        "brand_specific_name": "DISTRONIC",
                        "price": 1200.0,
                        "category": "adas",
                        "code": "SA-239",
                        "currency": "EUR",
                    }
                ],
            }
        ],
        "errors": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_index(path: Path) -> None:
    index = {
        "markets": {},
        "option_summary_by_market": {},
        "available_markets": [],
    }
    path.write_text(json.dumps(index), encoding="utf-8")


def test_validator_accepts_valid_data(tmp_path: Path, monkeypatch) -> None:
    prices = tmp_path / "prices"
    prices.mkdir()
    _write_snapshot(prices / "mercedes-benz_2026-11-04.json")
    _write_index(prices / "index.json")

    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_crawl_results.py", str(prices), "--date", "2026-11-04", "--check-index"],
    )
    assert validator.main() == 0


def test_validator_rejects_currency_mismatch(tmp_path: Path, monkeypatch) -> None:
    prices = tmp_path / "prices"
    prices.mkdir()
    _write_snapshot(prices / "mercedes-benz_2026-11-04.json", currency="USD")

    monkeypatch.setattr(
        sys,
        "argv",
        ["validate_crawl_results.py", str(prices), "--date", "2026-11-04"],
    )
    assert validator.main() == 1
