#!/usr/bin/env python3
"""Typed Telegram control — durable status/pause/resume/cancel/retry
(issue #12 / Task 2.7, PRD §6.4 Control, CFG02).

Each test class maps to an acceptance criterion:

- A1 — an allowlisted numeric actor in the configured chat submitting
  a recognized command gets a durable record (command ID, restricted
  actor/chat references, action, target, receipt + commit times,
  outcome) before the acknowledgement returns.
- A2 — pause during a stage records ``requested``; the next stage
  cannot start until resume; status distinguishes requested / paused /
  resumed and carries task/attempt, blocker, SHA/time, limits,
  committed-effect links and last heartbeat.
- A3 — cancel fences the generation inside the handler's commit before
  termination starts; new effects and late completions are denied;
  workers exit inside the deadline or the work quarantines with
  notification; uncertain termination blocks replacement.
- A4 — fence acknowledgement and process exit are separate persisted
  outcomes; pre-fence effects stay linked; cancel is terminal; retry
  mints a fresh audited generation.
- A5 — forged actor, wrong chat, missing/ambiguous target, duplicate
  command, stale generation and unsupported requests reject without
  broadening permissions or repeating effects; natural-language
  proposals pass the same typed validator.
- A6 — responses use explicit state words, stable identifiers and
  authorized links; every affordance has a documented text command;
  committed control state never depends on transport success.
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.config import registration, schema  # noqa: E402
from factory_kit.control import ControlService  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.execution import (  # noqa: E402
    ExecutionLane, ScriptedWorker, WorkerResult)
from factory_kit.intake import service  # noqa: E402

MANIFEST = ROOT / ".factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"

#: Host facts that satisfy the pre-dispatch gate.
READINGS = {
    "tools": {"git": "/usr/bin/git", "gh": "/x/gh", "hermes": "/x/hermes"},
    "models": {"openai-codex": {"authenticated": True,
                                "available": ["gpt-6-luna"]}},
    "skills": {"issue-resolver": "0.19.0", "issue-pr-review": "0.19.0"},
}

#: The manifest's allowlisted numeric Telegram IDs (CFG02).
USER = 123456789
CHAT = -1001234567890


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


class ControlFixture(unittest.TestCase):
    """Registered repo + intake-accepted work + lane + control service."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.eff = effective()
        self.repo_id = self.eff["identity"]["repo_id"]
        self.clock = [time.time()]
        self.registrations = registration.RegistrationStore(
            Path(self.tmp.name) / "registration.json")
        self.registrations.register(self.eff, readiness=READY,
                                    supported_versions=VERSIONS)
        self.store = durable.IntakeStore(Path(self.tmp.name) / "in.db")
        self.service = service.IntakeService(
            self.store, self.registrations, {self.repo_id: self.eff},
            signing_secret="s", opt_in_label="factory-kit")
        self.worker = ScriptedWorker()
        self.lane = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=self.worker,
            workspace_root=Path(self.tmp.name) / "ws",
            readings=READINGS, now=self._now)
        self.alerts = []
        self.control = ControlService(
            self.store, self.lane, self.registrations,
            {self.repo_id: self.eff}, alert_sink=self.alerts,
            now=self._now)
        self._cid = [0]

    def _now(self):
        return self.clock[0]

    def _accept(self, issue, sender="luongnv89"):
        env = {"delivery_id": f"d-{issue}", "channel": "reconciliation",
               "repo_id": self.repo_id,
               "repository": "luongnv89/money-mind", "issue": issue,
               "action": "reconcile", "sender": sender,
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.service.deliver(env)
        assert ack["outcome"] == "accepted", ack
        return ack["work_key"]

    def _msg(self, action, *, issue=1, generation=1, user=USER,
             chat=CHAT, command_id=None, **fields):
        self._cid[0] += 1
        msg = {"command_id": command_id or f"c-{self._cid[0]:03d}",
               "actor": {"user_id": user},
               "chat": {"chat_id": chat},
               "action": action, "repo_id": self.repo_id,
               "issue": issue, "generation": generation}
        msg.update(fields)
        return msg

    def _records(self, command_id=None):
        rows = self.store.control_rows()
        if command_id is None:
            return rows
        return [r for r in rows if r["command_id"] == command_id]


# ---------------------------------------------------------------------------
# A1 — typed command → durable record before acknowledgement
# ---------------------------------------------------------------------------

class TestA1Records(ControlFixture):

    def test_recognized_commands_persist_then_ack(self):
        self._accept(1)
        for action in ("status", "pause", "resume", "cancel"):
            out = self.control.handle(
                self._msg(action, issue=1))
            self.assertTrue(out["ok"], (action, out))
        # Each command has exactly one durable record carrying the
        # restricted references, action, target and both timestamps.
        records = self._records()
        self.assertEqual(len(records), 4)
        for rec in records:
            self.assertEqual(rec["actor_ref"], f"telegram:{USER}")
            self.assertEqual(rec["chat_ref"], f"telegram:{CHAT}")
            self.assertEqual(rec["repo_id"], self.repo_id)
            self.assertEqual(rec["issue"], 1)
            self.assertTrue(rec["received_at"])
            self.assertTrue(rec["committed_at"])
            self.assertIn(rec["outcome"], ("accepted", "answered"))
        # Committed before ack: the record exists at handle() return —
        # a transport failure could only lose the text, never the row.

    def test_every_record_has_monotonic_seq(self):
        self._accept(1)
        self.control.handle(self._msg("status", issue=1))
        self.control.handle(self._msg("pause", issue=1))
        seqs = [r["seq"] for r in self._records()]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs))


# ---------------------------------------------------------------------------
# A2 — pause boundary, resume, status surface
# ---------------------------------------------------------------------------

class TestA2PauseBoundary(ControlFixture):

    def test_pause_during_stage_blocks_next_until_resume(self):
        work_key = self._accept(2)
        seen = {}

        def impl(ctx):
            # Pause arrives while the implementation stage is live.
            seen["pause"] = self.control.handle(
                self._msg("pause", issue=2))
            return WorkerResult(verdict="completed")

        worker = ScriptedWorker(script={"implementation": impl})
        lane = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=worker,
            workspace_root=Path(self.tmp.name) / "ws2",
            readings=READINGS, now=self._now)
        self.control.lane = lane
        lane.tick()

        # Recorded as requested at receipt…
        self.assertEqual(seen["pause"]["detail"], "pause-requested")
        # …and the lane committed the boundary: paused at the *review*
        # stage — implementation finished, review never started.
        pause = self.store.pause_info(work_key)
        self.assertEqual(pause["pause_state"], "paused")
        self.assertEqual(pause["paused_stage"], "review")
        roles = [r["role"] for r in
                 self.store.attempt_record_rows(work_key)]
        self.assertEqual(roles, ["implementation"])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "pending")

        # Status distinguishes the paused state explicitly.
        status = self.control.handle(self._msg("status", issue=2))
        self.assertIn("PAUSED", status["text"])
        self.assertIn(work_key, status["text"])

        # Resume → the held stage dispatches; the run completes.
        out = self.control.handle(self._msg("resume", issue=2))
        self.assertEqual(out["outcome"], "accepted")
        lane.tick()
        roles = [r["role"] for r in
                 self.store.attempt_record_rows(work_key)]
        self.assertEqual(roles, ["implementation", "review"])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "active")
        self.assertEqual(self.store.pause_info(work_key)
                         ["pause_state"], "resumed")

    def test_pause_on_queued_work_pauses_immediately(self):
        work_key = self._accept(3)
        out = self.control.handle(self._msg("pause", issue=3))
        self.assertEqual(out["outcome"], "accepted")
        self.assertEqual(self.store.pause_info(work_key)
                         ["pause_state"], "paused")
        # The lane will not dispatch it while paused.
        self.lane.tick()
        self.assertEqual(self.store.attempt_record_rows(work_key), [])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "pending")

    def test_status_distinguishes_pause_states(self):
        self._accept(4)
        before = self.control.handle(self._msg("status", issue=4))
        self.assertIn("not paused", before["text"])
        self.control.handle(self._msg("pause", issue=4))
        paused = self.control.handle(self._msg("status", issue=4))
        self.assertIn("PAUSED", paused["text"])
        self.control.handle(self._msg("resume", issue=4))
        resumed = self.control.handle(self._msg("status", issue=4))
        self.assertIn("RESUMED", resumed["text"])


# ---------------------------------------------------------------------------
# A3 — cancel: fence before termination; late effects denied; exit or
# quarantine within the window
# ---------------------------------------------------------------------------

class TestA3CancelFencesFirst(ControlFixture):

    def test_cancel_midflight_fences_then_terminates(self):
        work_key = self._accept(5)
        seen = {}

        def impl(ctx):
            seen["cancel"] = self.control.handle(
                self._msg("cancel", issue=5))
            # The fence is already durable at this point — inside the
            # handler's commit, before termination ran.
            seen["fence_at_return"] = self.store.fence_state(work_key)
            return WorkerResult(verdict="completed")

        worker = ScriptedWorker(script={"implementation": impl})
        lane = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=worker,
            workspace_root=Path(self.tmp.name) / "ws5",
            readings=READINGS, now=self._now)
        self.control.lane = lane
        lane.tick()

        cancel = seen["cancel"]
        self.assertEqual(cancel["outcome"], "accepted")
        self.assertEqual(cancel["termination"], "confirmed")
        self.assertTrue(seen["fence_at_return"]["fenced"])
        self.assertEqual(seen["fence_at_return"]["termination"],
                         "confirmed")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "canceled")
        # The late completion was denied: fenced-result evidence.
        self.assertTrue(self.store.alert_rows("fenced-result"))
        results = [r for r in self.store.result_rows()
                   if not r["accepted"]]
        self.assertTrue(results)
        # New effects are denied for the fenced generation.
        from factory_kit.publication import (
            OPERATION_PR_PUBLISH, PublicationBroker, ScriptedRemote)
        broker = PublicationBroker(
            self.store, ScriptedRemote(self.repo_id, "o/n"),
            self.registrations, self.eff, clock=self._now)
        out = broker.publish(work_key, {
            "operation": OPERATION_PR_PUBLISH,
            "target": "b/5",
            "repository": {"repo_id": self.repo_id,
                           "full_name": "luongnv89/money-mind"},
            "actor_ref": "luongnv89"})
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "fenced")

    def test_uncertain_termination_quarantines_and_blocks_retry(self):
        work_key = self._accept(6)
        seen = {}

        def impl(ctx):
            seen["cancel"] = self.control.handle(
                self._msg("cancel", issue=6))
            return WorkerResult(verdict="completed")

        worker = ScriptedWorker(script={"implementation": impl})
        lane = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=worker,
            workspace_root=Path(self.tmp.name) / "ws6",
            readings=READINGS, now=self._now)
        self.control.lane = lane
        # The attempt's termination is configured uncertain *before*
        # the cancel runs — attempt ids are task-derived (a01 = first).
        task_id = self.store.get_work(work_key)["task_id"]
        worker.terminations[f"{task_id}-a01"] = "uncertain"
        lane.tick()

        self.assertEqual(seen["cancel"]["termination"], "quarantined")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "quarantined")
        self.assertEqual(self.store.fence_state(work_key)
                         ["termination"], "quarantined")
        # The operator was notified inside the window.
        self.assertTrue(self.store.alert_rows("termination-uncertain"))
        # …and no replacement may dispatch while termination is
        # uncertain (A3/A5).
        retry = self.control.handle(self._msg("retry", issue=6))
        self.assertEqual(retry["outcome"], "rejected")
        self.assertEqual(retry["reason"], "termination-uncertain")


# ---------------------------------------------------------------------------
# A4 — fence ack ≠ exit; effects stay linked; retry needs fresh auth
# ---------------------------------------------------------------------------

class TestA4SeparateOutcomesAndRetry(ControlFixture):

    def test_fence_and_exit_are_separate_persisted_outcomes(self):
        work_key = self._accept(7)
        out = self.control.handle(self._msg("cancel", issue=7))
        self.assertEqual(out["outcome"], "accepted")
        fence = self.store.fence_state(work_key)
        # Two distinct persisted facts: the fence acknowledgement and
        # the confirmed process exit.
        self.assertTrue(fence["fenced"])
        self.assertEqual(fence["termination"], "confirmed")
        self.assertEqual(fence["fence_reason"], "cancel")
        self.assertTrue(self.store.event_rows("work_terminated"))
        self.assertTrue(self.store.event_rows("work_fenced"))

    def test_effects_committed_before_cancel_stay_linked(self):
        work_key = self._accept(8)
        # Commit an effect first (authorized publish), then cancel —
        # the committed effect is accurately linked, never reversed.
        from factory_kit.publication import (
            OPERATION_PR_PUBLISH, PublicationBroker, ScriptedRemote)
        remote = ScriptedRemote(self.repo_id, "o/n")
        broker = PublicationBroker(
            self.store, remote, self.registrations, self.eff,
            clock=self._now)
        pub = broker.publish(work_key, {
            "operation": OPERATION_PR_PUBLISH, "target": "b/8",
            "repository": {"repo_id": self.repo_id,
                           "full_name": "luongnv89/money-mind"},
            "actor_ref": "luongnv89"})
        self.assertEqual(pub["outcome"], "applied")
        self.control.handle(self._msg("cancel", issue=8))
        intent = self.store.get_intent(pub["intent_id"])
        self.assertEqual(intent["state"], "linked")
        self.assertEqual(self.store.get_work(work_key)["linked_pr"],
                         intent["remote_ref"])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "canceled")

    def test_retry_mints_fresh_audited_generation(self):
        work_key = self._accept(9)
        self.control.handle(self._msg("cancel", issue=9))
        out = self.control.handle(self._msg("retry", issue=9))
        self.assertEqual(out["outcome"], "accepted")
        self.assertEqual(out["generation"], 2)
        self.assertNotEqual(out["work_key"], work_key)
        reg = self.registrations.get(self.repo_id)
        self.assertEqual(reg["active_generation"], 2)
        self.assertEqual(len(reg["generations"]), 2)
        # The retry generation is audited: attributable authorized_by.
        self.assertEqual(reg["generations"][1]["authorized_by"],
                         f"telegram:{USER}")
        # Prior attempts/state stay visible under the old work key.
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "canceled")
        # …and the retried row is queued, ready for the lane.
        self.assertEqual(self.store.get_work(out["work_key"])["state"],
                         "pending")

    def test_cancel_is_terminal_for_the_generation(self):
        work_key = self._accept(10)
        self.control.handle(self._msg("cancel", issue=10))
        # A second cancel on the same generation is rejected — cancel
        # is terminal, not idempotent re-entry.
        out = self.control.handle(self._msg("cancel", issue=10))
        self.assertEqual(out["outcome"], "rejected")
        self.assertIn(out["reason"], ("work-terminal",
                                      "already-canceled"))


# ---------------------------------------------------------------------------
# A5 — every rejection class; natural language through the same
# validator; no broadened permissions, no repeated effects
# ---------------------------------------------------------------------------

class TestA5Rejections(ControlFixture):

    def test_forged_actor_rejected(self):
        self._accept(11)
        out = self.control.handle(
            self._msg("status", issue=11, user=999999))
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "forged-actor")
        self.assertEqual(self._records(out["command_id"])[0]
                         ["outcome"], "rejected")

    def test_wrong_chat_rejected(self):
        self._accept(12)
        out = self.control.handle(
            self._msg("status", issue=12, chat=-777))
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "wrong-chat")

    def test_canonical_negative_chat_ref_roundtrips(self):
        """Structured transports may echo the *canonical* ref form —
        ``telegram:-<digits>`` for group chats. The emitted form must
        be accepted back (negative Telegram IDs are the norm)."""
        self._accept(24)
        out = self.control.handle({
            "command_id": "c-neg", "actor_ref": f"telegram:{USER}",
            "chat_ref": f"telegram:{CHAT}", "action": "status",
            "repo_id": self.repo_id, "issue": 24})
        self.assertEqual(out["outcome"], "answered", out)
        # …and the record holds the same canonical form an int emits.
        rec = self._records("c-neg")[0]
        self.assertEqual(rec["chat_ref"], f"telegram:{CHAT}")

    def test_missing_target_asks_for_concrete(self):
        self._accept(13)
        msg = self._msg("pause", issue=13)
        del msg["generation"]
        out = self.control.handle(msg)
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "target-required")
        self.assertIn("concrete target", out["text"])
        self.assertIn("pause", out["text"])         # shows the syntax

    def test_unknown_target_rejected(self):
        self._accept(14)
        out = self.control.handle(
            self._msg("pause", issue=999, generation=1))
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "target-required")

    def test_duplicate_command_replays_without_repeating(self):
        work_key = self._accept(15)
        msg = self._msg("pause", issue=15)
        first = self.control.handle(msg)
        second = self.control.handle(dict(msg))     # same command_id
        self.assertEqual(first["outcome"], "accepted")
        self.assertEqual(second["outcome"], "duplicate")
        self.assertEqual(second["recorded_outcome"], "accepted")
        # One durable record; no second effect.
        self.assertEqual(len(self._records(msg["command_id"])), 1)
        self.assertEqual(self.store.pause_info(work_key)
                         ["pause_state"], "paused")

    def test_forged_replay_rejected(self):
        self._accept(16)
        msg = self._msg("status", issue=16)
        self.control.handle(msg)
        replay = dict(msg)
        replay["actor"] = {"user_id": 999999}       # forged actor
        out = self.control.handle(replay)
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "forged-replay")

    def test_stale_generation_rejected(self):
        work_key = self._accept(17)
        self.control.handle(self._msg("cancel", issue=17))
        self.control.handle(self._msg("retry", issue=17))  # gen 2 minted
        out = self.control.handle(
            self._msg("pause", issue=17, generation=1))
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "stale-generation")

    def test_unsupported_action_rejected(self):
        self._accept(18)
        out = self.control.handle(
            self._msg("detonate", issue=18))
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "unsupported-action")

    def test_natural_language_passes_same_validator(self):
        work_key = self._accept(19)
        out = self.control.handle({
            "command_id": "nl-1", "actor": {"user_id": USER},
            "chat": {"chat_id": CHAT},
            "text": f"please pause issue 19 repo {self.repo_id} "
                    f"generation 1"})
        self.assertEqual(out["outcome"], "accepted", out)
        self.assertEqual(self.store.pause_info(work_key)
                         ["pause_state"], "paused")
        # The record holds the typed action — the proposal was
        # validated into 'pause', not executed as prose.
        self.assertEqual(self._records("nl-1")[0]["action"], "pause")

    def test_natural_language_ambiguous_asks_concrete(self):
        self._accept(20)
        out = self.control.handle({
            "command_id": "nl-2", "actor": {"user_id": USER},
            "chat": {"chat_id": CHAT},
            "text": "maybe pause something?"})     # no target
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "target-required")
        self.assertIn("concrete target", out["text"])


# ---------------------------------------------------------------------------
# A6 — accessible responses: explicit state words, stable ids, text
# alternatives, no color/emoji dependence
# ---------------------------------------------------------------------------

class TestA6AccessibleResponses(ControlFixture):

    def test_status_text_is_explicit_and_complete(self):
        work_key = self._accept(21)
        out = self.control.handle(self._msg("status", issue=21))
        text = out["text"]
        self.assertIn("STATE QUEUED", text)
        self.assertIn(work_key, text)
        self.assertIn("task", text)
        self.assertIn("generation 1", text)
        self.assertIn("Attempts:", text)
        self.assertIn("Limits:", text)        # the budget surface (A2)
        self.assertIn("Blocker:", text)
        self.assertIn("Committed effects:", text)
        self.assertIn("Last heartbeat:", text)
        self.assertIn("Fence:", text)
        self.assertIn("Commands:", text)      # documented alternatives
        # ASCII-only: nothing depends on colour or emoji glyphs.
        self.assertTrue(text.isascii())

    def test_ack_texts_carry_state_words_and_ids(self):
        work_key = self._accept(22)
        out = self.control.handle(self._msg("pause", issue=22))
        self.assertIn("PAUSED", out["text"])
        self.assertIn(work_key, out["text"])
        self.assertIn(out["command_id"], out["text"])
        self.assertTrue(out["text"].isascii())
        cancel = self.control.handle(self._msg("cancel", issue=22))
        self.assertIn("CANCELED", cancel["text"])
        self.assertTrue(cancel["text"].isascii())

    def test_committed_state_survives_without_ack(self):
        """If the response were lost after commit, the durable record
        and state still stand — transport failure cannot undo them."""
        work_key = self._accept(23)
        out = self.control.handle(self._msg("pause", issue=23))
        rec = self._records(out["command_id"])[0]
        self.assertEqual(rec["outcome"], "accepted")
        self.assertEqual(self.store.pause_info(work_key)
                         ["pause_state"], "paused")
        # Re-delivering the same command replays the recorded outcome —
        # the ack is reconstructible from the durable row.
        replay = self.control.handle({
            "command_id": out["command_id"],
            "actor": {"user_id": USER}, "chat": {"chat_id": CHAT}})
        self.assertEqual(replay["outcome"], "duplicate")


if __name__ == "__main__":
    unittest.main()
