"""Snapshot utilities for Biotech Risk Scout scan outputs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


SNAPSHOT_DATE_FORMAT = "%Y%m%d"


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for snapshot metadata."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def snapshot_filename(timestamp: str | None = None) -> str:
    """Return the standard dated snapshot filename."""
    if timestamp is None:
        stamp = datetime.now(timezone.utc).strftime(SNAPSHOT_DATE_FORMAT)
    else:
        stamp = timestamp[:10].replace("-", "")
    return f"scan-{stamp}.json"


def build_snapshot_payload(records: list[dict[str, Any]], timestamp: str | None = None) -> dict[str, Any]:
    """Build a stable snapshot payload with metadata and records."""
    run_at = timestamp or utc_timestamp()
    return {
        "generated_at": run_at,
        "record_count": len(records),
        "records": records,
    }


def write_snapshot(records: list[dict[str, Any]], snapshot_dir: str, timestamp: str | None = None) -> dict[str, str]:
    """Write a dated snapshot and latest.json into snapshot_dir.

    Returns paths for the dated snapshot and latest snapshot.
    """
    os.makedirs(snapshot_dir, exist_ok=True)
    payload = build_snapshot_payload(records, timestamp=timestamp)

    dated_path = os.path.join(snapshot_dir, snapshot_filename(payload["generated_at"]))
    latest_path = os.path.join(snapshot_dir, "latest.json")

    for path in (dated_path, latest_path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")

    return {"snapshot_path": dated_path, "latest_path": latest_path}


def load_snapshot(path: str) -> dict[str, Any]:
    """Load a snapshot payload from disk."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return build_snapshot_payload(payload, timestamp="unknown")
    return payload


def compare_snapshots(old_snapshot: dict[str, Any], new_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Compare two scan snapshots by ticker."""
    old_records = _records_by_ticker(old_snapshot.get("records", []))
    new_records = _records_by_ticker(new_snapshot.get("records", []))

    old_tickers = set(old_records)
    new_tickers = set(new_records)

    added = sorted(new_tickers - old_tickers)
    removed = sorted(old_tickers - new_tickers)
    common = sorted(old_tickers & new_tickers)

    changed: list[dict[str, Any]] = []
    for ticker in common:
        old = old_records[ticker]
        new = new_records[ticker]
        score_delta = _safe_number(new.get("score"), 0) - _safe_number(old.get("score"), 0)
        changes: dict[str, Any] = {}
        for field in ("score", "rank", "upcoming_catalyst", "days_until_event", "cash_runway_months", "dilution_risk", "evidence_quality", "validated_sec_flags", "validated_sec_filings", "sec_validation_errors"):
            if old.get(field) != new.get(field):
                changes[field] = {"old": old.get(field), "new": new.get(field)}
        if changes:
            changed.append(
                {
                    "ticker": ticker,
                    "score_delta": score_delta,
                    "old_score": old.get("score"),
                    "new_score": new.get("score"),
                    "changes": changes,
                    "new_record": new,
                }
            )

    changed.sort(key=lambda item: abs(item["score_delta"]), reverse=True)
    return {
        "old_generated_at": old_snapshot.get("generated_at"),
        "new_generated_at": new_snapshot.get("generated_at"),
        "added": added,
        "added_records": [new_records[ticker] for ticker in added],
        "removed": removed,
        "changed": changed,
        "summary": {
            "added_count": len(added),
            "removed_count": len(removed),
            "changed_count": len(changed),
        },
    }


def format_snapshot_comparison(comparison: dict[str, Any], max_changes: int = 20) -> str:
    """Return a readable snapshot comparison report."""
    summary = comparison.get("summary", {})
    lines = [
        "Snapshot Comparison",
        "-------------------",
        f"Old: {comparison.get('old_generated_at')}",
        f"New: {comparison.get('new_generated_at')}",
        f"Added: {summary.get('added_count', 0)} | Removed: {summary.get('removed_count', 0)} | Changed: {summary.get('changed_count', 0)}",
    ]

    if comparison.get("added"):
        lines.extend(["", "Added tickers:", ", ".join(comparison["added"])])
    if comparison.get("removed"):
        lines.extend(["", "Removed tickers:", ", ".join(comparison["removed"])])
    if comparison.get("changed"):
        lines.extend(["", "Changed tickers:"])
        for item in comparison["changed"][:max_changes]:
            delta = item.get("score_delta", 0)
            sign = "+" if delta > 0 else ""
            lines.append(
                f"- {item['ticker']}: score {item.get('old_score')} -> {item.get('new_score')} ({sign}{delta})"
            )
            for field, values in item.get("changes", {}).items():
                if field == "score":
                    continue
                lines.append(f"  {field}: {values.get('old')} -> {values.get('new')}")
    return "\n".join(lines)


def _records_by_ticker(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("ticker", "")).upper(): record for record in records if record.get("ticker")}


def _safe_number(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
