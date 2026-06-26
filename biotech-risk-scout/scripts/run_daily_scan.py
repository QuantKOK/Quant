#!/usr/bin/env python
"""Run a daily Biotech Risk Scout watchlist scan.

Replaces the previous subprocess-based approach with a direct Python call so
errors surface as exceptions, stdout is captured cleanly, and there is no
dependency on working-directory or PATH assumptions.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from app.scan import (  # noqa: E402
    export_rows_to_csv,
    export_rows_to_json,
    export_snapshot,
    load_tickers_from_file,
    print_scan_table,
    scan_tickers,
)
from scout.reports.alerts import write_alert_report  # noqa: E402
from scout.storage.snapshots import compare_snapshots, load_snapshot  # noqa: E402

DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "snapshots")
DEFAULT_WATCHLIST = os.path.join(PROJECT_ROOT, "watchlists", "biotech-watchlist.txt")


def preserve_previous_latest(output_dir: str) -> str | None:
    """Copy latest.json before the scan overwrites it."""
    latest_path = os.path.join(output_dir, "latest.json")
    if not os.path.exists(latest_path):
        return None
    previous_path = os.path.join(output_dir, "previous-latest.json")
    shutil.copyfile(latest_path, previous_path)
    return previous_path


def run_scan(args: argparse.Namespace, output_dir: str) -> int:
    """Load tickers, run the scan, and write all exports. Returns exit code."""
    date_stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = os.path.join(output_dir, f"scan-{date_stamp}.csv")
    json_path = os.path.join(output_dir, f"scan-{date_stamp}.records.json")

    try:
        tickers = load_tickers_from_file(args.tickers_file)
    except OSError as exc:
        print(f"Failed to load tickers file: {exc}", file=sys.stderr)
        return 1

    if not tickers:
        print("No tickers found in watchlist file.", file=sys.stderr)
        return 1

    print(f"Scanning {len(tickers)} tickers …", file=sys.stderr)
    rows = scan_tickers(tickers, max_workers=args.max_workers)

    if not args.no_table:
        print_scan_table(rows, min_score=args.min_score)

    export_rows_to_json(rows, json_path, min_score=args.min_score)
    export_rows_to_csv(rows, csv_path, min_score=args.min_score)
    export_snapshot(rows, output_dir, min_score=args.min_score)

    print(f"Exports written to {output_dir}", file=sys.stderr)
    return 0


def write_post_scan_alerts(output_dir: str, previous_snapshot: str | None) -> None:
    """Write latest-alerts.md and latest-comparison.json after a scan."""
    alert_path = os.path.join(output_dir, "latest-alerts.md")
    comparison_path = os.path.join(output_dir, "latest-comparison.json")
    latest_path = os.path.join(output_dir, "latest.json")

    if not previous_snapshot or not os.path.exists(previous_snapshot):
        _write_note(
            alert_path,
            "No previous `latest.json` snapshot was available, so this run created the baseline snapshot.\n\n"
            "Run the daily scan again to generate score-change alerts.",
        )
        return

    if not os.path.exists(latest_path):
        _write_note(
            alert_path,
            "The scan completed without a `latest.json` snapshot, so no alert comparison could be generated.",
        )
        return

    comparison = compare_snapshots(load_snapshot(previous_snapshot), load_snapshot(latest_path))
    with open(comparison_path, "w", encoding="utf-8") as handle:
        json.dump(comparison, handle, indent=2)
        handle.write("\n")
    write_alert_report(comparison, alert_path)


def _write_note(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"# Biotech Risk Scout Alerts\n\n{body}\n")


# Keep build_command for tests that import it directly
def build_command(args: argparse.Namespace) -> list[str]:
    """Retained for backwards-compatibility with existing tests only.

    New code should call run_scan() directly.
    """
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    date_stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = os.path.join(output_dir, f"scan-{date_stamp}.csv")
    json_path = os.path.join(output_dir, f"scan-{date_stamp}.records.json")
    command = [
        sys.executable,
        os.path.join(PROJECT_ROOT, "app", "scan.py"),
        "--tickers-file", os.path.abspath(args.tickers_file),
        "--snapshot-dir", output_dir,
        "--csv", csv_path,
        "--json", json_path,
        "--min-score", str(args.min_score),
    ]
    if args.no_table:
        command.append("--no-table")
    return command


def write_no_previous_alert(path: str) -> None:
    """Retained for backwards-compatibility with existing tests only."""
    _write_note(
        path,
        "No previous `latest.json` snapshot was available, so this run created the baseline snapshot.\n\n"
        "Run the daily scan again to generate score-change alerts.",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the daily Biotech Risk Scout watchlist scan.")
    parser.add_argument("--tickers-file", default=DEFAULT_WATCHLIST, help="Watchlist file to scan")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for snapshots and exports")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum score to keep in outputs")
    parser.add_argument("--date", help="Optional YYYYMMDD date stamp for deterministic output filenames")
    parser.add_argument("--no-table", action="store_true", help="Suppress table output")
    parser.add_argument("--max-workers", type=int, default=8, help="Max concurrent ticker fetches (default: 8)")
    args = parser.parse_args(argv)

    if not os.path.exists(args.tickers_file):
        print(f"Watchlist file not found: {args.tickers_file}", file=sys.stderr)
        return 1

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    previous_snapshot = preserve_previous_latest(output_dir)

    exit_code = run_scan(args, output_dir)
    if exit_code != 0:
        return exit_code

    write_post_scan_alerts(output_dir, previous_snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
