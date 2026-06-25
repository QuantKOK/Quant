import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.ingest.sec_filings import _calculate_cash_runway  # noqa: E402


def make_company_facts(cash_value, ocf_value, start="2026-01-01", end="2026-03-31"):
    return {
        "facts": {
            "us-gaap": {
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            {
                                "val": cash_value,
                                "end": end,
                                "filed": "2026-05-10",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q1",
                            }
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "val": ocf_value,
                                "start": start,
                                "end": end,
                                "filed": "2026-05-10",
                                "form": "10-Q",
                                "fy": 2026,
                                "fp": "Q1",
                            }
                        ]
                    }
                },
            }
        }
    }


def test_calculate_cash_runway_from_negative_operating_cash_flow():
    facts = make_company_facts(cash_value=120000000, ocf_value=-30000000)
    result = _calculate_cash_runway(facts)

    assert result["cash"] == 120000000
    assert result["operating_cash_flow"] == -30000000
    assert result["monthly_burn"] is not None
    assert result["cash_runway_months"] is not None
    assert 11.5 <= result["cash_runway_months"] <= 12.5
    assert result["cash_fact"]["tag"] == "CashAndCashEquivalentsAtCarryingValue"
    assert result["operating_cash_flow_fact"]["tag"] == "NetCashProvidedByUsedInOperatingActivities"


def test_calculate_cash_runway_returns_unknown_when_cash_flow_is_positive():
    facts = make_company_facts(cash_value=120000000, ocf_value=10000000)
    result = _calculate_cash_runway(facts)

    assert result["cash"] == 120000000
    assert result["operating_cash_flow"] == 10000000
    assert result["monthly_burn"] is None
    assert result["cash_runway_months"] is None


def test_calculate_cash_runway_handles_missing_facts_cleanly():
    result = _calculate_cash_runway({"facts": {"us-gaap": {}}})

    assert result["cash"] is None
    assert result["operating_cash_flow"] is None
    assert result["monthly_burn"] is None
    assert result["cash_runway_months"] is None
    assert result["cash_fact"] is None
    assert result["operating_cash_flow_fact"] is None
