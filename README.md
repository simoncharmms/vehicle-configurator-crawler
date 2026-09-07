#  Vehicle Configurator Crawler

AI-powered vehicle configurator crawler with multi-brand support, dual-engine scraping, and a GitHub Pages dashboard for price tracking.

## Architecture

```
vehicle-configurator-crawler/
├── crawler/
│   ├── ai_analyzer.py          # Claude-powered page analysis
│   ├── base.py                 # Data models (VehicleData, CrawlConfig, etc.)
│   ├── orchestrator.py         # Runs all crawlers, saves results
│   ├── engines/
│   │   ├── playwright_engine.py    # JS-heavy pages (React/Angular/Vue)
│   │   └── beautifulsoup_engine.py # Static HTML pages
│   └── brands/
│       ├── mercedes.py         # Mercedes-Benz (DE configurator)
│       ├── audi.py             # Audi (DE configurator)
│       ├── porsche.py          # Porsche (DE models page)
│       └── registry.py         # Brand discovery & registration
├── data/prices/                # JSON snapshots (git-tracked)
├── docs/                       # GitHub Pages dashboard
│   ├── index.html
│   ├── style.css
│   └── app.js
├── .github/workflows/
│   └── crawl.yml               # Daily 6 AM CET crawl + deploy
└── tests/
    └── test_crawlers.py
```

## How It Works

### Extraction Strategy

Modern car configurators are complex JS apps, but they embed structured data
in their initial HTML (SSR data, Apollo caches, JSON-LD). The crawlers exploit
this: **no Playwright needed for the primary extraction path**.

| Brand | Method | Data Source |
|-------|--------|-------------|
| **Mercedes-Benz** | Static HTML | SSR navigation data with model names, prices, images |
| **Audi** | Static HTML | Apollo GraphQL cache with carline structure |
| **Porsche** | Static HTML | JSON-LD structured data + model links |

The Playwright engine is still available as a fallback for sites that don't
embed data, or for deep-diving into individual model configurations.

### Dual-Engine Framework

| Engine | Use Case | Method |
|--------|----------|--------|
| **Playwright** | JS-heavy pages requiring full rendering | Headless Chromium, API interception + DOM extraction |
| **BeautifulSoup** | Static/server-rendered pages with embedded data | `curl` fetch + `lxml` parsing |

### AI-Powered Strategy Detection

The `AIAnalyzer` uses Claude to analyze any configurator page and determine:
- Which engine to use (Playwright vs BeautifulSoup)
- CSS selectors for vehicle cards, names, prices
- JavaScript triggers needed before scraping
- Confidence score (0–1)

```python
from crawler.ai_analyzer import analyze_url

result = analyze_url("https://www.audi.de/de/brand/de/neuwagen.html")
print(result.config.engine)     # EngineType.PLAYWRIGHT
print(result.config.selectors)  # {'vehicle_card': '...', 'price': '...'}
print(result.config.confidence) # 0.85
```

### Crawl Strategy

Each brand crawler implements a multi-strategy approach:
1. **Embedded Data** — Parse SSR/Apollo/JSON-LD data from the initial HTML (fastest, most reliable)
2. **API Interception** — Navigate with Playwright, intercept XHR/fetch responses
3. **DOM Extraction** — CSS selector-based extraction from the rendered page
4. **Generic Extraction** — Last resort: scan for any vehicle-like structured data

## Setup

### Prerequisites
- Python 3.11+
- [Anthropic API key](https://console.anthropic.com/) (for AI analyzer)

### Installation

```bash
git clone <repo-url>
cd vehicle-configurator-crawler

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY
```

### Run Locally

```bash
# Crawl all brands
python -m crawler.orchestrator

# Crawl specific brands
python -m crawler.orchestrator --brands mercedes-benz audi

# With debug output
python -m crawler.orchestrator --verbose

# List registered brands
python -m crawler.orchestrator --list-brands
```

### Run Tests

```bash
# Unit tests (no network required)
pytest tests/ -v

# Live integration tests (requires network + Playwright)
pytest tests/ -m live -v
```

## Data Schema

Each crawl produces a JSON file in `data/prices/`:

```
data/prices/
├── index.json                    # Summary index for the dashboard
├── mercedes-benz_2025-01-15.json
├── audi_2025-01-15.json
└── porsche_2025-01-15.json
```

### Vehicle Data Format

```json
{
  "brand": "Mercedes-Benz",
  "timestamp": "2025-01-15T05:00:00+00:00",
  "vehicle_count": 12,
  "vehicles": [
    {
      "brand": "Mercedes-Benz",
      "model": "A-Klasse",
      "variant": "A 180",
      "base_price": 35900.0,
      "currency": "EUR",
      "fuel_type": "petrol",
      "options": [],
      "url": "https://...",
      "image_url": "https://..."
    }
  ],
  "errors": [],
  "strategy": {
    "engine": "playwright",
    "confidence": 0.7
  },
  "duration_seconds": 45.2
}
```

## Adding a New Brand

1. Create `crawler/brands/yourbrand.py`:

```python
from crawler.base import BrandCrawler, CrawlConfig, CrawlResult, EngineType
from crawler.brands.registry import BrandRegistry

@BrandRegistry.register
class YourBrandCrawler(BrandCrawler):
    brand = "YourBrand"
    base_url = "https://www.yourbrand.com"
    configurator_url = "https://www.yourbrand.com/configurator"

    def get_default_config(self) -> CrawlConfig:
        return CrawlConfig(
            engine=EngineType.PLAYWRIGHT,
            selectors={
                "vehicle_card": ".model-card",
                "model_name": "h3",
                "price": ".price",
            },
            wait_selector=".model-card",
        )

    async def crawl(self, config=None) -> CrawlResult:
        # Implement crawl logic (see mercedes.py for reference)
        ...
```

2. Import in `crawler/orchestrator.py`:
```python
import crawler.brands.yourbrand  # noqa: F401
```

3. Run: `python -m crawler.orchestrator --brands yourbrand`

### Using the AI Analyzer for a New Brand

```python
from crawler.ai_analyzer import analyze_url

# Drop in any configurator URL
result = analyze_url("https://www.yourbrand.com/configurator")
print(result.config.to_dict())
# Use the returned selectors in your brand crawler
```

## GitHub Actions

The workflow runs daily at 6:00 AM CET:
1. Installs Python + Playwright
2. Runs the orchestrator for all brands
3. Commits new data to `data/prices/`
4. Deploys the dashboard to GitHub Pages

### Setup

1. Add `ANTHROPIC_API_KEY` as a repository secret
2. Enable GitHub Pages (Settings → Pages → Source: GitHub Actions)
3. The workflow auto-triggers daily; or run manually via Actions → Run workflow

### Manual Trigger

```bash
gh workflow run crawl.yml -f brands="mercedes-benz,audi"
```

## Legal / robots.txt Compliance

| Brand | Status | Notes |
|-------|--------|-------|
| **Mercedes-Benz** |  Allowed | `Allow: /passengercars/content-pool/tool-pages/car-configurator.html*` |
| **Audi** |  Allowed | Only `/userinfo/` disallowed |
| **Porsche** |  Check | robots.txt timed out during initial check; test before committing |
| **BMW** |  Skipped | robots.txt returns 404; configurator ToS unclear |
| **Tesla** |  Skipped | `Crawl-delay: 10`; configurator heavily JS-dependent |

All crawlers implement:
- Respectful rate limiting (≥2s between requests)
- Standard browser User-Agent
- Cookie consent handling
- No authentication bypass

## Dashboard

The dashboard at `docs/` auto-deploys to GitHub Pages:
- Filter by brand, model, time period
- Price trend charts (Chart.js)
- Vehicle cards with current prices
- Dark theme, responsive design

## License

MIT
