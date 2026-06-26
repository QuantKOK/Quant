"""
ClinicalTrials.gov API v2 client.

Public surface
--------------
    fetch_clinical_trials(ticker: str, fallback_sponsor_name: str | None = None) -> dict

The function accepts either a ticker symbol such as "MRNA" or a plain company /
sponsor name such as "Moderna". It searches ClinicalTrials.gov by sponsor,
then enriches the result with upcoming-catalyst metadata compatible with
ResearchCard.from_sources(). If the ticker is not in the manual sponsor map,
it can fall back to an SEC company name supplied by the SEC ingestion layer.

API reference
-------------
    https://clinicaltrials.gov/data-api/api
    Base URL: https://clinicaltrials.gov/api/v2/studies
"""

from __future__ import annotations

from datetime import date, datetime
import json
import logging
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

_FIELDS = ",".join(
    [
        "NCTId",
        "BriefTitle",
        "OverallStatus",
        "Phase",
        "Condition",
        "InterventionName",
        "EnrollmentCount",
        "PrimaryCompletionDate",
        "CompletionDate",
        "LeadSponsorName",
        "CollaboratorName",
        "StartDate",
        "StudyType",
    ]
)

_TICKER_TO_SPONSOR: dict[str, str] = {
    "MRNA": "Moderna",
    "PFE": "Pfizer",
    "BNTX": "BioNTech",
    "JNJ": "Johnson & Johnson",
    "AZN": "AstraZeneca",
    "REGN": "Regeneron",
    "GILD": "Gilead Sciences",
    "ABBV": "AbbVie",
    "BMY": "Bristol-Myers Squibb",
    "MRK": "Merck",
    "LLY": "Eli Lilly",
    "AMGN": "Amgen",
    "BIIB": "Biogen",
    "VRTX": "Vertex Pharmaceuticals",
    "ALNY": "Alnylam Pharmaceuticals",
    "NTLA": "Intellia Therapeutics",
    "BEAM": "Beam Therapeutics",
    "CRSP": "CRISPR Therapeutics",
    "EDIT": "Editas Medicine",
    "IONS": "Ionis Pharmaceuticals",
    "INCY": "Incyte",
    "SGEN": "Seagen",
    "HALO": "Halozyme Therapeutics",
    "EXAS": "Exact Sciences",
    "RETA": "Reata Pharmaceuticals",
    "BLUE": "bluebird bio",
    "FATE": "Fate Therapeutics",
    "KYMR": "Kymera Therapeutics",
    "RVMD": "Revolution Medicines",
    "ACAD": "ACADIA Pharmaceuticals",
    "SAGE": "Sage Therapeutics",
    "PTCT": "PTC Therapeutics",
    "RARE": "Ultragenyx Pharmaceutical",
    "FOLD": "Amicus Therapeutics",
    "ARQT": "Arcutis Biotherapeutics",
    "IMVT": "Immunovant",
    "PRAX": "Praxis Precision Medicine",
    "KDNY": "Chinook Therapeutics",
    "RCUS": "Arcus Biosciences",
    "MGNX": "MacroGenics",
    "XENE": "Xenon Pharmaceuticals",
    "NKTR": "Nektar Therapeutics",
    "ARWR": "Arrowhead Pharmaceuticals",
    "AGEN": "Agenus",
    "ADMA": "ADMA Biologics",
    "TGTX": "TG Therapeutics",
    "DNLI": "Denali Therapeutics",
    "PRME": "Prime Medicine",
    "VERV": "Verve Therapeutics",
}

_COMPANY_SUFFIXES = (
    "incorporated",
    "inc",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "plc",
    "nv",
    "sa",
    "ag",
    "se",
    "holdings",
    "holding",
    "therapeutics",
    "pharmaceuticals",
    "biopharma",
    "biotherapeutics",
    "biosciences",
)

_ACTIVE_STATUSES = {
    "RECRUITING",
    "ACTIVE_NOT_RECRUITING",
    "ENROLLING_BY_INVITATION",
    "NOT_YET_RECRUITING",
}


def _get_json(url: str, params: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    """Fetch a JSON API response."""
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    logger.debug("GET %s", full_url)
    req = urllib.request.Request(
        full_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "biotech-risk-scout/1.0 research-tool",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _extract_trial(study: dict[str, Any]) -> dict[str, Any]:
    """Flatten one ClinicalTrials.gov v2 study object into our schema."""
    proto = study.get("protocolSection", {})
    id_mod = proto.get("identificationModule", {})
    status_mod = proto.get("statusModule", {})
    design_mod = proto.get("designModule", {})
    conds_mod = proto.get("conditionsModule", {})
    interventions_mod = proto.get("armsInterventionsModule", {})
    sponsors_mod = proto.get("sponsorCollaboratorsModule", {})

    phase_raw = design_mod.get("phases", [])
    if isinstance(phase_raw, list):
        phase = "/".join(phase_raw) if phase_raw else "N/A"
    else:
        phase = str(phase_raw) or "N/A"
    phase = phase.replace("PHASE", "Phase ").replace("_", "/")

    interventions = [
        iv.get("name", "")
        for iv in interventions_mod.get("interventions", [])
        if iv.get("name")
    ]

    enrollment_info = design_mod.get("enrollmentInfo", {})
    enrollment = enrollment_info.get("count")

    primary_completion = status_mod.get("primaryCompletionDateStruct", {}).get("date")
    completion = status_mod.get("completionDateStruct", {}).get("date")

    lead = sponsors_mod.get("leadSponsor", {}).get("name", "")
    collabs = [c.get("name", "") for c in sponsors_mod.get("collaborators", []) if c.get("name")]
    all_sponsors = [s for s in ([lead] + collabs) if s]

    return {
        "nct_id": id_mod.get("nctId", ""),
        "brief_title": id_mod.get("briefTitle", ""),
        "status": status_mod.get("overallStatus", ""),
        "phase": phase,
        "conditions": conds_mod.get("conditions", []),
        "interventions": interventions,
        "enrollment": enrollment,
        "primary_completion_date": primary_completion,
        "completion_date": completion,
        "sponsor_names": all_sponsors,
        "start_date": status_mod.get("startDateStruct", {}).get("date"),
    }


def _days_until(date_str: str | None) -> int | None:
    """Return calendar days from today until a YYYY-MM-DD or YYYY-MM date string."""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            target = datetime.strptime(date_str, fmt).date()
            return (target - date.today()).days
        except ValueError:
            continue
    return None


def _pick_upcoming_catalyst(trials: list[dict[str, Any]]) -> tuple[str, int | None]:
    """Choose the most imminent primary-completion date among active trials."""
    best_label = "No active trials found"
    best_days: int | None = None

    for trial in trials:
        if trial.get("status", "").upper() not in _ACTIVE_STATUSES:
            continue
        days = _days_until(trial.get("primary_completion_date"))
        if days is None:
            continue
        if days >= 0 and (best_days is None or days < best_days):
            best_days = days
            best_label = (
                f"{trial['brief_title'][:60]} "
                f"(Phase {trial['phase']}, primary completion {trial['primary_completion_date']})"
            )

    return best_label, best_days


def _score_evidence_quality(trials: list[dict[str, Any]]) -> str:
    """Heuristic evidence-quality score based on phase distribution."""
    phases = [trial.get("phase", "") for trial in trials]
    has_phase3 = any("3" in phase or "4" in phase for phase in phases)
    has_phase2 = any("2" in phase for phase in phases)
    trial_count = len(trials)

    if trial_count == 0:
        return "None"
    if has_phase3 and trial_count >= 3:
        return "High"
    if has_phase3 or (has_phase2 and trial_count >= 5):
        return "Moderate-High"
    if has_phase2:
        return "Moderate"
    return "Low"


def normalize_company_name(company_name: str | None) -> str:
    """Normalize SEC company names for sponsor fallback searches."""
    if not company_name:
        return ""
    name = company_name.strip()
    for suffix in ("/DE", "/DE/", "/MA", "/NY", "/CA"):
        if name.upper().endswith(suffix):
            name = name[: -len(suffix)].strip()
    name = name.replace(".", " ").replace(",", " ").replace("/", " ")
    words = [word for word in name.split() if word]
    while words and words[-1].lower() in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words).strip() or company_name.strip()


def _resolve_sponsor_query(ticker_or_name: str, fallback_sponsor_name: str | None = None) -> tuple[str, str]:
    """Resolve the sponsor query and explain where it came from."""
    raw = ticker_or_name.strip()
    upper = raw.upper()
    if upper in _TICKER_TO_SPONSOR:
        return _TICKER_TO_SPONSOR[upper], "manual_ticker_map"

    fallback = normalize_company_name(fallback_sponsor_name)
    if fallback:
        return fallback, "sec_company_name_fallback"

    return raw, "input"


def _resolve_sponsor_name(ticker: str) -> str:
    """Backward-compatible sponsor-name resolver."""
    sponsor_query, _ = _resolve_sponsor_query(ticker)
    return sponsor_query


def fetch_clinical_trials(ticker: str, fallback_sponsor_name: str | None = None) -> dict[str, Any]:
    """Fetch clinical trial data for a ticker or sponsor/company name."""
    sponsor_name, sponsor_query_source = _resolve_sponsor_query(ticker, fallback_sponsor_name)

    empty_result: dict[str, Any] = {
        "ticker": ticker,
        "sponsor_query": sponsor_name,
        "sponsor_query_source": sponsor_query_source,
        "upcoming_catalyst": "No trials found",
        "days_until_event": None,
        "evidence_quality": "None",
        "sponsor_names": [],
        "trials": [],
        "notes": "",
    }

    params: dict[str, Any] = {
        "query.spons": sponsor_name,
        "fields": _FIELDS,
        "pageSize": 100,
        "format": "json",
        "sort": "LastUpdatePostDate:desc",
    }

    raw_studies: list[dict[str, Any]] = []
    total_count: int = 0
    page = 1

    while True:
        try:
            data = _get_json(_BASE_URL, params)
        except urllib.error.HTTPError as exc:
            msg = f"ClinicalTrials.gov HTTP {exc.code}: {exc.reason}"
            logger.warning(msg)
            empty_result["notes"] = msg
            return empty_result
        except urllib.error.URLError as exc:
            msg = f"ClinicalTrials.gov network error: {exc.reason}"
            logger.warning(msg)
            empty_result["notes"] = msg
            return empty_result
        except json.JSONDecodeError as exc:
            msg = f"ClinicalTrials.gov returned invalid JSON: {exc}"
            logger.warning(msg)
            empty_result["notes"] = msg
            return empty_result
        except Exception as exc:  # noqa: BLE001
            msg = f"Unexpected error fetching trials: {exc}"
            logger.exception(msg)
            empty_result["notes"] = msg
            return empty_result

        page_studies = data.get("studies", [])
        raw_studies.extend(page_studies)
        if total_count == 0:
            total_count = data.get("totalCount", len(page_studies))

        next_token = data.get("nextPageToken")
        if not next_token or not page_studies:
            break

        params["pageToken"] = next_token
        page += 1
        logger.debug("Fetching page %d for sponsor '%s'", page, sponsor_name)

    if not raw_studies:
        empty_result["notes"] = (
            f"No trials found on ClinicalTrials.gov for sponsor '{sponsor_name}' "
            f"using {sponsor_query_source}."
        )
        return empty_result

    trials: list[dict[str, Any]] = []
    for study in raw_studies:
        try:
            trials.append(_extract_trial(study))
        except Exception as exc:  # noqa: BLE001
            nct = (
                study.get("protocolSection", {})
                .get("identificationModule", {})
                .get("nctId", "unknown")
            )
            logger.warning("Skipping malformed study %s: %s", nct, exc)

    seen: set[str] = set()
    all_sponsors: list[str] = []
    for trial in trials:
        for sponsor in trial.get("sponsor_names", []):
            if sponsor and sponsor not in seen:
                seen.add(sponsor)
                all_sponsors.append(sponsor)

    upcoming_catalyst, days_until_event = _pick_upcoming_catalyst(trials)
    evidence_quality = _score_evidence_quality(trials)

    status_counts: dict[str, int] = {}
    for trial in trials:
        status = trial.get("status", "Unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    status_summary = "; ".join(f"{count} {status}" for status, count in sorted(status_counts.items()))
    notes = (
        f"Retrieved {len(trials)} of {total_count} trials for sponsor '{sponsor_name}' "
        f"using {sponsor_query_source}. Status breakdown: {status_summary}."
    )

    for trial in trials:
        trial.pop("sponsor_names", None)
        trial.pop("start_date", None)

    return {
        "ticker": ticker,
        "sponsor_query": sponsor_name,
        "sponsor_query_source": sponsor_query_source,
        "upcoming_catalyst": upcoming_catalyst,
        "days_until_event": days_until_event,
        "evidence_quality": evidence_quality,
        "sponsor_names": all_sponsors,
        "trials": trials,
        "notes": notes,
    }
