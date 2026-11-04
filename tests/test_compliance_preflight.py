"""Unit tests for robots compliance preflight checks."""

from __future__ import annotations

from scripts.check_compliance_preflight import (
    HostPolicy,
    PreflightTarget,
    _can_fetch_from_robots_text,
    _parse_robots,
    default_targets,
    evaluate_targets,
)


def test_can_fetch_from_robots_text_allow_and_disallow() -> None:
    robots_url = "https://example.com/robots.txt"
    robots_text = """
    User-agent: *
    Disallow: /private
    Allow: /
    """
    assert _can_fetch_from_robots_text(robots_url, robots_text, "https://example.com/models")
    assert not _can_fetch_from_robots_text(robots_url, robots_text, "https://example.com/private/price")


def test_evaluate_targets_uses_host_cache_and_status_mapping() -> None:
    calls: dict[str, int] = {}

    def loader(scheme: str, host: str, timeout: float) -> HostPolicy:
        del timeout
        calls[host] = calls.get(host, 0) + 1
        robots_url = f"{scheme}://{host}/robots.txt"
        if host == "missing.example":
            return HostPolicy(state="missing", detail="missing", robots_url=robots_url)
        if host == "deny.example":
            parser = _parse_robots(robots_url, "User-agent: *\nDisallow: /deny")
            return HostPolicy(state="ok", parser=parser, detail="ok", robots_url=robots_url)
        return HostPolicy(state="error", detail="timeout", robots_url=robots_url)

    targets = [
        PreflightTarget("BrandA", "DE", "https://missing.example/first"),
        PreflightTarget("BrandA", "US", "https://missing.example/second"),
        PreflightTarget("BrandB", "DE", "https://deny.example/deny/path"),
        PreflightTarget("BrandC", "DE", "https://error.example/route"),
    ]

    results = evaluate_targets(targets, loader=loader)
    by_url = {row["url"]: row for row in results}

    assert calls["missing.example"] == 1
    assert calls["deny.example"] == 1
    assert calls["error.example"] == 1

    assert by_url["https://missing.example/first"]["status"] == "allowed"
    assert by_url["https://missing.example/second"]["status"] == "allowed"
    assert by_url["https://deny.example/deny/path"]["status"] == "blocked"
    assert by_url["https://error.example/route"]["status"] == "unknown"


def test_default_targets_include_focus_brand_new_markets() -> None:
    targets = {(target.brand, target.market) for target in default_targets()}
    assert ("Volkswagen", "US") in targets
    assert ("Volkswagen", "CN") in targets
    assert ("Range Rover", "US") in targets
    assert ("Range Rover", "DE") in targets
