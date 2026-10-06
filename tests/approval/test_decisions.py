#!/usr/bin/env python3
"""One-use revision-bound approval decisions — issue #16 (Task 3.2,
PRD §3.2 F12, §6.4 Approval, §7.1).

Each test class maps to an acceptance criterion:

- A1 — the durable request commits *before* presentation: request ID,
  action=merge, repository/PR target, exact head/base SHA,
  evidence/preview/config/policy digests, issuance and expiry; the
  presentation carries the repository/PR link, head/base, preview
  URL, observed smoke, merge method and explicit expiry.
- A2 — typed decisions only: a current allowlisted numeric Telegram
  actor in the configured conversation, on the unambiguous
  request/target; durable decision ID, restricted actor, action,
  target, receipt/outcome and consumed/revoked state, twinned by
  ``approval_decided``/``approval_invalidated``.
- A3 — default 60-minute expiry; forged/replayed/expired/rejected/
  revoked decisions grant nothing and retain auditable reasons; a
  rejection leaves the work human-blocked without re-prompting.
- A4 — repeated/concurrent approvals and restarts produce one
  accepted decision and at most one consumable grant; awaiting
  survives restart without replaying chat history as authority.
- A5 — moved head/base, config/policy drift, failed/missing checks,
  unhealthy/stale preview, revoked actor, canceled/paused task all
  invalidate standing authority; resume never revives it.
- A6 — decisions never broaden action, scope, revision or expiry;
  decision history is append-only.
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
from factory_kit.preview import (  # noqa: E402
    PreviewService, ScriptedPreview)
from factory_kit.verification import (  # noqa: E402
    VerificationService)

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

#: The manifest's allowlisted numeric Telegram IDs (CFG02).
USER = 123456789
CHAT = -1001234567890
USER_REF = f"telegram:{USER}"
CHAT_REF = f"telegram:{CHAT}"

HEAD_A = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
HEAD_B = "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222"
HEAD_C = "eeee5555eeee5555eeee5555eeee5555eeee5555"
BASE_SHA = "cccc3333cccc3333cccc3333cccc3333cccc3333"
BASE_B = "dddd4444dddd4444dddd4444dddd4444dddd4444"
CHECKS = [{"name": "ci", "conclusion": "success"}]


def effective(**overrides):
    """The validated manifest effective config, with per-group patches."""
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


class ApprovalFixture(unittest.TestCase):
    """Registered repo + accepted work + verified evidence + verified
    preview + the approval surface wired to all three invalidation
    hooks (verification evidence, preview retire, control pause/
    cancel)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "in.db"
        self.eff = self._effective()
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
        self._wire(self.store)
        self._cid = [0]

    def _effective(self):
        """The fixture's effective config — subclasses override to
        declare manifest options *before* the registration binds the
        config/policy digests."""
        return effective()

    def _wire(self, store):
        """(Re)build every service on one store — used again after a
        simulated restart so the reopened file is the only authority."""
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

    def _now(self):
        return self.clock[0]

    def _reopen(self):
        """Simulated controller restart: close, reopen the same file,
        rewire the services. Chat history is gone; the durable rows
        are the only authority that survives."""
        self.store.close()
        self._wire(durable.IntakeStore(self.db_path))

    def _accept(self, issue=42, sender="luongnv89"):
        env = {"delivery_id": f"d-{issue}", "channel": "reconciliation",
               "repo_id": self.repo_id, "repository": self.full_name,
               "issue": issue, "action": "reconcile", "sender": sender,
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.intake.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        self.registrations.record_work(self.repo_id, work_key)
        return work_key

    def _verify(self, work_key, head_sha=HEAD_A, base_sha=BASE_SHA,
                status="verified", pr_number=17, review_id="rev-1"):
        """Append the independently-verified observation of the exact
        head — the F04 evidence the approval request binds."""
        with self.store.transact() as tx:
            tx.record_evidence(
                f"ev-{head_sha[:6]}-{len(self.store.evidence_rows())}",
                work_key=work_key, seq=tx.next_seq(),
                pr_number=pr_number,
                pr_url=f"https://github.test/{self.full_name}/pull/"
                       f"{pr_number}",
                head_sha=head_sha, base_name="main", base_sha=base_sha,
                checks=CHECKS, review_id=review_id,
                review_session="sess-1", contract_digest="cd-1",
                status=status, reason="checks-green"
                if status == "verified" else f"evidence-{status}")
        return self.store.latest_evidence(work_key)

    def _deploy(self, work_key, head_sha=HEAD_A):
        out = self.preview.deploy_preview(work_key, head_sha=head_sha)
        assert out["outcome"] in ("verified", "converged"), out
        return out

    def _ready(self, issue=42, head_sha=HEAD_A):
        """Work + verified evidence + verified preview — the A1
        prerequisite set an approval request may bind."""
        work_key = self._accept(issue=issue)
        self._verify(work_key, head_sha=head_sha)
        self._deploy(work_key, head_sha=head_sha)
        return work_key

    def _request(self, work_key):
        return self.approval.request_approval(work_key)

    def _approve(self, request_id=None, work_key=None, user=USER,
                 chat=CHAT, verdict="approve", command_id=None):
        return self.approval.decide(
            request_id=request_id, work_key=work_key,
            command_id=command_id,
            actor_ref=f"telegram:{user}", chat_ref=f"telegram:{chat}",
            verdict=verdict)

    def _msg(self, action, *, issue=42, generation=1, user=USER,
             chat=CHAT, command_id=None, **fields):
        self._cid[0] += 1
        msg = {"command_id": command_id or f"c-{self._cid[0]:03d}",
               "actor": {"user_id": user},
               "chat": {"chat_id": chat},
               "action": action, "repo_id": self.repo_id,
               "issue": issue, "generation": generation}
        msg.update(fields)
        return msg

    def _events(self, kind, work_key=None):
        return [r for r in self.store.event_rows(kind)
                if work_key is None or r.get("work_key") == work_key]


# ---------------------------------------------------------------------------
# A1 — request commits before presentation
# ---------------------------------------------------------------------------

class TestA1RequestIssuance(ApprovalFixture):

    def test_request_commits_then_presents(self):
        work_key = self._ready()
        out = self._request(work_key)
        self.assertEqual(out["outcome"], "requested", out)
        row = self.store.get_approval_request(out["request_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "awaiting")
        self.assertEqual(row["action"], "merge")
        self.assertEqual(row["target"], f"{self.full_name}#17")
        self.assertEqual(row["pr_number"], "17")
        self.assertEqual(row["head_sha"], HEAD_A)
        self.assertEqual(row["base_name"], "main")
        self.assertEqual(row["base_sha"], BASE_SHA)
        self.assertEqual(row["merge_method"], "squash")
        self.assertTrue(row["evidence_digest"])
        self.assertTrue(row["preview_digest"])
        self.assertEqual(row["config_digest"],
                         schema.effective_digest(self.eff))
        self.assertEqual(row["policy_digest"],
                         schema.policy_digest(self.eff))
        self.assertEqual(row["generation"], 1)
        # A1 — the presentation is built from the committed row: repo
        # link, head/base, preview URL, smoke result, method, expiry.
        p = out["presentation"]
        self.assertEqual(p["repository"], self.full_name)
        self.assertEqual(p["pr_url"], row["pr_url"])
        self.assertEqual(p["head_sha"], HEAD_A)
        self.assertEqual(p["base_sha"], BASE_SHA)
        self.assertEqual(p["merge_method"], "squash")
        self.assertTrue(p["preview_url"].startswith("https://preview-"))
        self.assertEqual(p["smoke_observed"]["http"], "200")
        self.assertTrue(p["expires_iso"])
        self.assertEqual(p["expires_epoch"], row["expires_epoch"])
        self.assertIn("APPROVAL REQUIRED", p["text"])
        self.assertIn(row["request_id"], p["text"])
        self.assertIn("approve ", p["approve_command"])
        # the awaiting state is durable in the queue surface
        queue = self.store.queue_entry(work_key)
        self.assertEqual(queue["state"], "awaiting-approval")

    def test_default_expiry_sixty_minutes(self):
        work_key = self._ready()
        out = self._request(work_key)
        row = self.store.get_approval_request(out["request_id"])
        self.assertAlmostEqual(
            row["expires_epoch"] - self.clock[0], 3600.0, places=3)
        self.assertEqual(self.eff["endpoint"]["merge"]
                         ["approval_expiry_minutes"], 60)

    def test_denied_without_current_evidence(self):
        work_key = self._accept()
        self._deploy_unchecked = None
        out = self._request(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "evidence-not-current")
        self.assertIsNone(self.store.live_approval_request(work_key))

    def test_denied_without_verified_preview(self):
        work_key = self._accept()
        self._verify(work_key)
        out = self._request(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "preview-not-current")

    def test_denied_on_blocked_work(self):
        work_key = self._ready()
        with self.store.transact() as tx:
            tx.set_work_state(work_key, "blocked", reason="x")
        out = self._request(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "work-not-active")

    def test_denied_on_paused_work(self):
        work_key = self._ready()
        with self.store.transact() as tx:
            tx.set_pause(work_key, "paused", stage="verification")
        out = self._request(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "task-paused")

    def test_converged_repeat_no_second_grant(self):
        work_key = self._ready()
        first = self._request(work_key)
        again = self._request(work_key)
        self.assertEqual(again["outcome"], "converged")
        self.assertEqual(again["request_id"], first["request_id"])
        self.assertEqual(again["presentation"]["request_id"],
                         first["request_id"])
        rows = self.store.approval_request_rows(work_key)
        self.assertEqual(len(rows), 1)

    def test_moved_revision_supersedes_live_request(self):
        work_key = self._ready()
        first = self._request(work_key)
        # revision moved: fresh verified observation + verified preview
        # on HEAD_B — the old bound authority dies in the same commit.
        self._verify(work_key, head_sha=HEAD_B)
        self._deploy(work_key, head_sha=HEAD_B)
        second = self._request(work_key)
        self.assertEqual(second["outcome"], "requested")
        self.assertNotEqual(second["request_id"], first["request_id"])
        old = self.store.get_approval_request(first["request_id"])
        self.assertEqual(old["state"], "invalidated")
        # the deploy's same-commit invalidation lands first — the
        # grant died the moment its bound preview did
        self.assertEqual(old["reason"], "preview-moved")
        new = self.store.get_approval_request(second["request_id"])
        self.assertEqual(new["head_sha"], HEAD_B)
        live = self.store.live_approval_requests(work_key)
        self.assertEqual([r["request_id"] for r in live],
                         [second["request_id"]])


# ---------------------------------------------------------------------------
# A2 — typed restricted decisions
# ---------------------------------------------------------------------------

class TestA2TypedDecisions(ApprovalFixture):

    def _issued(self, issue=42):
        work_key = self._ready(issue=issue)
        out = self._request(work_key)
        assert out["outcome"] == "requested", out
        return work_key, out["request_id"]

    def test_approve_commits_durable_decision(self):
        work_key, req = self._issued()
        out = self._approve(request_id=req)
        self.assertEqual(out["outcome"], "approved", out)
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "approved")
        self.assertEqual(row["actor_ref"], USER_REF)
        dec = self.store.get_approval_decision(out["decision_id"])
        self.assertIsNotNone(dec)
        self.assertEqual(dec["request_id"], req)
        self.assertEqual(dec["work_key"], work_key)
        self.assertEqual(dec["actor_ref"], USER_REF)
        self.assertEqual(dec["chat_ref"], CHAT_REF)
        self.assertEqual(dec["action"], "merge")
        self.assertEqual(dec["target"], f"{self.full_name}#17")
        self.assertEqual(dec["verdict"], "approve")
        self.assertEqual(dec["outcome"], "accepted")
        self.assertEqual(dec["state"], "accepted")
        self.assertTrue(dec["received_at"] and dec["decided_at"])
        # §7.1 typed twin
        events = self._events("approval_decided")
        self.assertEqual(len(events), 1)
        detail = json.loads(events[0]["detail"])
        self.assertEqual(detail["request_id"], req)
        self.assertEqual(detail["actor_ref"], USER_REF)
        self.assertEqual(detail["outcome"], "accepted")

    def test_approve_via_control_command(self):
        work_key, req = self._issued()
        out = self.control.handle(
            self._msg("approve", request_id=req))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["decision_outcome"], "approved")
        self.assertIn("APPROVED", out["text"])
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "approved")
        # the command record committed alongside the decision
        records = self.store.control_rows(work_key)
        self.assertEqual(records[-1]["action"], "approve")
        self.assertEqual(records[-1]["outcome"], "accepted")

    def test_control_approve_resolves_live_request(self):
        work_key, req = self._issued()
        out = self.control.handle(self._msg("approve"))  # no request_id
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["request_id"], req)

    def test_approve_text_command_natural_language(self):
        work_key, req = self._issued()
        out = self.control.handle(self._msg(
            None, text=f"approve repo {self.repo_id} issue #42 gen 1"))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["request_id"], req)

    def test_forged_actor_denied_and_recorded(self):
        work_key, req = self._issued()
        out = self._approve(request_id=req, user=999999)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "forged-actor")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "awaiting")  # grant not spent
        decs = self.store.approval_decision_rows(request_id=req)
        self.assertEqual(len(decs), 1)
        self.assertEqual(decs[0]["outcome"], "denied")
        self.assertEqual(decs[0]["reason"], "forged-actor")
        self.assertEqual(decs[0]["actor_ref"], "telegram:999999")

    def test_forged_actor_via_control(self):
        work_key, req = self._issued()
        out = self.control.handle(
            self._msg("approve", request_id=req, user=999999))
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "forged-actor")
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "awaiting")

    def test_wrong_chat_denied(self):
        work_key, req = self._issued()
        out = self._approve(request_id=req, chat=-555)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "wrong-chat")

    def test_non_numeric_actor_denied(self):
        work_key, req = self._issued()
        out = self.approval.decide(
            request_id=req, actor_ref="hacker-handle",
            chat_ref=CHAT_REF, verdict="approve")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "no-actor")

    def test_unknown_request_denied(self):
        work_key, req = self._issued()
        out = self._approve(request_id="apr-forged", work_key=work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "unknown-request")
        decs = self.store.approval_decision_rows()
        self.assertEqual(decs[-1]["request_id"], "apr-forged")
        # an explicit forged id never falls back to the live request
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "awaiting")

    def test_target_mismatch_denied(self):
        work_key, req = self._issued(issue=42)
        other_key = self._ready(issue=43)
        other = self._request(other_key)
        out = self._approve(request_id=req, work_key=other_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "target-mismatch")
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "awaiting")
        self.assertEqual(
            self.store.get_approval_request(
                other["request_id"])["state"], "awaiting")

    def test_decision_copies_bound_scope_verbatim(self):
        work_key, req = self._issued()
        out = self._approve(request_id=req)
        request = self.store.get_approval_request(req)
        dec = self.store.get_approval_decision(out["decision_id"])
        # A6 — the decision inherits action/target/revision from the
        # request; nothing in the decision can widen them.
        self.assertEqual(dec["action"], request["action"])
        self.assertEqual(dec["target"], request["target"])
        self.assertEqual(request["head_sha"], HEAD_A)
        self.assertEqual(request["base_sha"], BASE_SHA)


# ---------------------------------------------------------------------------
# A3 — expiry, replay, rejection, revocation grant nothing
# ---------------------------------------------------------------------------

class TestA3DenialSemantics(ApprovalFixture):

    def _issued(self, issue=42):
        work_key = self._ready(issue=issue)
        out = self._request(work_key)
        assert out["outcome"] == "requested", out
        return work_key, out["request_id"]

    def test_replayed_approve_denied(self):
        work_key, req = self._issued()
        first = self._approve(request_id=req)
        self.assertEqual(first["outcome"], "approved")
        again = self._approve(request_id=req)
        self.assertEqual(again["outcome"], "denied")
        self.assertEqual(again["reason"], "replayed")
        accepted = [d for d in self.store.approval_decision_rows(req)
                    if d["outcome"] == "accepted"]
        self.assertEqual(len(accepted), 1)
        denied = [d for d in self.store.approval_decision_rows(req)
                  if d["outcome"] == "denied"]
        self.assertEqual(denied[-1]["reason"], "replayed")

    def test_expired_request_denies_and_marks(self):
        work_key, req = self._issued()
        self.clock[0] += 3601.0
        out = self._approve(request_id=req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "expired")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "expired")
        events = self._events("approval_invalidated")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["reason"], "expired")

    def test_expiry_boundary(self):
        work_key, req = self._issued()
        row = self.store.get_approval_request(req)
        self.clock[0] = float(row["expires_epoch"])  # exactly at expiry
        out = self._approve(request_id=req)
        self.assertEqual(out["outcome"], "approved", out)
        # a request one second past expiry denies
        self.clock[0] += 1.0
        out2 = self.approval.decide(request_id=req, actor_ref=USER_REF,
                                    chat_ref=CHAT_REF, verdict="reject")
        self.assertEqual(out2["outcome"], "denied")
        self.assertIn(out2["reason"], ("replayed", "expired"))

    def test_expired_consume_denied(self):
        work_key, req = self._issued()
        self._approve(request_id=req)
        self.clock[0] += 3601.0
        out = self.approval.consume(req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "expired")
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "expired")

    def test_reject_blocks_work_without_reprompt(self):
        work_key, req = self._issued()
        out = self._approve(request_id=req, verdict="reject")
        self.assertEqual(out["outcome"], "rejected")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "rejected")
        dec = self.store.get_approval_decision(out["decision_id"])
        self.assertEqual(dec["outcome"], "rejected")
        self.assertEqual(dec["verdict"], "reject")
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "blocked")
        # A3 — no automatic repeated approval prompts: a rejected task
        # is human-blocked and a new request is denied.
        again = self._request(work_key)
        self.assertEqual(again["outcome"], "denied")
        self.assertIsNone(self.store.live_approval_request(work_key))

    def test_reject_via_control(self):
        work_key, req = self._issued()
        out = self.control.handle(self._msg("reject", request_id=req))
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["decision_outcome"], "rejected")
        self.assertIn("REJECTION RECORDED", out["text"])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "blocked")

    def test_rejected_request_decide_denied(self):
        work_key, req = self._issued()
        self._approve(request_id=req, verdict="reject")
        out = self._approve(request_id=req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "state-rejected")

    def test_consumed_request_replay_denied(self):
        work_key, req = self._issued()
        self._approve(request_id=req)
        spent = self.approval.consume(req)
        self.assertEqual(spent["outcome"], "consumed")
        out = self._approve(request_id=req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "replayed")

    def test_consume_is_one_use(self):
        work_key, req = self._issued()
        self._approve(request_id=req)
        first = self.approval.consume(req)
        self.assertEqual(first["outcome"], "consumed")
        self.assertEqual(first["action"], "merge")
        self.assertEqual(first["target"], f"{self.full_name}#17")
        self.assertEqual(first["head_sha"], HEAD_A)
        self.assertEqual(first["merge_method"], "squash")
        second = self.approval.consume(req)
        self.assertEqual(second["outcome"], "denied")
        self.assertEqual(second["reason"], "replayed")
        dec = self.store.get_approval_decision(first["decision_id"])
        self.assertEqual(dec["state"], "consumed")

    def test_consume_denied_before_decision(self):
        work_key, req = self._issued()
        out = self.approval.consume(req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "state-awaiting")

    def test_consume_rechecks_lapse_after_config_drift(self):
        """An approved grant that drifted between decide and consume
        is denied + invalidated at the consume boundary — the sweep's
        cadence is not the only guard."""
        work_key, req = self._issued()
        self._approve(request_id=req)
        self.eff["endpoint"]["merge"]["method"] = "rebase"
        self.approval.configs[self.repo_id] = self.eff
        out = self.approval.consume(req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "stale-config")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        self.assertEqual(row["reason"], "stale-config")
        dec = self.store.get_approval_decision(row["decision_id"])
        self.assertEqual(dec["state"], "revoked")

    def test_consume_rechecks_lapse_after_actor_revoked(self):
        """A revoked allowlist voids a standing grant at consume —
        the decided actor must still hold the permit."""
        work_key, req = self._issued()
        self._approve(request_id=req)
        self.eff["authorization"]["telegram_users"] = [999]
        self.approval.configs[self.repo_id] = self.eff
        out = self.approval.consume(req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "actor-revoked")
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "invalidated")


# ---------------------------------------------------------------------------
# A4 — one accepted decision under races and restarts
# ---------------------------------------------------------------------------

class TestA4OnceUnderRaceAndRestart(ApprovalFixture):

    def _issued(self, issue=42):
        work_key = self._ready(issue=issue)
        out = self._request(work_key)
        assert out["outcome"] == "requested", out
        return work_key, out["request_id"]

    def test_concurrent_approvals_single_winner(self):
        work_key, req = self._issued()
        results = []
        barrier = threading.Barrier(2)

        def go():
            barrier.wait(timeout=10)
            results.append(self._approve(request_id=req))

        t1, t2 = threading.Thread(target=go), threading.Thread(target=go)
        t1.start(); t2.start(); t1.join(15); t2.join(15)
        outcomes = sorted(r["outcome"] for r in results)
        self.assertEqual(outcomes, ["approved", "denied"], results)
        self.assertEqual(
            [r["reason"] for r in results if r["outcome"] == "denied"],
            ["replayed"])
        accepted = [d for d in self.store.approval_decision_rows(req)
                    if d["outcome"] == "accepted"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "approved")

    def test_repeated_control_approvals_single_winner(self):
        # Repeated button presses (distinct callback IDs, serialized
        # dispatch — the CAS race itself is proven by the direct
        # decide race above) still land exactly one accepted decision.
        work_key, req = self._issued()
        results = [self.control.handle(
            self._msg("approve", command_id=f"tap-{i}"))
            for i in range(4)]
        wins = [r for r in results if r.get("decision_outcome")
                == "approved"]
        self.assertEqual(len(wins), 1, results)
        denials = [r["reason"] for r in results
                   if r.get("decision_outcome") == "denied"]
        self.assertEqual(denials, ["replayed"] * 3, results)
        accepted = [d for d in self.store.approval_decision_rows(req)
                    if d["outcome"] == "accepted"]
        self.assertEqual(len(accepted), 1)

    def test_repeated_command_id_deduplicated(self):
        work_key, req = self._issued()
        first = self._approve(request_id=req, command_id="cmd-1")
        self.assertEqual(first["outcome"], "approved")
        again = self._approve(request_id=req, command_id="cmd-1")
        self.assertEqual(again["outcome"], "duplicate")
        self.assertEqual(again["decision_id"], first["decision_id"])
        self.assertEqual(len(self.store.approval_decision_rows(req)), 1)

    def test_restart_preserves_awaiting(self):
        work_key, req = self._issued()
        self._reopen()
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "awaiting")
        self.assertEqual(
            [r["request_id"] for r in self.approval.awaiting_approvals()],
            [req])
        # the durable request — not chat history — still decides, once.
        out = self._approve(request_id=req)
        self.assertEqual(out["outcome"], "approved")
        again = self._approve(request_id=req)
        self.assertEqual(again["outcome"], "denied")

    def test_restart_after_decision_consumable_once(self):
        work_key, req = self._issued()
        self._approve(request_id=req)
        self._reopen()
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "approved")
        spent = self.approval.consume(req)
        self.assertEqual(spent["outcome"], "consumed")
        again = self.approval.consume(req)
        self.assertEqual(again["outcome"], "denied")

    def test_consume_inside_caller_transaction(self):
        work_key, req = self._issued()
        self._approve(request_id=req)
        # Task 3.3's seam: consume + the merge intent in ONE commit.
        with self.store.transact() as tx:
            res = self.approval.consume_tx(tx, req)
            self.assertEqual(res["outcome"], "consumed")
            tx.record_event("merge_intent_created", work_key=work_key,
                            reason=req)
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "consumed")


# ---------------------------------------------------------------------------
# A5 — invalidation on every lapse class
# ---------------------------------------------------------------------------

class TestA5Invalidation(ApprovalFixture):

    def _issued(self, issue=42, approve=True):
        work_key = self._ready(issue=issue)
        out = self._request(work_key)
        assert out["outcome"] == "requested", out
        req = out["request_id"]
        if approve:
            self._approve(request_id=req)
        return work_key, req

    def test_head_move_invalidates(self):
        work_key, req = self._issued()
        self._verify(work_key, head_sha=HEAD_B)
        out = self.approval.invalidate_for_evidence(work_key)
        self.assertEqual(out["invalidated"],
                         [{"request_id": req,
                           "reason": "evidence-moved"}])
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        self.assertEqual(row["reason"], "evidence-moved")
        dec = self.store.get_approval_decision(row["decision_id"])
        self.assertEqual(dec["state"], "revoked")
        self.assertFalse(self.approval.status(work_key)["consumable"])

    def test_failed_checks_invalidate(self):
        work_key, req = self._issued()
        self._verify(work_key, status="failed")
        out = self.approval.invalidate_for_evidence(work_key)
        self.assertEqual(out["invalidated"][0]["reason"],
                         "evidence-not-current")

    def test_mark_stale_invalidates_via_verifier_hook(self):
        work_key, req = self._issued()
        out = self.verifier.mark_stale(work_key, "head-moved")
        self.assertEqual(out["outcome"], "stale")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        self.assertEqual(row["reason"], "evidence-not-current")
        self.assertEqual(len(self._events("approval_invalidated")), 1)

    def test_fresh_review_identity_invalidates(self):
        # a *new* review identity is a fresh evidence set even at the
        # same head — A5: reverification requires fresh approval.
        work_key, req = self._issued()
        self._verify(work_key, review_id="rev-2")
        out = self.approval.invalidate_for_evidence(work_key)
        self.assertEqual(out["invalidated"],
                         [{"request_id": req,
                           "reason": "evidence-moved"}])

    def test_identical_reverification_keeps_grant(self):
        # identical binding content is *unchanged* evidence — the
        # grant survives a byte-for-byte re-observation.
        work_key, req = self._issued()
        with self.store.transact() as tx:
            tx.record_evidence(
                "ev-copy", work_key=work_key, seq=tx.next_seq(),
                pr_number=17,
                pr_url=f"https://github.test/{self.full_name}/pull/17",
                head_sha=HEAD_A, base_name="main", base_sha=BASE_SHA,
                checks=CHECKS, review_id="rev-1",
                review_session="sess-1", contract_digest="cd-1",
                status="verified", reason="checks-green")
        out = self.approval.invalidate_for_evidence(work_key)
        self.assertEqual(out["invalidated"], [])
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "approved")

    def test_base_move_invalidates(self):
        work_key, req = self._issued()
        self._verify(work_key, base_sha=BASE_B)
        out = self.approval.invalidate_for_evidence(work_key)
        self.assertEqual(out["invalidated"][0]["reason"],
                         "evidence-moved")

    def test_preview_retire_invalidates(self):
        work_key, req = self._issued()
        self.preview.invalidate(work_key, "reconciled-offline")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        self.assertEqual(row["reason"], "preview-moved")

    def test_preview_supersede_invalidates(self):
        # approval bound to preview A; a redeployed preview B
        # supersedes A — the retire hook voids the grant bound to the
        # dead evidence.
        work_key, req = self._issued()
        self._verify(work_key, head_sha=HEAD_B)
        self._deploy(work_key, head_sha=HEAD_B)
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        self.assertEqual(row["reason"], "preview-moved")
        # a fresh request on HEAD_B then binds preview B
        out2 = self._request(work_key)
        self.assertEqual(out2["outcome"], "requested", out2)
        self.assertEqual(self.store.get_approval_request(
            out2["request_id"])["head_sha"], HEAD_B)

    def test_config_drift_invalidates(self):
        work_key, req = self._issued(approve=False)
        drifted = effective(authorization={
            **self.eff["authorization"],
            "telegram_chats": [-1001234567890, -1]})
        self.approval.configs[self.repo_id] = drifted
        out = self.approval.decide(
            request_id=req, actor_ref=USER_REF, chat_ref=CHAT_REF,
            verdict="approve")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "stale-config")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        self.assertEqual(row["reason"], "stale-config")

    def test_stale_config_via_sweep(self):
        work_key, req = self._issued()
        drifted = effective()
        drifted["endpoint"] = dict(drifted["endpoint"])
        drifted["endpoint"]["merge"] = dict(
            drifted["endpoint"]["merge"], method="merge")
        self.approval.configs[self.repo_id] = drifted
        report = self.approval.sweep()
        self.assertEqual([h["reason"] for h in
                          report["invalidated"]], ["stale-config"])
        self.assertEqual(report["invalidated"][0]["request_id"], req)

    def test_actor_revoked_via_sweep(self):
        work_key, req = self._issued(approve=True)
        revoked = effective(authorization={
            **self.eff["authorization"], "telegram_users": [777]})
        self.approval.configs[self.repo_id] = revoked
        report = self.approval.sweep()
        self.assertEqual([h["reason"] for h in
                          report["invalidated"]], ["actor-revoked"])
        self.assertEqual(report["invalidated"][0]["request_id"], req)

    def test_expired_via_sweep(self):
        work_key, req = self._issued(approve=False)
        self.clock[0] += 3601.0
        report = self.approval.sweep()
        self.assertEqual(report["expired"], [req])
        self.assertEqual(self.store.get_approval_request(req)["state"],
                         "expired")

    def test_pause_invalidates_in_same_commit(self):
        work_key, req = self._issued()
        out = self.control.handle(self._msg("pause"))
        self.assertTrue(out["ok"], out)
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        self.assertEqual(row["reason"], "task-paused")
        dec = self.store.get_approval_decision(row["decision_id"])
        self.assertEqual(dec["state"], "revoked")

    def test_cancel_invalidates_in_same_commit(self):
        work_key, req = self._issued()
        out = self.control.handle(self._msg("cancel"))
        self.assertTrue(out["ok"], out)
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        self.assertEqual(row["reason"], "canceled")
        dec = self.store.get_approval_decision(row["decision_id"])
        self.assertEqual(dec["state"], "revoked")

    def test_resume_cannot_revive(self):
        work_key, req = self._issued()
        self.control.handle(self._msg("pause"))
        out = self.control.handle(self._msg("resume"))
        self.assertTrue(out["ok"], out)
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")
        # no revival: decide on the dead request denies, and a fresh
        # request requires fresh evidence + preview anyway.
        denied = self._approve(request_id=req)
        self.assertEqual(denied["outcome"], "denied")
        self.assertEqual(denied["reason"], "state-invalidated")

    def test_fenced_invalidates_via_sweep(self):
        work_key, req = self._issued(approve=False)
        self.store.fence_work(work_key, "cancel", 1)
        report = self.approval.sweep()
        self.assertEqual([h["reason"] for h in
                          report["invalidated"]], ["fenced"])
        self.assertEqual(report["invalidated"][0]["request_id"], req)

    def test_work_terminal_invalidates_via_sweep(self):
        work_key, req = self._issued(approve=False)
        with self.store.transact() as tx:
            tx.set_work_state(work_key, "parked", reason="x")
        report = self.approval.sweep()
        self.assertEqual(report["invalidated"][0]["reason"],
                         "work-terminal")

    def test_decide_rechecks_lapse_inside_commit(self):
        work_key, req = self._issued(approve=False)
        self.store.fence_work(work_key, "cancel", 1)
        out = self._approve(request_id=req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "fenced")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["state"], "invalidated")


# ---------------------------------------------------------------------------
# A6 — append-only audit + no broadening
# ---------------------------------------------------------------------------

class TestA6AuditAndBounds(ApprovalFixture):

    def test_audit_trail_append_only(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        # forged attempt, then the real approval, then an invalidation
        self._approve(request_id=req, user=999999)
        ok = self._approve(request_id=req)
        self.preview.invalidate(work_key, "gone")
        decs = self.store.approval_decision_rows(req)
        self.assertEqual(len(decs), 2)  # denied + accepted, never edited
        outcomes = [d["outcome"] for d in decs]
        self.assertEqual(sorted(outcomes), ["accepted", "denied"])
        kinds = [e["kind"] for e in self.store.event_rows()
                 if e["kind"].startswith("approval_")]
        self.assertIn("approval_decided", kinds)
        self.assertIn("approval_invalidated", kinds)
        inv = self._events("approval_invalidated")[-1]
        detail = json.loads(inv["detail"])
        self.assertEqual(detail["request_id"], req)
        self.assertEqual(detail["prior_state"], "approved")

    def test_bound_fields_cannot_be_rewritten(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        with self.store.transact() as tx:
            with self.assertRaises(durable.IntakeStoreError):
                tx.update_approval_request(req, head_sha=HEAD_B)
            with self.assertRaises(durable.IntakeStoreError):
                tx.update_approval_request(req, expires_epoch=0.0)
            with self.assertRaises(durable.IntakeStoreError):
                tx.update_approval_request(req, action="delete")
            with self.assertRaises(durable.IntakeStoreError):
                tx.update_approval_request(req, target="other/repo#1")
        row = self.store.get_approval_request(req)
        self.assertEqual(row["head_sha"], HEAD_A)
        self.assertEqual(row["action"], "merge")

    def test_status_surface(self):
        work_key = self._ready()
        st = self.approval.status(work_key)
        self.assertEqual(st["state"], "none")
        self.assertFalse(st["consumable"])
        self.assertFalse(st["merge_authorized"])
        req = self._request(work_key)["request_id"]
        st = self.approval.status(work_key)
        self.assertEqual(st["state"], "awaiting")
        self.assertFalse(st["consumable"])
        self._approve(request_id=req)
        st = self.approval.status(work_key)
        self.assertEqual(st["state"], "approved")
        self.assertTrue(st["consumable"])
        self.assertFalse(st["merge_authorized"])

    def test_dedup_find_control_replays_command(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        msg = self._msg("approve", request_id=req, command_id="c-x")
        first = self.control.handle(msg)
        self.assertTrue(first["ok"], first)
        replay = self.control.handle(dict(msg))
        self.assertEqual(replay["outcome"], "duplicate")
        accepted = [d for d in self.store.approval_decision_rows(req)
                    if d["outcome"] == "accepted"]
        self.assertEqual(len(accepted), 1)


# ---------------------------------------------------------------------------
# Operator-CLI channel — the verified GitHub login deciding locally
# (endpoint.merge.approval_channels entry "operator-cli")
# ---------------------------------------------------------------------------

class TestOperatorChannel(ApprovalFixture):
    """``decide_operator``: the same shared post-authorization tail as
    the Telegram channel, with authority proven by the CLI credential's
    *verified* login (``github:<login>`` in ``cli:<host>``)."""

    def _effective(self):
        """The channel declared in the manifest-equivalent effective
        config — bound into the registered digests."""
        eff = effective()
        eff["endpoint"]["merge"]["approval_channels"] = [
            "telegram", "operator-cli"]
        return eff

    def _op(self, request_id, login="luongnv89", verified="luongnv89",
            verdict="approve", **kw):
        return self.approval.decide_operator(
            request_id=request_id, github_login=login,
            verified_login=verified, verdict=verdict,
            host="operator-box", **kw)

    def test_approve_via_operator_cli(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        out = self._op(req)
        self.assertEqual(out["outcome"], "approved")
        self.assertEqual(
            self.store.get_approval_request(req)["state"], "approved")
        rows = self.store.approval_decision_rows(req)
        self.assertEqual(rows[0]["actor_ref"], "github:luongnv89")
        self.assertEqual(rows[0]["chat_ref"], "cli:operator-box")

    def test_reject_via_operator_cli(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        out = self._op(req, verdict="reject")
        self.assertEqual(out["outcome"], "rejected")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "blocked")

    def test_forged_actor_mismatch_denied(self):
        """The claimed login must equal the credential's verified
        login — a spoofed ``--actor`` can never borrow authority."""
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        out = self._op(req, login="luongnv89", verified="other-user")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "forged-actor")

    def test_not_allowlisted_denied(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        out = self._op(req, login="mallory", verified="mallory")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "forged-actor")

    def test_malformed_login_denied_no_actor(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        out = self._op(req, login="", verified="")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "no-actor")

    def test_replay_denied(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        self.assertEqual(self._op(req)["outcome"], "approved")
        again = self._op(req)
        self.assertEqual(again["outcome"], "denied")
        self.assertEqual(again["reason"], "replayed")

    def test_expired_denied(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        self.clock[0] += 3601                 # past the 60-min expiry
        out = self._op(req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "expired")

    def test_actor_revoked_after_removal(self):
        """A granted operator approval dies when the login leaves
        ``authorization.github_actors`` — the lapse recheck names
        ``actor-revoked``, not generic drift."""
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        self.assertEqual(self._op(req)["outcome"], "approved")
        self.eff["authorization"]["github_actors"] = []
        report = self.approval.sweep()
        self.assertEqual(
            self.store.get_approval_request(req)["state"],
            "invalidated")
        self.assertIn("actor-revoked",
                      [h["reason"] for h in report["invalidated"]])


class TestOperatorChannelDisabled(ApprovalFixture):
    """The default channel list is ``["telegram"]`` — an operator
    decision on a manifest that never opted in is denied."""

    def test_channel_not_enabled_denied(self):
        work_key = self._ready()
        req = self._request(work_key)["request_id"]
        out = self.approval.decide_operator(
            request_id=req, github_login="luongnv89",
            verified_login="luongnv89", verdict="approve",
            host="operator-box")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "channel-not-enabled")


if __name__ == "__main__":
    unittest.main()
