"""Optional Discord webhook delivery for alert reports.

Opt-in only: nothing here runs unless a caller constructs
``DiscordWebhookDelivery`` with an explicit webhook URL. Uses the Python
standard library only. The webhook URL is never echoed back in results, logs,
or error messages.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scout.delivery.base import AlertDelivery, DeliveryResult

DISCORD_CONTENT_LIMIT = 2000
DEFAULT_USERNAME = "Biotech Risk Scout"
HEADER = "Biotech Risk Scout alert report"
DISCLAIMER = "Diligence triage only. Not investment advice."
TRUNCATION_NOTE = "... truncated; see full latest-alerts.md artifact."


def build_discord_message(report_text: str, max_chars: int = 1900) -> str:
    """Build a compact Discord message from an alert report.

    Includes a short header, the Operator Brief if present, and a diligence-only
    disclaimer. Long messages are truncated to ``max_chars`` (well under
    Discord's 2000-character content limit) with a pointer to the full report.
    """
    brief = _extract_operator_brief(report_text)
    sections = [HEADER]
    if brief:
        sections.append(brief)
    sections.append(DISCLAIMER)
    message = "\n\n".join(sections)

    if len(message) <= max_chars:
        return message

    suffix = "\n\n" + TRUNCATION_NOTE
    keep = max(0, max_chars - len(suffix))
    return message[:keep].rstrip() + suffix


def _extract_operator_brief(report_text: str) -> str:
    """Return the '## Operator Brief' section of a report, or '' if absent."""
    collected: list[str] = []
    capturing = False
    for line in report_text.splitlines():
        if line.strip() == "## Operator Brief":
            capturing = True
            collected.append(line)
            continue
        if capturing:
            if line.startswith("## "):  # next section starts
                break
            collected.append(line)
    return "\n".join(collected).strip()


class DiscordWebhookDelivery(AlertDelivery):
    """Post a compact alert summary to a Discord webhook.

    Network failures are returned as a failed ``DeliveryResult`` rather than
    raised, so a delivery problem never aborts the surrounding scan.
    """

    channel = "discord"

    def __init__(self, webhook_url: str, username: str = DEFAULT_USERNAME) -> None:
        self.webhook_url = webhook_url
        self.username = username

    def deliver(self, report_path: str, report_text: str) -> DeliveryResult:
        message = build_discord_message(report_text)
        payload = json.dumps({"username": self.username, "content": message}).encode("utf-8")
        request = Request(
            self.webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "BiotechRiskScout",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=15) as response:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
        except HTTPError as exc:
            return self._failure(f"Discord webhook failed with HTTP {exc.code}")
        except URLError as exc:
            return self._failure(f"Discord webhook failed: {exc.reason}")
        except Exception as exc:  # noqa: BLE001
            return self._failure(f"Discord webhook failed: {exc}")

        if status is not None and 200 <= int(status) < 300:
            return DeliveryResult(
                channel=self.channel,
                destination="webhook",
                ok=True,
                message=f"posted alert summary to Discord webhook (HTTP {status})",
            )
        return self._failure(f"Discord webhook returned unexpected HTTP {status}")

    def _failure(self, message: str) -> DeliveryResult:
        """Build a failed result, redacting the webhook URL from the message."""
        return DeliveryResult(
            channel=self.channel,
            destination="webhook",
            ok=False,
            message=self._redact(message),
        )

    def _redact(self, text: str) -> str:
        if self.webhook_url:
            return text.replace(self.webhook_url, "<redacted-webhook>")
        return text
