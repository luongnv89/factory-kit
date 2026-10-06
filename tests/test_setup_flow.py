#!/usr/bin/env python3
"""End-to-end setup flow tests — plan → accept → apply → readiness →
register (A6, with A1/A2 wired in).

Maps to issue #7 acceptance criterion A6:

- ``setup_checked`` events persist local project ID, version set,
  duration and per-prerequisite outcomes;
- registration is enabled only with the accepted plan AND passing
  substantive readiness;
- failed setup remains diagnosable without presenting queued work as
  authorized — a failing verdict registers nothing, and every report
  keeps ``dispatched: false`` / ``dispatch: denied``.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_setup_flow.py
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "setup_repo.py"
READINESS_FIXTURES = ROOT / "tests" / "fixtures" / "readiness"

sys.path.insert(0, str(ROOT))
from factory_kit.config import schema  # noqa: E402
from factory_kit.config.registration import RegistrationStore  # noqa: E402
from factory_kit import VERSION  # noqa: E402
from factory_kit.setup import apply as apply_mod  # noqa: E402
from factory_kit.setup import plan as plan_mod  # noqa: E402
from factory_kit.setup import readiness  # noqa: E402
from factory_kit.setup.ownership import SetupStore  # noqa: E402

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)


def readings(name="ready"):
    return json.loads(
        (READINESS_FIXTURES / f"{name}.json").read_text())


class FullFlowTests(unittest.TestCase):
    """The A6 contract across the whole pipeline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = fx.build_repo(Path(self.tmp.name) / "repo")
        self.manifest = Path(self.tmp.name) / "candidate.yml"
        self.manifest.write_text(fx.manifest_text(), encoding="utf-8")
        self.store = SetupStore(str(Path(self.tmp.name) / "state.json"))
        self.reg = RegistrationStore(
            str(Path(self.tmp.name) / "registrations.json"))

    def _apply(self):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        accepted = plan_mod.accept(plan, "operator-1")
        return apply_mod.apply_plan(
            accepted, self.repo, self.store,
            registration_store=self.reg,
            supported_versions=["factory-kit/0.1.0",
                             "manifest/1"])

    def test_happy_path_enables_registration(self):
        result = self._apply()
        self.assertEqual(result["outcome"], "applied")
        effective = schema.load_manifest(
            self.manifest.read_text())
        report = readiness.run_setup_checked(
            project_id="R_TEST0001", effective=effective,
            store=self.store, registration_store=self.reg,
            supported_versions=["factory-kit/0.1.0",
                             "manifest/1"],
            readings=readings())
        self.assertEqual(report["verdict"], "ready")
        self.assertTrue(report["setup_enabled"])
        record = self.reg.get("R_TEST0001")
        self.assertEqual(record["readiness"]["verdict"], "ready")
        self.assertTrue(record["readiness"]["setup_enabled"])
        self.assertEqual(record["supported_versions"][0],
                         "factory-kit/0.1.0")
        self.assertEqual(VERSION, "0.2.0b1")
        self.assertEqual(report["version_set"]["kit"], VERSION)

        events = self.store.events("setup_checked")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["project_id"], "R_TEST0001")
        self.assertIn("hermes", event["version_set"])
        self.assertIsInstance(event["duration_ms"], int)
        names = {o["name"] for o in event["prerequisite_outcomes"]}
        self.assertIn("cancellation", names)
        self.assertIn("model", names)

    def test_failed_readiness_registers_nothing_enabled(self):
        self._apply()
        effective = schema.load_manifest(
            self.manifest.read_text())
        report = readiness.run_setup_checked(
            project_id="R_TEST0001", effective=effective,
            store=self.store, registration_store=self.reg,
            supported_versions=["factory-kit/0.1.0",
                             "manifest/1"],
            readings=readings("conflicting-owner"))
        self.assertEqual(report["verdict"], "not-ready")
        self.assertFalse(report["setup_enabled"])
        self.assertEqual(report["dispatch"], "denied")
        record = self.reg.get("R_TEST0001")
        self.assertEqual(record["readiness"]["verdict"], "not-ready")
        self.assertFalse(record["readiness"]["setup_enabled"],
                         "the stored row records the failing verdict — "
                         "dispatch stays disabled and diagnosable")
        events = self.store.events("setup_checked")
        self.assertEqual(events[0]["verdict"], "not-ready")
        self.assertIn("conflicting-task-owner",
                      events[0]["blocker_codes"])

    def test_readiness_without_accepted_plan_registers_nothing(self):
        """A6 — readiness alone must not create or enable a
        registration: only an accepted plan may."""
        effective = schema.load_manifest(
            self.manifest.read_text())
        report = readiness.run_setup_checked(
            project_id="R_TEST0001", effective=effective,
            store=self.store, registration_store=self.reg,
            readings=readings())
        self.assertEqual(report["verdict"], "ready")
        self.assertFalse(report["setup_enabled"])
        self.assertEqual(report["registration"]["status"], "absent")
        self.assertIsNone(self.reg.get("R_TEST0001"),
                          "no row appears without an accepted plan")
        # The event still persisted — the failed/incomplete setup is
        # diagnosable, and nothing presents queued work as authorized.
        self.assertEqual(len(self.store.events("setup_checked")), 1)

    def test_repeated_setup_checked_events_accumulate(self):
        self._apply()
        effective = schema.load_manifest(
            self.manifest.read_text())
        readiness.run_setup_checked(
            project_id="R_TEST0001", effective=effective,
            store=self.store, registration_store=self.reg,
            readings=readings("conflicting-owner"))
        readiness.run_setup_checked(
            project_id="R_TEST0001", effective=effective,
            store=self.store, registration_store=self.reg,
            readings=readings())
        events = self.store.events("setup_checked")
        self.assertEqual([e["verdict"] for e in events],
                         ["not-ready", "ready"],
                         "the durable trail keeps the failure and the "
                         "later pass — diagnosable, not rewritten")

    def test_cli_plan_is_read_only_and_json(self):
        import subprocess
        out = Path(self.tmp.name) / "plan.json"
        proc = subprocess.run(
            [sys.executable, "-m", "factory_kit.setup", "plan",
             "--repo", str(self.repo), "--manifest", str(self.manifest),
             "--out", str(out)],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads(out.read_text())
        self.assertTrue(plan["appliable"])
        self.assertIn("entries", plan)

    def test_cli_apply_requires_accepted_by(self):
        import subprocess
        plan_path = Path(self.tmp.name) / "plan.json"
        subprocess.run(
            [sys.executable, "-m", "factory_kit.setup", "plan",
             "--repo", str(self.repo), "--manifest", str(self.manifest),
             "--out", str(plan_path)],
            capture_output=True, text=True, cwd=str(ROOT), check=True)
        proc = subprocess.run(
            [sys.executable, "-m", "factory_kit.setup", "apply",
             "--repo", str(self.repo), "--plan", str(plan_path)],
            capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 2,
                         "missing --accepted-by is a usage error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
