"""Offline source-isolated take-rate build; see crawler/intelligence/CONTRACT.md."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler.intelligence.build import build_artifacts, repack_artifacts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("synthetic", "observed"), default="synthetic")
    parser.add_argument("--repack", action="store_true", help="repack published source without loading prices or retraining")
    parser.add_argument("--prices-dir", type=Path, default=ROOT / "data/prices")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/intelligence")
    parser.add_argument("--demand-dir", type=Path, default=ROOT / "data/demand")
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--markets", default="all", help="all or comma-separated ISO market codes")
    parser.add_argument("--seed", type=int, default=None, help="override configured seed (default 42)")
    parser.add_argument("--config", type=Path, default=ROOT / "data/demand/synthetic_config.json")
    arguments = parser.parse_args(argv)
    try:
        if arguments.repack:
            if arguments.observations is not None or arguments.seed is not None or arguments.markets != "all":
                raise ValueError("--repack uses all existing source endpoints; no observations, seed or market override")
            manifest = repack_artifacts(arguments.output_dir, arguments.demand_dir, arguments.source)
        else:
            manifest = build_artifacts(arguments.prices_dir, arguments.output_dir, arguments.demand_dir,
                                       arguments.source, arguments.observations, arguments.markets,
                                       arguments.seed, arguments.config)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Build rejected; previous published artifacts preserved.\n{error}\n")
    print(json.dumps(dict(manifest=str(arguments.output_dir / "manifest.json"),
                          source=arguments.source,
                          markets=len(manifest["sources"][arguments.source]["markets"]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())