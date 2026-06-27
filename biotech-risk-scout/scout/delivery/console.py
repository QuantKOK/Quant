"""Console (stdout) alert-report delivery.

Safe, local-only: prints the report text to stdout. No external services.
"""

from __future__ import annotations

from scout.delivery.base import AlertDelivery, DeliveryResult


class ConsoleDelivery(AlertDelivery):
    """Print the alert report to stdout."""

    channel = "console"

    def deliver(self, report_path: str, report_text: str) -> DeliveryResult:
        print(report_text)
        return DeliveryResult(
            channel="console",
            destination=None,
            ok=True,
            message="printed alert report",
        )
