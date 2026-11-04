"""Build compact artifacts, publishing a manifest only after complete validation."""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections import defaultdict
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from crawler.base import MARKET_CURRENCY
from .data import DEMAND_FIELDS, load_price_panel, read_demand, validate_demand, write_demand
from .forecast import aggregate_daily, fit_model, predict_model, rolling_validation
from .synthetic import DEFAULT_CONFIG, generate_demand, validate_config


ASSUMPTIONS = [
    "Forecast target is option purchase take rate conditional on exposure, not price or sales volume.",
    "Inventory observations are retained but never fitted or interpreted as purchases.",
    "Latest error-free nonpartial nonempty UTC run per market/brand/day; conflicts quarantined; no missing-day imputation.",
    "Historical model/variant identities are unverified across generations; missing OEM codes use flagged labels.",
    "Future price is held constant unless a scenario change is explicitly supplied; trend is omitted.",
    "Configured sensitivity is an assumption when history/price variation cannot identify it; fitted signs are never flipped.",
    "Intervals are approximate model/source-conditional expected-rate intervals, not predictive purchase counts or causal effects.",
    "Intervals omit serial dependence, selection bias, future context shifts and model misspecification.",
    "Rolling-origin metrics use only past transformations and prices; short history cannot validate a 90-day horizon.",
]


PUBLICATION_LAYOUT = "sharded-gzip-v1"
SUMMARY_FIELDS = (
    "id", "brand", "vehicle_model", "variant", "option_code", "option_name", "standardized_name",
    "category", "currency", "latest_price", "base_price", "take_rate", "eligible_count",
    "selected_count", "price_change_pct", "coverage", "as_of", "warnings",
)
MODEL_FIELDS = (
    "forecast_available", "reference_price", "last_price", "last_date", "coefficients",
    "covariance", "sensitivity_source", "kind", "training_days",
)


def progress(message):
    print(message, file=sys.stderr, flush=True)


@contextmanager
def gzip_text(path):
    with Path(path).open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as handle:
                yield handle


def write_json_gzip(path, payload, limit):
    with gzip_text(path) as handle:
        json.dump(payload, handle, allow_nan=False, separators=(",", ":"))
    if path.stat().st_size > limit:
        raise ValueError(f"compressed publication size limit exceeded: {path.name}")


def read_payload(path):
    opener = gzip.open if Path(path).suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def write_market(stage, source, revision, market, payload):
    if payload.get("schema_version") != 1 or payload.get("market") != market or payload.get("demand_source") != source:
        raise ValueError(f"market/source/schema mismatch: {market}")
    summaries = []
    transform = None
    directory = stage / market
    directory.mkdir()
    for offset in range(0, len(payload["series"]), 50):
        records = payload["series"][offset:offset + 50]
        shard_name = f"{offset // 50:04d}.json.gz"
        detail_path = f"{source}/{revision}/{market}/{shard_name}"
        write_json_gzip(directory / shard_name,
                        dict(schema_version=1, market=market, demand_source=source, series=records),
                        1_000_000)
        for record in records:
            model = record.get("model", {})
            current_transform = model.get("transform")
            if current_transform is not None:
                if transform is not None and transform != current_transform:
                    raise ValueError(f"inconsistent model transforms in {market}")
                transform = current_transform
            summary = {key: record[key] for key in SUMMARY_FIELDS if key in record}
            summary["model"] = {key: model[key] for key in MODEL_FIELDS if key in model}
            summary["detail_path"] = detail_path
            summaries.append(summary)
    compact = {key: value for key, value in payload.items() if key not in ("series", "model_transform")}
    compact.update(publication_layout=PUBLICATION_LAYOUT, model_transform=transform, series=summaries)
    write_json_gzip(stage / f"{market}.json.gz", compact, 5_000_000)


def partition_cohorts(stage, markets, paths, source):
    """Partition CSV cells without type conversion or loading the aggregate export."""
    with ExitStack() as stack:
        writers = {}
        header = None
        for path in paths:
            handle = stack.enter_context(gzip.open(path, "rt", encoding="utf-8", newline=""))
            reader = csv.reader(handle)
            current_header = next(reader)
            if header is None:
                header = current_header
                market_index, source_index = header.index("market"), header.index("demand_source")
                for market in markets:
                    writer = csv.writer(stack.enter_context(gzip_text(stage / f"{market}.csv.gz")))
                    writer.writerow(header)
                    writers[market] = writer
            elif current_header != header:
                raise ValueError("inconsistent cohort headers")
            for row in reader:
                if len(row) != len(header) or row[source_index] != source:
                    raise ValueError("cohort source/column mismatch during repack")
                market = row[market_index]
                if market not in writers:
                    raise ValueError(f"cohort market has no endpoint: {market}")
                writers[market].writerow(row)
        if header is None:
            for market in markets:
                with gzip_text(stage / f"{market}.csv.gz") as handle:
                    csv.writer(handle).writerow(DEMAND_FIELDS)


def publish_artifacts(output_dir, demand_dir, source, payloads, provenance, cohort_writer, manifest=None):
    output_dir, demand_dir = Path(output_dir), Path(demand_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    demand_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    if manifest is None:
        manifest = read_payload(manifest_path) if manifest_path.exists() else dict(schema_version=1, sources={})
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported existing manifest schema")
    revision = uuid.uuid4().hex
    output_target = output_dir / source / revision
    demand_target = demand_dir / source / revision
    output_target.parent.mkdir(parents=True, exist_ok=True)
    demand_target.parent.mkdir(parents=True, exist_ok=True)
    output_stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=output_dir))
    demand_stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=demand_dir))
    committed = False
    try:
        endpoints = {}
        for market, payload in payloads:
            progress(f"Publishing {source}/{market}: {len(payload['series'])} series")
            write_market(output_stage, source, revision, market, payload)
            endpoints[market] = f"{source}/{revision}/{market}.json.gz"
        progress("Partitioning cohort exports")
        cohort_writer(demand_stage, sorted(endpoints))
        write_json_gzip(demand_stage / "provenance.json.gz", provenance, 95_000_000)
        previous_source = manifest["sources"].get(source, {})
        observations_by_market = {**previous_source.get("observations_by_market", {}), **{
            market: os.path.relpath(demand_target / f"{market}.csv.gz", output_dir) for market in endpoints}}
        metadata = dict(previous_source, schema_version=1, demand_source=source,
                        generated_at=provenance.get("generated_at"), publication_layout=PUBLICATION_LAYOUT,
                        markets={**previous_source.get("markets", {}), **endpoints}, revision=revision,
                        observations_by_market=observations_by_market,
                        provenance_path=os.path.relpath(demand_target / "provenance.json.gz", output_dir),
                        diagnostics_count=len(provenance.get("validation_report", {}).get("diagnostics", [])),
                        scope_markets=sorted(endpoints))
        metadata.pop("observations_path", None)
        if len(endpoints) == 1:
            metadata["observations_path"] = observations_by_market[next(iter(endpoints))]
        manifest["sources"][source] = metadata
        manifest.update(generated_at=datetime.now(timezone.utc).isoformat(), contract="option-take-rate-v1")
        (output_stage / "manifest.json").write_text(json.dumps(manifest, allow_nan=False, separators=(",", ":")))
        os.replace(demand_stage, demand_target)
        os.replace(output_stage, output_target)
        os.replace(output_target / "manifest.json", manifest_path)
        committed = True
    finally:
        for path in (output_stage, demand_stage):
            if path.exists():
                shutil.rmtree(path)
        if not committed:
            for path in (output_target, demand_target):
                if path.exists():
                    shutil.rmtree(path)
    return manifest


def cohort_vehicle_models(paths, source):
    labels = {}
    progress("Recovering vehicle labels from cohort exports")
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            if not {"series_id", "model", "demand_source"}.issubset(header):
                raise ValueError("vehicle_model recovery requires cohort series_id, model and demand_source")
            identity_index = header.index("series_id")
            model_index = header.index("model")
            source_index = header.index("demand_source")
            for row in reader:
                if len(row) != len(header) or row[source_index] != source:
                    raise ValueError("cohort source/column mismatch during repack")
                identity, label = row[identity_index], row[model_index]
                if not identity or not label.strip():
                    raise ValueError("vehicle_model recovery requires nonempty cohort identity and model")
                if identity in labels and labels[identity] != label:
                    raise ValueError(f"conflicting cohort vehicle models: {identity}")
                labels[identity] = label
    return labels


def repack_artifacts(output_dir, demand_dir, source="synthetic"):
    """Re-publish existing full records and cohorts without price loading or fitting."""
    if source not in ("synthetic", "observed"):
        raise ValueError("source must be synthetic or observed")
    output_dir = Path(output_dir)
    manifest = read_payload(output_dir / "manifest.json")
    metadata = manifest["sources"][source]
    provenance = read_payload(output_dir / metadata["provenance_path"])
    paths = ([output_dir / path for path in dict.fromkeys(metadata["observations_by_market"].values())]
             if "observations_by_market" in metadata else
             [output_dir / metadata["observations_path"]] if metadata.get("observations_path") else [])

    def payloads():
        vehicle_models = None
        for market, endpoint in sorted(metadata["markets"].items()):
            payload = read_payload(output_dir / endpoint)
            if payload.get("publication_layout") == PUBLICATION_LAYOUT:
                records = []
                for detail_path in dict.fromkeys(record["detail_path"] for record in payload["series"]):
                    shard = read_payload(output_dir / detail_path)
                    if shard.get("market") != market or shard.get("demand_source") != source:
                        raise ValueError("detail shard source/market mismatch")
                    records.extend(shard["series"])
                by_identity = {record["id"]: record for record in records}
                payload["series"] = [by_identity[record["id"]] for record in payload["series"]]
            for record in payload["series"]:
                if "vehicle_model" not in record:
                    if vehicle_models is None:
                        vehicle_models = cohort_vehicle_models(paths, source)
                    if record["id"] not in vehicle_models:
                        raise ValueError(f"missing cohort vehicle model: {record['id']}")
                    record["vehicle_model"] = vehicle_models[record["id"]]
            yield market, payload

    return publish_artifacts(output_dir, demand_dir, source, payloads(), provenance,
                             lambda stage, markets: partition_cohorts(stage, markets, paths, source), manifest)


def coverage(history):
    days = sorted({point["date"] for point in history})
    span = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days + 1 if days else 0
    prices = [point["price"] for point in sorted(history, key=lambda point: point["date"])]
    return dict(observed_days=len(days), calendar_span_days=span, missing_days=span - len(days),
                first_date=days[0] if days else None, last_date=days[-1] if days else None,
                different_price_levels=len(set(prices)),
                price_changes=sum(first != second for first, second in zip(prices, prices[1:])))


def series_artifact(series, rows, prior, prior_sd):
    purchase_rows = [row for row in rows if row["observation_kind"] == "purchase"]
    daily = aggregate_daily(purchase_rows)
    counts = {point["date"]: point for point in daily}
    prices = {point["date"]: point for point in series["history"]}
    dates = sorted(prices.keys() | counts.keys())
    history = []
    for day in dates[-90:]:
        demand = counts.get(day)
        point = prices.get(day)
        history.append(dict(date=day, price=point["price"] if point else demand["price"],
                            demand_price=demand["price"] if demand else None,
                            eligible_count=demand["eligible_count"] if demand else None,
                            selected_count=demand["selected_count"] if demand else None,
                            take_rate=demand["take_rate"] if demand else None))
    eligible = sum(row["eligible_count"] for row in purchase_rows)
    selected = sum(row["selected_count"] for row in purchase_rows)
    model = fit_model(purchase_rows, prior=prior, prior_sd=prior_sd)
    latest = series["history"][-1]
    warnings = list(series["quality_flags"]) + model["warnings"]
    if model.get("forecast_available"):
        if model["last_date"] < dates[-1]:
            warnings.append("purchase_history_stale_relative_to_price_catalogue")
        if latest["date"] < dates[-1]:
            warnings.append("catalogue_price_stale_relative_to_purchase_history")
        model["last_date"] = dates[-1]
        model["last_price"] = latest["price"]
        model["scenario_price_range"] = [min(point["price"] for point in series["history"]),
                                         max(point["price"] for point in series["history"])]
        warnings.append("scenarios_outside_training_price_range_are_extrapolations")
    metrics = rolling_validation(purchase_rows, prior=prior, prior_sd=prior_sd)
    if metrics["90"]["status"] != "validated_rolling_origin":
        warnings.append("90_day_forecast_unvalidated")
    if any(row["observation_kind"] == "inventory" for row in rows):
        warnings.append("inventory_excluded_from_model")
    if any(len({row["price"] for row in purchase_rows if row["date"] == day}) > 1 for day in counts):
        warnings.append("daily_demand_price_is_exposure_weighted_for_multiple_cohorts")
    first_price = series["history"][0]["price"]
    result = {key: value for key, value in series.items() if key not in ("history", "quality_flags", "market")}
    result.update(vehicle_model=series["model"], latest_price=latest["price"], base_price=latest["base_price"],
                  history=history, history_limit_points=90, model=model,
                  forecast=predict_model(model), metrics=metrics, warnings=sorted(set(warnings)),
                  eligible_count=eligible, selected_count=selected,
                  take_rate=selected / eligible if eligible else None,
                  price_change_pct=100 * (latest["price"] / first_price - 1) if first_price else None,
                  coverage=coverage(series["history"]), as_of=dates[-1],
                  observation_kinds=sorted({row["observation_kind"] for row in rows}),
                  price_sources=sorted({row["price_source"] for row in rows}))
    return result


def build_artifacts(prices_dir, output_dir, demand_dir, source="synthetic", observations=None,
                    markets="all", seed=None, config_path=None):
    """Immutable revisions + atomic manifest commit preserve both sources on failure.

    Consumers must resolve source endpoints from manifest.json, never guess revision
    paths. Old revisions are deliberately not garbage collected by this builder.
    """
    if source not in ("synthetic", "observed"):
        raise ValueError("source must be synthetic or observed")
    if source == "synthetic" and observations is not None:
        raise ValueError("--observations is only valid with --source observed")
    progress("Loading price archive")
    panel = load_price_panel(prices_dir)
    selected = set(panel.markets) if markets == "all" else set(
        part.strip().upper() for part in markets.split(",") if part.strip())
    if not selected or selected - panel.markets:
        raise ValueError("unknown/empty market selection: " + ", ".join(sorted(selected - panel.markets)))
    config = validate_config(json.loads(Path(config_path).read_text()) if config_path else dict(DEFAULT_CONFIG))
    effective_seed = config["seed"] if seed is None else seed
    config = dict(config, seed=effective_seed)
    validate_config(config)
    if source == "synthetic":
        progress("Simulating and validating demand")
        rows = validate_demand(generate_demand(panel, config), panel, source)
    else:
        rows = read_demand(observations, panel, source) if observations else []
    rows = [row for row in rows if row["market"] in selected]
    by_series = defaultdict(list)
    for row in rows:
        by_series[row["series_id"]].append(row)
    generated_at = datetime.now(timezone.utc).isoformat()
    payloads = {}
    for market in sorted(selected):
        progress(f"Fitting {source}/{market}")
        series_list = []
        for identity, series in sorted(panel.series.items()):
            if series["market"] != market:
                continue
            series_list.append(series_artifact(series, by_series[identity], config["log_price_effect"],
                                                config["sensitivity_prior_sd"]))
            if len(series_list) % 1000 == 0:
                progress(f"Fitting {source}/{market}: {len(series_list)} series")
        market_rows = [row for row in rows if row["market"] == market]
        payloads[market] = dict(schema_version=1, market=market, currency=MARKET_CURRENCY.get(market),
                                demand_source=source, generated_at=generated_at,
                                as_of=max((series["as_of"] for series in series_list), default=None),
                                assumptions=ASSUMPTIONS + ([
                                    "All exposures, selections, eligibility and sensitivity assumptions are arbitrary simulated demonstration data, not actual sales."
                                ] if source == "synthetic" else [
                                    "Observed mode has no synthetic fallback. Configured fallback sensitivity is an explicit assumption."
                                ]),
                                coverage=dict(priced_series=len(series_list),
                                              purchase_series=sum(series["eligible_count"] > 0 for series in series_list),
                                              forecast_series=sum(bool(series["forecast"]) for series in series_list),
                                              cohort_rows=len(market_rows),
                                              inventory_rows=sum(row["observation_kind"] == "inventory" for row in market_rows),
                                              zero_exposure_rows=sum(row["eligible_count"] == 0 for row in market_rows)),
                                status="ready" if market_rows else "no_observed_data" if source == "observed" else "no_usable_prices",
                                series=series_list)
    output_dir, demand_dir = Path(output_dir), Path(demand_dir)
    if output_dir.resolve() == Path(prices_dir).resolve() or demand_dir.resolve() == Path(prices_dir).resolve():
        raise ValueError("output/demand directories must not be the price archive")
    provenance = dict(schema_version=1, demand_source=source, generated_at=generated_at,
                          generator="seeded_binomial_ar1" if source == "synthetic" else None,
                          config=config if source == "synthetic" else None,
                          observations_input=str(observations) if observations else None,
                          row_count=len(rows), price_provenance="unchanged archived configurator snapshots",
                          demand_provenance="arbitrary simulated purchase cohorts" if source == "synthetic" else "user-supplied aggregate observations",
                          fallback_sensitivity=dict(mean=config["log_price_effect"], sd=config["sensitivity_prior_sd"]),
                          validation_report=dict(status="valid", errors=[], diagnostics=panel.diagnostics),
                          fields=list(rows[0]) if rows else [])

    def cohort_writer(stage, selected_markets):
        for market in selected_markets:
            write_demand(stage / f"{market}.csv.gz", (row for row in rows if row["market"] == market))

    return publish_artifacts(output_dir, demand_dir, source, payloads.items(), provenance, cohort_writer)