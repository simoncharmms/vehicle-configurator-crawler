#!/usr/bin/env python3
"""Validate crawl snapshot integrity before publication."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.base import MARKET_CURRENCY, currency_for_market


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _load_runs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    raise ValueError("snapshot must be a JSON object or array of objects")


def _iter_snapshot_files(prices_dir: Path, date_filter: str | None) -> list[Path]:
    files: list[Path] = []
    for path in sorted(prices_dir.glob("*.json")):
        if path.name == "index.json":
            continue
        if date_filter and not path.name.endswith(f"_{date_filter}.json"):
            continue
        files.append(path)
    return files


def _validate_run(run: dict[str, Any], source_file: Path, run_index: int) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    brand = run.get("brand")
    market = (run.get("market") or "DE").upper()
    currency = run.get("currency")
    timestamp = run.get("timestamp")
    vehicles = run.get("vehicles")

    prefix = f"{source_file.name} run#{run_index}"

    if not isinstance(brand, str) or not brand.strip():
        errors.append(f"{prefix}: missing/invalid brand")

    if market not in MARKET_CURRENCY:
        errors.append(f"{prefix}: unsupported market '{market}'")

    expected_currency = currency_for_market(market)
    if currency != expected_currency:
        errors.append(
            f"{prefix}: run currency '{currency}' does not match market currency '{expected_currency}'"
        )

    if not isinstance(timestamp, str):
        errors.append(f"{prefix}: missing/invalid timestamp")
    else:
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"{prefix}: invalid ISO timestamp '{timestamp}'")

    if not isinstance(vehicles, list):
        errors.append(f"{prefix}: vehicles must be a list")
        return errors, warnings

    vehicle_count = run.get("vehicle_count", len(vehicles))
    if vehicle_count != len(vehicles):
        errors.append(
            f"{prefix}: vehicle_count={vehicle_count} does not match actual vehicles={len(vehicles)}"
        )

    run_errors = run.get("errors", [])
    if not vehicles and not run_errors:
        warnings.append(f"{prefix}: no vehicles extracted and no crawler errors were reported")

    has_base_price = False
    for i, vehicle in enumerate(vehicles):
        vprefix = f"{prefix} vehicle[{i}]"
        if not isinstance(vehicle, dict):
            errors.append(f"{vprefix}: vehicle must be an object")
            continue

        if not isinstance(vehicle.get("model"), str) or not vehicle.get("model", "").strip():
            errors.append(f"{vprefix}: missing model")

        if vehicle.get("market") != market:
            errors.append(
                f"{vprefix}: market '{vehicle.get('market')}' does not match run market '{market}'"
            )

        if vehicle.get("currency") != expected_currency:
            errors.append(
                f"{vprefix}: currency '{vehicle.get('currency')}' does not match '{expected_currency}'"
            )

        base_price = vehicle.get("base_price")
        if base_price is not None:
            if not _is_number(base_price) or base_price <= 0:
                errors.append(f"{vprefix}: base_price must be finite and > 0 when present")
            else:
                has_base_price = True

        options = vehicle.get("available_options", [])
        if not isinstance(options, list):
            errors.append(f"{vprefix}: available_options must be a list")
            continue

        seen_option_ids: set[str] = set()
        for j, option in enumerate(options):
            oprefix = f"{vprefix} option[{j}]"
            if not isinstance(option, dict):
                errors.append(f"{oprefix}: option must be an object")
                continue

            identity = option.get("code") or option.get("standardized_name") or option.get("brand_specific_name")
            if not isinstance(identity, str) or not identity.strip():
                errors.append(f"{oprefix}: option has no identity (code/standardized_name/brand_specific_name)")
            else:
                identity_key = identity.strip().lower()
                if identity_key in seen_option_ids:
                    errors.append(f"{oprefix}: duplicate option identity '{identity}'")
                seen_option_ids.add(identity_key)

            price = option.get("price")
            if price is not None and (not _is_number(price) or price < 0):
                errors.append(f"{oprefix}: price must be finite and >= 0 when present")

            opt_currency = option.get("currency", expected_currency)
            if opt_currency != expected_currency:
                errors.append(
                    f"{oprefix}: currency '{opt_currency}' does not match market currency '{expected_currency}'"
                )

    if vehicles and not has_base_price and not run_errors:
        warnings.append(f"{prefix}: no vehicle has a positive base list price")

    return errors, warnings


def _validate_index(prices_dir: Path) -> list[str]:
    errors: list[str] = []
    path = prices_dir / "index.json"
    if not path.exists():
        errors.append("index.json is missing")
        return errors

    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"index.json is invalid JSON: {exc}")
        return errors

    if not isinstance(index, dict):
        errors.append("index.json must be a JSON object")
        return errors

    if "markets" not in index or not isinstance(index.get("markets"), dict):
        errors.append("index.json missing object key: markets")
    if "option_summary_by_market" not in index or not isinstance(index.get("option_summary_by_market"), dict):
        errors.append("index.json missing object key: option_summary_by_market")
    if "available_markets" not in index or not isinstance(index.get("available_markets"), list):
        errors.append("index.json missing array key: available_markets")

    comparable = index.get("comparable_models_by_market")
    if comparable is not None and not isinstance(comparable, dict):
        errors.append("index.json key comparable_models_by_market must be an object when present")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prices_dir", type=Path, help="Directory containing crawl JSON snapshots")
    parser.add_argument("--date", help="Validate only snapshots ending with this date (YYYY-MM-DD)")
    parser.add_argument("--check-index", action="store_true", help="Also validate index.json contract keys")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args()

    if args.date and not DATE_RE.match(args.date):
        parser.error("--date must be YYYY-MM-DD")

    prices_dir = args.prices_dir
    if not prices_dir.exists() or not prices_dir.is_dir():
        parser.error(f"prices_dir is not a directory: {prices_dir}")

    files = _iter_snapshot_files(prices_dir, args.date)
    if not files:
        parser.error("No snapshot files found for the requested scope")

    errors: list[str] = []
    warnings: list[str] = []
    run_count = 0

    for file_path in files:
        try:
            runs = _load_runs(file_path)
        except Exception as exc:
            errors.append(f"{file_path.name}: failed to parse snapshot ({exc})")
            continue

        for idx, run in enumerate(runs):
            run_count += 1
            run_errors, run_warnings = _validate_run(run, file_path, idx)
            errors.extend(run_errors)
            warnings.extend(run_warnings)

    if args.check_index:
        errors.extend(_validate_index(prices_dir))

    print(
        f"Validated {len(files)} file(s), {run_count} run(s): "
        f"{len(errors)} error(s), {len(warnings)} warning(s)"
    )

    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}")

    if errors:
        return 1
    if warnings and args.strict:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
