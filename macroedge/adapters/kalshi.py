"""Offline Kalshi market adapter.

This module converts Kalshi-style market JSON into MacroEdge's platform-neutral
contract draft shape. It performs no network calls, authentication, or trading.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from macroedge.contracts import ContractError, build_contract_record


KALSHI_MARKET_URL_PREFIX = "https://kalshi.com/markets/"
KALSHI_SETTLEMENT_SOURCE = "Kalshi settlement per contract rules"


class KalshiAdapterError(ContractError):
    """Raised when a Kalshi-style market cannot be mapped safely."""


def kalshi_market_to_contract_draft(
    market: dict[str, Any],
    *,
    observed_at: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    """Convert one Kalshi-style market object into a MacroEdge contract draft."""
    if not isinstance(market, dict):
        raise KalshiAdapterError("Kalshi market must be a JSON object")

    ticker = _require_text(market, "ticker")
    _require_text(market, "title")
    observed = observed_at or _optional_text(market, "observed_at") or _optional_text(market, "updated_time")
    if not observed:
        raise KalshiAdapterError("observed_at is required (pass it explicitly or include observed_at/updated_time)")

    mapped_event_type = event_type or _optional_text(market, "macroedge_event_type")
    if not mapped_event_type:
        raise KalshiAdapterError(
            "event_type is required (pass it explicitly or include macroedge_event_type)"
        )

    release_datetime = _first_text(
        market,
        "macroedge_release_datetime",
        "occurrence_datetime",
    )
    settlement_datetime = _first_text(
        market,
        "expiration_time",
        "expected_expiration_time",
        "latest_expiration_time",
    )
    settlement_source = (
        _optional_text(market, "macroedge_settlement_source")
        or _optional_text(market, "macroedge_authoritative_source")
        or KALSHI_SETTLEMENT_SOURCE
    )
    settlement_rules = _settlement_rules(market)

    yes_bid = _optional_price(market, "yes_bid_dollars", "yes_bid")
    yes_ask = _optional_price(market, "yes_ask_dollars", "yes_ask")
    last_price = _optional_price(market, "last_price_dollars", "last_price")
    if yes_bid is None and yes_ask is None and last_price is None:
        raise KalshiAdapterError(
            "one of yes_bid_dollars/yes_ask_dollars/last_price_dollars is required"
        )
    if yes_bid is not None and yes_ask is not None and yes_bid > yes_ask:
        raise KalshiAdapterError("yes_bid cannot be greater than yes_ask")

    return {
        "observed_at": observed,
        "event": {
            "event_type": mapped_event_type,
            "name": _event_name(market),
            "release_datetime": release_datetime,
            "settlement_datetime": settlement_datetime,
            "settlement_source": settlement_source,
            "settlement_rules": settlement_rules,
        },
        "market": {
            "platform": "Kalshi",
            "contract_id": ticker,
            "question": _question(market),
            "market_url": _market_url(market, ticker),
            "status": _optional_text(market, "status") or "observed",
        },
        "prices": {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "last_price": last_price,
        },
        "notes": _notes(market),
    }


def build_contract_record_from_kalshi(
    market: dict[str, Any],
    *,
    observed_at: str | None = None,
    event_type: str | None = None,
    observation_id: str | None = None,
) -> dict[str, Any]:
    """Convert and validate a Kalshi-style market as a canonical contract record."""
    return build_contract_record(
        kalshi_market_to_contract_draft(
            market,
            observed_at=observed_at,
            event_type=event_type,
        ),
        observation_id=observation_id,
    )


def _settlement_rules(market: dict[str, Any]) -> str:
    primary = _require_text(market, "rules_primary")
    secondary = _optional_text(market, "rules_secondary")
    if secondary:
        return f"{primary}\n\n{secondary}"
    return primary


def _event_name(market: dict[str, Any]) -> str:
    event_ticker = _optional_text(market, "event_ticker")
    title = _require_text(market, "title")
    if event_ticker:
        return f"{event_ticker}: {title}"
    return title


def _question(market: dict[str, Any]) -> str:
    title = _require_text(market, "title")
    subtitle = _optional_text(market, "subtitle")
    if subtitle:
        return f"{title} - {subtitle}"
    return title


def _market_url(market: dict[str, Any], ticker: str) -> str:
    return _optional_text(market, "market_url") or f"{KALSHI_MARKET_URL_PREFIX}{ticker}"


def _notes(market: dict[str, Any]) -> str:
    fields = []
    for key in (
        "event_ticker",
        "category",
        "status",
        "close_time",
        "expiration_time",
        "expected_expiration_time",
        "latest_expiration_time",
        "yes_bid_dollars",
        "yes_ask_dollars",
        "last_price_dollars",
        "yes_bid",
        "yes_ask",
        "last_price",
    ):
        value = market.get(key)
        if value is not None and str(value).strip():
            fields.append(f"{key}={value}")
    if fields:
        return "Kalshi adapter source fields: " + ", ".join(fields)
    return "Kalshi adapter source fields."


def _optional_price(market: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in market and market[key] is not None:
            return _price_to_probability(market[key], key)
    return None


def _price_to_probability(value: Any, field: str) -> float | None:
    if isinstance(value, bool):
        raise KalshiAdapterError(f"{field} must be numeric")

    is_dollar_field = field.endswith("_dollars")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise KalshiAdapterError(f"{field} must be numeric")
        if text.endswith("¢"):
            text = text[:-1]
            cents = True
        else:
            cents = False
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise KalshiAdapterError(f"{field} must be numeric") from exc
        if cents:
            number = number / Decimal("100")
        elif not is_dollar_field and number > 1:
            number = number / Decimal("100")
    elif isinstance(value, int):
        number = Decimal(value)
        if not is_dollar_field:
            number = number / Decimal("100")
    elif isinstance(value, float):
        number = Decimal(str(value))
        if not is_dollar_field and number > 1:
            number = number / Decimal("100")
    else:
        raise KalshiAdapterError(f"{field} must be numeric")

    if number.is_nan() or number.is_infinite():
        raise KalshiAdapterError(f"{field} must be numeric")
    if number <= 0 or number >= 1:
        return None
    return float(round(number, 10))


def _first_text(market: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _optional_text(market, key)
        if value:
            return value
    raise KalshiAdapterError(f"one of {', '.join(keys)} is required")


def _require_text(market: dict[str, Any], key: str) -> str:
    value = _optional_text(market, key)
    if not value:
        raise KalshiAdapterError(f"{key} is required")
    return value


def _optional_text(market: dict[str, Any], key: str) -> str | None:
    value = market.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise KalshiAdapterError(f"{key} must be a string")
    value = value.strip()
    return value or None
