#!/usr/bin/env python3
"""Durable notification outbox — PRD §7.1/§7.3 (issue #18 / Task 3.4,
F07 A1/A4/A6).

One durable row per ``(kind, identity, severity)`` signal, committed
*before* the transport send — the same commit-before-effect discipline
the publication intents use. On top of it:

- **Dedup by task/event identity and severity transition (A1):** a
  repeated signal returns ``deduplicated`` and never produces a second
  send; a severity *transition* is a different row by construction, so
  escalation stays visible.
- **Bounded retries (A1):** a failed send schedules up to
  ``MAX_RETRIES`` further attempts, each ``RETRY_SPACING_S`` apart, so
  the retry window spans at least ``MIN_RETRY_WINDOW_S`` (3 retries at
  +20s/+40s/+60s). Exhaustion settles the row ``failed`` — the pending
  record is *retained* with its terminal outcome, never deleted — and a
  ``notification_failed`` event plus a deduplicated local alert keep the
  failure visible without touching committed task/control state.
- **Authorized destination (A1/A4):** the destination is the restricted
  reference form ``telegram:<digits>``; when the service carries the
  repo configs, a work-scoped notification is additionally checked
  against that repo's ``authorization.telegram_chats`` allowlist — an
  unlisted destination is denied *before* any send.
- **Redaction (A4):** bodies pass through the §7.1 secret boundary
  (:data:`factory_kit.events.schema.SECRET_VALUE_RE`) before they are
  persisted or sent — a credential-shaped span can never leave through
  the outbox.
- **Honest delivery (A6):** ``delivered`` is recorded only when the
  transport call actually returned; an offline host accumulates
  ``pending``/``failed`` rows and never claims remote delivery — a
  separately configured external monitor is the only thing that could
  carry an alert off a dead host, and this module is not it. No
  centralized telemetry is emitted anywhere on this path.

The transport is injected: a callable ``(destination_ref, text) ->
None`` (or an object exposing ``.send(destination_ref, text)``); any
exception or ``False`` return means the send failed. ``now`` is an
injectable epoch clock so outage/restart windows test deterministically.
"""

from __future__ import annotations

import re
import time
import uuid

from factory_kit.events import schema as _schema

__all__ = [
    "MAX_RETRIES",
    "RETRY_SPACING_S",
    "MIN_RETRY_WINDOW_S",
    "DESTINATION_REF_RE",
    "NotificationService",
]

#: §7.3 — three bounded retries after the initial attempt, spaced so the
#: retry window covers at least 60 seconds end to end.
MAX_RETRIES = 3
RETRY_SPACING_S = 20.0
MIN_RETRY_WINDOW_S = 60.0

#: The restricted destination reference (CFG02): numeric Telegram chat
#: IDs only — a username or free-form address can never be authorized.
DESTINATION_REF_RE = re.compile(r"^telegram:-?\d+$")


def _redact_text(text):
    """Secret-scan a notification body (A4): credential-shaped spans are
    rewritten to ``<redacted>`` — the canary can never ride a Telegram
    message or sit in the durable body."""
    if not isinstance(text, str):
        return text
    return _schema.SECRET_VALUE_RE.sub("<redacted>", text)


class NotificationService:
    """Durable outbox + bounded retry driver over the intake store.

    - ``store`` — :class:`factory_kit.durable.store.IntakeStore`.
    - ``transport`` — the injected send; ``callable(dest, text)`` or an
      object with ``.send(dest, text)``. ``None`` means "no transport
      configured": notifications still persist and are retried by
      ``flush`` — they simply always fail until one is wired.
    - ``configs`` — optional ``{repo_id: effective}`` map; when present,
      a work-scoped notification's destination must sit on that repo's
      ``authorization.telegram_chats`` allowlist.
    - ``now`` — injectable epoch clock.
    """

    def __init__(self, store, transport=None, *, configs=None,
                 now=None):
        self.store = store
        self._transport = transport
        self._configs = dict(configs or {})
        self._now = now or time.time

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    def notify(self, kind, identity, severity, *, destination_ref,
               body=None, work_key=None, notification_id=None):
        """Persist + attempt one operator notification.

        Returns ``{"outcome": ..., "notification_id": ...}`` where
        outcome is ``delivered``, ``pending`` (send failed; retry
        scheduled), ``deduplicated`` (signal already recorded) or
        ``denied`` (malformed/unauthorized destination — nothing is
        persisted or sent).
        """
        destination_ref = str(destination_ref or "").strip()
        if not DESTINATION_REF_RE.match(destination_ref):
            return {"outcome": "denied", "reason": "bad-destination-ref",
                    "notification_id": None}
        auth = self._authorize_destination(destination_ref, work_key)
        if auth is not None:
            return {"outcome": "denied", "reason": auth,
                    "notification_id": None}
        notification_id = notification_id or \
            f"ntf-{uuid.uuid4().hex[:12]}"
        body = _redact_text(body)
        now_epoch = float(self._now())
        with self.store.transact() as tx:
            enq = tx.enqueue_notification(
                notification_id, kind=kind, identity=identity,
                severity=severity, destination_ref=destination_ref,
                body=body, work_key=work_key, seq=tx.next_seq())
        if enq["deduplicated"]:
            row = enq.get("row") or {}
            return {"outcome": "deduplicated",
                    "notification_id": row.get("notification_id",
                                               notification_id),
                    "state": row.get("state")}
        return self._attempt(self.store.get_notification(notification_id),
                             now_epoch)

    def flush(self, *, now=None):
        """Drive every due pending notification — the restart-safe drain.

        Called on a scheduler tick *and* once after process start: the
        pending rows committed before a crash/outage resume their bounded
        retry schedule from durable state, never from memory (A1).
        Returns the per-row outcomes."""
        now_epoch = float(self._now() if now is None else now)
        out = []
        for row in self.store.due_notifications(now_epoch):
            out.append(self._attempt(row, now_epoch))
        return out

    def rows(self, work_key=None, state=None):
        return self.store.notification_rows(work_key=work_key,
                                            state=state)

    def counts(self, work_key=None):
        return self.store.notification_counts(work_key)

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _authorize_destination(self, destination_ref, work_key):
        """Allowlist check (A1 authorized destination): with configs and
        a resolvable work row, the chat must be on the repo's
        ``authorization.telegram_chats`` list. Without either, the
        restricted *reference form* is all that is enforceable — the
        transport-facing shape is still validated."""
        if not self._configs or not work_key:
            return None
        work = self.store.get_work(work_key)
        if work is None:
            return "unknown-work"
        effective = self._configs.get(work["repo_id"])
        if effective is None:
            return "unregistered-authority"
        allowed = (effective.get("authorization") or {}).get(
            "telegram_chats") or []
        try:
            chat = int(destination_ref.split(":", 1)[1])
        except (ValueError, IndexError):
            return "bad-destination-ref"
        if chat not in allowed:
            return "unauthorized-destination"
        return None

    def _send(self, destination_ref, text):
        """The injected transport boundary. Returns ``(ok, error)`` —
        never raises into the caller's commit path."""
        transport = self._transport
        try:
            if callable(transport):
                result = transport(destination_ref, text)
            elif transport is not None and \
                    hasattr(transport, "send"):
                result = transport.send(destination_ref, text)
            else:
                return False, "no-transport"
            return (True, None) if result is not False else \
                (False, "transport-refused")
        except Exception as exc:  # noqa: BLE001 — the boundary must not
            # propagate: a failed send loses the push, never the record.
            return False, type(exc).__name__

    def _attempt(self, row, now_epoch):
        """One send attempt against a pending row + outcome bookkeeping.

        Terminal failure needs both bounds: ``MAX_RETRIES`` retries used
        *and* ``MIN_RETRY_WINDOW_S`` elapsed since the first attempt —
        an exhausted budget inside a shorter window reschedules the last
        retry at the window edge instead of settling early (§7.3's
        "over at least 60 seconds").
        """
        notification_id = row["notification_id"]
        ok, error = self._send(row["destination_ref"], row.get("body"))
        if ok:
            with self.store.transact() as tx:
                tx.notification_attempt(notification_id,
                                        now_epoch=now_epoch, ok=True)
                tx.record_typed_event(
                    "notification_delivered",
                    properties={
                        "notification_id": notification_id,
                        "destination_ref": row["destination_ref"],
                        "retries": row["attempts"]},
                    work_key=row.get("work_key"))
            return {"outcome": "delivered",
                    "notification_id": notification_id,
                    "attempts": row["attempts"] + 1}

        attempts_used = row["attempts"] + 1      # incl. this failure
        retries_used = max(0, attempts_used - 1)
        first = row["first_attempt_epoch"] or now_epoch
        retries_exhausted = retries_used >= MAX_RETRIES
        window_elapsed = (now_epoch - first) >= MIN_RETRY_WINDOW_S
        if retries_exhausted and window_elapsed:
            with self.store.transact() as tx:
                tx.notification_attempt(notification_id,
                                        now_epoch=now_epoch, ok=False,
                                        error=error,
                                        terminal="retries-exhausted")
                tx.record_typed_event(
                    "notification_failed",
                    properties={
                        "notification_id": notification_id,
                        "destination_ref": row["destination_ref"],
                        "retries": retries_used,
                        "reason": error or "transport-failed"},
                    work_key=row.get("work_key"))
            # Local visibility that survives the outage (§7.3/A6): a
            # deduplicated alert row — committed state is untouched.
            self.store.emit_alert(
                "notification-failed", notification_id, "medium",
                f"{row['kind']} to {row['destination_ref']}: {error}")
            return {"outcome": "failed",
                    "notification_id": notification_id,
                    "reason": "retries-exhausted",
                    "attempts": attempts_used}

        # Next retry: evenly spaced, and never before the §7.3 window
        # has elapsed for the final attempt.
        next_epoch = now_epoch + RETRY_SPACING_S
        window_edge = first + MIN_RETRY_WINDOW_S
        if retries_used + 1 >= MAX_RETRIES and next_epoch < window_edge:
            next_epoch = window_edge
        with self.store.transact() as tx:
            tx.notification_attempt(notification_id,
                                    now_epoch=now_epoch, ok=False,
                                    error=error,
                                    next_attempt_epoch=next_epoch)
        return {"outcome": "pending",
                "notification_id": notification_id,
                "attempts": attempts_used,
                "next_attempt_epoch": next_epoch,
                "reason": error}
