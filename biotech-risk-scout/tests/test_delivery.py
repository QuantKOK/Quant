import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from scout.delivery import (  # noqa: E402
    AlertDelivery,
    ConsoleDelivery,
    DeliveryResult,
    FileArchiveDelivery,
    build_email_digest,
)

SAMPLE_REPORT = "# Biotech Risk Scout Alerts\n\n## Summary\n\n- Added tickers: **1**\n"


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
