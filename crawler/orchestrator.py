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

from crawler.base import BrandCrawler, CrawlResult, OptionData
from crawler.option_mappings import (
    OPTION_DEFINITIONS,
    get_category,
    get_category_label,
    get_description,
    get_reference_option_summary,
)
from crawler.brands.registry import BrandRegistry

# Import brand modules to trigger registration
import crawler.brands.mercedes  # noqa: F401
import crawler.brands.audi      # noqa: F401
import crawler.brands.porsche   # noqa: F401

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data" / "prices"


async def crawl_brand(crawler: BrandCrawler) -> CrawlResult:
    """Run a single brand crawler with error handling."""
    logger.info(f"Starting crawl for {crawler.brand}...")
    try:
        result = await crawler.crawl()
        vehicle_count = len(result.vehicles)
        option_count = sum(len(v.available_options) for v in result.vehicles)
        if result.vehicles:
            logger.info(
                f"✓ {crawler.brand}: {vehicle_count} vehicles, "
                f"{option_count} options ({result.duration_seconds:.1f}s)"
            )
        else:
            logger.warning(
                f"✗ {crawler.brand}: no vehicles extracted "
                f"({result.duration_seconds:.1f}s) — errors: {result.errors}"
            )
        return result
    except Exception as e:
        logger.error(f"✗ {crawler.brand} failed: {e}")
        return CrawlResult(brand=crawler.brand, errors=[str(e)])


async def crawl_all(
    brands: Sequence[str] | None = None,
    data_dir: Path = DEFAULT_DATA_DIR,
    sequential: bool = True,
) -> list[CrawlResult]:
    """Run crawlers for all (or selected) brands and save results.

    Args:
        brands: Brand names to crawl (None = all registered).
        data_dir: Directory to save JSON snapshots.
        sequential: If True, run brands one at a time (respects rate limits).
    """
    if brands:
        crawlers = [BrandRegistry.get(b) for b in brands]
    else:
        crawlers = BrandRegistry.all()

    if not crawlers:
        logger.error("No brand crawlers registered!")
        return []

    logger.info(f"Crawling {len(crawlers)} brand(s): {[c.brand for c in crawlers]}")

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
            f"  {status} {r.brand}: {len(r.vehicles)} vehicles, "
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

    for result in results:
        brand_key = result.brand.lower().replace("-", "").replace(" ", "_")
        if brand_key not in index["brands"]:
            index["brands"][brand_key] = {"name": result.brand, "snapshots": []}

        option_count = sum(len(v.available_options) for v in result.vehicles)
        snapshot = {
            "date": date_str,
            "file": f"{result.brand.lower().replace('-', '').replace(' ', '_')}_{date_str}.json",
            "vehicle_count": len(result.vehicles),
            "option_count": option_count,
            "error_count": len(result.errors),
        }
        existing_dates = [s["date"] for s in index["brands"][brand_key]["snapshots"]]
        if date_str not in existing_dates:
            index["brands"][brand_key]["snapshots"].append(snapshot)

    index["crawl_history"].append({
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "total_vehicles": sum(len(r.vehicles) for r in results),
        "total_options": sum(
            len(v.available_options) for r in results for v in r.vehicles
        ),
        "brands_crawled": [r.brand for r in results],
    })

    # Compute cross-brand option summary
    # Uses live data when available, falls back to reference prices.
    summary = _compute_option_summary(results)
    if not summary.get("options"):
        logger.info("No live option data — using reference prices as fallback")
        summary = {
            "last_updated": datetime.now().isoformat(),
            "source": "reference",
            "options": get_reference_option_summary(),
        }
    index["option_summary"] = summary

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

    results = asyncio.run(
        crawl_all(
            brands=args.brands,
            data_dir=args.data_dir,
            sequential=not args.parallel,
        )
    )

    # Exit with error if no vehicles extracted from any brand
    if not any(r.vehicles for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
