"""Static HTML dashboards for MacroEdge performance summaries.

The dashboard layer consumes exported performance summaries, not raw ledgers.
It is offline-only and produces a self-contained HTML file suitable for local
review, screenshots, or later dashboard/report automation.
"""

from __future__ import annotations

import csv
import html
import json
import os
import tempfile
from typing import Any


NESTED_FIELDS = {
    "event_types",
    "sides",
    "outcomes",
    "actual_results",
    "unsettled_candidate_ids",
    "errors",
}
INTEGER_FIELDS = {
    "candidate_count",
    "settlement_count",
    "settled_count",
    "unsettled_count",
    "won_count",
    "lost_count",
    "void_count",
}
FLOAT_FIELDS = {
    "win_rate",
    "average_brier_score",
    "planned_risk_usd",
    "settled_planned_risk_usd",
    "unsettled_planned_risk_usd",
    "average_candidate_edge_percentage_points",
    "average_settled_edge_percentage_points",
}


def load_performance_summary(path: str) -> dict[str, Any]:
    """Load a performance summary from the JSON or CSV export format."""
    absolute = os.path.abspath(path)
    suffix = os.path.splitext(absolute)[1].lower()
    if suffix == ".json":
        with open(absolute, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("performance JSON must contain one object")
        return payload
    if suffix == ".csv":
        with open(absolute, "r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != 1:
            raise ValueError("performance CSV must contain exactly one data row")
        return _summary_from_csv_row(rows[0])
    raise ValueError("performance summary input must be .json or .csv")


def render_performance_dashboard(summary: dict[str, Any], output_path: str) -> str:
    """Write a self-contained HTML dashboard and return its absolute path."""
    _validate_summary(summary)
    output = os.path.abspath(output_path)
    output_dir = os.path.dirname(output) or "."
    os.makedirs(output_dir, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=output_dir, delete=False
        ) as handle:
            handle.write(build_performance_dashboard_html(summary))
            temp_path = handle.name
        os.replace(temp_path, output)
    except OSError:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    return output


def build_performance_dashboard_html(summary: dict[str, Any]) -> str:
    """Return a static dashboard HTML document for a performance summary."""
    _validate_summary(summary)
    ok = bool(summary.get("ok"))
    status = "Verified" if ok else "Needs review"
    status_class = "good" if ok else "warn"
    candidate_count = _int(summary.get("candidate_count"))
    settlement_count = _int(summary.get("settlement_count"))
    settled_count = _int(summary.get("settled_count"))
    unsettled_count = _int(summary.get("unsettled_count"))
    won_count = _int(summary.get("won_count"))
    lost_count = _int(summary.get("lost_count"))
    void_count = _int(summary.get("void_count"))
    win_rate = summary.get("win_rate")
    avg_brier = summary.get("average_brier_score")
    planned_risk = summary.get("planned_risk_usd")
    settled_risk = summary.get("settled_planned_risk_usd")
    unsettled_risk = summary.get("unsettled_planned_risk_usd")
    avg_edge = summary.get("average_candidate_edge_percentage_points")
    avg_settled_edge = summary.get("average_settled_edge_percentage_points")
    unsettled_ids = summary.get("unsettled_candidate_ids") or []
    errors = summary.get("errors") or []

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MacroEdge performance dashboard</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f7f7;
      --surface: #ffffff;
      --text: #111111;
      --muted: #686868;
      --border: rgba(17, 17, 17, 0.12);
      --accent: #165dff;
      --good: #087443;
      --warn: #b95000;
      --soft: #f2f4f8;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.55 ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; }}
    .shell {{ width: min(1120px, calc(100% - 28px)); margin: 18px auto 48px; padding: 36px; border: 1px solid var(--border); border-radius: 24px; background: var(--surface); box-shadow: 0 10px 30px rgba(0,0,0,0.05); }}
    .kicker {{ color: var(--accent); font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ margin: 8px 0 8px; font-size: clamp(30px, 5vw, 44px); letter-spacing: -0.04em; line-height: 1.05; }}
    h2 {{ margin: 34px 0 10px; font-size: 22px; letter-spacing: -0.02em; }}
    p {{ color: var(--muted); }}
    .status {{ display: inline-flex; gap: 8px; align-items: center; margin-top: 12px; padding: 6px 10px; border-radius: 999px; background: var(--soft); font-weight: 700; }}
    .status.good {{ color: var(--good); }}
    .status.warn {{ color: var(--warn); }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 28px; }}
    .card {{ padding: 18px; border: 1px solid var(--border); border-radius: 20px; background: var(--surface); }}
    .label {{ color: var(--muted); font-size: 12px; }}
    .value {{ margin-top: 6px; font-size: 28px; font-weight: 650; letter-spacing: -0.04em; font-variant-numeric: tabular-nums; }}
    .note {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: right; font-variant-numeric: tabular-nums; }}
    th:first-child, td:first-child {{ text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; }}
    tr:last-child td {{ border-bottom: 0; }}
    .bar {{ height: 10px; border-radius: 99px; background: var(--soft); overflow: hidden; }}
    .fill {{ height: 100%; background: linear-gradient(90deg, var(--accent), #7b61ff); }}
    .section {{ margin-top: 18px; padding: 18px; border: 1px solid var(--border); border-radius: 20px; }}
    .ids {{ display: flex; flex-wrap: wrap; gap: 8px; padding: 0; list-style: none; }}
    .ids li {{ padding: 5px 8px; border-radius: 999px; background: var(--soft); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px; }}
    .caveat {{ margin-top: 28px; padding: 14px 16px; border-radius: 14px; background: var(--soft); color: var(--muted); font-size: 12px; }}
    @media (prefers-color-scheme: dark) {{
      :root {{ --bg: #161616; --surface: #202020; --text: #f5f5f5; --muted: #bdbdbd; --border: rgba(255,255,255,0.12); --soft: #2d2d2d; --accent: #6aa7ff; --good: #70d99a; --warn: #ffae73; }}
    }}
    @media (max-width: 860px) {{ .shell {{ width: 100%; margin: 0; border: 0; border-radius: 0; padding: 24px 16px; }} .cards, .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <div class="kicker">MacroEdge research desk</div>
    <h1>Performance dashboard</h1>
    <p>Track-record view built from an exported performance summary. This is research reporting only: it does not contact a market/API and does not place trades.</p>
    <div class="status {status_class}">{_escape(status)} · {_escape(str(candidate_count))} candidates · {_escape(str(settlement_count))} settlements</div>

    <section class="cards" aria-label="Headline metrics">
      {_metric_card("Candidates", _number(candidate_count), f"{settled_count} settled / {unsettled_count} unresolved")}
      {_metric_card("Win rate", _percent(win_rate), f"{won_count} won / {lost_count} lost / {void_count} void")}
      {_metric_card("Avg Brier score", _number(avg_brier, digits=4), "Lower is better; excludes void outcomes")}
      {_metric_card("Planned risk", _currency(planned_risk), f"{_currency(settled_risk)} settled / {_currency(unsettled_risk)} unresolved")}
      {_metric_card("Avg candidate edge", _points(avg_edge), "Across all logged candidates")}
      {_metric_card("Avg settled edge", _points(avg_settled_edge), "Across settled candidates only")}
      {_metric_card("Unresolved", _number(unsettled_count), "Candidates still awaiting settlement")}
      {_metric_card("Integrity status", _escape(status), "Source ledgers were verified before export")}
    </section>

    <section class="grid" aria-label="Breakdowns">
      {_breakdown_table("Event mix", summary.get("event_types") or {}, candidate_count)}
      {_breakdown_table("Side mix", summary.get("sides") or {}, candidate_count)}
      {_breakdown_table("Outcome mix", summary.get("outcomes") or {}, max(settlement_count, 1))}
      {_breakdown_table("Actual results", summary.get("actual_results") or {}, max(settlement_count, 1))}
    </section>

    <section class="section">
      <h2>Unresolved candidates</h2>
      {_unsettled_list(unsettled_ids)}
    </section>

    <section class="section">
      <h2>Source and definitions</h2>
      <table>
        <tbody>
          <tr><td>Source grain</td><td>One exported MacroEdge performance summary</td></tr>
          <tr><td>Candidate count</td><td>Trade candidates in the verified journal ledger</td></tr>
          <tr><td>Settlement count</td><td>Post-mortem settlement records in the verified settlement ledger</td></tr>
          <tr><td>Win rate</td><td>Won / (won + lost), excluding void outcomes</td></tr>
          <tr><td>Brier score</td><td>Mean squared probability error for non-void settled candidates</td></tr>
        </tbody>
      </table>
    </section>

    {_error_block(errors)}
    <div class="caveat"><strong>Research use only.</strong> This dashboard summarizes logged probability research and post-mortems. It is not investment advice, a trade recommendation, or an execution system.</div>
  </main>
</body>
</html>
"""


def _summary_from_csv_row(row: dict[str, str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in row.items():
        value = value.strip() if isinstance(value, str) else value
        if key == "ok":
            summary[key] = value.lower() == "true"
        elif key in INTEGER_FIELDS:
            summary[key] = int(value) if value else 0
        elif key in FLOAT_FIELDS:
            summary[key] = float(value) if value else None
        elif key in NESTED_FIELDS:
            summary[key] = json.loads(value) if value else ([] if key in {"unsettled_candidate_ids", "errors"} else {})
        else:
            summary[key] = value
    return summary


def _validate_summary(summary: dict[str, Any]) -> None:
    if not isinstance(summary, dict):
        raise ValueError("performance summary must be a JSON object")
    required = {"ok", "candidate_count", "settlement_count", "settled_count", "unsettled_count"}
    missing = sorted(key for key in required if key not in summary)
    if missing:
        raise ValueError("performance summary missing required fields: " + ", ".join(missing))


def _metric_card(label: str, value: str, note: str) -> str:
    return f"""<div class="card">
        <div class="label">{_escape(label)}</div>
        <div class="value">{value}</div>
        <div class="note">{_escape(note)}</div>
      </div>"""


def _breakdown_table(title: str, values: dict[str, Any], denominator: int) -> str:
    rows = []
    total = max(denominator, 1)
    for key, value in sorted(values.items()):
        count = _int(value)
        pct = count / total
        rows.append(
            f"""<tr>
              <td>{_escape(str(key))}<div class="bar" aria-hidden="true"><div class="fill" style="width:{pct * 100:.1f}%"></div></div></td>
              <td>{count}</td>
              <td>{_percent(pct)}</td>
            </tr>"""
        )
    body = "\n".join(rows) if rows else '<tr><td colspan="3">No records yet</td></tr>'
    return f"""<section class="section">
      <h2>{_escape(title)}</h2>
      <table>
        <thead><tr><th>Segment</th><th>Count</th><th>Share</th></tr></thead>
        <tbody>{body}</tbody>
      </table>
    </section>"""


def _unsettled_list(values: list[Any]) -> str:
    if not values:
        return "<p>No unresolved candidates in this summary.</p>"
    items = "".join(f"<li>{_escape(str(value))}</li>" for value in values)
    return f'<ul class="ids">{items}</ul>'


def _error_block(errors: list[Any]) -> str:
    if not errors:
        return ""
    items = "".join(f"<li>{_escape(str(error))}</li>" for error in errors)
    return f"""<section class="section">
      <h2>Validation issues</h2>
      <ul>{items}</ul>
    </section>"""


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _number(value: Any, *, digits: int = 0) -> str:
    if value is None or value == "":
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _escape(str(value))
    if digits == 0:
        return f"{number:,.0f}"
    return f"{number:,.{digits}f}"


def _currency(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return _escape(str(value))


def _percent(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return _escape(str(value))


def _points(value: Any) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.1f} pp"
    except (TypeError, ValueError):
        return _escape(str(value))


def _escape(value: str) -> str:
    return html.escape(value, quote=True)
