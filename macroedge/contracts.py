"""Validated macro event-contract records.

Contracts are market observations, not trade recommendations. They capture the
question, settlement rules, prices, and source metadata needed before a trade
candidate can be considered.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from macroedge.journal import SUPPORTED_EVENT_TYPES, TradeJournalError, canonical_json


SCHEMA_VERSION = 1


class ContractError(TradeJournalError):
    """Raised when a market contract draft violates the contract schema."""


def build_contract_record(
    draft: dict[str, Any],
    *,
    observed_at: str | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    """Validate a market-contract observation and return a canonical record."""
    if not isinstance(draft, dict):
        raise ContractError("contract draft must be a JSON object")

    event = _require_dict(draft, "event")
    market = _require_dict(draft, "market")
    prices = _require_dict(draft, "prices")

    observed = _parse_timestamp(observed_at or _require_text(draft, "observed_at"), "observed_at")
    event_type = _require_text(event, "event_type")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ContractError(
            "event.event_type must be one of: " + ", ".join(sorted(SUPPORTED_EVENT_TYPES))
        )

    yes_bid = _optional_probability(prices, "yes_bid")
    yes_ask = _optional_probability(prices, "yes_ask")
    last_price = _optional_probability(prices, "last_price")
    if yes_bid is None and yes_ask is None and last_price is None:
        raise ContractError("prices must include at least one of yes_bid, yes_ask, or last_price")
    if yes_bid is not None and yes_ask is not None and yes_bid > yes_ask:
        raise ContractError("prices.yes_bid cannot be greater than prices.yes_ask")

    midpoint = None
    spread = None
    if yes_bid is not None and yes_ask is not None:
        midpoint = round((yes_bid + yes_ask) / 2, 10)
        spread = round(yes_ask - yes_bid, 10)

    record = {
        "schema_version": SCHEMA_VERSION,
        "observation_id": observation_id or str(uuid.uuid4()),
        "observed_at": observed.isoformat(),
        "status": _optional_text(market, "status", default="observed"),
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
            "market_url": _require_url(market, "market_url"),
        },
        "prices": {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "last_price": last_price,
            "midpoint_probability": midpoint,
            "spread": spread,
            "implied_probability_source": _implied_probability_source(yes_bid, yes_ask, last_price),
        },
        "source": {
            "retrieved_from": _require_url(market, "market_url"),
            "notes": _optional_text(draft, "notes", default=""),
        },
    }
    record["contract_hash"] = _record_hash(record)
    return record


def _record_hash(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("contract_hash", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _implied_probability_source(
    yes_bid: float | None,
    yes_ask: float | None,
    last_price: float | None,
) -> str:
    if yes_bid is not None and yes_ask is not None:
        return "bid_ask_midpoint"
    if last_price is not None:
        return "last_price"
    if yes_bid is not None:
        return "yes_bid"
    return "yes_ask"


def _require_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ContractError(f"{key} must be a JSON object")
    return item


def _require_text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ContractError(f"{key} must be a non-empty string")
    return item.strip()


def _optional_text(value: dict[str, Any], key: str, *, default: str) -> str:
    item = value.get(key, default)
    if item is None:
        return default
    if not isinstance(item, str):
        raise ContractError(f"{key} must be a string")
    return item.strip()


def _optional_probability(value: dict[str, Any], key: str) -> float | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, int | float) or isinstance(item, bool):
        raise ContractError(f"{key} must be a number between 0 and 1")
    probability = float(item)
    if probability <= 0 or probability >= 1:
        raise ContractError(f"{key} must be greater than 0 and less than 1")
    return probability


def _require_url(value: dict[str, Any], key: str) -> str:
    url = _require_text(value, key)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ContractError(f"{key} must use http or https")
    return url


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ContractError(f"{field} must include a timezone")
    return parsed
