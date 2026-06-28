import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from scout.delivery import (  # noqa: E402
    AlertDelivery,
    ConsoleDelivery,
    DeliveryResult,
    DiscordWebhookDelivery,
    FileArchiveDelivery,
    build_discord_message,
    build_email_digest,
)
from scout.delivery import discord as discord_module  # noqa: E402
from scout.reports.alerts import build_alert_report  # noqa: E402
from scout.storage.snapshots import build_snapshot_payload, compare_snapshots  # noqa: E402

SAMPLE_REPORT = "# Biotech Risk Scout Alerts\n\n## Summary\n\n- Added tickers: **1**\n"

REPORT_WITH_BRIEF = (
    "# Biotech Risk Scout Alerts\n\n"
    "Old snapshot: `2026-06-24T12:00:00+00:00`\n"
    "New snapshot: `2026-06-25T12:00:00+00:00`\n\n"
    "## Operator Brief\n\n"
    "- Scan date: 2026-06-25\n"
    "- New names surfaced: 1\n"
    "- Highest priority name: AAA, score 72\n\n"
    "## Summary\n\n- Added tickers: **1**\n"
)


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getcode(self):
        return self.status


def test_build_email_digest_returns_subject_and_body():
    digest = build_email_digest(SAMPLE_REPORT)

    assert set(digest) == {"subject", "body"}
    assert digest["subject"].startswith("Biotech Risk Scout")
    # Body preserves the markdown report and prepends the diligence-only header.
    assert "not investment advice" in digest["body"]
    assert SAMPLE_REPORT in digest["body"]


def test_build_email_digest_subject_includes_date():
    digest = build_email_digest(SAMPLE_REPORT, today="2026-06-27")

    assert "2026-06-27" in digest["subject"]


def test_build_email_digest_custom_prefix():
    digest = build_email_digest(SAMPLE_REPORT, subject_prefix="Custom Prefix", today="2026-06-27")

    assert digest["subject"].startswith("Custom Prefix")


def test_console_delivery_prints(capsys):
    result = ConsoleDelivery().deliver("latest-alerts.md", SAMPLE_REPORT)

    captured = capsys.readouterr()
    assert SAMPLE_REPORT in captured.out
    assert result.channel == "console"
    assert result.destination is None
    assert result.ok is True
    assert result.message == "printed alert report"


def test_file_archive_delivery_writes_file(tmp_path):
    archive_dir = tmp_path / "archive"
    result = FileArchiveDelivery(str(archive_dir)).deliver("snapshots/latest-alerts.md", SAMPLE_REPORT)

    archived = archive_dir / "latest-alerts.md"
    assert result.ok is True
    assert result.channel == "file_archive"
    assert archived.exists()
    assert archived.read_text(encoding="utf-8") == SAMPLE_REPORT
    assert result.destination == str(archived)


def test_file_archive_delivery_creates_missing_nested_dir(tmp_path):
    archive_dir = tmp_path / "deeply" / "nested" / "archive"
    result = FileArchiveDelivery(str(archive_dir)).deliver("latest-alerts.md", SAMPLE_REPORT)

    assert result.ok is True
    assert (archive_dir / "latest-alerts.md").exists()


def test_file_archive_delivery_reports_failure_without_raising(tmp_path):
    # Point the archive "dir" at an existing file so makedirs raises OSError.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")

    result = FileArchiveDelivery(str(blocker)).deliver("latest-alerts.md", SAMPLE_REPORT)

    assert result.ok is False
    assert result.channel == "file_archive"
    assert "failed to archive" in result.message


def test_alert_delivery_base_is_abstract():
    with pytest.raises(NotImplementedError):
        AlertDelivery().deliver("path", "text")


def test_delivery_result_fields():
    result = DeliveryResult(channel="x", destination=None, ok=True, message="m")
    assert result.channel == "x"
    assert result.destination is None
    assert result.ok is True
    assert result.message == "m"


# --- Discord webhook delivery -------------------------------------------------


def test_build_discord_message_includes_operator_brief():
    message = build_discord_message(REPORT_WITH_BRIEF)

    assert message.startswith("Biotech Risk Scout alert report")
    assert "## Operator Brief" in message
    assert "Highest priority name: AAA, score 72" in message
    assert "Not investment advice." in message
    # The next section must not bleed into the message.
    assert "## Summary" not in message


def test_build_discord_message_truncates_long_report():
    long_brief = "## Operator Brief\n\n" + "\n".join(f"- line {i}" for i in range(500))
    report = f"# Biotech Risk Scout Alerts\n\n{long_brief}\n\n## Summary\n\n- x\n"

    message = build_discord_message(report, max_chars=200)

    assert len(message) <= 200
    assert message.endswith("... truncated; see full latest-alerts.md artifact.")


def test_discord_delivery_posts_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["data"] = request.data
        captured["method"] = request.get_method()
        return _FakeResponse(204)

    monkeypatch.setattr(discord_module, "urlopen", fake_urlopen)

    result = DiscordWebhookDelivery("https://discord.test/webhook/abc").deliver(
        "latest-alerts.md", REPORT_WITH_BRIEF
    )

    assert result.ok is True
    assert result.channel == "discord"
    assert result.destination == "webhook"
    assert captured["method"] == "POST"
    assert captured["url"] == "https://discord.test/webhook/abc"
    payload = json.loads(captured["data"].decode("utf-8"))
    assert payload["username"] == "Biotech Risk Scout"
    assert "Biotech Risk Scout alert report" in payload["content"]


def test_discord_delivery_hides_webhook_url_on_failure(monkeypatch):
    secret_url = "https://discord.test/webhook/super-secret-token"

    def fake_urlopen(request, timeout=None):
        # Simulate a worst case where the error text embeds the URL.
        raise discord_module.URLError(f"connection refused for {secret_url}")

    monkeypatch.setattr(discord_module, "urlopen", fake_urlopen)

    result = DiscordWebhookDelivery(secret_url).deliver("latest-alerts.md", SAMPLE_REPORT)

    assert result.ok is False
    assert secret_url not in result.message
    assert "super-secret-token" not in result.message


def test_discord_delivery_http_error_is_failure(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise discord_module.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(discord_module, "urlopen", fake_urlopen)

    result = DiscordWebhookDelivery("https://discord.test/webhook/abc").deliver(
        "latest-alerts.md", SAMPLE_REPORT
    )

    assert result.ok is False
    assert "404" in result.message
    assert "discord.test" not in result.message


def test_discord_message_inherits_current_state_operator_brief():
    records = [
        {
            "ticker": "AAA",
            "score": 88,
            "main_reason": "Near-term catalyst",
            "validated_sec_flags": {"has_going_concern": True},
        }
    ]
    old = build_snapshot_payload(records, timestamp="2026-06-26T12:00:00+00:00")
    new = build_snapshot_payload(records, timestamp="2026-06-27T12:00:00+00:00")
    report = build_alert_report(compare_snapshots(old, new))

    message = build_discord_message(report)

    assert "Highest priority name: AAA, score 88" in message
    assert "Names with validated SEC filing-text flags: 1" in message
