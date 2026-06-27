"""Local-only delivery layer for generated alert reports.

Supported channels are safe and offline: console/stdout, file archive, and
email-style digest generation (which builds text only and sends nothing). The
``AlertDelivery`` interface lets future channels (email, Slack, Discord, GitHub
Issues, etc.) be added without changing existing callers.
"""

from scout.delivery.base import AlertDelivery, DeliveryResult
from scout.delivery.console import ConsoleDelivery
from scout.delivery.email_digest import build_email_digest
from scout.delivery.files import FileArchiveDelivery

__all__ = [
    "AlertDelivery",
    "DeliveryResult",
    "ConsoleDelivery",
    "FileArchiveDelivery",
    "build_email_digest",
]
