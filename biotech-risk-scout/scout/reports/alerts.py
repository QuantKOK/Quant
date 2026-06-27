"""Markdown alert reports for Biotech Risk Scout snapshot comparisons."""

from __future__ import annotations

from typing import Any


def build_alert_report(comparison: dict[str, Any], max_items: int = 10) -> str:
    """Build a concise markdown alert report from a snapshot comparison."""
    summary = comparison.get("summary", {})
    lines = [
        "# Biotech Risk Scout Alerts",
        "",
        f"Old snapshot: `{comparison.get('old_generated_at')}`",
        f"New snapshot: `{comparison.get('new_generated_at')}`",
        "",
        "## Summary",
        "",
        f"- Added tickers: **{summary.get('added_count', 0)}**",
        f"- Removed tickers: **{summary.get('removed_count', 0)}**",
        f"- Changed tickers: **{summary.get('changed_count', 0)}**",
        "",
    ]

    lines.extend(_biggest_score_moves(comparison, max_items=max_items))
    lines.extend(_new_names(comparison, max_items=max_items))
    lines.extend(_removed_names(comparison, max_items=max_items))
    lines.extend(_manual_review(comparison, max_items=max_items))
    lines.extend(_validated_sec_flags(comparison, max_items=max_items))

    return "\n".join(lines).rstrip() + "\n"


def write_alert_report(comparison: dict[str, Any], path: str, max_items: int = 10) -> None:
    """Write a markdown alert report to disk."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(build_alert_report(comparison, max_items=max_items))


def _biggest_score_moves(comparison: dict[str, Any], max_items: int) -> list[str]:
    changed = [item for item in comparison.get("changed", []) if item.get("old_score") != item.get("new_score")]
    if not changed:
        return ["## Biggest Score Moves", "", "No score changes detected.", ""]

    lines = ["## Biggest Score Moves", ""]
    for item in changed[:max_items]:
        delta = item.get("score_delta", 0)
        sign = "+" if delta > 0 else ""
        lines.append(f"- **{item['ticker']}**: {item.get('old_score')} → {item.get('new_score')} ({sign}{delta})")
        reason = _change_reason(item)
        if reason:
            lines.append(f"  - Why it matters: {reason}")
    lines.append("")
    return lines


def _new_names(comparison: dict[str, Any], max_items: int) -> list[str]:
    added = comparison.get("added", [])[:max_items]
    lines = ["## New Names Surfaced", ""]
    if not added:
        lines.extend(["No new tickers surfaced.", ""])
        return lines
    for ticker in added:
        lines.append(f"- **{ticker}**: new ticker in the scan universe or above the active filter.")
    lines.append("")
    return lines


def _removed_names(comparison: dict[str, Any], max_items: int) -> list[str]:
    removed = comparison.get("removed", [])[:max_items]
    lines = ["## Removed Names", ""]
    if not removed:
        lines.extend(["No tickers dropped out.", ""])
        return lines
    for ticker in removed:
        lines.append(f"- **{ticker}**: no longer present in the new snapshot or active filter.")
    lines.append("")
    return lines


def _manual_review(comparison: dict[str, Any], max_items: int) -> list[str]:
    watch_items = []
    for item in comparison.get("changed", []):
        changes = item.get("changes", {})
        if any(field in changes for field in ("upcoming_catalyst", "days_until_event", "cash_runway_months", "dilution_risk", "evidence_quality")):
            watch_items.append(item)

    lines = ["## Names To Inspect Manually", ""]
    if not watch_items:
        lines.extend(["No catalyst, runway, dilution, or evidence-quality changes detected.", ""])
        return lines

    for item in watch_items[:max_items]:
        lines.append(f"- **{item['ticker']}**")
        for field, values in item.get("changes", {}).items():
            if field == "score":
                continue
            label = field.replace("_", " ")
            lines.append(f"  - {label}: {values.get('old')} → {values.get('new')}")
    lines.append("")
    return lines


def _validated_sec_flags(comparison: dict[str, Any], max_items: int) -> list[str]:
    records = list(comparison.get("added_records", []))
    records.extend(item.get("new_record", {}) for item in comparison.get("changed", []))
    matched_records = []
    for record in records:
        flags = [name for name, matched in (record.get("validated_sec_flags") or {}).items() if matched]
        filings = [filing for filing in record.get("validated_sec_filings") or [] if filing.get("matched_flags")]
        if flags or filings:
            matched_records.append((record, flags, filings))

    if not matched_records:
        return []

    lines = ["## Validated SEC filing text flags", ""]
    rendered = 0
    for record, flags, filings in matched_records:
        lines.append(f"- **{record.get('ticker', '?')}**: heuristic match in filing text; requires human review ({', '.join(flags)}).")
        for filing in filings:
            label = f"{filing.get('form') or 'filing'} {filing.get('filing_date') or ''}".strip()
            lines.append(f"  - [{label}]({filing.get('primary_document_url')}): matched filing text for {', '.join(filing['matched_flags'])}.")
        rendered += 1
        if rendered >= max_items:
            break
    lines.append("")
    return lines


def _change_reason(item: dict[str, Any]) -> str:
    changes = item.get("changes", {})
    reasons = []
    if "days_until_event" in changes:
        reasons.append("catalyst timing changed")
    if "upcoming_catalyst" in changes:
        reasons.append("catalyst description changed")
    if "cash_runway_months" in changes:
        reasons.append("cash runway changed")
    if "dilution_risk" in changes:
        reasons.append("dilution risk changed")
    if "evidence_quality" in changes:
        reasons.append("evidence quality changed")
    return "; ".join(reasons)
