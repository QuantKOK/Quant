"""Append-only, hash-chained ledger for MacroEdge contract observations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections import Counter
from datetime import datetime
from typing import Any

from macroedge.contracts import ContractError, build_contract_record, verify_contract_record
from macroedge.journal import canonical_json


GENESIS_HASH = "0" * 64
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LEDGER_LOCK = threading.Lock()


class ContractLedgerError(ContractError):
    """Raised when a contract observation ledger operation fails."""


def build_ledger_record(
    draft: dict[str, Any],
    previous_hash: str = GENESIS_HASH,
    *,
    observed_at: str | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    """Validate a contract draft and return a hash-chained ledger record."""
    if not isinstance(previous_hash, str) or not _HEX64.fullmatch(previous_hash):
        raise ContractLedgerError("previous_hash must be a 64-character lowercase hex digest")
    record = build_contract_record(draft, observed_at=observed_at, observation_id=observation_id)
    record["previous_hash"] = previous_hash
    record["ledger_hash"] = _ledger_hash(record)
    return record


def append_observation(
    ledger_path: str,
    draft: dict[str, Any],
    *,
    observed_at: str | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    """Append one validated contract observation to the ledger and fsync it."""
    ledger_path = os.path.abspath(ledger_path)
    with _LEDGER_LOCK:
        verification = verify_ledger(ledger_path)
        if not verification["ok"]:
            raise ContractLedgerError(
                "cannot append to an invalid ledger: " + "; ".join(verification["errors"])
            )
        record = build_ledger_record(
            draft,
            verification["head_hash"],
            observed_at=observed_at,
            observation_id=observation_id,
        )
        last_observed_at = verification.get("last_observed_at")
        if last_observed_at and _parse_timestamp(record["observed_at"]) < _parse_timestamp(last_observed_at):
            raise ContractLedgerError("observed_at cannot be earlier than the ledger head")
        if record["observation_id"] in verification["observation_ids"]:
            raise ContractLedgerError(f"duplicate observation_id: {record['observation_id']}")

        os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
        with open(ledger_path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(record))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    return record


def verify_ledger(ledger_path: str) -> dict[str, Any]:
    """Verify hashes, chain links, ids, ordering, and contract observation records."""
    ledger_path = os.path.abspath(ledger_path)
    if not os.path.exists(ledger_path):
        return _result([], GENESIS_HASH, set(), count=0, last_observed_at=None)

    try:
        with open(ledger_path, "r", encoding="utf-8") as handle:
            lines = list(handle)
    except OSError as exc:
        return _result([f"could not read ledger: {exc}"], GENESIS_HASH, set())

    errors: list[str] = []
    expected_previous = GENESIS_HASH
    observation_ids: set[str] = set()
    previous_observed_at: datetime | None = None
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

        observation_record = {
            key: value
            for key, value in record.items()
            if key not in ("previous_hash", "ledger_hash")
        }
        verification = verify_contract_record(observation_record)
        if not verification["ok"]:
            errors.extend(
                f"line {line_number}: contract observation violation: {error}"
                for error in verification["errors"]
            )

        if record.get("previous_hash") != expected_previous:
            errors.append(f"line {line_number}: previous_hash does not match ledger head")

        observation_id = record.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            errors.append(f"line {line_number}: missing observation_id")
        elif observation_id in observation_ids:
            errors.append(f"line {line_number}: duplicate observation_id")
        else:
            observation_ids.add(observation_id)

        try:
            observed = _parse_timestamp(record.get("observed_at"))
            if previous_observed_at is not None and observed < previous_observed_at:
                errors.append(f"line {line_number}: non-monotonic observed_at")
            previous_observed_at = observed
        except ContractLedgerError:
            errors.append(f"line {line_number}: invalid observed_at")

        if isinstance(stored_ledger_hash, str):
            expected_previous = stored_ledger_hash

    return _result(
        errors,
        expected_previous,
        observation_ids,
        count=count,
        last_observed_at=previous_observed_at.isoformat() if previous_observed_at else None,
    )


def summarize_ledger(ledger_path: str) -> dict[str, Any]:
    """Return a compact, read-only summary of a contract observation ledger."""
    verification = verify_ledger(ledger_path)
    summary = {
        "ok": verification["ok"],
        "record_count": verification["record_count"],
        "head_hash": verification["head_hash"],
        "first_observed_at": None,
        "last_observed_at": verification["last_observed_at"],
        "event_types": {},
        "platforms": {},
        "statuses": {},
        "implied_probability_sources": {},
        "errors": verification["errors"],
    }
    if not verification["ok"] or verification["record_count"] == 0:
        return summary

    event_types: Counter[str] = Counter()
    platforms: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    first_observed_at = None

    with open(os.path.abspath(ledger_path), "r", encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            observed_at = record.get("observed_at")
            if first_observed_at is None:
                first_observed_at = observed_at
            event = record.get("event", {})
            market = record.get("market", {})
            prices = record.get("prices", {})
            event_types[str(event.get("event_type", "unknown"))] += 1
            platforms[str(market.get("platform", "unknown"))] += 1
            statuses[str(record.get("status", "unknown"))] += 1
            sources[str(prices.get("implied_probability_source", "unknown"))] += 1

    summary["first_observed_at"] = first_observed_at
    summary["event_types"] = dict(sorted(event_types.items()))
    summary["platforms"] = dict(sorted(platforms.items()))
    summary["statuses"] = dict(sorted(statuses.items()))
    summary["implied_probability_sources"] = dict(sorted(sources.items()))
    return summary


def _ledger_hash(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("ledger_hash", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ContractLedgerError("observed_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractLedgerError("observed_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractLedgerError("observed_at must include a timezone")
    return parsed


def _result(
    errors: list[str],
    head_hash: str,
    observation_ids: set[str],
    *,
    count: int = 0,
    last_observed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": not errors,
        "record_count": count,
        "head_hash": head_hash,
        "last_observed_at": last_observed_at,
        "observation_ids": observation_ids,
        "errors": errors,
    }
