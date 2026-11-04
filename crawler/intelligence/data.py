"""Canonical dated price panel and strict aggregate demand interchange."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from crawler.base import MARKET_CURRENCY


def numeric(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def canonical_date(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date must be YYYY-MM-DD")
    return value


def series_identity(market, brand, model, variant, code, name):
    parts = [market, brand, model, variant, code or "label:" + name]
    return "opt_" + hashlib.sha256(json.dumps(parts, ensure_ascii=True).encode()).hexdigest()[:24]


@dataclass
class PricePanel:
    series: dict = field(default_factory=dict)
    markets: set = field(default_factory=lambda: set(MARKET_CURRENCY))
    diagnostics: list = field(default_factory=list)


def load_price_panel(directory):
    """Use latest successful UTC run/day; reject any errors or explicit partial status.

    Conflicting duplicate identities are quarantined for that day, never averaged.
    Missing days remain absent. Historical variant keys cannot prove generation identity.
    """
    panel = PricePanel()
    runs = {}
    for path in sorted(Path(directory).glob("*.json")):
        payload = json.loads(path.read_text())
        for position, run in enumerate(payload if isinstance(payload, list) else [payload]):
            if not isinstance(run, dict) or "timestamp" not in run:
                panel.diagnostics.append(f"{path.name}: skipped non-run record")
                continue
            market = run.get("market", "DE")
            panel.markets.add(market)
            try:
                stamp = datetime.fromisoformat(run["timestamp"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                stamp = stamp.astimezone(timezone.utc)
            except (ValueError, TypeError):
                panel.diagnostics.append(f"{path.name}: invalid timestamp")
                continue
            vehicles = run.get("vehicles", [])
            if (run.get("errors") or run.get("partial") or run.get("status", "success")
                    not in ("success", "complete") or not vehicles
                    or run.get("vehicle_count", len(vehicles)) != len(vehicles)):
                panel.diagnostics.append(f"{path.name}: skipped failed/partial/empty run")
                continue
            currency = MARKET_CURRENCY.get(market)
            if currency is None or run.get("currency", currency) != currency:
                panel.diagnostics.append(f"{path.name}: mismatched/unknown market currency")
                continue
            key = (market, run.get("brand", ""), stamp.date().isoformat())
            rank = (stamp, path.name, position)
            if key not in runs or rank > runs[key][0]:
                runs[key] = (rank, run)
    for (market, brand, day), (_, run) in sorted(runs.items()):
        currency = MARKET_CURRENCY[market]
        daily = {}
        conflicts = set()
        for vehicle in run["vehicles"]:
            if vehicle.get("currency", currency) != currency or vehicle.get("market", market) != market:
                panel.diagnostics.append(f"{day} {brand}: vehicle currency/market mismatch")
                continue
            model, variant = vehicle.get("model", ""), vehicle.get("variant", "")
            for option in vehicle.get("available_options", []):
                price = option.get("price")
                if not numeric(price) or option.get("currency", currency) != currency:
                    panel.diagnostics.append(f"{day} {brand}: invalid option price/currency")
                    continue
                code = option.get("code", "")
                name = option.get("brand_specific_name", "")
                if not model or not (code or name):
                    panel.diagnostics.append(f"{day} {brand}: missing identity")
                    continue
                identity = series_identity(market, brand, model, variant, code, name)
                base_price = vehicle.get("base_price")
                base_price = base_price if numeric(base_price) and base_price > 0 else None
                entry = dict(id=identity, market=market, currency=currency, brand=brand,
                             model=model, variant=variant, option_code=code, option_name=name,
                             standardized_name=option.get("standardized_name", ""),
                             category=option.get("category", ""),
                             quality_flags=["historical_identity_unverified"] +
                             ([] if code else ["label_identity_fallback"]),
                             date=day, price=price, base_price=base_price)
                if identity in daily and daily[identity] != entry:
                    conflicts.add(identity)
                daily[identity] = entry
        for identity in conflicts:
            daily.pop(identity, None)
            panel.diagnostics.append(f"{day} {identity}: ambiguous identity collision quarantined")
        for identity, entry in daily.items():
            if identity not in panel.series:
                panel.series[identity] = {key: value for key, value in entry.items()
                                          if key not in ("date", "price", "base_price")}
                panel.series[identity]["history"] = []
            panel.series[identity]["history"].append(
                {key: entry[key] for key in ("date", "price", "base_price")})
    return panel


DEMAND_FIELDS = (
    "schema_version", "date", "cohort_id", "series_id", "market", "currency",
    "brand", "model", "variant", "option_code", "option_name", "standardized_name",
    "price", "base_price", "eligible_count", "selected_count", "demand_source",
    "observation_kind", "price_source", "source_id", "quality_flags",
)


class DemandValidationError(ValueError):
    """Actionable row-indexed import failures; nothing is imported on error."""

    def __init__(self, errors):
        self.errors = errors
        super().__init__("Demand import rejected:\n" + "\n".join(errors))


def validate_demand(rows, panel, source):
    if source not in ("synthetic", "observed"):
        raise ValueError("source must be synthetic or observed")
    result, errors, seen = [], [], set()
    catalogues = {identity: {point["date"]: point for point in series["history"]}
                  for identity, series in panel.series.items()}
    for index, row in enumerate(rows, 1):
        try:
            missing = set(DEMAND_FIELDS) - set(row)
            if missing:
                raise ValueError("missing columns: " + ", ".join(sorted(missing)))
            if type(row["schema_version"]) is not int or row["schema_version"] != 1:
                raise ValueError("schema_version must be integer 1")
            canonical_date(row["date"])
            identity = row["series_id"]
            if identity not in panel.series:
                raise ValueError(f"unknown series_id {identity!r}; join exactly to exported series.id")
            series = panel.series[identity]
            for key in ("market", "currency", "brand", "model", "variant", "option_code",
                        "option_name", "standardized_name"):
                if row[key] != series[key]:
                    raise ValueError(f"{key} does not match series mapping")
            if row["demand_source"] != source:
                raise ValueError(f"demand_source must be {source}; sources cannot be mixed")
            if row["observation_kind"] not in ("purchase", "inventory"):
                raise ValueError("observation_kind must be purchase or inventory")
            if row["price_source"] not in ("scraped", "scenario", "observed"):
                raise ValueError("invalid price_source")
            for key in ("eligible_count", "selected_count"):
                if type(row[key]) is not int or row[key] < 0:
                    raise ValueError(f"{key} must be a nonnegative integer (not float/bool)")
            if row["selected_count"] > row["eligible_count"]:
                raise ValueError("selected_count exceeds eligible_count")
            if not numeric(row["price"]):
                raise ValueError("price must be finite and nonnegative")
            if row["base_price"] is not None and (not numeric(row["base_price"]) or row["base_price"] <= 0):
                raise ValueError("base_price must be null or finite and positive")
            if not isinstance(row["quality_flags"], list) or not all(
                    isinstance(flag, str) for flag in row["quality_flags"]):
                raise ValueError("quality_flags must be an array of strings")
            if not all(isinstance(row[key], str) and row[key].strip()
                       for key in ("cohort_id", "source_id")):
                raise ValueError("cohort_id and source_id must be nonempty strings")
            if row["price_source"] == "scraped":
                point = catalogues[identity].get(row["date"])
                if point is None or not math.isclose(row["price"], point["price"], abs_tol=0.005, rel_tol=0):
                    raise ValueError("scraped price/date does not match dated catalogue; use observed for transaction prices")
                if row["base_price"] != point["base_price"]:
                    raise ValueError("base_price does not match dated catalogue")
            duplicate = (row["date"], identity, row["cohort_id"])
            if duplicate in seen:
                raise ValueError("duplicate date+series_id+cohort_id")
            seen.add(duplicate)
            result.append(dict(row))
        except (ValueError, TypeError, KeyError) as error:
            errors.append(f"row {index}: {error}")
    if errors:
        raise DemandValidationError(errors)
    return sorted(result, key=lambda row: (row["series_id"], row["date"], row["cohort_id"]))


def read_demand(path, panel, source="observed"):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    csv_input = path.name.endswith((".csv", ".csv.gz"))
    try:
        with opener(path, "rt", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle)) if csv_input else json.load(handle)
        if not isinstance(rows, list):
            raise ValueError("JSON must contain an array of canonical daily cohort records")
        if csv_input:
            for index, row in enumerate(rows, 1):
                try:
                    for key in ("schema_version", "eligible_count", "selected_count"):
                        value = row.get(key, "")
                        if not value.isascii() or not value.isdecimal():
                            raise ValueError(f"{key} must contain integer digits only")
                        row[key] = int(value)
                    row["price"] = float(row.get("price", ""))
                    row["base_price"] = float(row["base_price"]) if row.get("base_price") else None
                    row["quality_flags"] = json.loads(row.get("quality_flags", "[]"))
                except (ValueError, TypeError) as error:
                    raise DemandValidationError([f"row {index}: {error}"]) from error
        return validate_demand(rows, panel, source)
    except DemandValidationError:
        raise
    except (ValueError, TypeError) as error:
        raise DemandValidationError([str(error)]) from error


def write_demand(path, rows):
    """Write canonical CSV with deterministic gzip headers (no individual records)."""
    import io

    path = Path(path)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=DEMAND_FIELDS)
                writer.writeheader()
                for row in rows:
                    output = dict(row)
                    output["quality_flags"] = json.dumps(row["quality_flags"], separators=(",", ":"))
                    writer.writerow(output)