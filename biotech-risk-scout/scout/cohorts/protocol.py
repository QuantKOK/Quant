"""Cohort selection protocol: rules, field extraction, and eligibility.

This module evaluates ClinicalTrials.gov v2 full-study objects against the
first historical pilot cohort protocol. It records *every* failing exclusion
reason for each study and never infers a scientific/efficacy outcome. "Has
posted results" is an inclusion criterion only; it is never a ``met``/``not_met``
label.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

COHORT_VERSION = "pilot-2020-2023-phase3-v1"

PRIMARY_COMPLETION_START = "2020-01-01"
PRIMARY_COMPLETION_END = "2023-12-31"
MIN_ENROLLMENT = 50
DEFAULT_COHORT_CAP = 50
MAX_COHORT_CAP = 100

DRUG_OR_BIOLOGICAL_TYPES = frozenset({"DRUG", "BIOLOGICAL"})

REGISTRY_URL_TEMPLATE = "https://clinicaltrials.gov/study/{nct_id}"

#: Exclusion reason codes (stable identifiers; every failing reason is recorded).
EXCLUSION_REASONS = (
    "not_interventional",
    "invalid_nct_id",
    "not_phase3",
    "no_drug_or_biological_intervention",
    "not_randomized",
    "not_industry_sponsored",
    "no_posted_results",
    "primary_completion_missing",
    "primary_completion_out_of_range",
    "enrollment_missing",
    "enrollment_below_minimum",
)

NCT_PATTERN = re.compile(r"^NCT\d{8}$")

INCLUSION_RULES = {
    "study_type": "INTERVENTIONAL",
    "phase": "PHASE3",
    "intervention_types_any_of": sorted(DRUG_OR_BIOLOGICAL_TYPES),
    "allocation": "RANDOMIZED",
    "lead_sponsor_class": "INDUSTRY",
    "has_posted_results": True,
    "primary_completion_between": [PRIMARY_COMPLETION_START, PRIMARY_COMPLETION_END],
    "min_enrollment": MIN_ENROLLMENT,
    "sort": "NCTId ascending (applied client-side; the API does not sort by NCT ID)",
    "pilot_cap_default": DEFAULT_COHORT_CAP,
}


def cohort_query_params(page_size: int) -> dict[str, Any]:
    """Return the exact ClinicalTrials.gov v2 search parameters for discovery.

    A coarse server-side filter narrows the set; authoritative eligibility is
    always re-checked client-side by :func:`evaluate_study`.
    """
    advanced = (
        "AREA[StudyType]INTERVENTIONAL"
        " AND AREA[Phase]PHASE3"
        " AND AREA[DesignAllocation]RANDOMIZED"
        " AND AREA[LeadSponsorClass]INDUSTRY"
        f" AND AREA[PrimaryCompletionDate]RANGE[{PRIMARY_COMPLETION_START},{PRIMARY_COMPLETION_END}]"
    )
    # NOTE: the API v2 does not support sorting by NCT ID (it returns HTTP 400:
    # "Unsupported sort field type: nct"). NCT ordering is therefore enforced
    # deterministically client-side after candidates are scanned.
    return {
        "filter.advanced": advanced,
        "aggFilters": "results:with",
        "pageSize": page_size,
        "format": "json",
        "countTotal": "true",
    }


def _protocol(study: dict[str, Any]) -> dict[str, Any]:
    return study.get("protocolSection", {}) if isinstance(study, dict) else {}


def extract_fields(study: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields the cohort needs from a full-study object."""
    proto = _protocol(study)
    identification = proto.get("identificationModule", {})
    status = proto.get("statusModule", {})
    design = proto.get("designModule", {})
    arms = proto.get("armsInterventionsModule", {})
    sponsors = proto.get("sponsorCollaboratorsModule", {})
    conditions = proto.get("conditionsModule", {})
    outcomes = proto.get("outcomesModule", {})

    interventions = [
        {"name": item.get("name", ""), "type": (item.get("type") or "").upper()}
        for item in arms.get("interventions", [])
        if isinstance(item, dict)
    ]
    primary_outcomes = [
        {
            "measure": item.get("measure", ""),
            "time_frame": item.get("timeFrame", ""),
            "description": item.get("description", ""),
        }
        for item in outcomes.get("primaryOutcomes", [])
        if isinstance(item, dict)
    ]
    results_first_posted = status.get("resultsFirstPostDateStruct", {}).get("date")
    has_results = bool(study.get("hasResults")) or bool(results_first_posted)

    return {
        "nct_id": identification.get("nctId", ""),
        "title": identification.get("briefTitle") or identification.get("officialTitle") or "",
        "study_type": (design.get("studyType") or "").upper(),
        "phases": [str(p).upper() for p in design.get("phases", []) if p],
        "allocation": (design.get("designInfo", {}).get("allocation") or "").upper(),
        "lead_sponsor": sponsors.get("leadSponsor", {}).get("name", ""),
        "lead_sponsor_class": (sponsors.get("leadSponsor", {}).get("class") or "").upper(),
        "interventions": interventions,
        "intervention_types": sorted({iv["type"] for iv in interventions if iv["type"]}),
        "conditions": [c for c in conditions.get("conditions", []) if c],
        "enrollment": design.get("enrollmentInfo", {}).get("count"),
        "primary_completion_date": status.get("primaryCompletionDateStruct", {}).get("date"),
        "overall_status": (status.get("overallStatus") or "").upper(),
        "has_posted_results": has_results,
        "results_first_posted_date": results_first_posted,
        "primary_outcomes": primary_outcomes,
    }


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}", text):
            return date.fromisoformat(f"{text}-01")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text)
        return None
    except ValueError:
        return None


def evaluate_study(study: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one full-study object against the cohort protocol.

    Returns ``{"nct_id", "eligible", "exclusion_reasons", "fields"}``. Records
    every failing reason, not just the first. Never assigns an outcome.
    """
    fields = extract_fields(study)
    reasons: list[str] = []

    if not NCT_PATTERN.fullmatch(str(fields["nct_id"])):
        reasons.append("invalid_nct_id")
    if fields["study_type"] != INCLUSION_RULES["study_type"]:
        reasons.append("not_interventional")
    if "PHASE3" not in fields["phases"]:
        reasons.append("not_phase3")
    if not DRUG_OR_BIOLOGICAL_TYPES.intersection(fields["intervention_types"]):
        reasons.append("no_drug_or_biological_intervention")
    if fields["allocation"] != INCLUSION_RULES["allocation"]:
        reasons.append("not_randomized")
    if fields["lead_sponsor_class"] != INCLUSION_RULES["lead_sponsor_class"]:
        reasons.append("not_industry_sponsored")
    if not fields["has_posted_results"]:
        reasons.append("no_posted_results")

    completion = _parse_iso_date(fields["primary_completion_date"])
    if completion is None:
        reasons.append("primary_completion_missing")
    elif not (date.fromisoformat(PRIMARY_COMPLETION_START) <= completion <= date.fromisoformat(PRIMARY_COMPLETION_END)):
        reasons.append("primary_completion_out_of_range")

    enrollment = fields["enrollment"]
    if not isinstance(enrollment, int) or isinstance(enrollment, bool):
        reasons.append("enrollment_missing")
    elif enrollment < MIN_ENROLLMENT:
        reasons.append("enrollment_below_minimum")

    return {
        "nct_id": fields["nct_id"],
        "eligible": not reasons,
        "exclusion_reasons": reasons,
        "fields": fields,
    }


def registry_url(nct_id: str) -> str:
    """Return the canonical ClinicalTrials.gov study URL for an NCT ID."""
    return REGISTRY_URL_TEMPLATE.format(nct_id=nct_id)
