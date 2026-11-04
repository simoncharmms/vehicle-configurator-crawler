"""Count-weighted binomial logistic fits and portable conditional scenarios.

No trend is extrapolated. Fixed base-price ratios duplicate the price feature and
are omitted. Hessian bands are approximate expected-rate intervals conditional on
the binomial model and priors, not purchase prediction or causal-effect intervals.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta


FEATURE_NAMES = ["intercept", "log_relative_price", "recent_change"]
TRANSFORM = {
    "version": 1,
    "price_offset": 1.0,
    "relative_clip": [-2.0, 2.0],
    "change_clip": [-1.0, 1.0],
    "logit_clip": [-35.0, 35.0],
    "lag_rule": "previous available purchase-cohort day; future previous scenario day",
    "trend": "omitted",
    "base_price_ratio": "omitted: redundant for fixed base price",
    "interval_z": 1.96,
    "interval_method": "approximate regularized binomial Hessian / explicit Gaussian prior",
    "interval_scope": "expected rate, source/model conditional; not future cohort counts",
}


def clip(value, bounds):
    return max(bounds[0], min(bounds[1], value))


def sigmoid(value):
    return 1.0 / (1.0 + math.exp(-clip(value, [-35.0, 35.0])))


def features(price, previous_price, reference_price):
    return [1.0,
            clip(math.log((price + 1.0) / (reference_price + 1.0)), [-2.0, 2.0]),
            clip(math.log((price + 1.0) / (previous_price + 1.0)), [-1.0, 1.0])]


def dot(left, right):
    return sum(first * second for first, second in zip(left, right))


def inverse(matrix):
    size = len(matrix)
    augmented = [list(row) + [float(index == column) for column in range(size)]
                 for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda index: abs(augmented[index][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        if abs(scale) < 1e-14:
            raise ValueError("singular model Hessian")
        augmented[column] = [value / scale for value in augmented[column]]
        for index in range(size):
            if index != column:
                factor = augmented[index][column]
                augmented[index] = [value - factor * other for value, other in
                                    zip(augmented[index], augmented[column])]
    return [row[size:] for row in augmented]


def aggregate_daily(rows):
    buckets = defaultdict(list)
    for row in rows:
        if row["observation_kind"] == "purchase":
            buckets[row["date"]].append(row)
    result = []
    for day, cohorts in sorted(buckets.items()):
        eligible = sum(row["eligible_count"] for row in cohorts)
        selected = sum(row["selected_count"] for row in cohorts)
        price = (sum(row["price"] * row["eligible_count"] for row in cohorts) / eligible
                 if eligible else cohorts[-1]["price"])
        if len({row["price"] for row in cohorts}) == 1:
            price = cohorts[0]["price"]
        result.append(dict(date=day, price=price, eligible_count=eligible,
                           selected_count=selected, take_rate=selected / eligible if eligible else None))
    return result


def fit_model(rows, cutoff=None, prior=-1.5, prior_sd=1.0):
    rows = sorted([row for row in rows if row["observation_kind"] == "purchase"
                   and row["eligible_count"] > 0 and (cutoff is None or row["date"] <= cutoff)],
                  key=lambda row: (row["date"], row["cohort_id"]))
    sources = {row["demand_source"] for row in rows}
    identities = {row["series_id"] for row in rows}
    contexts = {(row.get("market"), row.get("currency")) for row in rows}
    if len(sources) > 1 or len(identities) > 1 or len(contexts) > 1:
        raise ValueError("fit_model requires a single source and series")
    if not math.isfinite(prior) or prior >= 0 or not math.isfinite(prior_sd) or prior_sd <= 0:
        raise ValueError("sensitivity prior must be explicitly negative with positive sd")
    if not rows:
        return dict(kind="unavailable", warnings=["no_positive_purchase_exposure"], forecast_available=False)
    daily = aggregate_daily(rows)
    eligible = sum(row["eligible_count"] for row in rows)
    selected = sum(row["selected_count"] for row in rows)
    smoothed = (selected + 0.5) / (eligible + 1)
    intercept = math.log(smoothed / (1 - smoothed))
    latest = daily[-1]
    reference = daily[0]["price"]
    levels = len({row["price"] for row in rows})
    warnings = ["association_not_causal", "approximate_model_conditional_intervals",
                "serial_dependence_and_sampling_bias_not_in_intervals", "no_time_trend_extrapolation"]
    if sources == {"synthetic"}:
        warnings.append("simulation_only_not_real_demand_accuracy")
    available = len(daily) >= 7 and eligible >= 100
    varying = levels >= 3 and max(row["price"] for row in rows) - min(row["price"] for row in rows) >= max(1, reference * 0.02)
    learned = len(daily) >= 14 and eligible >= 200 and varying
    coefficients = [intercept, prior, 0.0]
    covariance = [[0.0] * 3 for _ in range(3)]
    variance = 1 / (selected + 0.5) + 1 / (eligible - selected + 0.5)
    if not learned:
        warnings.append("flat_prices_elasticity_unidentifiable" if levels == 1 else "insufficient_price_variation_or_history")
        warnings.append("price_sensitivity_is_configured_assumption")
        reference = latest["price"]
        covariance[0][0], covariance[1][1] = variance, prior_sd ** 2
        covariance[2][2] = prior_sd ** 2
    else:
        previous = daily[0]["price"]
        lag = {}
        for point in daily:
            lag[point["date"]] = previous
            previous = point["price"]
        design = [(features(row["price"], lag[row["date"]], reference),
                   row["eligible_count"], row["selected_count"]) for row in rows]
        coefficients = [intercept, 0.0, 0.0]
        precision = [0.01, 1.0, 4.0]

        def objective(parameters):
            value = 0.5 * sum(weight * coefficient ** 2 for weight, coefficient in zip(precision, parameters))
            for vector, exposure, successes in design:
                linear = dot(vector, parameters)
                value += exposure * (max(linear, 0) + math.log1p(math.exp(-abs(linear)))) - successes * linear
            return value

        converged = False
        for _ in range(60):
            gradient = [-weight * coefficient for weight, coefficient in zip(precision, coefficients)]
            hessian = [[precision[index] if index == column else 0.0 for column in range(3)]
                       for index in range(3)]
            for vector, exposure, successes in design:
                rate = sigmoid(dot(vector, coefficients))
                for index in range(3):
                    gradient[index] += vector[index] * (successes - exposure * rate)
                    for column in range(3):
                        hessian[index][column] += exposure * rate * (1 - rate) * vector[index] * vector[column]
            covariance = inverse(hessian)
            step = [dot(row, gradient) for row in covariance]
            scale = 1.0
            current = objective(coefficients)
            while scale > 1e-8:
                candidate = [value + scale * delta for value, delta in zip(coefficients, step)]
                if objective(candidate) <= current + 1e-10:
                    break
                scale *= 0.5
            coefficients = candidate
            if max(abs(scale * value) for value in step) < 1e-8:
                converged = True
                break
        hessian = [[precision[index] if index == column else 0.0 for column in range(3)]
                   for index in range(3)]
        for vector, exposure, _ in design:
            rate = sigmoid(dot(vector, coefficients))
            for index in range(3):
                for column in range(3):
                    hessian[index][column] += exposure * rate * (1 - rate) * vector[index] * vector[column]
        covariance = inverse(hessian)
        if not converged:
            warnings.append("optimizer_not_converged_forecast_suppressed")
            available = False
        if coefficients[1] > 0:
            warnings.append("positive_fitted_price_association_not_flipped")
    if not available:
        warnings.append("minimum_forecast_gate_7_days_100_exposures_not_met" if len(daily) < 7 or eligible < 100
                        else "forecast_suppressed")
    return dict(kind="regularized_binomial_logistic" if learned else "smoothed_intercept_with_price_prior",
                intercept=coefficients[0], coefficients=coefficients, feature_names=list(FEATURE_NAMES),
                reference_price=reference, last_price=latest["price"], last_date=latest["date"],
                lag_price=daily[-2]["price"] if len(daily) > 1 else latest["price"],
                sensitivity_source="fitted_association" if learned else "configured_prior_assumption",
                sensitivity_prior=None if learned else dict(mean=prior, sd=prior_sd, lag_mean=0.0, lag_sd=prior_sd),
                covariance=covariance, transform=dict(TRANSFORM), forecast_available=available,
                training_days=len(daily), eligible_count=eligible, selected_count=selected,
                price_levels=levels, training_start=daily[0]["date"], training_end=latest["date"],
                demand_source=next(iter(sources)), warnings=warnings,
                regularization_precision=[0.01, 1.0, 4.0] if learned else None)


def predict_model(model, pct_change=0.0, horizon=90):
    """Apply price change once, then hold scenario price; lag resets each future day."""
    if isinstance(pct_change, bool) or not isinstance(pct_change, (int, float)) or not math.isfinite(pct_change) or pct_change < -100:
        raise ValueError("pct_change must be finite and >= -100")
    if type(horizon) is not int or not 1 <= horizon <= 90:
        raise ValueError("horizon must be an integer in [1,90]")
    if not model.get("forecast_available"):
        return []
    price = model["last_price"] * (1 + pct_change / 100)
    if not math.isfinite(price):
        raise ValueError("scenario price overflows")
    previous = model["last_price"]
    origin = date.fromisoformat(model["last_date"])
    result = []
    for day in range(1, horizon + 1):
        vector = features(price, previous, model["reference_price"])
        linear = dot(vector, model["coefficients"])
        variance = sum(vector[index] * dot(row, vector) for index, row in enumerate(model["covariance"]))
        margin = 1.96 * math.sqrt(max(0, variance))
        result.append(dict(date=(origin + timedelta(days=day)).isoformat(), day=day,
                           rate=sigmoid(linear), lower=sigmoid(linear - margin), upper=sigmoid(linear + margin)))
        previous = price
    return result


def score(predictions):
    exposure = sum(row["eligible_count"] for row, _ in predictions)
    if exposure == 0:
        return None
    losses, brier, selected, expected = 0.0, 0.0, 0, 0.0
    bins = [dict(eligible_count=0, selected_count=0, expected_count=0.0) for _ in range(5)]
    for row, probability in predictions:
        probability = clip(probability, [1e-12, 1 - 1e-12])
        eligible, successes = row["eligible_count"], row["selected_count"]
        losses -= successes * math.log(probability) + (eligible - successes) * math.log1p(-probability)
        brier += successes * (1 - probability) ** 2 + (eligible - successes) * probability ** 2
        selected += successes
        expected += eligible * probability
        bucket = bins[min(4, int(probability * 5))]
        bucket["eligible_count"] += eligible
        bucket["selected_count"] += successes
        bucket["expected_count"] += eligible * probability
    return dict(eligible_count=exposure, selected_count=selected, log_loss=losses / exposure,
                brier=brier / exposure, observed_rate=selected / exposure,
                predicted_rate=expected / exposure, calibration_error=(expected - selected) / exposure,
                calibration_bins=bins)


def rolling_validation(rows, prior=-1.5, prior_sd=1.0):
    rows = [row for row in rows if row["observation_kind"] == "purchase" and row["eligible_count"] > 0]
    days = sorted({row["date"] for row in rows})
    results = {}
    for horizon in (1, 30, 90):
        candidates = [day for day in days if (date.fromisoformat(day) + timedelta(days=horizon)).isoformat() in days
                      and sum(other <= day for other in days) >= 14
                      and sum(day < other <= (date.fromisoformat(day) + timedelta(days=horizon)).isoformat()
                              for other in days) >= math.ceil(horizon * 0.8)]
        origins = candidates[-3:]
        predictions, pooled_predictions, latest_predictions, evaluated = [], [], [], []
        for origin in origins:
            training = [row for row in rows if row["date"] <= origin]
            fitted = fit_model(training, prior=prior, prior_sd=prior_sd)
            future = predict_model(fitted, horizon=horizon)
            if not future:
                continue
            expected = {point["date"]: point["rate"] for point in future}
            latest = aggregate_daily(training)[-1]
            pooled_rate = (sum(row["selected_count"] for row in training) + 0.5) / (sum(row["eligible_count"] for row in training) + 1)
            latest_rate = (latest["selected_count"] + 0.5) / (latest["eligible_count"] + 1)
            for row in rows:
                if row["date"] in expected:
                    predictions.append((row, expected[row["date"]]))
                    pooled_predictions.append((row, pooled_rate))
                    latest_predictions.append((row, latest_rate))
            evaluated.append(origin)
        results[str(horizon)] = dict(status="validated_rolling_origin" if evaluated else "unvalidated_insufficient_history",
                                     horizon_days=horizon, origins=evaluated,
                                     model=score(predictions), pooled_smoothed_baseline=score(pooled_predictions),
                                     latest_day_smoothed_baseline=score(latest_predictions),
                                     evaluated_dates=sorted({row["date"] for row, _ in predictions}),
                                     policy="last up to 3 chronological origins; >=80% actual horizon days plus endpoint required; unchanged origin price; origins may overlap",
                                     demand_source=rows[0]["demand_source"] if rows else None)
    return results