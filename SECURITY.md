# Security Policy

## Supported Branch

Security updates are handled on `main`.

## Reporting

Please report suspected security issues through GitHub private vulnerability reporting when it is available for this repository. If that is not available, open a private issue with the repository owner rather than posting exploit details publicly.

## Repository Safety

- Only trusted maintainers should have write access.
- Generated crawler data is committed by GitHub Actions as `github-actions[bot]`.
- Manual workflow inputs are validated before being passed to the crawler.
- When the repository is public, enable branch protection or repository rules for `main` with required pull requests and code owner review.

## Data Quality Gates

- Snapshot publication is blocked when crawler output violates structural invariants (market/currency mismatch, invalid timestamps, non-finite prices, duplicate option identities).
- CI runs `pytest` before crawler execution and validates newly generated snapshots with `scripts/validate_crawl_results.py` before commit/publish.
- CI runs `scripts/check_compliance_preflight.py` before crawler execution to detect explicit robots disallow rules for configured crawl entry points.
- Validation warnings can be escalated to blocking failures (`--strict`) for release workflows.
- Market activation for new countries/brands requires passing extraction validation and compliance review; unsupported or blocked markets must remain explicitly documented.
