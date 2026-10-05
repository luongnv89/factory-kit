#!/usr/bin/env python3
"""Event schema + append-only ledger (issue #14 / Task 2.9, PRD §7.1).

Acceptance mapping:

- A2 — typed events persist attempt boundaries, evidence checks,
  restricted-actor control records, recovery outcomes and
  ``operator_effort_recorded`` with the required identity/runtime/
  model/verdict/observation fields; a missing required field is a
  defect (``IntakeStoreError``), and a missing *effort* value persists
  as honest unknown.
- A3 — measured usage keeps token/provider/unit attribution, billed
  amount/currency and quota in separate fields, and the four wait
  classes as separate durations; absent values stay ``None`` — never
  zero-filled, never an invented currency estimate.
- A1/A7 — corrections append superseding rows/events without
  rewriting history; the append-only trail survives a store reopen.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.events import schema  # noqa: E402


class LedgerFixture(unittest.TestCase):
    """A durable store with one work row to hang events on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_path = Path(self.tmp.name) / "intake.db"
        self.store = durable.IntakeStore(self.store_path)
        self.work_key = durable.work_key_for("gh:R_1", 42, 1)
        self.store.insert_work(
            self.work_key, "gh:R_1", "R_1", 42, 1, "task-1",
            "pending", "fp", "rev-1", "{}", "cfgd", "pold")

    def reopen(self):
        self.store.close()
        self.store = durable.IntakeStore(self.store_path)


# ---------------------------------------------------------------------------
# A2 — the typed schema contract
# ---------------------------------------------------------------------------

class TestEventSchema(unittest.TestCase):

    def test_unknown_kind_rejected(self):
        check = schema.validate_event("made_up_kind", {})
        self.assertFalse(check["ok"])
        self.assertEqual(check["reason"], "unknown-event-kind")

    def test_missing_required_fields_reported(self):
        check = schema.validate_event(
            "attempt_started", {"attempt_id": "a-1", "role": "review"})
        self.assertFalse(check["ok"])
        self.assertEqual(check["reason"], "missing-fields")
        for field in ("task_id", "generation"):
            self.assertIn(field, check["missing"])

    def test_nullable_fields_accept_unknown(self):
        # runtime/model unreported → null or absent both spell
        # "unknown"; the record keeps the honest gap either way.
        props = {"task_id": "t", "attempt_id": "a", "generation": 1,
                 "role": "implementation", "runtime": None,
                 "model": None}
        self.assertTrue(
            schema.validate_event("attempt_started", props)["ok"])
        del props["runtime"], props["model"]
        self.assertTrue(
            schema.validate_event("attempt_started", props)["ok"])

    def test_required_any_alternatives(self):
        scope_only = schema.validate_event(
            "recovery_completed", {"source": "github-poll",
                                   "scope": "pass"})
        action_only = schema.validate_event(
            "recovery_completed", {"source": "restart",
                                   "action": "resumed"})
        neither = schema.validate_event(
            "recovery_completed", {"source": "restart"})
        self.assertTrue(scope_only["ok"])
        self.assertTrue(action_only["ok"])
        self.assertFalse(neither["ok"])

    def test_every_emitted_kind_satisfies_its_schema(self):
        """Producers' real events pass the contract — the §7.1 trail
        already emitted by intake/execution/control/recovery."""
        cases = [
            ("work_accepted", {"delivery_id": "d1",
                               "work_key": "w", "config_digest": "c"}),
            ("work_rejected", {"delivery_id": "d2",
                               "reason": "bad-signature"}),
            ("attempt_started",
             {"task_id": "t", "attempt_id": "a", "generation": 1,
              "role": "implementation", "runtime": "hermes",
              "model": "gpt-6", "session_id": "s"}),
            ("attempt_finished",
             {"task_id": "t", "attempt_id": "a", "generation": 1,
              "role": "review", "duration_s": 12.0,
              "verdict": "approved", "outcome": "completed",
              "usage": {"tokens": {"count": 10}}}),
            ("evidence_checked",
             {"evidence_id": "ev-1", "status": "verified",
              "pr": "7", "head_sha": "abc", "observed_at": "t"}),
            ("control_recorded",
             {"command_id": "c1", "actor_ref": "telegram:1",
              "chat_ref": "telegram:-2", "action": "pause",
              "outcome": "accepted", "recorded_at": "t"}),
            ("recovery_completed",
             {"task_id": "t", "attempt_id": "a", "source": "restart",
              "action": "resumed", "reused_identity": "task-1"}),
            ("operator_effort_recorded",
             {"effort_id": "e1", "work_key": "w",
              "actor_ref": "telegram:1", "active_minutes": 30,
              "category": "approval", "source": "operator",
              "supersedes": None, "recorded_at": "t"}),
            ("participant_agreement_recorded",
             {"scope": "pilot-export", "actor_ref": "telegram:1",
              "recorded_at": "t"}),
        ]
        for kind, props in cases:
            with self.subTest(kind=kind):
                self.assertTrue(
                    schema.validate_event(kind, props)["ok"], kind)


class TestTypedEmit(LedgerFixture):

    def test_typed_event_persists(self):
        out = self.store.record_typed_event(
            "work_accepted", work_key=self.work_key,
            delivery_id="d-1", config_digest="cfgd")
        self.assertEqual(out["outcome"], "recorded")
        rows = self.store.event_rows("work_accepted")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seq"], out["seq"])
        self.assertEqual(rows[0]["config_digest"], "cfgd")

    def test_typed_event_missing_field_is_defect(self):
        with self.assertRaises(durable.IntakeStoreError):
            self.store.record_typed_event(
                "evidence_checked", work_key=self.work_key)
        self.assertEqual(
            self.store.event_rows("evidence_checked"), [])

    def test_unknown_kind_is_defect(self):
        with self.assertRaises(durable.IntakeStoreError):
            self.store.record_typed_event("telemetry_ping")

    def test_corrections_supersede_without_rewrite(self):
        """A1/A7 — a correction appends a row pointing at the prior
        seq; the original event is kept verbatim."""
        first = self.store.record_typed_event(
            "evidence_checked", work_key=self.work_key,
            properties={"evidence_id": "ev-1", "status": "verified",
                        "head_sha": "abc123", "observed_at": "t1"})
        second = self.store.record_typed_event(
            "evidence_checked", work_key=self.work_key,
            properties={"evidence_id": "ev-2", "status": "stale",
                        "head_sha": "abc123", "observed_at": "t2",
                        "reason": "base-moved"},
            supersedes=first["seq"])
        rows = self.store.event_rows("evidence_checked")
        self.assertEqual(len(rows), 2)          # history preserved
        self.assertTrue(
            self.store.event_superseded(first["seq"]))
        current = self.store.current_events("evidence_checked")
        self.assertEqual([r["seq"] for r in current],
                         [second["seq"]])
        self.assertEqual(rows[0]["detail"],  # original untouched
                         json.dumps({"evidence_id": "ev-1",
                                     "status": "verified",
                                     "head_sha": "abc123",
                                     "observed_at": "t1"},
                                    sort_keys=True))


class TestControlRecorded(LedgerFixture):
    """A2 — restricted-actor control records emit the §7.1 event."""

    def test_record_control_emits_event(self):
        with self.store.transact() as tx:
            tx.record_control(
                "cmd-1", received_at="2026-10-05T00:00:00.000Z",
                committed_at="2026-10-05T00:00:00.010Z",
                actor_ref="telegram:123", chat_ref="telegram:-99",
                action="pause", work_key=self.work_key,
                outcome="accepted", seq=tx.next_seq())
        rows = self.store.event_rows("control_recorded")
        self.assertEqual(len(rows), 1)
        detail = json.loads(rows[0]["detail"])
        self.assertEqual(detail["command_id"], "cmd-1")
        self.assertEqual(detail["actor_ref"], "telegram:123")
        self.assertEqual(detail["action"], "pause")
        self.assertEqual(detail["outcome"], "accepted")
        self.assertTrue(schema.validate_event(
            "control_recorded", detail)["ok"])
        controls = self.store.control_rows(self.work_key)
        self.assertEqual(len(controls), 1)


# ---------------------------------------------------------------------------
# A2 — operator effort
# ---------------------------------------------------------------------------

class TestOperatorEffort(LedgerFixture):

    def test_supplied_minutes_and_category_retain_attribution(self):
        out = self.store.record_operator_effort(
            self.work_key, actor_ref="telegram:123",
            active_minutes=45, category="approval")
        self.assertEqual(out["outcome"], "recorded")
        rows = self.store.effort_rows(self.work_key)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["active_minutes"], 45.0)
        self.assertEqual(rows[0]["category"], "approval")
        self.assertEqual(rows[0]["actor_ref"], "telegram:123")
        events = self.store.event_rows("operator_effort_recorded")
        self.assertEqual(len(events), 1)
        detail = json.loads(events[0]["detail"])
        self.assertEqual(detail["effort_id"], out["effort_id"])
        self.assertTrue(schema.validate_event(
            "operator_effort_recorded", detail)["ok"])

    def test_missing_effort_stays_unknown(self):
        self.store.record_operator_effort(
            self.work_key, actor_ref="telegram:123")
        row = self.store.effort_rows(self.work_key)[0]
        self.assertIsNone(row["active_minutes"])
        self.assertIsNone(row["category"])
        measured, unknown = self.store.work_effort_minutes(
            self.work_key)
        self.assertEqual(measured, 0)
        self.assertEqual(unknown, 1)

    def test_category_is_fixed_vocabulary(self):
        with self.assertRaises(durable.IntakeStoreError):
            self.store.record_operator_effort(
                self.work_key, actor_ref="telegram:123",
                category="spent ages debugging the login page")
        self.assertEqual(self.store.effort_rows(), [])

    def test_negative_minutes_rejected(self):
        with self.assertRaises(durable.IntakeStoreError):
            self.store.record_operator_effort(
                self.work_key, actor_ref="telegram:123",
                active_minutes=-5)

    def test_correction_supersedes(self):
        first = self.store.record_operator_effort(
            self.work_key, actor_ref="telegram:123",
            active_minutes=30, category="approval")
        second = self.store.record_operator_effort(
            self.work_key, actor_ref="telegram:123",
            active_minutes=50, category="approval",
            supersedes=first["effort_id"])
        rows = self.store.effort_rows(self.work_key)
        self.assertEqual(len(rows), 2)          # history preserved
        self.assertEqual(rows[1]["supersedes"], first["effort_id"])
        measured, unknown = self.store.work_effort_minutes(
            self.work_key)
        self.assertEqual(measured, 50.0)         # corrected value
        self.assertEqual(unknown, 0)
        events = self.store.event_rows("operator_effort_recorded")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["supersedes"], events[0]["seq"])
        current = self.store.current_events("operator_effort_recorded")
        self.assertEqual([r["seq"] for r in current],
                         [events[1]["seq"]])

    def test_supersedes_unknown_effort_rejected(self):
        with self.assertRaises(durable.IntakeStoreError):
            self.store.record_operator_effort(
                self.work_key, actor_ref="telegram:123",
                supersedes="eff-nonexistent")


# ---------------------------------------------------------------------------
# A3 — usage normalization + separate fields
# ---------------------------------------------------------------------------

class TestUsageShape(unittest.TestCase):

    def test_none_stays_none(self):
        self.assertIsNone(schema.normalize_usage(None))

    def test_separate_token_billed_quota_fields(self):
        usage = schema.normalize_usage({
            "tokens": [{"count": 1200, "provider": "openai",
                        "unit": "tokens"},
                       {"count": 40, "provider": "openai",
                        "unit": "cached_tokens"}],
            "billed": {"amount": 0.031, "currency": "USD"},
            "quota": {"kind": "subscription", "used": 12,
                      "limit": 100, "remaining": 88},
            "durations": {"queue_seconds": 3.0,
                          "model_execution_seconds": 61.0,
                          "ci_wait_seconds": 120.0,
                          "human_wait_seconds": 900.0}})
        self.assertEqual(usage["tokens"][0]["provider"], "openai")
        self.assertEqual(usage["tokens"][1]["unit"], "cached_tokens")
        self.assertEqual(usage["billed"],
                         {"amount": 0.031, "currency": "USD"})
        self.assertEqual(usage["quota"]["remaining"], 88)
        for field in schema.USAGE_DURATION_FIELDS:
            self.assertIsInstance(
                usage["durations"][field], float)

    def test_absent_values_are_unknown_not_zero(self):
        usage = schema.normalize_usage({"tokens": 500})
        self.assertEqual(usage["tokens"],
                         [{"count": 500, "provider": None,
                           "unit": "tokens"}])
        self.assertIsNone(usage["billed"])       # no invented currency
        self.assertIsNone(usage["quota"])
        for field in schema.USAGE_DURATION_FIELDS:
            self.assertIsNone(usage["durations"][field])

    def test_billed_without_currency_keeps_honest_gap(self):
        usage = schema.normalize_usage({"billed": {"amount": 0.5}})
        self.assertEqual(usage["billed"],
                         {"amount": 0.5, "currency": None})


class TestAttemptUsagePersistence(LedgerFixture):
    """A3 — the finish path persists the normalized shape."""

    def _attempt(self, usage):
        self.store.set_work_state(self.work_key, "active")
        self.store.begin_execution_attempt(
            self.work_key, attempt_id="att-1", role="implementation",
            task_id="task-1", generation=1, session_id="sess-1",
            runtime="hermes", model="gpt-6-luna", skills="{}",
            config_digest="cfgd", policy_digest="pold",
            workspace="/tmp/ws",
            limits=json.dumps({"implementation_attempts": 2}),
            now_epoch=1000.0)
        self.store.finish_execution_attempt(
            "att-1", verdict="completed", outcome="completed",
            active_seconds=61.0, usage=usage, duration_s=64.0)

    def test_normalized_usage_row_and_event(self):
        self._attempt({"tokens": {"count": 900, "provider": "openai"},
                       "billed": {"amount": 0.02, "currency": "USD"},
                       "queue_seconds": 2.5,
                       "model_execution_seconds": 55.0})
        rows = [r for r in self.store._rows("attempt_usage")
                if r["attempt_id"] == "att-1"]
        self.assertEqual(len(rows), 1)
        usage = json.loads(rows[0]["usage"])
        self.assertEqual(usage["tokens"][0]["count"], 900)
        self.assertEqual(usage["billed"]["currency"], "USD")
        self.assertEqual(
            usage["durations"]["model_execution_seconds"], 55.0)
        self.assertIsNone(usage["durations"]["ci_wait_seconds"])
        event = self.store.event_rows("attempt_finished")[0]
        detail = json.loads(event["detail"])
        self.assertEqual(detail["verdict"], "completed")
        self.assertEqual(detail["usage"]["tokens"][0]["count"], 900)

    def test_unknown_usage_records_unknown(self):
        self._attempt(None)
        row = [r for r in self.store._rows("attempt_usage")
               if r["attempt_id"] == "att-1"][0]
        self.assertIsNone(row["usage"])
        rec = [r for r in self.store.attempt_record_rows(self.work_key)
               if r["attempt_id"] == "att-1"][0]
        self.assertEqual(rec["usage_state"], "unknown")


# ---------------------------------------------------------------------------
# A7 — durability across restart + fixture identity
# ---------------------------------------------------------------------------

class TestRestartSurvival(LedgerFixture):

    def test_events_effort_and_agreements_survive_reopen(self):
        self.store.record_typed_event(
            "work_accepted", work_key=self.work_key,
            delivery_id="d-9")
        self.store.record_operator_effort(
            self.work_key, actor_ref="telegram:123",
            active_minutes=15, category="baseline")
        self.store.record_participant_agreement(
            "pilot-export", actor_ref="telegram:123")
        self.reopen()
        self.assertEqual(
            len(self.store.event_rows("work_accepted")), 1)
        self.assertEqual(
            len(self.store.effort_rows(self.work_key)), 1)
        self.assertIsNotNone(
            self.store.participant_agreement("pilot-export"))
        # …and the supersede chain still resolves after reopen.
        first = self.store.effort_rows(self.work_key)[0]
        self.store.record_operator_effort(
            self.work_key, actor_ref="telegram:123",
            active_minutes=20, category="baseline",
            supersedes=first["effort_id"])
        self.reopen()
        rows = self.store.effort_rows(self.work_key)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["supersedes"], first["effort_id"])

    def test_pre_supersedes_database_is_migrated(self):
        """An events table created before the column existed is
        upgraded in place — old rows keep supersedes=NULL."""
        import sqlite3
        legacy = Path(self.tmp.name) / "legacy.db"
        db = sqlite3.connect(str(legacy))
        db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY"
                   " AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT"
                   " NULL, delivery_id TEXT, work_key TEXT, reason"
                   " TEXT, config_digest TEXT, detail TEXT)")
        db.execute("INSERT INTO events(ts,kind) VALUES('t0','legacy')")
        db.commit()
        db.close()
        store = durable.IntakeStore(legacy)
        try:
            rows = store.event_rows("legacy")
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["supersedes"])
            out = store.record_typed_event(
                "work_parked", reason="test", supersedes=rows[0]["seq"])
            self.assertTrue(store.event_superseded(rows[0]["seq"]))
            self.assertEqual(len(store.current_events()), 1)
            self.assertEqual(store.current_events()[0]["seq"],
                             out["seq"])
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
