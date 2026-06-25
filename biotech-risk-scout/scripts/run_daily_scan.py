#!/usr/bin/env python
"""Run a daily Biotech Risk Scout watchlist scan.

This is a thin local wrapper around app/scan.py so a cron job, Task Scheduler,
or GitHub Actions workflow can run the same scan command consistently.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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

    command = build_command(args)
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
