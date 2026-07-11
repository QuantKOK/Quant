"""Performance reconciliation for MacroEdge trade journals and settlements.

This module is deliberately read-only: it verifies the candidate journal ledger
and settlement ledger first, then reconciles them by ``candidate_id`` and
``candidate_hash``. It never contacts a market/API and never places or executes
a trade.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any

from macroedge.ledger import verify_ledger as verify_candidate_ledger
from macroedge.settlement_ledger import verify_ledger as verify_settlement_ledger


def summarize_performance(journal_ledger_path: str, settlement_ledger_path: str) -> dict[str, Any]:
    """Reconcile candidate and settlement ledgers into a compact scorecard."""
    candidate_check = verify_candidate_ledger(journal_ledger_path)
    settlement_check = verify_settlement_ledger(settlement_ledger_path)
    summary = _empty_summary(
        candidate_count=candidate_check["record_count"],
        settlement_count=settlement_check["record_count"],
    )

    errors = [
        *(f"candidate ledger: {error}" for error in candidate_check["errors"]),
        *(f"settlement ledger: {error}" for error in settlement_check["errors"]),
    ]
    if errors:
        summary["errors"] = errors
        return summary

    candidates = _load_jsonl(journal_ledger_path)
    settlements = _load_jsonl(settlement_ledger_path)
    by_candidate_id = {record["candidate_id"]: record for record in candidates}

    reconciliation_errors: list[str] = []
    settled_candidate_ids: set[str] = set()
    outcomes: Counter[str] = Counter()
    actual_results: Counter[str] = Counter()
    event_types: Counter[str] = Counter()
    sides: Counter[str] = Counter()
    candidate_edges: list[float] = []
    settled_edges: list[float] = []
    brier_scores: list[float] = []
    planned_risk = 0.0
    settled_planned_risk = 0.0

    for candidate in candidates:
        event_types[str(candidate["event"]["event_type"])] += 1
        sides[str(candidate["market"]["side"])] += 1
        candidate_edges.append(float(candidate["thesis"]["edge_percentage_points"]))
        planned_risk += float(candidate["risk"]["planned_risk_usd"])

    for settlement in settlements:
        candidate_snapshot = settlement["candidate"]
        candidate_id = candidate_snapshot["candidate_id"]
        candidate = by_candidate_id.get(candidate_id)
        if candidate is None:
            reconciliation_errors.append(
                f"settlement {settlement['settlement_id']}: candidate_id not found in journal: {candidate_id}"
            )
            continue
        if candidate_snapshot["candidate_hash"] != candidate["candidate_hash"]:
            reconciliation_errors.append(
                f"settlement {settlement['settlement_id']}: candidate_hash does not match journal candidate"
            )
            continue

        settled_candidate_ids.add(candidate_id)
        settlement_payload = settlement["settlement"]
        outcome = settlement_payload["outcome"]
        outcomes[outcome] += 1
        actual_results[settlement_payload["actual_result"]] += 1
        settled_edges.append(float(candidate["thesis"]["edge_percentage_points"]))
        settled_planned_risk += float(candidate["risk"]["planned_risk_usd"])
        if settlement_payload["brier_score"] is not None:
            brier_scores.append(float(settlement_payload["brier_score"]))

    if reconciliation_errors:
        summary["errors"] = reconciliation_errors
        return summary

    won_count = outcomes["won"]
    lost_count = outcomes["lost"]
    void_count = outcomes["void"]
    scored_count = won_count + lost_count
    unsettled_ids = sorted(set(by_candidate_id) - settled_candidate_ids)

    summary.update(
        {
            "ok": True,
            "settled_count": len(settled_candidate_ids),
            "unsettled_count": len(unsettled_ids),
            "won_count": won_count,
            "lost_count": lost_count,
            "void_count": void_count,
            "win_rate": round(won_count / scored_count, 6) if scored_count else None,
            "average_brier_score": (
                round(sum(brier_scores) / len(brier_scores), 6) if brier_scores else None
            ),
            "planned_risk_usd": round(planned_risk, 2),
            "settled_planned_risk_usd": round(settled_planned_risk, 2),
            "unsettled_planned_risk_usd": round(planned_risk - settled_planned_risk, 2),
            "average_candidate_edge_percentage_points": _average(candidate_edges),
            "average_settled_edge_percentage_points": _average(settled_edges),
            "event_types": dict(sorted(event_types.items())),
            "sides": dict(sorted(sides.items())),
            "outcomes": dict(sorted(outcomes.items())),
            "actual_results": dict(sorted(actual_results.items())),
            "unsettled_candidate_ids": unsettled_ids,
            "errors": [],
        }
    )
    return summary


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    absolute = os.path.abspath(path)
    if not os.path.exists(absolute):
        return []
    with open(absolute, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _empty_summary(*, candidate_count: int, settlement_count: int) -> dict[str, Any]:
    return {
        "ok": False,
        "candidate_count": candidate_count,
        "settlement_count": settlement_count,
        "settled_count": 0,
        "unsettled_count": 0,
        "won_count": 0,
        "lost_count": 0,
        "void_count": 0,
        "win_rate": None,
        "average_brier_score": None,
        "planned_risk_usd": 0.0,
        "settled_planned_risk_usd": 0.0,
        "unsettled_planned_risk_usd": 0.0,
        "average_candidate_edge_percentage_points": None,
        "average_settled_edge_percentage_points": None,
        "event_types": {},
        "sides": {},
        "outcomes": {},
        "actual_results": {},
        "unsettled_candidate_ids": [],
        "errors": [],
    }
