"""Research-priority scoring for multi-ticker biotech scans.

The priority score ranks which tickers deserve human diligence first. It is a
research-prioritization score, not an instruction to trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GENERIC_CATALYSTS = {
    "",
    "unknown",
    "unknown catalyst",
    "no active trials found",
    "no trials found",
}

ACTIVE_TRIAL_STATUSES = {
    "RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "ENROLLING_BY_INVITATION",
    "NOT_YET_RECRUITING",
}


@dataclass(frozen=True)
class PriorityScore:
    """Breakdown for a research-priority score."""

    score: int
    main_reason: str
    red_flag_summary: str
    c1_catalyst_urgency: int
    c2_evidence_quality: int
    c3_financial_viability: int
    c4_dilution_structure: int
    c5_data_confidence: int
    c6_red_flags: int


def normalize_ticker_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize a card/source dictionary into scorer-friendly fields."""
    trials = data.get("trials") or []
    trial_count = data.get("trial_count")
    if trial_count is None:
        trial_count = len(trials)

    active_trial_count = data.get("active_trial_count")
    if active_trial_count is None:
        active_trial_count = sum(
            1 for trial in trials if str(trial.get("status", "")).upper() in ACTIVE_TRIAL_STATUSES
        )

    return {
        **data,
        "trial_count": trial_count,
        "active_trial_count": active_trial_count,
        "has_shelf": bool(data.get("has_shelf")),
        "has_recent_financing_form": bool(data.get("has_recent_financing_form")),
    }


def score_catalyst_urgency(days: int | None, catalyst: str | None) -> int:
    if days is not None:
        if days < 0:
            return 2
        if days <= 30:
            return 30
        if days <= 60:
            return 27
        if days <= 90:
            return 24
        if days <= 180:
            return 18
        if days <= 365:
            return 10
        return 4

    if catalyst and catalyst.lower().strip() not in GENERIC_CATALYSTS:
        return 6
    return 2


def score_evidence_quality(
    evidence_quality: str | None,
    active_trial_count: int | None,
    trial_count: int | None,
) -> int:
    base_map = {
        "high": 14,
        "moderate-high": 11,
        "moderate": 8,
        "low": 5,
        "none": 0,
    }
    base = base_map.get((evidence_quality or "").lower(), 3)

    n = active_trial_count
    if n is None:
        n = int((trial_count or 0) * 0.5)

    if n >= 5:
        modifier = 6
    elif n >= 3:
        modifier = 4
    elif n == 2:
        modifier = 2
    elif n == 1:
        modifier = 1
    else:
        modifier = 0

    return min(20, base + modifier)


def score_financial_viability(runway: float | None, days_until_event: int | None) -> int:
    if runway is None:
        points = 8
    elif runway >= 24:
        points = 20
    elif runway >= 18:
        points = 17
    elif runway >= 12:
        points = 13
    elif runway >= 6:
        points = 7
    else:
        points = 2

    if runway is not None and days_until_event is not None and days_until_event >= 0:
        months_to_event = days_until_event / 30.0
        if runway < months_to_event:
            points = max(0, points - 5)

    return points


def score_dilution_risk(
    dilution_risk: str | None,
    has_shelf: bool,
    has_recent_financing: bool,
    cash: float | None,
) -> int:
    base_map = {
        "low": 15,
        "moderate": 10,
        "high": 5,
        "watch": 5,
        "critical": 1,
    }
    points = base_map.get((dilution_risk or "").lower(), 8)

    if has_shelf:
        points -= 3
    if has_recent_financing:
        points -= 2
    if cash is not None and cash > 0:
        points += 1

    return max(0, points)


def score_data_confidence(data: dict[str, Any]) -> int:
    catalyst = (data.get("upcoming_catalyst") or data.get("catalyst") or "").lower().strip()
    evidence = (data.get("evidence_quality") or "").lower().strip()
    signals = [
        data.get("cash") is not None,
        data.get("monthly_burn") is not None or data.get("burn_rate") is not None,
        data.get("cash_runway_months") is not None,
        data.get("latest_10q") is not None,
        data.get("latest_10k") is not None,
        (data.get("trial_count") or 0) > 0,
        evidence not in ("", "none", "unknown"),
        catalyst not in GENERIC_CATALYSTS,
        data.get("days_until_event") is not None,
        bool(data.get("sponsor_names")),
    ]
    return int(sum(signals))


def score_red_flags(data: dict[str, Any]) -> int:
    deductions = 0
    runway = data.get("cash_runway_months")
    cash = data.get("cash")
    dilution = (data.get("dilution_risk") or "").lower()
    has_financing = bool(data.get("has_recent_financing_form"))
    trials = data.get("trial_count") or 0
    active = data.get("active_trial_count") or 0
    evidence = (data.get("evidence_quality") or "").lower()
    days = data.get("days_until_event")
    has_weak_runway = runway is not None and runway < 6
    has_specific_financing_flag = any(
        bool(data.get(flag))
        for flag in (
            "has_atm_or_offering",
            "has_registration_statement",
            "has_shelf_registration",
        )
    )

    if runway is not None and runway < 3:
        deductions += 15
    if dilution == "critical":
        deductions += 10
    if cash is not None and cash <= 0:
        deductions += 12
    if has_financing and has_weak_runway and not has_specific_financing_flag:
        deductions += 5
    if trials == 0 and active == 0:
        deductions += 5
    if evidence == "none" and days is None:
        deductions += 5

    if data.get("has_going_concern"):
        deductions += 10
    if data.get("has_reverse_split"):
        deductions += 7
    if data.get("has_delisting_or_listing_noncompliance"):
        deductions += 7
    if data.get("has_atm_or_offering") and has_weak_runway:
        deductions += 5
    if data.get("has_registration_statement") and has_weak_runway:
        deductions += 5
    if data.get("has_shelf_registration") and has_weak_runway:
        deductions += 3

    return -min(25, deductions)


def compute_priority_score(data: dict[str, Any]) -> PriorityScore:
    d = normalize_ticker_data(data)

    c1 = score_catalyst_urgency(d.get("days_until_event"), d.get("upcoming_catalyst") or d.get("catalyst"))
    c2 = score_evidence_quality(d.get("evidence_quality"), d.get("active_trial_count"), d.get("trial_count"))
    c3 = score_financial_viability(d.get("cash_runway_months"), d.get("days_until_event"))
    c4 = score_dilution_risk(
        d.get("dilution_risk"),
        bool(d.get("has_shelf")),
        bool(d.get("has_recent_financing_form")),
        d.get("cash"),
    )
    c5 = score_data_confidence(d)
    c6 = score_red_flags(d)

    zombie = (
        d.get("cash") is not None
        and d.get("cash") <= 0
        and (d.get("cash_runway_months") or 99) < 3
        and (d.get("dilution_risk") or "").lower() == "critical"
    )
    if zombie:
        return PriorityScore(5, "Zombie flag", "Critical cash/dilution combination", c1, c2, c3, c4, c5, c6)

    raw = c1 + c2 + c3 + c4 + c5 + c6
    score = max(0, min(100, int(raw)))
    return PriorityScore(
        score=score,
        main_reason=_derive_reason(c1, c2, c3, c4, c5, c6),
        red_flag_summary=_derive_red_flag_summary(d, c6),
        c1_catalyst_urgency=c1,
        c2_evidence_quality=c2,
        c3_financial_viability=c3,
        c4_dilution_structure=c4,
        c5_data_confidence=c5,
        c6_red_flags=c6,
    )


def _derive_reason(c1: int, c2: int, c3: int, c4: int, c5: int, c6: int) -> str:
    components = {
        "Near-term catalyst": c1,
        "Strong pipeline evidence": c2,
        "Solid financial runway": c3,
        "Clean cap structure": c4,
        "Rich data coverage": c5,
    }
    top = max(components, key=components.get)
    if c6 < -10:
        return f"{top} / red flags present"
    if c6 < 0:
        return f"{top} / moderate red flags"
    return top


def _derive_red_flag_summary(data: dict[str, Any], red_flag_score: int) -> str:
    if red_flag_score == 0:
        return "-"

    flags: list[str] = []
    runway = data.get("cash_runway_months")
    if runway is not None and runway < 3:
        flags.append("runway <3mo")
    if (data.get("dilution_risk") or "").lower() == "critical":
        flags.append("critical dilution")
    if data.get("cash") is not None and data.get("cash") <= 0:
        flags.append("cash <=0")
    if (data.get("trial_count") or 0) == 0 and (data.get("active_trial_count") or 0) == 0:
        flags.append("no trials")

    if data.get("has_going_concern"):
        flags.append("going concern")
    if data.get("has_reverse_split"):
        flags.append("reverse split")
    if data.get("has_delisting_or_listing_noncompliance"):
        flags.append("listing non-compliance")
    if data.get("has_atm_or_offering") and runway is not None and runway < 6:
        flags.append("ATM/offering")
    if data.get("has_registration_statement") and runway is not None and runway < 6:
        flags.append("registration statement")
    if data.get("has_shelf_registration") and runway is not None and runway < 6:
        flags.append("shelf + weak runway")

    if not flags:
        flags.append("red flags")
    return ", ".join(flags)
