#!/usr/bin/env python3
"""Periodic GitHub reconciliation — issue #13 / Task 2.8 (F06,
acceptance criteria A4–A7, PRD §5.1/§7.3).

The pass runs on an external cadence (``hermes cron``) — the service
only answers "due or not" from durable state. Exercises:

- A4 — a dropped webhook's eligible work is discovered through the
  same intake pipeline inside the bound; webhook/poll overlap makes
  one task; a revoked opt-in or a closed issue revokes the work.
- A5 — GitHub down/rate-limited fixtures back off bounded (≤120s
  self-imposed, ≤300s nominal cap, upstream ``Retry-After`` may
  exceed); remote state is marked fresh/unknown/stale.
- A6 — unexpected terminal PR state, human head movement and an
  irreconcilable remote identity park visibly without new remote
  effects; ``status()`` reports liveness + last success honestly.
- A7 — five minutes without a successful observation alerts once per
  episode (deduplicated medium), marks the remote stale and stops
  evidence-dependent completion — across restarts, and even when the
  operator channel itself is down.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/recovery/test_reconcile.py
"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.config import registration, schema
from factory_kit.durable import store as durable
from factory_kit.intake import service
from factory_kit.publication import (
    OPERATION_PR_PUBLISH,
    PublicationBroker,
    ScriptedRemote,
)
from factory_kit.publication.remote import _retry_after_hint
from factory_kit.recovery import (
    BACKOFF_CAP_S,
    DISCOVERY_BOUND_S,
    NOMINAL_INTERVAL_S,
    STALE_REMOTE_S,
    BackoffPolicy,
    RecoveryService,
    ScriptedIssueSource,
)
from factory_kit.verification import VerificationService

MANIFEST = ROOT / ".factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"
ACTOR = "luongnv89"
SHA = "a1" * 20
HEAD = "factory-kit/impl-42"


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


def issue(number, *, labels=("factory-kit",), state="open",
          updated="2026-10-05T01:00:00Z", author=ACTOR, body=BODY):
    """One scripted GitHub issue row."""
    return {"title": "t", "body": body,
            "labels": [{"name": l} for l in labels],
            "state": state, "updated_at": updated,
            "author": {"login": author}}


class ReconcileFixture(unittest.TestCase):
    """Registered repo + intake + broker + scripted remote + scripted
    issue source + recovery service, all on one fake clock."""

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
        self.issue_rows = {}
        self.faults = {}
        # The remote + issue source *are* GitHub — they survive a
        # controller restart, so one instance lives across rebuilds
        # (their issues/faults dicts rebind to the fixture's).
        self.remote = ScriptedRemote(self.repo_id, self.full_name)
        self.source = ScriptedIssueSource(self.repo_id, self.full_name)
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
            signing_secret="s", opt_in_label="factory-kit")
        # Both scripted ports copy their dicts at construction — rebind
        # so tests keep scripting GitHub state through the fixture.
        self.remote.faults = self.faults
        self.source.faults = self.faults
        self.source.issues = self.issue_rows
        self.broker = PublicationBroker(
            self.store, self.remote, self.registrations, self.eff,
            clock=self._now, alert_sink=self.alerts)
        self.verifier = VerificationService(
            self.store, now=self._now)
        self.recovery = RecoveryService(
            self.store, self.registrations, {self.repo_id: self.eff},
            intake=self.service, broker=self.broker,
            verification=self.verifier, issue_source=self.source,
            remote=self.remote, now=self._now, **service_kw)

    def _restart(self):
        self.store.close()
        self._build()

    def _accept(self, issue_no=42, sender=ACTOR):
        env = {"delivery_id": f"d-{issue_no}",
               "channel": "reconciliation", "repo_id": self.repo_id,
               "repository": self.full_name, "issue": issue_no,
               "action": "reconcile", "sender": sender,
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.service.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        self.registrations.record_work(self.repo_id, work_key)
        return work_key

    def _linked(self, work_key, pr="100", head_sha=SHA):
        """The durable linked-PR state a completed publish would have
        left: the intent row bound to the remote identity + the
        ``linked_pr`` + the remote pull itself at ``head_sha``."""
        work = self.store.get_work(work_key)
        self.store.insert_intent(
            "pub-linked", work_key=work_key, task_id=work["task_id"],
            generation=work["generation"],
            authority_key=work["authority_key"], repo_id=self.repo_id,
            operation=OPERATION_PR_PUBLISH,
            expected_revision=head_sha, target=HEAD,
            actor_ref=ACTOR, state="linked", remote_ref=str(pr))
        self.store.link_pr(work_key, pr)
        self.remote.branches[HEAD] = head_sha
        self.remote.pulls[str(pr)] = {
            "number": str(pr), "head": HEAD, "base": "main",
            "title": "t", "state": "open",
            "url": f"https://example.test/{self.full_name}/pull/{pr}",
            "identity": {"authority_key": work["authority_key"],
                         "work_key": work_key}}
        return str(pr)

    def _verified_evidence(self, work_key, pr="100"):
        with self.store.transact() as tx:
            tx.record_evidence(
                "ev-1", work_key=work_key, seq=tx.next_seq(),
                pr_number=pr, head_sha=SHA, base_name="main",
                base_sha=self.remote.branches["main"],
                status="verified", reason="all-checks-pass")


# ---------------------------------------------------------------------------
# A4 — dropped-webhook discovery, overlap, revocation, interval bound
# ---------------------------------------------------------------------------

class TestA4Discovery(ReconcileFixture):

    def test_poll_discovers_dropped_webhook_inside_bound(self):
        self.issue_rows[42] = issue(42)
        report = self.recovery.tick()
        self.assertEqual(report["accepted"],
                         [{"work_key":
                           self.store.work_rows()[0]["work_key"],
                           "task_id": "fk-task-000001"}])
        self.assertEqual(self.store.counts()["work"], 1)
        work = self.store.work_rows()[0]
        self.assertEqual(work["state"], "pending")
        self.assertEqual(work["task_state"], "bound")
        # The pass observed the remote — the watermark is fresh and the
        # next pass is scheduled inside the nominal ≤60s interval.
        wm = self.store.reconcile_watermark()
        self.assertEqual(wm["remote_state"], "fresh")
        self.assertAlmostEqual(
            wm["next_due_epoch"] - wm["last_success_epoch"],
            NOMINAL_INTERVAL_S)
        self.assertLessEqual(
            wm["next_due_epoch"] - self.clock[0], DISCOVERY_BOUND_S)

    def test_webhook_and_poll_overlap_converge_one_task(self):
        # The webhook already accepted the issue; the poll's
        # re-observation converges on the same durable identity.
        work_key = self._accept(42)
        self.issue_rows[42] = issue(42)
        report = self.recovery.tick()
        self.assertEqual(report["accepted"], [])
        # The poll row reconciles; the targeted re-observation of the
        # known issue reconciles too — both land on the ONE row.
        self.assertGreaterEqual(report["reconciled"], 1)
        self.assertEqual(self.store.counts()["work"], 1)
        self.assertEqual(
            {w["task_id"] for w in self.store.work_rows()},
            {"fk-task-000001"})
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "pending")

    def test_revoked_opt_in_via_poll_parks_work(self):
        work_key = self._accept(42)
        # The label is gone at the next observation — same revocation
        # path a webhook would take (A4).
        self.issue_rows[42] = issue(42, labels=())
        report = self.recovery.tick()
        # Both the set-level poll row and the targeted re-observation
        # deliver the revocation — the first lands, the second
        # dedups/reconciles on the same durable identity.
        self.assertTrue(any(r["work_key"] == work_key
                            for r in report["revoked"]))
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertEqual(work["parked_reason"], "opt-in-revoked")

    def test_closed_issue_revokes_via_targeted_observe(self):
        work_key = self._accept(42)
        # The issue closed — invisible to the open-issue poll, so the
        # targeted re-observation is what discovers it.
        self.issue_rows[42] = issue(42, state="closed")
        report = self.recovery.tick()
        self.assertEqual(report["polled"], 0)       # open-only set
        self.assertTrue(any(
            r["work_key"] == work_key and r["reason"] == "issue-closed"
            for r in report["revoked"]))
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")

    def test_interval_clamped_and_due_persisted_across_restart(self):
        # A configured interval larger than the §5.1 bound clamps.
        self._build(interval_s=3600)
        self.assertEqual(self.recovery.interval_s, NOMINAL_INTERVAL_S)
        report = self.recovery.tick()
        self.assertEqual(report["observed"], True)
        # Not-due ticks are honest — the durable schedule survives a
        # restart too (a process bounce never skips or replays a pass).
        self.assertEqual(self.recovery.tick()["outcome"], "not-due")
        self._restart()
        self.assertEqual(self.recovery.tick()["outcome"], "not-due")
        self.clock[0] += NOMINAL_INTERVAL_S + 1
        report = self.recovery.tick()
        self.assertIn("observed", report)

    def test_revoked_registration_prohibits_dispatch(self):
        work_key = self._accept(42)
        # A registration-side revocation must forbid new effects the
        # same way a webhook-side one does.
        reg = self.registrations.get(self.repo_id)
        reg["work"][work_key]["state"] = "parked"
        self.registrations._save()
        report = self.recovery.recover()
        self.assertEqual([p["work_key"] for p in report["parked"]],
                         [work_key])
        self.assertEqual(
            self.store.get_work(work_key)["parked_reason"],
            "revoked-registration")


# ---------------------------------------------------------------------------
# A5 — bounded retry/backoff, upstream guidance, state marking
# ---------------------------------------------------------------------------

class TestA5Backoff(ReconcileFixture):

    def test_backoff_policy_bounds(self):
        pol = BackoffPolicy()
        self.assertEqual(pol.delay(1), 1.0)
        self.assertEqual(pol.delay(2), 2.0)
        self.assertEqual(pol.delay(9), 256.0)
        # the nominal five-minute cap — never exceeded on our own
        self.assertEqual(pol.delay(20), BACKOFF_CAP_S)
        self.assertEqual(BACKOFF_CAP_S, 300.0)
        # upstream guidance may exceed the cap — and smaller guidance
        # never shortens the computed wait.
        self.assertEqual(pol.delay(1, 900), 900.0)
        self.assertEqual(pol.delay(9, 30), 256.0)
        self.assertEqual(pol.delay(1, "junk"), 1.0)

    def test_remote_down_retries_inside_discovery_bound(self):
        self.faults["poll"] = "down"
        self.faults["observe"] = "down"
        self.faults["pr-read"] = {"kind": "down"}
        report = self.recovery.tick()
        self.assertFalse(report["observed"])
        self.assertEqual(report["remote_state"], "unknown")
        # Self-imposed backoff stays inside the 120-second discovery
        # bound (§5.1) — failures escalate but never gap the schedule.
        for _ in range(20):
            self.clock[0] += report["backoff_s"]
            report = self.recovery.tick()
            self.assertFalse(report["observed"])
            self.assertLessEqual(report["backoff_s"],
                                 DISCOVERY_BOUND_S)
            self.assertLessEqual(report["backoff_s"], BACKOFF_CAP_S)

    def test_retry_after_guidance_overrides_cap(self):
        # GitHub says "not for 15 minutes" — upstream-directed backoff
        # wins over the nominal five-minute cap (§5.1).
        self.faults["poll"] = {"kind": "rate-limit", "retry_after": 900}
        report = self.recovery.tick()
        self.assertFalse(report["observed"])
        self.assertEqual(report["backoff_s"], 900.0)
        self.assertAlmostEqual(
            self.store.reconcile_watermark()["next_due_epoch"],
            self.clock[0] + 900)
        # And inside the window a tick is honest about not being due —
        # no blind repeat mutation ever goes out (A5).
        self.clock[0] += 100
        self.assertEqual(self.recovery.tick()["outcome"], "not-due")

    def test_retry_after_hint_parsed_from_cli_stderr(self):
        self.assertEqual(_retry_after_hint("Retry-After: 30\n"), 30.0)
        self.assertEqual(
            _retry_after_hint("API rate limit; retry after 120 seconds"),
            120.0)
        self.assertIsNone(_retry_after_hint("no guidance"))
        self.assertIsNone(_retry_after_hint(None))

    def test_remote_error_carries_retry_after(self):
        self.faults["poll"] = {"kind": "rate-limit", "retry_after": 45}
        try:
            self.source.poll_issues()
        except Exception as exc:
            self.assertEqual(exc.retry_after, 45)
        else:  # pragma: no cover - fault must fire
            self.fail("expected RemoteError")

    def test_unknown_and_stale_states_marked_durably(self):
        wm = self.store.reconcile_watermark()
        self.assertIsNone(wm)                    # never observed
        self.faults["poll"] = "down"
        self.recovery.tick()
        wm = self.store.reconcile_watermark()
        self.assertEqual(wm["remote_state"], "unknown")
        self.assertEqual(wm["consecutive_failures"], 1)
        # Recovery: a successful pass marks fresh and resets counters —
        # the backoff-scheduled next-due gates the tick first.
        self.faults.pop("poll")
        self.issue_rows[42] = issue(42)
        self.clock[0] += wm["next_due_epoch"] - self.clock[0] + 1
        self.recovery.tick()
        wm = self.store.reconcile_watermark()
        self.assertEqual(wm["remote_state"], "fresh")
        self.assertEqual(wm["consecutive_failures"], 0)
        self.assertIsNotNone(wm["last_success_epoch"])


# ---------------------------------------------------------------------------
# A6 — remote drift parks visibly; observability
# ---------------------------------------------------------------------------

class TestA6Drift(ReconcileFixture):

    def test_human_head_movement_parks_and_stales_evidence(self):
        work_key = self._accept(42)
        self.issue_rows[42] = issue(42)
        pr = self._linked(work_key)
        self._verified_evidence(work_key, pr)
        # A human push moved the published head — the work parks, the
        # verified evidence stales, and nothing is ever force-pushed
        # back over the human's commit (A6).
        self.remote.branches[HEAD] = "cc" * 20
        report = self.recovery.tick()
        self.assertTrue(report["parked"])
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertTrue(work["parked_reason"].startswith(
            "remote-head-moved"))
        self.assertTrue(self.store.alert_rows("remote-drift"))
        latest = self.store.latest_evidence(work_key)
        self.assertEqual(latest["status"], "stale")
        self.assertEqual(latest["reason"], "remote-head-moved")
        self.assertEqual(len(self.remote.pulls), 1)

    def test_same_sha_head_branch_move_parks(self):
        work_key = self._accept(42)
        self.issue_rows[42] = issue(42)
        pr = self._linked(work_key)
        # The PR's head was repointed to a *different* branch carrying
        # the same SHA — the SHA check alone cannot see it; the durable
        # expectation is the serialized intent's recorded target (A6).
        self.remote.branches["human-takeover"] = SHA
        self.remote.pulls[pr]["head"] = "human-takeover"
        report = self.recovery.tick()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertTrue(work["parked_reason"].startswith(
            "remote-head-moved"))
        self.assertTrue(self.store.alert_rows("remote-drift"))
        # Nothing was pushed back — drift is parked, never repaired.
        self.assertEqual(
            [c["op"] for c in self.remote.calls].count("pr-publish"),
            0)

    def test_human_base_movement_parks_and_stales_evidence(self):
        work_key = self._accept(42)
        self.issue_rows[42] = issue(42)
        pr = self._linked(work_key)
        self._verified_evidence(work_key, pr)
        # A human retargeted the PR's base — the durable expectation is
        # the verified observation's recorded base (A6).
        self.remote.pulls[pr]["base"] = "develop"
        self.remote.branches["develop"] = "dd" * 20
        report = self.recovery.tick()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertTrue(work["parked_reason"].startswith(
            "remote-base-moved"))
        self.assertTrue(self.store.alert_rows("remote-drift"))
        latest = self.store.latest_evidence(work_key)
        self.assertEqual(latest["status"], "stale")
        self.assertEqual(latest["reason"], "remote-base-moved")

    def test_base_sha_move_same_name_parks(self):
        work_key = self._accept(42)
        self.issue_rows[42] = issue(42)
        pr = self._linked(work_key)
        self._verified_evidence(work_key, pr)
        # A human force-push moved the base revision — same name, new
        # SHA; the verified observation's recorded base SHA is the
        # durable expectation (A6).
        self.remote.branches["main"] = "dd" * 20
        report = self.recovery.tick()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertTrue(work["parked_reason"].startswith(
            "remote-base-moved"))
        self.assertTrue(self.store.alert_rows("remote-drift"))

    def test_unexpected_terminal_pr_state_parks(self):
        work_key = self._accept(42)
        self.issue_rows[42] = issue(42)
        pr = self._linked(work_key)
        self.remote.pr_states[pr] = "MERGED"
        self.recovery.tick()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertEqual(work["parked_reason"],
                         "remote-terminal:merged")
        self.assertTrue(self.store.alert_rows("remote-terminal"))

    def test_vanished_pr_is_irreconcilable(self):
        work_key = self._accept(42)
        self.issue_rows[42] = issue(42)
        pr = self._linked(work_key)
        del self.remote.pulls[pr]
        self.recovery.tick()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertEqual(work["parked_reason"], "remote-irreconcilable")
        alert = self.store.alert_rows("remote-irreconcilable")[0]
        self.assertEqual(alert["severity"], "high")
        # No replacement PR was created — irreconcilable means stop.
        self.assertEqual(
            [c["op"] for c in self.remote.calls].count("pr-publish"),
            0)

    def test_status_reports_liveness_and_last_success(self):
        self.issue_rows[42] = issue(42)
        self.recovery.tick()
        status = self.recovery.status()
        self.assertEqual(status["remote_state"], "fresh")
        self.assertIsNotNone(status["last_success_epoch"])
        self.assertEqual(status["interval_s"], NOMINAL_INTERVAL_S)
        self.assertIn("process_started_epoch", status)
        self.assertIn("non_terminal_work", status)
        self.assertIn("alerts", status)


# ---------------------------------------------------------------------------
# A7 — five-minute stale rule, dedup'd alert, restart-surviving state
# ---------------------------------------------------------------------------

class TestA7Stale(ReconcileFixture):

    def _outage(self):
        # Both ports share the fixture's faults dict (rebound in
        # ``_build``) — one mutation scripts the whole GitHub outage.
        self.faults["poll"] = "down"
        self.faults["observe"] = "down"
        self.faults["pr-read"] = {"kind": "down"}

    def test_five_minutes_marks_stale_and_alerts_once(self):
        self._accept(42)
        self.issue_rows[42] = issue(42)
        self.recovery.tick()                       # one good pass
        self._outage()
        # Fail through the five-minute window — the staleness check
        # runs on every tick even when a pass isn't due.
        for _ in range(12):
            self.clock[0] += 30
            report = self.recovery.tick()
            if report["remote_state"] == "stale":
                break
        wm = self.store.reconcile_watermark()
        self.assertEqual(wm["remote_state"], "stale")
        self.assertGreater(self.clock[0] - wm["stale_since_epoch"],
                           STALE_REMOTE_S)
        alerts = self.store.alert_rows("reconciliation")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "medium")
        # A repeated check inside the same episode never re-pages.
        self.clock[0] += 60
        self.recovery.tick()
        self.assertEqual(len(self.store.alert_rows("reconciliation")),
                         1)

    def test_stale_stops_evidence_dependent_completion(self):
        work_key = self._accept(42)
        self.issue_rows[42] = issue(42)
        pr = self._linked(work_key)
        self._verified_evidence(work_key, pr)
        self.recovery.tick()
        self._outage()
        self.clock[0] += STALE_REMOTE_S + 1
        self.recovery.tick()
        self.assertEqual(
            self.store.reconcile_watermark()["remote_state"], "stale")
        latest = self.store.latest_evidence(work_key)
        self.assertEqual(latest["status"], "stale")
        self.assertEqual(latest["reason"], "remote-unobserved")
        self.assertEqual(
            self.verifier.verification_status(work_key)["status"],
            "stale")

    def test_stale_state_and_alerts_survive_restart(self):
        self._outage()
        self.recovery.tick()
        self.clock[0] += STALE_REMOTE_S + 1
        self.recovery.tick()
        self.assertEqual(
            self.store.reconcile_watermark()["remote_state"], "stale")
        # The watermark is durable — a restarted process reports the
        # same staleness and does not lose the episode anchor.
        self._restart()
        status = self.recovery.status()
        self.assertEqual(status["remote_state"], "stale")
        self.assertIsNotNone(status["stale_since_epoch"])
        self.assertEqual(len(self.store.alert_rows("reconciliation")),
                         1)

    def test_new_outage_episode_alerts_again(self):
        self._outage()
        self.recovery.tick()
        self.clock[0] += STALE_REMOTE_S + 1
        self.recovery.tick()
        self.assertEqual(len(self.store.alert_rows("reconciliation")),
                         1)
        # Recovery → fresh; a later *new* outage is a new episode.
        self.faults.clear()
        self.issue_rows[42] = issue(42)
        due = self.store.reconcile_watermark()["next_due_epoch"]
        self.clock[0] = max(self.clock[0], due) + 1
        self.recovery.tick()
        self.assertEqual(
            self.store.reconcile_watermark()["remote_state"], "fresh")
        self._outage()
        due = self.store.reconcile_watermark()["next_due_epoch"]
        self.clock[0] = max(self.clock[0], due) + 1
        self.recovery.tick()
        self.clock[0] += STALE_REMOTE_S + 1
        self.recovery.tick()
        self.assertEqual(len(self.store.alert_rows("reconciliation")),
                         2)

    def test_operator_channel_failure_keeps_local_alert(self):
        # Telegram-side delivery is best-effort: when the push raises,
        # the durable alert row is still the locally-visible record.
        def dying_sink(_):
            raise RuntimeError("telegram unavailable")
        self._build(alert_sink=dying_sink)
        self._outage()
        self.recovery.tick()
        self.clock[0] += STALE_REMOTE_S + 1
        self.recovery.tick()
        alerts = self.store.alert_rows("reconciliation")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "medium")
        self.assertGreaterEqual(self.recovery._notify_failures, 1)


if __name__ == "__main__":
    unittest.main()
