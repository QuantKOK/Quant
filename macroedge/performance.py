"""Performance reconciliation for MacroEdge trade journals and settlements.

This module is deliberately read-only: it verifies the candidate journal ledger
and settlement ledger first, then reconciles them by ``candidate_id`` and
``candidate_hash``. It never contacts a market/API and never places or executes
a trade.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import Counter
from typing import Any

from macroedge.journal import canonical_json
from macroedge.ledger import verify_ledger as verify_candidate_ledger
from macroedge.settlement_ledger import verify_ledger as verify_settlement_ledger

PERFORMANCE_CSV_FIELDS = [
    "ok",
    "candidate_count",
    "settlement_count",
    "settled_count",
    "unsettled_count",
    "won_count",
    "lost_count",
    "void_count",
    "win_rate",
    "average_brier_score",
    "planned_risk_usd",
    "settled_planned_risk_usd",
    "unsettled_planned_risk_usd",
    "average_candidate_edge_percentage_points",
    "average_settled_edge_percentage_points",
    "event_types",
    "sides",
    "outcomes",
    "actual_results",
    "unsettled_candidate_ids",
    "errors",
]


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


def export_performance_summary(summary: dict[str, Any], output_path: str, *, file_format: str) -> str:
    """Write a performance summary as stable JSON or one-row CSV.

    Returns the absolute output path. Invalid summaries can still be exported;
    the ``ok`` and ``errors`` fields make the artifact self-describing.
    """
    file_format = file_format.lower().strip()
    if file_format not in {"json", "csv"}:
        raise ValueError("file_format must be json or csv")

    absolute = os.path.abspath(output_path)
    output_dir = os.path.dirname(absolute) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_path = None
    try:
        if file_format == "json":
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="\n", dir=output_dir, delete=False
            ) as handle:
                json.dump(summary, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                temp_path = handle.name
        else:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", newline="", dir=output_dir, delete=False
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=PERFORMANCE_CSV_FIELDS)
                writer.writeheader()
                writer.writerow(_csv_row(summary))
                temp_path = handle.name
        os.replace(temp_path, absolute)
    except OSError:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    return absolute


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    absolute = os.path.abspath(path)
    if not os.path.exists(absolute):
        return []
    with open(absolute, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _csv_row(summary: dict[str, Any]) -> dict[str, Any]:
    return {field: _csv_cell(summary.get(field)) for field in PERFORMANCE_CSV_FIELDS}


def _csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return canonical_json(value)
    return value


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
