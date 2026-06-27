import argparse
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

from scripts import run_daily_scan  # noqa: E402
from scripts.run_daily_scan import build_command, preserve_previous_latest, write_no_previous_alert  # noqa: E402
from scout.scoring.research_priority import PriorityScore  # noqa: E402


def _priority(score=72, summary="-"):
    return PriorityScore(score, "Near-term catalyst", summary, 27, 14, 13, 10, 8, 0)


def _successful_row(ticker):
    return {
        "ticker": ticker,
        "card": object(),
        "priority": _priority(),
        "data": {
            "upcoming_catalyst": "Phase 3 readout",
            "cash_runway_months": 18.0,
            "dilution_risk": "Low",
            "evidence_quality": "High",
            "financing_form_counts": {},
            "structural_red_flags": [],
        },
    }


def _failed_row(ticker):
    return {
        "ticker": ticker,
        "card": None,
        "priority": _priority(score=0, summary="SEC ingestion failed"),
        "data": {
            "upcoming_catalyst": "Error",
            "dilution_risk": "?",
            "evidence_quality": "?",
        },
    }


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


def test_build_command_includes_sec_validation_options(tmp_path):
    args = argparse.Namespace(
        tickers_file=str(tmp_path / "watchlist.txt"),
        output_dir=str(tmp_path / "snapshots"),
        min_score=0,
        date="20260627",
        no_table=True,
        validate_sec_text=True,
        max_sec_documents=2,
        sec_validation_cache=str(tmp_path / "sec-validation.json"),
    )

    command = build_command(args)

    assert "--validate-sec-text" in command
    assert command[command.index("--max-sec-documents") + 1] == "2"
    assert command[command.index("--sec-validation-cache") + 1] == str(
        tmp_path / "sec-validation.json"
    )


def test_run_scan_passes_sec_validation_options(monkeypatch, tmp_path):
    tickers_file = tmp_path / "watchlist.txt"
    tickers_file.write_text("MRNA\n", encoding="utf-8")
    captured = {}

    def fake_scan_tickers(tickers, **kwargs):
        captured["tickers"] = tickers
        captured.update(kwargs)
        return [{"ticker": "MRNA", "card": object()}]

    monkeypatch.setattr(run_daily_scan, "scan_tickers", fake_scan_tickers)
    monkeypatch.setattr(run_daily_scan, "export_rows_to_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_daily_scan, "export_rows_to_csv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(run_daily_scan, "export_snapshot", lambda *_args, **_kwargs: None)
    args = argparse.Namespace(
        tickers_file=str(tickers_file),
        min_score=0,
        date="20260627",
        no_table=True,
        max_workers=4,
        validate_sec_text=True,
        max_sec_documents=2,
        sec_validation_cache=str(tmp_path / "sec-validation.json"),
    )

    assert run_daily_scan.run_scan(args, str(tmp_path)) == 0
    assert captured == {
        "tickers": ["MRNA"],
        "max_workers": 4,
        "validate_sec_text": True,
        "max_sec_documents": 2,
        "sec_validation_cache": str(tmp_path / "sec-validation.json"),
    }


def test_run_scan_does_not_replace_outputs_when_all_tickers_fail(monkeypatch, tmp_path):
    tickers_file = tmp_path / "watchlist.txt"
    tickers_file.write_text("MRNA\n", encoding="utf-8")
    latest = tmp_path / "latest.json"
    latest.write_text('{"records": [{"ticker": "OLD"}]}\n', encoding="utf-8")
    monkeypatch.setattr(
        run_daily_scan,
        "scan_tickers",
        lambda *_args, **_kwargs: [{"ticker": "MRNA", "card": None}],
    )
    args = argparse.Namespace(
        tickers_file=str(tickers_file),
        min_score=0,
        date="20260627",
        no_table=True,
        max_workers=4,
        validate_sec_text=True,
        max_sec_documents=2,
        sec_validation_cache=str(tmp_path / "sec-validation.json"),
    )

    assert run_daily_scan.run_scan(args, str(tmp_path)) == 1
    assert latest.read_text(encoding="utf-8") == '{"records": [{"ticker": "OLD"}]}\n'


def test_run_scan_reports_partial_failure_and_writes_outputs(monkeypatch, tmp_path, capsys):
    tickers_file = tmp_path / "watchlist.txt"
    tickers_file.write_text("GOOD\nFAIL\n", encoding="utf-8")
    monkeypatch.setattr(
        run_daily_scan,
        "scan_tickers",
        lambda *_args, **_kwargs: [_successful_row("GOOD"), _failed_row("FAIL")],
    )
    args = argparse.Namespace(
        tickers_file=str(tickers_file),
        min_score=0,
        date="20260627",
        no_table=True,
        max_workers=4,
        validate_sec_text=True,
        max_sec_documents=2,
        sec_validation_cache=str(tmp_path / "sec-validation.json"),
    )

    assert run_daily_scan.run_scan(args, str(tmp_path)) == 0
    assert (tmp_path / "scan-20260627.csv").exists()
    assert (tmp_path / "scan-20260627.records.json").exists()
    assert "WARNING: FAIL scan failed" in capsys.readouterr().err


def test_main_requires_sec_user_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    assert run_daily_scan.main(["--output-dir", str(tmp_path)]) == 1


def test_main_proceeds_when_sec_user_agent_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "BiotechRiskScout test-contact")
    tickers_file = tmp_path / "watchlist.txt"
    tickers_file.write_text("MRNA\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(
        run_daily_scan,
        "run_scan",
        lambda args, output_dir: called.append((args, output_dir)) or 0,
    )
    monkeypatch.setattr(run_daily_scan, "write_post_scan_alerts", lambda *_args: None)

    assert run_daily_scan.main(
        ["--tickers-file", str(tickers_file), "--output-dir", str(tmp_path)]
    ) == 0
    assert called
