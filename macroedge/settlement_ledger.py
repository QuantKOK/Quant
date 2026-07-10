"""Append-only, hash-chained ledger for MacroEdge candidate settlements."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections import Counter
from datetime import datetime
from typing import Any

from macroedge.journal import canonical_json
from macroedge.ledger import verify_ledger as verify_candidate_ledger
from macroedge.settlements import (
    SettlementError,
    build_settlement_record,
    verify_settlement_record,
)


GENESIS_HASH = "0" * 64
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LEDGER_LOCK = threading.Lock()


class SettlementLedgerError(SettlementError):
    """Raised when a settlement ledger operation fails."""


def find_candidate_in_ledger(journal_ledger_path: str, candidate_id: str) -> dict[str, Any]:
    """Return one candidate record from a verified candidate journal ledger."""
    verification = verify_candidate_ledger(journal_ledger_path)
    if not verification["ok"]:
        raise SettlementLedgerError(
            "candidate journal is invalid: " + "; ".join(verification["errors"])
        )
    with open(os.path.abspath(journal_ledger_path), "r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("candidate_id") == candidate_id:
                return record
    raise SettlementLedgerError(f"candidate_id not found: {candidate_id}")


def build_ledger_record(
    candidate: dict[str, Any],
    previous_hash: str = GENESIS_HASH,
    *,
    actual_result: str,
    settled_at: str,
    notes: str = "",
    mistake_tags: list[str] | None = None,
    recorded_at: str | None = None,
    settlement_id: str | None = None,
) -> dict[str, Any]:
    """Build a hash-chained settlement ledger record."""
    if not isinstance(previous_hash, str) or not _HEX64.fullmatch(previous_hash):
        raise SettlementLedgerError("previous_hash must be a 64-character lowercase hex digest")
    record = build_settlement_record(
        candidate,
        actual_result=actual_result,
        settled_at=settled_at,
        notes=notes,
        mistake_tags=mistake_tags,
        recorded_at=recorded_at,
        settlement_id=settlement_id,
    )
    record["previous_hash"] = previous_hash
    record["ledger_hash"] = _ledger_hash(record)
    return record


def append_settlement(
    ledger_path: str,
    candidate: dict[str, Any],
    *,
    actual_result: str,
    settled_at: str,
    notes: str = "",
    mistake_tags: list[str] | None = None,
    recorded_at: str | None = None,
    settlement_id: str | None = None,
) -> dict[str, Any]:
    """Append one settlement record to the settlement ledger and fsync it."""
    ledger_path = os.path.abspath(ledger_path)
    with _LEDGER_LOCK:
        verification = verify_ledger(ledger_path)
        if not verification["ok"]:
            raise SettlementLedgerError(
                "cannot append to an invalid settlement ledger: " + "; ".join(verification["errors"])
            )
        record = build_ledger_record(
            candidate,
            verification["head_hash"],
            actual_result=actual_result,
            settled_at=settled_at,
            notes=notes,
            mistake_tags=mistake_tags,
            recorded_at=recorded_at,
            settlement_id=settlement_id,
        )
        last_recorded_at = verification.get("last_recorded_at")
        if last_recorded_at and _parse_timestamp(record["recorded_at"]) < _parse_timestamp(last_recorded_at):
            raise SettlementLedgerError("recorded_at cannot be earlier than the settlement ledger head")
        if record["settlement_id"] in verification["settlement_ids"]:
            raise SettlementLedgerError(f"duplicate settlement_id: {record['settlement_id']}")
        candidate_id = record["candidate"]["candidate_id"]
        if candidate_id in verification["candidate_ids"]:
            raise SettlementLedgerError(f"duplicate settlement for candidate_id: {candidate_id}")

        os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(record))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


def verify_ledger(ledger_path: str) -> dict[str, Any]:
    """Verify settlement hashes, chain links, ids, and record schema."""
    ledger_path = os.path.abspath(ledger_path)
    if not os.path.exists(ledger_path):
        return _result([], GENESIS_HASH, set(), set(), count=0, last_recorded_at=None)

    try:
        with open(ledger_path, "r", encoding="utf-8") as handle:
            lines = list(handle)
    except OSError as exc:
        return _result([f"could not read settlement ledger: {exc}"], GENESIS_HASH, set(), set())

    errors: list[str] = []
    expected_previous = GENESIS_HASH
    settlement_ids: set[str] = set()
    candidate_ids: set[str] = set()
    previous_recorded_at: datetime | None = None
    count = 0

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            errors.append(f"line {line_number}: blank lines are not allowed")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(record, dict):
            errors.append(f"line {line_number}: record must be a JSON object")
            continue

        count += 1
        stored_ledger_hash = record.get("ledger_hash")
        if not isinstance(stored_ledger_hash, str) or stored_ledger_hash != _ledger_hash(record):
            errors.append(f"line {line_number}: ledger_hash mismatch")

        settlement_record = {
            key: value
            for key, value in record.items()
            if key not in ("previous_hash", "ledger_hash")
        }
        verification = verify_settlement_record(settlement_record)
        if not verification["ok"]:
            errors.extend(
                f"line {line_number}: settlement violation: {error}"
                for error in verification["errors"]
            )

        if record.get("previous_hash") != expected_previous:
            errors.append(f"line {line_number}: previous_hash does not match settlement ledger head")

        settlement_id = record.get("settlement_id")
        if not isinstance(settlement_id, str) or not settlement_id:
            errors.append(f"line {line_number}: missing settlement_id")
        elif settlement_id in settlement_ids:
            errors.append(f"line {line_number}: duplicate settlement_id")
        else:
            settlement_ids.add(settlement_id)

        candidate = record.get("candidate")
        candidate_id = candidate.get("candidate_id") if isinstance(candidate, dict) else None
        if not isinstance(candidate_id, str) or not candidate_id:
            errors.append(f"line {line_number}: missing candidate_id")
        elif candidate_id in candidate_ids:
            errors.append(f"line {line_number}: duplicate settlement for candidate_id")
        else:
            candidate_ids.add(candidate_id)

        try:
            recorded_at = _parse_timestamp(record.get("recorded_at"))
            if previous_recorded_at is not None and recorded_at < previous_recorded_at:
                errors.append(f"line {line_number}: non-monotonic recorded_at")
            previous_recorded_at = recorded_at
        except SettlementLedgerError:
            errors.append(f"line {line_number}: invalid recorded_at")

        if isinstance(stored_ledger_hash, str):
            expected_previous = stored_ledger_hash

    return _result(
        errors,
        expected_previous,
        settlement_ids,
        candidate_ids,
        count=count,
        last_recorded_at=previous_recorded_at.isoformat() if previous_recorded_at else None,
    )


def summarize_ledger(ledger_path: str) -> dict[str, Any]:
    """Return a compact summary of a verified settlement ledger."""
    verification = verify_ledger(ledger_path)
    summary = {
        "ok": verification["ok"],
        "record_count": verification["record_count"],
        "head_hash": verification["head_hash"],
        "last_recorded_at": verification["last_recorded_at"],
        "outcomes": {},
        "actual_results": {},
        "event_types": {},
        "average_brier_score": None,
        "void_count": 0,
        "errors": verification["errors"],
    }
    if not verification["ok"] or verification["record_count"] == 0:
        return summary

    outcomes: Counter[str] = Counter()
    actual_results: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    brier_scores: list[float] = []
    void_count = 0

    with open(os.path.abspath(ledger_path), "r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            candidate = record["candidate"]
            settlement = record["settlement"]
            outcomes[settlement["outcome"]] += 1
            actual_results[settlement["actual_result"]] += 1
            event_types[candidate["event_type"]] += 1
            if settlement["outcome"] == "void":
                void_count += 1
            elif settlement["brier_score"] is not None:
                brier_scores.append(float(settlement["brier_score"]))

    summary["outcomes"] = dict(sorted(outcomes.items()))
    summary["actual_results"] = dict(sorted(actual_results.items()))
    summary["event_types"] = dict(sorted(event_types.items()))
    summary["average_brier_score"] = (
        round(sum(brier_scores) / len(brier_scores), 6) if brier_scores else None
    )
    summary["void_count"] = void_count
    return summary


def _ledger_hash(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("ledger_hash", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SettlementLedgerError("recorded_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise SettlementLedgerError("recorded_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise SettlementLedgerError("recorded_at must include a timezone")
    return parsed


def _result(
    errors: list[str],
    head_hash: str,
    settlement_ids: set[str],
    candidate_ids: set[str],
    *,
    count: int = 0,
    last_recorded_at: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": not errors,
        "record_count": count,
        "head_hash": head_hash,
        "last_recorded_at": last_recorded_at,
        "settlement_ids": settlement_ids,
        "candidate_ids": candidate_ids,
        "errors": errors,
    }
