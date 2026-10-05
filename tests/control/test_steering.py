#!/usr/bin/env python3
"""Checkpointed scope steering — F09 (issue #25 / Task 4.1).

Each test class maps to an acceptance criterion:

- A1 — an allowlisted operator's scope change persists the target
  generation, revised criteria, authorization and audit identity in
  ``scope_revisions`` *before* acknowledgement; duplicate commands and
  controller restart converge on one accepted change.
- A2 — at a supported checkpoint the old attempt is fenced before the
  replacement obtains authority; the replacement receives the revised
  criteria and the recorded remaining active-time / fix-attempt /
  wall-time budgets — consumed usage is retained, never reset.
- A3 — changing scope/revision invalidates old verified evidence and
  live approvals; the active preview retires; completion needs fresh
  matching evidence under the new revision.
- A4 — canceled generations cannot be revived by steering, a fenced
  attempt's late result is denied, and a ``live`` request on a runtime
  without the capability is answered with checkpoint/restart behavior
  — no unsupported harness is dispatched.
- A5 — an uncertain checkpoint fence or an exhausted remaining budget
  parks with the durable reason; retry/cancel/restart races never
  leave overlapping authority.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.config import registration, schema  # noqa: E402
from factory_kit.control import ControlService  # noqa: E402
from factory_kit.control import steering  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.execution import (  # noqa: E402
    ExecutionLane, ScriptedWorker, WorkerResult)
from factory_kit.intake import service  # noqa: E402

MANIFEST = ROOT / ".factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"

READINGS = {
    "tools": {"git": "/usr/bin/git", "gh": "/x/gh", "hermes": "/x/hermes"},
    "models": {"openai-codex": {"authenticated": True,
                                "available": ["gpt-6-luna"]}},
    "skills": {"issue-resolver": "0.19.0", "issue-pr-review": "0.19.0"},
}

USER = 123456789
CHAT = -1001234567890

CRITERIA_V2 = "Revised scope: focus on the documentation surface only."


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


class _PreviewStub:
    """Captures service-level invalidation calls — the durable A3
    assertions live on the store; the stub proves the post-commit
    hook fires with the steer reason."""

    def __init__(self):
        self.calls = []

    def invalidate(self, work_key, reason):
        self.calls.append((work_key, reason))
        return {"outcome": "invalidated", "work_key": work_key}


class SteerFixture(unittest.TestCase):
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
        self.preview = _PreviewStub()
        self.alerts = []
        self.control = ControlService(
            self.store, self.lane, self.registrations,
            {self.repo_id: self.eff}, preview=self.preview,
            alert_sink=self.alerts, now=self._now)
        self.lane.steer_post_apply = self.control._steer_post_apply
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

    def _msg(self, action="steer", *, issue=1, generation=1,
             user=USER, chat=CHAT, command_id=None, **fields):
        self._cid[0] += 1
        msg = {"command_id": command_id or f"c-{self._cid[0]:03d}",
               "actor": {"user_id": user},
               "chat": {"chat_id": chat},
               "action": action, "repo_id": self.repo_id,
               "issue": issue, "generation": generation}
        msg.update(fields)
        return msg

    def _steer(self, issue=1, criteria=CRITERIA_V2, **fields):
        return self.control.handle(
            self._msg("steer", issue=issue, criteria=criteria,
                      **fields))

    def _wire_lane(self, worker, name="wsX"):
        """A lane owning the given scripted worker, bound to control —
        the pattern the mid-flight tests need for steer_terminate to
        reach the live handle."""
        lane = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=worker,
            workspace_root=Path(self.tmp.name) / name,
            readings=READINGS, now=self._now)
        lane.steer_post_apply = self.control._steer_post_apply
        self.control.lane = lane
        return lane

    def _steers(self, work_key):
        return self.store.steer_rows(work_key)


# ---------------------------------------------------------------------------
# A1 — durable revision before ack; dedup; restart replay → one change
# ---------------------------------------------------------------------------

class TestA1RecordedBeforeAck(SteerFixture):

    def test_steer_persists_revision_then_ack(self):
        work_key = self._accept(1)
        out = self._steer(1)
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["steer_state"], "applied")
        rows = self._steers(work_key)
        self.assertEqual(len(rows), 1)
        steer = rows[0]
        # Target generation + revised criteria + authorization + audit
        # identity — all durable before the ack text returned.
        self.assertEqual(steer["generation"], 1)
        self.assertEqual(steer["revision"], 1)
        self.assertEqual(steer["criteria"], CRITERIA_V2)
        self.assertEqual(steer["criteria_fingerprint"],
                         steering.criteria_fingerprint(CRITERIA_V2))
        self.assertEqual(steer["actor_ref"], f"telegram:{USER}")
        self.assertEqual(steer["chat_ref"], f"telegram:{CHAT}")
        self.assertEqual(steer["command_id"], out["command_id"])
        self.assertEqual(steer["work_key"], work_key)
        # The control record committed in the same handler.
        rec = self.store.find_control(out["command_id"])
        self.assertEqual(rec["outcome"], "accepted")
        # The revised context is now the work's binding.
        work = self.store.get_work(work_key)
        self.assertEqual(work["context_fingerprint"],
                         steer["criteria_fingerprint"])
        # …and the original issue revision is preserved, not rewritten.
        self.assertEqual(work["issue_revision"], "2026-10-05")

    def test_duplicate_command_replays_one_change(self):
        work_key = self._accept(2)
        msg = self._msg("steer", issue=2, criteria=CRITERIA_V2)
        first = self.control.handle(msg)
        second = self.control.handle(dict(msg))
        self.assertEqual(first["outcome"], "accepted")
        self.assertEqual(second["outcome"], "duplicate")
        self.assertEqual(second["recorded_outcome"], "accepted")
        self.assertEqual(len(self._steers(work_key)), 1)
        rows = [r for r in self.store.control_rows()
                if r["command_id"] == msg["command_id"]]
        self.assertEqual(len(rows), 1)

    def test_restart_replay_yields_one_change(self):
        """A fresh service instance over the same store (the controller
        restart) replays the same command id onto the committed
        revision — never a second scope change (A1)."""
        work_key = self._accept(3)
        msg = self._msg("steer", issue=3, criteria=CRITERIA_V2)
        first = self.control.handle(msg)
        self.assertTrue(first["ok"])
        restarted = ControlService(
            self.store, self.lane, self.registrations,
            {self.repo_id: self.eff}, alert_sink=self.alerts,
            now=self._now)
        replay = restarted.handle(dict(msg))
        self.assertEqual(replay["outcome"], "duplicate")
        self.assertEqual(replay["recorded_outcome"], "accepted")
        self.assertEqual(len(self._steers(work_key)), 1)


# ---------------------------------------------------------------------------
# A2 — fence precedes authority; revised criteria + remaining budgets
# ---------------------------------------------------------------------------

class TestA2FenceThenReplace(SteerFixture):

    def test_midflight_steer_fences_then_replaces(self):
        work_key = self._accept(4)
        seen = {}

        def impl(ctx):
            if "steer" not in seen:
                seen["steer"] = self._steer(4)
                # The durable fence already landed inside the
                # handler's commit — before termination ran and
                # before any replacement could claim (A2).
                seen["fence"] = [
                    r for r in self.store.attempt_rows()
                    if r["work_key"] == work_key]
            return WorkerResult(verdict="completed",
                                active_seconds=42.0)

        worker = ScriptedWorker(script={"implementation": impl})
        lane = self._wire_lane(worker, "ws4")
        lane.tick()

        steer = seen["steer"]
        self.assertEqual(steer["outcome"], "accepted")
        self.assertEqual(steer["steer_state"], "applied")
        self.assertEqual(steer["termination"], "confirmed")
        # The in-flight attempt was fenced while the handler ran.
        self.assertEqual(seen["fence"][0]["state"], "fenced")

        # The replacement re-ran the implementation stage under the
        # revised criteria — one fenced attempt + one replacement +
        # review, in order.
        recs = self.store.attempt_record_rows(work_key)
        self.assertEqual([r["role"] for r in recs],
                         ["implementation", "implementation",
                          "review"])
        self.assertEqual(
            self.store.get_work(work_key)["state"], "completed")

        # Replacement context: the new fingerprint's acceptance ref
        # and the recorded remaining budgets ride the dispatch.
        ctxs = worker.started
        self.assertEqual(len(ctxs), 3)
        old_ref = ctxs[0].acceptance_ref
        new_ref = ctxs[1].acceptance_ref
        self.assertNotEqual(old_ref, new_ref)
        self.assertIn(
            steering.criteria_fingerprint(CRITERIA_V2)[:12], new_ref)
        bound = ctxs[1].limits.get("steer") or {}
        self.assertEqual(bound["revision"], 1)
        remaining = bound["remaining"]
        # The fenced attempt *consumed* one fix budget slot — the
        # replacement got the remainder, never a reset (A2).
        self.assertEqual(remaining["implementation_attempts"], 1)
        self.assertEqual(bound["steer_id"],
                         self._steers(work_key)[0]["steer_id"])

        # Durable budget record on the revision row.
        budgets = json.loads(self._steers(work_key)[0]["budgets"])
        self.assertEqual(
            budgets["consumed"]["implementation_attempts"], 1)
        self.assertEqual(
            budgets["remaining"]["implementation_attempts"], 1)

        # The fenced attempt's late result was denied — durable
        # rejection evidence, never a committed transition (A4).
        denied = [r for r in self.store.result_rows()
                  if not r["accepted"]]
        self.assertTrue(denied)
        self.assertIn("fenced", denied[0]["reason"])

        # …and the fence preceded the apply in the event trail.
        kinds = [e["kind"] for e in self.store.event_rows()]
        self.assertLess(kinds.index("attempts_fenced"),
                        kinds.index("scope_steer_applied"))
        self.assertIn("steer_terminated", kinds)

    def test_queued_work_applies_in_one_commit(self):
        """No stage in flight = already at a checkpoint: record +
        apply land inside the same transaction (A2)."""
        work_key = self._accept(5)
        out = self._steer(5)
        self.assertEqual(out["steer_state"], "applied")
        self.assertEqual(out["termination"], None)
        steer = self._steers(work_key)[0]
        self.assertEqual(steer["applied_stage"], "implementation")
        # The preview hook ran for the applied revision (A3).
        self.assertEqual(self.preview.calls,
                         [(work_key, "scope-steered")])
        # The next dispatch runs the revised criteria end-to-end.
        self.lane.tick()
        ctx = self.worker.started[0]
        self.assertIn(
            steering.criteria_fingerprint(CRITERIA_V2)[:12],
            ctx.acceptance_ref)
        self.assertEqual(
            self.store.get_work(work_key)["state"], "completed")

    def test_paused_boundary_steers_the_held_stage(self):
        """A pause-held boundary is the checkpoint: the revision
        applies there and the held stage re-enters under revised
        criteria on resume (A2)."""
        work_key = self._accept(6)
        seen = {}

        def impl(ctx):
            seen["pause"] = self.control.handle(
                self._msg("pause", issue=6))
            return WorkerResult(verdict="completed",
                                active_seconds=120.0)

        worker = ScriptedWorker(script={"implementation": impl})
        lane = self._wire_lane(worker, "ws6")
        lane.tick()
        self.assertEqual(self.store.pause_info(work_key)
                         ["paused_stage"], "review")

        out = self._steer(6)
        self.assertTrue(out["ok"], out)
        steer = self._steers(work_key)[0]
        self.assertEqual(steer["state"], "applied")
        # The held review boundary is the recorded checkpoint.
        self.assertEqual(steer["applied_stage"], "review")
        # Consumed usage is retained — the 120 measured seconds are
        # subtracted from the bound, never reset (A2).
        budgets = json.loads(steer["budgets"])
        self.assertEqual(
            budgets["consumed"]["active_worker_seconds"], 120.0)
        self.assertEqual(
            budgets["remaining"]["active_worker_seconds"],
            3600.0 - 120.0)
        self.assertEqual(
            budgets["consumed"]["implementation_attempts"], 1)

        # Resume → the held review stage runs under the revision.
        self.control.handle(self._msg("resume", issue=6))
        lane.tick()
        recs = self.store.attempt_record_rows(work_key)
        self.assertEqual([r["role"] for r in recs],
                         ["implementation", "review"])
        self.assertEqual(worker.started[-1].role, "review")
        self.assertIn(
            steering.criteria_fingerprint(CRITERIA_V2)[:12],
            worker.started[-1].acceptance_ref)


# ---------------------------------------------------------------------------
# A3 — revision change invalidates old evidence + approvals + preview
# ---------------------------------------------------------------------------

class TestA3EvidenceInvalidation(SteerFixture):

    def test_steer_stales_verified_evidence_and_kills_approval(self):
        work_key = self._accept(7)
        work = self.store.get_work(work_key)
        # A verified observation + a live approved grant under the
        # old context.
        self.store.record_evidence(
            "ev-old", work_key=work_key, pr_number=11,
            pr_url="https://x/11", head_sha="aaa", base_name="main",
            base_sha="bbb", checks=[{"name": "ci", "state": "pass"}],
            review_id="rev-1", review_session="sess-r",
            contract_digest="cd", status="verified")
        self.store.insert_approval_request(
            "req-1", work_key=work_key, task_id=work["task_id"],
            generation=1, authority_key=work["authority_key"],
            repo_id=self.repo_id, action="merge",
            target="b/7", head_sha="aaa", state="approved",
            issued_at="2026-10-05T00:00:00Z", expires_epoch=9999999999)

        out = self._steer(7)
        self.assertTrue(out["ok"], out)

        # Verified evidence is now stale — a new observation carrying
        # the same identities, marked with the steer reason (A3).
        latest = self.store.latest_evidence(work_key)
        self.assertEqual(latest["status"], "stale")
        self.assertEqual(latest["reason"], "scope-steered")
        self.assertEqual(latest["head_sha"], "aaa")
        # The live grant died with the superseded revision.
        self.assertEqual(self.store.live_approval_requests(work_key),
                         [])
        req = self.store.get_approval_request("req-1")
        self.assertEqual(req["state"], "invalidated")
        self.assertEqual(req["reason"], "scope-steered")
        # …and the preview hook retired the revision-bound preview.
        self.assertEqual(self.preview.calls,
                         [(work_key, "scope-steered")])

    def test_completion_needs_fresh_evidence_under_new_revision(self):
        """Post-steer, the gate reads the new context binding — the
        superseded revision's verified evidence cannot satisfy it."""
        work_key = self._accept(8)
        self.store.record_evidence(
            "ev-old", work_key=work_key, pr_number=12, head_sha="aaa",
            status="verified")
        self._steer(8)
        # latest verified anchor = none for the new revision; the
        # stale row is the *latest* observation only.
        latest = self.store.latest_evidence(work_key)
        self.assertEqual(latest["status"], "stale")
        verified = [e for e in self.store.evidence_rows(work_key)
                    if e["status"] == "verified"]
        self.assertEqual(len(verified), 1)   # old row kept verbatim
        # A fresh verified observation under the new revision is what
        # completion consumes — history is never rewritten.
        self.store.record_evidence(
            "ev-new", work_key=work_key, pr_number=12, head_sha="ccc",
            status="verified")
        self.assertEqual(self.store.latest_evidence(work_key)
                         ["status"], "verified")


# ---------------------------------------------------------------------------
# A4 — no revival; late descendant denied; unsupported live explained
# ---------------------------------------------------------------------------

class TestA4NoRevival(SteerFixture):

    def test_canceled_generation_rejects_steer(self):
        work_key = self._accept(9)
        self.control.handle(self._msg("cancel", issue=9))
        out = self._steer(9)
        self.assertEqual(out["outcome"], "rejected")
        self.assertIn(out["reason"],
                      ("work-terminal", "generation-canceled"))
        # No revision row — a canceled generation gains no authority.
        self.assertEqual(self._steers(work_key), [])

    def test_stale_generation_rejects_steer(self):
        work_key = self._accept(10)
        self.control.handle(self._msg("cancel", issue=10))
        retry = self.control.handle(self._msg("retry", issue=10))
        self.assertEqual(retry["generation"], 2)
        out = self._steer(10, generation=1)
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "stale-generation")
        self.assertEqual(self._steers(work_key), [])

    def test_live_mode_unsupported_explains_checkpoint(self):
        work_key = self._accept(11)
        out = self._steer(11, mode="live")
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "live-steering-unsupported")
        # A4 — the answer teaches the supported path; it never
        # claims live injection and never dispatches a harness.
        self.assertIn("checkpoint", out["text"])
        self.assertIn("cancel", out["text"])
        self.assertIn("retry", out["text"])
        self.assertEqual(self._steers(work_key), [])
        self.assertEqual(self.store.attempt_record_rows(work_key), [])

    def test_late_descendant_result_denied(self):
        work_key = self._accept(12)
        seen = {}

        def impl(ctx):
            seen["steer"] = self._steer(12)
            return WorkerResult(verdict="completed")

        worker = ScriptedWorker(script={"implementation": impl})
        self._wire_lane(worker, "ws12").tick()
        denied = [r for r in self.store.result_rows(
            attempt_id=f"{self.store.get_work(work_key)['task_id']}-a01")
            if not r["accepted"]]
        self.assertTrue(denied)
        self.assertEqual(denied[0]["reason"], "attempt-fenced")


# ---------------------------------------------------------------------------
# A5 — parks; retry/cancel/restart races never overlap authority
# ---------------------------------------------------------------------------

class TestA5SafeParkingAndRaces(SteerFixture):

    def test_uncertain_termination_parks_no_overlap(self):
        work_key = self._accept(13)
        task_id = self.store.get_work(work_key)["task_id"]
        seen = {}

        def impl(ctx):
            seen["steer"] = self._steer(13)
            return WorkerResult(verdict="completed")

        worker = ScriptedWorker(script={"implementation": impl})
        worker.terminations[f"{task_id}-a01"] = "uncertain"
        lane = self._wire_lane(worker, "ws13")
        lane.tick()

        steer = self._steers(work_key)[0]
        self.assertEqual(steer["state"], "parked")
        self.assertEqual(steer["reason"], "steer-termination-uncertain")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")
        # A5 — the operator was notified inside the window and NO
        # replacement attempt ever claimed (authority never overlapped).
        self.assertTrue(
            self.store.alert_rows("steer-terminate-uncertain"))
        self.assertEqual(
            self.store.count_attempts(work_key), 1)
        self.assertFalse(seen["steer"]["ok"])

    def test_exhausted_remaining_budget_parks(self):
        """A revision whose recorded remaining budget is already spent
        parks instead of minting replacement authority (A5)."""
        work_key = self._accept(14)

        def impl(ctx):
            self.control.handle(self._msg("pause", issue=14))
            # 4000s measured > the 3600s bound — consumed usage is
            # retained, so the replacement's remaining is zero.
            return WorkerResult(verdict="completed",
                                active_seconds=4000.0)

        worker = ScriptedWorker(script={"implementation": impl})
        self._wire_lane(worker, "ws14").tick()
        self.assertEqual(self.store.pause_info(work_key)
                         ["pause_state"], "paused")

        out = self._steer(14)
        self.assertFalse(out["ok"])
        steer = self._steers(work_key)[0]
        self.assertEqual(steer["state"], "parked")
        self.assertIn("steer-budget-exhausted", steer["reason"])
        self.assertIn("active_worker_seconds", steer["reason"])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")
        self.assertEqual(self.store.count_attempts(work_key), 1)

    def test_restart_window_pending_steer_recovers(self):
        """Crash between the record+fence commit and the apply: a
        rebuilt controller finds the pending revision at the
        checkpoint and parks honestly — the fenced attempt's exit can
        never be confirmed without its handle (A5)."""
        work_key = self._accept(15)
        work = self.store.get_work(work_key)
        task_id = work["task_id"]
        self.store.set_work_state(work_key, "active")
        begun = self.store.begin_execution_attempt(
            work_key, attempt_id=f"{task_id}-a01",
            role="implementation", task_id=task_id, generation=1,
            session_id="s-crash", runtime="hermes-kanban",
            model="gpt-6-luna", skills="{}",
            config_digest=work["config_digest"],
            policy_digest=work["policy_digest"],
            workspace="/tmp/x", limits="{}",
            now_epoch=self.clock[0])
        self.assertEqual(begun["outcome"], "active")
        with self.store.transact() as tx:
            steer = self.store.record_steer(
                "steer-crash-1", command_id="c-crash",
                work_key=work_key,
                authority_key=work["authority_key"],
                repo_id=self.repo_id, issue=15, generation=1,
                revision=1, criteria=CRITERIA_V2,
                criteria_fingerprint=
                    steering.criteria_fingerprint(CRITERIA_V2),
                actor_ref=f"telegram:{USER}", seq=tx.next_seq(),
                received_at="2026-10-05T00:00:00Z")
            tx.fence_attempts_tx(work_key, f"steer:{steer['steer_id']}")
        # "Restart": a fresh lane owns no handles — the pre-crash
        # worker's exit can never be confirmed.
        restarted = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=self.worker,
            workspace_root=Path(self.tmp.name) / "ws15",
            readings=READINGS, now=self._now)
        restarted.tick()

        steer = self.store.get_steer("steer-crash-1")
        self.assertEqual(steer["state"], "parked")
        self.assertEqual(steer["reason"], "steer-termination-uncertain")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")
        self.assertEqual(self.store.count_attempts(work_key), 1)

    def test_restart_window_finished_attempt_applies(self):
        """Same crash window but the fenced attempt provably ended
        before the fence — the checkpoint apply is safe: termination
        is confirmed by the durable end, the revision commits (A5)."""
        work_key = self._accept(16)
        work = self.store.get_work(work_key)
        task_id = work["task_id"]
        self.store.set_work_state(work_key, "active")
        self.store.begin_execution_attempt(
            work_key, attempt_id=f"{task_id}-a01",
            role="implementation", task_id=task_id, generation=1,
            session_id="s-done", runtime="hermes-kanban",
            model="gpt-6-luna", skills="{}",
            config_digest=work["config_digest"],
            policy_digest=work["policy_digest"],
            workspace="/tmp/x", limits="{}",
            now_epoch=self.clock[0])
        self.store.finish_execution_attempt(
            f"{task_id}-a01", verdict="completed",
            outcome="completed", active_seconds=30.0, usage=None,
            duration_s=31.0)
        with self.store.transact() as tx:
            steer = self.store.record_steer(
                "steer-crash-2", command_id="c-crash2",
                work_key=work_key,
                authority_key=work["authority_key"],
                repo_id=self.repo_id, issue=16, generation=1,
                revision=1, criteria=CRITERIA_V2,
                criteria_fingerprint=
                    steering.criteria_fingerprint(CRITERIA_V2),
                actor_ref=f"telegram:{USER}", seq=tx.next_seq(),
                received_at="2026-10-05T00:00:00Z")
            tx.fence_attempts_tx(work_key, f"steer:{steer['steer_id']}")

        restarted = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=self.worker,
            workspace_root=Path(self.tmp.name) / "ws16",
            readings=READINGS, now=self._now)
        restarted.tick()

        steer = self.store.get_steer("steer-crash-2")
        self.assertEqual(steer["state"], "applied")
        self.assertEqual(steer["applied_stage"], "implementation")
        # The replacement ran the stage under revised criteria.
        recs = self.store.attempt_record_rows(work_key)
        self.assertGreaterEqual(len(recs), 2)
        self.assertIn(
            steering.criteria_fingerprint(CRITERIA_V2)[:12],
            self.worker.started[-1].acceptance_ref)

    def test_cancel_after_applied_steer_still_wins(self):
        """Cancel racing an applied steer: the generation fence lands
        and no replacement dispatches — no overlapping authority."""
        work_key = self._accept(17)
        seen = {}

        def impl(ctx):
            seen["steer"] = self._steer(17)
            seen["cancel"] = self.control.handle(
                self._msg("cancel", issue=17))
            return WorkerResult(verdict="completed")

        worker = ScriptedWorker(script={"implementation": impl})
        self._wire_lane(worker, "ws17").tick()

        self.assertTrue(seen["steer"]["ok"])
        self.assertEqual(self._steers(work_key)[0]["state"], "applied")
        self.assertEqual(seen["cancel"]["outcome"], "accepted")
        state = self.store.get_work(work_key)["state"]
        self.assertIn(state, ("canceled", "quarantined"))
        # The generation fence is terminal — the replacement never
        # claimed: exactly one attempt record exists.
        self.assertEqual(self.store.count_attempts(work_key), 1)

    def test_pending_steer_rejects_second_steer(self):
        """At most one un-applied scope change per work — a second
        command while one is pending rejects, it never queues (A1)."""
        work_key = self._accept(18)
        work = self.store.get_work(work_key)
        with self.store.transact() as tx:
            self.store.record_steer(
                "steer-pend-1", command_id="c-pend",
                work_key=work_key,
                authority_key=work["authority_key"],
                repo_id=self.repo_id, issue=18, generation=1,
                revision=1, criteria=CRITERIA_V2,
                criteria_fingerprint=
                    steering.criteria_fingerprint(CRITERIA_V2),
                actor_ref=f"telegram:{USER}", seq=tx.next_seq(),
                received_at="2026-10-05T00:00:00Z")
        out = self._steer(18, criteria="a different scope")
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "steer-pending")
        self.assertEqual(len(self._steers(work_key)), 1)

    def test_retry_after_parked_steer_mints_new_generation(self):
        """A parked steer doesn't trap the issue: an authorized retry
        mints a fresh generation, and the new generation steers
        independently (A4/A5)."""
        work_key = self._accept(19)
        task_id = self.store.get_work(work_key)["task_id"]

        def impl(ctx):
            self._steer(19)
            return WorkerResult(verdict="completed")

        worker = ScriptedWorker(script={"implementation": impl})
        worker.terminations[f"{task_id}-a01"] = "uncertain"
        self._wire_lane(worker, "ws19").tick()
        self.assertEqual(self._steers(work_key)[0]["state"], "parked")

        retry = self.control.handle(self._msg("retry", issue=19))
        self.assertEqual(retry["outcome"], "accepted", retry)
        self.assertEqual(retry["generation"], 2)
        new_key = retry["work_key"]
        self.assertNotEqual(new_key, work_key)

        # The new generation is steerable — its own revision chain.
        out = self._steer(19, generation=2)
        self.assertTrue(out["ok"], out)
        new_steer = self._steers(new_key)[0]
        self.assertEqual(new_steer["revision"], 1)
        self.assertEqual(new_steer["generation"], 2)
        # The old revision stayed bound to the old work key — the
        # audit trail is immutable.
        self.assertEqual(self._steers(work_key)[0]["state"], "parked")


# ---------------------------------------------------------------------------
# Command surface — typed validation + natural-language proposal
# ---------------------------------------------------------------------------

class TestSteerCommandSurface(SteerFixture):

    def test_missing_criteria_rejected(self):
        self._accept(20)
        out = self.control.handle(self._msg("steer", issue=20))
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "target-required")
        rec = [r for r in self.store.control_rows()
               if r["command_id"] == out["command_id"]][0]
        self.assertIn("criteria", rec["detail"])

    def test_oversized_criteria_rejected(self):
        self._accept(21)
        out = self._steer(21, criteria="x" * 4001)
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "criteria-too-large")

    def test_unknown_mode_rejected(self):
        self._accept(22)
        out = self._steer(22, mode="quantum")
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "unsupported-mode")

    def test_identical_criteria_rejected(self):
        work_key = self._accept(23)
        first = self._steer(23)
        self.assertTrue(first["ok"])
        out = self._steer(23)                    # same criteria again
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "criteria-unchanged")
        self.assertEqual(len(self._steers(work_key)), 1)

    def test_forged_actor_rejected(self):
        self._accept(24)
        out = self._steer(24, user=999999)
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(out["reason"], "forged-actor")

    def test_natural_language_steer_proposal(self):
        work_key = self._accept(25)
        text = (f"please steer issue 25 repo {self.repo_id} "
                "generation 1 - revised criteria: narrow to docs")
        out = self.control.handle({
            "command_id": "nl-steer-1",
            "actor": {"user_id": USER}, "chat": {"chat_id": CHAT},
            "text": text})
        self.assertTrue(out["ok"], out)
        steer = self._steers(work_key)[0]
        # The operator's own sentence is the recorded criteria — the
        # proposal passed the same typed validator as a structured
        # command (A5).
        self.assertEqual(steer["criteria"], text)
        self.assertEqual(steer["state"], "applied")

    def test_command_reference_lists_steer(self):
        from factory_kit.control import commands
        actions = {e["action"] for e in commands.command_reference()}
        self.assertIn("steer", actions)
        ref = commands.reference_text()
        self.assertIn("steer <repo> <issue> <generation>", ref)


if __name__ == "__main__":
    unittest.main()
