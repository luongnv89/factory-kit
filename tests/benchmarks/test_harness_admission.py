#!/usr/bin/env python3
"""§8.4/GATE-E04 optional-harness admission assessment — issue #32
(task 5.1).

CI-side mechanics tests for ``tools/probes/harness_admission.py``: the
A1 pilot gate (completed dogfood cohort, three external measurements,
operator-burden cost, the actual continue/narrow/stop decision,
repeated-use evidence), the A2 single-candidate/demand/maintenance/
capability-matrix rows, the A3 compatibility probes, the A4 operator
adoption record and the A5 scope lock — plus the fail-closed contract:
a fabricated admission over a non-passed gate, an over-claimed
capability row, a dropped deferred reason and an asserted adapter
execution each surface as instrumentation breaches, never an
admissible verdict.

Run:  python3 -m unittest tests.benchmarks.test_harness_admission -v
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

import harness_admission as ha

ARCHIVE = (ROOT / "docs" / "adapters"
           / "admission-assessment-2026-10-05.json")
CANDIDATE = (ROOT / "tests" / "fixtures" / "adapters"
             / "candidate-codex-cli.json")

#: Planted canary for the secret-scan audit — the codebase's own test
#: idiom (tests/privacy/test_retention.py), constructed so it is never
#: a literal credential in the source.
CANARY = "ghp_" + "b2" * 20


def _passed_gate(**over):
    g = {"state": "passed", "summary": "s", "evidence": [],
         "sources": {}, "blockers": []}
    g.update(over)
    return g


def _matrix():
    rows = [{"capability": c, "required": True, "optional": False,
             "claimed": "unproven", "derived": "unproven",
             "claim": "x", "evidence": []}
            for c in ha.REQUIRED_CAPABILITIES]
    rows += [{"capability": c, "required": False, "optional": True,
              "claimed": "optional", "derived": "optional",
              "claim": "x", "evidence": []}
             for c in ha.OPTIONAL_CAPABILITIES]
    return rows


def _probes():
    return [{"probe": p, "claimed": "unproven", "derived": "unproven",
             "claim": "x", "evidence": []} for p in ha.REQUIRED_PROBES]


def _candidate():
    return {"harness": "codex-cli", "readings_key": "codex",
            "harness_version": "0.160.0",
            "integration_path": "hermes codex-runtime",
            "provenance": {"host": {"source": "s"},
                           "model": {"source": "s"},
                           "skill": {"source": "s"}},
            "demand": {"signals": [{"id": "s1", "class": "c",
                                    "favorable": False,
                                    "source": "s"}]},
            "incremental_maintenance": {"new_surfaces": ["x"],
                                        "effort_class": "≤3d",
                                        "measured_context": "m"},
            "matrix": _matrix(), "probes": _probes()}


def _fixture(gate_states=None):
    """A minimal admission archive: every checklist gate at the given
    state (or a full gate dict), one consistent candidate, clean
    safety."""
    gate_states = gate_states or {}
    gates = {}
    for gid in ha.CHECKLIST_ORDER:
        spec = gate_states.get(gid, "passed")
        gates[gid] = (spec if isinstance(spec, dict)
                      else _passed_gate(state=spec))
    return {"kind": "harness-admission-assessment",
            "version": ha.VERSION,
            "mode": "fixture-replay",
            "checklist": list(ha.CHECKLIST_ORDER),
            "sources": {}, "gates": gates,
            "candidates": [_candidate()],
            "demand": {"candidate": "codex-cli", "signals": []},
            "safety": {"clean": True, "reasons": []},
            "adapter_execution": "none"}


class TestRecordedAssessment(unittest.TestCase):
    """The committed archive re-judges to itself — the replayability
    contract — and the live tree's gates match the recorded ledger."""

    def test_live_run_is_complete_and_deferred(self):
        report = ha.run()
        self.assertEqual(report["verdict"], "assessment-complete")
        self.assertEqual(report["breaches"], [])
        self.assertEqual(report["disposition"], "admission-deferred")
        self.assertEqual(report["adapter_execution"], "none")
        self.assertIn("deferred", report["task_5_2"])

    def test_gate_states_on_this_tree(self):
        gates = ha.run()["gates"]
        self.assertEqual(gates["dogfood_cohort"]["state"], "passed")
        self.assertEqual(gates["external_measurements"]["state"],
                         "passed")
        self.assertEqual(gates["operator_burden"]["state"], "passed")
        self.assertEqual(gates["pilot_decision"]["state"], "blocked")
        self.assertEqual(gates["repeat_use_evidence"]["state"],
                         "blocked")
        self.assertEqual(gates["single_candidate"]["state"], "passed")
        self.assertEqual(gates["demand"]["state"], "blocked")
        self.assertEqual(gates["maintenance_cost"]["state"], "passed")
        self.assertEqual(gates["capability_matrix"]["state"], "passed")
        self.assertEqual(gates["probe_authorization"]["state"],
                         "blocked")
        self.assertEqual(gates["probe_credential_separation"]["state"],
                         "blocked")
        self.assertEqual(gates["probe_hermes_lifecycle"]["state"],
                         "blocked")
        self.assertEqual(gates["probe_hermes_lifecycle"]["derived"],
                         "no-go")
        self.assertEqual(gates["probe_endpoint_compat"]["state"],
                         "blocked")
        self.assertEqual(gates["operator_adoption"]["state"],
                         "pending")
        self.assertEqual(gates["scope_lock"]["state"], "passed")

    def test_every_nonpassed_gate_is_a_named_reason(self):
        report = ha.run()
        named = {r["gate"] for r in report["deferred_reasons"]}
        non_passed = {g for g, v in report["gates"].items()
                      if v["state"] != "passed"}
        self.assertEqual(named, non_passed)
        self.assertEqual(len(non_passed), 8)
        self.assertEqual(
            non_passed,
            {"pilot_decision", "repeat_use_evidence", "demand",
             "probe_authorization", "probe_credential_separation",
             "probe_hermes_lifecycle", "probe_endpoint_compat",
             "operator_adoption"})

    def test_candidate_singleton_and_matrix_shape(self):
        cand = ha.run()["candidates"]
        self.assertEqual(len(cand), 1)
        self.assertEqual(cand[0]["harness"], "codex-cli")
        self.assertEqual(cand[0]["harness_version"], "0.160.0")
        caps = {r["capability"]: r for r in cand[0]["matrix"]}
        for c in ha.REQUIRED_CAPABILITIES:
            self.assertIn(c, caps)
            self.assertTrue(caps[c]["required"])
        for c in ha.OPTIONAL_CAPABILITIES:
            self.assertTrue(caps[c]["optional"])
            self.assertEqual(caps[c]["claimed"], "optional")

    def test_recorded_archive_rejudges_consistently(self):
        recorded = json.loads(ARCHIVE.read_text())
        re = ha.reevaluate(copy.deepcopy(recorded))
        self.assertEqual(re["verdict"], "assessment-complete")
        self.assertEqual(re["disposition"], recorded["disposition"])
        self.assertEqual(re["breaches"], [])

    def test_scope_and_never_authorized(self):
        report = ha.run()
        self.assertTrue(report["scope"]["one_active_task_per_project"])
        self.assertEqual(report["scope"]["additional_adapters"], 0)
        self.assertFalse(report["scope"]["dates_promised"])
        self.assertFalse(report["scope"]["broader_abstraction"])
        joined = " ".join(report["never_authorized"])
        self.assertIn("adapter code execution", joined)
        self.assertIn("second scheduler", joined)
        self.assertTrue(report["re_entry"])
        self.assertEqual(
            report["audits"]["secret_scan"]["outcome"], "clean")


class TestGatedEvidence(unittest.TestCase):
    """The deferred/blocked path must hold under failed, pending,
    fabricated and over-claimed fixtures — admission never flips on
    edited text."""

    def test_all_gates_passed_is_ready_for_owner_not_admitted(self):
        report = ha.reevaluate(_fixture())
        self.assertEqual(report["disposition"],
                         "admission-ready-for-owner")
        self.assertEqual(report["deferred_reasons"], [])
        # Still never an execution: the owner act gates 5.2.
        self.assertIn("owner", report["task_5_2"])
        self.assertEqual(report["adapter_execution"], "none")

    def test_failed_gate_stays_deferred(self):
        report = ha.reevaluate(
            _fixture({"pilot_decision": "blocked"}))
        self.assertEqual(report["disposition"], "admission-deferred")
        named = {r["gate"] for r in report["deferred_reasons"]}
        self.assertEqual(named, {"pilot_decision"})

    def test_unclean_safety_blocks_not_defers(self):
        fx = _fixture()
        fx["safety"] = {"clean": False,
                        "reasons": ["fault-matrix verdict 'failed'"]}
        report = ha.reevaluate(fx)
        self.assertEqual(report["disposition"], "admission-blocked")

    def test_fabricated_ready_over_blocked_gate_is_a_breach(self):
        fx = _fixture({"pilot_decision": "blocked"})
        fx["disposition"] = "admission-ready-for-owner"
        fx["deferred_reasons"] = []
        report = ha.reevaluate(fx)
        self.assertIn("no_admission_on_gated", report["breaches"])
        self.assertIn("deferred_reason_coverage", report["breaches"])
        self.assertEqual(report["verdict"],
                         "assessment-instrumentation-failed")

    def test_dropped_reason_is_a_breach(self):
        fx = _fixture({"pilot_decision": "blocked",
                       "demand": "blocked"})
        fx["disposition"] = "admission-deferred"
        fx["deferred_reasons"] = [
            {"gate": "demand", "state": "blocked", "detail": "x"}]
        report = ha.reevaluate(fx)
        self.assertIn("deferred_reason_coverage", report["breaches"])

    def test_unevaluated_gate_is_a_denominator_breach(self):
        fx = _fixture()
        del fx["gates"]["scope_lock"]
        report = ha.reevaluate(fx)
        self.assertIn("gate_denominator", report["breaches"])

    def test_bad_gate_state_is_a_denominator_breach(self):
        fx = _fixture({"demand": "green"})
        report = ha.reevaluate(fx)
        self.assertIn("gate_denominator", report["breaches"])

    def test_zero_or_two_candidates_is_a_singleton_breach(self):
        fx = _fixture()
        fx["candidates"] = []
        self.assertIn("candidate_singleton",
                      ha.reevaluate(fx)["breaches"])
        fx = _fixture()
        fx["candidates"] = [_candidate(), _candidate()]
        self.assertIn("candidate_singleton",
                      ha.reevaluate(fx)["breaches"])

    def test_over_claimed_capability_is_a_breach(self):
        fx = _fixture({"capability_matrix": "blocked"})
        row = next(r for r in fx["candidates"][0]["matrix"]
                   if r["capability"] == "start")
        row["claimed"] = "proven"
        report = ha.reevaluate(fx)
        self.assertIn("matrix_honesty", report["breaches"])

    def test_presumed_optional_row_is_a_breach(self):
        fx = _fixture({"capability_matrix": "blocked"})
        row = next(r for r in fx["candidates"][0]["matrix"]
                   if r["capability"] == "live_steering")
        row["claimed"] = "proven"
        row["optional"] = False
        report = ha.reevaluate(fx)
        self.assertIn("matrix_honesty", report["breaches"])

    def test_missing_probe_row_is_a_breach(self):
        fx = _fixture({"capability_matrix": "blocked"})
        fx["candidates"][0]["probes"] = \
            fx["candidates"][0]["probes"][:-1]
        report = ha.reevaluate(fx)
        self.assertIn("matrix_honesty", report["breaches"])

    def test_asserted_adapter_execution_is_a_breach(self):
        fx = _fixture()
        fx["adapter_execution"] = "executed"
        report = ha.reevaluate(fx)
        self.assertIn("no_adapter_execution", report["breaches"])

    def test_banned_content_key_is_an_aggregate_breach(self):
        fx = _fixture()
        fx["demand"] = {"signals": [{"id": "s1",
                                     "issue_body": "leaked"}]}
        report = ha.reevaluate(fx)
        self.assertIn("aggregate_only", report["breaches"])

    def test_secret_canary_is_an_instrumentation_breach(self):
        fx = _fixture()
        fx["gates"]["demand"]["summary"] = CANARY
        report = ha.reevaluate(fx)
        self.assertEqual(
            report["audits"]["secret_scan"]["outcome"], "leak")
        self.assertIn("secret_scan", report["breaches"])

    def test_wrong_kind_fixture_cannot_complete(self):
        with self.assertRaises(ha.HarnessAdmissionError):
            ha.reevaluate({"kind": "external-pilot"})

    def test_malformed_fixture_cannot_complete(self):
        with self.assertRaises(ha.HarnessAdmissionError):
            ha.reevaluate({"kind": "harness-admission-assessment"})


class TestLiveSourceEdges(unittest.TestCase):
    """Missing/unreadable sources fail closed on the live path — an
    absent record can never yield an unearned pass."""

    def test_missing_candidate_fails_closed(self):
        report = ha.run(paths={"candidate": "/nonexistent-c.json"})
        self.assertEqual(report["gates"]["single_candidate"]["state"],
                         "unknown")
        self.assertEqual(report["disposition"], "admission-deferred")
        self.assertIn("candidate_singleton", report["breaches"])
        self.assertEqual(
            report["audits"]["source_coverage"]["gaps"]["candidate"],
            "missing")

    def test_wrong_kind_archive_is_unknown_not_pass(self):
        report = ha.run(paths={"evaluation": str(
            ROOT / "docs" / "measurements"
            / "dogfood-comparison-2026-10-05.json")})
        self.assertEqual(report["gates"]["pilot_decision"]["state"],
                         "unknown")
        self.assertEqual(report["gates"]["operator_burden"]["state"],
                         "unknown")
        self.assertEqual(report["disposition"], "admission-deferred")

    def test_missing_decision_record_keeps_pending(self):
        report = ha.run(paths={"f13_record": "/nonexistent-f13.md"})
        self.assertEqual(report["gates"]["operator_adoption"]["state"],
                         "pending")
        self.assertEqual(report["disposition"], "admission-deferred")


class TestCliContract(unittest.TestCase):
    """Exit-code vocabulary matches the sibling probes."""

    def test_clean_deferred_run_exits_zero(self):
        self.assertEqual(ha.main([]), 0)

    def test_fixture_recheck_exits_zero(self):
        self.assertEqual(ha.main(["--fixture", str(ARCHIVE)]), 0)

    def test_unreadable_fixture_exits_four(self):
        self.assertEqual(
            ha.main(["--fixture", "/nonexistent-xyz.json"]), 4)

    def test_fabricated_fixture_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture({"demand": "blocked"})
            fx["disposition"] = "admission-ready-for-owner"
            path = Path(td) / "fabricated.json"
            path.write_text(json.dumps(fx))
            self.assertEqual(ha.main(["--fixture", str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
