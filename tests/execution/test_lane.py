#!/usr/bin/env python3
"""Bounded sequential execution lane — deterministic boundary tests
(issue #9 / Task 2.4, F03).

Each test class maps to an acceptance criterion:

- A1 — one ready task takes the isolated lane + full §6.4 dispatch
  context; a second ready task stays durably queued with a visible
  ``lane-occupied:<work>`` reason.
- A2 — implementation and review are distinct sequential sessions on
  the supported runtime; no merge verb, no role fan-out, no model
  fallback exists in the dispatch path.
- A3 — missing tool/model/capability/skill blocks before dispatch; the
  worker port carries no state-transition authority.
- A4 — two implementation attempts total; 60 active worker minutes;
  24-hour wall; one warn at 80%; park at 100%; unknown usage stays
  unknown.
- A5 — operator retry needs fresh ``authorized_by``; a new audited
  generation mints only after the old is fenced with termination
  confirmed/quarantine resolved; prior attempts stay visible.
- A6 — a 60-second heartbeat gap fences + quarantines before any
  replacement; late heartbeat/result output is durably rejected.
- A7 — a 30-minute idle window records zero model calls;
  ``attempt_started``/``attempt_finished`` events carry the §7.1 fields;
  alerts deduplicate per (kind, identity, severity).
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
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.execution import (  # noqa: E402
    ExecutionLane, HermesKanbanWorker, ScriptedWorker, WorkerError,
    WorkerResult)
from factory_kit.intake import service  # noqa: E402

MANIFEST = ROOT / "docs" / "examples" / "reference.factory-kit.yml"
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


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


class LaneFixture(unittest.TestCase):
    """Ready registration + intake service + lane on a fake clock."""

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

    def _now(self):
        return self.clock[0]

    def _lane(self, worker=None, eff=None, readings=READINGS, **kw):
        configs = {self.repo_id: eff or self.eff}
        return ExecutionLane(
            self.store, self.registrations, configs,
            worker=worker or self.worker,
            workspace_root=Path(self.tmp.name) / "ws",
            readings=readings, now=self._now, **kw)

    def _accept(self, issue, delivery=None, sender="luongnv89"):
        env = {"delivery_id": delivery or f"d-{issue}",
               "channel": "reconciliation", "repo_id": self.repo_id,
               "repository": "luongnv89/money-mind", "issue": issue,
               "action": "reconcile", "sender": sender,
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.service.deliver(env)
        assert ack["outcome"] == "accepted", ack
        return ack["work_key"]

    def _attempts(self, work_key=None, role=None):
        rows = self.store.attempt_record_rows(work_key)
        if role is not None:
            rows = [r for r in rows if r["role"] == role]
        return rows


# ---------------------------------------------------------------------------
# A1 — isolated workspace + full dispatch identity + visible queue
# ---------------------------------------------------------------------------

class TestA1DispatchAndQueue(LaneFixture):

    def test_dispatch_binds_identity_digests_skills_workspace(self):
        work_key = self._accept(42)
        lane = self._lane()
        lane.tick()
        rec = self._attempts(work_key, "implementation")[0]
        self.assertEqual(rec["task_id"], "fk-task-000001")
        self.assertEqual(rec["generation"], 1)
        self.assertEqual(rec["model"], "openai-codex/gpt-6-luna")
        self.assertEqual(rec["runtime"], "hermes-kanban")
        self.assertEqual(rec["config_digest"],
                         self.store.get_work(work_key)["config_digest"])
        self.assertEqual(rec["policy_digest"],
                         self.store.get_work(work_key)["policy_digest"])
        self.assertIn("issue-resolver", rec["skills"])
        self.assertTrue(rec["workspace"].endswith(
            work_key.replace(":", "-").replace("#", "-")))
        self.assertTrue(
            (Path(rec["workspace"])).is_dir())
        # monotonic: first attempt of the first task
        self.assertTrue(rec["attempt_id"].endswith("-a01"))

    def test_second_ready_task_queued_with_visible_reason(self):
        first = self._accept(1)
        second = self._accept(2)
        # Simulate the lane mid-flight: first occupies durably.
        self.store.acquire_lane(first, "fk-task-000001")
        lane = self._lane()
        report = lane.tick()
        self.assertEqual(report["outcome"], "occupied")
        queued = self.store.queue_entry(second)
        self.assertEqual(queued["state"], "queued")
        self.assertEqual(queued["reason"], f"lane-occupied:{first}")
        self.assertEqual(self._attempts(second), [])
        # And once released the queued work drains — FIFO order.
        self.store.release_lane(expected_work=first)
        lane.tick()
        # The lane hands off at the ``reviewed`` boundary — the work
        # stays active until the driver merges it.
        self.assertEqual(self.store.get_work(second)["state"],
                         "active")
        self.assertEqual(self.store.queue_entry(second)["state"],
                         "reviewed")
        self.assertEqual(
            [a["task_id"] for a in self._attempts(second)][0],
            "fk-task-000002")

    def test_tick_max_dispatch_limits_one_dispatch(self):
        """The driver's ``max_dispatch=1`` bound: one work drains per
        pass; the rest stay eligible — ``limited``, never consumed."""
        first = self._accept(1)
        second = self._accept(2)
        lane = self._lane()
        report = lane.tick(max_dispatch=1)
        self.assertEqual(report["outcome"], "limited")
        self.assertEqual(report["dispatched"], [first])
        # Second row untouched: still pending + eligible next pass.
        self.assertEqual(self._attempts(second), [])
        report = lane.tick(max_dispatch=1)
        self.assertEqual(report["dispatched"], [second])
        self.assertEqual(self.store.queue_entry(second)["state"],
                         "reviewed")
        report = lane.tick(max_dispatch=1)
        self.assertEqual(report["outcome"], "idle")
        self.assertEqual(report["dispatched"], [])

    def test_acquire_lane_is_atomic_and_release_is_guarded(self):
        self.store.acquire_lane("w1", "t1")
        occupied = self.store.acquire_lane("w2", "t2")
        self.assertEqual(occupied["outcome"], "occupied")
        self.assertEqual(occupied["occupied_by"], "w1")
        denied = self.store.release_lane(expected_work="w2")
        self.assertEqual(denied["outcome"], "denied")
        self.assertEqual(self.store.lane()["occupied_work"], "w1")
        self.store.release_lane(expected_work="w1")
        self.assertIsNone(self.store.lane()["occupied_work"])

    def test_workspace_is_per_work_isolated_dir(self):
        self._accept(9)
        self._accept(10)
        self._lane().tick()
        ws = {r["work_key"]: Path(r["workspace"])
              for r in self.store.attempt_record_rows()}
        paths = set(ws.values())
        self.assertEqual(len(paths), len(ws))
        for p in paths:
            self.assertTrue(p.is_dir())
            self.assertTrue(str(p).startswith(self.tmp.name))


# ---------------------------------------------------------------------------
# A2 — sequential distinct sessions, bounded fix, no merge/fallback
# ---------------------------------------------------------------------------

class TestA2SequentialSessions(LaneFixture):

    def test_implementation_then_review_distinct_sessions(self):
        work_key = self._accept(3)
        self._lane().tick()
        recs = self._attempts(work_key)
        self.assertEqual([r["role"] for r in recs],
                         ["implementation", "review"])
        self.assertNotEqual(recs[0]["session_id"], recs[1]["session_id"])
        # review strictly after implementation closed
        self.assertIsNotNone(recs[0]["ended"])
        # Approved review is a stage boundary — ``active`` at queue
        # state ``reviewed``, not ``completed`` (the merge owns that).
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "active")
        self.assertEqual(self.store.queue_entry(work_key)["state"],
                         "reviewed")

    def test_one_fix_attempt_then_park(self):
        work_key = self._accept(4)
        worker = ScriptedWorker(script={
            "review": WorkerResult(verdict="changes-requested")})
        self._lane(worker=worker).tick()
        impl = self._attempts(work_key, "implementation")
        self.assertEqual(len(impl), 2)          # initial + one fix, never more
        review = self._attempts(work_key, "review")
        self.assertEqual(len(review), 2)
        self.assertEqual(self.store.get_work(work_key)["state"], "parked")
        self.assertEqual(self.store.get_work(work_key)["parked_reason"],
                         "attempts-exhausted")

    def test_changes_requested_with_fix_then_approve(self):
        work_key = self._accept(5)
        reviews = [WorkerResult(verdict="changes-requested"),
                   WorkerResult(verdict="completed")]
        worker = ScriptedWorker(script={"review": reviews})
        self._lane(worker=worker).tick()
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "active")
        self.assertEqual(self.store.queue_entry(work_key)["state"],
                         "reviewed")
        self.assertEqual(len(self._attempts(work_key, "implementation")),
                         2)

    def test_no_merge_verb_and_no_unsupported_role(self):
        worker = HermesKanbanWorker("/tmp", "o/r", {})
        # The port exposes no merge verb — the merge owner is a
        # separate service the worker never reaches.
        self.assertFalse(hasattr(worker, "merge"))
        ctx_attempt = {"work_key": "w", "task_id": "t", "issue": 1,
                       "generation": 1, "attempt_id": "a",
                       "role": "merge", "session_id": "s",
                       "runtime": "hermes-kanban", "model": "m",
                       "skills": {}, "config_digest": "c",
                       "policy_digest": "p", "workspace": "/w",
                       "limits": {}, "acceptance_ref": "r"}
        from factory_kit.execution.worker import DispatchContext
        with self.assertRaises(WorkerError):
            worker.start(DispatchContext(**ctx_attempt))


# ---------------------------------------------------------------------------
# A3 — pre-dispatch gate blocks; workers cannot commit transitions
# ---------------------------------------------------------------------------

class TestA3PreDispatchBlocks(LaneFixture):

    def _blocked(self, **readings_patch):
        self._accept(6)
        readings = json.loads(json.dumps(READINGS))
        readings.update(readings_patch)
        lane = self._lane(readings=readings)
        lane.tick()
        return self.store.work_rows()[0]

    def test_missing_tool_blocks_before_dispatch(self):
        work = self._blocked(
            tools={"git": "/usr/bin/git", "gh": None, "hermes": "/x/h"})
        self.assertEqual(work["state"], "blocked")
        self.assertIn("dispatch-blocked", work["parked_reason"])
        self.assertIn("missing-tool", work["parked_reason"])
        self.assertEqual(self._attempts(), [])       # nothing dispatched
        self.assertEqual(self.worker.model_calls, 0)

    def test_unavailable_model_blocks(self):
        work = self._blocked(
            models={"openai-codex": {"authenticated": True,
                                     "available": ["other-model"]}})
        self.assertEqual(work["state"], "blocked")
        self.assertIn("model-unavailable", work["parked_reason"])

    def test_unauthenticated_provider_blocks(self):
        work = self._blocked(
            models={"openai-codex": {"authenticated": False,
                                     "available": ["gpt-6-luna"]}})
        self.assertEqual(work["state"], "blocked")
        self.assertIn("model-auth-failed", work["parked_reason"])

    def test_unprobed_provider_fails_closed(self):
        work = self._blocked(models={})
        self.assertEqual(work["state"], "blocked")
        self.assertIn("model-unverified", work["parked_reason"])

    def test_unsatisfied_skill_pin_blocks(self):
        work = self._blocked(
            skills={"issue-resolver": "0.19.0",
                    "issue-pr-review": "9.9.9"})
        self.assertEqual(work["state"], "blocked")
        self.assertIn("skill-not-approved", work["parked_reason"])

    def test_no_workspace_capability_blocks(self):
        eff = effective()
        eff["runtime"]["capabilities"] = {
            "allow": ["workspace.dir-ambient"],
            "deny": []}
        # An ambient-directory capability can never be the lane
        # workspace — both the isolated-capability and ambient checks
        # fire.
        self._accept(6)
        self._lane(eff=eff).tick()
        work = self.store.work_rows()[0]
        self.assertEqual(work["state"], "blocked")
        self.assertIn("unsupported-capability", work["parked_reason"])

    def test_worker_result_is_request_only(self):
        """accept_result is the *only* path worker output can take, and
        it commits nothing by itself — unknown/fenced attempts are
        denied and no ledger state changes."""
        out = self.store.accept_result("r-x", "attempt-that-never-was")
        self.assertEqual(out["accepted"], 0)
        self.assertEqual(self.store.event_rows("result_rejected")[-1]
                         ["reason"], "unknown-attempt")
        # The worker port itself carries no store handle / mutation API.
        self.assertFalse(hasattr(self.worker, "transact"))
        self.assertFalse(hasattr(self.worker, "set_work_state"))


# ---------------------------------------------------------------------------
# A4 — deterministic budgets: attempts, active minutes, wall, warn/stop
# ---------------------------------------------------------------------------

class TestA4Budgets(LaneFixture):

    def _eff_with(self, **limits):
        eff = effective()
        eff["limits"].update(limits)
        return eff

    def test_active_worker_minutes_exhaustion_parks(self):
        work_key = self._accept(7)
        # 50 active minutes on the first session is >80% (warn) and the
        # second session pushes past 60 (exhausted at next checkpoint).
        usage = WorkerResult(verdict="completed", active_seconds=3000,
                             usage={"tokens": 10})
        worker = ScriptedWorker(script={
            "implementation": usage,
            "review": WorkerResult(verdict="changes-requested")})
        # review verdict changes-requested → fix attempt; fix also 50m →
        # cumulative 100m > 60m cap → next checkpoint exhausts.
        self._lane(worker=worker).tick()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertIn("budget-exhausted", work["parked_reason"])
        alerts = self.store.alert_rows("budget")
        self.assertTrue(any(a["severity"] == "high" for a in alerts))
        self.assertTrue(any(a["severity"] == "medium" for a in alerts))

    def test_warn_fires_once_per_budget(self):
        work_key = self._accept(8)
        # Exactly at the 80% warn line on the first attempt, then park
        # the work manually so the loop can't re-warn on later passes.
        worker = ScriptedWorker(script={
            "implementation": WorkerResult(verdict="completed",
                                           active_seconds=2880)})
        lane = self._lane(worker=worker, eff=self._eff_with(
            active_worker_minutes=60))
        # Exhaust wall clock inside review's checkpoint so the run stops
        # after exactly one loop pass beyond the warning state.
        orig_collect = worker.collect

        def collect_then_age(handle):
            out = orig_collect(handle)
            if handle.role == "implementation":
                self.clock[0] += 25 * 3600   # past wall bound
            return out

        worker.collect = collect_then_age
        lane.tick()
        warns = [a for a in self.store.alert_rows("budget")
                 if a["severity"] == "medium"]
        # one warn identity per (work, budget) — exactly one row here
        self.assertEqual(len(warns), 1)
        emit = self.store.emit_alert("budget", warns[0]["identity"],
                                     "medium")
        self.assertTrue(emit["deduplicated"])

    def test_wall_clock_exhaustion(self):
        work_key = self._accept(9)
        worker = ScriptedWorker()

        def collect_age(handle):
            self.clock[0] += 25 * 3600
            return WorkerResult(verdict="completed",
                                active_seconds=60)

        worker.collect = collect_age
        self._lane(worker=worker).tick()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertIn("wall_seconds", work["parked_reason"])

    def test_unknown_usage_never_zero_filled(self):
        work_key = self._accept(10)
        worker = ScriptedWorker(script={
            "implementation": WorkerResult(verdict="completed",
                                           active_seconds=None,
                                           usage=None),
            "review": WorkerResult(verdict="completed",
                                   active_seconds=10)})
        self._lane(worker=worker).tick()
        rec = self._attempts(work_key, "implementation")[0]
        self.assertEqual(rec["usage_state"], "unknown")
        measured, unknown = self.store.work_active_seconds(work_key)
        self.assertEqual(measured, 10)
        self.assertEqual(unknown, 1)

    def test_no_silent_budget_extension_via_limits_snapshot(self):
        work_key = self._accept(11)
        self._lane().tick()
        rec = self._attempts(work_key)[0]
        limits = json.loads(rec["limits"])
        self.assertEqual(limits["implementation_attempts"], 2)
        self.assertEqual(limits["active_worker_seconds"], 3600)
        self.assertEqual(limits["wall_seconds"], 86400)


# ---------------------------------------------------------------------------
# A5 — explicit operator retry: fresh authorization, new generation
# ---------------------------------------------------------------------------

class TestA5OperatorRetry(LaneFixture):

    def _fenced_work(self, issue=20):
        """Run a work row to a fenced state, return its key."""
        work_key = self._accept(issue)
        work = self.store.get_work(work_key)
        self.store.activate_work(work_key)
        self.store.acquire_lane(work_key, work["task_id"])
        begun = self.store.begin_execution_attempt(
            work_key, attempt_id=f"{work['task_id']}-a01",
            role="implementation", task_id=work["task_id"],
            generation=work["generation"], session_id="s1",
            runtime="hermes-kanban", model="m", skills="{}",
            config_digest=work["config_digest"],
            policy_digest=work["policy_digest"], workspace="/w",
            limits="{}", now_epoch=self._now())
        assert begun["outcome"] == "active", begun
        return work_key, work

    def test_retry_denied_while_prior_generation_active(self):
        self._fenced_work()      # active attempt, not yet fenced
        out = self._lane().request_retry(
            self.repo_id, 20, authorized_by="luongnv89")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "prior-generation-active")

    def test_retry_denied_while_termination_uncertain(self):
        work_key, work = self._fenced_work()
        self.store.fence_work(work_key, "cancelled", work["generation"])
        self.store.resolve_fence(work_key, "quarantined", "uncertain")
        out = self._lane().request_retry(
            self.repo_id, 20, authorized_by="luongnv89")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "termination-uncertain")

    def test_retry_denied_without_authorized_by(self):
        work_key, work = self._fenced_work()
        self.store.fence_work(work_key, "cancelled", work["generation"])
        self.store.resolve_fence(work_key, "confirmed")
        with self.assertRaises(registration.RegistrationError):
            self._lane().request_retry(
                self.repo_id, 20, authorized_by="")

    def test_retry_mints_new_generation_preserving_history(self):
        work_key, work = self._fenced_work()
        self.store.fence_work(work_key, "cancelled", work["generation"])
        self.store.resolve_fence(work_key, "confirmed")
        self.store.release_lane(expected_work=work_key)
        self.store.set_work_state(work_key, "parked",
                                  reason="operator-cancelled")
        lane = self._lane()
        out = lane.request_retry(self.repo_id, 20,
                                 authorized_by="luongnv89")
        self.assertEqual(out["outcome"], "retry-authorized")
        self.assertEqual(out["generation"], 2)
        new_key = out["work_key"]
        self.assertNotEqual(new_key, work_key)
        new = self.store.get_work(new_key)
        self.assertEqual(new["generation"], 2)
        self.assertEqual(new["task_state"], "bound")
        # prior generation's attempts/usage still visible
        self.assertEqual(len(self._attempts(work_key)), 1)
        # the new generation is dispatchable
        lane.tick()
        self.assertEqual(self.store.get_work(new_key)["state"],
                         "active")
        self.assertEqual(self.store.queue_entry(new_key)["state"],
                         "reviewed")
        reg = self.registrations.get(self.repo_id)
        self.assertEqual(reg["active_generation"], 2)
        gens = [g["generation"] for g in reg["generations"]]
        self.assertEqual(gens, [1, 2])
        ev = self.store.event_rows("generation_retried")
        self.assertEqual(len(ev), 1)
        self.assertIn("authorized_by=luongnv89", ev[0]["detail"])

    def test_retry_denied_when_issue_never_authorized(self):
        """No prior intake-authorized work row → retrying would mint
        execution authority that never passed the intake gates."""
        out = self._lane().request_retry(
            self.repo_id, 999, authorized_by="luongnv89")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "unknown-work")
        self.assertEqual(self.store.work_rows(), [])

    def test_retry_parked_budget_exhausted_needs_no_fence(self):
        """A parked row has no live worker — the boundary that must
        precede replacement is termination certainty, and there is
        nothing left to terminate. The retry mints a fresh generation
        with a fresh budget under the new work key."""
        work_key = self._accept(21)
        self.store.set_work_state(work_key, "parked",
                                  reason="budget-exhausted")
        lane = self._lane()
        out = lane.request_retry(self.repo_id, 21,
                                 authorized_by="luongnv89")
        self.assertEqual(out["outcome"], "retry-authorized")
        lane.tick()
        self.assertEqual(
            self.store.get_work(out["work_key"])["state"], "active")
        self.assertEqual(
            self.store.queue_entry(out["work_key"])["state"],
            "reviewed")
        # prior parked row + its records stay visible
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")

    def test_quarantine_resolved_then_retry(self):
        work_key, work = self._fenced_work()
        self.store.fence_work(work_key, "cancelled", work["generation"])
        self.store.resolve_fence(work_key, "quarantined", "uncertain")
        self.store.resolve_fence(work_key, "resolved", "operator ok")
        self.store.release_lane(expected_work=work_key)
        self.store.set_work_state(work_key, "parked", reason="cancelled")
        out = self._lane().request_retry(
            self.repo_id, 20, authorized_by="luongnv89")
        self.assertEqual(out["outcome"], "retry-authorized")


# ---------------------------------------------------------------------------
# A6 — heartbeat timeout fences before replacement; late output rejected
# ---------------------------------------------------------------------------

class TestA6HeartbeatFence(LaneFixture):

    def _live_attempt(self, issue=30):
        work_key = self._accept(issue)
        work = self.store.get_work(work_key)
        self.store.activate_work(work_key)
        self.store.acquire_lane(work_key, work["task_id"])
        attempt_id = f"{work['task_id']}-a01"
        self.store.begin_execution_attempt(
            work_key, attempt_id=attempt_id, role="implementation",
            task_id=work["task_id"], generation=work["generation"],
            session_id="s1", runtime="hermes-kanban", model="m",
            skills="{}", config_digest=work["config_digest"],
            policy_digest=work["policy_digest"], workspace="/w",
            limits="{}", now_epoch=self._now())
        return work_key, attempt_id

    def test_heartbeat_accepted_then_stale_sweep_fences(self):
        work_key, attempt_id = self._live_attempt()
        lane = self._lane()
        beat = lane.record_heartbeat(attempt_id)
        self.assertEqual(beat["outcome"], "beat")
        self.clock[0] += 61                       # past the 60s window
        out = lane.sweep()
        self.assertEqual(len(out["fenced"]), 1)
        fence = self.store.fence_state(work_key)
        self.assertTrue(fence["fenced"])
        self.assertEqual(fence["fence_reason"], "heartbeat-lost")
        # worker not tracked → termination uncertain → quarantined
        self.assertEqual(fence["termination"], "quarantined")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "quarantined")
        self.assertIsNone(self.store.lane()["occupied_work"])

    def test_late_heartbeat_and_result_rejected(self):
        work_key, attempt_id = self._live_attempt()
        lane = self._lane()
        self.clock[0] += 61
        lane.sweep()
        late = lane.record_heartbeat(attempt_id)
        self.assertEqual(late["outcome"], "denied")
        result = lane.accept_result("r-late", attempt_id)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["reason"], "generation-fenced")
        rows = self.store.result_rows(attempt_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["accepted"], 0)
        alerts = self.store.alert_rows("fenced-result")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "high")

    def test_confirmed_termination_makes_replacement_eligible(self):
        work_key, attempt_id = self._live_attempt()
        lane = self._lane()
        # Register a tracked handle so terminate() reports confirmed.
        handle = self.worker.handles.get(attempt_id)
        if handle is None:
            from factory_kit.execution.worker import SessionHandle
            handle = SessionHandle("s", attempt_id, "implementation", 0)
            lane._handles[attempt_id] = handle
        self.clock[0] += 61
        lane.sweep()
        fence = self.store.fence_state(work_key)
        self.assertEqual(fence["termination"], "confirmed")
        self.assertTrue(self.store.replacement_eligible(work_key))
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")

    def test_uncertain_termination_blocks_replacement(self):
        work_key, attempt_id = self._live_attempt()
        lane = self._lane()
        self.clock[0] += 61
        lane.sweep()
        self.assertFalse(self.store.replacement_eligible(work_key))
        out = lane.request_retry(self.repo_id, 30,
                                 authorized_by="luongnv89")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "termination-uncertain")
        # operator resolves the quarantine → retry may proceed
        self.store.resolve_fence(work_key, "resolved", "verified dead")
        out = lane.request_retry(self.repo_id, 30,
                                 authorized_by="luongnv89")
        self.assertEqual(out["outcome"], "retry-authorized")


# ---------------------------------------------------------------------------
# A7 — idle audit, event evidence, alert dedup
# ---------------------------------------------------------------------------

class TestA7AuditEvidence(LaneFixture):

    def test_idle_audit_records_zero_model_calls(self):
        lane = self._lane()
        self.assertEqual(lane.tick()["outcome"], "idle")
        self.clock[0] += 30 * 60 + 1
        audit = lane.idle_audit(now_epoch=time.time())
        self.assertTrue(audit["idle"])
        self.assertEqual(audit["model_calls"], 0)
        self.assertEqual(self.worker.model_calls, 0)
        ev = self.store.event_rows("idle_audit")
        self.assertEqual(len(ev), 1)
        self.assertIn('"model_calls": 0', ev[0]["detail"])

    def test_attempt_events_carry_required_fields(self):
        self._accept(50)
        self._lane().tick()
        started = self.store.event_rows("attempt_started")
        finished = self.store.event_rows("attempt_finished")
        self.assertEqual(len(started), 2)
        self.assertEqual(len(finished), 2)
        s = json.loads(started[0]["detail"])
        for key in ("task_id", "attempt_id", "generation", "role",
                    "runtime", "model", "session_id", "workspace"):
            self.assertIn(key, s)
        f = json.loads(finished[0]["detail"])
        for key in ("task_id", "attempt_id", "generation", "role",
                    "duration_s", "verdict", "outcome", "usage"):
            self.assertIn(key, f)

    def test_alerts_deduplicate_per_identity_severity(self):
        self.store.emit_alert("heartbeat", "w1", "high", detail="a")
        dup = self.store.emit_alert("heartbeat", "w1", "high",
                                    detail="b")
        self.assertTrue(dup["deduplicated"])
        self.store.emit_alert("heartbeat", "w1", "medium")
        self.store.emit_alert("budget", "w1", "high")
        rows = self.store.alert_rows()
        self.assertEqual(len(rows), 3)
        # dedup keeps the first detail — no silent overwrite
        first = [r for r in rows
                 if r["kind"] == "heartbeat" and r["severity"] == "high"]
        self.assertEqual(first[0]["detail"], "a")


if __name__ == "__main__":
    unittest.main()
