#!/usr/bin/env python
"""Multi-ticker scanner for Biotech Risk Scout."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Any

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scout.ingest.clinical_trials import fetch_clinical_trials  # type: ignore
from scout.ingest.sec_filings import SecClientError, fetch_sec_filings  # type: ignore
from scout.reports.alerts import write_alert_report  # type: ignore
from scout.reports.research_card import ResearchCard  # type: ignore
from scout.scoring.research_priority import PriorityScore, compute_priority_score  # type: ignore
from scout.storage.snapshots import compare_snapshots, format_snapshot_comparison, load_snapshot, write_snapshot  # type: ignore

EXPORT_COLUMNS = [
    "rank",
    "ticker",
    "company_name",
    "score",
    "main_reason",
    "red_flag_summary",
    "upcoming_catalyst",
    "days_until_event",
    "trial_count",
    "active_trial_count",
    "evidence_quality",
    "cash",
    "operating_cash_flow",
    "monthly_burn",
    "cash_runway_months",
    "dilution_risk",
    "has_shelf",
    "has_recent_financing_form",
    "latest_10q_date",
    "latest_10k_date",
    "latest_8k_date",
]


def build_scan_row(ticker: str) -> dict[str, Any]:
    """Fetch sources, build a card, and score one ticker."""
    filings = fetch_sec_filings(ticker)
    trials = fetch_clinical_trials(ticker)
    card = ResearchCard.from_sources(ticker=ticker, filings=filings, trials=trials)

    scorer_input = {
        "ticker": card.ticker,
        "company_name": card.company_name,
        "upcoming_catalyst": card.catalyst,
        "days_until_event": trials.get("days_until_event"),
        "trial_count": card.trial_count,
        "active_trial_count": card.active_trial_count,
        "evidence_quality": card.evidence_quality,
        "cash": card.cash,
        "operating_cash_flow": card.operating_cash_flow,
        "monthly_burn": card.monthly_burn,
        "cash_runway_months": card.cash_runway_months,
        "dilution_risk": card.dilution_risk,
        "has_recent_financing_form": filings.get("has_recent_financing_form"),
        "has_shelf": filings.get("has_shelf"),
        "latest_10q": card.latest_10q,
        "latest_10k": card.latest_10k,
        "latest_8k": card.latest_8k,
        "sponsor_names": card.sponsor_names,
        "trials": card.extra.get("trials", []),
    }
    priority = compute_priority_score(scorer_input)
    return {"ticker": card.ticker, "card": card, "priority": priority, "data": scorer_input}


def scan_tickers(tickers: list[str]) -> list[dict[str, Any]]:
    """Scan tickers and return ranked rows."""
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            rows.append(build_scan_row(ticker.upper()))
        except SecClientError as exc:
            rows.append(
                {
                    "ticker": ticker.upper(),
                    "card": None,
                    "priority": PriorityScore(0, "SEC ingestion failed", str(exc), 0, 0, 0, 0, 0, -25),
                    "data": {"upcoming_catalyst": "Error", "dilution_risk": "?", "evidence_quality": "?"},
                }
            )
    return sorted(rows, key=lambda row: (-row["priority"].score, _sort_days(row)))


def print_scan_table(rows: list[dict[str, Any]], min_score: int = 0) -> None:
    """Print a compact ranked scanner table."""
    filtered = _filter_rows(rows, min_score)
    headers = [
        "Rank",
        "Ticker",
        "Score",
        "Catalyst",
        "Days",
        "Runway",
        "Dilution",
        "Evidence",
        "Trials",
        "Main Reason",
        "Main Risk",
    ]
    widths = [4, 6, 5, 42, 5, 8, 9, 10, 14, 25, 25]
    print(_format_row(headers, widths))
    print(_format_row(["-" * width for width in widths], widths))

    for index, row in enumerate(filtered, start=1):
        data = row["data"]
        priority: PriorityScore = row["priority"]
        card = row.get("card")
        values = [
            str(index),
            row["ticker"],
            str(priority.score),
            _truncate(data.get("upcoming_catalyst") or "-", 42),
            _format_days(data.get("days_until_event")),
            _format_runway(data.get("cash_runway_months")),
            _short_dilution(data.get("dilution_risk")),
            _short_evidence(data.get("evidence_quality")),
            f"{data.get('active_trial_count') or 0} active/{data.get('trial_count') or 0} total",
            _truncate(priority.main_reason, 25),
            _truncate(card.main_risk if card else priority.red_flag_summary, 25),
        ]
        print(_format_row(values, widths))


def print_score_explanations(rows: list[dict[str, Any]], min_score: int = 0) -> None:
    """Print detailed score explanations for filtered rows."""
    for row in _filter_rows(rows, min_score):
        print()
        print(format_score_explanation(row))


def format_score_explanation(row: dict[str, Any]) -> str:
    """Format a per-ticker score breakdown."""
    priority: PriorityScore = row["priority"]
    data = row.get("data", {})
    lines = [
        f"{row.get('ticker')} score: {priority.score}/100",
        "",
        "Component Breakdown",
        "-------------------",
        f"Catalyst urgency:       {priority.c1_catalyst_urgency:>3} / 30",
        f"Evidence quality:       {priority.c2_evidence_quality:>3} / 20",
        f"Financial viability:    {priority.c3_financial_viability:>3} / 20",
        f"Dilution structure:     {priority.c4_dilution_structure:>3} / 15",
        f"Data confidence:        {priority.c5_data_confidence:>3} / 10",
        f"Red flags:              {priority.c6_red_flags:>3} / -25",
        "",
        f"Main reason: {priority.main_reason}",
        f"Red flags: {priority.red_flag_summary}",
        "",
        "Inputs Used",
        "-----------",
        f"Company: {data.get('company_name') or '-'}",
        f"Catalyst: {data.get('upcoming_catalyst') or '-'}",
        f"Days until event: {_format_days(data.get('days_until_event'))}",
        f"Trials: {data.get('active_trial_count') or 0} active / {data.get('trial_count') or 0} total",
        f"Evidence quality: {data.get('evidence_quality') or '-'}",
        f"Cash: {_format_money(data.get('cash'))}",
        f"Monthly burn: {_format_money(data.get('monthly_burn'))}",
        f"Runway: {_format_runway(data.get('cash_runway_months'))}",
        f"Dilution risk: {data.get('dilution_risk') or '-'}",
        f"Shelf: {bool(data.get('has_shelf'))}",
        f"Recent financing form: {bool(data.get('has_recent_financing_form'))}",
    ]
    return "\n".join(lines)


def export_rows_to_json(rows: list[dict[str, Any]], path: str, min_score: int = 0) -> None:
    """Write ranked scan rows to JSON."""
    payload = export_records(rows, min_score=min_score)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def export_rows_to_csv(rows: list[dict[str, Any]], path: str, min_score: int = 0) -> None:
    """Write ranked scan rows to CSV."""
    payload = export_records(rows, min_score=min_score)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(payload)


def export_records(rows: list[dict[str, Any]], min_score: int = 0) -> list[dict[str, Any]]:
    """Return ranked export records for JSON, CSV, and snapshots."""
    return [_export_record(rank, row) for rank, row in enumerate(_filter_rows(rows, min_score), start=1)]


def export_snapshot(rows: list[dict[str, Any]], snapshot_dir: str, min_score: int = 0) -> dict[str, str]:
    """Write a dated JSON snapshot plus latest.json."""
    return write_snapshot(export_records(rows, min_score=min_score), snapshot_dir)


def compare_snapshot_files(old_path: str, new_path: str) -> dict[str, Any]:
    """Load and compare two snapshot files."""
    return compare_snapshots(load_snapshot(old_path), load_snapshot(new_path))


def load_tickers_from_file(path: str) -> list[str]:
    """Load tickers from a file with newline and comma support."""
    with open(path, "r", encoding="utf-8") as handle:
        return parse_ticker_text(handle.read())


def parse_ticker_text(text: str) -> list[str]:
    """Parse tickers from newline/comma-separated text, ignoring comments."""
    tickers: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        for piece in line.replace(",", " ").split():
            ticker = piece.strip().upper()
            if ticker and ticker not in seen:
                seen.add(ticker)
                tickers.append(ticker)
    return tickers


def resolve_tickers(cli_tickers: list[str], tickers_file: str | None) -> list[str]:
    """Merge positional tickers and optional file tickers while preserving order."""
    combined: list[str] = []
    seen: set[str] = set()
    for ticker in cli_tickers:
        normalized = ticker.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            combined.append(normalized)
    if tickers_file:
        for ticker in load_tickers_from_file(tickers_file):
            if ticker not in seen:
                seen.add(ticker)
                combined.append(ticker)
    return combined


def _export_record(rank: int, row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data", {})
    priority: PriorityScore = row["priority"]
    return {
        "rank": rank,
        "ticker": row.get("ticker"),
        "company_name": data.get("company_name"),
        "score": priority.score,
        "main_reason": priority.main_reason,
        "red_flag_summary": priority.red_flag_summary,
        "upcoming_catalyst": data.get("upcoming_catalyst"),
        "days_until_event": data.get("days_until_event"),
        "trial_count": data.get("trial_count"),
        "active_trial_count": data.get("active_trial_count"),
        "evidence_quality": data.get("evidence_quality"),
        "cash": data.get("cash"),
        "operating_cash_flow": data.get("operating_cash_flow"),
        "monthly_burn": data.get("monthly_burn"),
        "cash_runway_months": data.get("cash_runway_months"),
        "dilution_risk": data.get("dilution_risk"),
        "has_shelf": data.get("has_shelf"),
        "has_recent_financing_form": data.get("has_recent_financing_form"),
        "latest_10q_date": _filing_date(data.get("latest_10q")),
        "latest_10k_date": _filing_date(data.get("latest_10k")),
        "latest_8k_date": _filing_date(data.get("latest_8k")),
    }


def _filter_rows(rows: list[dict[str, Any]], min_score: int) -> list[dict[str, Any]]:
    return [row for row in rows if row["priority"].score >= min_score]


def _sort_days(row: dict[str, Any]) -> int:
    days = row.get("data", {}).get("days_until_event")
    return days if isinstance(days, int) and days >= 0 else 999999


def _format_row(values: list[str], widths: list[int]) -> str:
    return " | ".join(str(value).ljust(width)[:width] for value, width in zip(values, widths))


def _truncate(value: Any, max_len: int) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _format_days(value: Any) -> str:
    return str(value) if value is not None else "-"


def _format_runway(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}mo"
    except (TypeError, ValueError):
        return f"{value}mo"


def _format_money(value: Any) -> str:
    if value is None:
        return "-"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    abs_amount = abs(amount)
    sign = "-" if amount < 0 else ""
    if abs_amount >= 1_000_000_000:
        return f"{sign}${abs_amount / 1_000_000_000:.1f}B"
    if abs_amount >= 1_000_000:
        return f"{sign}${abs_amount / 1_000_000:.1f}M"
    if abs_amount >= 1_000:
        return f"{sign}${abs_amount / 1_000:.1f}K"
    return f"{sign}${abs_amount:.0f}"


def _short_dilution(value: Any) -> str:
    if not value:
        return "?"
    mapping = {"Moderate": "Mod", "Critical": "Crit", "Unknown": "?"}
    return mapping.get(str(value), str(value))


def _short_evidence(value: Any) -> str:
    if not value:
        return "?"
    mapping = {"Moderate-High": "Mod-H", "Moderate": "Mod", "Unknown": "?"}
    return mapping.get(str(value), str(value))


def _filing_date(filing: Any) -> Any:
    if not isinstance(filing, dict):
        return None
    return filing.get("filing_date") or filing.get("filed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rank biotech tickers by research priority.")
    parser.add_argument("tickers", nargs="*", help="Ticker symbols to scan")
    parser.add_argument("--tickers-file", help="Optional newline/comma-separated ticker file")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum score to display/export")
    parser.add_argument("--json", dest="json_path", help="Optional path to write JSON scan results")
    parser.add_argument("--csv", dest="csv_path", help="Optional path to write CSV scan results")
    parser.add_argument("--snapshot-dir", help="Optional directory to write scan-YYYYMMDD.json and latest.json")
    parser.add_argument("--compare-snapshots", nargs=2, metavar=("OLD", "NEW"), help="Compare two snapshot JSON files and exit")
    parser.add_argument("--compare-json", dest="compare_json_path", help="Optional path to write snapshot comparison JSON")
    parser.add_argument("--alert-report", dest="alert_report_path", help="Optional path to write a markdown alert report from snapshot comparison")
    parser.add_argument("--explain", action="store_true", help="Print per-ticker score explanations after the scan table")
    parser.add_argument("--no-table", action="store_true", help="Do not print the table; useful for export-only runs")
    args = parser.parse_args(argv)

    if args.compare_snapshots:
        comparison = compare_snapshot_files(args.compare_snapshots[0], args.compare_snapshots[1])
        print(format_snapshot_comparison(comparison))
        if args.compare_json_path:
            with open(args.compare_json_path, "w", encoding="utf-8") as handle:
                json.dump(comparison, handle, indent=2)
                handle.write("\n")
        if args.alert_report_path:
            write_alert_report(comparison, args.alert_report_path)
        return 0

    if args.alert_report_path:
        parser.error("--alert-report requires --compare-snapshots OLD NEW")

    tickers = resolve_tickers(args.tickers, args.tickers_file)
    if not tickers:
        parser.error("provide at least one ticker, --tickers-file, or --compare-snapshots OLD NEW")

    rows = scan_tickers(tickers)
    if not args.no_table:
        print_scan_table(rows, min_score=args.min_score)
    if args.explain:
        print_score_explanations(rows, min_score=args.min_score)
    if args.json_path:
        export_rows_to_json(rows, args.json_path, min_score=args.min_score)
    if args.csv_path:
        export_rows_to_csv(rows, args.csv_path, min_score=args.min_score)
    if args.snapshot_dir:
        export_snapshot(rows, args.snapshot_dir, min_score=args.min_score)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
