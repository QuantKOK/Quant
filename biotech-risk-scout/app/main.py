#!/usr/bin/env python
"""Entry point for the Biotech Risk Scout CLI."""

import argparse
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scout.ingest.clinical_trials import fetch_clinical_trials  # type: ignore
from scout.ingest.sec_filings import SecClientError, fetch_sec_filings  # type: ignore
from scout.reports.research_card import ResearchCard  # type: ignore


def build_research_card(ticker: str) -> ResearchCard:
    """Construct a research card from available ingestion sources."""
    filing_data = fetch_sec_filings(ticker)
    trial_data = fetch_clinical_trials(ticker)

    return ResearchCard.from_sources(
        ticker=ticker,
        filings=filing_data,
        trials=trial_data,
    )


def main(argv=None) -> int:
    """Run the CLI. Accepts a ticker symbol and prints a research card."""
    parser = argparse.ArgumentParser(description="Generate a research card for a ticker.")
    parser.add_argument("ticker", help="Ticker symbol, for example MRNA")
    args = parser.parse_args(argv)

    try:
        card = build_research_card(args.ticker.upper())
    except SecClientError as exc:
        print(f"SEC ingestion failed: {exc}", file=sys.stderr)
        return 1

    print(card.to_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
