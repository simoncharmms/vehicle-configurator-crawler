# Vehicle Configurator Crawler

Automated vehicle price tracker with multi-brand support, network-hardened Playwright fetching, and a GitHub Pages dashboard for daily price tracking.

![BMW M Concept](docs/P90643939_highRes_bmw-m-concept-neue-k.jpg)

## Current Status

| Brand | Status | Vehicles | Notes |
|-------|--------|----------|-------|
| **Mercedes-Benz** | ✅ Active | 100 | Configurator JSON API (real option prices) |
| **Lexus** | ✅ Active | 49 | JSON state blob extraction |
| **Porsche** | ✅ Active | 85 | SPA rendering (networkidle) |
| **BYD** | ✅ Active | 11 | Playwright rendering |
| **Polestar** | ✅ Active | 5 | Model page price probes |
| **XPeng** | ✅ Active | 5 | Model page scraping |
| **Zeekr** | ✅ Active | 4 | Homepage text extraction |
| **Audi** | ⛔ Blocked | 0 | HTTP 403 (was working 2026-09-05) |
| **Volvo** | ⛔ Blocked | 0 | HTTP 403 on all endpoints |

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
| **Porsche** | Playwright + JSON-LD | Structured data + model links |
| **Lexus** | Curl + JSON state | Embedded JSON state blob with full model data |
| **BYD** | Playwright + DOM | JS-rendered model cards with prices |
| **XPeng** | Curl + model pages | "ab" prices from individual model pages |
| **Zeekr** | Curl + homepage | "Ab XX EUR" prices from Nuxt-rendered homepage |
| **Polestar** | Playwright + pages | Model page prices (CDN blocks curl) |

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

# Debug output
python -m crawler.orchestrator --verbose

# List registered brands
python -m crawler.orchestrator --list-brands
```

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
