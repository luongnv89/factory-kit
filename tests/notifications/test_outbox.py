#!/usr/bin/env python3
"""Durable notification outbox (issue #18 / Task 3.4, PRD §7.1/§7.3).

Acceptance mapping:

- A1 — notification/event IDs, authorized destination reference, retry
  count and terminal outcome persist; dedup is the (kind, identity,
  severity) signal triple so a severity transition still notifies; a
  Telegram failure gets 3 bounded retries over >=60 seconds, the
  pending record is retained and ``notification_failed`` exposes the
  failure locally without rolling back committed task/control state.
- A4 — bodies are secret-scanned before persist and send; the
  destination must be the restricted ``telegram:<id>`` reference and,
  when repo configs are wired, an allowlisted chat.
- A6 — ``delivered`` is only ever the transport's real outcome: an
  offline host accumulates pending/failed rows, never a remote-delivery
  claim; no telemetry leaves this path.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.durable.store import IntakeStore, work_key_for  # noqa: E402
from factory_kit.notifications.outbox import (  # noqa: E402
    MAX_RETRIES,
    MIN_RETRY_WINDOW_S,
    RETRY_SPACING_S,
    NotificationService,
)

#: A credential canary — assembled so the literal in this file never
#: matches the secret pattern itself, while the runtime value does.
CANARY = "ghp_" + "a1" * 20


class OutboxFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "i.db"
        self.store = IntakeStore(self.db_path)
        self.clock = [10000.0]
        self.sent = []
        self.fail_with = [None]
        self.work_key = work_key_for("gh:R_1", 7, 1)
        self.store.insert_work(
            self.work_key, "gh:R_1", "R_1", 7, 1, "task-7",
            "pending", "fp", "rev-7", "{}", "cfgd", "pold")

    def _now(self):
        return self.clock[0]

    def _transport(self, dest, text):
        if self.fail_with[0] is not None:
            raise self.fail_with[0]
        self.sent.append((dest, text))

    def _service(self, store=None, configs=None):
        return NotificationService(store or self.store, self._transport,
                                   configs=configs, now=self._now)


# ---------------------------------------------------------------------------
# A1 — durable record + dedup + severity transition


class TestPersistAndDedup(OutboxFixture):

    def test_successful_delivery_persists_terminal_outcome(self):
        svc = self._service()
        out = svc.notify("recovery-parked", self.work_key, "high",
                         destination_ref="telegram:-100",
                         body="work parked", work_key=self.work_key)
        self.assertEqual(out["outcome"], "delivered")
        row = self.store.get_notification(out["notification_id"])
        self.assertEqual(row["state"], "delivered")
        self.assertEqual(row["terminal_outcome"], "delivered")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["destination_ref"], "telegram:-100")
        self.assertEqual(row["kind"], "recovery-parked")
        self.assertEqual(row["identity"], self.work_key)
        self.assertEqual(row["severity"], "high")
        self.assertEqual(self.sent, [("telegram:-100", "work parked")])
        events = self.store.event_rows("notification_delivered")
        self.assertEqual(len(events), 1)

    def test_duplicate_signal_deduplicates(self):
        svc = self._service()
        first = svc.notify("recovery-parked", self.work_key, "high",
                           destination_ref="telegram:-100",
                           work_key=self.work_key)
        second = svc.notify("recovery-parked", self.work_key, "high",
                            destination_ref="telegram:-100",
                            work_key=self.work_key)
        self.assertEqual(second["outcome"], "deduplicated")
        self.assertEqual(second["notification_id"],
                         first["notification_id"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.store.notification_rows()), 1)

    def test_severity_transition_is_a_new_signal(self):
        svc = self._service()
        svc.notify("budget", self.work_key, "medium",
                   destination_ref="telegram:-100", work_key=self.work_key)
        out = svc.notify("budget", self.work_key, "high",
                         destination_ref="telegram:-100",
                         work_key=self.work_key)
        self.assertEqual(out["outcome"], "delivered")
        self.assertEqual(len(self.store.notification_rows()), 2)
        self.assertEqual(len(self.sent), 2)

    def test_bad_destination_ref_denied_without_send(self):
        svc = self._service()
        out = svc.notify("kind", "id", "high",
                         destination_ref="@someone",
                         work_key=self.work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "bad-destination-ref")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.notification_rows(), [])

    def test_unauthorized_destination_denied(self):
        eff = {"authorization": {"telegram_chats": [-100]}}
        svc = self._service(configs={"R_1": eff})
        out = svc.notify("kind", self.work_key, "high",
                         destination_ref="telegram:-999",
                         work_key=self.work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "unauthorized-destination")
        self.assertEqual(self.sent, [])

    def test_allowlisted_destination_sends(self):
        eff = {"authorization": {"telegram_chats": [-100]}}
        svc = self._service(configs={"R_1": eff})
        out = svc.notify("kind", self.work_key, "high",
                         destination_ref="telegram:-100",
                         work_key=self.work_key)
        self.assertEqual(out["outcome"], "delivered")


# ---------------------------------------------------------------------------
# A1 — bounded retries over >=60s, outage + restart


class TestRetryWindow(OutboxFixture):

    def test_three_bounded_retries_over_at_least_60s(self):
        self.fail_with[0] = RuntimeError("telegram down")
        svc = self._service()
        out = svc.notify("alert", self.work_key, "medium",
                         destination_ref="telegram:-100",
                         work_key=self.work_key)
        self.assertEqual(out["outcome"], "pending")
        nid = out["notification_id"]

        # Retry 1 at +spacing — still pending, record retained.
        self.clock[0] += RETRY_SPACING_S
        r = svc.flush()[0]
        self.assertEqual(r["outcome"], "pending")
        self.assertEqual(r["attempts"], 2)

        # Retry 2 at +2*spacing.
        self.clock[0] += RETRY_SPACING_S
        r = svc.flush()[0]
        self.assertEqual(r["outcome"], "pending")
        self.assertEqual(r["attempts"], 3)

        # Retry 3 lands at >=60s from the first attempt — terminal.
        self.clock[0] += RETRY_SPACING_S
        r = svc.flush()[0]
        self.assertEqual(r["outcome"], "failed")
        self.assertEqual(r["reason"], "retries-exhausted")

        row = self.store.get_notification(nid)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["terminal_outcome"], "retries-exhausted")
        self.assertEqual(row["attempts"], 1 + MAX_RETRIES)
        self.assertGreaterEqual(
            row["last_attempt_epoch"] - row["first_attempt_epoch"],
            MIN_RETRY_WINDOW_S)
        # The pending record is retained (column update, not delete) and
        # the failure is locally visible via the typed event + alert.
        self.assertIsNotNone(row["last_error"])
        self.assertEqual(
            len(self.store.event_rows("notification_failed")), 1)
        self.assertEqual(
            self.store.alert_rows("notification-failed")[0]["severity"],
            "medium")

    def test_committed_work_state_not_rolled_back(self):
        self.store.set_work_state(self.work_key, "completed")
        self.fail_with[0] = RuntimeError("down")
        svc = self._service()
        svc.notify("alert", self.work_key, "high",
                   destination_ref="telegram:-100",
                   work_key=self.work_key)
        self.clock[0] += 600.0
        svc.flush()
        self.assertEqual(self.store.get_work(self.work_key)["state"],
                         "completed")

    def test_outage_then_restart_drains_pending(self):
        """A crash between commit and send loses nothing: a fresh
        service instance over the same store resumes the pending row."""
        self.fail_with[0] = RuntimeError("down")
        svc = self._service()
        out = svc.notify("alert", self.work_key, "high",
                         destination_ref="telegram:-100",
                         work_key=self.work_key)
        nid = out["notification_id"]
        # Restart: new store + service objects, same DB file.
        store2 = IntakeStore(self.db_path)
        self.clock[0] += 30.0
        svc2 = NotificationService(store2, self._transport,
                                   now=self._now)
        pending = store2.notification_rows(state="pending")
        self.assertEqual([r["notification_id"] for r in pending], [nid])
        self.fail_with[0] = None
        results = svc2.flush()
        self.assertEqual(results[0]["outcome"], "delivered")
        self.assertEqual(store2.get_notification(nid)["state"],
                         "delivered")

    def test_recovery_after_failure_then_success(self):
        """Mid-outage recovery delivers on a scheduled retry and clears
        the pending state — attempts count reflects the real sends."""
        self.fail_with[0] = RuntimeError("down")
        svc = self._service()
        out = svc.notify("alert", self.work_key, "high",
                         destination_ref="telegram:-100",
                         work_key=self.work_key)
        self.clock[0] += RETRY_SPACING_S
        self.fail_with[0] = None
        svc.flush()
        row = self.store.get_notification(out["notification_id"])
        self.assertEqual(row["state"], "delivered")
        self.assertEqual(row["attempts"], 2)

    def test_terminal_failure_dedups_repeated_flush(self):
        """Once failed, a signal is never re-sent — a later flush skips
        settled rows entirely."""
        self.fail_with[0] = RuntimeError("down")
        svc = self._service()
        svc.notify("alert", self.work_key, "high",
                   destination_ref="telegram:-100",
                   work_key=self.work_key)
        # Drive every scheduled retry to terminal failure.
        for _ in range(MAX_RETRIES):
            self.clock[0] += RETRY_SPACING_S
            svc.flush()
        row = self.store.notification_rows()[0]
        self.assertEqual(row["state"], "failed")
        # Now transport recovers; flushing again must not re-send.
        self.fail_with[0] = None
        self.assertEqual(svc.flush(), [])
        self.assertEqual(self.sent, [])
        # A repeat of the same signal still dedups against the row.
        again = svc.notify("alert", self.work_key, "high",
                           destination_ref="telegram:-100",
                           work_key=self.work_key)
        self.assertEqual(again["outcome"], "deduplicated")
        self.assertEqual(again["state"], "failed")


# ---------------------------------------------------------------------------
# A4 — redaction on the outbound path


class TestRedaction(OutboxFixture):

    def test_canary_never_reaches_transport_or_store(self):
        svc = self._service()
        out = svc.notify("alert", self.work_key, "high",
                         destination_ref="telegram:-100",
                         body=f"token: {CANARY} — detail",
                         work_key=self.work_key)
        self.assertEqual(out["outcome"], "delivered")
        body = self.sent[0][1]
        self.assertNotIn(CANARY, body)
        self.assertIn("<redacted>", body)
        row = self.store.get_notification(out["notification_id"])
        self.assertNotIn(CANARY, row["body"])


# ---------------------------------------------------------------------------
# A1 — RecoveryService wiring: alert → durable outbox


class TestRecoveryWiring(OutboxFixture):

    def _recovery(self, chats=(-100,), notifier=None):
        from factory_kit.recovery.service import RecoveryService
        return RecoveryService(
            self.store, None,
            {"R_1": {"authorization": {"telegram_chats": list(chats)}}},
            notifier=notifier)

    def test_emitted_alert_enqueues_authorized_destination(self):
        svc = self._service()
        rec = self._recovery(notifier=svc)
        out = rec._notify("recovery-parked", self.work_key, "high",
                          detail="parked", work_key=self.work_key)
        self.assertEqual(out["outcome"], "emitted")
        self.assertEqual(self.sent, [("telegram:-100", "parked")])
        rows = self.store.notification_rows(work_key=self.work_key)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["destination_ref"], "telegram:-100")
        self.assertEqual(rows[0]["state"], "delivered")

    def test_no_configured_destination_enqueues_nothing(self):
        svc = self._service()
        rec = self._recovery(chats=(), notifier=svc)
        out = rec._notify("recovery-parked", self.work_key, "high",
                          detail="parked", work_key=self.work_key)
        # The alert still commits; nothing is sent to an invented
        # destination (A1 — authorized destination reference only).
        self.assertEqual(out["outcome"], "emitted")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.notification_rows(), [])

    def test_outbox_fault_never_rolls_back_the_alert(self):
        class BadNotifier:
            def notify(self, *a, **k):
                raise RuntimeError("outbox down")
        rec = self._recovery(notifier=BadNotifier())
        out = rec._notify("recovery-parked", self.work_key, "high",
                          detail="parked", work_key=self.work_key)
        self.assertEqual(out["outcome"], "emitted")
        self.assertEqual(rec._notify_failures, 1)
        self.assertEqual(
            len(self.store.alert_rows("recovery-parked")), 1)


if __name__ == "__main__":
    unittest.main()
