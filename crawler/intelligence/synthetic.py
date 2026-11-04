"""Deterministic arbitrary simulated exposure, never an estimate of vehicle sales."""

import hashlib
import math
import random


DEFAULT_CONFIG = {
    "schema_version": 1,
    "generator_version": "1",
    "seed": 42,
    "min_exposure": 20,
    "max_exposure": 80,
    "baseline_logit": -1.0,
    "affinity_sd": 0.6,
    "log_price_effect": -1.5,
    "lag_change_effect": -0.3,
    "shock_autocorrelation": 0.7,
    "shock_sd": 0.12,
    "sensitivity_prior_sd": 1.0,
}


def validate_config(config):
    if set(config) != set(DEFAULT_CONFIG):
        raise ValueError("synthetic config must contain exactly: " + ", ".join(DEFAULT_CONFIG))
    if config["schema_version"] != 1 or config["generator_version"] != "1":
        raise ValueError("unsupported generator config version")
    for key in ("seed", "min_exposure", "max_exposure"):
        if type(config[key]) is not int or config[key] < 0:
            raise ValueError(f"{key} must be a nonnegative integer")
    if config["max_exposure"] < config["min_exposure"] or config["max_exposure"] > 10000:
        raise ValueError("exposure bounds must be ordered and <= 10000")
    for key in ("baseline_logit", "affinity_sd", "log_price_effect", "lag_change_effect",
                "shock_autocorrelation", "shock_sd", "sensitivity_prior_sd"):
        if isinstance(config[key], bool) or not isinstance(config[key], (int, float)) or not math.isfinite(config[key]):
            raise ValueError(f"{key} must be finite")
    if config["log_price_effect"] >= 0 or config["lag_change_effect"] > 0:
        raise ValueError("configured price effects must be negative/nonpositive")
    if not 0 <= config["shock_autocorrelation"] < 1:
        raise ValueError("shock_autocorrelation must be in [0,1)")
    if any(config[key] < 0 for key in ("affinity_sd", "shock_sd")) or config["sensitivity_prior_sd"] <= 0:
        raise ValueError("invalid standard deviation")
    return config


def generate_demand(panel, config=None, seed=None):
    config = validate_config(dict(DEFAULT_CONFIG if config is None else config))
    seed = config["seed"] if seed is None else seed
    rows = []
    for identity, series in sorted(panel.series.items()):
        digest = hashlib.sha256(f"{seed}:{identity}".encode()).digest()
        rng = random.Random(int.from_bytes(digest, "big"))
        affinity = config["baseline_logit"] + rng.gauss(0, config["affinity_sd"])
        history = sorted(series["history"], key=lambda point: point["date"])
        reference = history[0]["price"] + 1.0
        previous = history[0]["price"]
        shock = 0.0
        for point in history:
            shock = config["shock_autocorrelation"] * shock + rng.gauss(0, config["shock_sd"])
            relative = math.log((point["price"] + 1.0) / reference)
            change = math.log((point["price"] + 1.0) / (previous + 1.0))
            logit = affinity + config["log_price_effect"] * relative + config["lag_change_effect"] * change + shock
            rate = 1 / (1 + math.exp(-max(-35, min(35, logit))))
            eligible = rng.randint(config["min_exposure"], config["max_exposure"])
            selected = sum(rng.random() < rate for _ in range(eligible))
            row = {key: series[key] for key in ("market", "currency", "brand", "model", "variant",
                                               "option_code", "option_name", "standardized_name")}
            row.update(schema_version=1, date=point["date"], cohort_id="simulated_daily",
                       series_id=identity, price=point["price"], base_price=point["base_price"],
                       eligible_count=eligible, selected_count=selected, demand_source="synthetic",
                       observation_kind="purchase", price_source="scraped",
                       source_id=f"generator:1:seed:{seed}",
                       quality_flags=series["quality_flags"] + ["simulated_exposure", "eligibility_assumed"])
            rows.append(row)
            previous = point["price"]
    return rows