#!/usr/bin/env python3
"""Unit tests for factory_kit.setup.remove — F08 removal (issue #19).

Maps to issue #19 acceptance criteria:

- A1 — a reviewable uninstall plan is built from *recorded* ownership
  (owned paths/checksums, remote-effect intents, the registration row,
  the intake work/preview inventory); apply refuses unaccepted or
  digest-tampered plans, removes only unchanged owned resources, and
  leaves unrelated repository bytes identical.
- A2 — edits to factory-owned files are preserved (``user-modified`` is
  never deleted) unless the operator binds a specific reviewed
  ``resolution: "discard"``; unrelated dirty files survive byte-for-
  byte; reinstall is idempotent with no duplicate integrations.
- A3 — intake stops first (readiness ``removing``/dispatch ``denied``),
  the active-generation fence commits before termination, and
  unconfirmed worker exit quarantines + blocks every destructive phase.
- A4 — the task-history choice is explicit: ``retain``, ``export`` or
  ``delete`` — no default. ``delete`` is scoped to the one authority
  key; shared Hermes/IDD/skill/token resources are never in scope.
- A5 — owned provider previews are removed through the port; foreign
  deployments refuse removal; provider outages leave a visible
  ``cleanup-pending`` backlog, never a false removal claim.
- A6 — fixtures cover clean uninstall, edited owned files, unrelated
  dirty files, active descendants, unconfirmed termination, shared
  infrastructure, each history choice, and before/after filesystem and
  remote-identity comparisons.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_setup_remove.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "setup_repo.py"

sys.path.insert(0, str(ROOT))
from factory_kit.config.registration import RegistrationStore  # noqa: E402
from factory_kit.durable.store import IntakeStore, work_key_for  # noqa: E402
from factory_kit.preview.port import PreviewError, ScriptedPreview  # noqa: E402
from factory_kit.setup import apply as apply_mod  # noqa: E402
from factory_kit.setup import plan as plan_mod  # noqa: E402
from factory_kit.setup import remove as remove_mod  # noqa: E402
from factory_kit.setup.ownership import SetupStore  # noqa: E402

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)

REPO_ID = "R_TEST0001"
AUTHORITY = f"gh:{REPO_ID}"


class _StubTerminator:
    """A ``terminate_work`` seam with the ExecutionLane signature.

    Asserts the removal already committed the generation fence before
    termination is attempted (A3 ordering), then reports ``outcome``.
    """

    def __init__(self, store, outcome="confirmed"):
        self.store = store
        self.outcome = outcome
        self.calls = []

    def terminate_work(self, work_key, *, reason=None, deadline_s=None):
        fence = self.store.fence_state(work_key)
        self.calls.append({"work_key": work_key, "reason": reason,
                           "fenced_before": bool(fence and
                                                 fence["fenced"])})
        if self.outcome == "confirmed":
            self.store.resolve_fence(work_key, "confirmed",
                                     "stub terminator: all handles dead")
            self.store.set_work_state(work_key, "canceled",
                                      reason=reason or "uninstall")
        else:
            self.store.resolve_fence(work_key, "quarantined",
                                     "stub terminator: exit uncertain")
            self.store.set_work_state(work_key, "quarantined",
                                      reason="termination-uncertain")
        return {"termination": self.outcome}


class RemoveFixture(unittest.TestCase):
    """An installed fixture: owned manifest, effects, registration."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = fx.build_repo(Path(self.tmp.name) / "repo")
        self.manifest = Path(self.tmp.name) / "candidate.yml"
        self.manifest.write_text(fx.manifest_text(), encoding="utf-8")
        self.store_path = Path(self.tmp.name) / "setup-state.json"
        self.reg_path = Path(self.tmp.name) / "registrations.json"
        self.db_path = Path(self.tmp.name) / "intake.db"
        self.baseline = fx.snapshot(self.repo)

    # -- helpers ------------------------------------------------------------

    def _store(self):
        return SetupStore(str(self.store_path))

    def _reg(self):
        return RegistrationStore(str(self.reg_path))

    def _intake(self):
        return IntakeStore(str(self.db_path))

    def _install(self, **kw):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        accepted = plan_mod.accept(plan, "operator-1")
        result = apply_mod.apply_plan(
            accepted, self.repo, self._store(),
            registration_store=self._reg(),
            supported_versions=["factory-kit/0.1.0", "manifest/1"], **kw)
        self.assertIn(result["outcome"], ("applied", "idempotent"),
                      result)
        return result

    def _removal_plan(self, **kw):
        return remove_mod.inspect_removal(
            self.repo, self._store(),
            registration_store=kw.pop("registration_store",
                                    self._reg()),
            intake_store=kw.pop("intake_store", self._intake()), **kw)

    def _apply_removal(self, plan=None, *, history="retain",
                       accepted_by="operator-1", **kw):
        plan = plan if plan is not None else self._removal_plan()
        accepted = remove_mod.accept_removal(
            plan, accepted_by, history=history)
        kw.setdefault("registration_store", self._reg())
        kw.setdefault("intake_store", self._intake())
        return remove_mod.apply_removal(
            accepted, self.repo, self._store(), **kw)

    def _add_work(self, intake, *, issue=42, generation=1,
                  state="active", attempt=False, repo_id=REPO_ID):
        authority = f"gh:{repo_id}"
        wk = work_key_for(authority, issue, generation)
        intake.insert_work(
            wk, authority, repo_id, issue, generation, "task-1", state,
            "f" * 64, "2026-10-05T01:00:00Z", "{}", "c" * 64, "p" * 64)
        if attempt:
            intake.begin_execution_attempt(
                wk, attempt_id="att-1", role="implementation",
                task_id="task-1", generation=generation,
                session_id="sess-1", runtime="test-rt", model="m",
                skills="{}", config_digest="c" * 64,
                policy_digest="p" * 64, workspace="/tmp/ws",
                limits="{}", now_epoch=time.time())
        return wk

    def _add_preview(self, intake, wk, preview_id="pv-1",
                     deployment_id="dpl-1", state="verified"):
        with intake.transact() as tx:
            tx.insert_preview(preview_id, work_key=wk, generation=1,
                              provider="vercel",
                              deployment_id=deployment_id,
                              state=state)


# ---------------------------------------------------------------------------
# A1 — reviewable plan + gated apply
# ---------------------------------------------------------------------------

class TestPlanReview(RemoveFixture):

    def test_plan_lists_owned_scope_and_is_readonly(self):
        self._install()
        before = fx.snapshot(self.repo)
        plan = self._removal_plan()
        # Read-only: nothing on disk changed by inspecting.
        self.assertEqual(fx.snapshot(self.repo), before)
        ids = {e["id"] for e in plan["files"]}
        self.assertEqual(ids, {"file:.factory-kit.yml"})
        self.assertEqual(plan["files"][0]["action"], "remove")
        effects = {e["effect_id"] for e in plan["remote_effects"]}
        self.assertEqual(effects, {"webhook:intake-events",
                                   "dependency:approved-skill-pins"})
        self.assertEqual(plan["registration"]["action"], "tombstone")
        self.assertEqual(plan["registration"]["authority_key"],
                         AUTHORITY)
        self.assertTrue(plan["appliable"])
        self.assertEqual(len(plan["removal_digest"]), 64)
        self.assertIsNone(plan["acceptance"])
        # The review surface explains consequences + never-scope (A4).
        self.assertTrue(plan["history"]["required"])
        self.assertEqual(plan["history"]["choices"],
                         ["retain", "export", "delete"])
        self.assertTrue(plan["history"]["consequences"])
        never = " ".join(plan["shared_infrastructure"]
                         ["never_removed"])
        self.assertIn(".gitissue.yml", never)
        self.assertIn("Hermes", never)
        # The preserved checksum set covers unrelated bytes for
        # post-apply re-verification.
        preserved = {r["path"] for r in plan["preserved"]}
        self.assertIn("app.py", preserved)
        self.assertIn("scratch-notes.txt", preserved)
        self.assertNotIn(".factory-kit.yml", preserved)

    def test_plan_detects_edited_owned_file(self):
        self._install()
        (self.repo / ".factory-kit.yml").write_text(
            "factory_kit: 1\n# developer edits\n", encoding="utf-8")
        plan = self._removal_plan()
        entry = plan["files"][0]
        self.assertEqual(entry["state"], "user-modified")
        self.assertEqual(entry["action"], "preserve")
        self.assertIn("discard", entry["detail"])

    def test_nothing_to_remove_is_a_reviewable_plan(self):
        plan = self._removal_plan()  # no install at all
        self.assertTrue(plan["appliable"])
        result = self._apply_removal(plan)
        self.assertEqual(result["outcome"], "nothing-to-remove")

    def test_unaccepted_and_tampered_plans_refused(self):
        self._install()
        plan = self._removal_plan()
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.apply_removal(
                plan, self.repo, self._store(),
                registration_store=self._reg())
        accepted = remove_mod.accept_removal(plan, "op", history="retain")
        accepted["files"][0]["path"] = "app.py"
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.apply_removal(
                accepted, self.repo, self._store(),
                registration_store=self._reg())
        # Acceptance bound to a different digest refuses.
        accepted = remove_mod.accept_removal(plan, "op",
                                             history="retain")
        accepted["acceptance"]["removal_digest"] = "0" * 64
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.apply_removal(
                accepted, self.repo, self._store(),
                registration_store=self._reg())

    def test_tampered_project_id_refused(self):
        """``project_id`` feeds the intake-stop/tombstone fallback at
        apply — it is digest-covered, so mutating it after acceptance
        re-keys the plan and apply refuses instead of acting on an
        unreviewed identity."""
        self._install()
        plan = self._removal_plan()
        plan["project_id"] = "R_VICTIM99"
        accepted = remove_mod.accept_removal(plan, "op",
                                             history="retain")
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.apply_removal(
                accepted, self.repo, self._store(),
                registration_store=self._reg(),
                intake_store=self._intake())
        # The other identity was never touched.
        reg = self._reg().get(REPO_ID)
        self.assertFalse(reg.get("tombstone", False))

    def test_path_traversal_entry_never_deleted(self):
        """A forged plan entry naming an unowned path is preserved —
        state is re-derived at apply, and only *recorded owned* files
        can ever be unlinked."""
        self._install()
        plan = self._removal_plan()
        plan["files"].append({
            "id": "file:evil", "kind": "file", "path": "../escape.txt",
            "action": "remove", "state": "unchanged"})
        plan["removal_digest"] = remove_mod.removal_digest(plan)
        accepted = remove_mod.accept_removal(plan, "op",
                                             history="retain")
        result = remove_mod.apply_removal(
            accepted, self.repo, self._store(),
            registration_store=self._reg())
        self.assertFalse(
            (Path(self.tmp.name) / "escape.txt").exists())
        removed = result["phases"]["files"]["removed"]
        self.assertNotIn("file:evil", removed)
        kept = {p["id"] for p in result["phases"]["files"]["preserved"]}
        self.assertIn("file:evil", kept)


# ---------------------------------------------------------------------------
# A1/A2 — clean uninstall, edits preserved, bytes identical
# ---------------------------------------------------------------------------

class TestCleanUninstall(RemoveFixture):

    def test_clean_uninstall_removes_only_owned(self):
        self._install()
        result = self._apply_removal(history="retain")
        self.assertEqual(result["outcome"], "removed", result)
        self.assertFalse((self.repo / ".factory-kit.yml").exists())
        # Every unrelated byte identical — the preservation oracle.
        after = fx.snapshot(self.repo)
        self.assertEqual(after, self.baseline,
                         "unrelated content must be byte-for-byte")
        self.assertEqual(result["verification"]["preserved_violations"],
                         [])
        self.assertEqual(result["verification"]["still_present"], [])
        # Registration is an identity tombstone — provable, not silent.
        self.assertTrue(self._reg().is_tombstone(REPO_ID))
        tombstone = self._reg().get(REPO_ID)
        self.assertEqual(tombstone["authority_key"], AUTHORITY)
        self.assertEqual(tombstone["removed_by"], "operator-1")
        self.assertTrue(result["verification"]["tombstone"])
        # Effects marked removal-recorded — kept, never dropped (A2).
        effects = self._store().effects()
        for rec in effects.values():
            self.assertEqual(rec["removal"]["status"],
                             "removal-recorded")
        # Ownership rows dropped for the confirmed-removed file.
        self.assertEqual(self._store().owned_files(), {})

    def test_durable_removal_event_recorded(self):
        self._install()
        self._apply_removal(history="retain")
        events = [e for e in self._store().events("setup_removed")]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["outcome"], "removed")
        self.assertEqual(events[0]["history"], "retain")
        self.assertEqual(events[0]["repo_id"], REPO_ID)
        intake = self._intake()
        typed = [e for e in intake.event_rows("setup_removed")]
        self.assertEqual(len(typed), 1,
                         "intake-side typed twin recorded (retain)")

    def test_edited_owned_file_preserved(self):
        self._install()
        edited = "factory_kit: 1\n# operator kept these edits\n"
        (self.repo / ".factory-kit.yml").write_text(edited,
                                                    encoding="utf-8")
        result = self._apply_removal(history="retain")
        self.assertEqual(result["outcome"], "removed")
        self.assertTrue((self.repo / ".factory-kit.yml").exists())
        self.assertEqual(
            (self.repo / ".factory-kit.yml").read_text(), edited)
        self.assertEqual(result["preserved_edits"][0]["path"],
                         ".factory-kit.yml")
        # Ownership row survives — the file is still owned + tracked.
        self.assertIn(".factory-kit.yml",
                      self._store().owned_files())

    def test_reviewed_discard_resolution_removes_edited_file(self):
        """The operator's specific reviewed resolution — bound into the
        accepted digest — overrides preservation for that entry."""
        self._install()
        (self.repo / ".factory-kit.yml").write_text(
            "# operator edits to discard\nfactory_kit: 1\n",
            encoding="utf-8")
        plan = self._removal_plan()
        plan["files"][0]["resolution"] = "discard"
        plan["removal_digest"] = remove_mod.removal_digest(plan)
        result = self._apply_removal(plan, history="retain")
        self.assertEqual(result["outcome"], "removed")
        self.assertFalse((self.repo / ".factory-kit.yml").exists())
        self.assertEqual(result["preserved_edits"], [])

    def test_unrelated_dirty_files_never_touched(self):
        self._install()
        # Extra dirty work appearing after install is still preserved.
        (self.repo / "wip.py").write_text("# brand-new WIP\n",
                                          encoding="utf-8")
        self._apply_removal(history="retain")
        self.assertEqual((self.repo / "wip.py").read_text(),
                         "# brand-new WIP\n")
        self.assertIn("developer WIP",
                      (self.repo / "app.py").read_text())
        self.assertTrue((self.repo / "scratch-notes.txt").exists())

    def test_missing_owned_file_dropped_not_failed(self):
        self._install()
        os.unlink(self.repo / ".factory-kit.yml")
        plan = self._removal_plan()
        self.assertEqual(plan["files"][0]["action"], "absent")
        result = self._apply_removal(plan, history="retain")
        self.assertEqual(result["outcome"], "removed")
        self.assertEqual(result["phases"]["files"]["absent"],
                         ["file:.factory-kit.yml"])

    def test_shared_infrastructure_untouched(self):
        self._install()
        # A second registration for another repo must survive.
        other_manifest = Path(self.tmp.name) / "other.yml"
        other_manifest.write_text(
            fx.manifest_text(repo_id="R_OTHER999"), encoding="utf-8")
        other_eff = None
        from factory_kit.config import schema as _schema
        other_eff = _schema.load_manifest_file(str(other_manifest))
        other_reg = self._reg()
        other_reg.register(other_eff,
                           readiness={"verdict": "ready",
                                      "dispatch": "allowed",
                                      "blockers": []},
                           supported_versions=["factory-kit/0.1.0",
                                               "manifest/1"])
        self._apply_removal(history="retain")
        reg = self._reg()  # fresh view — JSON stores load at open
        self.assertFalse(reg.is_tombstone("R_OTHER999"))
        self.assertTrue(reg.is_tombstone(REPO_ID))
        for path in (".gitissue.yml", ".github/workflows/ci.yml",
                     "README.md"):
            self.assertTrue((self.repo / path).exists(), path)

    def test_reinstall_after_removal_is_idempotent(self):
        self._install()
        self._apply_removal(history="retain")
        # Reinstall: fresh reviewed plan, applied once → clean apply.
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        accepted = plan_mod.accept(plan, "operator-2")
        result = apply_mod.apply_plan(
            accepted, self.repo, self._store(),
            registration_store=self._reg(),
            supported_versions=["factory-kit/0.1.0", "manifest/1"])
        self.assertEqual(result["outcome"], "applied")
        self.assertTrue((self.repo / ".factory-kit.yml").exists())
        reg = self._reg().get(REPO_ID)
        self.assertFalse(reg.get("tombstone", False))
        self.assertEqual(reg.get("prior_tombstone", {})
                         .get("removed_by"), "operator-1")
        # No duplicate integrations — the effect ids are stable, so the
        # recorded rows are re-reported existing, not duplicated.
        self.assertEqual(len(self._store().effects()), 2)
        # And a second apply is still an empty effective diff.
        second = apply_mod.apply_plan(
            accepted, self.repo, self._store(),
            registration_store=self._reg(),
            supported_versions=["factory-kit/0.1.0", "manifest/1"])
        self.assertEqual(second["outcome"], "idempotent")


# ---------------------------------------------------------------------------
# A3 — intake stop, generation fence, confirmed/quarantined termination
# ---------------------------------------------------------------------------

class TestAuthorityFence(RemoveFixture):

    def test_intake_stopped_before_state_removed(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active")
        self._reg().record_work(REPO_ID, wk)
        plan = self._removal_plan()
        result = self._apply_removal(plan, history="retain")
        phases = result["phases"]
        self.assertEqual(phases["intake_stopped"]["outcome"],
                         "removing")
        self.assertEqual(phases["intake_stopped"]["parked_work"], [wk])
        # A delivery denies at the gate — the readiness verdict the
        # intake gate reads flipped durably before state removal.
        denial = self._deliver_one(intake)
        self.assertEqual(denial["outcome"], "denied")
        self.assertEqual(denial["reason"], "registration-not-ready")
        self.assertEqual(result["outcome"], "removed")
        self.assertEqual(
            phases["authority"]["settled"][0]["termination"],
            "confirmed")

    def test_tombstoned_registration_denies_intake(self):
        """After removal the tombstone carries no readiness — intake
        denies deliveries for the removed identity (A4 consequence)."""
        self._install()
        intake = self._intake()
        self._apply_removal(history="retain")
        denial = self._deliver_one(intake)
        self.assertEqual(denial["outcome"], "denied")
        self.assertEqual(denial["reason"], "registration-not-ready")

    def _deliver_one(self, intake):
        """One valid signed webhook event through the real intake
        service — the gate answer a post-removal delivery gets."""
        from factory_kit.config import schema as _schema
        from factory_kit.intake import service as intake_service
        eff = _schema.load_manifest_file(str(self.manifest))
        secret = "remove-test-hmac"
        svc = intake_service.IntakeService(
            intake, self._reg(), {REPO_ID: eff},
            signing_secret=secret, opt_in_label="factory-kit")
        delivery = "dlv-remove-1"
        env = {
            "delivery_id": delivery, "channel": "webhook",
            "repo_id": REPO_ID, "repository": "testowner/testrepo",
            "issue": 42, "action": "labeled", "sender": "testowner",
            "labels": ["factory-kit"], "issue_title": "t",
            "issue_body": "## Acceptance Criteria\n\n- [ ] A1. x\n",
            "issue_revision": "2026-10-05T01:00:00Z",
            "signature": intake_service.compute_signature(
                secret, delivery, REPO_ID, 42),
        }
        return svc.deliver(env)

    def test_fence_commits_before_terminator_runs(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active", attempt=True)
        self._reg().record_work(REPO_ID, wk)
        term = _StubTerminator(intake, outcome="confirmed")
        plan = self._removal_plan()
        result = self._apply_removal(plan, history="retain",
                                     terminator=term)
        self.assertEqual(result["outcome"], "removed")
        self.assertTrue(term.calls[0]["fenced_before"],
                        "generation fence must commit before "
                        "termination is attempted (A3)")
        self.assertEqual(intake.fence_state(wk)["termination"],
                         "confirmed")

    def test_unconfirmed_termination_quarantines_and_blocks(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active", attempt=True)
        self._reg().record_work(REPO_ID, wk)
        plan = self._removal_plan()
        # No terminator wired → live durable attempt is uncertain.
        result = self._apply_removal(plan, history="retain")
        self.assertEqual(result["outcome"], "quarantined")
        self.assertIn("recovery", result["quarantine"])
        self.assertEqual(result["quarantine"]["work_keys"], [wk])
        # Nothing destructive ran: file intact, effects unmarked, the
        # registration is *stopping intake* but not tombstoned.
        self.assertTrue((self.repo / ".factory-kit.yml").exists())
        for rec in self._store().effects().values():
            self.assertNotIn("removal", rec)
        reg = self._reg().get(REPO_ID)
        self.assertFalse(reg.get("tombstone", False))
        self.assertEqual(reg["readiness"]["verdict"], "removing")
        self.assertEqual(reg["readiness"]["dispatch"], "denied")
        self.assertEqual(intake.fence_state(wk)["termination"],
                         "quarantined")
        self.assertEqual(intake.get_work(wk)["state"], "quarantined")
        alerts = [a for a in intake._rows("alerts")
                  if a["kind"] == "removal-quarantine"]
        self.assertEqual(len(alerts), 1)
        # The quarantined removal is itself a durable event.
        ev = self._store().events("setup_removed")
        self.assertEqual(ev[-1]["outcome"], "quarantined")

    def test_terminator_uncertain_quarantines(self):
        self._install()
        intake = self._intake()
        self._add_work(intake, state="active", attempt=True)
        term = _StubTerminator(intake, outcome="quarantined")
        plan = self._removal_plan()
        result = self._apply_removal(plan, history="retain",
                                     terminator=term)
        self.assertEqual(result["outcome"], "quarantined")
        self.assertTrue((self.repo / ".factory-kit.yml").exists())

    def test_terminator_exception_is_quarantine_never_claim(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active", attempt=True)

        def boom(work_key):
            raise RuntimeError("worker unreachable")

        plan = self._removal_plan()
        result = self._apply_removal(plan, history="retain",
                                     terminator=boom)
        self.assertEqual(result["outcome"], "quarantined")
        self.assertTrue((self.repo / ".factory-kit.yml").exists())
        self.assertEqual(intake.fence_state(wk)["termination"],
                         "quarantined")

    def test_live_authority_without_store_refuses(self):
        """A plan binding live work rows cannot apply without the
        intake store — the generation fence cannot commit (A3)."""
        self._install()
        intake = self._intake()
        self._add_work(intake, state="active")
        plan = self._removal_plan()
        accepted = remove_mod.accept_removal(plan, "op",
                                             history="retain")
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.apply_removal(
                accepted, self.repo, self._store(),
                registration_store=self._reg(), intake_store=None)
        self.assertTrue((self.repo / ".factory-kit.yml").exists())
        self.assertFalse(self._reg().is_tombstone(REPO_ID))

    def test_resolved_fence_allows_rerun(self):
        """Recovery: operator confirms exit, resolves the quarantine,
        re-runs — the second plan applies cleanly."""
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active", attempt=True)
        plan = self._removal_plan()
        result = self._apply_removal(plan, history="retain")
        self.assertEqual(result["outcome"], "quarantined")
        # Operator confirms the worker exited, resolves the fence, ends
        # the durable attempt record, then re-inspects + re-applies.
        intake.resolve_fence(wk, "resolved",
                             "operator confirmed worker exit")
        intake.finish_execution_attempt(
            "att-1", verdict="canceled", outcome="canceled",
            active_seconds=None, usage=None, duration_s=None)
        plan2 = self._removal_plan()
        result2 = self._apply_removal(plan2, history="retain")
        self.assertEqual(result2["outcome"], "removed")
        self.assertFalse((self.repo / ".factory-kit.yml").exists())


# ---------------------------------------------------------------------------
# A4 — explicit history choice: retain / export / delete
# ---------------------------------------------------------------------------

class TestHistoryChoice(RemoveFixture):

    def test_choice_is_required_and_enumerated(self):
        self._install()
        plan = self._removal_plan()
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.accept_removal(plan, "op", history=None)
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.accept_removal(plan, "op", history="purge")
        # A forged acceptance without the choice also refuses.
        accepted = remove_mod.accept_removal(plan, "op",
                                             history="retain")
        accepted["acceptance"]["history"] = None
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.apply_removal(
                accepted, self.repo, self._store(),
                registration_store=self._reg(),
                intake_store=self._intake())

    def test_retain_keeps_history(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active")
        self._apply_removal(history="retain")
        self.assertIsNotNone(intake.get_work(wk),
                             "retain leaves durable rows inspectable")

    def test_export_writes_repo_scoped_history(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active")
        export = Path(self.tmp.name) / "hist" / "export.json"
        result = self._apply_removal(history="export",
                                     export_path=str(export))
        self.assertTrue(export.is_file())
        payload = json.loads(export.read_text())
        self.assertEqual(payload["authority_key"], AUTHORITY)
        self.assertIn(wk, payload["work_keys"])
        self.assertIn(wk, {r["work_key"] for r in
                           payload["tables"]["work"]})
        self.assertTrue(
            result["phases"]["history"]["export"]["verified"])
        # Export keeps the rows — it is a copy, not a delete.
        self.assertIsNotNone(intake.get_work(wk))

    def test_export_needs_store_and_verifies(self):
        self._install()
        plan = self._removal_plan()
        accepted = remove_mod.accept_removal(plan, "op",
                                             history="export")
        with self.assertRaises(remove_mod.RemovalError):
            remove_mod.apply_removal(
                accepted, self.repo, self._store(),
                registration_store=self._reg(),
                intake_store=None)

    def test_delete_is_scoped_to_one_authority(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active")
        port = ScriptedPreview()
        dep = port.deploy({"head_sha": "abc123"},
                          {"preview_id": "pv-1", "work_key": wk})
        self._add_preview(intake, wk, preview_id="pv-1",
                          deployment_id=dep["deployment_id"])
        # Another identity's rows in the SAME store survive.
        other_wk = self._add_work(intake, issue=7, state="active",
                                  repo_id="R_OTHER999")
        result = self._apply_removal(history="delete",
                                     preview_port=port)
        self.assertEqual(result["outcome"], "removed")
        deleted = result["phases"]["history"]["deleted"]
        self.assertGreater(deleted.get("work", 0), 0)
        self.assertIsNone(intake.get_work(wk))
        self.assertIsNone(intake.get_preview("pv-1"))
        self.assertIsNotNone(intake.get_work(other_wk),
                             "another identity's history survives")
        # Global/shared rows untouched.
        self.assertTrue(intake._rows("meta"))
        # The tombstone still proves removal — delete never erases it.
        self.assertTrue(self._reg().is_tombstone(REPO_ID))


# ---------------------------------------------------------------------------
# A5 — provider boundary: owned previews removed, foreign + outage safe
# ---------------------------------------------------------------------------

class TestPreviewRemoval(RemoveFixture):

    def test_owned_preview_removed_foreign_preserved(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active")
        port = ScriptedPreview()
        dep = port.deploy({"head_sha": "abc123"},
                          {"preview_id": "pv-1", "work_key": wk,
                           "generation": 1})
        foreign = port.add_foreign("dpl-human-1")
        self._add_preview(intake, wk, preview_id="pv-1",
                          deployment_id=dep["deployment_id"])
        plan = self._removal_plan()
        self.assertEqual(len(plan["previews"]), 1)
        result = self._apply_removal(plan, history="retain",
                                     preview_port=port)
        self.assertEqual(result["outcome"], "removed")
        self.assertEqual(
            result["phases"]["previews"]["previews"][0]["status"],
            "removed")
        self.assertNotIn(dep["deployment_id"], port.deployments)
        self.assertIn(foreign, port.foreign,
                      "human-owned provider resource untouched")
        self.assertEqual(intake.get_preview("pv-1")["state"],
                         "removed")

    def test_provider_outage_leaves_cleanup_pending(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake, state="active")
        port = ScriptedPreview()
        dep = port.deploy({"head_sha": "abc123"},
                          {"preview_id": "pv-1", "work_key": wk})
        self._add_preview(intake, wk, preview_id="pv-1",
                          deployment_id=dep["deployment_id"])
        port.outage = True
        plan = self._removal_plan()
        result = self._apply_removal(plan, history="retain",
                                     preview_port=port)
        self.assertEqual(result["outcome"], "removed-with-pending")
        reasons = {p["reason"] for p in result["pending"]}
        self.assertIn("provider-removal-unconfirmed", reasons)
        # The durable record says cleanup-pending — never "removed".
        self.assertEqual(intake.get_preview("pv-1")["state"],
                         "cleanup-pending")
        self.assertIn(dep["deployment_id"], port.deployments,
                      "outage means the resource is still live")
        backlog = [a for a in intake._rows("alerts")
                   if a["kind"] == "preview-cleanup-backlog"]
        self.assertEqual(len(backlog), 1)
        # Recovery: provider back, sweep re-runs the removal.
        port.outage = False
        plan2 = self._removal_plan()
        result2 = self._apply_removal(plan2, history="retain",
                                      preview_port=port)
        self.assertEqual(result2["outcome"], "removed")
        self.assertNotIn(dep["deployment_id"], port.deployments)

    def test_effect_sink_absent_and_pending(self):
        self._install()

        class DownSink:
            def remove(self, effect_id, entry=None):
                raise PreviewError("provider 5xx")

        plan = self._removal_plan()
        result = self._apply_removal(plan, history="retain",
                                     effect_sink=DownSink())
        self.assertEqual(result["outcome"], "removed-with-pending")
        self.assertEqual(len(
            result["phases"]["remote_effects"]["pending"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
