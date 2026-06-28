"""Delivery layer for generated alert reports.

Most channels are local and offline. Discord webhook delivery additionally makes
a network call only when explicitly configured. The shared interface lets
further channels be added without changing existing callers.
"""

from scout.delivery.base import AlertDelivery, DeliveryResult
from scout.delivery.console import ConsoleDelivery
from scout.delivery.discord import DiscordWebhookDelivery, build_discord_message
from scout.delivery.email_digest import build_email_digest
from scout.delivery.files import FileArchiveDelivery

__all__ = [
    "AlertDelivery",
    "DeliveryResult",
    "ConsoleDelivery",
    "FileArchiveDelivery",
    "DiscordWebhookDelivery",
    "build_discord_message",
    "build_email_digest",
]
