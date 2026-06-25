#!/usr/bin/env python
"""Run a daily Biotech Risk Scout watchlist scan.

This is a thin local wrapper around app/scan.py so a cron job, Task Scheduler,
or GitHub Actions workflow can run the same scan command consistently.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.reports.alerts import write_alert_report  # noqa: E402
from scout.storage.snapshots import compare_snapshots, load_snapshot  # noqa: E402

SCAN_APP = os.path.join(PROJECT_ROOT, "app", "scan.py")
DEFAULT_OUTPUT_DIR = os.path.join(PROJECT_ROOT, "snapshots")
DEFAULT_WATCHLIST = os.path.join(PROJECT_ROOT, "watchlists", "biotech-watchlist.txt")


def build_command(args: argparse.Namespace) -> list[str]:
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    date_stamp = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    csv_path = os.path.join(output_dir, f"scan-{date_stamp}.csv")
    json_path = os.path.join(output_dir, f"scan-{date_stamp}.records.json")

    command = [
        sys.executable,
        SCAN_APP,
        "--tickers-file",
        os.path.abspath(args.tickers_file),
        "--snapshot-dir",
        output_dir,
        "--csv",
        csv_path,
        "--json",
        json_path,
        "--min-score",
        str(args.min_score),
    ]
    if args.no_table:
        command.append("--no-table")
    return command


def preserve_previous_latest(output_dir: str) -> str | None:
    """Copy latest.json before the scan overwrites it."""
    latest_path = os.path.join(output_dir, "latest.json")
    if not os.path.exists(latest_path):
        return None
    previous_path = os.path.join(output_dir, "previous-latest.json")
    shutil.copyfile(latest_path, previous_path)
    return previous_path


def write_post_scan_alerts(output_dir: str, previous_snapshot: str | None) -> None:
    """Write latest-alerts.md and latest-comparison.json after a scan."""
    alert_path = os.path.join(output_dir, "latest-alerts.md")
    comparison_path = os.path.join(output_dir, "latest-comparison.json")
    latest_path = os.path.join(output_dir, "latest.json")

    if not previous_snapshot or not os.path.exists(previous_snapshot):
        write_no_previous_alert(alert_path)
        return
    if not os.path.exists(latest_path):
        write_scan_missing_alert(alert_path)
        return

    comparison = compare_snapshots(load_snapshot(previous_snapshot), load_snapshot(latest_path))
    with open(comparison_path, "w", encoding="utf-8") as handle:
        json.dump(comparison, handle, indent=2)
        handle.write("\n")
    write_alert_report(comparison, alert_path)


def write_no_previous_alert(path: str) -> None:
    """Write a markdown note for the first run with no prior snapshot."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "# Biotech Risk Scout Alerts\n\n"
            "No previous `latest.json` snapshot was available, so this run created the baseline snapshot.\n\n"
            "Run the daily scan again to generate score-change alerts.\n"
        )


def write_scan_missing_alert(path: str) -> None:
    """Write a markdown note when the scan did not create latest.json."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(
            "# Biotech Risk Scout Alerts\n\n"
            "The scan completed without a `latest.json` snapshot, so no alert comparison could be generated.\n"
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the daily Biotech Risk Scout watchlist scan.")
    parser.add_argument("--tickers-file", default=DEFAULT_WATCHLIST, help="Watchlist file to scan")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory for snapshots and exports")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum score to keep in outputs")
    parser.add_argument("--date", help="Optional YYYYMMDD date stamp for deterministic output filenames")
    parser.add_argument("--no-table", action="store_true", help="Suppress table output")
    args = parser.parse_args(argv)

    if not os.path.exists(args.tickers_file):
        print(f"Watchlist file not found: {args.tickers_file}", file=sys.stderr)
        return 1

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    previous_snapshot = preserve_previous_latest(output_dir)

    command = build_command(args)
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        return int(result.returncode)

    write_post_scan_alerts(output_dir, previous_snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
