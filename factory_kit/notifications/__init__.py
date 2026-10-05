"""factory_kit.notifications — durable notification outbox (§7.1, §7.3).

PRD §7.1 ``notification_delivered``/``notification_failed``, §7.3
"Telegram delivery failure — 3 bounded retries over at least 60
seconds" (issue #18 / Task 3.4, F07 A1/A6).

The operator channel is a best-effort transport over a *durable* outbox:
every signal first commits a pending ``notification_outbox`` row, then
the transport is attempted. A crash between commit and send — or a
Telegram outage mid-flight — loses nothing: the pending row survives,
the injected clock schedules bounded retries, and the terminal
``failed`` outcome stays locally visible (alert + event) while the
committed task/control state it reported is never rolled back.

- :mod:`factory_kit.notifications.outbox` — :class:`NotificationService`
  plus the retry-window constants and the destination-reference rule.
"""

from .outbox import (
    DESTINATION_REF_RE,
    MAX_RETRIES,
    MIN_RETRY_WINDOW_S,
    RETRY_SPACING_S,
    NotificationService,
)

__all__ = [
    "DESTINATION_REF_RE",
    "MAX_RETRIES",
    "MIN_RETRY_WINDOW_S",
    "RETRY_SPACING_S",
    "NotificationService",
]
