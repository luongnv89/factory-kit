#!/usr/bin/env python3
"""Diagnostics view layer + command reference (issue #18 / Task 3.4).

Acceptance mapping:

- A2 — the per-work and installation views expose queue age, remote
  observation freshness (explicit ``stale``/``unknown`` labels, never
  silently fresh), heartbeat/reconciliation bookkeeping, notification
  failures and the existing state/blocker/revision/link/limit fields;
  unmeasured values stay ``None`` (``unknown``), never zero.
- A3 — every button action has a documented text-command equivalent in
  ``COMMAND_REFERENCE`` with exact syntax, target scope, mutability and
  effect; ambiguous targets are documented as rejected, not executed;
  the text renderers emit plain words and links only — no color or
  emoji anywhere.
"""

import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.control.commands import (  # noqa: E402
    COMMAND_REFERENCE, ACTIONS, command_reference, reference_text)
from factory_kit.diagnostics import views  # noqa: E402
from factory_kit.durable.store import IntakeStore, work_key_for  # noqa: E402

NOW = 1_800_000_000.0
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿]")


def _iso(epoch):
    return (datetime.fromtimestamp(epoch, timezone.utc)
            .isoformat(timespec="milliseconds"))


class ViewsFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = IntakeStore(Path(self.tmp.name) / "i.db")
        self.work_key = work_key_for("gh:R_1", 11, 1)
        self.store.insert_work(
            self.work_key, "gh:R_1", "R_1", 11, 1, "task-11",
            "pending", "fp", "rev-11", "{}", "cfgd", "pold")


# ---------------------------------------------------------------------------
# A2 — per-work status view


class TestStatusView(ViewsFixture):

    def test_unknown_fields_stay_unknown(self):
        """No queue row, no watermark, no notifications — every extra
        field is explicitly ``None``/unknown, never zero or invented."""
        view = views.status_view(self.store, self.work_key, now=NOW)
        self.assertIsNone(view["queue_age_seconds"])
        self.assertIsNone(view["queue_state"])
        self.assertIsNone(view["remote"])
        self.assertEqual(view["notifications"]["pending"], 0)
        self.assertIsNone(view["notifications"]["last_failure_reason"])
        # The base projection fields are carried through.
        self.assertEqual(view["work_key"], self.work_key)
        self.assertIn("status", view)
        self.assertIn("blocker", view)

    def test_queue_age_from_durable_stamp(self):
        """Queue age is measured from the durable ``enqueued_at``
        stamp — a re-upsert cannot reset an operator-visible wait."""
        self.store.enqueue_work(self.work_key, 1, state="queued",
                                reason="lane-occupied",
                                enqueued_at=_iso(NOW - 300))
        # A reconcile rewrite must keep the original stamp.
        self.store.enqueue_work(self.work_key, 1, state="queued",
                                reason="lane-occupied")
        view = views.status_view(self.store, self.work_key, now=NOW)
        self.assertEqual(view["queue_state"], "queued")
        self.assertAlmostEqual(view["queue_age_seconds"], 300, delta=2)

    def test_remote_observation_labels(self):
        """The durable watermark drives explicit freshness words:
        ``stale`` stays labelled, never inferred fresh."""
        self.store.update_reconcile_watermark(
            last_attempt_epoch=NOW - 900, last_success_epoch=NOW - 1200,
            consecutive_failures=3, remote_state="stale",
            stale_since_epoch=NOW - 600)
        view = views.status_view(self.store, self.work_key, now=NOW)
        self.assertEqual(view["remote"]["remote_state"], "stale")
        self.assertTrue(view["remote"]["stale"])
        self.assertEqual(view["remote"]["consecutive_failures"], 3)
        self.assertEqual(view["remote"]["last_attempt_epoch"],
                         NOW - 900)

    def test_notification_failures_visible(self):
        """A terminal outbox failure surfaces in the status view — the
        local notification_failed record is never hidden (A1/A6)."""
        from factory_kit.notifications.outbox import NotificationService
        def dead(d, t):
            raise RuntimeError("dead")
        clock = [NOW]
        svc = NotificationService(self.store, dead, now=lambda: clock[0])
        out = svc.notify("alert", self.work_key, "high",
                         destination_ref="telegram:-100",
                         work_key=self.work_key)
        for _ in range(3):
            clock[0] += 60
            svc.flush()
        view = views.status_view(self.store, self.work_key, now=NOW)
        self.assertEqual(view["notifications"]["failed"], 1)
        self.assertEqual(view["notifications"]["last_failure_reason"],
                         "RuntimeError")

    def test_render_status_text_plain_accessible(self):
        """The renderer emits plain words and explicit labels — no
        emoji, no colour codes — and marks unobserved remote state as
        unknown."""
        self.store.enqueue_work(self.work_key, 2, state="queued",
                                reason="lane-occupied",
                                enqueued_at=_iso(NOW - 60))
        text = views.render_status_text(
            views.status_view(self.store, self.work_key, now=NOW))
        self.assertIsNone(EMOJI.search(text))
        self.assertNotIn("\x1b", text)
        self.assertIn("Queue:", text)
        self.assertIn("queued", text)
        self.assertIn("unknown", text)
        self.assertIn("Notifications:", text)

    def test_render_status_text_stale_label(self):
        self.store.update_reconcile_watermark(
            last_attempt_epoch=NOW - 900, remote_state="stale",
            stale_since_epoch=NOW - 600, consecutive_failures=2)
        text = views.render_status_text(
            views.status_view(self.store, self.work_key, now=NOW))
        self.assertIn("STALE", text)
        self.assertIn("Remote observation:", text)


# ---------------------------------------------------------------------------
# A2 — operations view


class TestOperationsView(ViewsFixture):

    def test_operations_view_counts(self):
        """The installation view carries work counts, rates, effort
        minutes, notification tallies and the remote block."""
        view = views.operations_view(self.store, now=NOW)
        for key in ("work", "outcomes", "rates", "installation",
                    "effort", "usage", "notification_outbox",
                    "remote"):
            self.assertIn(key, view)
        self.assertIsNone(view["remote"])
        self.assertEqual(view["notification_outbox"]["pending"], 0)

    def test_render_operations_text_plain(self):
        self.store.enqueue_work(self.work_key, 1, state="queued",
                                enqueued_at=_iso(NOW - 30))
        text = views.render_operations_text(
            views.operations_view(self.store, now=NOW))
        self.assertIsNone(EMOJI.search(text))
        self.assertIn("OPERATIONS SUMMARY", text)
        self.assertIn("Installation:", text)
        self.assertIn("unknown", text)
        self.assertIn("Remote observation:", text)


# ---------------------------------------------------------------------------
# A3 — command reference


class TestCommandReference(unittest.TestCase):

    def test_every_action_documented(self):
        """All seven button actions have a text-command equivalent —
        the reference and the parser's action set cannot drift."""
        self.assertEqual(
            sorted(e["action"] for e in COMMAND_REFERENCE),
            sorted(ACTIONS))
        for entry in COMMAND_REFERENCE:
            self.assertIn(entry["action"], entry["command"])
            self.assertTrue(entry["scope"])
            self.assertIsInstance(entry["mutates"], bool)
            self.assertTrue(entry["effect"])

    def test_reference_lookup(self):
        entries = {e["action"]: e for e in command_reference()}
        self.assertIn("<generation>", entries["pause"]["command"])
        self.assertTrue(entries["pause"]["mutates"])
        self.assertFalse(entries["status"]["mutates"])

    def test_reference_text_explains_ambiguity(self):
        text = reference_text()
        self.assertIsNone(EMOJI.search(text))
        # Every action's syntax appears.
        for entry in COMMAND_REFERENCE:
            self.assertIn(entry["command"], text)
        # The ambiguity rule is stated verbatim.
        self.assertIn("ambiguous", text.lower())
        self.assertIn("nothing executes on a guess", text.lower())

    def test_syntax_requires_explicit_targets(self):
        """Mutation commands bind an explicit (repo, issue, generation)
        triple — nothing acts on a guessed target."""
        commands = {e["action"]: e["command"]
                    for e in COMMAND_REFERENCE}
        for action in ("pause", "resume", "cancel", "approve",
                       "reject"):
            syntax = commands[action]
            self.assertIn("<repo>", syntax)
            self.assertIn("<issue>", syntax)
            self.assertIn("<generation>", syntax)


if __name__ == "__main__":
    unittest.main()
