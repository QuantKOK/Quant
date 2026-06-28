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
        sec_validation_cache_ttl_days=45,
    )

    command = build_command(args)

    assert "--validate-sec-text" in command
    assert command[command.index("--max-sec-documents") + 1] == "2"
    assert command[command.index("--sec-validation-cache") + 1] == str(
        tmp_path / "sec-validation.json"
    )
    assert command[command.index("--sec-validation-cache-ttl-days") + 1] == "45"


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
        sec_validation_cache_ttl_days=45,
    )

    assert run_daily_scan.run_scan(args, str(tmp_path)) == 0
    assert captured == {
        "tickers": ["MRNA"],
        "max_workers": 4,
        "validate_sec_text": True,
        "max_sec_documents": 2,
        "sec_validation_cache": str(tmp_path / "sec-validation.json"),
        "sec_validation_ttl_days": 45,
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


# --- Alert-report delivery ----------------------------------------------------


def _delivery_args(**kwargs):
    defaults = dict(
        print_alert_report=False,
        archive_alert_report_dir=None,
        write_email_digest=None,
        discord_webhook_url=None,
        discord_webhook_env=None,
        require_delivery=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _seed_alert_report(output_dir):
    report = "# Biotech Risk Scout Alerts\n\n## Summary\n\n- Added tickers: **1**\n"
    (output_dir / "latest-alerts.md").write_text(report, encoding="utf-8")
    return report


def test_deliver_alert_report_no_flags_is_noop(tmp_path):
    # No flags set and no alert file present: must not raise and returns nothing.
    results = run_daily_scan.deliver_alert_report(_delivery_args(), str(tmp_path))
    assert results == []


def test_deliver_alert_report_print(tmp_path, capsys):
    report = _seed_alert_report(tmp_path)

    results = run_daily_scan.deliver_alert_report(
        _delivery_args(print_alert_report=True), str(tmp_path)
    )

    assert [r.channel for r in results] == ["console"]
    assert report in capsys.readouterr().out


def test_deliver_alert_report_archive(tmp_path):
    report = _seed_alert_report(tmp_path)
    archive_dir = tmp_path / "archive"

    results = run_daily_scan.deliver_alert_report(
        _delivery_args(archive_alert_report_dir=str(archive_dir)), str(tmp_path)
    )

    archived = archive_dir / "latest-alerts.md"
    assert results[0].ok is True
    assert archived.read_text(encoding="utf-8") == report


def test_deliver_alert_report_email_digest(tmp_path):
    _seed_alert_report(tmp_path)
    digest_path = tmp_path / "digests" / "digest.txt"

    results = run_daily_scan.deliver_alert_report(
        _delivery_args(write_email_digest=str(digest_path)), str(tmp_path)
    )

    assert results[0].channel == "email_digest"
    assert results[0].ok is True
    content = digest_path.read_text(encoding="utf-8")
    assert content.startswith("Subject: ")
    assert "not investment advice" in content


def test_deliver_alert_report_failure_does_not_raise(tmp_path, capsys):
    _seed_alert_report(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")

    results = run_daily_scan.deliver_alert_report(
        _delivery_args(archive_alert_report_dir=str(blocker)), str(tmp_path)
    )

    assert results[0].ok is False
    assert "WARNING" in capsys.readouterr().err


def test_daily_runner_skips_discord_when_env_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("MISSING_DISCORD_HOOK", raising=False)
    _seed_alert_report(tmp_path)

    results = run_daily_scan.deliver_alert_report(
        _delivery_args(discord_webhook_env="MISSING_DISCORD_HOOK"), str(tmp_path)
    )

    assert results == []
    assert "MISSING_DISCORD_HOOK" in capsys.readouterr().err


def test_daily_runner_uses_discord_env_when_present(monkeypatch, tmp_path):
    monkeypatch.setenv("PRESENT_DISCORD_HOOK", "https://discord.test/webhook/xyz")
    _seed_alert_report(tmp_path)

    captured = {}

    class FakeDiscord:
        def __init__(self, webhook_url, username="Biotech Risk Scout"):
            captured["webhook_url"] = webhook_url

        def deliver(self, report_path, report_text):
            captured["delivered"] = True
            from scout.delivery import DeliveryResult

            return DeliveryResult(channel="discord", destination="webhook", ok=True, message="posted")

    monkeypatch.setattr(run_daily_scan, "DiscordWebhookDelivery", FakeDiscord)

    results = run_daily_scan.deliver_alert_report(
        _delivery_args(discord_webhook_env="PRESENT_DISCORD_HOOK"), str(tmp_path)
    )

    assert captured["webhook_url"] == "https://discord.test/webhook/xyz"
    assert captured.get("delivered") is True
    assert [r.channel for r in results] == ["discord"]
    assert results[0].ok is True


def test_required_delivery_fails_when_no_channel_completed(capsys):
    failed = run_daily_scan.delivery_requirement_failed(
        _delivery_args(require_delivery=True),
        [],
    )

    assert failed is True
    assert "no delivery channel completed" in capsys.readouterr().err


def test_required_delivery_fails_when_channel_failed(capsys):
    result = run_daily_scan.DeliveryResult(
        channel="discord",
        destination="webhook",
        ok=False,
        message="failed",
    )

    failed = run_daily_scan.delivery_requirement_failed(
        _delivery_args(require_delivery=True),
        [result],
    )

    assert failed is True
    assert "discord" in capsys.readouterr().err


def test_required_delivery_passes_when_channel_succeeded():
    result = run_daily_scan.DeliveryResult(
        channel="discord",
        destination="webhook",
        ok=True,
        message="posted",
    )

    assert run_daily_scan.delivery_requirement_failed(
        _delivery_args(require_delivery=True),
        [result],
    ) is False


def test_delivery_failures_remain_best_effort_by_default():
    result = run_daily_scan.DeliveryResult(
        channel="discord",
        destination="webhook",
        ok=False,
        message="failed",
    )

    assert run_daily_scan.delivery_requirement_failed(_delivery_args(), [result]) is False


def test_main_runs_delivery_when_flags_set(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SEC_USER_AGENT", "BiotechRiskScout test-contact")
    tickers_file = tmp_path / "watchlist.txt"
    tickers_file.write_text("MRNA\n", encoding="utf-8")
    output_dir = tmp_path / "out"
    digest_path = tmp_path / "digest.txt"

    report = "# Biotech Risk Scout Alerts\n\nbody line\n"

    def fake_write_alerts(out_dir, _previous):
        (tmp_path / "out" / "latest-alerts.md").write_text(report, encoding="utf-8")

    monkeypatch.setattr(run_daily_scan, "run_scan", lambda args, out_dir: 0)
    monkeypatch.setattr(run_daily_scan, "write_post_scan_alerts", fake_write_alerts)

    code = run_daily_scan.main(
        [
            "--tickers-file", str(tickers_file),
            "--output-dir", str(output_dir),
            "--print-alert-report",
            "--write-email-digest", str(digest_path),
        ]
    )

    assert code == 0
    assert report in capsys.readouterr().out
    assert digest_path.exists()
    assert "not investment advice" in digest_path.read_text(encoding="utf-8")
