import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.scoring.research_priority import score_red_flags  # noqa: E402


def test_specific_financing_flag_replaces_broad_financing_fallback():
    common = {
        "cash_runway_months": 4,
        "trial_count": 1,
        "active_trial_count": 1,
        "evidence_quality": "Low",
    }

    broad_only = score_red_flags(
        {
            **common,
            "has_recent_financing_form": True,
            "has_atm_or_offering": False,
        }
    )
    specific_only = score_red_flags(
        {
            **common,
            "has_recent_financing_form": False,
            "has_atm_or_offering": True,
        }
    )
    both = score_red_flags(
        {
            **common,
            "has_recent_financing_form": True,
            "has_atm_or_offering": True,
        }
    )

    assert broad_only == -5
    assert specific_only == -5
    assert both == -5
