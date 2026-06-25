#!/usr/bin/env python
"""Multi-ticker scanner for Biotech Risk Scout."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scout.ingest.clinical_trials import fetch_clinical_trials  # type: ignore
from scout.ingest.sec_filings import SecClientError, fetch_sec_filings  # type: ignore
from scout.reports.research_card import ResearchCard  # type: ignore
from scout.scoring.research_priority import PriorityScore, compute_priority_score  # type: ignore


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
    filtered = [row for row in rows if row["priority"].score >= min_score]
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
    return f"{value}mo"


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Rank biotech tickers by research priority.")
    parser.add_argument("tickers", nargs="+", help="Ticker symbols to scan")
    parser.add_argument("--min-score", type=int, default=0, help="Minimum score to display")
    args = parser.parse_args(argv)

    rows = scan_tickers(args.tickers)
    print_scan_table(rows, min_score=args.min_score)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
