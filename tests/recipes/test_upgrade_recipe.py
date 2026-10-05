#!/usr/bin/env python3
"""Tested upgrade/repair/rollback recipe — issue #26 / Task 4.2 (A6).

Every command the recipe `docs/recipes/upgrade-repair.md` publishes is
exercised here as a real ``python3 -m factory_kit.setup`` subprocess
against the disposable repository fixture — docs claim only what this
walkthrough executes. Fixture state (the install itself, and the
interrupted-migration states a crash would leave) is built through the
library API; the *recipe commands* — ``upgrade``, ``migrate``,
``readiness``, ``repair``, ``rollback``, ``uninstall``, ``remove`` — all
run through the CLI exactly as documented.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/recipes/test_upgrade_recipe.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "setup_repo.py"
READINESS_FIXTURES = ROOT / "tests" / "fixtures" / "readiness"

sys.path.insert(0, str(ROOT))
from factory_kit import VERSION  # noqa: E402
from factory_kit.setup import upgrade as upgrade_mod  # noqa: E402
from factory_kit.setup.ownership import SetupStore  # noqa: E402

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)

OPERATOR = "recipe-test-operator"
PINS = [f"factory-kit/{VERSION}", "manifest/1"]


class UpgradeRecipeFixture(unittest.TestCase):
    """A fresh fixture repository plus scratch operator-profile state —
    an installed, registered, readiness-verified setup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.repo = fx.build_repo(self.work / "repo")
        self.manifest = self.work / "candidate.factory-kit.yml"
        self.manifest.write_text(fx.manifest_text(), encoding="utf-8")
        # The recipe's "v2" candidate: a config-only drift (preview TTL)
        # — pinned skills stay on the tested set so the post-upgrade
        # readiness re-pass is a legitimate `ready`.
        self.candidate = self.work / "candidate-v2.factory-kit.yml"
        self.candidate.write_text(
            fx.manifest_text().replace("ttl_hours: 24",
                                       "ttl_hours: 48"),
            encoding="utf-8")
        self.state = self.work / "setup-state.json"
        self.registrations = self.work / "registrations.json"

    def cli(self, *args):
        """Run the published command exactly as the recipe writes it."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + \
            env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "factory_kit.setup", *args],
            capture_output=True, text=True, cwd=str(ROOT), env=env)

    def _install(self):
        """Recipe steps 1–3: plan → apply → readiness (ready fixture)."""
        proc = self.cli("plan", "--repo", str(self.repo),
                        "--manifest", str(self.manifest),
                        "--out", str(self.work / "plan.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.cli("apply", "--repo", str(self.repo),
                        "--plan", str(self.work / "plan.json"),
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.cli("readiness", "--repo", str(self.repo),
                        "--readings",
                        str(READINESS_FIXTURES / "ready.json"),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "ready")

    def _upgrade_plan(self, *extra):
        return self.cli("upgrade", "--repo", str(self.repo),
                        "--manifest", str(self.candidate),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations),
                        "--out", str(self.work / "upgrade.json"),
                        *extra)

    def _migrate(self):
        return self.cli("migrate", "--repo", str(self.repo),
                        "--plan", str(self.work / "upgrade.json"),
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))

    def _interrupt(self, stage):
        """Build the state a crash would leave at ``stage`` — the
        recipe's repair command then owns the recovery."""
        plan = json.loads((self.work / "upgrade.json").read_text())
        accepted = upgrade_mod.accept_upgrade(plan, OPERATOR)
        from factory_kit.config.registration import RegistrationStore
        return upgrade_mod.apply_upgrade(
            accepted, str(self.repo), SetupStore(str(self.state)),
            registration_store=RegistrationStore(
                str(self.registrations)),
            stop_after=stage)


class TestUpgradeRepairRecipe(UpgradeRecipeFixture):
    """A6 — the documented install → upgrade → repair → rollback →
    removal walkthrough runs on the published commands."""

    def test_upgrade_migrate_and_readiness_resume(self):
        self._install()
        original = (self.repo / ".factory-kit.yml").read_text()
        # Step 1 — read-only reviewable upgrade plan.
        proc = self._upgrade_plan()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads((self.work / "upgrade.json").read_text())
        self.assertTrue(plan["appliable"])
        checks = {c["check"]: c["status"]
                  for c in plan["compatibility"]}
        self.assertTrue(all(v == "pass" for v in checks.values()))
        self.assertEqual(plan["target"]["migration_hops"], [])
        self.assertEqual(
            [s["stage"] for s in plan["migration_steps"]],
            list(upgrade_mod.MIGRATION_STAGES))
        self.assertTrue(plan["rollback"]["instructions"])
        # Read-only: the installed bytes are still the v1 manifest.
        self.assertEqual(
            (self.repo / ".factory-kit.yml").read_text(), original)

        # Step 2 — fenced staged migration on the accepted plan.
        proc = self._migrate()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["outcome"], "upgraded")
        self.assertIn("ttl_hours: 48",
                      (self.repo / ".factory-kit.yml").read_text())
        reg = json.loads(self.registrations.read_text())
        row = reg["registrations"]["R_TEST0001"]
        self.assertEqual(row["active_generation"], 2)
        self.assertEqual(row["readiness"]["verdict"], "pending")
        self.assertEqual(row["readiness"]["dispatch"], "denied")

        # Step 3 — substantive readiness re-passes on the new
        # configuration; dispatch resumes only here.
        proc = self.cli("readiness", "--repo", str(self.repo),
                        "--readings",
                        str(READINESS_FIXTURES / "ready.json"),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["verdict"], "ready")
        self.assertEqual(report["dispatch"], "allowed")

    def test_repair_restores_the_checkpoint(self):
        self._install()
        self.assertEqual(self._upgrade_plan().returncode, 0)
        interrupted = self._interrupt("files-migrated")
        self.assertEqual(interrupted["outcome"], "interrupted")
        # Step 4 — repair: checkpoint restore, sealed, denied until
        # the readiness re-check.
        proc = self.cli("repair", "--repo", str(self.repo),
                        "--repaired-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["outcome"], "repaired")
        self.assertIn('issue-resolver: "0.19.0"',
                      (self.repo / ".factory-kit.yml").read_text())
        reg = json.loads(self.registrations.read_text())
        row = reg["registrations"]["R_TEST0001"]
        self.assertEqual(row["readiness"]["verdict"], "restored")
        self.assertEqual(row["readiness"]["dispatch"], "denied")
        # Readiness re-passes on the restored configuration.
        proc = self.cli("readiness", "--repo", str(self.repo),
                        "--readings",
                        str(READINESS_FIXTURES / "ready.json"),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(json.loads(proc.stdout)["verdict"], "ready")

    def test_rollback_then_removal_leaves_no_orphaned_authority(self):
        self._install()
        self.assertEqual(self._upgrade_plan().returncode, 0)
        self.assertEqual(self._migrate().returncode, 0)
        # Step 5 — documented rollback: bytes + digests restored under
        # a fresh audited generation.
        proc = self.cli("rollback", "--repo", str(self.repo),
                        "--rolled-back-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["outcome"], "restored")
        self.assertIn('issue-resolver: "0.19.0"',
                      (self.repo / ".factory-kit.yml").read_text())
        self.assertEqual(result["verification"]["file_violations"], [])
        # Step 6 — removal of the rolled-back install: tombstone, no
        # orphaned authority.
        proc = self.cli("uninstall", "--repo", str(self.repo),
                        "--out", str(self.work / "removal.json"),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.cli("remove", "--repo", str(self.repo),
                        "--plan", str(self.work / "removal.json"),
                        "--history", "retain",
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["outcome"], "removed")
        self.assertFalse((self.repo / ".factory-kit.yml").exists())
        reg = json.loads(self.registrations.read_text())
        self.assertTrue(reg["registrations"]
                        ["R_TEST0001"]["tombstone"])

    def test_upgrade_names_conflicts_and_never_writes(self):
        self._install()
        # User edit on the owned manifest → named conflict, exit 1,
        # bytes preserved.
        edited = fx.manifest_text() + "# operator edits\n"
        (self.repo / ".factory-kit.yml").write_text(edited,
                                                    encoding="utf-8")
        proc = self._upgrade_plan()
        self.assertEqual(proc.returncode, 1, proc.stderr)
        plan = json.loads((self.work / "upgrade.json").read_text())
        self.assertFalse(plan["appliable"])
        self.assertIn("user-edit",
                      {c["source"] for c in plan["conflicts"]})
        self.assertEqual(
            (self.repo / ".factory-kit.yml").read_text(), edited)
        # The reviewed discard resolution re-keys the plan → appliable.
        proc = self._upgrade_plan(
            "--resolution", "file:.factory-kit.yml=discard")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads((self.work / "upgrade.json").read_text())
        self.assertTrue(plan["appliable"], plan["conflicts"])

    def test_migrate_refuses_a_tampered_plan(self):
        self._install()
        self.assertEqual(self._upgrade_plan().returncode, 0)
        plan_path = self.work / "upgrade.json"
        plan = json.loads(plan_path.read_text())
        plan["target"]["schema_version"] = 99
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True))
        proc = self._migrate()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("digest", proc.stderr)
        self.assertIn('issue-resolver: "0.19.0"',
                      (self.repo / ".factory-kit.yml").read_text())

    def test_repair_with_nothing_open_is_a_named_error(self):
        self._install()
        proc = self.cli("repair", "--repo", str(self.repo),
                        "--repaired-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no open migrations", proc.stderr)


if __name__ == "__main__":
    unittest.main()
