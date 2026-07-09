"""Validated trade-candidate records for macro event-contract research.

The journal is intentionally research-first. It records probability estimates,
market prices, settlement rules, sizing constraints, and post-mortem scaffolds;
it does not place trades.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = 1
DEFAULT_MIN_EDGE = 0.08
DEFAULT_MAX_RISK_PER_TRADE = 25.0
DEFAULT_MAX_EVENT_EXPOSURE = 50.0
HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_EVENT_TYPES = {
    "cpi",
    "unemployment",
    "fed_decision",
    "gdp",
    "recession",
    "financial_macro",
}


class TradeJournalError(ValueError):
    """Raised when a MacroEdge trade candidate violates the journal contract."""


def canonical_json(value: Any) -> str:
    """Return deterministic JSON used for candidate hashes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def build_trade_candidate(
    draft: dict[str, Any],
    *,
    created_at: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Validate a draft and return a canonical macro event-contract candidate.

    The returned record is suitable for a research journal or later append-only
    ledger. It enforces the project's small-bankroll guardrails by default:
    minimum edge, per-trade max risk, and per-event max exposure.
    """
    if not isinstance(draft, dict):
        raise TradeJournalError("trade candidate draft must be a JSON object")

    event = _require_dict(draft, "event")
    market = _require_dict(draft, "market")
    thesis = _require_dict(draft, "thesis")
    risk = _require_dict(draft, "risk")
    review = _optional_dict(draft, "post_mortem")
    contract_observation = _optional_contract_observation(draft)

    created = _parse_timestamp(created_at or _utc_now_iso(), "created_at")
    evidence_as_of = _parse_timestamp(_require_text(thesis, "evidence_as_of"), "thesis.evidence_as_of")
    if evidence_as_of > created:
        raise TradeJournalError("thesis.evidence_as_of cannot be later than created_at")

    event_type = _require_text(event, "event_type")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise TradeJournalError(
            "event.event_type must be one of: " + ", ".join(sorted(SUPPORTED_EVENT_TYPES))
        )

    side = _require_text(market, "side").upper()
    if side not in {"YES", "NO"}:
        raise TradeJournalError("market.side must be YES or NO")

    entry_price = _require_probability(market, "entry_price")
    fair_probability = _require_probability(thesis, "fair_probability")
    edge = round(fair_probability - entry_price, 10)

    min_edge = _require_non_negative_number(risk, "min_edge_required", default=DEFAULT_MIN_EDGE)
    planned_risk = _require_positive_number(risk, "planned_risk_usd")
    active_bankroll = _require_positive_number(risk, "active_bankroll_usd")
    max_risk = _require_positive_number(risk, "max_risk_per_trade_usd", default=DEFAULT_MAX_RISK_PER_TRADE)
    max_event_exposure = _require_positive_number(
        risk,
        "max_event_exposure_usd",
        default=DEFAULT_MAX_EVENT_EXPOSURE,
    )
    current_event_exposure = _require_non_negative_number(
        risk,
        "current_event_exposure_usd",
        default=0.0,
    )

    if edge < min_edge:
        raise TradeJournalError("edge must meet or exceed risk.min_edge_required")
    if planned_risk > max_risk:
        raise TradeJournalError("risk.planned_risk_usd exceeds risk.max_risk_per_trade_usd")
    if planned_risk + current_event_exposure > max_event_exposure:
        raise TradeJournalError("planned risk would exceed risk.max_event_exposure_usd")
    if planned_risk > active_bankroll:
        raise TradeJournalError("risk.planned_risk_usd cannot exceed active bankroll")

    data_sources = _require_url_list(thesis, "data_sources")
    market_url = _require_text(market, "market_url")
    if not _is_http_url(market_url):
        raise TradeJournalError("market.market_url must use http or https")

    record = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id or str(uuid.uuid4()),
        "created_at": created.isoformat(),
        "status": "candidate",
        "event": {
            "event_type": event_type,
            "name": _require_text(event, "name"),
            "release_datetime": _require_text(event, "release_datetime"),
            "settlement_datetime": _require_text(event, "settlement_datetime"),
            "settlement_source": _require_text(event, "settlement_source"),
            "settlement_rules": _require_text(event, "settlement_rules"),
        },
        "market": {
            "platform": _require_text(market, "platform"),
            "contract_id": _require_text(market, "contract_id"),
            "question": _require_text(market, "question"),
            "side": side,
            "entry_price": entry_price,
            "implied_probability": entry_price,
            "market_url": market_url,
        },
        "thesis": {
            "evidence_as_of": evidence_as_of.isoformat(),
            "fair_probability": fair_probability,
            "edge": edge,
            "edge_percentage_points": round(edge * 100, 4),
            "summary": _require_text(thesis, "summary"),
            "data_sources": data_sources,
        },
        "risk": {
            "active_bankroll_usd": round(active_bankroll, 2),
            "planned_risk_usd": round(planned_risk, 2),
            "max_risk_per_trade_usd": round(max_risk, 2),
            "current_event_exposure_usd": round(current_event_exposure, 2),
            "max_event_exposure_usd": round(max_event_exposure, 2),
            "min_edge_required": min_edge,
            "exit_plan": _require_text(risk, "exit_plan"),
        },
        "post_mortem": {
            "outcome": review.get("outcome"),
            "settled_at": review.get("settled_at"),
            "notes": review.get("notes", ""),
            "mistake_tags": review.get("mistake_tags", []),
        },
    }
    if contract_observation is not None:
        record["contract_observation"] = contract_observation
    record["candidate_hash"] = _record_hash(record)
    return record


def _record_hash(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("candidate_hash", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _require_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise TradeJournalError(f"{key} must be a JSON object")
    return item


def _optional_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key, {})
    if item is None:
        return {}
    if not isinstance(item, dict):
        raise TradeJournalError(f"{key} must be a JSON object")
    return item


def _optional_contract_observation(value: dict[str, Any]) -> dict[str, Any] | None:
    item = value.get("contract_observation")
    if item is None:
        return None
    if not isinstance(item, dict):
        raise TradeJournalError("contract_observation must be a JSON object")

    observed_at = _parse_timestamp(
        _require_text(item, "observed_at"),
        "contract_observation.observed_at",
    )
    contract_hash = _require_text(item, "contract_hash")
    if not HEX64_PATTERN.fullmatch(contract_hash):
        raise TradeJournalError("contract_observation.contract_hash must be a lowercase SHA-256 hex digest")

    return {
        "observation_id": _require_text(item, "observation_id"),
        "contract_hash": contract_hash,
        "observed_at": observed_at.isoformat(),
    }


def _require_text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise TradeJournalError(f"{key} must be a non-empty string")
    return item.strip()


def _require_probability(value: dict[str, Any], key: str) -> float:
    item = value.get(key)
    if not isinstance(item, int | float) or isinstance(item, bool):
        raise TradeJournalError(f"{key} must be a number between 0 and 1")
    probability = float(item)
    if probability <= 0 or probability >= 1:
        raise TradeJournalError(f"{key} must be greater than 0 and less than 1")
    return probability


def _require_positive_number(
    value: dict[str, Any],
    key: str,
    *,
    default: float | None = None,
) -> float:
    item = value.get(key, default)
    if not isinstance(item, int | float) or isinstance(item, bool) or float(item) <= 0:
        raise TradeJournalError(f"{key} must be a positive number")
    return float(item)


def _require_non_negative_number(
    value: dict[str, Any],
    key: str,
    *,
    default: float | None = None,
) -> float:
    item = value.get(key, default)
    if not isinstance(item, int | float) or isinstance(item, bool) or float(item) < 0:
        raise TradeJournalError(f"{key} must be a non-negative number")
    return float(item)


def _require_url_list(value: dict[str, Any], key: str) -> list[str]:
    item = value.get(key)
    if not isinstance(item, list) or not item:
        raise TradeJournalError(f"{key} must be a non-empty list")
    urls: list[str] = []
    for url in item:
        if not isinstance(url, str) or not url.strip() or not _is_http_url(url.strip()):
            raise TradeJournalError(f"{key} must contain only http or https URLs")
        urls.append(url.strip())
    return urls


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TradeJournalError(f"{field} must be an ISO timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise TradeJournalError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise TradeJournalError(f"{field} must include a timezone")
    return parsed


def _utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
