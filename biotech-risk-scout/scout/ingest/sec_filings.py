"""
Stub for SEC filings ingestion.

This module provides a placeholder function for retrieving SEC filings for a
specific ticker. In a future implementation, this would interface with the
SEC's EDGAR APIs or a third-party service to download and parse 10-Q, 10-K,
8-K and other relevant filings. For now, it returns fixed dummy data that
illustrates the expected structure.
"""

from typing import Any, Dict


def fetch_sec_filings(ticker: str) -> Dict[str, Any]:
    """
    Mock SEC filing ingestion for a ticker.
    """
    # Future implementation:
    # - hit the SEC EDGAR API
    # - parse recent 10-Q and 10-K filings
    # - extract cash, burn rate, shares outstanding
    # - detect shelf registrations or ATM offerings
    return {
        "ticker": ticker.upper(),
        "cash_runway_months": 12.0,
        "burn_rate": 5.0,
        "has_shelf": False,
        "notes": "Mock filing data for testing purposes.",
    }
