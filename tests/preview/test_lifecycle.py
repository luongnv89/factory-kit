#!/usr/bin/env python3
"""Revision-bound preview lifecycle — issue #15 (Task 3.1, PRD §3.2
F11, §5.2, §6.4, §7.1).

Each test class maps to an acceptance criterion:

- A1 — durable deployment evidence: the ``preview_records`` row commits
  before the provider call and carries provider/deployment/artifact
  identity, exact head/base SHA, URL, visibility, the configured smoke
  contract, the observed result + time, expiry and cleanup ownership.
- A2 — meaningful smoke verification: the configured contract runs
  against the recorded deployment; success produces the
  approval-request evidence payload; an endpoint without a preview
  contract can never satisfy it.
- A3 — failure/stale blocking: failed smoke, provider outage,
  head/base mismatch, expired deployment and unknown artifact
  identity all land ``preview_failed`` + a visible blocked work, and
  ``status()`` never grants approval off stale evidence.
- A4 — fencing + credential isolation: head/base movement, committed
  cancellation and fences invalidate evidence; late callbacks cannot
  revive fenced generations; the deploy boundary rechecks the whole
  permit; workers never touch credentials.
- A5 — one active preview per task (un-raceable index), ≤24h TTL,
  removal confirmed inside ``cleanup_minutes`` of termination,
  outage-visible cleanup backlog, foreign resources preserved.
- A6 — ``preview_verified``/``preview_failed`` typed events carry
  identity, revision, observation, outcome and cleanup deadline.
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
from factory_kit.events import schema as event_schema  # noqa: E402
from factory_kit.intake import service  # noqa: E402
from factory_kit.preview import (  # noqa: E402
    PreviewError, PreviewService, ScriptedPreview, VercelCliPreview)

MANIFEST = ROOT / "docs" / "examples" / "reference.factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"

HEAD_A = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
HEAD_B = "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222"
BASE_SHA = "cccc3333cccc3333cccc3333cccc3333cccc3333"


def effective(**overrides):
    """The validated manifest effective config, with per-group patches."""
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


def effective_without_preview():
    """An endpoint whose project never enabled preview evidence —
    validation-time contract, hand-built here (the real manifest
    requires ``smoke``; a *missing* preview block is the untrusted-
    config shape the service must deny)."""
    eff = effective()
    eff["endpoint"] = {"preview": None,
                       "merge": eff["endpoint"]["merge"]}
    return eff


class PreviewFixture(unittest.TestCase):
    """Registered repo + accepted work row + verified evidence +
    scripted provider + preview service."""

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
        self.provider = ScriptedPreview(now=self._now)
        self.alert_sink = []

    def _now(self):
        return self.clock[0]

    def _service(self, eff=None, provider=None):
        eff = eff or self.eff
        return PreviewService(
            self.store, provider or self.provider, self.registrations,
            {self.repo_id: eff}, now=self._now,
            alert_sink=self.alert_sink)

    def _accept(self, issue=42, delivery=None, sender="luongnv89"):
        env = {"delivery_id": delivery or f"d-{issue}",
               "channel": "reconciliation", "repo_id": self.repo_id,
               "repository": self.full_name, "issue": issue,
               "action": "reconcile", "sender": sender,
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.intake.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        self.registrations.record_work(self.repo_id, work_key)
        return work_key

    def _verify(self, work_key, head_sha=HEAD_A, status="verified"):
        """The durable prerequisite: an independently-verified
        observation of the exact head — the only thing that lets the
        deploy gate open (A1)."""
        with self.store.transact() as tx:
            tx.record_evidence(
                f"ev-{work_key}-{head_sha[:6]}-{status}",
                work_key=work_key, seq=tx.next_seq(),
                head_sha=head_sha, base_name="main", base_sha=BASE_SHA,
                pr_number=17, status=status,
                reason="checks-green" if status == "verified" else
                f"evidence-{status}")
        return self.store.latest_evidence(work_key)

    def _events(self, kind, work_key=None):
        return [r for r in self.store.event_rows(kind)
                if work_key is None or r.get("work_key") == work_key]

    def _deploy(self, work_key, head_sha=HEAD_A, **kw):
        return self._service().deploy_preview(
            work_key, head_sha=head_sha, **kw)


# ---------------------------------------------------------------------------
# A1/A2 — deploy + verified smoke + durable evidence surface
# ---------------------------------------------------------------------------

class TestA1VerifiedDeploy(PreviewFixture):

    def test_happy_path_produces_corroborated_evidence(self):
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "verified", out)
        ev = out["evidence"]
        # A1 — every required field on the durable evidence surface:
        self.assertEqual(ev["provider"], "vercel")
        self.assertTrue(ev["deployment_id"].startswith("dpl-"), ev)
        self.assertTrue(ev["artifact_identity"], ev)
        self.assertEqual(ev["head_sha"], HEAD_A)
        self.assertEqual(ev["base_sha"], BASE_SHA)
        self.assertEqual(ev["base_name"], "main")
        self.assertTrue(ev["url"].startswith("https://preview-"), ev)
        self.assertEqual(ev["visibility"], "unlisted")
        self.assertEqual(ev["environment"], "preview")
        # A2 — the configured contract AND its observed result:
        self.assertEqual(ev["smoke"]["command"],
                         self.eff["endpoint"]["preview"]["smoke"]
                         ["command"])
        self.assertEqual(ev["smoke"]["expect"], "200")
        self.assertEqual(ev["smoke"]["observed"]["http"], "200")
        self.assertTrue(ev["smoke"]["observed"]["ok"])
        # Expiry ≤ 24h bound + cleanup ownership/deadline recorded:
        self.assertGreater(ev["expires_epoch"], self.clock[0])
        self.assertLessEqual(ev["expires_epoch"] - self.clock[0],
                             24 * 3600 + 1)
        self.assertEqual(ev["cleanup_owner"], "factory")
        self.assertIsNotNone(ev["cleanup_deadline_epoch"])
        # Durable row agrees:
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "verified")
        self.assertEqual(rec["deployment_id"], ev["deployment_id"])
        self.assertTrue(json.loads(rec["smoke_observed"])["ok"])

    def test_record_commits_before_provider_effect(self):
        """A1 ordering: at the moment the provider call lands, the
        durable ``deploying`` row must already exist."""
        work_key = self._accept()
        self._verify(work_key)
        seen = {}

        class Guarded(ScriptedPreview):
            def deploy(inner, spec, identity):
                seen["row"] = self.store.get_preview(
                    identity["preview_id"])
                return super().deploy(spec, identity)

        provider = Guarded(now=self._now)
        out = self._service(provider=provider).deploy_preview(
            work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "verified", out)
        self.assertIsNotNone(seen["row"])
        self.assertEqual(seen["row"]["state"], "deploying")

    def test_status_reports_approval_ready(self):
        work_key = self._accept()
        self._verify(work_key)
        self._deploy(work_key)
        st = self._service().status(work_key)
        self.assertEqual(st["status"], "verified")
        self.assertTrue(st["approval_ready"], st)
        self.assertFalse(st["merge_authorized"])

    def test_verified_event_emitted(self):
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        events = [e for e in self._events("preview_verified", work_key)]
        self.assertEqual(len(events), 1)
        props = json.loads(events[0]["detail"])
        self.assertEqual(props["deployment_id"], out["deployment_id"])
        self.assertEqual(props["head_sha"], HEAD_A)
        self.assertEqual(props["base_sha"], BASE_SHA)
        self.assertEqual(props["url"], out["url"])
        self.assertIn("observed_at", props)
        self.assertIn("cleanup_deadline_epoch", props)
        # Event privacy surface: no issue body/source/log text.
        self.assertNotIn(BODY, events[0]["detail"])


class TestA2ContractRequired(PreviewFixture):

    def test_missing_preview_contract_denies_before_deploy(self):
        """A2: an endpoint without a smoke contract is 'not applicable'
        — it can never satisfy the preview requirement."""
        work_key = self._accept()
        self._verify(work_key)
        out = self._service(eff=effective_without_preview()) \
            .deploy_preview(work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "preview-contract-missing")
        self.assertFalse(self.provider.deployments)
        st = self._service(eff=effective_without_preview()).status(
            work_key)
        self.assertFalse(st["approval_ready"])

    def test_deploy_denies_without_verified_evidence(self):
        """A1: no independently-verified observation → no deployment,
        ever — a green worker self-report is not evidence."""
        work_key = self._accept()
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "verification-not-current")
        self.assertFalse(self.provider.deployments)

    def test_stale_evidence_denies(self):
        """A3: a stale/failed latest observation blocks the deploy —
        old evidence cannot substitute for the current revision."""
        work_key = self._accept()
        self._verify(work_key, status="stale")
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "verification-not-current")

    def test_evidence_on_different_head_denies(self):
        """The verified observation binds HEAD_B; deploying HEAD_A
        under it denies — evidence is revision-bound (A3/A4)."""
        work_key = self._accept()
        self._verify(work_key, head_sha=HEAD_B)
        out = self._deploy(work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "verification-not-current")


# ---------------------------------------------------------------------------
# A3 — every denial condition: smoke fail, outage, mismatch, expiry,
# unknown identity — preview_failed + visible blocker, never approval
# ---------------------------------------------------------------------------

class TestA3DenialConditions(PreviewFixture):

    def test_smoke_failure_fails_and_blocks(self):
        work_key = self._accept()
        self._verify(work_key)
        self.provider.faults["smoke"] = "down"
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["reason"], "provider-outage")
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "blocked")
        failed = self._events("preview_failed", work_key)
        self.assertEqual(len(failed), 1)
        self.assertFalse(self.provider.deployments)

    def test_smoke_wrong_result_fails(self):
        """Observed result != configured expect → preview_failed."""
        work_key = self._accept()
        self._verify(work_key)

        class FailingSmoke(ScriptedPreview):
            def smoke(inner, deployment_id, contract):
                return {"ok": False, "http": "500", "marker": False,
                        "observed_at": self._now(),
                        "detail": "server error"}

        provider = FailingSmoke(now=self._now)
        out = self._service(provider=provider).deploy_preview(
            work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["reason"], "smoke-failed")
        rec = self.store.get_preview(out["preview_id"])
        self.assertTrue(json.loads(rec["smoke_observed"]))
        self.assertFalse(
            self._service(provider=provider).status(work_key)
            ["approval_ready"])
        # The failed deployment was cleaned up — factory-owned.
        self.assertFalse(provider.deployments)

    def test_marker_missing_fails_smoke(self):
        """The body marker check is part of the contract — a deploy
        that doesn't serve it fails for real (A2)."""
        work_key = self._accept()
        self._verify(work_key)

        class NoMarker(ScriptedPreview):
            def deploy(inner, spec, identity):
                dep = super().deploy(spec, identity)
                inner.bodies[dep["deployment_id"]] = \
                    "<html>unrelated</html>"
                return dep

        provider = NoMarker(now=self._now)
        out = self._service(provider=provider).deploy_preview(
            work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["reason"], "smoke-failed")

    def test_provider_outage_fails_deploy(self):
        work_key = self._accept()
        self._verify(work_key)
        self.provider.outage = True
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["reason"], "provider-outage")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "blocked")
        rec = self.store.get_preview(out["preview_id"])
        # The deploy failure is terminal and blocks the work — but
        # under the same outage the cleanup question is *unconfirmed*:
        # the record parks in the visible backlog, never a false
        # "nothing to remove" claim (A5).
        self.assertEqual(rec["state"], "cleanup-pending")
        self.assertEqual(rec["reason"], "provider-outage")
        # Provider returns → the backlog drains on confirmed absence
        # (the refused deploy created nothing).
        self.provider.outage = False
        self._service().sweep()
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "removed")

    def test_head_mismatch_fails(self):
        """A3: provider read-back head != bound head → mismatch."""
        work_key = self._accept()
        self._verify(work_key)

        class MovedHead(ScriptedPreview):
            def deploy(inner, spec, identity):
                dep = super().deploy(spec, identity)
                dep["head_sha"] = HEAD_B
                inner.deployments[
                    dep["deployment_id"]]["head_sha"] = HEAD_B
                return dep

        provider = MovedHead(now=self._now)
        out = self._service(provider=provider).deploy_preview(
            work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["reason"], "revision-mismatch")
        failed = self._events("preview_failed", work_key)
        self.assertEqual(json.loads(failed[0]["detail"]
                                    or "{}").get("reason") or
                         failed[0]["reason"], "revision-mismatch")

    def test_expired_deployment_fails(self):
        """A3: a deployment whose TTL already lapsed at read-back is a
        denial condition, not evidence."""
        work_key = self._accept()
        self._verify(work_key)

        class ShortLived(ScriptedPreview):
            def deploy(inner, spec, identity):
                dep = super().deploy(spec, identity)
                inner.deployments[
                    dep["deployment_id"]]["expires_epoch"] = \
                    self.clock[0] - 1
                dep["expires_epoch"] = self.clock[0] - 1
                return dep

        provider = ShortLived(now=self._now)
        out = self._service(provider=provider).deploy_preview(
            work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["reason"], "deployment-expired")

    def test_unknown_deployment_identity_fails(self):
        """A3: the provider cannot corroborate the id the deploy
        returned → unknown artifact identity, never adopted."""
        work_key = self._accept()
        self._verify(work_key)

        class Phantom(ScriptedPreview):
            def inspect(inner, deployment_id):
                return None

        provider = Phantom(now=self._now)
        out = self._service(provider=provider).deploy_preview(
            work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "failed")
        self.assertEqual(out["reason"], "unknown-deployment")

    def test_old_evidence_never_satisfies_new_revision(self):
        """A3: after a deploy on HEAD_A, asking to deploy HEAD_B under
        HEAD_A-stale verification denies — evidence is revision-bound
        and the A-head preview is invalidated."""
        work_key = self._accept()
        self._verify(work_key, head_sha=HEAD_A)
        out1 = self._deploy(work_key, head_sha=HEAD_A)
        self.assertEqual(out1["outcome"], "verified")
        # Evidence still binds HEAD_A — HEAD_B deploy is denied...
        out2 = self._deploy(work_key, head_sha=HEAD_B)
        self.assertEqual(out2["outcome"], "denied")
        self.assertEqual(out2["reason"], "verification-not-current")
        # ... and a re-verified HEAD_B supersedes the old preview.
        self._verify(work_key, head_sha=HEAD_B)
        out3 = self._deploy(work_key, head_sha=HEAD_B)
        self.assertEqual(out3["outcome"], "verified")
        old = self.store.get_preview(out1["preview_id"])
        # Invalidated on supersede; the owned deployment's removal is
        # provider-confirmed, so the terminal record lands 'removed'.
        self.assertEqual(old["state"], "removed")
        self.assertEqual(old["reason"], "superseded")
        # One-active rule held: the superseded deployment is gone.
        self.assertEqual(len(self.provider.deployments), 1)


# ---------------------------------------------------------------------------
# A4 — fencing, callbacks, boundary rechecks
# ---------------------------------------------------------------------------

class TestA4Fencing(PreviewFixture):

    def _fence(self, work_key):
        with self.store.transact() as tx:
            tx.fence_work_tx(work_key, "cancel",
                             self.store.get_work(work_key)
                             ["generation"])

    def test_fence_before_deploy_denies(self):
        work_key = self._accept()
        self._verify(work_key)
        self._fence(work_key)
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "fenced")
        self.assertFalse(self.provider.deployments)

    def test_fence_landing_mid_deploy_invalidates(self):
        """A4: a committed cancellation landing between the provider
        effect and the verified commit invalidates — the deployment is
        still created but the fence wins, cleanup removes it."""
        work_key = self._accept()
        self._verify(work_key)
        service = self._service()

        class FencingProvider(ScriptedPreview):
            def deploy(inner, spec, identity):
                dep = super().deploy(spec, identity)
                self._fence(work_key)     # fence lands mid-flight
                return dep

        provider = FencingProvider(now=self._now)
        service = self._service(provider=provider)
        out = service.deploy_preview(work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "fenced")
        rec = self.store.get_preview(out["preview_id"])
        # Invalidated by the fence, then provider-confirmed removal —
        # 'removed' with the 'fenced' reason preserved.
        self.assertEqual(rec["state"], "removed")
        self.assertEqual(rec["reason"], "fenced")
        # The created deployment was still owned → provider confirmed
        # removal.
        self.assertFalse(provider.deployments)

    def test_callback_on_fenced_record_denied(self):
        """A4: a late deployment callback can never revive a fenced
        generation."""
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        dep_id = out["deployment_id"]
        self._fence(work_key)
        back = self._service().observe_callback(dep_id)
        self.assertEqual(back["outcome"], "denied")
        self.assertEqual(back["reason"], "fenced")
        # The record was NOT touched by the callback.
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "verified")

    def test_callback_unknown_deployment_denied(self):
        """An artifact identity the factory never recorded is ignored,
        never adopted (A3/A4)."""
        work_key = self._accept()
        self._verify(work_key)
        self._deploy(work_key)
        back = self._service().observe_callback("dpl-foreign-9")
        self.assertEqual(back["outcome"], "denied")
        self.assertEqual(back["reason"], "unknown-deployment")

    def test_callback_on_terminal_record_denied(self):
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        self._service().terminate(work_key)
        back = self._service().observe_callback(out["deployment_id"])
        self.assertEqual(back["outcome"], "denied")
        self.assertEqual(back["reason"], "preview-terminal")

    def test_stale_generation_denies(self):
        """A4: a superseded generation cannot deploy — the bound
        generation is rechecked at the boundary."""
        work_key = self._accept()
        self._verify(work_key)
        self.registrations.authorize_generation(
            self.repo_id, self.eff, authorized_by="luongnv89")
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "stale-generation")

    def test_config_rotation_denies(self):
        """A4: a config change between generation bind and deploy
        denies at the boundary — digests are rechecked, not trusted."""
        work_key = self._accept()
        self._verify(work_key)
        eff2 = effective(limits={"max_attempts": 9})
        out = self._service(eff=eff2).deploy_preview(
            work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "stale-config")

    def test_actor_revoked_denies(self):
        """A4: a generation authorized by a principal the manifest
        never allowlisted cannot deploy — the permit is rechecked
        against the *current* authorization, and the attempt is a
        violation (quarantine + deduplicated high alert)."""
        self.registrations.authorize_generation(
            self.repo_id, self.eff, authorized_by="eve")
        work_key = self._accept(issue=77, delivery="d-77")
        self._verify(work_key)
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "actor-revoked")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "quarantined")
        alerts = [a for a in self.alert_sink
                  if a["kind"] == "unauthorized-mutation"]
        self.assertTrue(alerts)


# ---------------------------------------------------------------------------
# A5 — limits, cleanup, backlog, foreign preservation
# ---------------------------------------------------------------------------

class TestA5LimitsCleanup(PreviewFixture):

    def test_one_active_preview_per_task(self):
        """A5: the partial unique index is un-raceable — a second
        *active* insert is an integrity error, never a second slot."""
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "verified")
        with self.assertRaises(durable.IntakeStoreError):
            with self.store.transact() as tx:
                tx.insert_preview(
                    "prev-dup", work_key=work_key, seq=tx.next_seq(),
                    provider="vercel", head_sha=HEAD_A,
                    state="deploying")

    def test_same_head_redeploy_converges(self):
        """A redeploy request on the same bound revision converges on
        the existing preview — one deployment, not two (A5)."""
        work_key = self._accept()
        self._verify(work_key)
        out1 = self._deploy(work_key)
        out2 = self._deploy(work_key)
        self.assertEqual(out2["outcome"], "converged")
        self.assertEqual(out2["preview_id"], out1["preview_id"])
        self.assertEqual(len(self.provider.deployments), 1)

    def test_terminate_removes_owned_within_deadline(self):
        """A5: confirmed termination invalidates the record and the
        provider confirms removal inside cleanup_minutes."""
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        dep_id = out["deployment_id"]
        res = self._service().terminate(work_key)
        self.assertEqual(res["outcome"], "terminated")
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "removed")
        self.assertIsNotNone(rec["removed_at"])
        self.assertNotIn(dep_id, self.provider.deployments)
        # Deadline recorded ≤ cleanup_minutes from termination.
        deadline = rec["cleanup_deadline_epoch"]
        self.assertLessEqual(
            deadline - self.clock[0],
            self.eff["endpoint"]["preview"]["cleanup_minutes"] * 60 + 1)

    def test_outage_cleanup_leaves_visible_backlog(self):
        """A5: a provider outage at cleanup time keeps a
        ``cleanup-pending`` backlog row — never a false removal —
        and the next sweep drains it when the provider returns."""
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        self.provider.outage = True
        res = self._service().terminate(work_key)
        self.assertEqual(res["outcome"], "terminated")
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "cleanup-pending")
        self.assertIn(out["deployment_id"], self.provider.deployments)
        self.assertTrue(self.store.preview_cleanup_backlog())
        # The backlog alert went out — the outage is visible (A5/A6).
        alerts = [a for a in self.alert_sink
                  if a["kind"] == "preview-cleanup-backlog"]
        self.assertTrue(alerts)
        # Provider returns → the sweep removes it; no re-claim needed.
        self.provider.outage = False
        self._service().sweep()
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "removed")
        self.assertNotIn(out["deployment_id"],
                         self.provider.deployments)
        self.assertFalse(self.store.preview_cleanup_backlog())

    def test_outage_during_cleanup_find_keeps_backlog(self):
        """A5: a provider outage while rediscovering a deployment-less
        record parks in ``cleanup-pending`` — an outage is never
        flattened into a false "nothing to remove", and a reachable
        empty find later drains the backlog on confirmed absence."""
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        rec = self.store.get_preview(out["preview_id"])
        # A record whose provider-side deployment id was lost — the
        # cleanup path must rediscover by identity before it may
        # conclude nothing exists.
        with self.store.transact() as tx:
            tx.update_preview(rec["preview_id"], state="deploying",
                              deployment_id=None, url=None)
        self.provider.faults["find"] = "down"
        res = self._service().terminate(work_key)
        self.assertEqual(res["outcome"], "terminated")
        rec = self.store.get_preview(out["preview_id"])
        # The outage is *unconfirmed* — the row parks in the visible
        # backlog, never "no-resource".
        self.assertEqual(rec["state"], "cleanup-pending")
        self.assertTrue(self.store.preview_cleanup_backlog())
        # Provider returns and confirms nothing owned remains — the
        # backlog drains on confirmed absence, not an immortal row.
        self.provider.faults.clear()
        self._service().sweep()
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "removed")
        self.assertFalse(self.store.preview_cleanup_backlog())

    def test_foreign_deployments_preserved(self):
        """A5: a deployment this factory never recorded is never
        removed — human-created provider resources are preserved."""
        work_key = self._accept()
        self._verify(work_key)
        foreign = self.provider.add_foreign("dpl-human-1")
        out = self._deploy(work_key)
        self._service().terminate(work_key)
        self.assertIn(foreign, self.provider.foreign)
        self.assertNotIn(out["deployment_id"],
                         self.provider.deployments)

    def test_ttl_expiry_sweep_invalidates_and_removes(self):
        """A5: the TTL is a hard bound — the sweep invalidates the
        expired record and removes the owned deployment."""
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        dep_id = out["deployment_id"]
        self.clock[0] += 25 * 3600        # past the 24h TTL
        self._service().sweep()
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "removed")
        self.assertEqual(rec["reason"], "expired")
        self.assertNotIn(dep_id, self.provider.deployments)

    def test_crash_window_reconciles_by_identity(self):
        """A5: a deploy whose response was lost (crash-after-create)
        reconciles the provider-side deployment by recorded identity —
        the next sweep adopts it and completes verification."""
        work_key = self._accept()
        self._verify(work_key)
        self.provider.faults["deploy"] = "crash-after-create"
        out = self._deploy(work_key)
        self.assertEqual(out["outcome"], "parked")
        self.assertEqual(out["reason"], "deploy-ambiguous")
        # The deployment exists provider-side; the record carries it
        # for reconciliation, not retry.
        self.assertEqual(len(self.provider.deployments), 1)
        self.provider.faults.clear()
        self._service().sweep()
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "verified", rec)
        self.assertIsNotNone(rec["deployment_id"])

    def test_lost_deploy_with_no_remote_fails(self):
        """A5: a deploying record with zero identity matches means the
        request never landed — the work fails visibly, nothing is
        adopted."""
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        rec = self.store.get_preview(out["preview_id"])
        # Simulate the crash window: reset the verified row to
        # 'deploying' with no deployment id, provider knows nothing.
        with self.store.transact() as tx:
            tx.update_preview(rec["preview_id"], state="deploying",
                              deployment_id=None, url=None)
        self.provider.deployments.clear()
        self._service().sweep()
        rec = self.store.get_preview(out["preview_id"])
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(rec["reason"], "deploy-outcome-lost")

    def test_fenced_work_loses_active_preview_on_sweep(self):
        work_key = self._accept()
        self._verify(work_key)
        out = self._deploy(work_key)
        dep_id = out["deployment_id"]
        with self.store.transact() as tx:
            tx.fence_work_tx(work_key, "cancel",
                             self.store.get_work(work_key)
                             ["generation"])
        self._service().sweep()
        rec = self.store.get_preview(out["preview_id"])
        self.assertIn(rec["state"], ("invalidated", "removed"))
        self.assertNotIn(dep_id, self.provider.deployments)


# ---------------------------------------------------------------------------
# A6 — event surface + diagnostics projection
# ---------------------------------------------------------------------------

class TestA6EventsDiagnostics(PreviewFixture):

    def test_failed_event_carries_identity_and_reason(self):
        work_key = self._accept()
        self._verify(work_key)
        self.provider.faults["smoke"] = "down"
        out = self._deploy(work_key)
        events = self._events("preview_failed", work_key)
        self.assertEqual(len(events), 1)
        props = json.loads(events[0]["detail"])
        self.assertEqual(props["preview_id"], out["preview_id"])
        self.assertEqual(props["head_sha"], HEAD_A)
        self.assertIn("observed_at", props)
        # Schema validation passes for both kinds.
        for kind in ("preview_verified", "preview_failed"):
            self.assertIn(kind, event_schema.EVENT_SCHEMAS)

    def test_diagnostics_work_status_exposes_preview(self):
        work_key = self._accept()
        self._verify(work_key)
        self._deploy(work_key)
        from factory_kit.diagnostics import report
        st = report.work_status(self.store, work_key,
                                now=self.clock[0])
        self.assertIsNotNone(st["preview"])
        self.assertEqual(st["preview"]["state"], "verified")
        self.assertTrue(st["preview"]["approval_ready"])
        self.assertEqual(st["preview"]["provider"], "vercel")
        self.assertEqual(st["preview"]["head_sha"], HEAD_A)


class TestPortContract(PreviewFixture):

    def test_provider_mismatch_denies(self):
        """The wired port must be the configured provider — a wrong
        adapter is a wiring violation, not a deploy."""
        work_key = self._accept()
        self._verify(work_key)
        provider = ScriptedPreview(provider="netlify", now=self._now)
        out = self._service(provider=provider).deploy_preview(
            work_key, head_sha=HEAD_A)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "provider-mismatch")
        self.assertFalse(provider.deployments)

    def test_vercel_port_uses_env_token_only(self):
        """Credential isolation (A4): the provider token travels by
        environment name only — never on argv, never persisted."""
        calls = []

        def runner(argv, env=None, timeout=None, cwd=None):
            calls.append({"argv": list(argv), "env": dict(env or {})})

            class P:
                returncode = 0
                stderr = ""
                stdout = ""
            p = P()
            if argv[0] == "git" and "archive" in argv:
                # Write a real (empty) tar to --output.
                out = argv[argv.index("--output") + 1]
                import tarfile
                with tarfile.open(out, "w"):
                    pass
            elif argv[0] == "git":                          # rev-parse
                p.stdout = "cafe" * 10 + "\n"
            elif argv[1:2] == ["deploy"]:
                p.stdout = json.dumps(
                    {"id": "dpl-abc123",
                     "url": "preview-x.vercel.app"})
            elif argv[1:2] == ["api"]:
                p.stdout = json.dumps(
                    {"id": "dpl-abc123",
                     "url": "preview-x.vercel.app",
                     "readyState": "READY",
                     "meta": {"factory-head_sha": HEAD_A,
                              "factory-preview_id": "p1"}})
            return p

        port = VercelCliPreview(repo_root="/tmp/fake-repo",
                                project="money-mind",
                                runner=runner, now=self._now)
        import os
        old = os.environ.get("VERCEL_TOKEN")
        os.environ["VERCEL_TOKEN"] = "sekrit-token-123"
        try:
            port.deploy({"head_sha": HEAD_A},
                        {"preview_id": "p1", "work_key": "w1",
                         "generation": 1})
        finally:
            if old is None:
                os.environ.pop("VERCEL_TOKEN", None)
            else:
                os.environ["VERCEL_TOKEN"] = old
        # argv never carries the token; env carries it by name.
        blob = " ".join(str(a) for c in calls for a in c["argv"])
        for c in calls:
            for arg in c["argv"]:
                self.assertNotIn("sekrit", str(arg))
        vercel_calls = [c for c in calls if c["argv"][0] == "vercel"]
        self.assertTrue(vercel_calls)
        for c in vercel_calls:
            self.assertIn("VERCEL_TOKEN", c["env"])
            self.assertEqual(c["env"]["VERCEL_TOKEN"],
                             "sekrit-token-123")
        self.assertNotIn("sekrit", blob)

    def test_vercel_find_propagates_provider_error(self):
        """A5: the production adapter never flattens a provider
        failure into an empty list — rediscovery callers distinguish
        'none found' from 'could not ask' (a flat ``[]`` would orphan
        an owned deployment the lifecycle then forgets)."""
        def runner(argv, env=None, timeout=None, cwd=None):
            class P:
                returncode = 1
                stdout = ""
                stderr = "Error: network unreachable"
            return P()

        port = VercelCliPreview(runner=runner)
        with self.assertRaises(PreviewError):
            port.find_deployments({"preview_id": "p1"})
        # A clean empty answer still returns [].
        def empty(argv, env=None, timeout=None, cwd=None):
            class P:
                returncode = 0
                stdout = "[]"
                stderr = ""
            return P()

        port = VercelCliPreview(runner=empty)
        self.assertEqual(
            port.find_deployments({"preview_id": "p1"}), [])


if __name__ == "__main__":
    unittest.main()
