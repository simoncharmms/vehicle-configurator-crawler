# Vehicle Configurator Crawler

Automated vehicle price tracker with multi-brand support, network-hardened Playwright fetching, and a GitHub Pages dashboard for daily price tracking.

![BMW M Concept](docs/P90643939_highRes_bmw-m-concept-neue-k.jpg)

## Current Status

| Brand | Status | Markets | Notes |
|-------|--------|---------|-------|
| **Mercedes-Benz** | ✅ Active | 17 — DE, AT, CH, FR, IT, ES, PT, NL, BE, LU, PL, CZ, SK, HU, RO, DK, GB | Configurator JSON API (real option prices) |
| **Porsche** | ✅ Active | 12 — DE, AT, CH, FR, IT, ES, NL, BE, PL, GB, SE, NO | Playwright configurator rendering, locale-aware price parsing |
| **XPeng** | ✅ Active | 8 — DE, AT, BE, DK, FR, NL, NO, SE | Configurator API (`carSpecificationGroup/list`) |
| **Zeekr** | ✅ Active | 3 — DE, NL, SE | Shop API (`queryCarModels` / `queryOptions`) |
| **Polestar** | ✅ Active | 2 — DE, SE | Configurator API (`car-configurator-back`) |
| **BYD** | ✅ Active | 2 — DE, NL | Public CMS configurator catalogue |
| **Lexus** | ✅ Active | 2 — DE, AT | Texus API (colours, wheels, upholstery, packs, options, accessories) |
| **Volkswagen** | ⚠️ Limited | 3 — DE, US, CN | US MSRP extraction + CN model catalogue (no stable public CN list prices) |
| **Range Rover** | ⚠️ Limited | 2 — DE, US | Public landing-page base list prices |
| **Audi** | ⛔ Blocked | DE | HTTP 403 (was working 2026-09-05) |
| **Volvo** | ⛔ Blocked | – | HTTP 403 on all endpoints |

Every crawl is stored per brand **and** market, prices are never converted between
currencies, and the dashboard has a country selector that switches the whole view
(stats, option table, charts, vehicles) to the selected market's currency.

Market metadata now includes **US** and **CN** currency support at framework level;
brand-market activation remains gated by verified extraction quality and compliance.

- **Dashboard:** https://simoncharmms.github.io/vehicle-configurator-crawler
- **Update Frequency:** Daily at 6:00 AM CET
- **Data Policy:** Only real extracted data — no reference or fallback pricing

### Dashboard Snapshot (2026-09-11)

- **7 brands** with vehicle data and **204 vehicle models**
- **439 options tracked**, including **23 option rows with numeric prices**
- The dashboard comparison currently uses **Mercedes-Benz and Porsche**, the only brands with comparable numeric option prices.
- **Shared priced categories:** leather seats (Mercedes €1,760; Porsche €1,107), rear/360° camera (Mercedes €547; Porsche €1,166), and premium sound (Mercedes €2,522; Porsche €1,178).
- Prices are daily snapshots from official configurators. Many tracked equipment labels do not currently include a numeric price.

### Mercedes-Benz: from HTTP 403 to real option prices (2026-09-14)

Crawls from 2026-09-09 onward returned `HTTP 403` because `www.mercedes-benz.de`
blocks datacenter IPs (sandboxes, GitHub Actions runners). On top of that, the
old option path (`startPage.preConfigs[].curatedComponents[]`) no longer exists
in the current payload, so even a successful page load would have produced zero
option prices. Separately, the daily workflow had been failing since 2026-09-11
because `requirements.txt` was removed in the `cleanup` commit.

The crawler now talks to the configurator's own JSON API
(`api.oneweb.mercedes-benz.com/owcc-backend/api/v3/de_DE/CCci/{sessionId}/…`),
which answers plain `requests` calls without a browser and without the 403 wall:

1. `entry?typeClass=<TC>` → all motorizations with base prices, fuel type, image
2. `entry?typeClass=<TC>&vehicleId=<id>` → `selectableComponents` with the real
   gross/net option prices

Measured full run (36 type classes, 2026-09-14): **100 motorizations, 8,256
priced options, ~38 s, 0 errors** — previously 0 vehicles and 0 prices.

### Brands Tested But Not Added

| Brand | Reason |
|-------|--------|
| **Tesla** | HTTP 403 — anti-bot protection blocks all access |
| **Volvo** | HTTP 403 — Access Denied on all pages (curl + Playwright) |
| **Land Rover** | No structured price data in static or rendered HTML |
| **Jaguar** | No new-car pricing on model pages (brand transitioning) |

## Architecture

```
vehicle-configurator-crawler/
├── crawler/
│   ├── ai_analyzer.py          # Claude-powered page analysis
│   ├── base.py                 # Data models (VehicleData, CrawlConfig, etc.)
│   ├── network.py              # Network resilience (retries, backoff, browser pool)
│   ├── orchestrator.py         # Runs all crawlers, saves results
│   ├── engines/
│   │   ├── playwright_engine.py
│   │   └── beautifulsoup_engine.py
│   ├── data/
│   │   └── mercedes_type_classes.json   # Mercedes type-class catalogue (W206, V297, …)
│   └── brands/
│       ├── mercedes.py         # Mercedes-Benz (configurator JSON API)
│       ├── audi.py             # Audi (Curl + Apollo GraphQL cache)
│       ├── porsche.py          # Porsche (Playwright + JSON-LD)
│       ├── lexus.py            # Lexus (Curl + embedded JSON state)
│       ├── byd.py              # BYD (Playwright + DOM extraction)
│       ├── xpeng.py            # XPeng (Curl + model page scraping)
│       ├── zeekr.py            # Zeekr (Curl + homepage text extraction)
│       ├── polestar.py         # Polestar (Playwright + model pages)
│       ├── volkswagen.py       # Volkswagen (public model pages / CN model catalogue)
│       ├── range_rover.py      # Range Rover (public market landing pages)
│       └── registry.py         # Brand discovery & registration
├── data/prices/                # JSON snapshots (git-tracked)
├── docs/                       # GitHub Pages dashboard
├── .github/workflows/
│   └── crawl.yml               # Daily 6 AM CET crawl + deploy
└── tests/
```

## Extraction Strategy

| Brand | Method | Data Source |
|-------|--------|-------------|
| **Mercedes-Benz** | Requests + JSON API | Configurator API `entry` endpoint: base prices + `selectableComponents` prices |
| **Audi** | Curl + Apollo | GraphQL cache with prices (Sec-Fetch headers) |
| **Porsche** | Playwright + configurator DOM | Model links per market + configurator option prices (locale number formats) |
| **Lexus** | Requests + Texus API | `getColourInfo`, `getCarWheels`, `getUpholsteries`, `getPacks`, `getOptionalEquipment`, `getOptionalAccessories` |
| **BYD** | Requests + CMS API | `cms-api.byd.com/car/byd/{market}/{model}.json` — trims, colours, interior, wheels |
| **XPeng** | Requests + configurator API | `store.xpeng.com/api/carSpecificationGroup/list` per car version |
| **Zeekr** | Requests + shop API | `gateway-pub-azure.zeekr.eu` `queryCarModels` / `queryOptions` |
| **Polestar** | Requests + configurator API | `pc-api.polestar.com/.../configurator/api/v2/configuration` feature groups |
| **Volkswagen** | Requests + market model pages | VW US `models.html` MSRP text + VW CN `CarModelList` links |
| **Range Rover** | Requests + market landing pages | `rangerover.com/{market}/index.html` model-card base list prices |

## Setup

### Prerequisites
- Python 3.11+
- [Anthropic API key](https://console.anthropic.com/) (for AI analyzer, optional)

### Installation

```bash
git clone <repo-url>
cd vehicle-configurator-crawler

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

### Run Locally

```bash
# Crawl all brands
python -m crawler.orchestrator

# Specific brands
python -m crawler.orchestrator --brands mercedes-benz lexus byd

# Specific markets (default: DE)
python -m crawler.orchestrator --brands mercedes-benz --markets DE FR PL

# Every market each brand supports
python -m crawler.orchestrator --markets all

# Debug output
python -m crawler.orchestrator --verbose

# List registered brands / supported markets per brand
python -m crawler.orchestrator --list-brands
python -m crawler.orchestrator --list-markets

# Validate newly generated snapshots before publishing
python scripts/validate_crawl_results.py data/prices --date "$(date +%Y-%m-%d)" --check-index --strict

# Validate robots.txt compliance posture for crawl entry points
python scripts/check_compliance_preflight.py --strict

# Run deterministic offline test suite
pytest tests/ -v -m "not live"

# Optional: run live network integration tests
pytest tests/ -v --run-live -m live
```

Snapshots are written as `data/prices/{brand}_{date}.json` for Germany and
`data/prices/{brand}_{market}_{date}.json` for every other market. `index.json`
carries `markets`, `option_summary_by_market` and `available_markets` next to the
legacy German keys, so older dashboard builds keep working.

## Option Pricing Intelligence

The dashboard includes source-separated option demand projections for all markets
with usable archived prices. Prices remain extracted configurator data; synthetic
exposures, selections and eligibility are simulated demonstration assumptions,
not actual sales or measured customer preferences. No currency conversion is used.

Generate the reproducible synthetic cohort dataset and dashboard artifacts:

```bash
python scripts/build_pricing_intelligence.py --source synthetic --markets all --seed 42
python -m http.server 8765 --bind 127.0.0.1
# Open http://127.0.0.1:8765/docs/
```

No additional runtime dependencies are required. The build reads the archive
without changing it, writes compressed aggregate cohorts under `data/demand/`,
and publishes versioned market artifacts through `data/intelligence/manifest.json`.
The deployment workflow rebuilds the synthetic demonstration without committing
generated cohorts or analytics to git. Analytics failure does not prevent
deployment of the price dashboard; a fresh runner may then have no analytics.
Market indexes and selected-option detail shards are gzip-compressed and loaded
on demand. A current browser with `DecompressionStream` support is required.

The Pages build also compresses the price archive without changing its source:

```bash
python scripts/build_dashboard_site.py
```

This writes `_site/` and enforces a 900 MiB publication budget. Run the same HTTP
server and open `http://127.0.0.1:8765/_site/` to verify the deployable version.

### Forecasts And Price Scenarios

The target is the fraction of eligible option exposures selected. A regularized
binomial logistic model uses relative option price and recent price changes.
Where history or price variation is insufficient, it uses a smoothed historical
rate with an explicitly configured sensitivity prior, not a learned elasticity.
The dashboard compares unchanged-price and user-entered price scenarios over 30
or 90 days. Forecast paths are conditional on that price assumption, not automatic
forecasts of future configurator prices. Without further changes or external
drivers, rates can remain flat after the initial price shock.

Intervals are approximate model-conditional expected-rate bands, not guarantees
or future purchase-count intervals. Synthetic backtests validate simulation
mechanics only. Ninety-day projections remain unvalidated until enough actual
history exists. Associations and assumed sensitivity are not causal price effects.

### Replace Simulation With Observations

Use the exported synthetic cohort CSV as a **schema/mapping template only**. Join
your own aggregate purchase counts to the exported `series.id`; replace all
synthetic records, source identifiers and provenance with actual observations.
Do not relabel synthetic rows as observed. The complete columns, example record,
identity rules and validation requirements are in
[the intelligence contract](crawler/intelligence/CONTRACT.md).

```bash
python scripts/build_pricing_intelligence.py --source observed --observations orders.csv.gz --markets all
python -m pytest tests/test_pricing_intelligence.py -q
```

CSV, gzip CSV and JSON arrays are supported. Imports validate dates, counts,
identities, source labels, currencies and duplicate cohorts before publication.
Observed transaction prices can differ from catalogue prices. Zero exposure is
unknown take rate, not zero demand. Inventory observations are excluded from
purchase models. Observed mode never fills gaps with simulated observations;
select **Observed purchases** in the dashboard after a successful import.

**Publication warning:** GitHub Pages publishes the generated `data/` directory,
including cohort archives. Import only aggregate data you are authorized to make
public, never customer identifiers or confidential sales exports. Changing this
to a private deployment is necessary before using confidential observations.
Generated revisions are immutable and currently retained locally; serialize
builds and monitor storage growth. Source-specific market cohort archives are
gzip CSV. Generated analytics and demand revisions are git-ignored; a clean
checkout needs a build. Daily deployment uses a fresh runner, and published
observed datasets require a separately authorized deployment process, not the
synthetic-only daily job. Unsupported or incomplete series remain flagged.

## robots.txt Compliance

| Brand | Status | Notes |
|-------|--------|-------|
| **Mercedes-Benz** | ✅ Allowed | Configurator allowed; `api.oneweb.mercedes-benz.com` serves no robots.txt (404) — public configurator backend, rate-limited to ≤4 parallel requests |
| **Audi** | ✅ Allowed | Only `/userinfo/` disallowed |
| **Porsche** | ⚠️ Check | robots.txt timed out during initial check |
| **Lexus** | ✅ Allowed | No specific blocks on `/modelle` |
| **BYD** | ✅ Allowed | No specific blocks |
| **XPeng** | ✅ Allowed | No specific blocks on `/de/model/` |
| **Zeekr** | ✅ Allowed | No specific blocks |
| **Polestar** | ✅ Allowed | No specific blocks on `/de/` model pages |
| **Volkswagen** | ⚠️ Partial | US model page is scrapeable; DE may be consent-wall dependent |
| **Range Rover** | ⚠️ Partial | Public market landing pages are scrapeable; site enforces consent banners |

Compliance is checked automatically in CI via `scripts/check_compliance_preflight.py`
before each crawl run; explicit robots disallow rules block publication.

All crawlers: respectful rate limiting, standard browser UA, no auth bypass. The
Mercedes API path uses bounded concurrency (`MERCEDES_MAX_CONCURRENCY`, default 4)
and a 0.25 s per-request delay instead of the ≥3 s page-scraping delay, since it
fetches small JSON documents rather than rendering full pages.

### Mercedes environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MERCEDES_MAX_CONCURRENCY` | 4 | Parallel API requests |
| `MERCEDES_MAX_OPTION_PROBES` | 160 | Cap on per-vehicle option lookups per run |

## License

MIT
