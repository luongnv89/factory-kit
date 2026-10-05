#!/usr/bin/env python3
"""Protected conditional merge + authoritative outcome reconciliation
— issue #17 (Task 3.3, PRD §3.2 F12, §6.4 Merge outcome, §7.1).

Each test class maps to an acceptance criterion:

- A1 — every merge-time condition is re-evaluated immediately before
  the spend: work/fence/pause/generation, bound config/policy digests,
  the deciding actor's current authorization, the bound evidence +
  independent review + preview identities, and fresh remote reads of
  PR state/draft/mergeability, exact head/base, required checks,
  protection and capabilities.
- A2 — the smoke observation must be ≤ ``max_smoke_age_minutes`` old
  and the approval unexpired; stale/failed smoke blocks the merge and
  revokes the grant (reverification + fresh approval required).
- A3 — one consume → one durable intent, atomically; the expected-head
  conditional merge is the only effect; concurrent invocations
  converge on the single live intent.
- A4 — repository-enforced protections close the read→mutate race:
  strict up-to-date base + required checks + enforce-admins. Missing
  or weak protection means the merge is *unsupported*, not hopeful.
- A5 — draft/closed/conflicted PRs, missing/failed checks, armed or
  repo-enabled auto-merge (a competing merge owner) and unsupported
  methods all block explicitly. Auto-merge is never enabled.
- A6 — ``merged`` is reported only from authoritative read-back; the
  durable intent carries request/decision/head/base/method identities
  plus merged flag, merge SHA, actual actor and read-back time. Lost
  or contradictory responses reconcile by read-back before any retry;
  unreadable outcomes park the intent, emit ``merge_observed``
  ``unknown`` and alert.
- A7 — a fence/cancel landing before the send boundary forbids the
  effect; a fence landing while the send is in flight records the
  actual remote outcome with the race explanation. A human-originated
  merge keeps its actual actor — never attributed to the factory
  approval.
"""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.approval import ApprovalService  # noqa: E402
from factory_kit.config import registration, schema  # noqa: E402
from factory_kit.control import ControlService  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.execution import (  # noqa: E402
    ExecutionLane, ScriptedWorker)
from factory_kit.intake import service  # noqa: E402
from factory_kit.merge import MergeService  # noqa: E402
from factory_kit.preview import (  # noqa: E402
    PreviewService, ScriptedPreview)
from factory_kit.publication.remote import (  # noqa: E402
    RemoteAmbiguity, ScriptedRemote)
from factory_kit.recovery import RecoveryService  # noqa: E402
from factory_kit.verification import VerificationService  # noqa: E402

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

HEAD_A = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
HEAD_B = "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222"
BASE_SHA = "cccc3333cccc3333cccc3333cccc3333cccc3333"
BASE_B = "dddd4444dddd4444dddd4444dddd4444dddd4444"
BRANCH = "task-42"
PR = "17"

#: The manifest's required check contexts — the merge-time gate
#: evaluates these against the *fresh* remote read.
GREEN_RUNS = [
    {"id": "1", "name": "Code Quality & Build", "status": "completed",
     "conclusion": "success", "app": "github-actions",
     "details_url": "https://ci.test/1"},
    {"id": "2", "name": "Security Scan", "status": "completed",
     "conclusion": "success", "app": "github-actions",
     "details_url": "https://ci.test/2"},
]


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


class MergeFixture(unittest.TestCase):
    """Registered repo + verified evidence + verified preview +
    approved request + the merge owner wired to the scripted remote."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "in.db"
        self.eff = effective()
        self.repo_id = self.eff["identity"]["repo_id"]
        self.full_name = (f"{self.eff['identity']['owner']}/"
                          f"{self.eff['identity']['name']}")
        self.clock = [time.time()]
        self.registrations = registration.RegistrationStore(
            Path(self.tmp.name) / "registration.json")
        self.registrations.register(self.eff, readiness=READY,
                                    supported_versions=VERSIONS)
        self.store = durable.IntakeStore(self.db_path)
        self.provider = ScriptedPreview(now=self._now)
        self.alerts = []
        self.remote = ScriptedRemote(self.repo_id, self.full_name)
        self._seed_remote()
        self._wire(self.store)
        self._cid = [0]

    def _now(self):
        return self.clock[0]

    def _seed_remote(self):
        """The remote's current truth for PR 17 on the approved head —
        the guard reads this fresh; tests mutate it to deny/race."""
        self.remote.branches[BRANCH] = HEAD_A
        self.remote.branches["main"] = BASE_SHA
        self.remote.pulls[PR] = {
            "number": PR, "head": BRANCH, "base": "main",
            "base_sha": BASE_SHA, "title": "t", "state": "open",
            "url": f"https://example.test/{self.full_name}/pull/{PR}",
            "identity": {}}
        self.remote.checks[HEAD_A] = [dict(r) for r in GREEN_RUNS]

    def _wire(self, store):
        self.store = store
        self.intake = service.IntakeService(
            store, self.registrations, {self.repo_id: self.eff},
            signing_secret="s", opt_in_label="factory-kit")
        self.approval = ApprovalService(
            store, self.registrations, {self.repo_id: self.eff},
            now=self._now)
        self.verifier = VerificationService(
            store, now=self._now, approval=self.approval)
        self.preview = PreviewService(
            store, self.provider, self.registrations,
            {self.repo_id: self.eff}, now=self._now,
            approval=self.approval)
        self.worker = ScriptedWorker()
        self.lane = ExecutionLane(
            store, self.registrations, {self.repo_id: self.eff},
            worker=self.worker,
            workspace_root=Path(self.tmp.name) / "ws",
            readings=READINGS, now=self._now)
        self.control = ControlService(
            store, self.lane, self.registrations,
            {self.repo_id: self.eff}, approval=self.approval,
            now=self._now)
        self.merge_svc = MergeService(
            store, self.remote, self.approval, self.registrations,
            {self.repo_id: self.eff}, now=self._now,
            alert_sink=self.alerts)
        self.recovery = RecoveryService(
            store, self.registrations, {self.repo_id: self.eff},
            intake=self.intake, lane=self.lane,
            preview=self.preview, merge=self.merge_svc,
            remote=self.remote, now=self._now,
            alert_sink=self.alerts)

    def _reopen(self):
        self.store.close()
        self._wire(durable.IntakeStore(self.db_path))

    def _accept(self, issue=42):
        env = {"delivery_id": f"d-{issue}", "channel": "reconciliation",
               "repo_id": self.repo_id, "repository": self.full_name,
               "issue": issue, "action": "reconcile",
               "sender": "luongnv89", "labels": ["factory-kit"],
               "issue_title": "t", "issue_body": BODY,
               "issue_revision": "2026-10-05"}
        ack = self.intake.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        self.registrations.record_work(self.repo_id, work_key)
        return work_key

    def _verify(self, work_key, head_sha=HEAD_A, base_sha=BASE_SHA,
                status="verified", pr_number=int(PR)):
        with self.store.transact() as tx:
            tx.record_evidence(
                f"ev-{head_sha[:6]}-{len(self.store.evidence_rows())}",
                work_key=work_key, seq=tx.next_seq(),
                pr_number=pr_number,
                pr_url=f"https://github.test/{self.full_name}/pull/"
                       f"{pr_number}",
                head_sha=head_sha, base_name="main", base_sha=base_sha,
                checks=GREEN_RUNS, review_id="rev-1",
                review_session="sess-1", contract_digest="cd-1",
                status=status, reason="checks-green")
        return self.store.latest_evidence(work_key)

    def _deploy(self, work_key, head_sha=HEAD_A):
        out = self.preview.deploy_preview(work_key, head_sha=head_sha)
        assert out["outcome"] in ("verified", "converged"), out
        return out

    def _approved(self, issue=42, head_sha=HEAD_A):
        """The full F12 run-up: accepted work + verified evidence +
        verified preview + issued request + accepted decision."""
        work_key = self._accept(issue=issue)
        self._verify(work_key, head_sha=head_sha)
        self._deploy(work_key, head_sha=head_sha)
        out = self.approval.request_approval(work_key)
        assert out["outcome"] == "requested", out
        dec = self.approval.decide(
            request_id=out["request_id"],
            actor_ref=f"telegram:{USER}", chat_ref=f"telegram:{CHAT}",
            verdict="approve")
        assert dec["outcome"] == "approved", dec
        return work_key, out["request_id"]

    def _events(self, kind, work_key=None):
        return [r for r in self.store.event_rows(kind)
                if work_key is None or r.get("work_key") == work_key]

    def _intents(self, work_key):
        return self.store.merge_intent_rows(work_key)

    def _merges(self):
        return [c for c in self.remote.calls if c["op"] == "pr-merge"]


# ---------------------------------------------------------------------------
# A1/A2 — the merge-time gate
# ---------------------------------------------------------------------------

class TestA1Guard(MergeFixture):

    def test_merge_happy_path(self):
        work_key, request_id = self._approved()
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "merged", out)
        self.assertEqual(out["merge_actor_kind"], "factory")
        self.assertTrue(out["merge_sha"].startswith("merge-17-"))
        # The durable intent carries the full §6.4 outcome surface.
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["state"], "merged")
        self.assertEqual(intent["request_id"], request_id)
        self.assertTrue(intent["decision_id"])
        self.assertEqual(intent["expected_head"], HEAD_A)
        self.assertEqual(intent["expected_base_name"], "main")
        self.assertEqual(intent["expected_base_sha"], BASE_SHA)
        self.assertEqual(intent["merge_method"], "squash")
        self.assertEqual(intent["actor_ref"], f"telegram:{USER}")
        self.assertEqual(intent["merged"], 1)
        self.assertEqual(intent["merge_sha"], out["merge_sha"])
        self.assertEqual(intent["merged_by"], "factory-bot")
        self.assertEqual(intent["merge_actor_kind"], "factory")
        self.assertTrue(intent["sent_at"])
        self.assertTrue(intent["read_back_at"])
        # The request is consumed — the one-use grant is spent.
        req = self.store.get_approval_request(request_id)
        self.assertEqual(req["state"], "consumed")
        # Typed events: the intent twin + the authoritative observation.
        recorded = self._events("merge_intent_recorded", work_key)
        self.assertEqual(len(recorded), 1)
        observed = self._events("merge_observed", work_key)
        self.assertEqual(len(observed), 1)
        detail = json.loads(observed[0]["detail"])
        self.assertEqual(detail["outcome"], "merged")
        self.assertEqual(detail["intent_id"], intent["intent_id"])
        self.assertEqual(detail["expected_head"], HEAD_A)
        self.assertEqual(detail["merged_by"], "factory-bot")
        self.assertEqual(detail["merge_actor_kind"], "factory")

    def test_denied_when_no_approved_request(self):
        work_key = self._accept()
        self._verify(work_key)
        self._deploy(work_key)
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "no-approved-request")

    def test_denied_when_request_only_awaiting(self):
        work_key = self._accept()
        self._verify(work_key)
        self._deploy(work_key)
        self.approval.request_approval(work_key)
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "state-awaiting")
        self.assertEqual(self._merges(), [])

    def test_denied_draft_pr(self):
        work_key, _ = self._approved()
        self.remote.pr_drafts.add(PR)
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "pr-draft")
        self.assertEqual(self._merges(), [])
        req = self._events("merge_denied", work_key)
        self.assertEqual(len(req), 1)

    def test_denied_closed_pr(self):
        work_key, _ = self._approved()
        self.remote.pr_states[PR] = "CLOSED"
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertTrue(out["reason"].startswith("pr-not-open"))
        self.assertEqual(self._merges(), [])

    def test_denied_head_moved(self):
        work_key, _ = self._approved()
        self.remote.branches[BRANCH] = HEAD_B
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "head-moved")
        self.assertEqual(self._merges(), [])

    def test_denied_missing_required_check(self):
        work_key, _ = self._approved()
        self.remote.checks[HEAD_A] = [GREEN_RUNS[0]]
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertTrue(out["reason"].startswith("checks-failed"),
                        out)
        self.assertIn("Security Scan", out["reason"])
        self.assertEqual(self._merges(), [])

    def test_denied_failed_check(self):
        work_key, _ = self._approved()
        runs = [dict(r) for r in GREEN_RUNS]
        runs[1]["conclusion"] = "failure"
        self.remote.checks[HEAD_A] = runs
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertTrue(out["reason"].startswith("checks-failed"))
        self.assertEqual(self._merges(), [])

    def test_denied_check_still_running(self):
        work_key, _ = self._approved()
        runs = [dict(r) for r in GREEN_RUNS]
        runs[1]["status"] = "in_progress"
        runs[1]["conclusion"] = None
        self.remote.checks[HEAD_A] = runs
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertTrue(out["reason"].startswith("checks-blocked"),
                        out)

    def test_denied_merge_conflict(self):
        work_key, _ = self._approved()
        self.remote.mergeable = "CONFLICTING"
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "merge-conflict")

    def test_denied_mergeable_pending(self):
        work_key, _ = self._approved()
        self.remote.mergeable = "UNKNOWN"
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "mergeable-pending")

    def test_denied_when_paused(self):
        work_key, _ = self._approved()
        with self.store.transact() as tx:
            tx.set_pause(work_key, "paused", stage="merge")
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "task-paused")
        self.assertEqual(self._merges(), [])

    def test_denied_when_fenced(self):
        work_key, request_id = self._approved()
        self.store.fence_work(work_key, "canceled", 1)
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "fenced")
        self.assertEqual(self._merges(), [])
        # The grant itself is not consumed by a denied merge.
        req = self.store.get_approval_request(request_id)
        self.assertEqual(req["state"], "approved")

    def test_denied_remote_unavailable(self):
        work_key, _ = self._approved()
        self.remote.faults["pr-read"] = "raise"
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertTrue(out["reason"].startswith("remote-unavailable"))
        self.assertEqual(self._merges(), [])


class TestA2SmokeAndExpiry(MergeFixture):

    def test_denied_stale_smoke_revokes_approval(self):
        work_key, request_id = self._approved()
        # Past the 10-minute smoke-age bound, inside the 60-minute
        # approval window.
        self.clock[0] += 11 * 60
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "smoke-stale")
        self.assertEqual(self._merges(), [])
        req = self.store.get_approval_request(request_id)
        self.assertEqual(req["state"], "invalidated")
        self.assertEqual(req["reason"], "smoke-stale")

    def test_denied_expired_approval(self):
        work_key, request_id = self._approved()
        self.clock[0] += 61 * 60
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "expired")
        req = self.store.get_approval_request(request_id)
        self.assertEqual(req["state"], "expired")
        self.assertEqual(self._merges(), [])

    def test_denied_failed_smoke_revokes(self):
        work_key, request_id = self._approved()
        preview = self.store.active_preview(work_key)
        with self.store.transact() as tx:
            tx.update_preview(
                preview["preview_id"],
                smoke_observed=json.dumps(
                    {"ok": False, "http": "503"}))
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "smoke-failed")
        req = self.store.get_approval_request(request_id)
        self.assertEqual(req["state"], "invalidated")
        self.assertEqual(req["reason"], "smoke-failed")

    def test_denied_preview_superseded_revokes(self):
        work_key, request_id = self._approved()
        with self.store.transact() as tx:
            tx.update_preview(
                self.store.active_preview(work_key)["preview_id"],
                state="failed", reason="provider-outage")
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        req = self.store.get_approval_request(request_id)
        self.assertEqual(req["state"], "invalidated")


# ---------------------------------------------------------------------------
# A3 — one approval → one intent; concurrency converges
# ---------------------------------------------------------------------------

class TestA3Atomicity(MergeFixture):

    def test_second_merge_converges_no_second_effect(self):
        work_key, request_id = self._approved()
        first = self.merge_svc.merge(work_key)
        self.assertEqual(first["outcome"], "merged")
        second = self.merge_svc.merge(work_key)
        self.assertEqual(second["outcome"], "converged")
        self.assertEqual(len(self._merges()), 1)
        self.assertEqual(len(self._intents(work_key)), 1)

    def test_concurrent_merges_one_intent(self):
        work_key, request_id = self._approved()
        results, errors = [], []

        def run():
            try:
                results.append(self.merge_svc.merge(work_key))
            except Exception as exc:  # pragma: no cover - diagnostic
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        # Exactly one intent exists and exactly one remote merge call
        # was ever made — the CAS + live-slot index serialize the race.
        self.assertEqual(len(self._intents(work_key)), 1)
        self.assertEqual(len(self._merges()), 1)
        outcomes = {r["outcome"] for r in results}
        self.assertTrue(outcomes <= {"merged", "converged", "parked"})

    def test_intent_carries_bound_identity(self):
        work_key, request_id = self._approved()
        self.merge_svc.merge(work_key)
        intent = self._intents(work_key)[0]
        self.assertEqual(intent["request_id"], request_id)
        self.assertEqual(intent["work_key"], work_key)
        self.assertEqual(intent["pr_number"], PR)
        self.assertEqual(intent["expected_head"], HEAD_A)
        self.assertEqual(intent["expected_base_sha"], BASE_SHA)
        self.assertEqual(intent["merge_method"], "squash")
        self.assertEqual(intent["generation"], 1)
        self.assertEqual(intent["actor_ref"], f"telegram:{USER}")
        # the merge call carried the expected-head precondition
        call = self._merges()[0]
        self.assertEqual(call["expected_head"], HEAD_A)
        self.assertEqual(call["method"], "squash")


# ---------------------------------------------------------------------------
# A4 — repository-enforced protection closes the race
# ---------------------------------------------------------------------------

class TestA4Protection(MergeFixture):

    def test_unprotected_branch_is_unsupported(self):
        work_key, _ = self._approved()
        self.remote.protection = {"protected": False}
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "protection-missing")
        self.assertEqual(self._merges(), [])

    def test_non_strict_base_is_unsupported(self):
        work_key, _ = self._approved()
        self.remote.protection["strict"] = False
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "strict-base-unenforced")

    def test_bypassable_admins_blocked(self):
        work_key, _ = self._approved()
        self.remote.protection["enforce_admins"] = False
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "bypassable-protection")

    def test_unprotected_required_check_blocked(self):
        work_key, _ = self._approved()
        self.remote.protection["required_checks"] = [
            "Code Quality & Build"]
        out = self.merge_svc.merge(work_key)
        self.assertTrue(
            out["reason"].startswith("required-checks-unprotected"),
            out)

    def test_base_moved_denied_before_send(self):
        work_key, _ = self._approved()
        self.remote.branches["main"] = BASE_B
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "base-moved")
        self.assertEqual(self._merges(), [])

    def test_base_race_refused_by_protection(self):
        """Base moves between the guard's read and the mutation — the
        repository's strict up-to-date enforcement refuses it, and the
        outcome reconciles to a clean not-merged (A4)."""
        work_key, request_id = self._approved()
        real_merge = self.remote.merge_pr

        def racing_merge(number, *, method, expected_head, identity):
            self.remote.branches["main"] = BASE_B   # base races in
            return real_merge(number, method=method,
                              expected_head=expected_head,
                              identity=identity)

        self.remote.merge_pr = racing_merge
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "not-merged")
        self.assertEqual(out["reason"], "merge-refused")
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["state"], "not-merged")
        self.assertEqual(intent["merged"], 0)
        pr = self.remote.read_pr(PR)
        self.assertFalse(pr["merged"])


# ---------------------------------------------------------------------------
# A5 — competing owners, auto-merge, methods
# ---------------------------------------------------------------------------

class TestA5Blocks(MergeFixture):

    def test_auto_merge_armed_blocks(self):
        work_key, _ = self._approved()
        self.remote.auto_merge = {"enabledBy": {"login": "dependabot"}}
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "auto-merge-armed")
        self.assertEqual(self._merges(), [])

    def test_repo_auto_merge_capability_blocks(self):
        work_key, _ = self._approved()
        self.remote.capabilities["allow_auto_merge"] = True
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "auto-merge-enabled")
        self.assertEqual(self._merges(), [])

    def test_unsupported_method_capability_blocks(self):
        work_key, _ = self._approved()
        self.remote.capabilities["allow_squash_merge"] = False
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["reason"], "method-unsupported")

    def test_config_method_drift_denied(self):
        work_key, request_id = self._approved()
        self.eff["endpoint"]["merge"]["method"] = "rebase"
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "stale-config")
        self.assertEqual(self._merges(), [])

    def test_never_enables_auto_merge(self):
        work_key, _ = self._approved()
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "merged")
        # The only remote calls are reads + the one conditional merge —
        # nothing enables auto-merge anywhere in the call log.
        ops = {c["op"] for c in self.remote.calls}
        self.assertEqual(
            ops, {"pr-read", "branch-read", "check-runs",
                  "protection-read", "capabilities-read",
                  "actor-read", "pr-merge"})


# ---------------------------------------------------------------------------
# A6 — authoritative outcome + ambiguity
# ---------------------------------------------------------------------------

class TestA6Outcome(MergeFixture):

    def test_response_lost_effect_landed_reconciles(self):
        """crash-after-create: the merge landed but the response was
        lost — read-back resolves it to merged; never retried."""
        work_key, request_id = self._approved()
        self.remote.faults["pr-merge"] = "crash-after-create"
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "merged", out)
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["state"], "merged")
        self.assertIn("ambiguous", intent["detail"])
        self.assertEqual(len(self._merges()), 1)

    def test_response_lost_request_lost_not_merged(self):
        """crash-before-create: the request never reached the remote —
        read-back proves unmerged and the intent resolves honestly."""
        work_key, request_id = self._approved()
        self.remote.faults["pr-merge"] = "crash-before-create"
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "not-merged")
        self.assertEqual(out["reason"], "merge-not-applied")
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["state"], "not-merged")
        self.assertFalse(self.remote.read_pr(PR)["merged"])

    def test_lost_response_and_unreadable_remote_parks(self):
        """The response was lost AND the read-back cannot answer — the
        intent parks unknown with the event + alert (A6)."""
        work_key, request_id = self._approved()
        self.remote.faults["pr-merge"] = "crash-after-create"
        # The read outage lands *with* the send — the guard's fresh
        # reads still ran; the post-effect read-back cannot answer.
        real_merge = self.remote.merge_pr

        def merge_then_blind(number, *, method, expected_head,
                             identity):
            try:
                return real_merge(number, method=method,
                                  expected_head=expected_head,
                                  identity=identity)
            finally:
                self.remote.faults["pr-read"] = "raise"

        self.remote.merge_pr = merge_then_blind
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "parked")
        self.assertEqual(out["reason"], "merge-outcome-unknown")
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["state"], "parked")
        observed = self._events("merge_observed", work_key)
        self.assertEqual(json.loads(observed[-1]["detail"])
                         ["outcome"], "unknown")
        kinds = [a["kind"] for a in self.alerts]
        self.assertIn("merge-outcome-unknown", kinds)
        # The parked intent holds the live merge slot — a repeated
        # merge() call converges and reconciles, never resends.
        self.remote.faults.clear()
        again = self.merge_svc.merge(work_key)
        self.assertEqual(again["outcome"], "merged")
        self.assertEqual(len(self._merges()), 1)
        self.assertEqual(len(self._intents(work_key)), 1)

    def test_reconcile_pending_settles_crash_window(self):
        """A ``recorded`` intent (the process died mid-merge) resolves
        by read-back on the recovery pass — never by resend."""
        work_key, request_id = self._approved()
        # The send lands but the post-effect read-back is unreachable.
        real_merge = self.remote.merge_pr

        def merge_then_outage(number, *, method, expected_head,
                              identity):
            result = real_merge(number, method=method,
                                expected_head=expected_head,
                                identity=identity)
            self.remote.faults["pr-read"] = "raise"
            return result

        self.remote.merge_pr = merge_then_outage
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "parked")
        self.assertEqual(out["reason"], "merge-outcome-unknown")
        # Read-back restored — reconcile resolves the parked intent.
        self.remote.faults.clear()
        self.remote.merge_pr = real_merge
        outcomes = self.merge_svc.reconcile_pending()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["outcome"], "merged")
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["state"], "merged")
        self.assertEqual(intent["merged_by"], "factory-bot")

    def test_recovery_pass_reconciles_merge_intents(self):
        """The Hermes-owned pass resolves the parked intent — no second
        scheduler, the same durable read-back path."""
        work_key, request_id = self._approved()
        real_merge = self.remote.merge_pr

        def merge_then_outage(number, *, method, expected_head,
                              identity):
            result = real_merge(number, method=method,
                                expected_head=expected_head,
                                identity=identity)
            self.remote.faults["pr-read"] = "raise"
            return result

        self.remote.merge_pr = merge_then_outage
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "parked")
        self.remote.faults.clear()
        self.remote.merge_pr = real_merge
        report = self.recovery.reconcile_once(source="poll")
        self.assertEqual(report["merge_intents"][0]["outcome"],
                         "merged")

    def test_merge_observed_only_after_read_back(self):
        """A claimed-success response with an unmerged read-back is a
        contradiction — parked unknown, never reported merged."""
        work_key, request_id = self._approved()
        real_read = self.remote.read_pr
        real_merge = self.remote.merge_pr

        def lying_merge(number, *, method, expected_head, identity):
            # Answer success but never apply the merge.
            return {"merged": True, "sha": "bogus"}

        self.remote.merge_pr = lying_merge
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "parked")
        self.assertEqual(out["reason"], "merge-outcome-unknown")
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["state"], "parked")
        self.assertIsNone(intent["merge_sha"])


# ---------------------------------------------------------------------------
# A7 — fence/cancel races + human-originated merges
# ---------------------------------------------------------------------------

class TestA7Races(MergeFixture):

    def test_fence_before_boundary_forbids_effect(self):
        """A fence landing inside the spend commit is caught by the
        pre-send boundary — the intent invalidates, no remote call."""
        work_key, request_id = self._approved()
        real_consume = self.approval.consume_tx

        def fencing_consume(tx, rid, **kw):
            res = real_consume(tx, rid, **kw)
            # The fence commits inside the same transaction as the
            # spend — the boundary must still refuse the wire.
            tx.fence_work_tx(work_key, "canceled",
                             self.store.get_work(work_key)
                             ["generation"])
            return res

        self.approval.consume_tx = fencing_consume
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "fenced")
        self.assertEqual(self._merges(), [])
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["state"], "invalidated")
        self.assertEqual(intent["reason"], "fenced")

    def test_in_flight_cancel_race_records_actual_outcome(self):
        """The fence lands while the merge call is on the wire — the
        remote outcome still records with the race explanation (A7)."""
        work_key, request_id = self._approved()
        real_merge = self.remote.merge_pr

        def cancel_during_merge(number, *, method, expected_head,
                                identity):
            result = real_merge(number, method=method,
                                expected_head=expected_head,
                                identity=identity)
            # The cancel commits while the conditional merge was in
            # flight — before the read-back runs.
            self.store.fence_work(work_key, "canceled", 1)
            return result

        self.remote.merge_pr = cancel_during_merge
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "merged")
        self.assertEqual(out["reason"], "cancellation-race")
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["reason"], "cancellation-race")
        self.assertIn("in flight", intent["detail"])
        observed = self._events("merge_observed", work_key)
        self.assertEqual(json.loads(observed[-1]["detail"])["outcome"],
                         "merged")

    def test_human_merge_keeps_actual_actor(self):
        """The remote attributes the merge to a different actor — the
        human's merge is recorded as external, never claimed by the
        factory approval (A7)."""
        work_key, request_id = self._approved()

        def human_merge(number, *, method, expected_head, identity):
            # A human lands the merge mid-flight: the response is lost
            # to the factory but the remote records the real actor.
            self.remote.pr_states[str(number)] = "MERGED"
            self.remote.mergers[str(number)] = {
                "sha": "human-merge-sha", "by": "human-dev"}
            raise RemoteAmbiguity("merge response lost")

        self.remote.merge_pr = human_merge
        out = self.merge_svc.merge(work_key)
        self.assertEqual(out["outcome"], "merged")
        self.assertEqual(out["merge_actor_kind"], "external")
        intent = self.store.merge_intent_for_request(request_id)
        self.assertEqual(intent["merged_by"], "human-dev")
        self.assertEqual(intent["merge_actor_kind"], "external")
        kinds = [a["kind"] for a in self.alerts]
        self.assertIn("merge-actor-external", kinds)

    def test_restart_between_spend_and_send_reconciles(self):
        """The process dies after the intent committed but before the
        send: a new service instance reconciles by read-back — the PR
        unmerged → not-merged, no resend, no double effect."""
        work_key, request_id = self._approved()
        # Forge the crash window directly: consume + intent committed,
        # no send ever ran (sent_at NULL).
        with self.store.transact() as tx:
            consumed = self.approval.consume_tx(tx, request_id)
            self.assertEqual(consumed["outcome"], "consumed")
            tx.insert_merge_intent(
                "mrg-crashtest", work_key=work_key, seq=tx.next_seq(),
                generation=1, repo_id=self.repo_id,
                request_id=request_id,
                decision_id=consumed["decision_id"], pr_number=PR,
                expected_head=HEAD_A, expected_base_name="main",
                expected_base_sha=BASE_SHA, merge_method="squash",
                actor_ref=f"telegram:{USER}", state="recorded",
                expires_epoch=consumed["expires_epoch"])
        self._reopen()
        outcomes = self.merge_svc.reconcile_pending()
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0]["outcome"], "not-merged")
        self.assertEqual(outcomes[0]["reason"], "merge-not-applied")
        self.assertEqual(self._merges(), [])


if __name__ == "__main__":
    unittest.main()
