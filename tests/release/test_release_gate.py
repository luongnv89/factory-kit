#!/usr/bin/env python3
"""v1.1 conditional release gate — issue #31 (task 4.7, A2/A4).

CI-side mechanics tests for ``tools/probes/release_gate.py``: the
release checklist (the A2 gates plus the GATE-P03/GATE-P06 package
gates), the honest withheld disposition, the denominator-preserving
withheld reasons, the aggregate-only disclosure (A3) and the audits —
including the issue's required *failed-gate fixture* scenario, which
proves a non-passed gate keeps the release withheld and a fabricated
``release-ready`` is an instrumentation breach, never a publishable
verdict.

Run:  python3 -m unittest tests.release.test_release_gate -v
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import release_gate  # noqa: E402

ARCHIVE = (ROOT / "docs" / "releases" / "v1.1"
           / "release-gate-2026-10-05.json")

#: Planted canary for the secret-scan audit — the codebase's own test
#: idiom (tests/privacy/test_retention.py), constructed so it is never
#: a literal credential in the source.
CANARY = "ghp_" + "b2" * 20


def _passed_gate(**over):
    g = {"state": "passed", "summary": "s", "evidence": [],
         "sources": {}, "blockers": []}
    g.update(over)
    return g


def _fixture(gate_states):
    """A minimal release-gate archive with every checklist gate set to
    the given state (or a full gate dict)."""
    gates = {}
    for gid in release_gate.CHECKLIST_ORDER:
        spec = gate_states.get(gid, "passed")
        gates[gid] = (spec if isinstance(spec, dict)
                      else _passed_gate(state=spec))
    return {"kind": "release-gate", "version": release_gate.VERSION,
            "mode": "fixture-replay", "checklist":
            list(release_gate.CHECKLIST_ORDER),
            "sources": {}, "gates": gates, "disclosure": {}}


class TestRecordedAudit(unittest.TestCase):
    """The committed archive re-judges to itself — the replayability
    contract."""

    def test_live_run_is_complete_and_withheld(self):
        report = release_gate.run()
        self.assertEqual(report["verdict"], "release-check-complete")
        self.assertEqual(report["breaches"], [])
        self.assertEqual(report["disposition"], "release-withheld")
        self.assertEqual(report["deliverable_scope"], "recipe-scoped")

    def test_gate_states_on_this_tree(self):
        gates = release_gate.run()["gates"]
        self.assertEqual(gates["f09_f10_evidence"]["state"], "passed")
        self.assertEqual(gates["support_recipes"]["state"], "passed")
        self.assertEqual(gates["package_docs"]["state"], "passed")
        self.assertEqual(gates["v1_0_gate"]["state"], "blocked")
        self.assertEqual(gates["consent_trust_admission"]["state"],
                         "passed")
        self.assertEqual(gates["pilot_observations"]["state"], "passed")
        self.assertEqual(gates["owner_decision"]["state"], "pending")
        self.assertEqual(gates["q8_resolved"]["state"], "blocked")

    def test_every_nonpassed_gate_is_a_named_reason(self):
        report = release_gate.run()
        named = {r["gate"] for r in report["withheld_reasons"]}
        non_passed = {g for g, v in report["gates"].items()
                      if v["state"] != "passed"}
        self.assertEqual(named, non_passed)
        self.assertEqual(
            non_passed, {"v1_0_gate", "owner_decision", "q8_resolved"})

    def test_recorded_archive_rejudges_consistently(self):
        recorded = json.loads(ARCHIVE.read_text())
        re = release_gate.reevaluate(copy.deepcopy(recorded))
        self.assertEqual(re["verdict"], "release-check-complete")
        self.assertEqual(re["disposition"], recorded["disposition"])
        self.assertEqual(re["breaches"], [])

    def test_disclosure_is_aggregate_only(self):
        report = release_gate.run()
        audit = report["audits"]["aggregate_only"]
        self.assertEqual(audit["bad_keys"], [])
        self.assertEqual(audit["nonconforming_rows"], [])
        # A3 — proposed vs actual plus observation limits are carried.
        self.assertTrue(report["disclosure"]["targets"])
        self.assertTrue(report["disclosure"]["observation_limits"])
        self.assertEqual(report["audits"]["secret_scan"]["outcome"],
                         "clean")


class TestFailedGateFixtures(unittest.TestCase):
    """The withheld path must hold under failed, pending, unknown and
    fabricated gates — release never flips on edited text."""

    def test_failed_gate_fixture_stays_withheld(self):
        report = release_gate.reevaluate(
            _fixture({"q8_resolved": "blocked"}))
        self.assertEqual(report["disposition"], "release-withheld")
        self.assertEqual(report["verdict"], "release-check-complete")
        named = {r["gate"] for r in report["withheld_reasons"]}
        self.assertEqual(named, {"q8_resolved"})

    def test_pending_and_unknown_gates_also_withhold(self):
        report = release_gate.reevaluate(
            _fixture({"owner_decision": "pending",
                      "v1_0_gate": "unknown"}))
        self.assertEqual(report["disposition"], "release-withheld")
        named = {r["gate"]: r["state"]
                 for r in report["withheld_reasons"]}
        self.assertEqual(named, {"owner_decision": "pending",
                                 "v1_0_gate": "unknown"})

    def test_all_gates_passed_is_release_ready(self):
        report = release_gate.reevaluate(_fixture({}))
        self.assertEqual(report["disposition"], "release-ready")
        self.assertEqual(report["withheld_reasons"], [])
        self.assertEqual(report["verdict"], "release-check-complete")

    def test_fabricated_ready_over_blocked_gate_is_a_breach(self):
        fx = _fixture({"q8_resolved": "blocked"})
        fx["disposition"] = "release-ready"
        fx["withheld_reasons"] = []
        report = release_gate.reevaluate(fx)
        self.assertIn("no_publish_on_unpassed", report["breaches"])
        self.assertIn("withheld_reason_coverage", report["breaches"])
        self.assertEqual(report["verdict"],
                         "release-check-instrumentation-failed")

    def test_dropped_reason_is_a_breach(self):
        fx = _fixture({"q8_resolved": "blocked",
                       "owner_decision": "pending"})
        fx["disposition"] = "release-withheld"
        # The archive names one blocker but silently drops the other.
        fx["withheld_reasons"] = [
            {"gate": "q8_resolved", "state": "blocked",
             "detail": "x"}]
        report = release_gate.reevaluate(fx)
        self.assertIn("withheld_reason_coverage", report["breaches"])
        self.assertEqual(report["verdict"],
                         "release-check-instrumentation-failed")

    def test_unevaluated_gate_is_a_denominator_breach(self):
        fx = _fixture({})
        del fx["gates"]["q8_resolved"]
        report = release_gate.reevaluate(fx)
        self.assertIn("gate_denominator", report["breaches"])
        self.assertEqual(report["verdict"],
                         "release-check-instrumentation-failed")

    def test_bad_gate_state_is_a_denominator_breach(self):
        fx = _fixture({"q8_resolved": "green"})
        report = release_gate.reevaluate(fx)
        self.assertIn("gate_denominator", report["breaches"])

    def test_banned_content_key_is_an_aggregate_breach(self):
        fx = _fixture({})
        fx["disclosure"] = {"targets": [
            {"id": "t1", "verdict": "pass", "issue_body": "leaked"}]}
        report = release_gate.reevaluate(fx)
        self.assertIn("aggregate_only", report["breaches"])

    def test_secret_canary_is_an_instrumentation_breach(self):
        fx = _fixture({})
        fx["gates"]["q8_resolved"]["summary"] = CANARY
        report = release_gate.reevaluate(fx)
        self.assertEqual(
            report["audits"]["secret_scan"]["outcome"], "leak")
        self.assertIn("secret_scan", report["breaches"])

    def test_wrong_kind_fixture_cannot_complete(self):
        with self.assertRaises(release_gate.ReleaseGateError):
            release_gate.reevaluate({"kind": "external-pilot"})

    def test_malformed_fixture_cannot_complete(self):
        with self.assertRaises(release_gate.ReleaseGateError):
            release_gate.reevaluate({"kind": "release-gate"})


class TestLiveSourceEdges(unittest.TestCase):
    """Missing/unreadable sources fail closed on the live path — an
    absent record can never yield an unearned pass."""

    def test_missing_source_fails_closed(self):
        report = release_gate.run(paths={"q8": "/nonexistent-q8.md"})
        gate = report["gates"]["q8_resolved"]
        self.assertEqual(gate["state"], "unknown")
        self.assertEqual(report["disposition"], "release-withheld")
        self.assertEqual(
            report["audits"]["source_coverage"]["gaps"]["q8"],
            "missing")

    def test_wrong_kind_archive_is_unknown_not_pass(self):
        report = release_gate.run(
            paths={"external": str(
                ROOT / "docs" / "measurements"
                / "dogfood-comparison-2026-10-05.json")})
        for gid in ("consent_trust_admission", "pilot_observations"):
            self.assertEqual(report["gates"][gid]["state"], "unknown")
        self.assertEqual(report["disposition"], "release-withheld")

    def test_missing_package_doc_blocks_the_package_gate(self):
        with tempfile.TemporaryDirectory() as td:
            report = release_gate.run(package_dir=td)
        self.assertEqual(report["gates"]["package_docs"]["state"],
                         "blocked")
        self.assertEqual(report["disposition"], "release-withheld")


class TestCliContract(unittest.TestCase):
    """Exit-code vocabulary matches the sibling probes."""

    def test_clean_withheld_run_exits_zero(self):
        self.assertEqual(release_gate.main([]), 0)

    def test_fixture_recheck_exits_zero(self):
        self.assertEqual(
            release_gate.main(["--fixture", str(ARCHIVE)]), 0)

    def test_unreadable_fixture_exits_four(self):
        self.assertEqual(
            release_gate.main(["--fixture", "/nonexistent-xyz.json"]),
            4)

    def test_fabricated_fixture_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture({"q8_resolved": "blocked"})
            fx["disposition"] = "release-ready"
            path = Path(td) / "fabricated.json"
            path.write_text(json.dumps(fx))
            self.assertEqual(
                release_gate.main(["--fixture", str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
