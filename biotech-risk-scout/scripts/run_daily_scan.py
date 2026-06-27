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
from scout.delivery import (  # noqa: E402
    ConsoleDelivery,
    DeliveryResult,
    FileArchiveDelivery,
    build_email_digest,
)
from scout.ingest.sec_validation import (  # noqa: E402
    DEFAULT_CACHE_TTL_DAYS,
    DEFAULT_SEC_VALIDATION_CACHE,
    prune_validation_cache,
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
    rows = scan_tickers(
        tickers,
        max_workers=args.max_workers,
        validate_sec_text=getattr(args, "validate_sec_text", False),
        max_sec_documents=max(0, getattr(args, "max_sec_documents", 2)),
        sec_validation_cache=getattr(args, "sec_validation_cache", None),
        sec_validation_ttl_days=getattr(args, "sec_validation_cache_ttl_days", DEFAULT_CACHE_TTL_DAYS),
    )

    failed = [row for row in rows if row.get("card") is None]
    successful = [row for row in rows if row.get("card") is not None]
    for row in failed:
        priority = row.get("priority")
        detail = getattr(priority, "red_flag_summary", "unknown error")
        print(
            f"WARNING: {row.get('ticker', '?')} scan failed — {detail}",
            file=sys.stderr,
        )

    if not rows or not successful:
        print(
            "All ticker scans failed; existing snapshots and exports were not replaced.",
            file=sys.stderr,
        )
        return 1

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


def deliver_alert_report(args: argparse.Namespace, output_dir: str) -> list[DeliveryResult]:
    """Run optional, local-only delivery actions for the generated alert report.

    Delivery is best-effort: a failure in any channel prints a warning to stderr
    but does not change the daily-scan exit code. No external services are
    contacted. Returns the per-channel results (useful for tests).
    """
    want_print = getattr(args, "print_alert_report", False)
    archive_dir = getattr(args, "archive_alert_report_dir", None)
    digest_path = getattr(args, "write_email_digest", None)
    if not (want_print or archive_dir or digest_path):
        return []

    alert_path = os.path.join(output_dir, "latest-alerts.md")
    try:
        with open(alert_path, "r", encoding="utf-8") as handle:
            report_text = handle.read()
    except OSError as exc:
        print(f"WARNING: could not read alert report for delivery: {exc}", file=sys.stderr)
        return []

    results: list[DeliveryResult] = []

    if want_print:
        results.append(ConsoleDelivery().deliver(alert_path, report_text))

    if archive_dir:
        results.append(FileArchiveDelivery(archive_dir).deliver(alert_path, report_text))

    if digest_path:
        results.append(_write_email_digest_file(digest_path, report_text))

    for result in results:
        if result.ok:
            print(f"Delivery [{result.channel}]: {result.message}", file=sys.stderr)
        else:
            print(f"WARNING: delivery [{result.channel}] failed: {result.message}", file=sys.stderr)
    return results


def _write_email_digest_file(digest_path: str, report_text: str) -> DeliveryResult:
    """Write an email-style digest file. No email is sent."""
    digest = build_email_digest(report_text)
    try:
        parent = os.path.dirname(os.path.abspath(digest_path))
        os.makedirs(parent, exist_ok=True)
        with open(digest_path, "w", encoding="utf-8") as handle:
            handle.write(f"Subject: {digest['subject']}\n\n{digest['body']}")
    except OSError as exc:
        return DeliveryResult(
            channel="email_digest",
            destination=digest_path,
            ok=False,
            message=f"failed to write email digest: {exc}",
        )
    return DeliveryResult(
        channel="email_digest",
        destination=digest_path,
        ok=True,
        message=f"wrote email digest to {digest_path}",
    )


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
    if getattr(args, "validate_sec_text", False):
        command.extend(
            [
                "--validate-sec-text",
                "--max-sec-documents",
                str(getattr(args, "max_sec_documents", 3)),
                "--sec-validation-cache",
                os.path.abspath(
                    getattr(args, "sec_validation_cache", DEFAULT_SEC_VALIDATION_CACHE)
                ),
                "--sec-validation-cache-ttl-days",
                str(getattr(args, "sec_validation_cache_ttl_days", DEFAULT_CACHE_TTL_DAYS)),
            ]
        )
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
    parser.add_argument(
        "--validate-sec-text",
        action="store_true",
        help="Validate selected high-risk SEC flags against filing text",
    )
    parser.add_argument(
        "--max-sec-documents",
        type=int,
        default=2,
        help="Maximum SEC documents to validate per ticker (default: 2)",
    )
    parser.add_argument(
        "--sec-validation-cache",
        default=DEFAULT_SEC_VALIDATION_CACHE,
        help="Persistent SEC validation cache path",
    )
    parser.add_argument(
        "--sec-validation-cache-ttl-days",
        type=int,
        default=DEFAULT_CACHE_TTL_DAYS,
        help=f"Days a cached SEC validation entry stays fresh before refetch (default: {DEFAULT_CACHE_TTL_DAYS})",
    )
    parser.add_argument(
        "--print-alert-report",
        action="store_true",
        help="Print the generated alert report to stdout after the scan",
    )
    parser.add_argument(
        "--archive-alert-report-dir",
        help="Directory to archive a copy of the generated alert report",
    )
    parser.add_argument(
        "--write-email-digest",
        help="Path to write an email-style digest file (no email is sent)",
    )
    args = parser.parse_args(argv)

    if not os.environ.get("SEC_USER_AGENT", "").strip():
        print(
            "SEC_USER_AGENT is required. Set it to an application name and monitored contact "
            "before running the daily scanner.",
            file=sys.stderr,
        )
        return 1

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
    deliver_alert_report(args, output_dir)

    if args.validate_sec_text and args.sec_validation_cache:
        counts = prune_validation_cache(args.sec_validation_cache, ttl_days=args.sec_validation_cache_ttl_days)
        if counts["removed"]:
            print(
                f"Pruned SEC validation cache: removed {counts['removed']} stale "
                f"entr{'y' if counts['removed'] == 1 else 'ies'} "
                f"({counts['before']} -> {counts['after']}).",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
