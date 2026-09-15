"""Orchestrator: runs all brand crawlers, saves results, computes option summaries."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from crawler.base import (
    DEFAULT_MARKET,
    MARKET_NAMES,
    BrandCrawler,
    CrawlResult,
    OptionData,
    currency_for_market,
    snapshot_key,
)
from crawler.option_mappings import (
    OPTION_DEFINITIONS,
    get_category,
    get_category_label,
    get_description,
)
from crawler.brands.registry import BrandRegistry

# Import brand modules to trigger registration
import crawler.brands.mercedes  # noqa: F401
import crawler.brands.audi      # noqa: F401
import crawler.brands.porsche   # noqa: F401
import crawler.brands.lexus     # noqa: F401
import crawler.brands.byd       # noqa: F401
import crawler.brands.xpeng     # noqa: F401
import crawler.brands.zeekr     # noqa: F401
import crawler.brands.polestar  # noqa: F401

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data" / "prices"


async def crawl_brand(crawler: BrandCrawler) -> CrawlResult:
    """Run a single brand crawler with error handling."""
    label = f"{crawler.brand} [{crawler.market}]"
    logger.info(f"Starting crawl for {label}...")
    try:
        result = await crawler.crawl()
        result.market = crawler.market
        vehicle_count = len(result.vehicles)
        option_count = sum(len(v.available_options) for v in result.vehicles)
        if result.vehicles:
            logger.info(
                f"✓ {label}: {vehicle_count} vehicles, "
                f"{option_count} options ({result.duration_seconds:.1f}s)"
            )
        else:
            logger.warning(
                f"✗ {label}: no vehicles extracted "
                f"({result.duration_seconds:.1f}s) — errors: {result.errors}"
            )
        return result
    except Exception as e:
        logger.error(f"✗ {label} failed: {e}")
        return CrawlResult(brand=crawler.brand, market=crawler.market, errors=[str(e)])


def build_crawlers(
    brands: Sequence[str] | None = None,
    markets: Sequence[str] | None = None,
) -> list[BrandCrawler]:
    """Instantiate one crawler per requested brand-market combination.

    Markets a brand does not support are skipped silently; ``markets=None``
    means the default market only, ``markets=["all"]`` every supported market.
    """
    brand_keys = list(brands) if brands else BrandRegistry.list_brands()
    requested = [m.upper() for m in (markets or [DEFAULT_MARKET])]
    want_all = "ALL" in requested

    crawlers: list[BrandCrawler] = []
    for brand in brand_keys:
        supported = BrandRegistry.markets_for(brand)
        targets = list(supported) if want_all else [m for m in requested if m in supported]
        skipped = [] if want_all else [m for m in requested if m not in supported]
        if skipped:
            logger.info(
                f"{brand}: market(s) {', '.join(skipped)} not supported — skipped"
            )
        for market in targets:
            crawlers.append(BrandRegistry.get(brand, market=market))
    return crawlers


async def crawl_all(
    brands: Sequence[str] | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
    sequential: bool = True,
    markets: Sequence[str] | None = None,
) -> list[CrawlResult]:
    """Run crawlers for all (or selected) brands and markets and save results.

    Args:
        brands: Brand names to crawl (None = all registered).
        data_dir: Directory to save JSON snapshots.
        sequential: If True, run brands one at a time (respects rate limits).
        markets: ISO country codes (None = default market, ["all"] = every
            market each brand supports).
    """
    crawlers = build_crawlers(brands, markets)

    if not crawlers:
        logger.error("No brand crawlers registered for the requested markets!")
        return []

    logger.info(
        f"Crawling {len(crawlers)} brand-market combination(s): "
        f"{[f'{c.brand}/{c.market}' for c in crawlers]}"
    )

    results: list[CrawlResult] = []
    if sequential:
        for crawler in crawlers:
            result = await crawl_brand(crawler)
            results.append(result)
            filepath = result.save(data_dir)
            logger.info(f"  Saved: {filepath}")
    else:
        tasks = [crawl_brand(c) for c in crawlers]
        results = await asyncio.gather(*tasks)
        for result in results:
            filepath = result.save(data_dir)
            logger.info(f"  Saved: {filepath}")

    # Summary
    total_vehicles = sum(len(r.vehicles) for r in results)
    total_options = sum(
        len(v.available_options) for r in results for v in r.vehicles
    )
    total_errors = sum(len(r.errors) for r in results)
    logger.info(f"\n{'='*60}")
    logger.info(
        f"Crawl complete: {total_vehicles} vehicles, "
        f"{total_options} options, {total_errors} errors"
    )
    for r in results:
        status = "✓" if r.vehicles else "✗"
        opts = sum(len(v.available_options) for v in r.vehicles)
        logger.info(
            f"  {status} {r.brand} [{r.market}]: {len(r.vehicles)} vehicles, "
            f"{opts} options, {len(r.errors)} errors"
        )
    logger.info(f"{'='*60}")

    # Write summary index (with option summary)
    _write_index(results, data_dir)

    return results


# ------------------------------------------------------------------
# Index & option summary
# ------------------------------------------------------------------

def _write_index(results: list[CrawlResult], data_dir: Path) -> None:
    """Write a summary index.json including cross-brand option summary."""
    index_path = data_dir / "index.json"
    date_str = datetime.now().strftime("%Y-%m-%d")

    # Load existing index
    index: dict = {}
    if index_path.exists():
        with open(index_path) as f:
            index = json.load(f)

    if "brands" not in index:
        index["brands"] = {}
    if "crawl_history" not in index:
        index["crawl_history"] = []

    if "markets" not in index:
        index["markets"] = {}

    for result in results:
        # Consistent brand key: lowercase, spaces → hyphens
        brand_key = result.brand.lower().replace(" ", "-")
        market = (result.market or DEFAULT_MARKET).upper()
        file_key = snapshot_key(brand_key, market)

        option_count = sum(len(v.available_options) for v in result.vehicles)
        snapshot = {
            "date": date_str,
            "file": f"{file_key}_{date_str}.json",
            "market": market,
            "currency": currency_for_market(market),
            "vehicle_count": len(result.vehicles),
            "option_count": option_count,
            "error_count": len(result.errors),
        }

        # Market-scoped tree (all markets, including DE)
        market_entry = index["markets"].setdefault(
            market,
            {
                "code": market,
                "name": MARKET_NAMES.get(market, market),
                "currency": currency_for_market(market),
                "brands": {},
            },
        )
        brand_entry = market_entry["brands"].setdefault(
            brand_key, {"name": result.brand, "snapshots": []}
        )
        if date_str not in [s["date"] for s in brand_entry["snapshots"]]:
            brand_entry["snapshots"].append(snapshot)

        # Legacy flat tree stays the German view so older consumers keep working
        if market == DEFAULT_MARKET:
            if brand_key not in index["brands"]:
                index["brands"][brand_key] = {"name": result.brand, "snapshots": []}
            existing_dates = [
                s["date"] for s in index["brands"][brand_key]["snapshots"]
            ]
            if date_str not in existing_dates:
                index["brands"][brand_key]["snapshots"].append(snapshot)

    index["crawl_history"].append({
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "total_vehicles": sum(len(r.vehicles) for r in results),
        "total_options": sum(
            len(v.available_options) for r in results for v in r.vehicles
        ),
        "brands_crawled": sorted({r.brand for r in results}),
        "markets_crawled": sorted({(r.market or DEFAULT_MARKET) for r in results}),
    })

    # Compute cross-brand option summary from live data only — no fallback.
    # Summaries are per market: prices from different currencies are never mixed.
    by_market: dict[str, list[CrawlResult]] = defaultdict(list)
    for result in results:
        by_market[(result.market or DEFAULT_MARKET).upper()].append(result)

    summaries = index.get("option_summary_by_market") or {}
    for market, market_results in by_market.items():
        summaries[market] = _compute_option_summary(market_results)
    index["option_summary_by_market"] = summaries

    # Legacy key = German summary (dashboard default view)
    if DEFAULT_MARKET in summaries:
        index["option_summary"] = summaries[DEFAULT_MARKET]

    index["available_markets"] = [
        {
            "code": code,
            "name": MARKET_NAMES.get(code, code),
            "currency": currency_for_market(code),
            "brands": sorted(index["markets"][code]["brands"].keys()),
        }
        for code in sorted(index["markets"].keys())
    ]

    index["last_updated"] = datetime.now().isoformat()

    with open(index_path, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    logger.info(f"Updated index: {index_path}")


def _compute_option_summary(results: list[CrawlResult]) -> dict[str, Any]:
    """Build the cross-brand option summary for the dashboard.

    Returns::

        {
            "last_updated": "2026-09-07T...",
            "options": [
                {
                    "standardized_name": "allrad",
                    "display_name": "All-Wheel Drive",
                    "category": "drivetrain",
                    "category_label": "Drivetrain",
                    "brands": {
                        "Mercedes-Benz": {
                            "name": "4MATIC",
                            "avg_price": 1500,
                            "min_price": 1200,
                            "max_price": 1800,
                            "model_count": 5
                        },
                        ...
                    },
                    "overall_avg_price": 1650,
                    "overall_min_price": 1200,
                    "overall_max_price": 2000,
                    "total_model_count": 13
                },
                ...
            ]
        }
    """
    # Bucket: std_name → brand → list[OptionData]
    buckets: dict[str, dict[str, list[OptionData]]] = defaultdict(lambda: defaultdict(list))

    for result in results:
        for vehicle in result.vehicles:
            for opt in vehicle.available_options:
                key = opt.standardized_name or opt.brand_specific_name.lower()
                if key:
                    buckets[key][result.brand].append(opt)

    option_rows: list[dict[str, Any]] = []

    for std_name, brand_map in buckets.items():
        defn = OPTION_DEFINITIONS.get(std_name, {})
        all_prices: list[float] = []
        brands_detail: dict[str, dict] = {}

        for brand, opts in brand_map.items():
            prices = [o.price for o in opts if o.price is not None and o.price > 0]
            names = [o.brand_specific_name for o in opts if o.brand_specific_name]
            brand_name = max(set(names), key=names.count) if names else std_name

            brands_detail[brand] = {
                "name": brand_name,
                "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
                "min_price": min(prices) if prices else None,
                "max_price": max(prices) if prices else None,
                "model_count": len(opts),
            }
            all_prices.extend(prices)

        total_count = sum(len(opts) for opts in brand_map.values())

        option_rows.append({
            "standardized_name": std_name,
            "display_name": defn.get("description", std_name),
            "category": get_category(std_name),
            "category_label": get_category_label(get_category(std_name)),
            "brands": brands_detail,
            "overall_avg_price": (
                round(sum(all_prices) / len(all_prices), 2)
                if all_prices
                else None
            ),
            "overall_min_price": min(all_prices) if all_prices else None,
            "overall_max_price": max(all_prices) if all_prices else None,
            "total_model_count": total_count,
        })

    # Sort by total model count descending, then by name
    option_rows.sort(key=lambda r: (-r["total_model_count"], r["standardized_name"]))

    return {
        "last_updated": datetime.now().isoformat(),
        "options": option_rows,
    }


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    """CLI entry point."""
    import argparse
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(description="Vehicle Configurator Crawler")
    parser.add_argument(
        "--brands", "-b",
        nargs="*",
        help="Specific brands to crawl (default: all)",
    )
    parser.add_argument(
        "--data-dir", "-d",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory for JSON snapshots",
    )
    parser.add_argument(
        "--parallel", "-p",
        action="store_true",
        help="Run brand crawlers in parallel (may hit rate limits)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--markets", "-m",
        nargs="*",
        help=(
            "Markets (ISO country codes) to crawl, e.g. DE FR IT. "
            "Use 'all' for every market a brand supports (default: DE)."
        ),
    )
    parser.add_argument(
        "--list-markets",
        action="store_true",
        help="List supported markets per brand and exit",
    )
    parser.add_argument(
        "--list-brands",
        action="store_true",
        help="List registered brands and exit",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_brands:
        print("Registered brands:")
        for brand in BrandRegistry.list_brands():
            print(f"  - {brand}")
        return

    if args.list_markets:
        print("Supported markets per brand:")
        for brand in BrandRegistry.list_brands():
            markets = ", ".join(BrandRegistry.markets_for(brand))
            print(f"  - {brand}: {markets}")
        return

    results = asyncio.run(
        crawl_all(
            brands=args.brands,
            data_dir=args.data_dir,
            sequential=not args.parallel,
            markets=args.markets,
        )
    )

    # Exit with error if no vehicles extracted from any brand
    if not any(r.vehicles for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
