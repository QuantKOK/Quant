"""File-archive alert-report delivery.

Safe, local-only: writes a copy of the alert report into an archive directory.
No external services are contacted.
"""

from __future__ import annotations

import os

from scout.delivery.base import AlertDelivery, DeliveryResult


class FileArchiveDelivery(AlertDelivery):
    """Write a copy of the alert report into an archive directory.

    The copy keeps the same basename as ``report_path``. The archive directory
    is created if it does not exist. Delivery failures (for example, an
    unwritable destination) are reported through ``DeliveryResult`` rather than
    raised, so one bad destination never aborts other deliveries.
    """

    channel = "file_archive"

    def __init__(self, archive_dir: str) -> None:
        self.archive_dir = archive_dir

    def deliver(self, report_path: str, report_text: str) -> DeliveryResult:
        basename = os.path.basename(report_path) or "alert-report.md"
        destination = os.path.join(self.archive_dir, basename)
        try:
            os.makedirs(self.archive_dir, exist_ok=True)
            with open(destination, "w", encoding="utf-8") as handle:
                handle.write(report_text)
        except OSError as exc:
            return DeliveryResult(
                channel=self.channel,
                destination=destination,
                ok=False,
                message=f"failed to archive alert report: {exc}",
            )
        return DeliveryResult(
            channel=self.channel,
            destination=destination,
            ok=True,
            message=f"archived alert report to {destination}",
        )
