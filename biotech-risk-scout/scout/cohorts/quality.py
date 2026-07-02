"""Machine-readable data-quality report for a discovered cohort.

Findings are graded critical / high / medium / low. The report never assigns or
implies a scientific outcome; it only measures registry-data completeness and
internal consistency for downstream human adjudication.
"""

from __future__ import annotations

from typing import Any

REPORT_VERSION = 1

_KEY_FIELDS = (
    "title",
    "lead_sponsor",
    "primary_completion_date",
    "results_first_posted_date",
    "enrollment",
    "conditions",
    "primary_outcomes",
)


def _is_missing(field: str, value: Any) -> bool:
    if field in ("conditions", "primary_outcomes"):
        return not isinstance(value, list) or len(value) == 0
    if field == "enrollment":
        return not isinstance(value, int) or isinstance(value, bool)
    return not isinstance(value, str) or not value.strip()


def build_quality_report(
    examined: list[dict[str, Any]],
    included_fields: list[dict[str, Any]],
    *,
    generated_at: str | None = None,
    bounded_scan: bool = False,
    source_duplicate_nct_count: int = 0,
) -> dict[str, Any]:
    """Build the cohort data-quality report from examined + included studies."""
    eligible = [item for item in examined if item.get("eligible")]
    ineligible = [item for item in examined if not item.get("eligible")]

    exclusion_reason_counts: dict[str, int] = {}
    for item in ineligible:
        for reason in item.get("exclusion_reasons", []):
            exclusion_reason_counts[reason] = exclusion_reason_counts.get(reason, 0) + 1

    included_count = len(included_fields)

    # Missingness across the frozen included cohort.
    missingness: dict[str, dict[str, Any]] = {}
    for field in _KEY_FIELDS:
        missing = sum(1 for f in included_fields if _is_missing(field, f.get(field)))
        rate = round(missing / included_count, 4) if included_count else 0.0
        missingness[field] = {"missing": missing, "rate": rate}

    # Duplicate NCT IDs within the included cohort.
    nct_ids = [f.get("nct_id") for f in included_fields]
    duplicate_nct_count = len(nct_ids) - len(set(nct_ids))

    # Internal consistency vs. the cohort's own inclusion criteria (defensive:
    # these should all be zero for a correctly selected cohort).
    phase_inconsistent = sum(1 for f in included_fields if f.get("phases") != ["PHASE3"])
    allocation_inconsistent = sum(1 for f in included_fields if f.get("allocation") != "RANDOMIZED")
    intervention_inconsistent = sum(
        1 for f in included_fields if not {"DRUG", "BIOLOGICAL"}.intersection(f.get("intervention_types", []))
    )
    missing_primary_outcomes = sum(1 for f in included_fields if _is_missing("primary_outcomes", f.get("primary_outcomes")))
    has_results_missing_date = sum(
        1
        for f in included_fields
        if f.get("has_posted_results") and _is_missing("results_first_posted_date", f.get("results_first_posted_date"))
    )
    unsuitable_for_adjudication = sum(
        1
        for f in included_fields
        if _is_missing("primary_completion_date", f.get("primary_completion_date"))
        or _is_missing("primary_outcomes", f.get("primary_outcomes"))
    )

    findings: list[dict[str, Any]] = []

    def add(severity: str, code: str, count: int, message: str) -> None:
        if count > 0:
            findings.append({"severity": severity, "code": code, "count": count, "message": message})

    add("critical", "duplicate_nct_in_cohort", duplicate_nct_count, "Duplicate NCT IDs in the included cohort.")
    add(
        "high",
        "duplicate_nct_in_source_scan",
        source_duplicate_nct_count,
        "Duplicate NCT IDs appeared in the paginated source scan; records were deduplicated.",
    )
    add(
        "high",
        "bounded_candidate_scan",
        len(examined) if bounded_scan else 0,
        "Candidate discovery stopped at an explicit page limit; selection is only within the scanned window.",
    )
    add("critical", "included_missing_primary_outcomes", missing_primary_outcomes, "Included studies lack primary outcome definitions.")
    add("critical", "unsuitable_for_adjudication", unsuitable_for_adjudication, "Included studies cannot be adjudicated (missing primary completion date or primary outcomes).")
    add("critical", "cohort_allocation_criterion_violation", allocation_inconsistent, "Included studies are not RANDOMIZED (selection integrity failure).")
    add("critical", "cohort_intervention_criterion_violation", intervention_inconsistent, "Included studies lack a drug/biological intervention (selection integrity failure).")
    add("high", "has_results_flag_without_posting_date", has_results_missing_date, "Studies flagged as having results but missing a results-first-posted date.")
    add("high", "included_missing_enrollment", missingness["enrollment"]["missing"], "Included studies missing enrollment count.")
    add("medium", "combined_phase_label", phase_inconsistent, "Included studies use a combined phase label (e.g., PHASE2/PHASE3), not PHASE3 alone.")
    add("medium", "included_missing_conditions", missingness["conditions"]["missing"], "Included studies missing condition terms.")

    findings.append(
        {
            "severity": "low",
            "code": "not_population_representative",
            "count": included_count,
            "message": "Pilot cohort is capped and NCT-ordered; not population-representative.",
        }
    )

    severity_summary = {level: 0 for level in ("critical", "high", "medium", "low")}
    for finding in findings:
        severity_summary[finding["severity"]] += 1

    report = {
        "report_version": REPORT_VERSION,
        "examined_count": len(examined),
        "eligible_count": len(eligible),
        "ineligible_count": len(ineligible),
        "included_count": included_count,
        "duplicate_nct_count": duplicate_nct_count,
        "source_duplicate_nct_count": source_duplicate_nct_count,
        "bounded_scan": bounded_scan,
        "exclusion_reason_counts": dict(sorted(exclusion_reason_counts.items())),
        "missingness": missingness,
        "inconsistencies": {
            "combined_phase_label": phase_inconsistent,
            "allocation_not_randomized": allocation_inconsistent,
            "no_drug_or_biological": intervention_inconsistent,
            "missing_primary_outcomes": missing_primary_outcomes,
            "has_results_without_posting_date": has_results_missing_date,
            "unsuitable_for_adjudication": unsuitable_for_adjudication,
        },
        "findings": findings,
        "severity_summary": severity_summary,
    }
    if generated_at is not None:
        report["generated_at"] = generated_at
    return report
