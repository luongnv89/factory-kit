#!/usr/bin/env python3
"""Restart recovery through Hermes ownership — issue #13 / Task 2.8
(F06, acceptance criteria A1–A3).

The recovery pass runs against the *durable* store — never chat
history, never a second scheduler. Exercises:

- A1 — a restart after durable acceptance (before dispatch, mid-binding
  or mid-attempt) recovers or explicitly parks every known task inside
  the 60-second readiness bound; acknowledged work is retained and
  control/notification identities survive.
- A2 — a crash after remote PR creation but before the response was
  recorded recovers by intent/work identity: one matching remote PR is
  linked, zero or many matches park, and no second remote mutation is
  ever sent.
- A3 — an expired-lease/orphaned attempt is fenced before replacement
  eligibility; uncertain descendant termination quarantines; repeated
  results cannot advance state.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/recovery/test_restart.py
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.config import registration, schema
from factory_kit.durable import store as durable
from factory_kit.execution import ExecutionLane, ScriptedWorker
from factory_kit.intake import service
from factory_kit.publication import (
    OPERATION_PR_PUBLISH,
    PublicationBroker,
    RemoteError,
    ScriptedRemote,
)
from factory_kit.recovery import RecoveryService

MANIFEST = ROOT / "docs" / "examples" / "reference.factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"
ACTOR = "luongnv89"

READINGS = {
    "tools": {"git": "/usr/bin/git", "gh": "/x/gh", "hermes": "/x/hermes"},
    "models": {"openai-codex": {"authenticated": True,
                                "available": ["gpt-6-luna"]}},
    "skills": {"issue-resolver": "0.19.0"},
}

SHA = "a1" * 20


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


class CrashThenHealRemote(ScriptedRemote):
    """Dies once mid-reconciliation — the 'crash after the remote PR
    was created but before the response was recorded' window: the pull
    exists, the link never committed. The kill only fires once a pull
    exists, so the pre-effect read still passes."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.kill_next_find = False

    def find_pull_requests(self, **kw):
        if self.kill_next_find and self.pulls:
            self.kill_next_find = False
            raise RemoteError("controller died mid-reconcile")
        return super().find_pull_requests(**kw)


class FlakyAssociator(service.LocalTaskAssociator):
    """Hermes-side binding endpoint down — publish raises until the
    test heals it, so task association stays 'pending'."""

    def __init__(self):
        self.down = True

    def publish(self, task_id, work_key):
        if self.down:
            raise RuntimeError("kanban unreachable")


class RecoveryFixture(unittest.TestCase):
    """Registered repo + intake + lane + broker + recovery service on a
    shared fake clock. ``_restart`` drops every in-memory handle and
    reopens the durable store — the honest crash boundary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.eff = effective()
        self.repo_id = self.eff["identity"]["repo_id"]
        self.full_name = (f"{self.eff['identity']['owner']}/"
                          f"{self.eff['identity']['name']}")
        self.clock = [time.time()]
        self.registrations = registration.RegistrationStore(
            Path(self.tmp.name) / "registration.json")
        self.registrations.register(self.eff, readiness=READY,
                                    supported_versions=VERSIONS)
        self.store_path = Path(self.tmp.name) / "in.db"
        self.alerts = []
        self.associator = None
        # The remote *is* GitHub — it survives a controller restart, so
        # one instance lives across every ``_restart`` rebuild.
        self.remote = CrashThenHealRemote(self.repo_id, self.full_name)
        self._build()

    def _now(self):
        return self.clock[0]

    def _build(self, **service_kw):
        service_kw.setdefault("alert_sink", self.alerts)
        old = getattr(self, "store", None)
        if old is not None:
            old.close()
        self.store = durable.IntakeStore(self.store_path)
        self.service = service.IntakeService(
            self.store, self.registrations, {self.repo_id: self.eff},
            signing_secret="s", opt_in_label="factory-kit",
            associator=self.associator)
        self.worker = ScriptedWorker()
        self.lane = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=self.worker,
            workspace_root=Path(self.tmp.name) / "ws",
            readings=READINGS, now=self._now)
        self.broker = PublicationBroker(
            self.store, self.remote, self.registrations, self.eff,
            clock=self._now, alert_sink=self.alerts)
        self.recovery = RecoveryService(
            self.store, self.registrations, {self.repo_id: self.eff},
            intake=self.service, lane=self.lane, broker=self.broker,
            remote=self.remote, now=self._now, **service_kw)

    def _restart(self):
        """Process died and came back: a fresh store connection and
        fresh service instances — only durable rows survived."""
        self.store.close()
        self._build()

    def _accept(self, issue=42, delivery=None, sender=ACTOR):
        env = {"delivery_id": delivery or f"d-{issue}",
               "channel": "reconciliation", "repo_id": self.repo_id,
               "repository": self.full_name, "issue": issue,
               "action": "reconcile", "sender": sender,
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.service.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        self.registrations.record_work(self.repo_id, work_key)
        return work_key

    def _live_attempt(self, work_key):
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
        return attempt_id

    def _recovered_events(self, work_key=None):
        rows = self.store.event_rows("recovery_completed")
        if work_key is not None:
            rows = [r for r in rows if r["work_key"] == work_key]
        return rows


# ---------------------------------------------------------------------------
# A1 — restart recovers or parks every known task inside the bound
# ---------------------------------------------------------------------------

class TestA1RestartRecovery(RecoveryFixture):

    def test_accepted_before_dispatch_resumes(self):
        work_key = self._accept(42)
        self._restart()
        report = self.recovery.recover()
        self.assertTrue(report["within_deadline"])
        self.assertLess(report["duration_s"], 60.0)
        self.assertEqual([r["work_key"] for r in report["resumed"]],
                         [work_key])
        work = self.store.get_work(work_key)
        # Acknowledged work is retained — same identity, same task.
        self.assertEqual(work["state"], "pending")
        self.assertEqual(work["task_state"], "bound")
        self.assertEqual(work["task_id"], "fk-task-000001")
        queued = self.store.queue_entry(work_key)
        self.assertEqual(queued["state"], "queued")
        self.assertEqual(queued["reason"], "restart-recovered")
        ev = self._recovered_events(work_key)[-1]
        detail = json.loads(ev["detail"])
        self.assertEqual(detail["task_id"], "fk-task-000001")
        self.assertEqual(detail["source"], "restart")
        self.assertEqual(detail["action"], "resumed")

    def test_pending_task_binding_repairs_exactly_once(self):
        # Crash between accept and the association publish: the binding
        # is pending, the reserved ID stable — repair binds it, never
        # mints a second task.
        self.associator = FlakyAssociator()
        self._build()
        work_key = self._accept(7)
        work = self.store.get_work(work_key)
        self.assertEqual(work["task_state"], "pending")
        reserved = work["task_id"]
        # Heal the kanban side, then the restart recovery pass runs.
        self._restart()
        report = self.recovery.recover()
        # The flaky associator is still down → the pass defers honestly.
        self.assertEqual([d["work_key"] for d in report["deferred"]],
                         [work_key])
        self.assertEqual(self.store.get_work(work_key)["task_state"],
                         "pending")
        # Hermes comes back; the next pass binds the SAME reserved id.
        self.associator.down = False
        self._restart()
        report = self.recovery.recover()
        self.assertEqual(report["bindings"]["repaired"],
                         [{"work_key": work_key, "task_id": reserved}])
        work = self.store.get_work(work_key)
        self.assertEqual(work["task_state"], "bound")
        self.assertEqual(work["task_id"], reserved)
        self.assertEqual([r["work_key"] for r in report["resumed"]],
                         [work_key])
        # No second task exists anywhere.
        self.assertEqual(self.store.counts()["work"], 1)
        self.assertTrue(all(w["task_id"] == reserved
                            for w in self.store.work_rows()))

    def test_superseded_generation_parks_explicitly(self):
        work_key = self._accept(5)
        # A fresh authorized generation supersedes the work's — the
        # restart pass must park it visibly, never re-dispatch it.
        self.registrations.authorize_generation(
            self.repo_id, self.eff, authorized_by=ACTOR,
            reason="operator-retry")
        self.assertEqual(
            self.registrations.get(self.repo_id)["active_generation"],
            2)
        self._restart()
        report = self.recovery.recover()
        self.assertEqual([p["work_key"] for p in report["parked"]],
                         [work_key])
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertEqual(work["parked_reason"], "generation-superseded")
        self.assertEqual(
            [a["kind"] for a in self.store.alert_rows()],
            ["recovery-parked"])

    def test_control_and_notification_identities_survive(self):
        """A3/A7's durable surfaces: a pause boundary, a control record
        and an alert row all committed before the crash must be intact
        afterwards — recovery never replays chat to rebuild them."""
        work_key = self._accept(9)
        self.store.set_pause(work_key, "paused", stage="implementation")
        self.store.record_control(
            "cmd-1", received_at="t", committed_at="t",
            actor_ref=ACTOR, chat_ref="tg:1", action="pause",
            repo_id=self.repo_id, issue=9, generation=1,
            work_key=work_key, outcome="applied", reason="operator",
            seq=self.store.next_seq())
        self.store.emit_alert("heartbeat", work_key, "high",
                              detail="pre-restart alert")
        n_events = self.store.counts()["events"]
        self._restart()
        self.recovery.recover()
        # Pause boundary persisted — the lane must still hold the work.
        self.assertEqual(
            self.store.pause_info(work_key)["pause_state"], "paused")
        self.assertEqual(
            self.lane.eligible_work(), [])
        # The control row's committed outcome is unchanged.
        ctrl = self.store.find_control("cmd-1")
        self.assertEqual(ctrl["outcome"], "applied")
        self.assertEqual(ctrl["actor_ref"], ACTOR)
        # The durable alert survived verbatim (local visibility, A7).
        alerts = self.store.alert_rows("heartbeat")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["detail"], "pre-restart alert")
        # Recovery produced no new intake decision — nothing replayed.
        self.assertEqual(self.store.counts()["work"], 1)
        self.assertEqual(self.store.counts()["deliveries"], 1)
        self.assertGreaterEqual(self.store.counts()["events"], n_events)


# ---------------------------------------------------------------------------
# A2 — crash after remote PR creation; identity-based rediscovery
# ---------------------------------------------------------------------------

class TestA2RemoteIdentity(RecoveryFixture):

    def _request(self, work_key):
        return {"work_key": work_key,
                "operation": OPERATION_PR_PUBLISH,
                "target": "factory-kit/impl-42", "base": "main",
                "title": "implement issue",
                "repository": {"repo_id": self.repo_id,
                               "full_name": self.full_name},
                "expected_revision": SHA,
                "actor_ref": ACTOR,
                "claim_expires_epoch": self.clock[0] + 600}

    def test_existing_pr_discovered_by_identity_never_recreated(self):
        work_key = self._accept(42)
        self.store.activate_work(work_key)
        # The publish's remote effect lands, then the process dies
        # mid-reconciliation — before the link commits.
        self.remote.faults["pr-publish"] = "crash-after-create"
        self.remote.kill_next_find = True
        with self.assertRaises(RemoteError):
            self.broker.publish(work_key, self._request(work_key))
        # The durable intent is still pending; exactly one remote PR.
        pending = self.store.pending_intents()
        self.assertEqual(len(pending), 1)
        self.assertEqual(len(self.remote.pulls), 1)
        self.remote.kill_next_find = False
        self._restart()
        self.recovery.recover()
        # Identity-keyed rediscovery linked the existing PR — no second
        # remote mutation was ever sent.
        pr_number = next(iter(self.remote.pulls))
        self.assertEqual(len(self.remote.pulls), 1)
        self.assertEqual(
            [c["op"] for c in self.remote.calls].count("pr-publish"),
            1)
        work = self.store.get_work(work_key)
        self.assertEqual(work["linked_pr"], pr_number)
        intent = self.store.pending_intents()
        self.assertEqual(intent, [])
        linked = self.store.intent_rows(work_key)[0]
        self.assertEqual(linked["state"], "linked")
        self.assertEqual(linked["remote_ref"], pr_number)
        ev = [e for e in self._recovered_events(work_key)
              if json.loads(e["detail"])["action"] == "resumed"]
        self.assertTrue(ev)
        detail = json.loads(ev[0]["detail"])
        self.assertEqual(detail["reused_identity"], pr_number)

    def test_duplicate_remote_prs_park_and_alert(self):
        work_key = self._accept(42)
        self.store.activate_work(work_key)
        # Two remote PRs already match the identity — ambiguous.
        for _ in range(2):
            self.remote.open_pr(
                "factory-kit/impl-42", "main", "t",
                {"work_key": work_key,
                 "authority_key":
                     self.store.get_work(work_key)["authority_key"],
                 "generation": 1})
        self.store.insert_intent(
            "pub-dup", work_key=work_key,
            task_id=self.store.get_work(work_key)["task_id"],
            generation=1,
            authority_key=self.store.get_work(work_key)[
                "authority_key"],
            repo_id=self.repo_id, operation=OPERATION_PR_PUBLISH,
            expected_revision=SHA, target="factory-kit/impl-42",
            actor_ref=ACTOR, state="recorded")
        self._restart()
        self.recovery.recover()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertEqual(work["parked_reason"], "duplicate-remote-pr")
        self.assertIsNone(work["linked_pr"])
        self.assertTrue(self.store.alert_rows("duplicate-remote-pr"))
        # No new remote mutation — the two pre-existing PRs are all.
        self.assertEqual(len(self.remote.pulls), 2)

    def test_absent_remote_identity_parks_ambiguous(self):
        work_key = self._accept(42)
        self.store.activate_work(work_key)
        self.store.insert_intent(
            "pub-none", work_key=work_key,
            task_id=self.store.get_work(work_key)["task_id"],
            generation=1,
            authority_key=self.store.get_work(work_key)[
                "authority_key"],
            repo_id=self.repo_id, operation=OPERATION_PR_PUBLISH,
            expected_revision=SHA, target="factory-kit/impl-42",
            actor_ref=ACTOR, state="recorded")
        self._restart()
        self.recovery.recover()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertEqual(work["parked_reason"], "ambiguous-outcome")
        self.assertEqual(len(self.remote.pulls), 0)


# ---------------------------------------------------------------------------
# A3 — fencing, uncertain termination, stale results cannot advance
# ---------------------------------------------------------------------------

class TestA3Fencing(RecoveryFixture):

    def test_orphaned_attempt_fenced_and_quarantined(self):
        work_key = self._accept(30)
        self._live_attempt(work_key)
        self.clock[0] += 61             # heartbeat window lapsed
        self._restart()
        report = self.recovery.recover()
        fence = self.store.fence_state(work_key)
        self.assertTrue(fence["fenced"])
        # Post-restart there is no live handle — termination is
        # uncertain, so the generation quarantines and replacement is
        # not eligible.
        self.assertEqual(fence["termination"], "quarantined")
        self.assertFalse(self.store.replacement_eligible(work_key))
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "quarantined")
        self.assertIsNone(self.store.lane()["occupied_work"])
        # The sweep's own report carried the quarantine evidence — the
        # lane's existing crash fences did this, not a new code path.
        self.assertEqual(
            [f["work_key"] for f in report["sweep"]["fenced"]],
            [work_key])
        ev = self.store.event_rows("work_fenced")
        self.assertTrue(ev)

    def test_repeated_result_cannot_advance_fenced_generation(self):
        work_key = self._accept(31)
        attempt_id = self._live_attempt(work_key)
        self.clock[0] += 61
        self._restart()
        self.recovery.recover()
        # A late/duplicated result from the dead generation is recorded
        # but cannot advance state — and a heartbeat cannot resurrect.
        result = self.lane.accept_result("r-late", attempt_id)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["reason"], "generation-fenced")
        late_beat = self.lane.record_heartbeat(attempt_id)
        self.assertEqual(late_beat["outcome"], "denied")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "quarantined")

    def test_live_heartbeat_resumes_through_restart(self):
        work_key = self._accept(32)
        attempt_id = self._live_attempt(work_key)
        self.lane.record_heartbeat(attempt_id)
        self._restart()                 # controller restarts; the
        report = self.recovery.recover()  # Hermes worker still beats
        self.assertEqual([r["work_key"] for r in report["resumed"]],
                         [work_key])
        self.assertEqual(report["resumed"][0]["reason"],
                         "heartbeat-live")
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "active")
        fence = self.store.fence_state(work_key)
        self.assertFalse(fence["fenced"] if fence else False)

    def test_active_without_attempts_redispatches(self):
        # Crash in the activate→claim window: the attempt provably
        # never ran, so the lane's retry boundary is honest.
        work_key = self._accept(33)
        self.store.activate_work(work_key)
        self._restart()
        report = self.recovery.recover()
        self.assertEqual([r["work_key"] for r in report["resumed"]],
                         [work_key])
        self.assertEqual(report["resumed"][0]["reason"],
                         "dispatch-not-started")

    def test_attempts_ended_mid_pipeline_park(self):
        # The interrupted stage's commit boundary was never reached —
        # park visibly rather than guess a resume point.
        work_key = self._accept(34)
        attempt_id = self._live_attempt(work_key)
        self.store.finish_execution_attempt(
            attempt_id, verdict="failed", outcome="failed",
            active_seconds=5, usage={"t": 1}, duration_s=5)
        self._restart()
        report = self.recovery.recover()
        self.assertEqual([p["work_key"] for p in report["parked"]],
                         [work_key])
        work = self.store.get_work(work_key)
        self.assertEqual(work["parked_reason"], "recovery-interrupted")

    def _post_lane(self, issue, queue_state):
        """Ended attempts + a durable post-lane queue boundary — the
        reviewed/awaiting-approval resume shape a restarted driver
        must honor."""
        work_key = self._accept(issue)
        attempt_id = self._live_attempt(work_key)
        self.store.finish_execution_attempt(
            attempt_id, verdict="completed", outcome="completed",
            active_seconds=5, usage={"t": 1}, duration_s=5)
        self.store.release_lane(expected_work=work_key)
        with self.store.transact() as tx:
            tx.enqueue_work(work_key, tx.next_seq(),
                            state=queue_state, reason=queue_state)
        return work_key

    def test_reviewed_boundary_resumes_for_driver(self):
        """The lane handed off at ``reviewed`` — restart must resume
        the work in place; the driver owns publication onward."""
        work_key = self._post_lane(35, "reviewed")
        self._restart()
        report = self.recovery.recover()
        resumed = [r for r in report["resumed"]
                   if r["work_key"] == work_key]
        self.assertEqual(resumed[0]["reason"], "post-lane-boundary")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "active")
        self.assertEqual(self.store.queue_entry(work_key)["state"],
                         "reviewed")
        events = self._recovered_events(work_key)
        self.assertIn("post-lane-boundary", events[0]["reason"])

    def test_awaiting_approval_boundary_resumes(self):
        work_key = self._post_lane(36, "awaiting-approval")
        self._restart()
        report = self.recovery.recover()
        resumed = [r for r in report["resumed"]
                   if r["work_key"] == work_key]
        self.assertEqual(resumed[0]["reason"], "post-lane-boundary")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "active")


if __name__ == "__main__":
    unittest.main()
