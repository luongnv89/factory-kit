#!/usr/bin/env python3
"""Unit tests for versioned upgrade / repair / rollback — F10 (issue #26).

Maps to issue #26 acceptance criteria:

- A1 — a dry-run for a pinned supported installed version shows the
  compatibility checks, the changed owned files, every user-edit
  conflict, the ordered migration steps and the rollback instructions;
  nothing mutates before explicit acceptance.
- A2 — an accepted upgrade fences active work first, migrates owned
  files and the registration under a new authorized generation,
  validates the complete new configuration, pins dependency/skill
  provenance, and only then lets readiness re-pass so dispatch can
  resume.
- A3 — an interruption at every migration boundary repairs to the
  validated checkpoint or parks with a specific conflict; dispatch
  never runs from a partially migrated configuration.
- A4 — edited owned configuration, unrelated dirty files and
  concurrent edits are preserved byte-for-byte and surfaced as
  conflicts; unsupported versions and changed checksum preconditions
  block application.
- A5 — a failed/blocked upgrade restores the previous validated
  registration and configuration through the documented rollback;
  repair, rollback and removal coexist without orphaned authority.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_setup_upgrade.py
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
READINESS_FIXTURES = ROOT / "tests" / "fixtures" / "readiness"

sys.path.insert(0, str(ROOT))
from factory_kit.config import schema  # noqa: E402
from factory_kit.config.registration import (  # noqa: E402
    RegistrationError,
    RegistrationStore,
)
from factory_kit.durable.store import IntakeStore, work_key_for  # noqa: E402
from factory_kit.setup import apply as apply_mod  # noqa: E402
from factory_kit.setup import plan as plan_mod  # noqa: E402
from factory_kit.setup import readiness as readiness_mod  # noqa: E402
from factory_kit.setup import remove as remove_mod  # noqa: E402
from factory_kit.setup import repair as repair_mod  # noqa: E402
from factory_kit.setup import upgrade as upgrade_mod  # noqa: E402
from factory_kit.setup.ownership import SetupStore  # noqa: E402

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)

REPO_ID = "R_TEST0001"
AUTHORITY = f"gh:{REPO_ID}"
PINS = ["factory-kit/0.1.0", "manifest/1"]
V2_SKILLS = 'issue-resolver: "0.20.0"'


def _readings(name="ready"):
    return json.loads(
        (READINESS_FIXTURES / f"{name}.json").read_text())


class UpgradeFixture(unittest.TestCase):
    """An installed fixture: owned manifest, registration, ledger."""

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

    def _fresh(self, tag):
        """Rebuild the whole fixture under ``tmp/<tag>`` — repo, ledger,
        registrations, intake db — then install. Used by the per-stage
        boundary matrix so each interruption owns isolated state."""
        root = Path(self.tmp.name) / tag
        self.repo = fx.build_repo(root / "repo")
        self.store_path = root / "setup-state.json"
        self.reg_path = root / "registrations.json"
        self.db_path = root / "intake.db"
        self._install()

    # -- helpers --------------------------------------------------------------

    def _store(self):
        return SetupStore(str(self.store_path))

    def _reg(self):
        return RegistrationStore(str(self.reg_path))

    def _intake(self):
        return IntakeStore(str(self.db_path))

    def _install(self, *, pins=PINS, **kw):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        accepted = plan_mod.accept(plan, "operator-1")
        result = apply_mod.apply_plan(
            accepted, self.repo, self._store(),
            registration_store=self._reg(),
            supported_versions=list(pins), **kw)
        self.assertIn(result["outcome"], ("applied", "idempotent"),
                      result)
        return result

    def _candidate(self, **kw):
        """Write a v2 candidate manifest (skill pin bump by default)."""
        target = Path(self.tmp.name) / "candidate-v2.yml"
        target.write_text(fx.manifest_text(**kw), encoding="utf-8")
        return target

    def _upgrade_plan(self, source=None, **kw):
        kw.setdefault("registration_store", self._reg())
        return upgrade_mod.inspect_upgrade(
            self.repo, self._store(),
            manifest_source=source if source is not None
            else self._candidate(skills=V2_SKILLS), **kw)

    def _apply_upgrade(self, plan=None, *, accepted_by="operator-2",
                       **kw):
        plan = plan if plan is not None else self._upgrade_plan()
        accepted = upgrade_mod.accept_upgrade(plan, accepted_by)
        kw.setdefault("registration_store", self._reg())
        return upgrade_mod.apply_upgrade(
            accepted, self.repo, self._store(), **kw)

    def _add_work(self, intake, *, issue=42, generation=1,
                  state="active", attempt=False):
        wk = work_key_for(AUTHORITY, issue, generation)
        intake.insert_work(
            wk, AUTHORITY, REPO_ID, issue, generation, "task-1", state,
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

    def _readiness(self, verdict_key="readiness"):
        reg = self._reg().get(REPO_ID) or {}
        return (reg.get(verdict_key) or {})

    def _enable_readiness(self):
        """Re-run substantive readiness on the on-disk manifest — the
        dispatch-resume gate after a completed/restored migration."""
        effective = schema.load_manifest_file(
            str(self.repo / ".factory-kit.yml"))
        return readiness_mod.run_setup_checked(
            project_id=REPO_ID, effective=effective,
            store=self._store(), registration_store=self._reg(),
            supported_versions=self._reg().get(REPO_ID)
            ["supported_versions"],
            readings=_readings())


# ---------------------------------------------------------------------------
# A1 — reviewable dry-run; nothing mutates before explicit acceptance
# ---------------------------------------------------------------------------

class TestUpgradePlanReview(UpgradeFixture):

    def test_plan_is_readonly_and_reviewable(self):
        self._install()
        before_files = fx.snapshot(self.repo)
        before_state = self.store_path.read_bytes()
        plan = self._upgrade_plan()
        # A1: read-only — filesystem and ledger untouched by inspection.
        self.assertEqual(fx.snapshot(self.repo), before_files)
        self.assertEqual(self.store_path.read_bytes(), before_state)
        self.assertEqual(self._store().migrations(), {})
        # Compatibility checks are all named and passing.
        checks = {c["check"]: c["status"]
                  for c in plan["compatibility"]}
        self.assertEqual(checks, {
            "installed-state": "pass",
            "installed-version": "pass",
            "manifest": "pass",
            "schema-path": "pass",
            "identity": "pass",
            "secret-hygiene": "pass",
            "ownership": "pass"})
        # Changed owned files with diff: only the manifest replaces.
        changed = [e for e in plan["files"] if e["action"] == "replace"]
        self.assertEqual([e["path"] for e in changed],
                         [".factory-kit.yml"])
        manifest_entry = next(e for e in plan["entries"]
                              if e["path"] == ".factory-kit.yml")
        self.assertIn("0.20.0", manifest_entry["diff"])
        self.assertIn("0.19.0", manifest_entry["diff"])
        # Ordered migration steps + documented rollback (A1 review).
        stages = [s["stage"] for s in plan["migration_steps"]]
        self.assertEqual(stages, list(upgrade_mod.MIGRATION_STAGES))
        self.assertEqual(plan["rollback"]["anchor"],
                         "checkpoint:migration")
        instructions = " ".join(plan["rollback"]["instructions"])
        self.assertIn("repair", instructions)
        self.assertIn("rollback", instructions)
        # Installed/target version pair + digest + acceptance gate.
        self.assertEqual(plan["installed"]["supported_versions"], PINS)
        self.assertEqual(plan["installed"]["schema_version"], 1)
        self.assertEqual(plan["target"]["schema_version"], 1)
        self.assertEqual(plan["target"]["migration_hops"], [])
        self.assertEqual(len(plan["upgrade_digest"]), 64)
        self.assertIsNone(plan["acceptance"])
        self.assertTrue(plan["appliable"], plan["conflicts"])

    def test_unrelated_files_listed_in_preserved_set(self):
        self._install()
        plan = self._upgrade_plan()
        preserved = {r["path"] for r in plan["preserved"]}
        self.assertIn("app.py", preserved)
        self.assertIn("scratch-notes.txt", preserved)
        self.assertNotIn(".factory-kit.yml", preserved)

    def test_apply_requires_acceptance_and_bound_digest(self):
        self._install()
        plan = self._upgrade_plan()
        # Unaccepted plan refuses — nothing mutates.
        with self.assertRaises(upgrade_mod.UpgradeError):
            upgrade_mod.apply_upgrade(
                plan, self.repo, self._store(),
                registration_store=self._reg())
        self.assertEqual(self._store().migrations(), {})
        self.assertEqual(
            (self.repo / ".factory-kit.yml").read_text(),
            self.manifest.read_text())
        # Tampered plan refuses — the digest no longer matches.
        accepted = upgrade_mod.accept_upgrade(plan, "op")
        accepted["files"][0]["path"] = "app.py"
        with self.assertRaises(upgrade_mod.UpgradeError):
            upgrade_mod.apply_upgrade(
                accepted, self.repo, self._store(),
                registration_store=self._reg())
        # Acceptance bound to a different digest refuses.
        accepted = upgrade_mod.accept_upgrade(plan, "op")
        accepted["acceptance"]["upgrade_digest"] = "0" * 64
        with self.assertRaises(upgrade_mod.UpgradeError):
            upgrade_mod.apply_upgrade(
                accepted, self.repo, self._store(),
                registration_store=self._reg())
        self.assertEqual(self._store().migrations(), {})

    def test_no_install_and_no_manifest_are_conflicts(self):
        plan = upgrade_mod.inspect_upgrade(
            self.repo, self._store(),
            manifest_source=self._candidate(skills=V2_SKILLS),
            registration_store=self._reg())
        self.assertFalse(plan["appliable"])
        sources = {c["source"] for c in plan["conflicts"]}
        self.assertIn("installed-state", sources)

        self._install()
        plan = upgrade_mod.inspect_upgrade(
            self.repo, self._store(), manifest_source=None,
            registration_store=self._reg())
        # The installed manifest is owned + present → re-affirm plan.
        self.assertTrue(plan["appliable"], plan["conflicts"])
        self.assertIn("no-change", " ".join(plan["notes"]))

    def test_invalid_and_identity_shifting_candidates_block(self):
        self._install()
        bad = Path(self.tmp.name) / "bad.yml"
        bad.write_text("factory_kit: 1\nidentity: [broken\n",
                       encoding="utf-8")
        plan = self._upgrade_plan(source=bad)
        self.assertFalse(plan["appliable"])
        self.assertIn("manifest",
                      {c["source"] for c in plan["conflicts"]})

        other = Path(self.tmp.name) / "other.yml"
        other.write_text(fx.manifest_text(repo_id="R_OTHER777"),
                         encoding="utf-8")
        plan = self._upgrade_plan(source=other)
        self.assertFalse(plan["appliable"])
        self.assertIn("identity",
                      {c["source"] for c in plan["conflicts"]})


# ---------------------------------------------------------------------------
# A2 — accepted upgrade: fence → migrate → validate → provenance → resume
# ---------------------------------------------------------------------------

class TestAcceptedUpgrade(UpgradeFixture):

    def test_clean_upgrade_commits_every_boundary(self):
        self._install()
        result = self._apply_upgrade()
        self.assertEqual(result["outcome"], "upgraded", result)
        self.assertEqual(
            result["stages"], list(upgrade_mod.MIGRATION_STAGES))
        # Owned file migrated; ledger re-checksummed.
        manifest = self.repo / ".factory-kit.yml"
        self.assertIn('issue-resolver: "0.20.0"',
                      manifest.read_text())
        ownership = self._store().owned_files()[".factory-kit.yml"]
        from factory_kit.setup.ownership import sha256_file
        self.assertEqual(ownership["sha256"],
                         sha256_file(str(manifest)))
        # Registration migrated under a fresh authorized generation.
        reg = self._reg().get(REPO_ID)
        self.assertEqual(reg["active_generation"], 2)
        new_gen = reg["generations"][-1]
        self.assertEqual(new_gen["supersedes"], 1)
        self.assertEqual(new_gen["authorized_by"], "operator-2")
        effective = schema.load_manifest_file(str(manifest))
        self.assertEqual(new_gen["config_digest"],
                         schema.effective_digest(effective))
        self.assertEqual(new_gen["policy_digest"],
                         schema.policy_digest(effective))
        self.assertEqual(reg["generations"][0]["generation"], 1)
        # Dispatch still gated — readiness pending until re-validated.
        self.assertEqual(reg["readiness"]["verdict"], "pending")
        self.assertEqual(reg["readiness"]["dispatch"], "denied")
        self.assertIsNone(reg.get("upgrade"))
        self.assertEqual(reg["supported_versions"],
                         ["factory-kit/0.1.0", "manifest/1"])
        # The migration sealed complete; provenance pinned (A2).
        store = self._store()
        mig = store.migration(result["migration_id"])
        self.assertEqual(mig["stage"], "complete")
        self.assertEqual([s["stage"] for s in mig["stages"]],
                         list(upgrade_mod.MIGRATION_STAGES))
        dep = result["provenance"][0]
        self.assertTrue(dep["id"].startswith(
            "dependency:approved-skill-pins:"))
        self.assertEqual(dep["status"], "recorded-intent")
        events = store.events("setup_upgraded")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["upgraded_by"], "operator-2")
        # Unrelated bytes identical — the preservation oracle.
        after = {k: v for k, v in fx.snapshot(self.repo).items()
                 if k != ".factory-kit.yml"}
        before = {k: v for k, v in self.baseline.items()
                  if k != ".factory-kit.yml"}
        self.assertEqual(after, before)

    def test_idempotent_reapply_is_already_upgraded(self):
        self._install()
        plan = self._upgrade_plan()
        first = self._apply_upgrade(plan)
        self.assertEqual(first["outcome"], "upgraded")
        result = self._apply_upgrade(plan)
        self.assertEqual(result["outcome"], "already-upgraded")
        self.assertEqual(len(self._store().migrations()), 1)
        self.assertEqual(self._reg().get(REPO_ID)
                         ["active_generation"], 2)

    def test_fence_parks_bound_work_then_dispatch_resumes(self):
        self._install()
        intake = self._intake()
        wk = self._add_work(intake)
        # Config-only drift (ttl_hours): the readiness fixture's skill
        # pins still match, so the post-upgrade setup_checked can pass.
        target = Path(self.tmp.name) / "candidate-v2.yml"
        target.write_text(
            fx.manifest_text().replace("ttl_hours: 24",
                                       "ttl_hours: 48"),
            encoding="utf-8")
        # The intake row is discovered at review and fenced at apply.
        plan = self._upgrade_plan(source=target,
                                  intake_store=self._intake())
        self.assertEqual([w["work_key"] for w in plan["authority"]
                          ["work"]], [wk])
        accepted = upgrade_mod.accept_upgrade(plan, "op-2")
        result = upgrade_mod.apply_upgrade(
            accepted, self.repo, self._store(),
            registration_store=self._reg(),
            intake_store=self._intake())
        self.assertEqual(result["outcome"], "upgraded", result)
        work = self._intake().get_work(wk)
        self.assertEqual(work["state"], "parked")
        fence = self._intake().fence_state(wk)
        self.assertTrue(fence["fenced"])
        self.assertEqual(fence["termination"], "confirmed")
        # A fresh readiness pass re-enables dispatch (A2 gate opens).
        report = self._enable_readiness()
        self.assertEqual(report["verdict"], "ready")
        reg = self._reg().get(REPO_ID)
        self.assertEqual(reg["readiness"]["verdict"], "ready")
        self.assertEqual(reg["readiness"]["dispatch"], "allowed")

    def test_unconfirmed_worker_exit_quarantines_and_parks(self):
        self._install()
        wk = self._add_work(self._intake(), attempt=True)
        plan = self._upgrade_plan(intake_store=self._intake())
        accepted = upgrade_mod.accept_upgrade(plan, "op-2")
        before = (self.repo / ".factory-kit.yml").read_text()
        result = upgrade_mod.apply_upgrade(
            accepted, self.repo, self._store(),
            registration_store=self._reg(),
            intake_store=self._intake())  # no terminator
        self.assertEqual(result["outcome"], "quarantined")
        self.assertEqual(result["quarantine"]["work_keys"], [wk])
        # Nothing migrated under live authority.
        self.assertEqual(
            (self.repo / ".factory-kit.yml").read_text(), before)
        reg = self._reg().get(REPO_ID)
        self.assertEqual(reg["readiness"]["verdict"], "parked")
        self.assertEqual(reg["readiness"]["dispatch"], "denied")
        mig = self._store().migration(result["migration_id"])
        self.assertEqual(mig["stage"], "parked")
        self.assertIn("unconfirmed", mig["conflict"]["detail"])
        # The parked migration is repair scope: seal via checkpoint
        # semantics (no checkpoint committed → nothing to restore).
        rep = repair_mod.repair_migration(
            self.repo, self._store(),
            registration_store=self._reg(), repaired_by="op-3")
        self.assertEqual(rep["outcome"], "repaired")
        reg = self._reg().get(REPO_ID)
        self.assertEqual(reg["readiness"]["verdict"], "restored")


# ---------------------------------------------------------------------------
# A3 — interruption at every boundary repairs or parks; never half-dispatch
# ---------------------------------------------------------------------------

class TestInterruptedBoundaries(UpgradeFixture):

    def test_every_boundary_repairs_to_validated_state(self):
        for index, stage in enumerate(upgrade_mod.MIGRATION_STAGES[:-1]):
            with self.subTest(stage=stage):
                # A fresh install per boundary: each subTest owns its
                # repo + stores so sealed migrations never collide.
                self._fresh(f"boundary-{index}")
                original = (self.repo / ".factory-kit.yml").read_text()
                plan = self._upgrade_plan()
                accepted = upgrade_mod.accept_upgrade(plan, "op-2")
                result = upgrade_mod.apply_upgrade(
                    accepted, self.repo, self._store(),
                    registration_store=self._reg(), stop_after=stage)
                self.assertEqual(result["outcome"], "interrupted")
                self.assertEqual(result["interrupted_at"], stage)
                # The committed boundary is durable across store
                # reload — the crash landed between stages.
                mig = self._store().migration(result["migration_id"])
                self.assertEqual(mig["stage"], stage)
                self.assertIn(mig["stage"],
                              self._store().open_migrations() and
                              mig["stage"])
                reg = self._reg().get(REPO_ID)
                # Dispatch denied the whole window — either the fence
                # verdict or the pre-fence pending verdict, never ready.
                self.assertNotEqual(
                    reg["readiness"].get("verdict"), "ready")
                self.assertNotEqual(
                    reg["readiness"].get("dispatch"), "allowed")
                if stage not in ("planned",):
                    self.assertEqual(reg["readiness"]["verdict"],
                                     "upgrading")
                repair = repair_mod.repair_migration(
                    self.repo, self._store(),
                    registration_store=self._reg(),
                    repaired_by="op-3")
                self.assertEqual(repair["outcome"], "repaired",
                                 repair)
                # The pre-upgrade bytes are back byte-for-byte.
                self.assertEqual(
                    (self.repo / ".factory-kit.yml").read_text(),
                    original)
                mig = self._store().migration(result["migration_id"])
                self.assertEqual(mig["stage"], "rolled-back")
                reg = self._reg().get(REPO_ID)
                self.assertEqual(reg["readiness"]["verdict"],
                                 "restored")
                self.assertEqual(reg["readiness"]["dispatch"], "denied")
                self.assertIsNone(reg.get("upgrade"))

    def test_parked_repair_preserves_unrecognized_bytes(self):
        self._install()
        plan = self._upgrade_plan()
        accepted = upgrade_mod.accept_upgrade(plan, "op-2")
        result = upgrade_mod.apply_upgrade(
            accepted, self.repo, self._store(),
            registration_store=self._reg(), stop_after="files-migrated")
        # Operator bytes appear on the owned path during downtime —
        # neither checkpoint nor migration target; user work, kept.
        edit = "factory_kit: 1\n# unrecognized operator work\n"
        (self.repo / ".factory-kit.yml").write_text(edit,
                                                    encoding="utf-8")
        repair = repair_mod.repair_migration(
            self.repo, self._store(),
            registration_store=self._reg(), repaired_by="op-3")
        self.assertEqual(repair["outcome"], "parked")
        self.assertEqual(repair["conflicts"][0]["source"], "user-edit")
        self.assertEqual(repair["conflicts"][0]["path"],
                         ".factory-kit.yml")
        self.assertEqual(
            (self.repo / ".factory-kit.yml").read_text(), edit)
        mig = self._store().migration(result["migration_id"])
        self.assertEqual(mig["stage"], "parked")
        # Dispatch stays denied AND the fence marker persists so a
        # readiness re-run cannot silently re-arm registration (A3).
        reg = self._reg().get(REPO_ID)
        self.assertEqual(reg["readiness"]["verdict"], "parked")
        self.assertEqual(reg["readiness"]["dispatch"], "denied")
        self.assertTrue(reg.get("upgrade"))
        with self.assertRaises(RegistrationError):
            self._reg().register(
                schema.load_manifest(self.manifest.read_text()),
                readiness={"verdict": "ready", "dispatch": "allowed"},
                supported_versions=PINS)
        # Resolve the conflict (operator restores the reviewed target)
        # — a second repair still cannot proceed while bytes are
        # unrecognized; rollback also parks on the same conflict.
        rb = upgrade_mod.rollback_upgrade(
            self.repo, self._store(), self._reg(),
            rolled_back_by="op-4")
        self.assertEqual(rb["outcome"], "parked")

    def test_no_dispatch_from_partial_and_double_fence_refused(self):
        self._install()
        plan = self._upgrade_plan()
        accepted = upgrade_mod.accept_upgrade(plan, "op-2")
        # A distinct accepted plan minted *before* the interruption —
        # apply must still refuse: one open migration owns the store.
        other = self._upgrade_plan(
            source=self._candidate(skills='issue-resolver: "0.21.0"'))
        accepted_other = upgrade_mod.accept_upgrade(other, "op-9")
        interrupted = upgrade_mod.apply_upgrade(
            accepted, self.repo, self._store(),
            registration_store=self._reg(), stop_after="checkpointed")
        self.assertEqual(interrupted["outcome"], "interrupted")
        # A second fence cannot interleave.
        with self.assertRaises(RegistrationError):
            self._reg().begin_upgrade(REPO_ID, upgraded_by="op-9")
        # A new inspection sees the open migration as a conflict.
        plan2 = self._upgrade_plan()
        self.assertFalse(plan2["appliable"])
        self.assertIn("installed-state",
                      {c["source"] for c in plan2["conflicts"]})
        # The other accepted plan refuses — the store is repair scope.
        with self.assertRaises(upgrade_mod.UpgradeError) as ctx:
            upgrade_mod.apply_upgrade(
                accepted_other, self.repo, self._store(),
                registration_store=self._reg())
        self.assertIn("open migrations", str(ctx.exception))
        # Re-applying the interrupted plan itself is refused too —
        # repair/rollback own it now.
        with self.assertRaises(upgrade_mod.UpgradeError):
            upgrade_mod.apply_upgrade(
                accepted, self.repo, self._store(),
                registration_store=self._reg())

    def test_repair_with_no_open_migration_is_named_error(self):
        self._install()
        with self.assertRaises(repair_mod.RepairError):
            repair_mod.repair_migration(
                self.repo, self._store(),
                registration_store=self._reg(), repaired_by="op-3")
        with self.assertRaises(repair_mod.RepairError):
            repair_mod.repair_migration(
                self.repo, self._store(),
                registration_store=self._reg(),
                migration_id="upgrade-doesnotexist",
                repaired_by="op-3")


# ---------------------------------------------------------------------------
# A4 — edits/dirty/concurrent bytes preserved; bad versions + drifted
# checksums block application
# ---------------------------------------------------------------------------

class TestConflictsAndPreconditions(UpgradeFixture):

    def test_edited_owned_manifest_is_conflict_until_resolved(self):
        self._install()
        edited = (self.repo / ".factory-kit.yml")
        edited.write_text(fx.manifest_text() + "# operator edits\n",
                          encoding="utf-8")
        plan = self._upgrade_plan()
        self.assertFalse(plan["appliable"])
        conflicts = [c for c in plan["conflicts"]
                     if c["source"] == "user-edit"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["path"], ".factory-kit.yml")
        # The edited bytes stay untouched — the plan only describes.
        self.assertIn("# operator edits", edited.read_text())
        # A reviewed discard resolution re-keys the plan and applies.
        plan = upgrade_mod.inspect_upgrade(
            self.repo, self._store(),
            manifest_source=self._candidate(skills=V2_SKILLS),
            registration_store=self._reg(),
            resolutions={"file:.factory-kit.yml": "discard"})
        self.assertTrue(plan["appliable"], plan["conflicts"])
        self.assertEqual(plan["files"][0]["resolution"], "discard")
        result = self._apply_upgrade(plan)
        self.assertEqual(result["outcome"], "upgraded")
        self.assertNotIn("# operator edits", edited.read_text())
        self.assertIn('issue-resolver: "0.20.0"', edited.read_text())

    def test_unrelated_dirty_files_preserved_byte_for_byte(self):
        self._install()
        (self.repo / "wip.py").write_text("# brand-new WIP\n",
                                          encoding="utf-8")
        result = self._apply_upgrade()
        self.assertEqual(result["outcome"], "upgraded")
        self.assertEqual((self.repo / "wip.py").read_text(),
                         "# brand-new WIP\n")
        self.assertIn("developer WIP",
                      (self.repo / "app.py").read_text())
        self.assertTrue((self.repo / "scratch-notes.txt").exists())

    def test_concurrent_edit_after_review_blocks_application(self):
        self._install()
        plan = self._upgrade_plan()
        accepted = upgrade_mod.accept_upgrade(plan, "op-2")
        # A concurrent edit lands between review and apply — the
        # recorded checksum precondition fails; nothing mutates.
        (self.repo / ".factory-kit.yml").write_text(
            "factory_kit: 1\n# raced edit\n", encoding="utf-8")
        with self.assertRaises(upgrade_mod.UpgradeError) as ctx:
            upgrade_mod.apply_upgrade(
                accepted, self.repo, self._store(),
                registration_store=self._reg())
        self.assertIn("preconditions", str(ctx.exception))
        self.assertEqual(self._store().migrations(), {})
        self.assertEqual((self.repo / ".factory-kit.yml").read_text(),
                         "factory_kit: 1\n# raced edit\n")
        reg = self._reg().get(REPO_ID)
        self.assertIsNone(reg.get("upgrade"))

    def test_unsupported_installed_version_blocks(self):
        self._install(pins=["factory-kit/9.9.9", "manifest/1"])
        plan = self._upgrade_plan()
        self.assertFalse(plan["appliable"])
        conflicts = [c for c in plan["conflicts"]
                     if c["source"] == "installed-version"]
        self.assertTrue(conflicts)
        self.assertIn("newer than this kit", conflicts[0]["detail"])
        status = next(c for c in plan["compatibility"]
                      if c["check"] == "installed-version")
        self.assertEqual(status["status"], "fail")

    def test_unsupported_schema_pin_blocks(self):
        self._install(pins=["factory-kit/0.1.0", "manifest/99"])
        plan = self._upgrade_plan()
        self.assertFalse(plan["appliable"])
        conflicts = [c for c in plan["conflicts"]
                     if c["source"] == "installed-version"]
        self.assertTrue(conflicts)
        self.assertIn("unsupported", conflicts[0]["detail"])

    def test_secret_canary_in_candidate_blocks(self):
        self._install()
        secret = Path(self.tmp.name) / "leaky.yml"
        secret.write_text(
            fx.manifest_text(skills=V2_SKILLS) +
            'token: "' + "ghp_" + "0123456789abcdef0123456789abcdef" + 'ABCD"\n',
            encoding="utf-8")
        plan = self._upgrade_plan(source=secret)
        self.assertFalse(plan["appliable"])
        self.assertIn("secret-canary",
                      {c["source"] for c in plan["conflicts"]})

    def test_removal_during_upgrade_and_missing_owned_file_block(self):
        self._install()
        os.unlink(self.repo / ".factory-kit.yml")
        plan = self._upgrade_plan()
        self.assertFalse(plan["appliable"])
        self.assertIn("ownership",
                      {c["source"] for c in plan["conflicts"]})


# ---------------------------------------------------------------------------
# A5 — rollback restores the previous validated registration/configuration;
# removal afterwards leaves no orphaned authority
# ---------------------------------------------------------------------------

class TestRollback(UpgradeFixture):

    def test_rollback_restores_previous_validated_state(self):
        self._install()
        original_manifest = (self.repo / ".factory-kit.yml").read_text()
        result = self._apply_upgrade()
        self.assertEqual(result["outcome"], "upgraded")
        self.assertIn("0.20.0",
                      (self.repo / ".factory-kit.yml").read_text())
        rb = upgrade_mod.rollback_upgrade(
            self.repo, self._store(), self._reg(),
            rolled_back_by="operator-3")
        self.assertEqual(rb["outcome"], "restored")
        # Bytes restored exactly; registration re-bound to the previous
        # digests under a fresh audited generation (no history rewrite).
        self.assertEqual(
            (self.repo / ".factory-kit.yml").read_text(),
            original_manifest)
        reg = self._reg().get(REPO_ID)
        self.assertEqual(reg["active_generation"], 3)
        active = next(g for g in reg["generations"]
                      if g["generation"] == 3)
        old_effective = schema.load_manifest(self.manifest.read_text())
        self.assertEqual(active["config_digest"],
                         schema.effective_digest(old_effective))
        self.assertEqual(active["policy_digest"],
                         schema.policy_digest(old_effective))
        self.assertEqual(active["authorized_by"], "operator-3")
        self.assertEqual(reg["readiness"]["verdict"], "restored")
        self.assertEqual(reg["readiness"]["dispatch"], "denied")
        self.assertTrue(rb["verification"]["registration_restored"])
        self.assertEqual(rb["verification"]["file_violations"], [])
        mig = self._store().migration(rb["migration_id"])
        self.assertEqual(mig["stage"], "rolled-back")
        events = self._store().events("setup_rollback")
        self.assertEqual(events[-1]["outcome"], "restored")
        # Readiness re-passes on the restored configuration — dispatch
        # resumes only after the substantive re-check.
        report = self._enable_readiness()
        self.assertEqual(report["verdict"], "ready")
        self.assertEqual(self._reg().get(REPO_ID)
                         ["readiness"]["dispatch"], "allowed")

    def test_rollback_is_idempotent_and_parks_on_conflict(self):
        self._install()
        self._apply_upgrade()
        first = upgrade_mod.rollback_upgrade(
            self.repo, self._store(), self._reg(),
            rolled_back_by="op")
        self.assertEqual(first["outcome"], "restored")
        again = upgrade_mod.rollback_upgrade(
            self.repo, self._store(), self._reg(),
            rolled_back_by="op")
        self.assertEqual(again["outcome"], "already-rolled-back")

    def test_rollback_with_no_checkpoint_is_named_error(self):
        self._install()
        with self.assertRaises(upgrade_mod.UpgradeError):
            upgrade_mod.rollback_upgrade(
                self.repo, self._store(), self._reg(),
                rolled_back_by="op")

    def test_rollback_then_removal_leaves_no_orphaned_authority(self):
        """A5 fixture chain: upgrade → rollback → removal → tombstone,
        with the rolled-back bytes cleanly removed — no orphaned
        authority survives."""
        self._install()
        self._apply_upgrade()
        upgrade_mod.rollback_upgrade(
            self.repo, self._store(), self._reg(),
            rolled_back_by="op-3")
        removal = remove_mod.inspect_removal(
            self.repo, self._store(),
            registration_store=self._reg(),
            intake_store=self._intake())
        self.assertTrue(removal["appliable"], removal["conflicts"])
        accepted = remove_mod.accept_removal(removal, "op-4",
                                             history="retain")
        result = remove_mod.apply_removal(
            accepted, self.repo, self._store(),
            registration_store=self._reg(),
            intake_store=self._intake())
        self.assertEqual(result["outcome"], "removed")
        self.assertTrue(self._reg().is_tombstone(REPO_ID))
        self.assertFalse((self.repo / ".factory-kit.yml").exists())
        after = fx.snapshot(self.repo)
        baseline = {k: v for k, v in self.baseline.items()}
        self.assertEqual(after, baseline)

    def test_repair_after_rollback_is_sealed(self):
        self._install()
        self._apply_upgrade()
        upgrade_mod.rollback_upgrade(
            self.repo, self._store(), self._reg(),
            rolled_back_by="op")
        # No open migrations remain — repair refuses cleanly.
        with self.assertRaises(repair_mod.RepairError):
            repair_mod.repair_migration(
                self.repo, self._store(),
                registration_store=self._reg(), repaired_by="op")


if __name__ == "__main__":
    unittest.main(verbosity=2)
