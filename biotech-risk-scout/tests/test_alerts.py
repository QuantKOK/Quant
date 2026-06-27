import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.reports.alerts import build_alert_report, write_alert_report  # noqa: E402


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
