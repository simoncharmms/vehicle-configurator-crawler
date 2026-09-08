#!/usr/bin/env python3
"""Build comprehensive cross-brand index from crawl data.

Reads all brand JSON files, selects the best run per brand,
re-normalizes all options using option_mappings.py, and produces:
  - Updated per-brand JSON files (2026-09-08) with normalized options
  - Cross-brand index.json with option_summary
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.option_mappings import (
    OPTION_DEFINITIONS,
    normalize_option_name,
    get_category,
    get_brand_name,
    get_description,
    get_category_label,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "prices")

BRANDS = [
    "mercedes-benz",
    "lexus",
    "porsche",
    "audi",
    "byd",
    "polestar",
    "xpeng",
    "zeekr",
]

BRAND_DISPLAY = {
    "mercedes-benz": "Mercedes-Benz",
    "lexus": "Lexus",
    "porsche": "Porsche",
    "audi": "Audi",
    "byd": "BYD",
    "polestar": "Polestar",
    "xpeng": "XPeng",
    "zeekr": "Zeekr",
}


def find_best_run(runs: list[dict]) -> dict | None:
    """Select the run with the most vehicles and options."""
    best = None
    best_score = -1
    for run in runs:
        vehicles = run.get("vehicles", [])
        total_opts = sum(len(v.get("available_options", [])) for v in vehicles)
        score = len(vehicles) * 1000 + total_opts
        if score > best_score:
            best_score = score
            best = run
    return best


def normalize_vehicle_options(vehicle: dict, brand: str) -> tuple[dict, int, int]:
    """Re-normalize all options on a vehicle using option_mappings.py.
    
    Returns (updated_vehicle, newly_normalized_count, total_options).
    """
    options = vehicle.get("available_options", [])
    newly_normalized = 0
    
    for opt in options:
        brand_name = opt.get("brand_specific_name", "")
        existing_std = opt.get("standardized_name")
        
        # Try to normalize using option_mappings.py
        std_name = normalize_option_name(brand_name, BRAND_DISPLAY.get(brand, brand))
        
        if std_name and not existing_std:
            opt["standardized_name"] = std_name
            opt["category"] = get_category(std_name)
            newly_normalized += 1
        elif std_name and existing_std != std_name:
            # Update if mappings have a better match
            opt["standardized_name"] = std_name
            opt["category"] = get_category(std_name)
    
    vehicle["available_options"] = options
    return vehicle, newly_normalized, len(options)


def build_option_summary(brand_data: dict[str, dict]) -> dict:
    """Build cross-brand option summary.
    
    Returns option_summary dict with:
      - Per standardized option: which brands have it, model counts, brand-specific names
      - Cross-brand options (appearing in 2+ brands)
      - Category breakdown
    """
    # Collect: std_name -> brand -> { models: set, brand_specific_names: list, prices: list }
    option_map = defaultdict(lambda: defaultdict(lambda: {
        "models": set(),
        "brand_specific_names": [],
        "prices": [],
    }))
    
    for brand_key, data in brand_data.items():
        display_name = BRAND_DISPLAY.get(brand_key, brand_key)
        vehicles = data.get("vehicles", [])
        
        for v in vehicles:
            model = v.get("model", "Unknown")
            for opt in v.get("available_options", []):
                std_name = opt.get("standardized_name")
                if not std_name:
                    continue
                
                entry = option_map[std_name][display_name]
                entry["models"].add(model)
                bsn = opt.get("brand_specific_name", "")
                if bsn and bsn not in entry["brand_specific_names"]:
                    entry["brand_specific_names"].append(bsn)
                
                price = opt.get("price")
                if price is not None:
                    entry["prices"].append(price)
    
    # Build options list
    options = []
    cross_brand_count = 0
    
    for std_name in sorted(option_map.keys()):
        brands_info = option_map[std_name]
        brand_count = len(brands_info)
        
        if brand_count >= 2:
            cross_brand_count += 1
        
        brands_detail = {}
        total_models = 0
        
        for brand_display, info in sorted(brands_info.items()):
            model_count = len(info["models"])
            total_models += model_count
            
            # Pick the most common brand-specific name
            primary_name = info["brand_specific_names"][0] if info["brand_specific_names"] else std_name
            
            entry = {
                "name": primary_name,
                "brand_specific_name": primary_name,
                "model_count": model_count,
                "models": sorted(info["models"]),
            }
            
            if info["prices"]:
                entry["avg_price"] = round(sum(info["prices"]) / len(info["prices"]), 2)
                entry["min_price"] = min(info["prices"])
                entry["max_price"] = max(info["prices"])
            
            brands_detail[brand_display] = entry
        
        # Compute overall price stats
        all_prices = []
        for info in brands_info.values():
            all_prices.extend(info["prices"])
        
        opt_entry = {
            "standardized_name": std_name,
            "display_name": OPTION_DEFINITIONS.get(std_name, {}).get("description", std_name.replace('_', ' ').title()),
            "description": get_description(std_name),
            "category": get_category(std_name),
            "category_label": get_category_label(get_category(std_name)),
            "brands": brands_detail,
            "cross_brand_count": brand_count,
            "total_model_count": total_models,
        }
        
        if all_prices:
            opt_entry["overall_avg_price"] = round(sum(all_prices) / len(all_prices), 2)
            opt_entry["overall_min_price"] = min(all_prices)
            opt_entry["overall_max_price"] = max(all_prices)
        else:
            opt_entry["overall_avg_price"] = None
            opt_entry["overall_min_price"] = None
            opt_entry["overall_max_price"] = None
        
        options.append(opt_entry)
    
    # Sort by cross-brand count (descending), then total models
    options.sort(key=lambda x: (-x["cross_brand_count"], -x["total_model_count"]))
    
    # Category breakdown
    categories = defaultdict(lambda: {"option_count": 0, "cross_brand_count": 0})
    for opt in options:
        cat = opt["category"]
        categories[cat]["option_count"] += 1
        if opt["cross_brand_count"] >= 2:
            categories[cat]["cross_brand_count"] += 1
    
    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "total_unique_options": len(options),
        "cross_brand_options": cross_brand_count,
        "options": options,
        "categories": {k: v for k, v in sorted(categories.items())},
    }


def build_brand_option_summary(vehicles: list[dict]) -> list[dict]:
    """Build per-brand option summary from vehicles."""
    opt_map = defaultdict(lambda: {
        "models": set(),
        "brand_specific_names": [],
        "prices": [],
    })
    
    for v in vehicles:
        model = v.get("model", "Unknown")
        for opt in v.get("available_options", []):
            std_name = opt.get("standardized_name")
            if not std_name:
                continue
            entry = opt_map[std_name]
            entry["models"].add(model)
            bsn = opt.get("brand_specific_name", "")
            if bsn and bsn not in entry["brand_specific_names"]:
                entry["brand_specific_names"].append(bsn)
            price = opt.get("price")
            if price is not None:
                entry["prices"].append(price)
    
    summary = []
    for std_name, info in sorted(opt_map.items(), key=lambda x: -len(x[1]["models"])):
        entry = {
            "standardized_name": std_name,
            "brand_specific_name": info["brand_specific_names"][0] if info["brand_specific_names"] else std_name,
            "category": get_category(std_name),
            "model_count": len(info["models"]),
        }
        if info["prices"]:
            entry["avg_price"] = round(sum(info["prices"]) / len(info["prices"]), 2)
            entry["min_price"] = min(info["prices"])
            entry["max_price"] = max(info["prices"])
        else:
            entry["avg_price"] = None
            entry["min_price"] = None
            entry["max_price"] = None
        summary.append(entry)
    
    return summary


def main():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    
    print(f"Building cross-brand index for {today}")
    print("=" * 60)
    
    # Phase 1: Load and select best runs
    brand_data = {}
    stats = {}
    
    for brand in BRANDS:
        # Try today's file first, fall back to 2026-09-07
        candidates = [
            os.path.join(DATA_DIR, f"{brand}_{today}.json"),
            os.path.join(DATA_DIR, f"{brand}_2026-09-07.json"),
        ]
        
        loaded = None
        source_file = None
        for path in candidates:
            if os.path.exists(path):
                with open(path) as f:
                    loaded = json.load(f)
                source_file = os.path.basename(path)
                break
        
        if not loaded:
            print(f"  {brand}: NO DATA FOUND")
            stats[brand] = {"vehicles": 0, "options": 0, "normalized_new": 0, "source": "none"}
            continue
        
        best = find_best_run(loaded)
        if not best or not best.get("vehicles"):
            print(f"  {brand}: no vehicles in best run")
            stats[brand] = {"vehicles": 0, "options": 0, "normalized_new": 0, "source": source_file}
            continue
        
        # Phase 2: Re-normalize all options
        vehicles = best["vehicles"]
        total_new = 0
        total_opts = 0
        
        for i, v in enumerate(vehicles):
            vehicles[i], new_count, opt_count = normalize_vehicle_options(v, brand)
            total_new += new_count
            total_opts += opt_count
        
        # Rebuild option_summary for this brand
        option_summary = build_brand_option_summary(vehicles)
        
        best["vehicles"] = vehicles
        best["option_summary"] = option_summary
        best["vehicle_count"] = len(vehicles)
        
        brand_data[brand] = best
        stats[brand] = {
            "vehicles": len(vehicles),
            "options": total_opts,
            "normalized_new": total_new,
            "normalized_total": sum(1 for v in vehicles for o in v.get("available_options", []) if o.get("standardized_name")),
            "source": source_file,
            "option_summary_count": len(option_summary),
        }
        
        print(f"  {brand}: {len(vehicles)} vehicles, {total_opts} options "
              f"({total_new} newly normalized, {stats[brand]['normalized_total']} total standardized), "
              f"{len(option_summary)} summary entries")
    
    # Phase 3: Write updated per-brand files
    print(f"\nWriting per-brand files...")
    for brand, data in brand_data.items():
        outfile = os.path.join(DATA_DIR, f"{brand}_{today}.json")
        with open(outfile, "w") as f:
            json.dump([data], f, indent=2, ensure_ascii=False)
        print(f"  Written: {os.path.basename(outfile)}")
    
    # Phase 4: Build cross-brand option summary
    print(f"\nBuilding cross-brand option summary...")
    option_summary = build_option_summary(brand_data)
    
    # Phase 5: Build index.json
    index = {
        "last_updated": now.isoformat(),
        "version": "3.0_cross_brand",
        "brands": {},
        "option_summary": option_summary,
    }
    
    for brand in BRANDS:
        display = BRAND_DISPLAY[brand]
        if brand in brand_data:
            data = brand_data[brand]
            vehicles = data.get("vehicles", [])
            total_opts = sum(len(v.get("available_options", [])) for v in vehicles)
            std_opts = sum(1 for v in vehicles for o in v.get("available_options", []) if o.get("standardized_name"))
            
            index["brands"][brand] = {
                "name": display,
                "vehicle_count": len(vehicles),
                "option_count": total_opts,
                "standardized_option_count": std_opts,
                "latest_crawl": data.get("timestamp", now.isoformat()),
                "snapshots": [{
                    "date": today,
                    "vehicle_count": len(vehicles),
                    "option_count": total_opts,
                    "standardized_option_count": std_opts,
                    "file": f"{brand}_{today}.json",
                }],
            }
        else:
            index["brands"][brand] = {
                "name": display,
                "vehicle_count": 0,
                "option_count": 0,
                "standardized_option_count": 0,
                "latest_crawl": None,
                "snapshots": [],
            }
    
    # Write index.json
    index_path = os.path.join(DATA_DIR, "index.json")
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    print(f"  Written: index.json")
    
    # Phase 6: Report
    print("\n" + "=" * 60)
    print("CROSS-BRAND ANALYSIS REPORT")
    print("=" * 60)
    
    total_vehicles = sum(s["vehicles"] for s in stats.values())
    total_options = sum(s["options"] for s in stats.values())
    
    print(f"\nBrands crawled: {len(BRANDS)}")
    print(f"Total vehicles: {total_vehicles}")
    print(f"Total options: {total_options}")
    print(f"Total unique standardized options: {option_summary['total_unique_options']}")
    print(f"Cross-brand options (2+ brands): {option_summary['cross_brand_options']}")
    
    print(f"\nPer-brand breakdown:")
    for brand in BRANDS:
        s = stats[brand]
        print(f"  {BRAND_DISPLAY[brand]:15s}: {s['vehicles']:3d} vehicles, {s['options']:5d} options "
              f"({s.get('normalized_total', 0)} standardized, {s.get('normalized_new', 0)} newly matched)")
    
    print(f"\nCross-brand options (appearing in 2+ brands):")
    cross_brand = [o for o in option_summary["options"] if o["cross_brand_count"] >= 2]
    for opt in cross_brand:
        brands_list = ", ".join(f"{b} ({d['model_count']})" for b, d in opt["brands"].items())
        print(f"  {opt['standardized_name']:30s} [{opt['category']:12s}] → {brands_list}")
    
    print(f"\nCategory breakdown:")
    for cat, info in sorted(option_summary["categories"].items()):
        label = get_category_label(cat)
        print(f"  {label:25s}: {info['option_count']:3d} options ({info['cross_brand_count']} cross-brand)")
    
    print(f"\n✅ Index generation complete: {len(cross_brand)} cross-brand options identified")
    
    return 0 if len(cross_brand) >= 10 else 1


if __name__ == "__main__":
    sys.exit(main())
