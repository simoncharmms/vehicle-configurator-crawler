import json
import csv
import gzip
import math
import subprocess
import sys
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from crawler.base import MARKET_CURRENCY
from crawler.intelligence.data import DemandValidationError, load_price_panel, read_demand, validate_demand, write_demand
from crawler.intelligence.synthetic import generate_demand
from crawler.intelligence.forecast import aggregate_daily, fit_model, predict_model, rolling_validation, score
from crawler.intelligence.build import build_artifacts


def load_payload(output, endpoint, expand=True):
    path = output / endpoint
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if expand and payload.get("publication_layout") == "sharded-gzip-v1":
        shards = {series["detail_path"] for series in payload["series"]}
        records = {record["id"]: record for shard in shards
                   for record in load_payload(output, shard, expand=False)["series"]}
        payload["series"] = [records[series["id"]] for series in payload["series"]]
    return payload


def snapshot(day="2026-01-01", price=100, **extra):
    return dict(brand="Test", market="DE", currency="EUR", timestamp=day + "T10:00:00Z",
                vehicles=[dict(model="Model", variant="A", base_price=30000,
                               available_options=[dict(code="X", brand_specific_name="Pack",
                                                       price=price, currency="EUR")])], **extra)


def test_latest_successful_run_and_archive_unchanged(tmp_path):
    first = snapshot(price=100)
    latest = snapshot(price=120)
    latest["timestamp"] = "2026-01-01T11:00:00Z"
    failed = snapshot(price=0, errors=["partial"])
    failed["timestamp"] = "2026-01-01T12:00:00Z"
    path = tmp_path / "not-a-date.json"
    path.write_text(json.dumps([first, latest, failed]))
    before = path.read_bytes()
    panel = load_price_panel(tmp_path)
    assert len(panel.series) == 1
    assert next(iter(panel.series.values()))["history"] == [
        {"date": "2026-01-01", "price": 120, "base_price": 30000}]
    assert panel.diagnostics
    assert path.read_bytes() == before


def test_stable_generation_and_strict_import(tmp_path):
    (tmp_path / "prices.json").write_text(json.dumps(snapshot()))
    panel = load_price_panel(tmp_path)
    rows = generate_demand(panel)
    assert rows == generate_demand(panel)
    assert rows != generate_demand(panel, seed=43)
    assert 20 <= rows[0]["eligible_count"] <= 80
    path = tmp_path / "cohorts.csv.gz"
    write_demand(path, rows)
    assert read_demand(path, panel, "synthetic") == rows
    with pytest.raises(DemandValidationError, match="sources cannot be mixed"):
        validate_demand(rows, panel, "observed")
    for key, value in [("selected_count", 1000), ("eligible_count", True),
                       ("eligible_count", 20.0), ("price", float("nan")),
                       ("date", "2026-02-30"), ("series_id", "unknown"),
                       ("currency", "GBP"), ("price", 999), ("price_source", "bad")]:
        with pytest.raises(DemandValidationError):
            validate_demand([dict(rows[0], **{key: value})], panel, "synthetic")
    with pytest.raises(DemandValidationError, match="duplicate"):
        validate_demand(rows + rows, panel, "synthetic")


def cohorts(days=40, flat=False):
    rows = []
    for index in range(days):
        price = 100 if flat else (60, 100, 160, 120)[index % 4]
        rate = 1 / (1 + math.exp(-(-0.3 - 1.8 * math.log((price + 1) / 101))))
        rows.append(dict(date=(date(2026, 1, 1) + timedelta(days=index)).isoformat(),
                         price=price, eligible_count=100, selected_count=round(100 * rate),
                         series_id="test", cohort_id="daily", demand_source="synthetic", observation_kind="purchase"))
    return rows


def test_model_flat_gate_bounds_and_cutoff():
    rows = cohorts()
    model = fit_model(rows)
    assert model["sensitivity_source"] == "fitted_association"
    assert model["coefficients"][1] < 0
    baseline = predict_model(model)
    lower_price = predict_model(model, -20)
    assert lower_price[-1]["rate"] > baseline[-1]["rate"]
    assert len(baseline) == 90
    assert all(0 < point["lower"] <= point["rate"] <= point["upper"] < 1 for point in baseline)
    assert baseline[0]["rate"] == baseline[1]["rate"]
    assert lower_price[0]["rate"] != lower_price[1]["rate"]
    cutoff = rows[19]["date"]
    altered = [dict(row, price=9999, selected_count=0) if row["date"] > cutoff else row for row in rows]
    assert fit_model(rows, cutoff=cutoff) == fit_model(altered, cutoff=cutoff)
    flat = fit_model(cohorts(flat=True))
    assert flat["sensitivity_source"] == "configured_prior_assumption"
    assert "flat_prices_elasticity_unidentifiable" in flat["warnings"]
    assert predict_model(flat, 20)[-1]["rate"] < predict_model(flat)[-1]["rate"]
    assert predict_model(fit_model(rows[:2])) == []
    assert rolling_validation(rows)["90"]["status"] == "unvalidated_insufficient_history"


def test_count_weighting_zero_exposure_and_inventory():
    rows = cohorts(1)
    rows[0].update(eligible_count=10, selected_count=9)
    rows.append(dict(rows[0], cohort_id="large", eligible_count=90, selected_count=1))
    assert aggregate_daily(rows)[0]["take_rate"] == 0.1
    metric = score([(row, 0.2) for row in rows])
    assert metric["brier"] == pytest.approx((10 * 0.8 ** 2 + 90 * 0.2 ** 2) / 100)
    assert metric["observed_rate"] == 0.1
    zero = [dict(rows[0], eligible_count=0, selected_count=0)]
    assert aggregate_daily(zero)[0]["take_rate"] is None
    assert fit_model(zero)["kind"] == "unavailable"
    assert fit_model([dict(rows[0], observation_kind="inventory")])["kind"] == "unavailable"


def fixture_archive(path, days=16):
    path.mkdir()
    for index in range(days):
        day = (date(2026, 1, 1) + timedelta(days=index)).isoformat()
        (path / f"run{index:03}.json").write_text(json.dumps(snapshot(day, (80, 100, 120)[index % 3])))


def test_cli_artifacts_source_isolation_and_failed_build(tmp_path):
    prices, output, demand = [tmp_path / name for name in ("prices", "output", "demand")]
    fixture_archive(prices)
    before = {path.name: path.read_bytes() for path in prices.iterdir()}
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/build_pricing_intelligence.py"),
               "--prices-dir", str(prices), "--output-dir", str(output), "--demand-dir", str(demand)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    first = json.loads((output / "manifest.json").read_text())
    endpoint = first["sources"]["synthetic"]["markets"]["DE"]
    original = (output / endpoint).read_bytes()
    market = load_payload(output, endpoint)
    assert market["currency"] == "EUR"
    compact = load_payload(output, endpoint, expand=False)
    for record in (market["series"][0], compact["series"][0]):
        assert record["vehicle_model"] == "Model"
        assert isinstance(record["model"], dict)
        assert record["model"]["coefficients"]
    panel = load_price_panel(prices)
    imported = read_demand(output / first["sources"]["synthetic"]["observations_by_market"]["DE"], panel, "synthetic")
    assert imported[0]["model"] == market["series"][0]["vehicle_model"]
    assert len(market["series"][0]["forecast"]) == 90
    assert market["series"][0]["metrics"]["90"]["status"].startswith("unvalidated")
    assert len(first["sources"]["synthetic"]["markets"]) == len(MARKET_CURRENCY)
    assert subprocess.run(command + ["--source", "observed"], capture_output=True).returncode == 0
    second = json.loads((output / "manifest.json").read_text())
    assert second["sources"]["synthetic"] == first["sources"]["synthetic"]
    assert (output / endpoint).read_bytes() == original
    observed = load_payload(output, second["sources"]["observed"]["markets"]["DE"])
    assert observed["status"] == "no_observed_data"
    assert observed["series"][0]["forecast"] == []
    assert observed["series"][0]["take_rate"] is None
    published = (output / "manifest.json").read_bytes()
    invalid = tmp_path / "invalid.json"
    invalid.write_text('[{"series_id":"unknown"}]')
    failure = subprocess.run(command + ["--source", "observed", "--observations", str(invalid)], capture_output=True, text=True)
    assert failure.returncode == 2
    assert "row 1" in failure.stderr
    assert (output / "manifest.json").read_bytes() == published
    assert subprocess.run(command, capture_output=True).returncode == 0
    rebuilt = json.loads((output / "manifest.json").read_text())
    assert rebuilt["sources"]["observed"] == second["sources"]["observed"]
    repeated = load_payload(output, rebuilt["sources"]["synthetic"]["markets"]["DE"])
    repeated.pop("generated_at")
    market.pop("generated_at")
    assert repeated == market
    assert before == {path.name: path.read_bytes() for path in prices.iterdir()}


def test_price_panel_identity_collision_currency_and_gaps(tmp_path):
    run = snapshot()
    vehicle = run["vehicles"][0]
    vehicle["available_options"].append(dict(vehicle["available_options"][0]))
    other_variant = dict(vehicle, variant="B")
    other_code = dict(vehicle, variant="C", available_options=[
        dict(vehicle["available_options"][0], code="Y"), dict(vehicle["available_options"][0], code="Z")])
    fallback = dict(vehicle, variant="D", available_options=[dict(vehicle["available_options"][0], code="")])
    run["vehicles"] += [other_variant, other_code, fallback]
    (tmp_path / "first.json").write_text(json.dumps(run))
    later = snapshot("2026-01-03")
    later["vehicles"][0]["available_options"] += [dict(vehicle["available_options"][0], price=999)]
    (tmp_path / "conflict.json").write_text(json.dumps(later))
    invalid = snapshot("2026-01-04", price=-1)
    (tmp_path / "negative.json").write_text(json.dumps(invalid))
    mismatch = snapshot("2026-01-05")
    mismatch["currency"] = "GBP"
    (tmp_path / "currency.json").write_text(json.dumps(mismatch))
    panel = load_price_panel(tmp_path)
    assert len(panel.series) == 5
    assert all(len(series["history"]) == 1 for series in panel.series.values())
    assert any("collision" in diagnostic for diagnostic in panel.diagnostics)
    assert any("currency" in diagnostic for diagnostic in panel.diagnostics)
    assert any("label_identity_fallback" in series["quality_flags"] for series in panel.series.values())


def test_grouped_cohorts_equivalent_to_weighted_expansion():
    rows = cohorts(20)
    split = []
    for row in rows:
        split.extend([dict(row, cohort_id="selected", eligible_count=row["selected_count"]),
                      dict(row, cohort_id="unselected", eligible_count=row["eligible_count"] - row["selected_count"], selected_count=0)])
    first, second = fit_model(rows), fit_model(split)
    assert first["coefficients"] == pytest.approx(second["coefficients"], abs=1e-8)
    for left, right in zip(first["covariance"], second["covariance"]):
        assert left == pytest.approx(right, abs=1e-8)


def test_observed_transaction_prices_inventory_and_json_import(tmp_path):
    prices, output, demand = [tmp_path / name for name in ("prices", "output", "demand")]
    fixture_archive(prices)
    panel = load_price_panel(prices)
    rows = generate_demand(panel)
    observed = [dict(row, demand_source="observed", price_source="observed", price=123.45,
                     source_id="aggregate_orders_fixture", quality_flags=[]) for row in rows]
    observed.append(dict(observed[0], cohort_id="inventory", observation_kind="inventory", selected_count=0))
    path = tmp_path / "observed.json"
    path.write_text(json.dumps(observed))
    manifest = build_artifacts(prices, output, demand, source="observed", observations=path, markets="DE")
    payload = load_payload(output, manifest["sources"]["observed"]["markets"]["DE"])
    series = payload["series"][0]
    assert series["model"]["sensitivity_source"] == "configured_prior_assumption"
    assert "inventory_excluded_from_model" in series["warnings"]
    assert series["eligible_count"] == sum(row["eligible_count"] for row in observed if row["observation_kind"] == "purchase")
    assert series["history"][-1]["demand_price"] == 123.45
    assert payload["demand_source"] == "observed"
    assert len(series["forecast"]) == 90
    compressed = tmp_path / "observed.csv.gz"
    write_demand(compressed, observed)
    assert read_demand(compressed, panel) == validate_demand(observed, panel, "observed")
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/build_pricing_intelligence.py"),
               "--source", "observed", "--observations", str(compressed), "--markets", "DE",
               "--prices-dir", str(prices), "--output-dir", str(output), "--demand-dir", str(demand)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_atomic_commit_failure_preserves_previous_files(tmp_path, monkeypatch):
    import crawler.intelligence.build as builder

    prices, output, demand = [tmp_path / name for name in ("prices", "output", "demand")]
    fixture_archive(prices)
    build_artifacts(prices, output, demand, markets="DE")
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for directory in (output, demand) for path in directory.rglob("*") if path.is_file()}
    replace = builder.os.replace

    def fail_commit(source, target):
        if Path(target).name == "manifest.json":
            raise OSError("simulated publication failure")
        return replace(source, target)

    monkeypatch.setattr(builder.os, "replace", fail_commit)
    with pytest.raises(OSError, match="publication failure"):
        build_artifacts(prices, output, demand, markets="DE")
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for directory in (output, demand) for path in directory.rglob("*") if path.is_file()}
    assert before == after


def test_portable_javascript_parity():
    node, jxa = shutil.which("node"), shutil.which("osascript")
    if not node and not jxa:
        pytest.skip("JavaScript parity requires node or macOS osascript")
    root = Path(__file__).resolve().parents[1]
    script = (root / "docs/intelligence-model.js").read_text()
    reference = (root / "crawler/intelligence/portable.js").read_text()
    assert script.split('\n\nif (typeof window')[0].rstrip() == reference.rstrip()
    for flat in (False, True):
        model = fit_model(cohorts(flat=flat))
        for percentage in (-100, -17.5, 0, 42.25, 100000):
            call = f"predictSeries({json.dumps({'model': model})}, {percentage}, 90)"
            code = script + (f"\nconsole.log(JSON.stringify({call}));" if node else f"\nJSON.stringify({call});")
            command = [node, "-e", code] if node else [jxa, "-l", "JavaScript", "-e", code]
            result = subprocess.run(command, capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            actual = json.loads(result.stdout)
            expected = predict_model(model, percentage, 90)
            for first, second in zip(actual, expected):
                assert first["date"] == second["date"]
                assert first["day"] == second["day"]
                for key in ("rate", "lower", "upper"):
                    assert first[key] == pytest.approx(second[key], abs=1e-12)


def test_sparse_calendar_does_not_validate_long_horizon():
    rows = cohorts(16)
    rows[-1]["date"] = (date.fromisoformat(rows[13]["date"]) + timedelta(days=90)).isoformat()
    assert rolling_validation(rows)["90"]["status"] == "unvalidated_insufficient_history"


def test_positive_fitted_sign_not_flipped_and_supported_horizons():
    rows = cohorts(110)
    reversed_effect = [dict(row, selected_count=100 - row["selected_count"]) for row in rows]
    model = fit_model(reversed_effect)
    assert model["coefficients"][1] > 0
    assert "positive_fitted_price_association_not_flipped" in model["warnings"]
    metrics = rolling_validation(rows)
    for horizon in ("1", "30", "90"):
        assert metrics[horizon]["status"] == "validated_rolling_origin"
        assert metrics[horizon]["model"]["eligible_count"] > 0
        assert metrics[horizon]["model"]["calibration_bins"]
        assert metrics[horizon]["pooled_smoothed_baseline"]


@pytest.mark.parametrize("count", ["20.0", "True", "-1", "nan", ""])
def test_csv_rejects_noninteger_counts(tmp_path, count):
    import csv
    from crawler.intelligence.data import DEMAND_FIELDS

    prices = tmp_path / "prices"
    fixture_archive(prices, 1)
    panel = load_price_panel(prices)
    row = generate_demand(panel)[0]
    row.update(demand_source="observed", eligible_count=count, quality_flags="[]")
    path = tmp_path / "import.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=DEMAND_FIELDS)
        writer.writeheader()
        writer.writerow(row)
    with pytest.raises(DemandValidationError, match="integer digits"):
        read_demand(path, panel)


def test_generation_prefix_and_series_stability(tmp_path):
    prices = tmp_path / "prices"
    fixture_archive(prices, 3)
    before_panel = load_price_panel(prices)
    before = generate_demand(before_panel)
    (prices / "future.json").write_text(json.dumps(snapshot("2026-02-01", 999)))
    other = snapshot("2026-01-01")
    other["brand"] = "Other"
    other["vehicles"][0]["variant"] = "different variant"
    (prices / "other.json").write_text(json.dumps(other))
    after_panel = load_price_panel(prices)
    assert len(after_panel.series) > len(before_panel.series)
    after = generate_demand(after_panel)
    assert [row for row in after if row["series_id"] in before_panel.series and row["date"] <= "2026-01-03"] == before


def test_native_currencies_zero_prices_and_all_market_manifest(tmp_path):
    prices, output, demand = [tmp_path / name for name in ("prices", "output", "demand")]
    prices.mkdir()
    for market, currency in MARKET_CURRENCY.items():
        run = snapshot(price=0)
        run.update(market=market, currency=currency)
        run["vehicles"][0].update(market=market, currency=currency)
        run["vehicles"][0]["available_options"][0]["currency"] = currency
        (prices / f"{market}.json").write_text(json.dumps(run))
    manifest = build_artifacts(prices, output, demand)
    endpoints = manifest["sources"]["synthetic"]["markets"]
    assert set(endpoints) == set(MARKET_CURRENCY)
    for market, endpoint in endpoints.items():
        payload = load_payload(output, endpoint)
        assert payload["currency"] == MARKET_CURRENCY[market]
        assert payload["series"][0]["latest_price"] == 0
        assert payload["series"][0]["price_change_pct"] is None
        assert payload["series"][0]["forecast"] == []


def legacy_publication(tmp_path):
    output, demand = tmp_path / "output", tmp_path / "demand"
    revision = "legacy"
    target = output / "synthetic" / revision
    cohort_target = demand / "synthetic" / revision
    target.mkdir(parents=True)
    cohort_target.mkdir(parents=True)
    originals = {}
    model = fit_model(cohorts())
    for market in ("DE", "GB"):
        records = [dict(id=f"{market}-{index}", brand="Test", vehicle_model="Model", model=model,
                        history=[dict(date="2026-01-01", price=100)], forecast=predict_model(model),
                        metrics={"90": {"status": "unvalidated"}}, warnings=[],
                        latest_price=100, eligible_count=100, selected_count=25,
                        extra_preserved={"value": index}) for index in range(105)]
        originals[market] = dict(schema_version=1, market=market, demand_source="synthetic", series=records)
        (target / f"{market}.json").write_text(json.dumps(originals[market]))
        header = ["market", "demand_source", "quality_flags", "selected_count", "extra", "series_id", "model"]
        rows = [[market, "synthetic", '["quoted,flag"]', "0001", "line\nbreak", record["id"], "Model"]
            for market, payload in originals.items() for record in payload["series"]]
    with gzip.open(cohort_target / "observations.csv.gz", "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    (cohort_target / "provenance.json").write_text(json.dumps({"row_count": len(rows)}))
    source = dict(schema_version=1, demand_source="synthetic", revision=revision,
                  markets={market: f"synthetic/{revision}/{market}.json" for market in originals},
                  observations_path=f"../demand/synthetic/{revision}/observations.csv.gz",
                  provenance_path=f"../demand/synthetic/{revision}/provenance.json",
                  scope_markets=sorted(originals))
    manifest = dict(schema_version=1, sources={"synthetic": source, "observed": {"untouched": True}})
    (output / "manifest.json").write_text(json.dumps(manifest))
    return output, demand, manifest, originals, header, rows


def test_repack_compact_details_cohort_partition_and_source_isolation(tmp_path, monkeypatch):
    import crawler.intelligence.build as builder

    output, demand, previous, originals, header, rows = legacy_publication(tmp_path)
    def no_training(*args, **kwargs):
        pytest.fail("repack must not fit or load prices")
    monkeypatch.setattr(builder, "fit_model", no_training)
    monkeypatch.setattr(builder, "load_price_panel", no_training)
    monkeypatch.setattr(builder, "cohort_vehicle_models", no_training)
    manifest = builder.repack_artifacts(output, demand)
    assert manifest["sources"]["observed"] == previous["sources"]["observed"]
    source = manifest["sources"]["synthetic"]
    assert source["publication_layout"] == "sharded-gzip-v1"
    assert not source.get("observations_path")
    assert read_provenance(output / source["provenance_path"]) == {"row_count": len(rows)}
    for market, endpoint in source["markets"].items():
        compact = load_payload(output, endpoint, expand=False)
        assert compact["model_transform"] == originals[market]["series"][0]["model"]["transform"]
        assert load_payload(output, endpoint)["series"] == originals[market]["series"]
        assert len({record["detail_path"] for record in compact["series"]}) == 3
        for record in compact["series"]:
            assert record["vehicle_model"] == "Model"
            assert not ({"history", "forecast", "metrics"} & record.keys())
            assert "transform" not in record["model"]
            assert record["model"]["sensitivity_source"] == "fitted_association"
            portable = dict(record["model"], transform=compact["model_transform"])
            assert predict_model(portable, -20) == predict_model(originals[market]["series"][0]["model"], -20)
            shard = load_payload(output, record["detail_path"], expand=False)
            assert len(shard["series"]) <= 50
            assert shard["market"] == market and shard["demand_source"] == "synthetic"
            assert (output / record["detail_path"]).stat().st_size < 1_000_000
        with gzip.open(output / source["observations_by_market"][market], "rt", newline="") as handle:
            partition = list(csv.reader(handle))
        assert partition == [header] + [row for row in rows if row[0] == market]
    assert (output / previous["sources"]["synthetic"]["markets"]["DE"]).exists()
    first_bytes = (output / source["markets"]["DE"]).read_bytes()
    first_shard = load_payload(output, source["markets"]["DE"], expand=False)["series"][0]["detail_path"]
    again = builder.repack_artifacts(output, demand)
    second_bytes = (output / again["sources"]["synthetic"]["markets"]["DE"]).read_bytes()
    assert first_bytes[:10] == second_bytes[:10] == b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff'
    second_shard = load_payload(output, again["sources"]["synthetic"]["markets"]["DE"], expand=False)["series"][0]["detail_path"]
    assert (output / first_shard).read_bytes() == (output / second_shard).read_bytes()
    assert load_payload(output, again["sources"]["synthetic"]["markets"]["DE"])["series"] == originals["DE"]["series"]


@pytest.mark.parametrize("sharded", [False, True])
def test_repack_recovers_vehicle_models_without_refitting(tmp_path, monkeypatch, sharded):
    import crawler.intelligence.build as builder

    output, demand, previous, originals, *_ = legacy_publication(tmp_path)
    if sharded:
        previous = builder.repack_artifacts(output, demand)
    for endpoint in previous["sources"]["synthetic"]["markets"].values():
        payload = load_payload(output, endpoint, expand=False)
        paths = {record["detail_path"] for record in payload["series"]} if sharded else {endpoint}
        for path in paths:
            detail = load_payload(output, path, expand=False)
            for record in detail["series"]:
                del record["vehicle_model"]
            opener = gzip.open if path.endswith(".gz") else open
            with opener(output / path, "wt", encoding="utf-8") as handle:
                json.dump(detail, handle)
    recover = builder.cohort_vehicle_models
    calls = []
    def recover_once(paths, source):
        calls.append(source)
        return recover(paths, source)
    def no_training(*args, **kwargs):
        pytest.fail("recovery must not fit or load prices")
    monkeypatch.setattr(builder, "cohort_vehicle_models", recover_once)
    monkeypatch.setattr(builder, "fit_model", no_training)
    monkeypatch.setattr(builder, "load_price_panel", no_training)
    manifest = builder.repack_artifacts(output, demand)
    assert calls == ["synthetic"]
    for market, endpoint in manifest["sources"]["synthetic"]["markets"].items():
        assert load_payload(output, endpoint)["series"] == originals[market]["series"]
        for record in load_payload(output, endpoint, expand=False)["series"]:
            assert record["vehicle_model"] == "Model"
            assert isinstance(record["model"], dict)
            assert record["model"]["coefficients"]


@pytest.mark.parametrize("defect", ["conflict", "missing", "blank"])
def test_repack_recovery_rejects_invalid_labels_atomically(tmp_path, defect):
    import crawler.intelligence.build as builder

    output, demand, previous, originals, header, rows = legacy_publication(tmp_path)
    endpoint = output / previous["sources"]["synthetic"]["markets"]["DE"]
    del originals["DE"]["series"][0]["vehicle_model"]
    endpoint.write_text(json.dumps(originals["DE"]))
    if defect == "conflict":
        rows.append(rows[0][:-1] + ["Different Model"])
    elif defect == "missing":
        rows = rows[1:]
    else:
        rows[0][-1] = " "
    cohort_path = output / previous["sources"]["synthetic"]["observations_path"]
    with gzip.open(cohort_path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="conflicting cohort|missing cohort|nonempty cohort"):
        builder.repack_artifacts(output, demand)
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_repack_failed_publish_preserves_all_public_files(tmp_path, monkeypatch):
    import crawler.intelligence.build as builder

    output, demand, *_ = legacy_publication(tmp_path)
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for directory in (output, demand) for path in directory.rglob("*") if path.is_file()}
    replace = builder.os.replace
    def fail_commit(source, target):
        if Path(target).name == "manifest.json":
            raise OSError("simulated publication failure")
        return replace(source, target)
    monkeypatch.setattr(builder.os, "replace", fail_commit)
    with pytest.raises(OSError, match="publication failure"):
        builder.repack_artifacts(output, demand)
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for directory in (output, demand) for path in directory.rglob("*") if path.is_file()}
    assert before == after


def read_provenance(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def test_repack_cli_and_finite_json_guard(tmp_path):
    output, demand, previous, *_ = legacy_publication(tmp_path)
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/build_pricing_intelligence.py"),
               "--repack", "--output-dir", str(output), "--demand-dir", str(demand),
               "--prices-dir", str(tmp_path / "nonexistent-prices")]
    endpoint = output / previous["sources"]["synthetic"]["markets"]["GB"]
    original = endpoint.read_bytes()
    payload = json.loads(original)
    payload["series"][0]["latest_price"] = float("nan")
    endpoint.write_text(json.dumps(payload))
    before = (output / "manifest.json").read_bytes()
    failure = subprocess.run(command, capture_output=True, text=True)
    assert failure.returncode == 2
    assert (output / "manifest.json").read_bytes() == before
    assert sorted(path.name for path in (output / "synthetic").iterdir()) == ["legacy"]
    endpoint.write_bytes(original)
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "Partitioning" in result.stderr
    published = json.loads((output / "manifest.json").read_text())
    assert published["sources"]["synthetic"]["publication_layout"] == "sharded-gzip-v1"


def test_repack_rejects_mixed_cohort_source(tmp_path):
    from crawler.intelligence.build import repack_artifacts

    output, demand, previous, *_ = legacy_publication(tmp_path)
    cohort_path = output / previous["sources"]["synthetic"]["observations_path"]
    with gzip.open(cohort_path, "wt", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["market", "demand_source"])
        writer.writerow(["DE", "observed"])
    before = (output / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="source/column mismatch"):
        repack_artifacts(output, demand)
    assert (output / "manifest.json").read_bytes() == before
    assert sorted(path.name for path in (output / "synthetic").iterdir()) == ["legacy"]