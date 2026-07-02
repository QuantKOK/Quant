"""Strict schema and validation for adjudication evidence packets.

A packet is a first-pass adjudication *draft* for a second human reviewer. This
module enforces the scientific guardrails: no final labels, no training-ready
status, no forecasting fields, non-null proposals must be evidence-backed
``needs_second_review`` drafts, and current registry snapshots stay
``historical_feature_snapshot_status: "unresolved"``.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = "1.0.0"

NCT_PATTERN = re.compile(r"^NCT\d{8}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_PATH_PATTERN = re.compile(r"^raw/NCT\d{8}\.json$")

PROPOSED_OUTCOMES = ("met", "not_met", "indeterminate", "censored")
CONFIDENCE_LEVELS = ("high", "medium", "low")
REVIEW_STATUSES = ("pending", "needs_second_review", "unresolved")

#: Evidence hierarchy (rank 1 = strongest). Used to annotate/compare sources.
SOURCE_TYPES = (
    "peer_reviewed_publication",
    "clinicaltrials_gov_results",
    "regulatory_document",
    "sponsor_topline",
)
SOURCE_TYPE_RANK = {name: index + 1 for index, name in enumerate(SOURCE_TYPES)}

MAX_EVIDENCE_NOTE_CHARS = 600  # paraphrase only; do not copy long passages

PACKET_FIELDS = (
    "packet_id",
    "nct_id",
    "source_snapshot_path",
    "source_snapshot_sha256",
    "primary_outcome_definitions",
    "registry_results",
    "evidence_sources",
    "conflicts",
    "proposed_outcome",
    "adjudication_rationale",
    "confidence",
    "reviewer",
    "reviewed_at",
    "review_status",
    "historical_feature_snapshot_status",
    "schema_version",
)
_PACKET_FIELD_SET = frozenset(PACKET_FIELDS)

EVIDENCE_SOURCE_FIELDS = ("source_type", "title", "url", "publication_date", "evidence_note")
_EVIDENCE_FIELD_SET = frozenset(EVIDENCE_SOURCE_FIELDS)

REGISTRY_RESULTS_FIELDS = (
    "has_posted_results",
    "results_first_posted_date",
    "primary_outcome_measure_titles",
    "primary_outcome_results",
    "posted_outcome_measure_count",
)
_REGISTRY_FIELD_SET = frozenset(REGISTRY_RESULTS_FIELDS)

#: Fields that must never appear (forecasting / final-label / training markers).
FORBIDDEN_FIELDS = frozenset(
    {
        "final_label",
        "final",
        "label",
        "gold_label",
        "training_label",
        "training_ready",
        "approved",
        "probability",
        "prob",
        "recommendation",
        "price_target",
        "rating",
    }
)


class AdjudicationError(ValueError):
    """Raised when a packet or evidence source violates the schema."""


def _text(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise AdjudicationError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise AdjudicationError(f"{field} must not be empty")
    return cleaned


def _valid_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        raise AdjudicationError(f"{field} must be an ISO date string (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise AdjudicationError(f"{field} must be a valid ISO date (YYYY-MM-DD)") from exc


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AdjudicationError(f"{field} must be an ISO-8601 timestamp with a timezone")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdjudicationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise AdjudicationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_evidence_source(raw: Any, *, reviewed_at: datetime | None = None) -> dict[str, Any]:
    """Validate one evidence source (real, dated, non-fabricated)."""
    if not isinstance(raw, dict):
        raise AdjudicationError("evidence source must be an object")
    unknown = sorted(set(raw) - _EVIDENCE_FIELD_SET)
    if unknown:
        raise AdjudicationError(f"evidence source has unknown field(s): {', '.join(unknown)}")

    source_type = _text(raw.get("source_type"), "evidence_source.source_type")
    if source_type not in SOURCE_TYPES:
        raise AdjudicationError(
            f"evidence_source.source_type must be one of {', '.join(SOURCE_TYPES)}"
        )
    title = _text(raw.get("title"), "evidence_source.title")
    url = _text(raw.get("url"), "evidence_source.url")
    if not _valid_http_url(url):
        raise AdjudicationError("evidence_source.url must be a valid http(s) URL")
    pub_date = _parse_date(raw.get("publication_date"), "evidence_source.publication_date")
    if reviewed_at is not None and pub_date > reviewed_at.date():
        raise AdjudicationError("evidence_source.publication_date cannot be after reviewed_at")
    note = _text(raw.get("evidence_note"), "evidence_source.evidence_note")
    if len(note) > MAX_EVIDENCE_NOTE_CHARS:
        raise AdjudicationError(
            f"evidence_source.evidence_note exceeds {MAX_EVIDENCE_NOTE_CHARS} chars (paraphrase only)"
        )
    return {
        "source_type": source_type,
        "title": title,
        "url": url,
        "publication_date": pub_date.isoformat(),
        "evidence_note": note,
    }


def _validate_primary_outcomes(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise AdjudicationError("primary_outcome_definitions must be a list")
    outcomes: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AdjudicationError("each primary outcome must be an object")
        outcomes.append(
            {
                "measure": _text(item.get("measure"), "primary_outcome.measure"),
                "time_frame": _text(item.get("time_frame"), "primary_outcome.time_frame", allow_empty=True),
                "description": _text(item.get("description"), "primary_outcome.description", allow_empty=True),
            }
        )
    return outcomes


def _validate_registry_results(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AdjudicationError("registry_results must be an object")
    unknown = sorted(set(raw) - _REGISTRY_FIELD_SET)
    if unknown:
        raise AdjudicationError(f"registry_results has unknown field(s): {', '.join(unknown)}")
    has_results = raw.get("has_posted_results")
    if not isinstance(has_results, bool):
        raise AdjudicationError("registry_results.has_posted_results must be a boolean")
    first_posted = raw.get("results_first_posted_date")
    if first_posted is not None:
        _parse_date(first_posted, "registry_results.results_first_posted_date")
    titles = raw.get("primary_outcome_measure_titles")
    if not isinstance(titles, list) or not all(isinstance(t, str) for t in titles):
        raise AdjudicationError("registry_results.primary_outcome_measure_titles must be a list of strings")
    primary_results = raw.get("primary_outcome_results")
    if not isinstance(primary_results, list) or not all(
        isinstance(item, dict) for item in primary_results
    ):
        raise AdjudicationError(
            "registry_results.primary_outcome_results must be a list of objects"
        )
    if any(str(item.get("type", "")).upper() != "PRIMARY" for item in primary_results):
        raise AdjudicationError(
            "registry_results.primary_outcome_results may contain only PRIMARY measures"
        )
    try:
        normalized_primary_results = json.loads(
            json.dumps(primary_results, ensure_ascii=False, sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise AdjudicationError(
            "registry_results.primary_outcome_results must contain JSON values"
        ) from exc
    count = raw.get("posted_outcome_measure_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise AdjudicationError("registry_results.posted_outcome_measure_count must be a non-negative int")
    return {
        "has_posted_results": has_results,
        "results_first_posted_date": first_posted,
        "primary_outcome_measure_titles": list(titles),
        "primary_outcome_results": normalized_primary_results,
        "posted_outcome_measure_count": count,
    }


def _validate_conflicts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise AdjudicationError("conflicts must be a list")
    conflicts: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AdjudicationError("each conflict must be an object")
        unknown = sorted(set(item) - {"description", "source_urls"})
        if unknown:
            raise AdjudicationError(f"conflict has unknown field(s): {', '.join(unknown)}")
        description = _text(item.get("description"), "conflict.description")
        source_urls = item.get("source_urls", [])
        if not isinstance(source_urls, list) or not all(isinstance(u, str) for u in source_urls):
            raise AdjudicationError("conflict.source_urls must be a list of strings")
        for candidate in source_urls:
            if not _valid_http_url(candidate):
                raise AdjudicationError(f"conflict.source_urls must be http(s): {candidate!r}")
        conflicts.append({"description": description, "source_urls": list(source_urls)})
    return conflicts


def validate_packet(raw: Any) -> dict[str, Any]:
    """Validate and normalize an adjudication packet. Raises ``AdjudicationError``."""
    if not isinstance(raw, dict):
        raise AdjudicationError("packet must be an object")

    forbidden = sorted(set(raw) & FORBIDDEN_FIELDS)
    if forbidden:
        raise AdjudicationError(
            f"forbidden field(s) {', '.join(forbidden)}: packets carry no final labels, "
            "training status, probabilities, or recommendations"
        )
    unknown = sorted(set(raw) - _PACKET_FIELD_SET)
    if unknown:
        raise AdjudicationError(f"packet has unknown field(s): {', '.join(unknown)}")
    missing = [field for field in PACKET_FIELDS if field not in raw]
    if missing:
        raise AdjudicationError(f"packet missing field(s): {', '.join(missing)}")

    if raw.get("schema_version") != SCHEMA_VERSION:
        raise AdjudicationError(f"schema_version must be {SCHEMA_VERSION!r}")

    packet_id = _text(raw.get("packet_id"), "packet_id")
    nct_id = _text(raw.get("nct_id"), "nct_id").upper()
    if not NCT_PATTERN.fullmatch(nct_id):
        raise AdjudicationError("nct_id must match NCT followed by 8 digits")

    snapshot_path = _text(raw.get("source_snapshot_path"), "source_snapshot_path")
    if not SNAPSHOT_PATH_PATTERN.fullmatch(snapshot_path):
        raise AdjudicationError("source_snapshot_path must look like raw/NCT########.json")
    if not snapshot_path.endswith(f"{nct_id}.json"):
        raise AdjudicationError("source_snapshot_path must reference this packet's nct_id")
    snapshot_sha = _text(raw.get("source_snapshot_sha256"), "source_snapshot_sha256")
    if not SHA256_PATTERN.fullmatch(snapshot_sha):
        raise AdjudicationError("source_snapshot_sha256 must be a lowercase SHA-256 hex digest")

    reviewed_at = _parse_timestamp(raw.get("reviewed_at"), "reviewed_at")
    reviewer = _text(raw.get("reviewer"), "reviewer")

    primary_outcomes = _validate_primary_outcomes(raw.get("primary_outcome_definitions"))
    registry_results = _validate_registry_results(raw.get("registry_results"))
    evidence_sources = [
        validate_evidence_source(item, reviewed_at=reviewed_at)
        for item in _require_list(raw.get("evidence_sources"), "evidence_sources")
    ]
    conflicts = _validate_conflicts(raw.get("conflicts"))
    rationale = _text(raw.get("adjudication_rationale"), "adjudication_rationale")

    review_status = _text(raw.get("review_status"), "review_status")
    if review_status not in REVIEW_STATUSES:
        raise AdjudicationError(f"review_status must be one of {', '.join(REVIEW_STATUSES)}")

    if raw.get("historical_feature_snapshot_status") != "unresolved":
        raise AdjudicationError("historical_feature_snapshot_status must be 'unresolved'")

    proposed_outcome = raw.get("proposed_outcome")
    confidence = raw.get("confidence")
    _validate_proposal(proposed_outcome, confidence, review_status, evidence_sources)

    return {
        "packet_id": packet_id,
        "nct_id": nct_id,
        "source_snapshot_path": snapshot_path,
        "source_snapshot_sha256": snapshot_sha,
        "primary_outcome_definitions": primary_outcomes,
        "registry_results": registry_results,
        "evidence_sources": evidence_sources,
        "conflicts": conflicts,
        "proposed_outcome": proposed_outcome,
        "adjudication_rationale": rationale,
        "confidence": confidence,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at.isoformat(),
        "review_status": review_status,
        "historical_feature_snapshot_status": "unresolved",
        "schema_version": SCHEMA_VERSION,
    }


def _validate_proposal(
    proposed_outcome: Any,
    confidence: Any,
    review_status: str,
    evidence_sources: list[dict[str, Any]],
) -> None:
    if proposed_outcome is None:
        if confidence is not None:
            raise AdjudicationError("confidence must be null when proposed_outcome is null")
        if review_status == "needs_second_review":
            raise AdjudicationError("a null proposed_outcome cannot be needs_second_review")
        return

    if proposed_outcome not in PROPOSED_OUTCOMES:
        raise AdjudicationError(
            f"proposed_outcome must be null or one of {', '.join(PROPOSED_OUTCOMES)}"
        )
    # Non-null proposals are drafts for a second reviewer, never final labels.
    if review_status != "needs_second_review":
        raise AdjudicationError("a non-null proposed_outcome must have review_status 'needs_second_review'")
    if confidence not in CONFIDENCE_LEVELS:
        raise AdjudicationError(
            f"a non-null proposed_outcome requires confidence in {', '.join(CONFIDENCE_LEVELS)}"
        )
    if not evidence_sources:
        raise AdjudicationError("a non-null proposed_outcome requires at least one dated evidence source")


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise AdjudicationError(f"{field} must be a list")
    return value
