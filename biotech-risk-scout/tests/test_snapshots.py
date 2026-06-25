import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scout.storage.snapshots import build_snapshot_payload, snapshot_filename, write_snapshot  # noqa: E402


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
