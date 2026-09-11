# Vehicle Configurator Crawler

Automated vehicle price tracker with multi-brand support, network-hardened Playwright fetching, and a GitHub Pages dashboard for daily price tracking.

![BMW M Concept](docs/P90643939_highRes_bmw-m-concept-neue-k.jpg)

## Current Status

| Brand | Status | Vehicles | Notes |
|-------|--------|----------|-------|
| **Mercedes-Benz** | ✅ Active | 45 | SSR data extraction |
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

### Dashboard Snapshot (2026-09-10)

- **7 brands** with vehicle data and **204 vehicle models**
- **441 options tracked**, including **24 option rows with numeric prices**
- **Average option price shown:** €5,700
- **Highest observed priced entries:** one-time payment €23,805; Weissach Package €11,965; Porsche Ceramic Composite Brake (PCCB) €7,914
- Prices are daily snapshots from official configurators. Many tracked equipment labels do not currently include a numeric price.

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
│   └── brands/
│       ├── mercedes.py         # Mercedes-Benz (Playwright + SSR data)
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
| **Mercedes-Benz** | Playwright + SSR | SSR navigation data with prices, images |
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
| **Mercedes-Benz** | ✅ Allowed | `Allow: /passengercars/content-pool/tool-pages/car-configurator.html*` |
| **Audi** | ✅ Allowed | Only `/userinfo/` disallowed |
| **Porsche** | ⚠️ Check | robots.txt timed out during initial check |
| **Lexus** | ✅ Allowed | No specific blocks on `/modelle` |
| **BYD** | ✅ Allowed | No specific blocks |
| **XPeng** | ✅ Allowed | No specific blocks on `/de/model/` |
| **Zeekr** | ✅ Allowed | No specific blocks |
| **Polestar** | ✅ Allowed | No specific blocks on `/de/` model pages |

All crawlers: respectful rate limiting (≥3s), standard browser UA, no auth bypass.

## License

MIT
