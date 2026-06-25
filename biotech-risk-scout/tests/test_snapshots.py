import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.storage.snapshots import (  # noqa: E402
    build_snapshot_payload,
    compare_snapshots,
    format_snapshot_comparison,
    load_snapshot,
    snapshot_filename,
    write_snapshot,
)


def test_snapshot_filename_uses_date_from_timestamp():
    assert snapshot_filename("2026-06-25T12:00:00+00:00") == "scan-20260625.json"


def test_build_snapshot_payload_adds_metadata():
    payload = build_snapshot_payload([{"ticker": "TEST", "score": 88}], timestamp="2026-06-25T12:00:00+00:00")

    assert payload["generated_at"] == "2026-06-25T12:00:00+00:00"
    assert payload["record_count"] == 1
    assert payload["records"][0]["ticker"] == "TEST"


def test_write_snapshot_writes_dated_and_latest_files(tmp_path):
    paths = write_snapshot(
        [{"ticker": "TEST", "score": 88}],
        str(tmp_path),
        timestamp="2026-06-25T12:00:00+00:00",
    )

    dated = tmp_path / "scan-20260625.json"
    latest = tmp_path / "latest.json"
    assert paths["snapshot_path"] == str(dated)
    assert paths["latest_path"] == str(latest)
    assert dated.exists()
    assert latest.exists()

    dated_payload = json.loads(dated.read_text(encoding="utf-8"))
    latest_payload = json.loads(latest.read_text(encoding="utf-8"))
    assert dated_payload == latest_payload
    assert dated_payload["record_count"] == 1


def test_load_snapshot_supports_plain_record_lists(tmp_path):
    path = tmp_path / "plain.json"
    path.write_text(json.dumps([{"ticker": "TEST", "score": 88}]), encoding="utf-8")

    payload = load_snapshot(str(path))

    assert payload["record_count"] == 1
    assert payload["records"][0]["ticker"] == "TEST"


def test_compare_snapshots_tracks_added_removed_and_changed():
    old = build_snapshot_payload(
        [
            {"ticker": "AAA", "rank": 1, "score": 80, "upcoming_catalyst": "Old catalyst", "days_until_event": 90},
            {"ticker": "BBB", "rank": 2, "score": 60},
        ],
        timestamp="2026-06-24T12:00:00+00:00",
    )
    new = build_snapshot_payload(
        [
            {"ticker": "AAA", "rank": 2, "score": 72, "upcoming_catalyst": "New catalyst", "days_until_event": 45},
            {"ticker": "CCC", "rank": 1, "score": 90},
        ],
        timestamp="2026-06-25T12:00:00+00:00",
    )

    comparison = compare_snapshots(old, new)

    assert comparison["added"] == ["CCC"]
    assert comparison["removed"] == ["BBB"]
    assert comparison["summary"] == {"added_count": 1, "removed_count": 1, "changed_count": 1}
    assert comparison["changed"][0]["ticker"] == "AAA"
    assert comparison["changed"][0]["score_delta"] == -8
    assert comparison["changed"][0]["changes"]["upcoming_catalyst"] == {
        "old": "Old catalyst",
        "new": "New catalyst",
    }


def test_format_snapshot_comparison_outputs_readable_report():
    comparison = {
        "old_generated_at": "old",
        "new_generated_at": "new",
        "added": ["CCC"],
        "removed": ["BBB"],
        "changed": [
            {
                "ticker": "AAA",
                "score_delta": 5,
                "old_score": 70,
                "new_score": 75,
                "changes": {"score": {"old": 70, "new": 75}},
            }
        ],
        "summary": {"added_count": 1, "removed_count": 1, "changed_count": 1},
    }

    report = format_snapshot_comparison(comparison)

    assert "Snapshot Comparison" in report
    assert "Added: 1 | Removed: 1 | Changed: 1" in report
    assert "CCC" in report
    assert "BBB" in report
    assert "AAA: score 70 -> 75 (+5)" in report
