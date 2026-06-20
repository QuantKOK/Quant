#!/usr/bin/env python
"""
Entry point for the Biotech Risk Scout CLI.

This script provides a simple command-line interface to generate a mock research
card for a given ticker. It demonstrates how the underlying ingestion and
reporting layers could be wired together without relying on external APIs.
"""

import argparse
import os
import sys

# Ensure that the parent directory is on the Python path so imports from the
# `scout` package resolve correctly when this file is executed as a script.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scout.ingest.clinical_trials import fetch_clinical_trials  # type: ignore
from scout.ingest.sec_filings import fetch_sec_filings  # type: ignore
from scout.reports.research_card import ResearchCard  # type: ignore


def build_mock_card(ticker: str) -> ResearchCard:
    """
    Construct a research card using mocked data from the ingestion stubs.
    """
    filing_data = fetch_sec_filings(ticker)
    trial_data = fetch_clinical_trials(ticker)

    return ResearchCard.from_sources(
        ticker=ticker,
        filings=filing_data,
        trials=trial_data,
    )


def main(argv=None) -> None:
    """
    Run the CLI. Accepts a ticker symbol and prints a mock research card.
    """
    parser = argparse.ArgumentParser(description="Generate a mock research card for a ticker.")
    parser.add_argument("ticker", help="Ticker symbol, for example ABCD")
    args = parser.parse_args(argv)

    card = build_mock_card(args.ticker.upper())
    print(card.to_text())


if __name__ == "__main__":
    main()
