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


def test_research_card_shows_structural_flags():
    card = ResearchCard.from_sources(
        ticker="XBIO",
        filings={
            "company_name": "Distressed Bio LLC",
            "cash": 5000000,
            "cash_runway_months": 3.0,
            "has_recent_financing_form": True,
            "has_going_concern": True,
            "has_reverse_split": True,
            "has_atm_or_offering": True,
            "has_delisting_or_listing_noncompliance": False,
            "has_shelf_registration": False,
            "has_registration_statement": False,
            "structural_red_flags": [],
            "latest_10q": None,
            "latest_10k": None,
            "latest_8k": None,
        },
        trials={
            "upcoming_catalyst": "Phase 1 update",
            "days_until_event": 200,
            "evidence_quality": "Low",
            "sponsor_names": [],
            "trials": [],
        },
    )
    text = card.to_text()
    assert "Financing watch:" in text
    assert "going concern language detected" in text
    assert "ATM/offering signal" in text
    assert "Structural risk flags:" in text
    assert "reverse split" in text


def test_research_card_no_flags_section_when_clean():
    card = ResearchCard.from_sources(
        ticker="CLEAN",
        filings={
            "company_name": "Clean Bio Inc.",
            "cash": 200000000,
            "cash_runway_months": 24.0,
            "has_recent_financing_form": False,
            "has_going_concern": False,
            "has_reverse_split": False,
            "has_atm_or_offering": False,
            "has_delisting_or_listing_noncompliance": False,
            "has_shelf_registration": False,
            "has_registration_statement": False,
            "structural_red_flags": [],
            "latest_10q": None,
            "latest_10k": None,
            "latest_8k": None,
        },
        trials={
            "upcoming_catalyst": "Phase 3 readout",
            "days_until_event": 60,
            "evidence_quality": "High",
            "sponsor_names": ["Clean Bio"],
            "trials": [{"status": "RECRUITING", "brief_title": "T", "phase": "Phase 3"}],
        },
    )
    text = card.to_text()
    assert "Financing watch:" not in text
    assert "Structural risk flags:" not in text
