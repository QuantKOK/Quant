"""Build trade-candidate drafts from verified contract observations.

This module is still manual-research first: it copies market/event facts from a
verified observation, then requires the human analyst to supply the thesis,
fair probability, data sources, side, and risk sizing.
"""

from __future__ import annotations

from typing import Any

from macroedge.contracts import verify_contract_record
from macroedge.journal import (
    DEFAULT_MAX_EVENT_EXPOSURE,
    DEFAULT_MAX_RISK_PER_TRADE,
    DEFAULT_MIN_EDGE,
    TradeJournalError,
    build_trade_candidate,
)


DEFAULT_EXIT_PLAN = (
    "No add-ons after entry. Reassess only if cited data changes or market price "
    "moves beyond fair value; document any exit before settlement."
)


class CandidateBuilderError(TradeJournalError):
    """Raised when an observation cannot safely seed a candidate draft."""


def build_trade_draft_from_observation(
    observation: dict[str, Any],
    *,
    side: str,
    fair_probability: float,
    thesis_summary: str,
    data_sources: list[str],
    active_bankroll_usd: float,
    planned_risk_usd: float,
    evidence_as_of: str | None = None,
    current_event_exposure_usd: float = 0.0,
    max_risk_per_trade_usd: float = DEFAULT_MAX_RISK_PER_TRADE,
    max_event_exposure_usd: float = DEFAULT_MAX_EVENT_EXPOSURE,
    min_edge_required: float = DEFAULT_MIN_EDGE,
    exit_plan: str = DEFAULT_EXIT_PLAN,
) -> dict[str, Any]:
    """Return a trade-candidate draft linked to a verified observation record."""
    _require_verified_observation(observation)
    side = side.upper()
    if side not in {"YES", "NO"}:
        raise CandidateBuilderError("side must be YES or NO")

    entry_price = _entry_price_for_side(observation, side)
    draft = {
        "contract_observation": {
            "observation_id": _require_text(observation, "observation_id"),
            "contract_hash": _require_text(observation, "contract_hash"),
            "observed_at": _require_text(observation, "observed_at"),
        },
        "event": dict(_require_dict(observation, "event")),
        "market": {
            "platform": _require_text(_require_dict(observation, "market"), "platform"),
            "contract_id": _require_text(_require_dict(observation, "market"), "contract_id"),
            "question": _require_text(_require_dict(observation, "market"), "question"),
            "side": side,
            "entry_price": entry_price,
            "market_url": _require_text(_require_dict(observation, "market"), "market_url"),
        },
        "thesis": {
            "evidence_as_of": evidence_as_of or _require_text(observation, "observed_at"),
            "fair_probability": fair_probability,
            "summary": thesis_summary,
            "data_sources": data_sources,
        },
        "risk": {
            "active_bankroll_usd": active_bankroll_usd,
            "planned_risk_usd": planned_risk_usd,
            "max_risk_per_trade_usd": max_risk_per_trade_usd,
            "current_event_exposure_usd": current_event_exposure_usd,
            "max_event_exposure_usd": max_event_exposure_usd,
            "min_edge_required": min_edge_required,
            "exit_plan": exit_plan,
        },
        "post_mortem": {
            "outcome": None,
            "settled_at": None,
            "notes": "",
            "mistake_tags": [],
        },
    }
    return draft


def build_candidate_from_observation(
    observation: dict[str, Any],
    *,
    side: str,
    fair_probability: float,
    thesis_summary: str,
    data_sources: list[str],
    active_bankroll_usd: float,
    planned_risk_usd: float,
    evidence_as_of: str | None = None,
    current_event_exposure_usd: float = 0.0,
    max_risk_per_trade_usd: float = DEFAULT_MAX_RISK_PER_TRADE,
    max_event_exposure_usd: float = DEFAULT_MAX_EVENT_EXPOSURE,
    min_edge_required: float = DEFAULT_MIN_EDGE,
    exit_plan: str = DEFAULT_EXIT_PLAN,
    created_at: str | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Build and validate a canonical candidate record from an observation."""
    return build_trade_candidate(
        build_trade_draft_from_observation(
            observation,
            side=side,
            fair_probability=fair_probability,
            thesis_summary=thesis_summary,
            data_sources=data_sources,
            active_bankroll_usd=active_bankroll_usd,
            planned_risk_usd=planned_risk_usd,
            evidence_as_of=evidence_as_of,
            current_event_exposure_usd=current_event_exposure_usd,
            max_risk_per_trade_usd=max_risk_per_trade_usd,
            max_event_exposure_usd=max_event_exposure_usd,
            min_edge_required=min_edge_required,
            exit_plan=exit_plan,
        ),
        created_at=created_at,
        candidate_id=candidate_id,
    )


def _require_verified_observation(observation: dict[str, Any]) -> None:
    result = verify_contract_record(observation)
    if not result["ok"]:
        raise CandidateBuilderError(
            "contract observation verification failed: " + "; ".join(result["errors"])
        )


def _entry_price_for_side(observation: dict[str, Any], side: str) -> float:
    prices = _require_dict(observation, "prices")
    yes_probability = _yes_entry_probability(prices)
    if side == "YES":
        return yes_probability
    return round(1 - yes_probability, 10)


def _yes_entry_probability(prices: dict[str, Any]) -> float:
    source = _require_text(prices, "implied_probability_source")
    if source == "bid_ask_midpoint":
        return _require_probability(prices, "midpoint_probability")
    if source == "last_price":
        return _require_probability(prices, "last_price")
    if source == "yes_bid":
        return _require_probability(prices, "yes_bid")
    if source == "yes_ask":
        return _require_probability(prices, "yes_ask")
    raise CandidateBuilderError(f"unsupported implied_probability_source: {source}")


def _require_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise CandidateBuilderError(f"{key} must be a JSON object")
    return item


def _require_text(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise CandidateBuilderError(f"{key} must be a non-empty string")
    return item.strip()


def _require_probability(value: dict[str, Any], key: str) -> float:
    item = value.get(key)
    if not isinstance(item, int | float) or isinstance(item, bool):
        raise CandidateBuilderError(f"{key} must be a number between 0 and 1")
    probability = float(item)
    if probability <= 0 or probability >= 1:
        raise CandidateBuilderError(f"{key} must be greater than 0 and less than 1")
    return probability
