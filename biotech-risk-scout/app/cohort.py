#!/usr/bin/env python
"""CLI for the real-trial cohort acquisition and adjudication workbench.

Subcommands:

* ``discover``      - query the live API, freeze snapshots, build queue+manifest
* ``verify``        - verify manifest, snapshot hashes, and queue reproduction
* ``summary``       - print a non-sensitive summary of a frozen cohort
* ``rebuild-queue`` - deterministically rebuild the queue from frozen snapshots

Requires an explicit output directory. Never prints secrets or raw payloads.
This tool freezes source data for later human adjudication. It never assigns a
scientific outcome and is scientific-risk research only, not investment advice.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scout.cohorts.clinicaltrials import (  # type: ignore
    MAX_PAGES,
    ClinicalTrialsClient,
    ClinicalTrialsError,
    MissingUserAgentError,
)
from scout.cohorts.discovery import (  # type: ignore
    CohortError,
    clamp_cap,
    discover_cohort,
    rebuild_queue,
    summarize_cohort,
    verify_cohort,
)
from scout.cohorts.protocol import DEFAULT_COHORT_CAP, MAX_COHORT_CAP


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_discover(args: argparse.Namespace) -> int:
    try:
        cap = clamp_cap(args.max_studies)
        if not 1 <= args.page_size <= 1000:
            raise CohortError("--page-size must be between 1 and 1000")
        if not 1 <= args.timeout <= 300:
            raise CohortError("--timeout must be between 1 and 300 seconds")
        if args.scan_page_limit is not None and not 1 <= args.scan_page_limit <= MAX_PAGES:
            raise CohortError(
                f"--scan-page-limit must be between 1 and {MAX_PAGES}"
            )
    except CohortError as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1
    try:
        client = ClinicalTrialsClient(timeout=args.timeout)
    except (MissingUserAgentError, ValueError) as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1
    try:
        summary = discover_cohort(
            client,
            args.output,
            max_studies=cap,
            force=args.force,
            page_size=args.page_size,
            scan_page_limit=args.scan_page_limit,
        )
    except (CohortError, OSError, ValueError) as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1
    except ClinicalTrialsError as exc:
        print(f"Discovery failed (API): {exc}", file=sys.stderr)
        return 1
    _print_json(summary)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    result = verify_cohort(args.output)
    _print_json({"ok": result["ok"], "errors": result["errors"]})
    return 0 if result["ok"] else 1


def cmd_summary(args: argparse.Namespace) -> int:
    try:
        summary = summarize_cohort(args.output)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Summary failed: {exc}", file=sys.stderr)
        return 1
    _print_json(summary)
    return 0


def cmd_rebuild_queue(args: argparse.Namespace) -> int:
    try:
        result = rebuild_queue(args.output)
    except (CohortError, OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"Rebuild failed: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0 if result["matches_manifest"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover, freeze, verify, and summarize a real-trial cohort."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover_p = subparsers.add_parser("discover", help="Query the API and freeze a cohort")
    discover_p.add_argument("--output", required=True, help="Output directory (required)")
    discover_p.add_argument(
        "--max-studies",
        type=int,
        default=DEFAULT_COHORT_CAP,
        help=f"Eligible-trial cap (default {DEFAULT_COHORT_CAP}, hard max {MAX_COHORT_CAP})",
    )
    discover_p.add_argument("--page-size", type=int, default=50, help="API page size")
    discover_p.add_argument("--timeout", type=int, default=30, help="Per-request timeout (seconds)")
    discover_p.add_argument(
        "--scan-page-limit",
        type=int,
        default=None,
        help="Bound the number of scanned pages (for bounded smoke checks; omit for a full build)",
    )
    discover_p.add_argument("--force", action="store_true", help="Overwrite an existing cohort directory")
    discover_p.set_defaults(func=cmd_discover)

    verify_p = subparsers.add_parser("verify", help="Verify a frozen cohort")
    verify_p.add_argument("--output", required=True, help="Cohort directory (required)")
    verify_p.set_defaults(func=cmd_verify)

    summary_p = subparsers.add_parser("summary", help="Summarize a frozen cohort")
    summary_p.add_argument("--output", required=True, help="Cohort directory (required)")
    summary_p.set_defaults(func=cmd_summary)

    rebuild_p = subparsers.add_parser("rebuild-queue", help="Rebuild the queue from frozen snapshots")
    rebuild_p.add_argument("--output", required=True, help="Cohort directory (required)")
    rebuild_p.set_defaults(func=cmd_rebuild_queue)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
