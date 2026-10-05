#!/usr/bin/env python3
"""Tested installation/support recipe — issue #23 / Task 3.9 (A1, A6).

Every command the recipes publish is exercised here as a real
``python3 -m factory_kit.setup`` subprocess against the disposable
repository fixture (``tests/fixtures/setup_repo.py``) — docs claim only
what this walkthrough executes:

- A1 (happy path): ``plan`` → ``apply`` → ``readiness`` → ``status`` →
  ``uninstall`` → ``remove`` on a fresh supported setup, including
  idempotent re-apply and the preservation guarantees the recipe cites.
- A6 (negative walkthrough): unsupported Hermes version, missing
  executable, unavailable/unauthenticated model, competing automation
  owner, unprotected base and a schema-incompatible manifest each report
  their concrete blocker with ``dispatch: denied`` — nothing is
  advertised beyond these tested commands.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/recipes/test_install_recipe.py
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

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)

OPERATOR = "recipe-test-operator"


def _readings(name):
    return json.loads((READINESS_FIXTURES / f"{name}.json").read_text())


class RecipeFixture(unittest.TestCase):
    """A fresh fixture repository plus scratch operator-profile state —
    the supported host/runtime setup the recipe documents."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.repo = fx.build_repo(self.work / "repo")
        self.manifest = self.work / "candidate.factory-kit.yml"
        self.manifest.write_text(fx.manifest_text(), encoding="utf-8")
        self.state = self.work / "setup-state.json"
        self.registrations = self.work / "registrations.json"

    def cli(self, *args):
        """Run the published command exactly as the recipe writes it —
        the operator's real entry point, not a library shortcut."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + \
            env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "factory_kit.setup", *args],
            capture_output=True, text=True, cwd=str(ROOT), env=env)
        return proc

    def plan(self, manifest=None):
        return self.cli("plan", "--repo", str(self.repo),
                        "--manifest", str(manifest or self.manifest),
                        "--out", str(self.work / "plan.json"))

    def apply(self):
        return self.cli("apply", "--repo", str(self.repo),
                        "--plan", str(self.work / "plan.json"),
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))

    def readiness(self, readings_path):
        return self.cli("readiness", "--repo", str(self.repo),
                        "--readings", str(readings_path),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))


class TestA1FreshSetupRecipe(RecipeFixture):
    """A1 — the reviewed install → readiness → removal sequence runs on
    the documented commands; re-apply stays idempotent and unrelated
    bytes are preserved."""

    def test_full_setup_recipe(self):
        # 1 — inspect: a reviewable, additive plan; exit 0 = appliable.
        proc = self.plan()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads((self.work / "plan.json").read_text())
        self.assertTrue(plan["appliable"])
        self.assertEqual(plan["conflicts"], [])
        self.assertEqual(
            [e["id"] for e in plan["entries"]],
            ["configuration:.factory-kit.yml", "webhook:intake-events",
             "registration:R_TEST0001",
             "dependency:approved-skill-pins"])
        self.assertFalse((self.repo / ".factory-kit.yml").exists())

        # 2 — apply the reviewed plan: only planned paths are written,
        # every preserved checksum re-verified.
        proc = self.apply()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["outcome"], "applied")
        self.assertEqual(result["effective_diff"], [])
        self.assertEqual(result["preserved"]["violations"], [])
        self.assertTrue((self.repo / ".factory-kit.yml").exists())
        # The developer's dirty work survives byte-for-byte (fixture).
        self.assertIn("developer WIP",
                      (self.repo / "app.py").read_text())
        self.assertTrue((self.repo / "scratch-notes.txt").exists())

        # 3 — substantive readiness against the recorded probe fixture:
        # the version set and per-prerequisite outcomes are reported,
        # registration flips pending → enabled.
        proc = self.readiness(READINESS_FIXTURES / "ready.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["verdict"], "ready")
        self.assertEqual(report["dispatch"], "allowed")
        self.assertTrue(report["setup_enabled"])
        self.assertFalse(report["dispatched"])
        self.assertEqual(report["version_set"]["kit"], VERSION)
        self.assertEqual(report["version_set"]["runtime"],
                         "hermes-kanban")
        self.assertEqual(report["registration"]["status"], "enabled")

        # 4 — status: the operator ledger reads back ownership, applied
        # plans and recorded effects.
        proc = self.cli("status", "--repo", str(self.repo),
                        "--state", str(self.state))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ledger = json.loads(proc.stdout)
        self.assertEqual(ledger["project_id"], "R_TEST0001")
        self.assertIn(".factory-kit.yml", ledger["ownership"])
        self.assertTrue(ledger["remote_effects"])
        self.assertTrue(ledger["events"])

        # 5 — uninstall (read-only) emits the reviewable removal plan:
        # ledger-owned files plus the recorded remote-effect intents.
        proc = self.cli("uninstall", "--repo", str(self.repo),
                        "--out", str(self.work / "removal.json"),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        removal = json.loads((self.work / "removal.json").read_text())
        self.assertTrue(removal["appliable"])
        self.assertEqual([f["id"] for f in removal["files"]],
                         ["file:.factory-kit.yml"])
        self.assertTrue(removal["remote_effects"])
        self.assertTrue((self.repo / ".factory-kit.yml").exists())

        # 6 — remove applies the accepted plan: intake stops first,
        # owned files are removed, the registration tombstones.
        proc = self.cli("remove", "--repo", str(self.repo),
                        "--plan", str(self.work / "removal.json"),
                        "--history", "retain",
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["outcome"], "removed")
        self.assertFalse((self.repo / ".factory-kit.yml").exists())
        # Developer work and repo CI are never touched.
        self.assertTrue((self.repo / ".github/workflows/ci.yml")
                        .exists())
        self.assertIn("developer WIP",
                      (self.repo / "app.py").read_text())

    def test_reapply_is_idempotent(self):
        self.plan()
        self.assertEqual(self.apply().returncode, 0)
        proc = self.apply()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        # The second apply of the same accepted plan writes nothing new —
        # every entry reports unchanged.
        self.assertEqual(result["effective_diff"], [])
        self.assertEqual(
            result["unchanged"],
            ["configuration:.factory-kit.yml", "webhook:intake-events",
             "registration:R_TEST0001",
             "dependency:approved-skill-pins"])

    def test_apply_refuses_tampered_plan(self):
        # The plan digest binds the reviewed bytes — an edited plan file
        # fails the digest check and apply refuses (exit 1).
        self.plan()
        plan_path = self.work / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan["entries"][0]["bytes"] += 1
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True))
        proc = self.apply()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("digest", proc.stderr)
        self.assertFalse((self.repo / ".factory-kit.yml").exists())

    def test_remove_without_history_choice_refused(self):
        # A4 (F08): there is no default history choice — a plan that
        # does not name retain|export|delete cannot apply.
        self.plan()
        self.apply()
        proc = self.cli("uninstall", "--repo", str(self.repo),
                        "--out", str(self.work / "removal.json"),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0)
        proc = self.cli("remove", "--repo", str(self.repo),
                        "--plan", str(self.work / "removal.json"),
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 2)
        self.assertTrue((self.repo / ".factory-kit.yml").exists())


class TestA6NegativeWalkthrough(RecipeFixture):
    """A6 — every unsupported condition reports its concrete blocker
    and never dispatches; the walkthrough names each blocker's code."""

    def _denied(self, fixture_name, expected_codes):
        # Negative legs run on an applied (but not yet ready) setup —
        # the same shape the recipe documents for first-run triage.
        self.plan()
        self.apply()
        proc = self.readiness(READINESS_FIXTURES /
                              f"{fixture_name}.json")
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertEqual(report["verdict"], "not-ready")
        self.assertEqual(report["dispatch"], "denied")
        self.assertFalse(report["dispatched"])
        for code in expected_codes:
            self.assertIn(code, report["blocker_codes"])
        return report

    def test_unsupported_version(self):
        report = self._denied("unsupported-version",
                              ["unsupported-version"])
        self.assertFalse(report["setup_enabled"])

    def test_missing_executable(self):
        readings = _readings("ready")
        readings["hermes"] = {"version": None,
                              "error": "hermes not found"}
        path = self.work / "no-hermes.json"
        path.write_text(json.dumps(readings))
        self.plan()
        self.apply()
        proc = self.readiness(path)
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertIn("missing-executable", report["blocker_codes"])
        self.assertEqual(report["dispatch"], "denied")

    def test_model_unavailable(self):
        self._denied("model-unavailable", ["model-unavailable"])

    def test_model_authentication_missing(self):
        # Mirrors the supported probe shape for a signed-out provider:
        # the role's provider is absent from authenticated providers.
        readings = _readings("ready")
        readings["model"]["provider"] = "otherprovider"
        readings["model"]["auth_status"] = "not logged in"
        path = self.work / "no-auth.json"
        path.write_text(json.dumps(readings))
        self.plan()
        self.apply()
        proc = self.readiness(path)
        self.assertEqual(proc.returncode, 1)
        report = json.loads(proc.stdout)
        self.assertIn("model-auth-failed", report["blocker_codes"])
        self.assertEqual(report["dispatch"], "denied")

    def test_competing_owner(self):
        self._denied("conflicting-owner", ["conflicting-task-owner"])

    def test_base_unprotected(self):
        self._denied("base-unprotected",
                     ["base-unprotected",
                      "verification-contract-unmet"])

    def test_missing_intake_interface(self):
        self._denied("missing-interface",
                     ["unsupported-interface",
                      "cancellation-inadequate"])

    def test_incompatible_manifest_not_appliable(self):
        # An out-of-schema manifest produces a reviewable but
        # non-appliable plan — the conflict is named, nothing is
        # written, and apply has nothing to accept.
        bad = self.work / "bad.factory-kit.yml"
        bad.write_text("factory_kit: 99\n", encoding="utf-8")
        proc = self.plan(manifest=bad)
        self.assertEqual(proc.returncode, 1)
        plan = json.loads((self.work / "plan.json").read_text())
        self.assertFalse(plan["appliable"])
        self.assertTrue(plan["conflicts"])
        self.assertFalse((self.repo / ".factory-kit.yml").exists())


if __name__ == "__main__":
    unittest.main()
