"""Orchestrator aggregation contracts."""

from __future__ import annotations

from crawler.base import CrawlResult, OptionData, VehicleData
from crawler.orchestrator import _compute_comparable_models


def _vehicle(brand: str, model: str, variant: str, price: float, options: list[str]) -> VehicleData:
    return VehicleData(
        brand=brand,
        model=model,
        variant=variant,
        base_price=price,
        market="DE",
        currency="EUR",
        available_options=[
            OptionData(
                standardized_name=opt,
                brand_specific_name=opt,
                category="adas" if "assist" in opt or "cruise" in opt else "comfort",
                price=1000.0,
                currency="EUR",
            )
            for opt in options
        ],
    )


def test_compute_comparable_models_builds_references() -> None:
    results = [
        CrawlResult(
            brand="Mercedes-Benz",
            market="DE",
            vehicles=[
                _vehicle(
                    "Mercedes-Benz",
                    "C-Klasse",
                    "C 200",
                    52000,
                    ["adaptive_cruise_control", "blind_spot_monitor", "leather_seats"],
                )
            ],
        ),
        CrawlResult(
            brand="Audi",
            market="DE",
            vehicles=[
                _vehicle(
                    "Audi",
                    "A4",
                    "40 TFSI",
                    51000,
                    ["adaptive_cruise_control", "blind_spot_monitor", "leather_seats", "metallic_paint"],
                )
            ],
        ),
    ]

    payload = _compute_comparable_models(results)
    rows = payload["vehicles"]

    assert rows
    first = rows[0]
    assert "vehicle_key" in first
    assert "reference_vehicle_key" in first
    assert first["reference_price"] > 0
    assert first["shared_option_count"] >= 1
    assert first["score"] >= 0


def test_compute_comparable_models_prefers_cross_brand_match() -> None:
    results = [
        CrawlResult(
            brand="Mercedes-Benz",
            market="DE",
            vehicles=[
                _vehicle(
                    "Mercedes-Benz",
                    "C-Klasse",
                    "C 200",
                    52000,
                    ["adaptive_cruise_control", "blind_spot_monitor", "leather_seats"],
                ),
                _vehicle(
                    "Mercedes-Benz",
                    "C-Klasse",
                    "C 300",
                    61000,
                    ["adaptive_cruise_control", "blind_spot_monitor", "leather_seats"],
                ),
            ],
        ),
        CrawlResult(
            brand="Audi",
            market="DE",
            vehicles=[
                _vehicle(
                    "Audi",
                    "A4",
                    "40 TFSI",
                    51000,
                    ["adaptive_cruise_control", "blind_spot_monitor", "leather_seats"],
                )
            ],
        ),
    ]

    payload = _compute_comparable_models(results)
    mb_c200 = next(
        row for row in payload["vehicles"]
        if row["vehicle_key"].startswith("Mercedes-Benz|C-Klasse|C 200")
    )

    assert mb_c200["reference_brand"] == "Audi"
