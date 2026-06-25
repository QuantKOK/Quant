import argparse
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scripts.run_daily_scan import build_command, preserve_previous_latest, write_no_previous_alert  # noqa: E402


def test_build_command_includes_snapshot_exports_and_no_table(tmp_path):
    tickers_file = tmp_path / "watchlist.txt"
    tickers_file.write_text("MRNA\n", encoding="utf-8")
    output_dir = tmp_path / "snapshots"
    args = argparse.Namespace(
        tickers_file=str(tickers_file),
        output_dir=str(output_dir),
        min_score=30,
        date="20260625",
        no_table=True,
    )

    command = build_command(args)

    assert "--tickers-file" in command
    assert str(tickers_file) in command
    assert "--snapshot-dir" in command
    assert str(output_dir) in command
    assert "--csv" in command
    assert os.path.join(str(output_dir), "scan-20260625.csv") in command
    assert "--json" in command
    assert os.path.join(str(output_dir), "scan-20260625.records.json") in command
    assert "--min-score" in command
    assert "30" in command
    assert "--no-table" in command


def test_preserve_previous_latest_copies_existing_snapshot(tmp_path):
    latest = tmp_path / "latest.json"
    latest.write_text('{"records": []}\n', encoding="utf-8")

    previous = preserve_previous_latest(str(tmp_path))

    assert previous == str(tmp_path / "previous-latest.json")
    assert (tmp_path / "previous-latest.json").read_text(encoding="utf-8") == '{"records": []}\n'


def test_write_no_previous_alert(tmp_path):
    output = tmp_path / "latest-alerts.md"

    write_no_previous_alert(str(output))

    content = output.read_text(encoding="utf-8")
    assert "No previous `latest.json` snapshot" in content
    assert content.startswith("# Biotech Risk Scout Alerts")
