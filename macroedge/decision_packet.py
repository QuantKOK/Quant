"""Offline pre-trade 'decision packet' research notes for MacroEdge.

A decision packet is the disciplined human research note captured *before* a
possible trade becomes a logged candidate. It links a verified contract
observation to the analyst's thesis, the fair probability they estimate, the
disconfirming evidence they considered, their intent (no-trade / paper /
tiny-live), and an explicit decision (observe_only / candidate_ok / reject).

It computes the market-implied probability for the selected side, the gross edge,
whether that edge clears a configured threshold, and a *workflow* next-action
label. None of this is an investment recommendation: a packet is a research
artifact, not a trade, an order, or advice. This module performs no network
calls, uses no credentials, and never places or executes trades.

Packets are deterministic given ``packet_id`` and ``created_at`` and carry a
``packet_hash`` (SHA-256 over the record minus the hash) for tamper detection,
mirroring ``contract_hash`` / ``candidate_hash`` elsewhere in MacroEdge.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from macroedge.contracts import verify_contract_record
from macroedge.journal import DEFAULT_MIN_EDGE, canonical_json


SCHEMA_VERSION = 1
HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_DECISIONS = ("observe_only", "candidate_ok", "reject")
SUPPORTED_INTENTS = ("no_trade", "paper", "tiny_live")


class DecisionPacketError(ValueError):
    """Raised when a decision packet violates its contract."""


def build_decision_packet(
    observation: dict[str, Any],
    *,
    side: str,
    fair_probability: float,
    thesis_summary: str,
    data_sources: list[str],
    intent: str,
    decision: str,
    risk_notes: str,
    disconfirming_evidence: str,
    evidence_as_of: str | None = None,
    min_edge_required: float = DEFAULT_MIN_EDGE,
    packet_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a decision packet from a verified observation plus human inputs."""
    _require_verified_observation(observation)

    side = _require_choice(side, "side", {"YES", "NO"}, upper=True)
    decision = _require_choice(decision, "decision", set(SUPPORTED_DECISIONS))
    intent = _require_choice(intent, "intent", set(SUPPORTED_INTENTS))

    fair = _require_probability(fair_probability, "fair_probability")
    min_edge = _require_non_negative_number(min_edge_required, "min_edge_required")

    created = _parse_timestamp(created_at or _utc_now_iso(), "created_at")
    observed_text = _require_nonempty_str(observation.get("observed_at"), "observation.observed_at")
    observed = _parse_timestamp(observed_text, "observation.observed_at")
    evidence = _parse_timestamp(evidence_as_of or observed_text, "evidence_as_of")
    if evidence > created:
        raise DecisionPacketError("evidence_as_of cannot be later than created_at")
    if observed > created:
        raise DecisionPacketError("observation.observed_at cannot be later than created_at")

    event = _require_dict(observation.get("event"), "observation.event")
    market = _require_dict(observation.get("market"), "observation.market")
    implied = _implied_probability_for_side(observation, side)
    edge = round(fair - implied, 10)
    clears = edge >= min_edge

    record = {
        "schema_version": SCHEMA_VERSION,
        "packet_id": packet_id or str(uuid.uuid4()),
        "created_at": created.isoformat(),
        "decision": decision,
        "intent": intent,
        "contract_observation": {
            "observation_id": _require_nonempty_str(observation.get("observation_id"), "observation.observation_id"),
            "contract_hash": _require_hash(observation.get("contract_hash"), "observation.contract_hash"),
            "observed_at": observed.isoformat(),
        },
        "event": {
            "event_type": _require_nonempty_str(event.get("event_type"), "event.event_type"),
            "name": _require_nonempty_str(event.get("name"), "event.name"),
        },
        "market": {
            "platform": _require_nonempty_str(market.get("platform"), "market.platform"),
            "contract_id": _require_nonempty_str(market.get("contract_id"), "market.contract_id"),
            "question": _require_nonempty_str(market.get("question"), "market.question"),
            "market_url": _require_http_url(market.get("market_url"), "market.market_url"),
            "side": side,
        },
        "thesis": {
            "evidence_as_of": evidence.isoformat(),
            "fair_probability": fair,
            "summary": _require_nonempty_str(thesis_summary, "thesis_summary"),
            "data_sources": _require_http_url_list(data_sources, "data_sources"),
            "disconfirming_evidence": _require_nonempty_str(disconfirming_evidence, "disconfirming_evidence"),
            "risk_notes": _require_nonempty_str(risk_notes, "risk_notes"),
        },
        "assessment": {
            "market_implied_probability": implied,
            "gross_edge": edge,
            "edge_percentage_points": round(edge * 100, 4),
            "min_edge_required": min_edge,
            "clears_edge_threshold": clears,
            "suggested_next_action": _suggested_next_action(decision, intent, clears),
        },
    }
    record["packet_hash"] = _record_hash(record)
    return record


def verify_decision_packet(record: dict[str, Any]) -> dict[str, Any]:
    """Verify a standalone decision packet record."""
    errors: list[str] = []
    if not isinstance(record, dict):
        return _verification_result(["record must be a JSON object"], None)

    stored_hash = record.get("packet_hash")
    if not isinstance(stored_hash, str) or not stored_hash:
        errors.append("missing packet_hash")
    elif stored_hash != _record_hash(record):
        errors.append("packet_hash mismatch")

    try:
        _validate_packet_record(record)
    except DecisionPacketError as exc:
        errors.append(f"schema violation: {exc}")

    return _verification_result(errors, stored_hash if isinstance(stored_hash, str) else None)


def candidate_seed_from_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Return the analyst inputs a ``candidate_ok`` packet carries to a candidate.

    This does not build a candidate and does not place a trade; it only extracts
    the human inputs so ``macroedge.candidate_builder`` can seed a draft from the
    same verified observation. Raises unless the packet verifies and its decision
    is ``candidate_ok``.
    """
    result = verify_decision_packet(packet)
    if not result["ok"]:
        raise DecisionPacketError(
            "decision packet verification failed: " + "; ".join(result["errors"])
        )
    if packet["decision"] != "candidate_ok":
        raise DecisionPacketError("only candidate_ok packets can seed a trade candidate")
    thesis = packet["thesis"]
    return {
        "side": packet["market"]["side"],
        "fair_probability": thesis["fair_probability"],
        "thesis_summary": thesis["summary"],
        "data_sources": list(thesis["data_sources"]),
        "evidence_as_of": thesis["evidence_as_of"],
    }


def _validate_packet_record(record: dict[str, Any]) -> None:
    if record.get("schema_version") != SCHEMA_VERSION:
        raise DecisionPacketError(f"schema_version must be {SCHEMA_VERSION}")
    _require_nonempty_str(record.get("packet_id"), "packet_id")
    created = _parse_timestamp(_require_nonempty_str(record.get("created_at"), "created_at"), "created_at")
    decision = _require_choice(record.get("decision"), "decision", set(SUPPORTED_DECISIONS))
    intent = _require_choice(record.get("intent"), "intent", set(SUPPORTED_INTENTS))

    observation = _require_dict(record.get("contract_observation"), "contract_observation")
    _require_nonempty_str(observation.get("observation_id"), "contract_observation.observation_id")
    _require_hash(observation.get("contract_hash"), "contract_observation.contract_hash")
    observed = _parse_timestamp(
        _require_nonempty_str(observation.get("observed_at"), "contract_observation.observed_at"),
        "contract_observation.observed_at",
    )
    if observed > created:
        raise DecisionPacketError("contract_observation.observed_at cannot be later than created_at")

    event = _require_dict(record.get("event"), "event")
    _require_nonempty_str(event.get("event_type"), "event.event_type")
    _require_nonempty_str(event.get("name"), "event.name")

    market = _require_dict(record.get("market"), "market")
    _require_nonempty_str(market.get("platform"), "market.platform")
    _require_nonempty_str(market.get("contract_id"), "market.contract_id")
    _require_nonempty_str(market.get("question"), "market.question")
    _require_http_url(market.get("market_url"), "market.market_url")
    side = _require_choice(market.get("side"), "market.side", {"YES", "NO"}, upper=True)

    thesis = _require_dict(record.get("thesis"), "thesis")
    evidence = _parse_timestamp(
        _require_nonempty_str(thesis.get("evidence_as_of"), "thesis.evidence_as_of"),
        "thesis.evidence_as_of",
    )
    if evidence > created:
        raise DecisionPacketError("thesis.evidence_as_of cannot be later than created_at")
    fair = _require_probability(thesis.get("fair_probability"), "thesis.fair_probability")
    _require_nonempty_str(thesis.get("summary"), "thesis.summary")
    _require_http_url_list(thesis.get("data_sources"), "thesis.data_sources")
    _require_nonempty_str(thesis.get("disconfirming_evidence"), "thesis.disconfirming_evidence")
    _require_nonempty_str(thesis.get("risk_notes"), "thesis.risk_notes")

    assessment = _require_dict(record.get("assessment"), "assessment")
    implied = _require_probability(
        assessment.get("market_implied_probability"), "assessment.market_implied_probability"
    )
    min_edge = _require_non_negative_number(
        assessment.get("min_edge_required"), "assessment.min_edge_required"
    )
    expected_edge = round(fair - implied, 10)
    if assessment.get("gross_edge") != expected_edge:
        raise DecisionPacketError(
            "assessment.gross_edge is inconsistent with fair_probability and market_implied_probability"
        )
    if assessment.get("edge_percentage_points") != round(expected_edge * 100, 4):
        raise DecisionPacketError("assessment.edge_percentage_points is inconsistent with gross_edge")
    stored_clears = assessment.get("clears_edge_threshold")
    if not isinstance(stored_clears, bool):
        raise DecisionPacketError("assessment.clears_edge_threshold must be a boolean")
    if stored_clears != (expected_edge >= min_edge):
        raise DecisionPacketError("assessment.clears_edge_threshold does not match edge and min_edge_required")
    expected_action = _suggested_next_action(decision, intent, expected_edge >= min_edge)
    if assessment.get("suggested_next_action") != expected_action:
        raise DecisionPacketError("assessment.suggested_next_action does not match decision, intent, and edge")


def _suggested_next_action(decision: str, intent: str, clears_threshold: bool) -> str:
    """Return a workflow next-step label. Never an investment recommendation."""
    if decision == "reject":
        return "Archive as rejected research; do not seed a candidate."
    if decision == "observe_only":
        return "Keep observing this market; no candidate yet."
    # decision == "candidate_ok"
    if not clears_threshold:
        return "Edge is below the configured threshold; do not seed a candidate yet."
    if intent == "no_trade":
        return "Edge clears the threshold; intent is no-trade, retain as research."
    return "Edge clears the threshold; eligible to seed a trade candidate for human review."


def _require_verified_observation(observation: dict[str, Any]) -> None:
    result = verify_contract_record(observation)
    if not result["ok"]:
        raise DecisionPacketError(
            "contract observation verification failed: " + "; ".join(result["errors"])
        )


def _implied_probability_for_side(observation: dict[str, Any], side: str) -> float:
    # Mirrors macroedge.candidate_builder entry-price logic so a packet's implied
    # probability equals the entry_price a later candidate would use for the side.
    prices = _require_dict(observation.get("prices"), "observation.prices")
    yes = _yes_implied_probability(prices)
    if side == "YES":
        return yes
    return round(1 - yes, 10)


def _yes_implied_probability(prices: dict[str, Any]) -> float:
    source = _require_nonempty_str(prices.get("implied_probability_source"), "prices.implied_probability_source")
    field = {
        "bid_ask_midpoint": "midpoint_probability",
        "last_price": "last_price",
        "yes_bid": "yes_bid",
        "yes_ask": "yes_ask",
    }.get(source)
    if field is None:
        raise DecisionPacketError(f"unsupported implied_probability_source: {source}")
    return _require_probability(prices.get(field), f"prices.{field}")


def _record_hash(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("packet_hash", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _require_dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionPacketError(f"{field} must be a JSON object")
    return value


def _require_choice(value: Any, field: str, allowed: set[str], *, upper: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionPacketError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    normalized = value.strip()
    if upper:
        normalized = normalized.upper()
    if normalized not in allowed:
        raise DecisionPacketError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _require_nonempty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionPacketError(f"{field} must be a non-empty string")
    return value.strip()


def _require_hash(value: Any, field: str) -> str:
    text = _require_nonempty_str(value, field)
    if not HEX64_PATTERN.fullmatch(text):
        raise DecisionPacketError(f"{field} must be a lowercase SHA-256 hex digest")
    return text


def _require_probability(value: Any, field: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise DecisionPacketError(f"{field} must be a number greater than 0 and less than 1")
    probability = float(value)
    if probability <= 0 or probability >= 1:
        raise DecisionPacketError(f"{field} must be greater than 0 and less than 1")
    return probability


def _require_non_negative_number(value: Any, field: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool) or float(value) < 0:
        raise DecisionPacketError(f"{field} must be a non-negative number")
    return float(value)


def _require_http_url(value: Any, field: str) -> str:
    text = _require_nonempty_str(value, field)
    if not _is_http_url(text):
        raise DecisionPacketError(f"{field} must use http or https")
    return text


def _require_http_url_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise DecisionPacketError(f"{field} must be a non-empty list of http or https URLs")
    urls: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or not _is_http_url(item.strip()):
            raise DecisionPacketError(f"{field} must contain only http or https URLs")
        urls.append(item.strip())
    return urls


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DecisionPacketError(f"{field} must be an ISO timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise DecisionPacketError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DecisionPacketError(f"{field} must include a timezone")
    return parsed


def _utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _verification_result(errors: list[str], packet_hash: str | None) -> dict[str, Any]:
    return {
        "ok": not errors,
        "packet_hash": packet_hash,
        "errors": errors,
    }
