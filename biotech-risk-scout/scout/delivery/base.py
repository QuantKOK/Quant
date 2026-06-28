"""Base abstractions for the alert-report delivery layer.

Most channels are local and offline. The Discord webhook channel makes a network
call only when explicitly configured. The interface lets further channels be
added without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DeliveryResult:
    """Outcome of a single delivery attempt.

    ``ok`` is True when the delivery action succeeded. ``destination`` is the
    concrete target (a file path, for example) or None for channels that have no
    addressable destination, such as stdout.
    """

    channel: str
    destination: str | None
    ok: bool
    message: str


class AlertDelivery:
    """Base interface for alert-report delivery channels.

    Subclasses set the ``channel`` attribute and implement :meth:`deliver`. This
    is a small base class rather than a ``typing.Protocol`` so channels can be
    subclassed directly and recognized with ``isinstance``.
    """

    channel: str = "base"

    def deliver(self, report_path: str, report_text: str) -> DeliveryResult:
        """Deliver an alert report. Implemented by subclasses.

        Implementations must not raise on an ordinary delivery failure; they
        should report it through ``DeliveryResult(ok=False, ...)`` so a single
        failing channel never aborts a batch of deliveries.
        """
        raise NotImplementedError("AlertDelivery subclasses must implement deliver()")
