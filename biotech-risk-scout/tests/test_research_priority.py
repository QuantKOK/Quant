import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.scoring.research_priority import compute_priority_score  # noqa: E402


def test_strong_near_term_story_scores_high():
    result = compute_priority_score(
        {
            "upcoming_catalyst": "Phase 3 readout",
            "days_until_event": 45,
            "evidence_quality": "High",
            "active_trial_count": 3,
            "trial_count": 5,
            "cash": 500000000,
            "monthly_burn": 20000000,
            "cash_runway_months": 25,
            "dilution_risk": "Low",
            "has_shelf": False,
            "has_recent_financing_form": False,
            "latest_10q": {"form": "10-Q"},
            "latest_10k": {"form": "10-K"},
            "sponsor_names": ["Example Bio"],
        }
    )

    assert result.score >= 85
    assert "catalyst" in result.main_reason.lower() or "pipeline" in result.main_reason.lower()


def test_distressed_name_is_deprioritized():
    result = compute_priority_score(
        {
            "upcoming_catalyst": "Phase 1 update",
            "days_until_event": 400,
            "evidence_quality": "Low",
            "active_trial_count": 1,
            "trial_count": 1,
            "cash": 0,
            "cash_runway_months": 2,
            "dilution_risk": "Critical",
            "has_shelf": True,
            "has_recent_financing_form": True,
        }
    )

    assert result.score == 5
    assert result.main_reason == "Zombie flag"


def test_data_poor_name_scores_low():
    result = compute_priority_score(
        {
            "upcoming_catalyst": "No trials found",
            "days_until_event": None,
            "evidence_quality": "None",
            "active_trial_count": 0,
            "trial_count": 0,
            "cash_runway_months": None,
            "dilution_risk": None,
        }
    )

    assert result.score < 25
    assert result.c6_red_flags < 0
