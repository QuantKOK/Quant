"""Append-only, hash-chained ledger for MacroEdge trade candidates.

This wraps :func:`macroedge.journal.build_trade_candidate` with predecessor-hash
chaining, mirroring the biotech prediction-ledger pattern, so a research journal
of macro event-contract candidates is deterministic and tamper-evident.

Offline only: no market data, no Kalshi API, no order placement or execution.
This is a probability-research journal, not a betting bot.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections import Counter
from datetime import datetime
from typing import Any

from macroedge.journal import (
    TradeJournalError,
    build_trade_candidate,
    canonical_json,
)

GENESIS_HASH = "0" * 64
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LEDGER_LOCK = threading.Lock()


class LedgerError(TradeJournalError):
    """Raised when a ledger operation or record violates the ledger contract."""


def _ledger_hash(record: dict[str, Any]) -> str:
    """SHA-256 over the record excluding its own ``ledger_hash`` field."""
    payload = {key: value for key, value in record.items() if key != "ledger_hash"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LedgerError("created_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise LedgerError("created_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise LedgerError("created_at must include a timezone")
    return parsed


def _draft_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the build_trade_candidate draft from a stored record.

    ``build_trade_candidate`` reads only known keys, so derived fields carried in
    the record (e.g. ``edge``) are ignored on re-validation.
    """
    return {
        "event": record.get("event"),
        "market": record.get("market"),
        "thesis": record.get("thesis"),
        "risk": record.get("risk"),
        "post_mortem": record.get("post_mortem"),
        "contract_observation": record.get("contract_observation"),
    }


def build_ledger_record(
    draft: dict[str, Any],
    previous_hash: str = GENESIS_HASH,
    *,
    created_at: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Validate a draft and return a hash-chained ledger record."""
    if not isinstance(previous_hash, str) or not _HEX64.fullmatch(previous_hash):
        raise LedgerError("previous_hash must be a 64-character lowercase hex digest")
    candidate = build_trade_candidate(draft, created_at=created_at, candidate_id=candidate_id)
    candidate["previous_hash"] = previous_hash
    candidate["ledger_hash"] = _ledger_hash(candidate)
    return candidate


def append_candidate(
    ledger_path: str,
    draft: dict[str, Any],
    *,
    created_at: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Append one validated candidate to the ledger and fsync it."""
    ledger_path = os.path.abspath(ledger_path)
    with _LEDGER_LOCK:
        verification = verify_ledger(ledger_path)
        if not verification["ok"]:
            raise LedgerError(
                "cannot append to an invalid ledger: " + "; ".join(verification["errors"])
            )
        record = build_ledger_record(
            draft,
            verification["head_hash"],
            created_at=created_at,
            candidate_id=candidate_id,
        )
        last_created_at = verification.get("last_created_at")
        if last_created_at and _parse_timestamp(record["created_at"]) < _parse_timestamp(last_created_at):
            raise LedgerError("created_at cannot be earlier than the ledger head")
        if record["candidate_id"] in verification["candidate_ids"]:
            raise LedgerError(f"duplicate candidate_id: {record['candidate_id']}")

        os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(record))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


def verify_ledger(ledger_path: str) -> dict[str, Any]:
    """Verify hashes, chain links, ids, ordering, and candidate schema/risk/edge."""
    ledger_path = os.path.abspath(ledger_path)
    if not os.path.exists(ledger_path):
        return _result([], GENESIS_HASH, set(), count=0, last_created_at=None)

    try:
        with open(ledger_path, "r", encoding="utf-8") as handle:
            lines = list(handle)
    except OSError as exc:
        return _result([f"could not read ledger: {exc}"], GENESIS_HASH, set())

    errors: list[str] = []
    expected_previous = GENESIS_HASH
    candidate_ids: set[str] = set()
    previous_created_at: datetime | None = None
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

        # 1. ledger_hash integrity (over stored content, independent of re-validation).
        stored_ledger_hash = record.get("ledger_hash")
        if not isinstance(stored_ledger_hash, str) or stored_ledger_hash != _ledger_hash(record):
            errors.append(f"line {line_number}: ledger_hash mismatch")

        # 2. Re-validate candidate schema/risk/edge and content/candidate_hash integrity.
        created_at = record.get("created_at")
        candidate_id = record.get("candidate_id")
        try:
            recomputed = build_trade_candidate(
                _draft_from_record(record),
                created_at=created_at if isinstance(created_at, str) else None,
                candidate_id=candidate_id if isinstance(candidate_id, str) else None,
            )
        except TradeJournalError as exc:
            errors.append(f"line {line_number}: schema/risk/edge violation: {exc}")
        else:
            stored_candidate = {
                key: value
                for key, value in record.items()
                if key not in ("previous_hash", "ledger_hash")
            }
            if canonical_json(stored_candidate) != canonical_json(recomputed):
                errors.append(f"line {line_number}: candidate_hash/content mismatch (record edited)")

        # 3. Predecessor-hash chain.
        if record.get("previous_hash") != expected_previous:
            errors.append(f"line {line_number}: previous_hash does not match ledger head")

        # 4. Duplicate candidate_id.
        if not isinstance(candidate_id, str) or not candidate_id:
            errors.append(f"line {line_number}: missing candidate_id")
        elif candidate_id in candidate_ids:
            errors.append(f"line {line_number}: duplicate candidate_id")
        else:
            candidate_ids.add(candidate_id)

        # 5. Monotonic (non-decreasing) created_at.
        try:
            current_created_at = _parse_timestamp(created_at)
            if previous_created_at is not None and current_created_at < previous_created_at:
                errors.append(f"line {line_number}: non-monotonic created_at")
            previous_created_at = current_created_at
        except LedgerError:
            errors.append(f"line {line_number}: invalid created_at")

        # Advance the chain using the stored ledger_hash.
        if isinstance(stored_ledger_hash, str):
            expected_previous = stored_ledger_hash

    return _result(
        errors,
        expected_previous,
        candidate_ids,
        count=count,
        last_created_at=previous_created_at.isoformat() if previous_created_at else None,
    )


def summarize_ledger(ledger_path: str) -> dict[str, Any]:
    """Return a compact, read-only summary of a trade-candidate ledger."""
    verification = verify_ledger(ledger_path)
    summary = {
        "ok": verification["ok"],
        "record_count": verification["record_count"],
        "head_hash": verification["head_hash"],
        "first_created_at": None,
        "last_created_at": verification["last_created_at"],
        "planned_risk_usd": 0.0,
        "average_planned_risk_usd": None,
        "largest_planned_risk_usd": None,
        "average_edge_percentage_points": None,
        "largest_edge_percentage_points": None,
        "smallest_edge_percentage_points": None,
        "event_types": {},
        "sides": {},
        "statuses": {},
        "open_post_mortems": 0,
        "linked_contract_observations": 0,
        "errors": verification["errors"],
    }
    if not verification["ok"] or verification["record_count"] == 0:
        return summary

    event_types: Counter[str] = Counter()
    sides: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    planned_risks: list[float] = []
    edges: list[float] = []
    first_created_at = None
    open_post_mortems = 0
    linked_contract_observations = 0

    with open(os.path.abspath(ledger_path), "r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            created_at = record.get("created_at")
            if first_created_at is None:
                first_created_at = created_at

            event = record.get("event", {})
            market = record.get("market", {})
            thesis = record.get("thesis", {})
            risk = record.get("risk", {})
            post_mortem = record.get("post_mortem", {})

            event_types[str(event.get("event_type", "unknown"))] += 1
            sides[str(market.get("side", "unknown"))] += 1
            statuses[str(record.get("status", "unknown"))] += 1
            planned_risks.append(float(risk.get("planned_risk_usd", 0.0)))
            edges.append(float(thesis.get("edge_percentage_points", 0.0)))

            if not post_mortem.get("outcome"):
                open_post_mortems += 1
            if record.get("contract_observation") is not None:
                linked_contract_observations += 1

    summary["first_created_at"] = first_created_at
    summary["planned_risk_usd"] = round(sum(planned_risks), 2)
    summary["average_planned_risk_usd"] = round(sum(planned_risks) / len(planned_risks), 2)
    summary["largest_planned_risk_usd"] = round(max(planned_risks), 2)
    summary["average_edge_percentage_points"] = round(sum(edges) / len(edges), 4)
    summary["largest_edge_percentage_points"] = round(max(edges), 4)
    summary["smallest_edge_percentage_points"] = round(min(edges), 4)
    summary["event_types"] = dict(sorted(event_types.items()))
    summary["sides"] = dict(sorted(sides.items()))
    summary["statuses"] = dict(sorted(statuses.items()))
    summary["open_post_mortems"] = open_post_mortems
    summary["linked_contract_observations"] = linked_contract_observations
    return summary


def _result(
    errors: list[str],
    head_hash: str,
    candidate_ids: set[str],
    *,
    count: int = 0,
    last_created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": not errors,
        "record_count": count,
        "head_hash": head_hash,
        "last_created_at": last_created_at,
        "candidate_ids": candidate_ids,
        "errors": errors,
    }
