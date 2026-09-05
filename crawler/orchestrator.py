"""Orchestrator: runs all brand crawlers and saves results."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

from crawler.base import BrandCrawler, CrawlResult
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
        if result.vehicles:
            logger.info(
                f"✓ {crawler.brand}: {len(result.vehicles)} vehicles "
                f"({result.duration_seconds:.1f}s)"
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
            # Save immediately
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
    total_errors = sum(len(r.errors) for r in results)
    logger.info(f"\n{'='*60}")
    logger.info(f"Crawl complete: {total_vehicles} vehicles, {total_errors} errors")
    for r in results:
        status = "✓" if r.vehicles else "✗"
        logger.info(f"  {status} {r.brand}: {len(r.vehicles)} vehicles, {len(r.errors)} errors")
    logger.info(f"{'='*60}")

    # Write summary index
    _write_index(results, data_dir)

    return results


def _write_index(results: list[CrawlResult], data_dir: Path) -> None:
    """Write a summary index.json for the dashboard."""
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

        snapshot = {
            "date": date_str,
            "file": f"{result.brand.lower().replace('-', '').replace(' ', '_')}_{date_str}.json",
            "vehicle_count": len(result.vehicles),
            "error_count": len(result.errors),
        }
        # Avoid duplicate entries for same date
        existing_dates = [s["date"] for s in index["brands"][brand_key]["snapshots"]]
        if date_str not in existing_dates:
            index["brands"][brand_key]["snapshots"].append(snapshot)

    index["crawl_history"].append({
        "date": date_str,
        "timestamp": datetime.now().isoformat(),
        "total_vehicles": sum(len(r.vehicles) for r in results),
        "brands_crawled": [r.brand for r in results],
    })

    index["last_updated"] = datetime.now().isoformat()

    with open(index_path, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    logger.info(f"Updated index: {index_path}")


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
        # Trigger imports
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
