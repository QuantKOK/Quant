"""Strict schema and validation for historical clinical-trial outcome records.

Design rules that this module enforces:

* Registry status and scientific outcome are separate concepts. This module
  never derives ``scientific_outcome`` from ``registry_status``. A ``COMPLETED``
  trial is not automatically ``met``; a ``TERMINATED`` trial is not automatically
  ``not_met``.
* Point-in-time integrity. Outcome observation and every piece of evidence must
  be dated at or after ``prediction_cutoff_at`` so a record cannot encode
  lookahead/leakage.
* Provenance. ``met`` / ``not_met`` require an adjudication rationale and at
  least one HTTP(S) evidence URL.
* No forecasting artifacts. Probabilities and investment recommendations are
  rejected outright, as are any unknown fields (to prevent silent schema drift).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = "1.0.0"

#: The four permitted adjudicated outcomes. ``censored`` and ``indeterminate``
#: are deliberately distinct: ``censored`` means the trial cannot inform the
#: endpoint (e.g., stopped for business reasons), while ``indeterminate`` means
#: the evidence is genuinely ambiguous.
SCIENTIFIC_OUTCOMES = ("met", "not_met", "indeterminate", "censored")

#: Outcomes that require a rationale plus at least one HTTP(S) evidence URL.
EVIDENCE_REQUIRED_OUTCOMES = ("met", "not_met")

#: Canonical field order (used for readable serialization; hashing is order
#: independent because the dataset is written with ``sort_keys=True``).
FIELD_ORDER = (
    "record_id",
    "nct_id",
    "sponsor",
    "intervention",
    "indication",
    "phase",
    "prediction_cutoff_at",
    "outcome_observed_at",
    "registry_status",
    "scientific_outcome",
    "outcome_definition",
    "adjudication_rationale",
    "evidence_urls",
    "source_publication_dates",
    "adjudicator",
    "schema_version",
)

ALLOWED_FIELDS = frozenset(FIELD_ORDER)

#: Required in the source record. ``evidence_urls`` and
#: ``source_publication_dates`` default to empty lists; ``schema_version`` is
#: stamped by the builder.
REQUIRED_FIELDS = (
    "record_id",
    "nct_id",
    "sponsor",
    "intervention",
    "indication",
    "phase",
    "prediction_cutoff_at",
    "outcome_observed_at",
    "registry_status",
    "scientific_outcome",
    "outcome_definition",
    "adjudication_rationale",
    "adjudicator",
)

#: Fields that must never appear: this dataset records observed scientific facts,
#: not forecasts or trade ideas.
FORBIDDEN_FIELDS = frozenset(
    {
        "probability",
        "probabilities",
        "prob",
        "odds",
        "expected_value",
        "confidence",
        "recommendation",
        "investment_recommendation",
        "rating",
        "price_target",
        "target_price",
        "position_size",
        "conviction",
        "buy",
        "sell",
        "hold",
    }
)

NCT_PATTERN = re.compile(r"^NCT\d{8}$")


class OutcomeValidationError(ValueError):
    """Raised when a source record violates the historical-outcome schema."""


@dataclass(frozen=True)
class HistoricalOutcomeRecord:
    """A single validated, normalized historical outcome record."""

    record_id: str
    nct_id: str
    sponsor: str
    intervention: str
    indication: str
    phase: str
    prediction_cutoff_at: str
    outcome_observed_at: str
    registry_status: str
    scientific_outcome: str
    outcome_definition: str
    adjudication_rationale: str
    evidence_urls: tuple[str, ...]
    source_publication_dates: tuple[str, ...]
    adjudicator: str
    schema_version: str

    def to_json_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict in canonical field order."""
        return {
            "record_id": self.record_id,
            "nct_id": self.nct_id,
            "sponsor": self.sponsor,
            "intervention": self.intervention,
            "indication": self.indication,
            "phase": self.phase,
            "prediction_cutoff_at": self.prediction_cutoff_at,
            "outcome_observed_at": self.outcome_observed_at,
            "registry_status": self.registry_status,
            "scientific_outcome": self.scientific_outcome,
            "outcome_definition": self.outcome_definition,
            "adjudication_rationale": self.adjudication_rationale,
            "evidence_urls": list(self.evidence_urls),
            "source_publication_dates": list(self.source_publication_dates),
            "adjudicator": self.adjudicator,
            "schema_version": self.schema_version,
        }


def validate_record(raw: Any) -> HistoricalOutcomeRecord:
    """Validate and normalize a single source record.

    Raises ``OutcomeValidationError`` with a specific message on any violation.
    Never infers the scientific outcome from registry status.
    """
    if not isinstance(raw, dict):
        raise OutcomeValidationError(f"record must be a JSON object, got {type(raw).__name__}")

    _reject_forbidden_and_unknown_fields(raw)

    for field in REQUIRED_FIELDS:
        if field not in raw:
            raise OutcomeValidationError(f"missing required field: {field}")

    record_id = _clean_str(raw["record_id"], "record_id")
    nct_id = _clean_str(raw["nct_id"], "nct_id").upper()
    if not NCT_PATTERN.match(nct_id):
        raise OutcomeValidationError(
            f"nct_id must match NCT followed by 8 digits, got {raw['nct_id']!r}"
        )

    sponsor = _clean_str(raw["sponsor"], "sponsor")
    intervention = _clean_str(raw["intervention"], "intervention")
    indication = _clean_str(raw["indication"], "indication")
    phase = _clean_str(raw["phase"], "phase")
    registry_status = _clean_str(raw["registry_status"], "registry_status")
    outcome_definition = _clean_str(raw["outcome_definition"], "outcome_definition")
    adjudication_rationale = _clean_str(raw["adjudication_rationale"], "adjudication_rationale")
    adjudicator = _clean_str(raw["adjudicator"], "adjudicator")

    scientific_outcome = _clean_str(raw["scientific_outcome"], "scientific_outcome").lower()
    if scientific_outcome not in SCIENTIFIC_OUTCOMES:
        raise OutcomeValidationError(
            "scientific_outcome must be one of "
            f"{', '.join(SCIENTIFIC_OUTCOMES)}, got {raw['scientific_outcome']!r}"
        )

    cutoff_dt = _parse_timestamp(raw["prediction_cutoff_at"], "prediction_cutoff_at")
    observed_dt = _parse_timestamp(raw["outcome_observed_at"], "outcome_observed_at")
    if observed_dt < cutoff_dt:
        raise OutcomeValidationError(
            "outcome_observed_at cannot predate prediction_cutoff_at"
        )

    evidence_urls, publication_dates = _clean_evidence(
        raw.get("evidence_urls", []),
        raw.get("source_publication_dates", []),
        cutoff_dt,
    )

    if scientific_outcome in EVIDENCE_REQUIRED_OUTCOMES:
        if not adjudication_rationale:
            raise OutcomeValidationError(
                f"scientific_outcome '{scientific_outcome}' requires an adjudication_rationale"
            )
        if not evidence_urls:
            raise OutcomeValidationError(
                f"scientific_outcome '{scientific_outcome}' requires at least one HTTP(S) evidence URL"
            )

    schema_version = _validate_schema_version(raw.get("schema_version"))

    return HistoricalOutcomeRecord(
        record_id=record_id,
        nct_id=nct_id,
        sponsor=sponsor,
        intervention=intervention,
        indication=indication,
        phase=phase,
        prediction_cutoff_at=cutoff_dt.isoformat(),
        outcome_observed_at=observed_dt.isoformat(),
        registry_status=registry_status,
        scientific_outcome=scientific_outcome,
        outcome_definition=outcome_definition,
        adjudication_rationale=adjudication_rationale,
        evidence_urls=tuple(evidence_urls),
        source_publication_dates=tuple(publication_dates),
        adjudicator=adjudicator,
        schema_version=schema_version,
    )


def _reject_forbidden_and_unknown_fields(raw: dict[str, Any]) -> None:
    forbidden = sorted(set(raw) & FORBIDDEN_FIELDS)
    if forbidden:
        raise OutcomeValidationError(
            "forbidden field(s) "
            f"{', '.join(forbidden)}: probabilities and investment recommendations "
            "do not belong in this dataset"
        )
    unknown = sorted(set(raw) - ALLOWED_FIELDS)
    if unknown:
        raise OutcomeValidationError(
            f"unknown field(s) {', '.join(unknown)} (rejected to prevent silent schema drift)"
        )


def _clean_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise OutcomeValidationError(f"{field} must be a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise OutcomeValidationError(f"{field} must not be empty")
    return cleaned


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise OutcomeValidationError(f"{field} must be a non-empty ISO-8601 string")
    text = value.strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise OutcomeValidationError(
            f"{field} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise OutcomeValidationError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not value.strip():
        raise OutcomeValidationError(f"{field} entries must be non-empty ISO-8601 date strings")
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise OutcomeValidationError(
            f"{field} entry is not a valid ISO-8601 date: {value!r}"
        ) from exc


def _clean_evidence(
    urls_value: Any,
    dates_value: Any,
    cutoff_dt: datetime,
) -> tuple[list[str], list[str]]:
    """Validate, pair, deduplicate, and deterministically sort evidence."""
    if not isinstance(urls_value, list):
        raise OutcomeValidationError("evidence_urls must be a list of HTTP(S) URLs")
    if not isinstance(dates_value, list):
        raise OutcomeValidationError("source_publication_dates must be a list of ISO-8601 dates")
    if len(urls_value) != len(dates_value):
        raise OutcomeValidationError(
            "evidence_urls and source_publication_dates must have the same length "
            "so every source is dated"
        )

    cutoff_date = cutoff_dt.date()
    pairs: list[tuple[str, str]] = []
    for url_item, date_item in zip(urls_value, dates_value):
        if not isinstance(url_item, str) or not url_item.strip():
            raise OutcomeValidationError("evidence_urls entries must be non-empty strings")
        url = url_item.strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise OutcomeValidationError(f"evidence URL must be HTTP(S): {url!r}")
        pub_date = _parse_date(date_item, "source_publication_dates")
        if pub_date < cutoff_date:
            raise OutcomeValidationError(
                "outcome evidence cannot predate prediction_cutoff_at: "
                f"source publication {pub_date.isoformat()} < cutoff {cutoff_date.isoformat()}"
            )
        pairs.append((url, pub_date.isoformat()))

    unique_pairs = sorted(set(pairs))
    return (
        [url for url, _ in unique_pairs],
        [publication_date for _, publication_date in unique_pairs],
    )


def _validate_schema_version(value: Any) -> str:
    if value is None:
        return SCHEMA_VERSION
    if not isinstance(value, str) or value.strip() != SCHEMA_VERSION:
        raise OutcomeValidationError(
            f"schema_version must be {SCHEMA_VERSION!r} if provided, got {value!r}"
        )
    return SCHEMA_VERSION
