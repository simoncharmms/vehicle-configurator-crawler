/* Portable artifact inference, independent of dashboard state or dependencies. */
function predictSeries(series, pctChange, horizon) {
    if (typeof pctChange !== "number" || !Number.isFinite(pctChange) || pctChange < -100) {
        throw new Error("pctChange must be finite and >= -100");
    }
    if (!Number.isInteger(horizon) || horizon < 1 || horizon > 90) {
        throw new Error("horizon must be an integer in [1,90]");
    }
    const model = series.model;
    if (!model.forecast_available) return [];
    const transform = model.transform;
    if (transform.version !== 1) throw new Error("Unsupported feature transform");
    const clamp = (value, bounds) => Math.max(bounds[0], Math.min(bounds[1], value));
    const logistic = value => 1 / (1 + Math.exp(-clamp(value, transform.logit_clip)));
    const price = model.last_price * (1 + pctChange / 100);
    if (!Number.isFinite(price)) throw new Error("Scenario price overflows");
    let previous = model.last_price;
    const offset = transform.price_offset;
    const result = [];
    for (let day = 1; day <= horizon; day += 1) {
        const vector = [1,
            clamp(Math.log((price + offset) / (model.reference_price + offset)), transform.relative_clip),
            clamp(Math.log((price + offset) / (previous + offset)), transform.change_clip)];
        const linear = vector.reduce((total, value, index) => total + value * model.coefficients[index], 0);
        const variance = model.covariance.reduce((total, row, index) => total + vector[index] *
            row.reduce((subtotal, value, column) => subtotal + value * vector[column], 0), 0);
        const margin = transform.interval_z * Math.sqrt(Math.max(0, variance));
        const future = new Date(model.last_date + "T00:00:00Z");
        future.setUTCDate(future.getUTCDate() + day);
        result.push({date: future.toISOString().slice(0, 10), day: day,
            rate: logistic(linear), lower: logistic(linear - margin), upper: logistic(linear + margin)});
        previous = price;
    }
    return result;
}