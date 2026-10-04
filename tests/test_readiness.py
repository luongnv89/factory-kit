#!/usr/bin/env python3
"""Unit tests for tools/probes/readiness.py (issue #2 / A1).

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_readiness.py
"""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / "probes" / "readiness.py"
FIXTURES = ROOT / "tests" / "fixtures" / "readiness"

spec = importlib.util.spec_from_file_location("readiness", PROBE)
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)


def load_fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class EvaluateFixtureTests(unittest.TestCase):
    """The verdict engine over recorded readings (A1 blocker matrix)."""

    def test_ready_fixture_allows_dispatch(self):
        report = readiness.evaluate(load_fixture("ready"))
        self.assertEqual(report["verdict"], "ready")
        self.assertEqual(report["dispatch"], "allowed")
        self.assertEqual(report["blockers"], [])
        for op in ("start", "liveness", "result", "cancel"):
            self.assertTrue(report["operations"][op]["supported"], op)

    def test_model_unavailable_blocks_and_denies_dispatch(self):
        report = readiness.evaluate(load_fixture("model-unavailable"))
        self.assertEqual(report["verdict"], "not-ready")
        self.assertEqual(report["dispatch"], "denied")
        self.assertIn("model-unavailable", report["blocker_codes"])
        self.assertFalse(report["dispatched"],
                         "a blocked verdict must dispatch nothing")

    def test_unsupported_version_blocks(self):
        report = readiness.evaluate(load_fixture("unsupported-version"))
        self.assertIn("unsupported-version", report["blocker_codes"])
        self.assertEqual(report["dispatch"], "denied")

    def test_conflicting_owner_blocks(self):
        report = readiness.evaluate(load_fixture("conflicting-owner"))
        self.assertIn("conflicting-task-owner", report["blocker_codes"])
        self.assertEqual(report["dispatch"], "denied")

    def test_missing_kanban_interface_blocks(self):
        report = readiness.evaluate(load_fixture("missing-interface"))
        self.assertIn("unsupported-interface", report["blocker_codes"])
        self.assertEqual(report["dispatch"], "denied")

    def test_unprotected_base_blocks_merge_endpoint(self):
        report = readiness.evaluate(load_fixture("base-unprotected"))
        self.assertIn("base-unprotected", report["blocker_codes"])
        self.assertEqual(report["dispatch"], "denied")

    def test_reachable_endpoint_with_wrong_model_blocks(self):
        readings = load_fixture("ready")
        readings["model"]["endpoint"]["configured_model_listed"] = False
        report = readiness.evaluate(readings)
        self.assertIn("model-unavailable", report["blocker_codes"])

    def test_operations_map_all_four_required(self):
        report = readiness.evaluate(load_fixture("ready"))
        self.assertEqual(
            set(report["operations"]),
            {"start", "liveness", "result", "cancel"},
            "A1 requires supported start/liveness/result/cancel operations")


class ProbeCliTests(unittest.TestCase):
    """CLI surface: self-test, fixture evaluation, verdict exit codes."""

    def run_probe(self, *argv):
        return subprocess.run(
            [sys.executable, str(PROBE), *argv],
            capture_output=True, text=True, timeout=60,
            env={"PATH": "/usr/bin:/bin"},  # fixtures must not need the host
        )

    def test_self_test_passes(self):
        r = self.run_probe("--self-test")
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("ready", "model-unavailable", "conflicting-owner"):
            self.assertIn(name, r.stdout)

    def test_fixture_ready_exit_0(self):
        r = self.run_probe("--fixture", str(FIXTURES / "ready.json"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["dispatch"], "allowed")

    def test_fixture_blocked_exit_1_is_verdict(self):
        r = self.run_probe("--fixture",
                           str(FIXTURES / "model-unavailable.json"))
        self.assertEqual(r.returncode, 1,
                         "a named-blocker verdict is exit 1, not a crash")
        self.assertEqual(json.loads(r.stdout)["dispatch"], "denied")

    def test_missing_fixture_exit_4(self):
        r = self.run_probe("--fixture", str(FIXTURES / "does-not-exist.json"))
        self.assertEqual(r.returncode, 4)

    def test_deliberately_unavailable_prereq_is_named(self):
        """A1's demo path: one unavailable prerequisite → one named blocker,
        nothing dispatched."""
        r = self.run_probe("--fixture",
                           str(FIXTURES / "model-unavailable.json"))
        report = json.loads(r.stdout)
        self.assertIn("model-unavailable", report["blocker_codes"])
        self.assertFalse(report["dispatched"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
