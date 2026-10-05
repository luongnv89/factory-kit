#!/usr/bin/env python3
"""Unit tests for factory_kit.setup.readiness — substantive probes (A3–A5).

Maps to issue #7 acceptance criteria:

- A3: executable capability, configured model/authentication, supported
  host/Hermes/runtime/skill versions and cancellation probes; every
  missing prerequisite is a *named* blocker and dispatch stays denied.
- A4: a competing automation-owner reading blocks activation.
- A5: the report records permissions/scopes, version set, pinned skill
  revisions, concrete verification commands and cancellation capability —
  and passes the secret-canary scan.

Fixture ``readings`` documents reuse the spike probe's shape
(``tests/fixtures/readiness/*.json``); the manifest comes from
``tests/fixtures/setup_repo.py``.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_setup_readiness.py
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "setup_repo.py"
READINESS_FIXTURES = ROOT / "tests" / "fixtures" / "readiness"

sys.path.insert(0, str(ROOT))
from factory_kit.config import schema  # noqa: E402
from factory_kit.setup import readiness  # noqa: E402

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)

EFFECTIVE = schema.load_manifest(fx.manifest_text())


def readings(name="ready"):
    return json.loads(
        (READINESS_FIXTURES / f"{name}.json").read_text())


def codes(report):
    return report["blocker_codes"]


class EvaluateFixtureTests(unittest.TestCase):
    """A3 — each missing prerequisite is a named blocker; dispatch
    remains disabled on every failing path."""

    def test_ready_allows_dispatch(self):
        report = readiness.evaluate(readings(), EFFECTIVE,
                                    project_id="R_TEST0001")
        self.assertEqual(report["verdict"], "ready")
        self.assertEqual(report["dispatch"], "allowed")
        self.assertEqual(report["blockers"], [])
        self.assertFalse(report["dispatched"])

    def test_missing_executable_blocks(self):
        r = readings()
        r["hermes"] = {"version": None,
                       "error": "hermes not found"}
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("missing-executable", codes(report))
        self.assertEqual(report["dispatch"], "denied")
        self.assertFalse(report["dispatched"])

    def test_unsupported_hermes_version_blocks(self):
        report = readiness.evaluate(readings("unsupported-version"),
                                    EFFECTIVE)
        self.assertIn("unsupported-version", codes(report))
        self.assertEqual(report["dispatch"], "denied")

    def test_unsupported_runtime_blocks(self):
        effective = dict(EFFECTIVE)
        effective["runtime"] = dict(EFFECTIVE["runtime"],
                                    name="external-cli-lane")
        report = readiness.evaluate(readings(), effective)
        self.assertIn("unsupported-runtime", codes(report))

    def test_unsupported_host_blocks(self):
        r = readings()
        r["host"]["os"] = "Windows"
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("unsupported-host", codes(report))

    def test_undetermined_host_blocks(self):
        """An unmeasurable host is not a supported host — readiness
        certifies only what it verified."""
        r = readings()
        del r["host"]
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("unsupported-host", codes(report))
        self.assertEqual(report["dispatch"], "denied")

    def test_unverifiable_protection_blocks(self):
        """A skipped repo-rules reading means the merge precondition was
        never verified — named blocker, not a silent pass."""
        r = readings()
        r["repo_rules"] = {"skipped": "repository unknown"}
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("base-unprotected", codes(report))
        self.assertEqual(report["dispatch"], "denied")

    def test_unavailable_model_blocks(self):
        report = readiness.evaluate(readings("model-unavailable"),
                                    EFFECTIVE)
        self.assertIn("model-unavailable", codes(report))
        self.assertEqual(report["dispatch"], "denied")

    def test_failed_auth_blocks(self):
        r = readings()
        r["model"]["provider"] = "otherprovider"
        r["model"]["auth_status"] = "not logged in"
        r["model"]["auth_providers"] = []
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("model-auth-failed", codes(report))
        self.assertEqual(report["dispatch"], "denied")

    def test_model_not_listed_blocks(self):
        r = readings()
        r["model"]["endpoint"]["models"] = ["some-other-model"]
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("model-unavailable", codes(report))

    def test_missing_kanban_interface_blocks(self):
        report = readiness.evaluate(readings("missing-interface"),
                                    EFFECTIVE)
        self.assertIn("unsupported-interface", codes(report))

    def test_inadequate_cancellation_named(self):
        r = readings()
        r["hermes"]["kanban_verbs"] = [
            v for v in r["hermes"]["kanban_verbs"]
            if v not in ("block", "reclaim")]
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("cancellation-inadequate", codes(report))
        self.assertFalse(report["cancellation"]["supported"])
        self.assertEqual(report["dispatch"], "denied")

    def test_skill_pin_mismatch_blocks(self):
        r = readings()
        r["idd"]["skills"]["issue-resolver"] = "0.9.9"
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("skill-version-mismatch", codes(report))

    def test_missing_skill_blocks(self):
        r = readings()
        r["idd"]["skills"] = {}
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("skill-version-mismatch", codes(report))

    def test_conflicting_owner_blocks_activation(self):
        report = readiness.evaluate(readings("conflicting-owner"),
                                    EFFECTIVE)
        self.assertIn("conflicting-task-owner", codes(report))
        self.assertEqual(report["dispatch"], "denied")

    def test_base_unprotected_blocks(self):
        report = readiness.evaluate(readings("base-unprotected"),
                                    EFFECTIVE)
        self.assertIn("base-unprotected", codes(report))

    def test_verification_contract_uncovered_blocks(self):
        r = readings()
        r["repo_rules"]["required_checks"] = ["Other Check"]
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("verification-contract-unmet", codes(report))

    def test_missing_transport_blocks(self):
        r = readings()
        r["transport"]["telegram_adapter"] = False
        report = readiness.evaluate(r, EFFECTIVE)
        self.assertIn("missing-transport", codes(report))


class ReportContentTests(unittest.TestCase):
    """A5 — the report records versions, scopes, skill pins, commands and
    cancellation capability; canary scan is clean."""

    def test_report_records_required_fields(self):
        report = readiness.evaluate(readings(), EFFECTIVE,
                                    project_id="R_TEST0001",
                                    duration_ms=42)
        self.assertEqual(report["duration_ms"], 42)
        self.assertIn("repo", report["permissions"]["github"]["token_scopes"],
                      "credential *scopes* recorded, never values")
        self.assertEqual(
            report["version_set"]["skills"]["issue-resolver"]["pin"],
            "0.19.0")
        self.assertEqual(
            report["version_set"]["skills"]["issue-resolver"]["installed"],
            "0.19.0")
        self.assertEqual(report["recipe"]["runtime"], "hermes-kanban")
        self.assertEqual(report["recipe"]["preview"], "vercel")
        self.assertEqual(report["recipe"]["merge"], "squash")
        self.assertIn("make test", report["verification_commands"])
        self.assertTrue(report["cancellation"]["supported"])
        self.assertEqual(report["cancellation"]["required_verbs"],
                         ["block", "reclaim"])
        names = {o["name"] for o in report["prerequisite_outcomes"]}
        self.assertIn("cancellation", names)
        self.assertIn("skill-versions", names)

    def test_report_canary_scan_clean(self):
        report = readiness.evaluate(readings(), EFFECTIVE)
        self.assertEqual(report["canary_scan"], "clean")
        blob = json.dumps(report)
        self.assertNotIn("ghp_", blob)
        self.assertNotIn("TELEGRAM_BOT_TOKEN=", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
