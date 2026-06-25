import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.reports.research_card import ResearchCard  # noqa: E402


def test_research_card_text_includes_financial_and_trial_sections():
    card = ResearchCard.from_sources(
        ticker="TEST",
        filings={
            "company_name": "Test Biotech Inc.",
            "cash": 120000000,
            "operating_cash_flow": -30000000,
            "monthly_burn": 10000000,
            "cash_runway_months": 12.0,
            "has_recent_financing_form": False,
            "latest_10q": {
                "form": "10-Q",
                "filing_date": "2026-05-10",
                "primary_document": "test-10q.htm",
            },
            "latest_10k": None,
            "latest_8k": None,
            "notes": "SEC notes.",
        },
        trials={
            "upcoming_catalyst": "Phase 2 readout",
            "days_until_event": 90,
            "evidence_quality": "Moderate",
            "sponsor_names": ["Test Biotech"],
            "trials": [
                {
                    "status": "RECRUITING",
                    "brief_title": "Test trial",
                    "phase": "Phase 2",
                }
            ],
            "notes": "Trial notes.",
        },
    )

    text = card.to_text()

    assert "BIOTECH RISK SCOUT: TEST" in text
    assert "Company: Test Biotech Inc." in text
    assert "Trial count: 1 total / 1 active-like" in text
    assert "Cash: $120.0M" in text
    assert "Monthly burn: $10.0M" in text
    assert "Runway: 12.0 months" in text
    assert "Latest 10-Q: 10-Q filed 2026-05-10" in text
    assert "SEC: SEC notes." in text
    assert "Trials: Trial notes." in text
