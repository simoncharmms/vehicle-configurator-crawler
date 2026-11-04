# Option Take-Rate Intelligence V1

This offline backend uses Python 3.11+ standard library only. There is no ML
dependency or intelligence extra. It never writes to the configurator archive.
Tests require `python -m pip install pytest`; use a local virtual environment.

## Commands

```sh
python scripts/build_pricing_intelligence.py --source synthetic --markets all --seed 42
python scripts/build_pricing_intelligence.py --source observed --observations orders.csv.gz
python scripts/build_pricing_intelligence.py --source observed
python scripts/build_pricing_intelligence.py --repack --source synthetic
```

The observed command without observations explicitly publishes empty observed demand, not synthetic
substitution. `--prices-dir`, `--output-dir`, `--demand-dir`, and `--config` accept
alternate paths. `--markets` accepts `all` or comma-separated ISO codes. All known
markets are retained by `all`, including markets with no usable prices. A subset
rebuild preserves other existing market endpoints. Its provenance/cohort export
covers only `scope_markets` of the newest revision. `--repack` reads the published
source's existing market records and cohort exports, without loading the price
archive, simulation, model fitting, or rolling evaluation. It handles legacy full
JSON and sharded gzip layouts. It preserves all full records and provenance and
streams CSV rows into market partitions without numeric conversion. If a full
record lacks `vehicle_model`, repack streams the existing cohort CSVs once to
recover its exact vehicle name via `series_id` -> `model`, rejecting conflicting,
blank or missing labels before publication. This is the only full-record addition;
prediction models, counts, histories and provenance are unchanged. Revisions
already containing vehicle names skip this recovery scan. Repack always
covers all existing endpoints of the selected source; observations, market and
seed overrides are rejected. Normal builds log loading, simulation, each market
and every 1000 fitted series to stderr; repacks log each market and cohort export.

These commands process actual local history; tests instead use tiny temporary
fixtures and do not produce full archive artifacts. No network is used by builds.

## Publication

Resolve `data/intelligence/manifest.json`, schema version 1. Each entry under
`sources.synthetic` or `sources.observed` has `markets`, mapping ISO codes to
relative payload paths, plus `publication_layout`, `observations_by_market`,
`provenance_path`, `revision`, and `scope_markets`. All paths, including detail
paths inside market payloads, are relative to the manifest directory, NOT the
market URL. Example source metadata (schema version remains 1):

```json
{"schema_version":1,"sources":{"synthetic":{"schema_version":1,"publication_layout":"sharded-gzip-v1","markets":{"DE":"synthetic/REVISION/DE.json.gz"},"observations_by_market":{"DE":"../demand/synthetic/REVISION/DE.csv.gz"},"observations_path":"../demand/synthetic/REVISION/DE.csv.gz","provenance_path":"../demand/synthetic/REVISION/provenance.json.gz","revision":"REVISION","scope_markets":["DE"]}}}
```

`observations_path` is provided only for a single selected market and aliases
that market's export. It is absent for multi-market publications: use
`observations_by_market[market]`. Subset builds retain other market endpoints and
existing per-market cohort mappings. Provenance describes the newest scope.
Legacy revisions without `publication_layout` remain readable as full JSON with
an aggregate `observations_path`; consumers must detect the layout, not guess.
Other demand sources are never changed by a build or repack.

Revisions are immutable. Complete output and canonical per-market demand CSV gzip are staged
on their respective filesystems, checked for valid finite JSON and strict demand
schema, and renamed into unique revision directories. An atomic `os.replace` of
the top manifest commits publication last. Failure leaves previous files and
manifest untouched; caught failures clean their own unreferenced revisions. Abrupt process
termination may leave unreferenced directories, never a partial published
revision. Old revisions are not garbage collected. Concurrent writers are not
supported; serialize builds. Resolve the manifest anew to discover updated data.
The publisher does not delete old revisions. Explicit generated-data cleanup is
safe only after a successful commit and after checking all manifest source,
cohort, provenance and summary detail references. Never delete observed data as
part of synthetic cleanup.

Gzip files use deterministic headers (`mtime=0`, no filename) and finite-only
JSON serialization. Indexes are capped at 5,000,000 compressed bytes. Detail
shards contain at most 50 series and are capped at 1,000,000 compressed bytes.
Oversized output rejects publication rather than truncating any series. All
full records, metrics, history, forecasts and verbose model fields are preserved
in details. Provenance is gzip JSON too, preserving the complete diagnostics
without an oversized uncompressed file. Cohorts are per-market gzip CSV, with
the original header and cell strings (including quoted flags) retained on repack.

## Browser Loading

Gzip is file content, not an assumed HTTP Content-Encoding. Static servers must
serve `.gz` bytes as-is. Decompress with the browser's native
`DecompressionStream('gzip')`; do not call `response.json()` on compressed bytes:

```js
async function loadPayload(manifestUrl, relativePath) {
  const response = await fetch(new URL(relativePath, manifestUrl));
  if (!response.ok) throw new Error(`Artifact HTTP ${response.status}`);
  if (!relativePath.endsWith('.gz')) return response.json();
  const stream = response.body.pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).json();
}
```

`manifestUrl` must be an absolute URL. The market index has the existing market
metadata (`schema_version`, `market`, `currency`, `demand_source`, `generated_at`,
`as_of`, `assumptions`, `coverage`, `status`) plus
`publication_layout: 'sharded-gzip-v1'`, `model_transform` and compact `series`.
Each summary retains these fields when present in the original full record:

```text
id,brand,vehicle_model,variant,option_code,option_name,standardized_name,category,currency,
latest_price,base_price,take_rate,eligible_count,selected_count,price_change_pct,
coverage,as_of,warnings,detail_path
model: {forecast_available,reference_price,last_price,last_date,coefficients,
        covariance,sensitivity_source,kind,training_days?}
```

Before calling `predictSeries`, inject the shared transformation:
`series.model.transform = payload.model_transform`. Compute arbitrary scenarios
and ranking forecasts from these parameters; indexes intentionally omit
`history`, `forecast`, `metrics` and all other verbose model fields. For a selected
series fetch its `detail_path`, cache the shard, and find its full record by `id`.
A detail shard is `{schema_version:1, market, demand_source, series:[...]}`.
All records are original full objects; never merge different demand sources.
Empty/no-observed-data markets still have valid indexes and header-only exports;
`model_transform` may be null when no model exports a transformation. No forecast
is available unless `model.forecast_available` is true.

## Full Detail Records

Each market payload has exactly these primary fields (additional descriptive
metadata may be added without changing schema version). This is the legacy full
payload format; in sharded publications the series objects below live in detail
shards instead of the index:

```text
schema_version: 1
market, currency, demand_source, generated_at, as_of
assumptions: string[]
coverage: {priced_series,purchase_series,forecast_series,cohort_rows,
           inventory_rows,zero_exposure_rows}
status: ready | no_observed_data | no_usable_prices
series: [{id,brand,vehicle_model,variant,option_code,option_name,standardized_name,category,
          currency,latest_price,base_price,
          history:[{date,price,demand_price,eligible_count,selected_count,take_rate}],
          model,forecast:[{date,day,rate,lower,upper}],metrics,warnings,
          eligible_count,selected_count,take_rate,price_change_pct,coverage,as_of,
          observation_kinds,price_sources,history_limit_points}]
```

`vehicle_model` is the exact catalogue vehicle name string in both summaries and
full records. `model` is exclusively the prediction-model object, including its
`coefficients`; it is never a vehicle label.

`history` is limited to 90 available dated points, not imputed calendar days.
Counts and summary take rates cover the entire imported purchase history, not
only displayed points. `price` is the dated catalogue price when available;
`demand_price` is the exposure-weighted transaction/cohort price on that date.
Same-day conflicting **catalogue** prices are quarantined, never averaged.
Counts/rates are null where no purchase cohort exists; zero exposure gives a null
rate, not 0%. `latest_price` is the latest usable catalogue price. Forecast origins
are per series (latest purchase or catalogue day), not necessarily the same as the
market's `as_of`. `training_end` remains the last positive purchase-exposure day.
Stale purchase/catalogue contexts produce warnings. Inventory contributes only to
coverage/provenance and is never fitted. Forecast arrays have 90 calendar days,
or are empty when the minimum forecast gate is unmet. Slice to 30 for that view.

## Catalogue Policy And Identity

Input files contain a run object or run list. Run dates come from timestamps,
normalized to UTC; historical naive timestamps are interpreted as UTC. Ignore
any errors, explicit partial/non-success status, empty vehicles or a declared
vehicle-count mismatch. Choose the latest successful run per market/brand/day;
timestamp ties break deterministically by filename and list position. No fallback
to a failed run and no missing-day carry-forward. Diagnostics are persisted in
the provenance report. Malformed JSON aborts publication rather than silently
dropping an unreadable file.

Identity hashes the exact tuple `(market,brand,model,variant,option_code)`, with
`label:option_name` replacing absent codes. Standardized feature names are not
keys. Exact duplicate daily records collapse; conflicting identity records are
quarantined for that day. Historical generation/provider identities cannot be
recovered, so all keys are flagged `historical_identity_unverified`; code-less
keys additionally carry `label_identity_fallback`. Currency must match the known
market at run, vehicle and option level. Omitted legacy currencies inherit the
market currency. Boolean, nonnumeric, nonfinite and negative prices are skipped;
explicit numeric zero is valid, never a substitute for missing or failed data.

## Canonical Demand Import

CSV, CSV gzip (`.csv.gz`) and JSON arrays use the same required columns:

```text
schema_version,date,cohort_id,series_id,market,currency,brand,model,variant,
option_code,option_name,standardized_name,price,base_price,eligible_count,
selected_count,demand_source,observation_kind,price_source,source_id,quality_flags
```

Use the exported `series.id` for an **exact join**; copy the matching market,
currency, brand, variant, code, name and standardized name. Set the canonical
demand row's string `model` from `series.vehicle_model`, never `series.model`
(the prediction object). The canonical `DEMAND_FIELDS` remain unchanged. A synthetic
cohort export provides a convenient column/mapping template, not observed data.
Replace every demand record and provenance field with your own aggregate data.
JSON fields use native numbers and `quality_flags` arrays. CSV stores flags as a
JSON array in the quoted field (e.g. `[]`), an optional base price as blank, and
counts/schema_version as unsigned integer digits only. Example JSON row:

```json
{"schema_version":1,"date":"2026-01-01","cohort_id":"orders_daily",
 "series_id":"COPY_EXPORTED_ID","market":"DE","currency":"EUR",
 "brand":"COPY_BRAND","model":"COPY_MODEL","variant":"COPY_VARIANT",
 "option_code":"COPY_CODE","option_name":"COPY_NAME","standardized_name":"COPY_NAME",
 "price":100,"base_price":null,"eligible_count":40,"selected_count":8,
 "demand_source":"observed","observation_kind":"purchase",
 "price_source":"observed","source_id":"orders_export_001","quality_flags":[]}
```

Dates must be exact `YYYY-MM-DD`. Counts cannot be floats/bools, cannot be
negative, and selected cannot exceed eligible. Duplicate date+series+cohort keys,
unknown IDs, metadata/currency mismatches, invalid prices/sources/flags are
rejected. Errors identify the row and cause; no rows are imported on failure.
Zero-exposure records are retained but cannot train a model. `demand_source` must
match the selected source. `observation_kind` is `purchase` or `inventory`.
`price_source` is `scraped`, `scenario`, or `observed`. Scraped prices and optional
base prices must match that exact dated catalogue. Explicitly observed
transaction prices need not match catalogue prices or catalogue dates. Scenario
prices likewise bypass the catalogue price match and remain labelled. Never
convert scenario/synthetic rows into observed purchases by relabelling them.

## Simulation

`data/demand/synthetic_config.json` records version, seed, arbitrary exposure
bounds (20-80), negative price sensitivity, affinity, and autocorrelated preference
shock parameters. Each series has a stable SHA-256-derived RNG seed. Sorted dated
catalogue rows drive daily binomial cohorts without fabricated future history or
missing-day fills. Appending future observations or adding another series does
not alter a series's earlier simulation. Cohorts are aggregate simulated exposure,
not estimates of registrations or sales. Option eligibility is explicitly assumed
in simulation, not inferred from availability. Config/seed/version and price vs
demand provenance accompany every compressed cohort revision.

## Model, Gates And Intervals

The target is binomial take rate. A per-series regularized logistic likelihood
uses `selected_count` successes and `eligible_count-selected_count` failures
directly. This is equivalent to two weighted class rows, without fake individual
customers. Ridge precision is `[0.01,1,4]` for intercept, relative price and recent
change. Newton updates use a line search. No coefficient signs are flipped.

Forecasting needs >=7 distinct positive-exposure days and >=100 exposures.
Learning sensitivity additionally requires >=14 days, >=200 exposures, >=3 price
levels and price spread >=max(1 currency unit,2% of reference price). Otherwise
use the Jeffreys-smoothed pooled rate `(selected+0.5)/(eligible+1)`, a configured
negative price prior and zero lag-effect prior. Sensitivity is explicitly marked
`configured_prior_assumption`, even when enough days exist but prices are flat.
Observed fallback priors use the configured price-effect and sensitivity SD too;
they do not become observed evidence. No usable purchases means no forecast.
Serial demand shocks and missing eligibility information prevent causal claims.

Three features only are exported. Trend is omitted to avoid weak extrapolation;
the option/base-price ratio is omitted because it is redundant for fixed base
prices. A currency-unit offset of 1 makes log features well-defined for free
options; it is not currency conversion. Large scenarios are clipped in feature
space and must be presented as extrapolations, not reliable elasticity estimates.

For learned models, `covariance` is the inverse penalized binomial Hessian at the
fit. This is a local Gaussian/Laplace posterior approximation conditional on ridge
priors, independent binomial cohorts, fixed design and model correctness. Fallback
covariance is diagonal: intercept variance `1/(selected+0.5)+1/(unselected+0.5)`,
price and lag variance `sensitivity_prior_sd^2`. Lag prior mean is zero. Bands are
`sigmoid(linear +/- 1.96*sqrt(x' covariance x))`. They are approximate conditional
expected-rate intervals, not a coverage guarantee, causal-effect interval or
future-count predictive interval. Dependence, prior uncertainty/misspecification,
sampling bias and future shocks are not fully captured. Synthetic intervals
describe the simulation only. Paired scenarios share parameters/covariance; do
not subtract separate marginal bounds to claim a scenario-effect interval.

## Exact Browser Transform

The dependency-free reference implementation is `portable.js` in this package:
`predictSeries(series, pctChange, horizon)`, returning forecast-shaped points.
No frontend files need modification to test it. Coefficients include the intercept
as their **first entry**; `model.intercept` is descriptive, not added a second time.

```text
price = last_price * (1 + pctChange / 100)       # finite pctChange >= -100
previous = last_price
for day = 1..horizon:                          # integer 1..90
  x = [1,
       clamp(log((price+1)/(reference_price+1)), -2, 2),
       clamp(log((price+1)/(previous+1)), -1, 1)]
  linear = sum(x[i] * coefficients[i])
  se = sqrt(max(0, sum(x[i]*covariance[i][j]*x[j])))
  rate = sigmoid(clamp(linear,-35,35))
  lower/upper = sigmoid(clamp(linear -/+ 1.96*se,-35,35))
  date = last_date + day calendar days in UTC
  previous = price
```

Training recent change uses the previous available positive purchase-exposure
day, never future rows. `lag_price` is exported historical context; forecast day
one uses `last_price`, not that historical lag. The price shock happens once;
recent change becomes zero from day two. `reference_price` is fitted on training
history only, never on future records. `forecast_available=false` returns `[]`.
Intervals apply the same features as the mean; all clipping constants and the
version live in `model.transform`. Warn on scenarios outside training price range.

## Chronological Evaluation

Rolling-origin backtests evaluate horizons 1, 30 and 90. Use up to three latest
eligible chronological origins with at least 14 past exposure days. Require an
actual endpoint and >=80% of future calendar days with positive purchase exposure
before marking a horizon validated. Fit transformations/model only on rows on or
before each cutoff; assume the origin price stays unchanged. Score all available
holdout cohorts within the horizon, not future realized prices as model inputs.
Origins may overlap, so their counts are evaluation exposures, not unique buyers.
Report origin dates and evaluated dates. Ninety-day projections remain explicitly
unvalidated until actual history qualifies.

Each horizon reports model, latest-day smoothed baseline and pooled per-series
smoothed baseline: exposure-weighted Bernoulli log loss, individual-binomial Brier
`[selected*(1-p)^2 + unselected*p^2]/eligible`, observed/predicted rates, signed
calibration error and five exposure-weighted calibration bins. These validate
pipeline mechanics on synthetic demand, not real-world predictive accuracy.