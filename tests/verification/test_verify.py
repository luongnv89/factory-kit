#!/usr/bin/env python3
"""Independent review + current-revision verification — issue #11
(Task 2.6, PRD §3.2 F04, §6.4 Evidence, §7.1 evidence_checked).

Each test class maps to an acceptance criterion:

- A1 — the separate reviewer session's own record: attempt/session
  identity, verdict, findings and the inspected SHA persist; a
  self-reported implementation review (implementation attempt,
  mismatched/shared session, re-graded verdict, unfinished attempt)
  can never mint one.
- A2 — approved review + linked open PR + permitted green required
  checks at SHA A reads GitHub and persists PR identity, head/base
  SHA, check-run/provider identities and conclusions, review
  identity, artifact/log references and observation time — only
  while the authoritative head still equals A.
- A3 — head moved A→B, failed/missing checks, unavailable GitHub or
  closed/draft PR yield stale/failed/blocked/unknown with reason and
  never ``verified``; a green worker exit alone satisfies nothing;
  verification only *reads* the remote — no human commit is ever
  overwritten to restore A.
- A4 — protected-check contexts + acceptance commands are the
  nonempty contract; missing blocks; skipped/neutral conclusions
  pass only when the contract permits them.
- A5 — a change request permits one fix while the cumulative
  attempt/time budget remains and rebuilds evidence at the new SHA;
  exhausted limits park instead of adding an attempt.
- A6 — later head/base/contract observations invalidate the old
  ``verified`` row; status always reports the SHA + observation time
  and never implies merge authorization; ``evidence_checked``
  carries the §7.1 identity fields.
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
from factory_kit.intake import service  # noqa: E402
from factory_kit.publication import ScriptedRemote  # noqa: E402
from factory_kit.verification import (  # noqa: E402
    VerificationService, contract_from_effective)

MANIFEST = ROOT / "docs" / "examples" / "reference.factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"
ACTOR = "luongnv89"

SHA_A = "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1"
SHA_B = "b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2"
BASE_SHA = "e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5"   # ScriptedRemote's main
BRANCH = "factory-kit/impl-42"

GREEN_RUNS = [
    {"id": "11", "name": "Code Quality & Build", "status": "completed",
     "conclusion": "success", "app": "github-actions",
     "details_url": "https://ci.test/runs/11"},
    {"id": "12", "name": "Security Scan", "status": "completed",
     "conclusion": "success", "app": "github-actions",
     "details_url": "https://ci.test/runs/12"},
]


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        if group in eff and isinstance(eff[group], dict):
            eff[group].update(patch)
        else:
            eff[group] = patch
    return eff


class VerifyFixture(unittest.TestCase):
    """Registered repo + accepted active work + scripted remote +
    the verification service on a fake clock."""

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
        self.store = durable.IntakeStore(Path(self.tmp.name) / "in.db")
        self.intake = service.IntakeService(
            self.store, self.registrations, {self.repo_id: self.eff},
            signing_secret="s", opt_in_label="factory-kit")
        self.remote = ScriptedRemote(self.repo_id, self.full_name)
        self.verifier = VerificationService(
            self.store, now=lambda: self.clock[0])

    # -- fixture builders ------------------------------------------------------

    def _accept(self, issue=42):
        env = {"delivery_id": f"d-{issue}", "channel": "reconciliation",
               "repo_id": self.repo_id, "repository": self.full_name,
               "issue": issue, "action": "reconcile",
               "sender": ACTOR, "labels": ["factory-kit"],
               "issue_title": "t", "issue_body": BODY,
               "issue_revision": "2026-10-05"}
        ack = self.intake.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        self.registrations.record_work(self.repo_id, work_key)
        self.store.activate_work(work_key)
        return work_key

    def _attempt(self, work_key, role, verdict, *,
                 session=None, active_seconds=10):
        """Mint one durable finished attempt — impl and review get
        distinct sessions unless the fixture deliberately shares one."""
        n = self.store.count_attempts(work_key) + 1
        task_id = self.store.get_work(work_key)["task_id"]
        aid = f"{task_id}-a{n:02d}"
        session = session or f"sess-{aid}"
        self.store.begin_execution_attempt(
            work_key, attempt_id=aid, role=role, task_id=task_id,
            generation=1, session_id=session, runtime="hermes-kanban",
            model="m", skills="{}", config_digest="c",
            policy_digest="p", workspace="/w", limits="{}",
            now_epoch=self.clock[0])
        self.store.finish_execution_attempt(
            aid, verdict=verdict, outcome="completed",
            active_seconds=active_seconds, usage={"t": 1},
            duration_s=10)
        return aid, session

    def _pair(self, work_key, *, impl_verdict="completed",
              review_verdict="approved"):
        """The implementation→review session pair at distinct sessions."""
        impl = self._attempt(work_key, "implementation", impl_verdict)
        rev = self._attempt(work_key, "review", review_verdict)
        return {"impl": impl, "review": rev}

    def _review(self, work_key, pair=None, **kw):
        pair = pair or self._pair(work_key)
        kw.setdefault("verdict", "approved")
        out = self.verifier.record_review(
            work_key, attempt_id=pair["review"][0],
            session_id=pair["review"][1], sha=kw.pop("sha", SHA_A),
            findings=kw.pop("findings", ["diff meets criteria"]),
            artifacts=kw.pop("artifacts",
                             [{"kind": "review-log",
                               "ref": "logs/rev-1.jsonl"}]),
            **kw)
        assert out["outcome"] == "recorded", out
        return out["review_id"]

    def _publish(self, work_key, sha=SHA_A, base="main", checks=None):
        """Publish the branch + PR on the scripted remote, link the PR,
        and register the check-runs the remote will report at ``sha``."""
        self.remote.branches[BRANCH] = sha
        pr = self.remote.open_pr(BRANCH, base, "implement issue",
                                 {"intent_id": "pub-1",
                                  "work_key": work_key,
                                  "authority_key": "k", "generation": 1,
                                  "actor_ref": "w"})
        self.store.link_pr(work_key, pr["number"])
        self.remote.checks[sha] = list(
            GREEN_RUNS if checks is None else checks)
        return pr["number"]

    def _verified(self, work_key=None, sha=SHA_A, checks=None):
        work_key = work_key or self._accept()
        pair = self._pair(work_key)
        self._review(work_key, pair)
        number = self._publish(work_key, sha, checks=checks)
        return self.verifier.verify(work_key, sha, remote=self.remote,
                                    effective=self.eff), number


# ---------------------------------------------------------------------------
# A1 — independent review record
# ---------------------------------------------------------------------------

class TestIndependentReview(VerifyFixture):

    def test_review_persists_identity_verdict_findings_sha(self):
        work_key = self._accept()
        pair = self._pair(work_key)
        review_id = self._review(work_key, pair)
        rows = self.store.review_rows(work_key)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["review_id"], review_id)
        self.assertEqual(row["attempt_id"], pair["review"][0])
        self.assertEqual(row["session_id"], pair["review"][1])
        self.assertEqual(row["verdict"], "approved")
        self.assertEqual(row["sha"], SHA_A)
        self.assertIn("diff meets criteria",
                      json.loads(row["findings"]))
        events = self.store.event_rows("review_recorded")
        self.assertEqual(len(events), 1)

    def test_implementation_attempt_cannot_self_report(self):
        """A1: a review record claiming an *implementation* attempt is
        denied — self-reported review never satisfies independence."""
        work_key = self._accept()
        pair = self._pair(work_key)
        out = self.verifier.record_review(
            work_key, attempt_id=pair["impl"][0],
            session_id=pair["impl"][1], sha=SHA_A, verdict="approved")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "not-review-attempt")
        self.assertEqual(self.store.review_rows(work_key), [])
        self.assertEqual(len(self.store.event_rows("review_rejected")), 1)

    def test_review_on_shared_session_is_not_independent(self):
        """A1: a review record minted on a session an implementation
        attempt used is denied ``not-independent``."""
        work_key = self._accept()
        impl = self._attempt(work_key, "implementation", "completed",
                             session="sess-shared")
        rev = self._attempt(work_key, "review", "approved",
                            session="sess-shared")
        out = self.verifier.record_review(
            work_key, attempt_id=rev[0], session_id="sess-shared",
            sha=SHA_A, verdict="approved")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "not-independent")
        self.assertEqual(self.store.review_rows(work_key), [])

    def test_session_mismatch_and_unfinished_denied(self):
        work_key = self._accept()
        pair = self._pair(work_key)
        out = self.verifier.record_review(
            work_key, attempt_id=pair["review"][0],
            session_id="sess-other", sha=SHA_A, verdict="approved")
        self.assertEqual(out["reason"], "session-mismatch")
        # unfinished review attempt — no durable finish yet
        n = self.store.count_attempts(work_key) + 1
        task_id = self.store.get_work(work_key)["task_id"]
        aid = f"{task_id}-a{n:02d}"
        self.store.begin_execution_attempt(
            work_key, attempt_id=aid, role="review", task_id=task_id,
            generation=1, session_id="sess-live",
            runtime="hermes-kanban", model="m", skills="{}",
            config_digest="c", policy_digest="p", workspace="/w",
            limits="{}", now_epoch=self.clock[0])
        out = self.verifier.record_review(
            work_key, attempt_id=aid, session_id="sess-live",
            sha=SHA_A, verdict="approved")
        self.assertEqual(out["reason"], "attempt-unfinished")

    def test_verdict_mismatch_denied(self):
        """A1: a review claiming a verdict different from the attempt
        record's own verdict is a substitution, not evidence."""
        work_key = self._accept()
        pair = self._pair(work_key, review_verdict="changes-requested")
        out = self.verifier.record_review(
            work_key, attempt_id=pair["review"][0],
            session_id=pair["review"][1], sha=SHA_A, verdict="approved")
        self.assertEqual(out["reason"], "verdict-mismatch")

    def test_duplicate_and_bad_inputs_denied(self):
        work_key = self._accept()
        pair = self._pair(work_key)
        self._review(work_key, pair)
        out = self.verifier.record_review(
            work_key, attempt_id=pair["review"][0],
            session_id=pair["review"][1], sha=SHA_A, verdict="approved")
        self.assertEqual(out["reason"], "already-recorded")
        for verdict in ("completed", "ok", "LGTM"):
            out = self.verifier.record_review(
                work_key, attempt_id="x", session_id="y",
                sha=SHA_A, verdict=verdict)
            self.assertEqual(out["reason"], "bad-verdict")
        out = self.verifier.record_review(
            work_key, attempt_id=pair["review"][0],
            session_id=pair["review"][1], sha="not-a-sha!",
            verdict="approved")
        self.assertEqual(out["reason"], "bad-revision")


# ---------------------------------------------------------------------------
# A2 — the verified observation and its persisted evidence
# ---------------------------------------------------------------------------

class TestVerifiedPath(VerifyFixture):

    def test_verified_persists_full_evidence(self):
        work_key = self._accept()
        out, number = self._verified(work_key)
        self.assertEqual(out["status"], "verified")
        latest = self.store.latest_evidence(work_key)
        self.assertEqual(latest["status"], "verified")
        self.assertEqual(latest["pr_number"], str(number))
        self.assertTrue(latest["pr_url"].startswith("https://"))
        self.assertEqual(latest["head_sha"], SHA_A)
        self.assertEqual(latest["base_name"], "main")
        self.assertEqual(latest["base_sha"], BASE_SHA)
        self.assertEqual(latest["review_session"], f"sess-"
                         f"{self.store.get_work(work_key)['task_id']}-a02")
        checks = json.loads(latest["checks"])
        self.assertEqual(
            {c["name"] for c in checks},
            {"Code Quality & Build", "Security Scan"})
        self.assertTrue(all(c["app"] == "github-actions"
                            and c["conclusion"] == "success"
                            for c in checks))
        artifacts = json.loads(latest["artifacts"])
        self.assertTrue(any(a.get("kind") == "check-run"
                            for a in artifacts))
        self.assertTrue(latest["observed_at"])
        self.assertTrue(latest["contract_digest"])

    def test_evidence_checked_event_carries_section71_fields(self):
        work_key = self._accept()
        self._verified(work_key)
        events = self.store.event_rows("evidence_checked")
        self.assertEqual(len(events), 1)
        detail = json.loads(events[0]["detail"])
        for field in ("pr", "head_sha", "checks", "review_id",
                      "review_session", "status", "observed_at"):
            self.assertIn(field, detail)
        self.assertEqual(detail["status"], "verified")
        self.assertEqual(detail["head_sha"], SHA_A)

    def test_explicit_pr_number_and_neutral_conclusion_contract(self):
        """A2+A4: a contract naming skipped/neutral conclusions accepts
        them for the named contexts."""
        work_key = self._accept()
        eff = effective()
        eff["verification"]["required_checks"]["conclusions"] = \
            ["success", "skipped", "neutral"]
        self._pair(work_key)
        self._review(work_key)
        number = self._publish(
            work_key, checks=[
                {"id": "1", "name": "Code Quality & Build",
                 "status": "completed", "conclusion": "skipped",
                 "app": "github-actions",
                 "details_url": "https://ci.test/runs/1"},
                {"id": "2", "name": "Security Scan",
                 "status": "completed", "conclusion": "neutral",
                 "app": "github-actions",
                 "details_url": "https://ci.test/runs/2"}])
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=eff, pr_number=number)
        self.assertEqual(out["status"], "verified")


# ---------------------------------------------------------------------------
# A3 — never verified
# ---------------------------------------------------------------------------

class TestNeverVerified(VerifyFixture):

    def test_head_moved_is_stale(self):
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        number = self._publish(work_key, SHA_A)
        self.remote.branches[BRANCH] = SHA_B     # A → B after review
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "stale")
        self.assertIn("head-moved", out["reason"])
        self.assertNotEqual(out["status"], "verified")

    def test_failed_and_missing_checks(self):
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        self._publish(work_key, checks=[
            GREEN_RUNS[0],
            {"id": "9", "name": "Security Scan", "status": "completed",
             "conclusion": "failure", "app": "github-actions",
             "details_url": "https://ci.test/runs/9"}])
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "failed")
        self.assertIn("Security Scan", out["reason"])

        work_key2 = self._accept(43)
        self._pair(work_key2)
        self._review(work_key2)
        self._publish(work_key2, checks=[GREEN_RUNS[0]])
        out = self.verifier.verify(work_key2, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "failed")
        self.assertIn("required-check-missing:Security Scan",
                      out["reason"])

    def test_incomplete_check_is_blocked(self):
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        self._publish(work_key, checks=[
            GREEN_RUNS[0],
            {"id": "9", "name": "Security Scan",
             "status": "in_progress", "conclusion": None,
             "app": "github-actions",
             "details_url": "https://ci.test/runs/9"}])
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "blocked")
        self.assertIn("check-incomplete:Security Scan", out["reason"])

    def test_github_unavailable_is_unknown(self):
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        self._publish(work_key)
        self.remote.faults["pr-read"] = "raise"
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "unknown")
        self.assertIn("remote-unavailable", out["reason"])

        work_key2 = self._accept(43)
        self._pair(work_key2)
        self._review(work_key2)
        self._publish(work_key2)
        self.remote.faults.clear()
        self.remote.faults["check-runs"] = "raise"
        out = self.verifier.verify(work_key2, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "unknown")

    def test_closed_and_draft_pr_blocked(self):
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        number = self._publish(work_key)
        self.remote.pr_states[number] = "CLOSED"
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "blocked")
        self.assertIn("pr-not-open", out["reason"])

        self.remote.pr_states.clear()
        self.remote.pr_drafts.add(number)
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["reason"], "pr-draft")

    def test_no_linked_pr_and_no_review(self):
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["reason"], "no-linked-pr")

        # A3: worker green exit alone — no durable review, no remote
        # read — can never verify.
        work_key2 = self._accept(43)
        self._pair(work_key2)
        self._publish(work_key2)
        out = self.verifier.verify(work_key2, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "independent-review-missing")

    def test_verification_is_read_only(self):
        """A3: no mutation verb is ever invoked — a head moved to B is
        reported stale, never force-restored to A."""
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        self._publish(work_key, SHA_A)
        self.remote.branches[BRANCH] = SHA_B
        calls_before = len(self.remote.calls)
        self.verifier.verify(work_key, SHA_A, remote=self.remote,
                             effective=self.eff)
        ops = [c["op"] for c in self.remote.calls[calls_before:]]
        self.assertNotIn("branch-publish", ops)
        self.assertNotIn("pr-publish", ops)
        self.assertEqual(self.remote.branches[BRANCH], SHA_B)


# ---------------------------------------------------------------------------
# A4 — the nonempty operator-approved contract
# ---------------------------------------------------------------------------

class TestContract(VerifyFixture):

    def test_missing_contract_blocks(self):
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        self._publish(work_key)
        bare = effective()
        bare["verification"] = {"acceptance_commands": [],
                                "required_checks": {}}
        self.assertIsNone(contract_from_effective(bare))
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=bare)
        self.assertEqual(out["status"], "blocked")
        self.assertEqual(out["reason"],
                         "verification-contract-missing")

    def test_absent_verification_block_blocks(self):
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        self._publish(work_key)
        bare = effective()
        del bare["verification"]
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=bare)
        self.assertEqual(out["status"], "blocked")

    def test_commands_only_contract_is_nonempty(self):
        """A4: a repo with no protected checks still verifies under its
        explicit acceptance commands — contexts may be empty."""
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        self._publish(work_key, checks=[])
        eff = effective()
        eff["verification"]["required_checks"]["contexts"] = []
        contract = contract_from_effective(eff)
        self.assertIsNotNone(contract)
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=eff)
        self.assertEqual(out["status"], "verified")

    def test_skipped_neutral_fail_unless_permitted(self):
        """A4: with the default ``[success]`` contract a skipped or
        neutral conclusion fails the required context."""
        work_key = self._accept()
        self._pair(work_key)
        self._review(work_key)
        self._publish(work_key, checks=[
            GREEN_RUNS[0],
            {"id": "9", "name": "Security Scan", "status": "completed",
             "conclusion": "skipped", "app": "github-actions",
             "details_url": "https://ci.test/runs/9"}])
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "failed")
        self.assertIn("check-conclusion:Security Scan=skipped",
                      out["reason"])


# ---------------------------------------------------------------------------
# A5 — one bounded fix, evidence rebuilt per revision
# ---------------------------------------------------------------------------

class TestFixBudget(VerifyFixture):

    def test_changes_requested_permits_one_fix_in_budget(self):
        work_key = self._accept()
        pair = self._pair(work_key, review_verdict="changes-requested")
        review_id = self._review(
            work_key, pair, verdict="changes-requested",
            findings=["edge case unhandled"])
        self._publish(work_key)
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "review-changes-requested")
        self.assertTrue(out["fix"]["permitted"])
        self.assertEqual(out["fix"]["attempts_used"], 1)
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "active")

    def test_exhausted_attempts_park(self):
        """A5: with the attempt budget spent, a change request parks —
        it never adds another attempt."""
        work_key = self._accept()
        # default manifest: implementation_attempts = 2 → spend both
        self._attempt(work_key, "implementation", "completed")
        self._attempt(work_key, "implementation", "completed")
        rev = self._attempt(work_key, "review", "changes-requested")
        self.verifier.record_review(
            work_key, attempt_id=rev[0], session_id=rev[1],
            sha=SHA_A, verdict="changes-requested")
        self._publish(work_key)
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "failed")
        self.assertFalse(out["fix"]["permitted"])
        self.assertIn("attempts-exhausted", out["reason"])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")
        self.assertEqual(len(self.store.event_rows("work_parked")), 1)

    def test_new_revision_rebuilds_evidence(self):
        """A5: the fix lands SHA B — verification at B needs B's own
        review + check evidence; A's row stays as history."""
        work_key = self._accept()
        pair = self._pair(work_key, review_verdict="changes-requested")
        self._review(work_key, pair, verdict="changes-requested")
        self._publish(work_key)
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "failed")

        # the bounded fix produces B: new implementation attempt, new
        # checks on B — but A's review is bound to A and cannot stand in
        self._attempt(work_key, "implementation", "completed")
        self.remote.branches[BRANCH] = SHA_B
        self.remote.checks[SHA_B] = list(GREEN_RUNS)
        out = self.verifier.verify(work_key, SHA_B, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["reason"], "independent-review-missing")
        rev_b = self._attempt(work_key, "review", "approved")
        self.verifier.record_review(
            work_key, attempt_id=rev_b[0], session_id=rev_b[1],
            sha=SHA_B, verdict="approved")
        out = self.verifier.verify(work_key, SHA_B, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "verified")
        rows = self.store.evidence_rows(work_key)
        self.assertEqual({r["status"] for r in rows},
                         {"failed", "verified"})
        latest = self.store.latest_evidence(work_key)
        self.assertEqual(latest["head_sha"], SHA_B)


# ---------------------------------------------------------------------------
# A6 — observation staleness and the status surface
# ---------------------------------------------------------------------------

class TestObservationStaleness(VerifyFixture):

    def test_later_head_move_invalidates(self):
        work_key = self._accept()
        out, number = self._verified(work_key)
        self.assertEqual(out["status"], "verified")
        status = self.verifier.verification_status(work_key)
        self.assertEqual(status["status"], "verified")
        self.assertEqual(status["head_sha"], SHA_A)
        self.assertTrue(status["observed_at"])
        self.assertIs(status["merge_authorized"], False)

        self.remote.branches[BRANCH] = SHA_B
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "stale")
        status = self.verifier.verification_status(work_key)
        self.assertEqual(status["status"], "stale")
        self.assertIn("head-moved", status["reason"])
        # the earlier verified row remains as history
        self.assertTrue(any(r["status"] == "verified" for r in
                            self.store.evidence_rows(work_key)))

    def test_base_move_and_contract_change_stale(self):
        work_key = self._accept()
        self._verified(work_key)
        # same head, base moved underneath
        self.remote.branches["main"] = SHA_B
        out = self.verifier.verify(work_key, SHA_A, remote=self.remote,
                                   effective=self.eff)
        self.assertEqual(out["status"], "stale")
        self.assertIn("base-moved", out["reason"])

        work_key2 = self._accept(43)
        self._verified(work_key2)
        eff2 = effective()
        eff2["verification"]["required_checks"]["contexts"].append(
            "Extra Gate")
        self.remote.checks[SHA_A] = GREEN_RUNS + [
            {"id": "13", "name": "Extra Gate", "status": "completed",
             "conclusion": "success", "app": "github-actions",
             "details_url": "https://ci.test/runs/13"}]
        out = self.verifier.verify(work_key2, SHA_A, remote=self.remote,
                                   effective=eff2)
        self.assertEqual(out["status"], "stale")
        self.assertEqual(out["reason"], "contract-changed")

    def test_mark_stale_external_observation(self):
        work_key = self._accept()
        self._verified(work_key)
        out = self.verifier.mark_stale(work_key,
                                       "reconciliation-head-moved")
        self.assertEqual(out["outcome"], "stale")
        status = self.verifier.verification_status(work_key)
        self.assertEqual(status["status"], "stale")
        self.assertEqual(status["reason"], "reconciliation-head-moved")
        self.assertEqual(status["head_sha"], SHA_A)
        # idempotent: nothing verified remains to invalidate
        out = self.verifier.mark_stale(work_key, "again")
        self.assertEqual(out["outcome"], "unchanged")

    def test_status_without_evidence_and_merge_authority(self):
        work_key = self._accept()
        status = self.verifier.verification_status(work_key)
        self.assertEqual(status["status"], "unknown")
        self.assertEqual(status["reason"], "no-evidence")
        self.assertIs(status["merge_authorized"], False)


# ---------------------------------------------------------------------------
# Fixture evidence — the remote's own reads are deterministic
# ---------------------------------------------------------------------------

class TestScriptedRemoteReads(VerifyFixture):

    def test_read_pr_and_check_runs_fixture(self):
        work_key = self._accept()
        number = self._publish(work_key)
        pr = self.remote.read_pr(number)
        self.assertEqual(pr["head"], SHA_A)
        self.assertEqual(pr["base"], "main")
        self.assertFalse(pr["draft"])
        runs = self.remote.check_runs(SHA_A)
        self.assertEqual([r["name"] for r in runs],
                         ["Code Quality & Build", "Security Scan"])
        self.assertIsNone(self.remote.read_pr("999"))

    def test_gh_cli_read_shapes(self):
        """GhCliRemote builds the documented argv for both reads."""
        from factory_kit.publication.remote import GhCliRemote

        calls = []

        class Probe(GhCliRemote):
            def _run(self, argv):
                calls.append(argv)
                if argv[1] == "pr":
                    return {"number": 7, "url": "u", "state": "OPEN",
                            "isDraft": False, "headRefOid": SHA_A,
                            "headRefName": BRANCH,
                            "baseRefName": "main"}
                return {"check_runs": [
                    {"id": 5, "name": "n", "status": "completed",
                     "conclusion": "success",
                     "app": {"slug": "github-actions"},
                     "details_url": "u"}]}

        remote = Probe("R", "o/r")
        pr = remote.read_pr(7)
        self.assertEqual(pr["head"], SHA_A)
        self.assertEqual(pr["head_branch"], BRANCH)
        runs = remote.check_runs(SHA_A)
        self.assertEqual(runs[0]["app"], "github-actions")
        self.assertIn("check-runs", calls[1][-1])


if __name__ == "__main__":
    unittest.main()
