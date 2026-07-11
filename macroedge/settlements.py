"""Settlement/post-mortem records for MacroEdge trade candidates.

Settlement records are append-only follow-ups to candidate journal entries. They
do not rewrite the original candidate ledger; instead they link back to the
frozen ``candidate_id`` and ``candidate_hash``.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from typing import Any

from macroedge.journal import TradeJournalError, build_trade_candidate, canonical_json


SCHEMA_VERSION = 1
SUPPORTED_ACTUAL_RESULTS = {"YES", "NO", "VOID"}


class SettlementError(TradeJournalError):
    """Raised when a settlement/post-mortem record is invalid."""


def build_settlement_record(
    candidate: dict[str, Any],
    *,
    actual_result: str,
    settled_at: str,
    notes: str = "",
    mistake_tags: list[str] | None = None,
    recorded_at: str | None = None,
    settlement_id: str | None = None,
) -> dict[str, Any]:
    """Build a settlement record linked to a validated candidate record."""
    candidate = _verified_candidate(candidate)
    result = actual_result.upper()
    if result not in SUPPORTED_ACTUAL_RESULTS:
        raise SettlementError("actual_result must be YES, NO, or VOID")

    settled = _parse_timestamp(settled_at, "settled_at")
    recorded = _parse_timestamp(recorded_at or _utc_now_iso(), "recorded_at")
    if recorded < settled:
        raise SettlementError("recorded_at cannot be earlier than settled_at")

    side = candidate["market"]["side"]
    outcome = _outcome_for_side(side, result)
    brier_score = _brier_score(candidate["thesis"]["fair_probability"], outcome)
    tags = _mistake_tags(mistake_tags or [])

    record = {
        "schema_version": SCHEMA_VERSION,
        "settlement_id": settlement_id or str(uuid.uuid4()),
        "recorded_at": recorded.isoformat(),
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "candidate_hash": candidate["candidate_hash"],
            "created_at": candidate["created_at"],
            "event_type": candidate["event"]["event_type"],
            "side": side,
            "entry_price": candidate["market"]["entry_price"],
            "fair_probability": candidate["thesis"]["fair_probability"],
            "planned_risk_usd": candidate["risk"]["planned_risk_usd"],
        },
        "settlement": {
            "settled_at": settled.isoformat(),
            "actual_result": result,
            "outcome": outcome,
            "brier_score": brier_score,
            "notes": _optional_text(notes),
            "mistake_tags": tags,
        },
    }
    record["settlement_hash"] = _settlement_hash(record)
    return record


def verify_settlement_record(record: dict[str, Any]) -> dict[str, Any]:
    """Verify a standalone settlement record."""
    errors: list[str] = []
    if not isinstance(record, dict):
        return _verification_result(["record must be a JSON object"], None)

    stored_hash = record.get("settlement_hash")
    if not isinstance(stored_hash, str) or not stored_hash:
        errors.append("missing settlement_hash")
    elif stored_hash != _settlement_hash(record):
        errors.append("settlement_hash mismatch")

    try:
        _validate_settlement_record(record)
    except SettlementError as exc:
        errors.append(f"schema violation: {exc}")

    return _verification_result(errors, stored_hash if isinstance(stored_hash, str) else None)


def _validate_settlement_record(record: dict[str, Any]) -> None:
    if record.get("schema_version") != SCHEMA_VERSION:
        raise SettlementError(f"schema_version must be {SCHEMA_VERSION}")
    _require_text(record, "settlement_id")
    recorded_at = _parse_timestamp(_require_text(record, "recorded_at"), "recorded_at")
    candidate = _require_dict(record, "candidate")
    settlement = _require_dict(record, "settlement")
    _require_text(candidate, "candidate_id")
    candidate_hash = _require_text(candidate, "candidate_hash")
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_hash):
        raise SettlementError("candidate_hash must be a lowercase SHA-256 hex digest")
    _parse_timestamp(_require_text(candidate, "created_at"), "candidate.created_at")
    _require_text(candidate, "event_type")
    side = _require_text(candidate, "side")
    if side not in {"YES", "NO"}:
        raise SettlementError("candidate.side must be YES or NO")
    fair_probability = _require_probability(candidate, "fair_probability")
    _require_probability(candidate, "entry_price")
    _positive_number(candidate, "planned_risk_usd")
    actual_result = _require_text(settlement, "actual_result")
    if actual_result not in SUPPORTED_ACTUAL_RESULTS:
        raise SettlementError("actual_result must be YES, NO, or VOID")
    settled_at = _parse_timestamp(_require_text(settlement, "settled_at"), "settlement.settled_at")
    if recorded_at < settled_at:
        raise SettlementError("recorded_at cannot be earlier than settled_at")
    outcome = _require_text(settlement, "outcome")
    expected_outcome = _outcome_for_side(side, actual_result)
    if outcome != expected_outcome:
        raise SettlementError("settlement.outcome does not match candidate side and actual_result")
    expected_brier = _brier_score(fair_probability, outcome)
    if settlement.get("brier_score") != expected_brier:
        raise SettlementError("settlement.brier_score does not match fair_probability and outcome")
    _optional_text(settlement.get("notes", ""))
    _mistake_tags(settlement.get("mistake_tags", []))


def _verified_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise SettlementError("candidate must be a JSON object")
    try:
        rebuilt = build_trade_candidate(
            _candidate_draft(candidate),
            created_at=_require_text(candidate, "created_at"),
            candidate_id=_require_text(candidate, "candidate_id"),
        )
    except TradeJournalError as exc:
        raise SettlementError(f"candidate verification failed: {exc}") from exc
    stored_candidate = {
        key: value
        for key, value in candidate.items()
        if key not in ("previous_hash", "ledger_hash")
    }
    if canonical_json(stored_candidate) != canonical_json(rebuilt):
        raise SettlementError("candidate verification failed: candidate_hash/content mismatch")
    return rebuilt


def _candidate_draft(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": candidate.get("event"),
        "market": candidate.get("market"),
        "thesis": candidate.get("thesis"),
        "risk": candidate.get("risk"),
        "post_mortem": candidate.get("post_mortem"),
        "contract_observation": candidate.get("contract_observation"),
    }


def _settlement_hash(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("settlement_hash", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _outcome_for_side(side: str, actual_result: str) -> str:
    if actual_result == "VOID":
        return "void"
    return "won" if side == actual_result else "lost"


def _brier_score(fair_probability: float, outcome: str) -> float | None:
    if outcome == "void":
        return None
    actual = 1.0 if outcome == "won" else 0.0
    return round((fair_probability - actual) ** 2, 6)


def _mistake_tags(value: list[str]) -> list[str]:
    if not isinstance(value, list):
        raise SettlementError("mistake_tags must be a list")
    tags = []
    for tag in value:
        if not isinstance(tag, str) or not tag.strip():
            raise SettlementError("mistake_tags must contain only non-empty strings")
        tags.append(tag.strip())
    return tags


def _require_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise SettlementError(f"{key} must be a JSON object")
    return item


def _require_text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise SettlementError(f"{key} must be a non-empty string")
    return item.strip()


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SettlementError("notes must be a string")
    return value.strip()


def _require_probability(value: dict[str, Any], key: str) -> float:
    item = value.get(key)
    if not isinstance(item, int | float) or isinstance(item, bool):
        raise SettlementError(f"{key} must be a number between 0 and 1")
    probability = float(item)
    if probability <= 0 or probability >= 1:
        raise SettlementError(f"{key} must be greater than 0 and less than 1")
    return probability


def _positive_number(value: dict[str, Any], key: str) -> float:
    item = value.get(key)
    if not isinstance(item, int | float) or isinstance(item, bool) or float(item) <= 0:
        raise SettlementError(f"{key} must be a positive number")
    return round(float(item), 2)


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SettlementError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise SettlementError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise SettlementError(f"{field} must include a timezone")
    return parsed


def _utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _verification_result(errors: list[str], settlement_hash: str | None) -> dict[str, Any]:
    return {
        "ok": not errors,
        "settlement_hash": settlement_hash,
        "errors": errors,
    }
