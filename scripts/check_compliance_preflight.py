#!/usr/bin/env python3
"""Run a robots.txt preflight check for crawler entry points.

The check is intentionally conservative:
- 2xx robots.txt: evaluate allow/deny for each target URL
- 404 robots.txt: treated as allow (no published robots policy)
- network/HTTP errors: reported as unknown

Unknown results can be reported without blocking by default, while explicit
robots disallow rules can be configured to fail the job in strict mode.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests


@dataclass(frozen=True)
class PreflightTarget:
    brand: str
    market: str
    url: str


@dataclass
class HostPolicy:
    state: str  # ok | missing | error
    parser: RobotFileParser | None = None
    detail: str = ""
    robots_url: str = ""


def default_targets() -> list[PreflightTarget]:
    """Brand-market entry URLs used for compliance preflight checks."""
    return [
        PreflightTarget("Mercedes-Benz", "DE", "https://api.oneweb.mercedes-benz.com/owcc-backend/api/v3/de_DE/CCci/00000000/entry?typeClass=W206"),
        PreflightTarget("Porsche", "DE", "https://www.porsche.com/germany/models/"),
        PreflightTarget("Lexus", "DE", "https://www.lexus.de/modelle"),
        PreflightTarget("BYD", "DE", "https://www.byd.com/de/konfiguration/atto-2"),
        PreflightTarget("XPeng", "DE", "https://www.xpeng.com/de/model/g6"),
        PreflightTarget("Zeekr", "DE", "https://www.zeekr.eu/de-de/"),
        PreflightTarget("Polestar", "DE", "https://www.polestar.com/de/configure/polestar-2"),
        PreflightTarget("Audi", "DE", "https://www.audi.de/de/brand/de/neuwagen.html"),
        PreflightTarget("Volkswagen", "DE", "https://www.volkswagen.de/de/modelle.html"),
        PreflightTarget("Volkswagen", "US", "https://www.vw.com/en/models.html"),
        PreflightTarget("Volkswagen", "CN", "https://www.vw.com.cn/CarModelList.html"),
        PreflightTarget("Range Rover", "DE", "https://www.rangerover.com/de-de/index.html"),
        PreflightTarget("Range Rover", "US", "https://www.rangerover.com/en-us/index.html"),
    ]


def _robots_url(scheme: str, host: str) -> str:
    return f"{scheme}://{host}/robots.txt"


def _parse_robots(robots_url: str, robots_text: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse((robots_text or "").splitlines())
    return parser


def _can_fetch_from_robots_text(
    robots_url: str,
    robots_text: str,
    target_url: str,
    user_agent: str = "*",
) -> bool:
    return _parse_robots(robots_url, robots_text).can_fetch(user_agent, target_url)


def _load_host_policy(scheme: str, host: str, timeout: float) -> HostPolicy:
    robots_url = _robots_url(scheme, host)
    try:
        response = requests.get(
            robots_url,
            headers={
                "User-Agent": "vehicle-configurator-crawler-preflight/1.0",
                "Accept": "text/plain,*/*",
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return HostPolicy(state="error", detail=str(exc), robots_url=robots_url)

    if response.status_code == 404:
        return HostPolicy(state="missing", detail="robots.txt not found", robots_url=robots_url)

    if response.status_code >= 400:
        return HostPolicy(
            state="error",
            detail=f"robots.txt HTTP {response.status_code}",
            robots_url=robots_url,
        )

    parser = _parse_robots(robots_url, response.text)
    return HostPolicy(state="ok", parser=parser, detail="robots.txt loaded", robots_url=robots_url)


def evaluate_targets(
    targets: list[PreflightTarget],
    *,
    user_agent: str = "*",
    timeout: float = 12.0,
    loader: Callable[[str, str, float], HostPolicy] | None = None,
) -> list[dict[str, object]]:
    """Evaluate robots policies for all targets.

    Args:
        targets: URLs to validate.
        user_agent: User agent checked against robots rules.
        timeout: Per-host robots fetch timeout.
        loader: Test hook to override robots loading.
    """
    loader = loader or _load_host_policy
    host_cache: dict[tuple[str, str], HostPolicy] = {}
    results: list[dict[str, object]] = []

    for target in targets:
        parsed = urlsplit(target.url)
        key = (parsed.scheme or "https", parsed.netloc)
        if key not in host_cache:
            host_cache[key] = loader(key[0], key[1], timeout)
        policy = host_cache[key]

        if policy.state == "error":
            results.append(
                {
                    "brand": target.brand,
                    "market": target.market,
                    "url": target.url,
                    "status": "unknown",
                    "allowed": None,
                    "reason": policy.detail,
                    "robots_url": policy.robots_url,
                }
            )
            continue

        if policy.state == "missing":
            results.append(
                {
                    "brand": target.brand,
                    "market": target.market,
                    "url": target.url,
                    "status": "allowed",
                    "allowed": True,
                    "reason": "robots.txt missing (treated as allow)",
                    "robots_url": policy.robots_url,
                }
            )
            continue

        assert policy.parser is not None
        allowed = policy.parser.can_fetch(user_agent, target.url)
        results.append(
            {
                "brand": target.brand,
                "market": target.market,
                "url": target.url,
                "status": "allowed" if allowed else "blocked",
                "allowed": allowed,
                "reason": "Allowed by robots.txt" if allowed else "Disallowed by robots.txt",
                "robots_url": policy.robots_url,
            }
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Fail when any target is explicitly blocked by robots.txt")
    parser.add_argument(
        "--fail-on-unknown",
        action="store_true",
        help="Also fail on unknown status (network/HTTP robots errors)",
    )
    parser.add_argument("--timeout", type=float, default=12.0, help="Per-host robots.txt timeout in seconds")
    parser.add_argument("--user-agent", default="*", help="User agent token used for robots evaluation")
    parser.add_argument("--json-out", type=Path, help="Optional output file for machine-readable results")
    args = parser.parse_args()

    targets = default_targets()
    results = evaluate_targets(
        targets,
        user_agent=args.user_agent,
        timeout=args.timeout,
    )

    allowed = [row for row in results if row["status"] == "allowed"]
    blocked = [row for row in results if row["status"] == "blocked"]
    unknown = [row for row in results if row["status"] == "unknown"]

    print(
        f"Compliance preflight checked {len(results)} target(s): "
        f"{len(allowed)} allowed, {len(blocked)} blocked, {len(unknown)} unknown"
    )
    for row in results:
        print(
            f"{str(row['status']).upper():7s} "
            f"{row['brand']}[{row['market']}] {row['url']} "
            f"({row['reason']})"
        )

    if args.json_out:
        payload = {
            "targets": [asdict(target) for target in targets],
            "results": results,
            "summary": {
                "allowed": len(allowed),
                "blocked": len(blocked),
                "unknown": len(unknown),
            },
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote JSON report: {args.json_out}")

    if args.strict and blocked:
        return 1
    if args.fail_on_unknown and unknown:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
