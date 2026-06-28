import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.reports.alerts import build_alert_report, write_alert_report  # noqa: E402
from scout.storage.snapshots import build_snapshot_payload, compare_snapshots  # noqa: E402


def sample_comparison():
    return {
        "old_generated_at": "2026-06-24T12:00:00+00:00",
        "new_generated_at": "2026-06-25T12:00:00+00:00",
        "added": ["CCC"],
        "removed": ["BBB"],
        "changed": [
            {
                "ticker": "AAA",
                "score_delta": 12,
                "old_score": 60,
                "new_score": 72,
                "changes": {
                    "score": {"old": 60, "new": 72},
                    "days_until_event": {"old": 90, "new": 45},
                    "dilution_risk": {"old": "Low", "new": "Moderate"},
                },
            }
        ],
        "summary": {"added_count": 1, "removed_count": 1, "changed_count": 1},
    }


def test_build_alert_report_contains_core_sections():
    report = build_alert_report(sample_comparison())

    assert "# Biotech Risk Scout Alerts" in report
    assert "## Summary" in report
    assert "## Biggest Score Moves" in report
    assert "**AAA**: 60 → 72 (+12)" in report
    assert "Why it matters: catalyst timing changed; dilution risk changed" in report
    assert "## New Names Surfaced" in report
    assert "**CCC**" in report
    assert "## Removed Names" in report
    assert "**BBB**" in report
    assert "## Names To Inspect Manually" in report
    assert "Validated SEC filing text flags" not in report
    assert "days until event: 90 → 45" in report


def test_write_alert_report(tmp_path):
    output = tmp_path / "alerts.md"

    write_alert_report(sample_comparison(), str(output))

    content = output.read_text(encoding="utf-8")
    assert content.startswith("# Biotech Risk Scout Alerts")
    assert "AAA" in content


def test_alert_report_renders_validated_sec_flags():
    comparison = sample_comparison()
    comparison["changed"][0]["new_record"] = {
        "ticker": "AAA",
        "validated_sec_flags": {"has_going_concern": True},
        "validated_sec_filings": [{"form": "10-Q", "filing_date": "2026-06-01", "primary_document_url": "https://sec.test/filing", "matched_flags": ["has_going_concern"]}],
    }
    report = build_alert_report(comparison)
    assert "## Validated SEC filing text flags" in report
    assert "heuristic match" in report
    assert "requires human review" in report
    assert "https://sec.test/filing" in report


def test_alert_report_omits_sec_section_when_flags_are_false():
    comparison = sample_comparison()
    comparison["changed"][0]["new_record"] = {
        "ticker": "AAA",
        "validated_sec_flags": {
            "has_going_concern": False,
            "has_reverse_split": False,
        },
        "validated_sec_filings": [],
    }

    report = build_alert_report(comparison)

    assert "## Validated SEC filing text flags" not in report


def test_operator_brief_appears_and_counts():
    report = build_alert_report(sample_comparison())

    assert "## Operator Brief" in report
    # Operator Brief sits above the existing Summary section.
    assert report.index("## Operator Brief") < report.index("## Summary")
    assert "- Scan date: 2026-06-25" in report
    assert "- New names surfaced: 1" in report
    assert "- Removed names: 1" in report
    assert "- Names with score changes: 1" in report
    assert "- Names with validated SEC filing-text flags: 0" in report
    assert "- Highest priority name: AAA, score 72" in report
    assert "- Biggest positive score move: AAA, +12" in report
    assert "- Biggest negative score move: none" in report


def test_operator_brief_counts_validated_sec_flags():
    comparison = sample_comparison()
    comparison["added_records"] = [
        {"ticker": "CCC", "score": 80, "validated_sec_flags": {"has_going_concern": True}},
    ]

    report = build_alert_report(comparison)

    assert "- Names with validated SEC filing-text flags: 1" in report


def test_operator_brief_highest_priority_prefers_top_score():
    comparison = sample_comparison()
    comparison["added_records"] = [{"ticker": "ZZZ", "score": 95}]

    report = build_alert_report(comparison)

    assert "- Highest priority name: ZZZ, score 95" in report


def test_operator_brief_reports_no_score_changes():
    comparison = {
        "old_generated_at": "2026-06-24T12:00:00+00:00",
        "new_generated_at": "2026-06-25T12:00:00+00:00",
        "added": [],
        "removed": [],
        "changed": [],
        "summary": {"added_count": 0, "removed_count": 0, "changed_count": 0},
    }

    report = build_alert_report(comparison)

    assert "- New names surfaced: 0" in report
    assert "- No score changes detected in this run." in report


def test_operator_brief_empty_comparison_does_not_crash():
    report = build_alert_report({})

    assert "## Operator Brief" in report
    assert "- Scan date: unknown" in report
    assert "- No score changes detected in this run." in report


def _quiet_comparison(records):
    old = build_snapshot_payload(records, timestamp="2026-06-26T12:00:00+00:00")
    new = build_snapshot_payload(records, timestamp="2026-06-27T12:00:00+00:00")
    return compare_snapshots(old, new)


def test_operator_brief_quiet_scan_reports_current_highest_priority():
    records = [
        {
            "ticker": "AAA",
            "score": 80,
            "main_reason": "Near-term catalyst",
            "validated_sec_flags": {"has_going_concern": True},
        },
        {
            "ticker": "BBB",
            "score": 50,
            "main_reason": "Clean cap structure",
            "validated_sec_flags": {},
        },
    ]

    report = build_alert_report(_quiet_comparison(records))

    assert "- Tickers in current scan: 2" in report
    assert "- Highest priority name: AAA, score 80" in report
    assert "- Names with validated SEC filing-text flags: 1" in report
    assert "- Names with score changes: 0" in report


def test_operator_brief_counts_unchanged_validated_flags():
    records = [
        {
            "ticker": "AAA",
            "score": 70,
            "main_reason": "Near-term catalyst",
            "validated_sec_flags": {"has_reverse_split": True},
        }
    ]

    report = build_alert_report(_quiet_comparison(records))

    assert "- Names with validated SEC filing-text flags: 1" in report
    assert "## Validated SEC filing text flags" not in report


def test_operator_brief_reports_failed_tickers():
    records = [
        {"ticker": "AAA", "score": 80, "main_reason": "Near-term catalyst"},
        {"ticker": "FAIL1", "score": 0, "main_reason": "SEC ingestion failed"},
        {"ticker": "FAIL2", "score": 0, "main_reason": "Fetch error"},
    ]

    report = build_alert_report(_quiet_comparison(records))

    assert "- Failed tickers: 2 (FAIL1, FAIL2)" in report
    assert "- Highest priority name: AAA, score 80" in report


def test_operator_brief_old_payload_without_current_summary():
    comparison = {
        "old_generated_at": "2026-06-26T12:00:00+00:00",
        "new_generated_at": "2026-06-27T12:00:00+00:00",
        "added": ["CCC"],
        "added_records": [
            {
                "ticker": "CCC",
                "score": 65,
                "validated_sec_flags": {"has_going_concern": True},
            }
        ],
        "removed": [],
        "changed": [],
        "summary": {"added_count": 1, "removed_count": 0, "changed_count": 0},
    }

    report = build_alert_report(comparison)

    assert "- Tickers in current scan:" not in report
    assert "- Failed tickers:" not in report
    assert "- Names with validated SEC filing-text flags: 1" in report
    assert "- Highest priority name: CCC, score 65" in report
