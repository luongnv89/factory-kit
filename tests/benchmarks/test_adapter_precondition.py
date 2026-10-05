#!/usr/bin/env python3
"""Task 5.2 admission-precondition check — issue #33.

CI-side mechanics tests for ``tools/probes/adapter_precondition.py``:
the recorded GATE-E04 assessment, the owner-confirmed continue
decision, the operator adoption act (checked ``[x] **Adopt**`` plus the
five A4 fields naming the assessed singleton), the candidate-lane
supported hooks, the grounded ≤3-day effort bound and the adapter-code
fence — plus the fail-closed contract: a fabricated ``met`` over a
non-passed gate, an adoption gate flipped without a named candidate, a
dropped unmet reason, asserted adapter execution and adapter code on
disk while unmet each surface as instrumentation breaches, never an
authorizing verdict.

Run:  python3 -m unittest tests.benchmarks.test_adapter_precondition -v
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

import adapter_precondition as ap

ARCHIVE = (ROOT / "docs" / "adapters"
           / "adapter-precondition-2026-10-05.json")
F13_RECORD = (ROOT / "docs" / "decisions" / "f13-admission.md")

#: Planted canary for the secret-scan audit — the codebase's own test
#: idiom (tests/privacy/test_retention.py), constructed so it is never
#: a literal credential in the source.
CANARY = "ghp_" + "b2" * 20


def _passed_gate(**over):
    g = {"state": "passed", "summary": "s", "evidence": [],
         "sources": {}, "blockers": []}
    g.update(over)
    return g


def _fixture(gate_states=None, **over):
    """A minimal precondition archive: every checklist gate at the
    given state, a consistent adoption block naming the assessed
    singleton, no adapter dirs on disk."""
    gate_states = gate_states or {}
    gates = {}
    for gid in ap.CHECKLIST_ORDER:
        spec = gate_states.get(gid, "passed")
        gates[gid] = (spec if isinstance(spec, dict)
                      else _passed_gate(state=spec))
    report = {"kind": "adapter-precondition",
              "version": ap.VERSION,
              "mode": "fixture-replay",
              "checklist": list(ap.CHECKLIST_ORDER),
              "sources": {},
              "assessment": {"candidates_count": 1,
                             "disposition": "admission-ready-for-owner",
                             "verdict": "assessment-complete"},
              "assessed_candidate": {"harness": "codex-cli",
                                     "harness_version": "0.160.0"},
              "adoption": {
                  "checked": True, "disposition": "adopted",
                  "candidate": "codex-cli",
                  "candidate_version": "0.160.0",
                  "fields": {k: "v" for k, _ in ap.ADOPTION_FIELDS},
                  "operator_decision": "adopted codex-cli 0.160.0"},
              "adapter_dirs_present": [],
              "gates": gates,
              "adapter_execution": "none"}
    report.update(over)
    return report


ADOPTED_RECORD = """# Decision record: F13 admission

- [x] **Adopt** — owner adopts exactly one named candidate/version with
      support/trust recipe, bounded test scope, cost/capacity and
      acceptance gates recorded inline
- [ ] **Defer confirmed**
- [ ] **Reject**

- **Adopted candidate/version:** codex-cli 0.160.0
- **Support/trust recipe:** docs/recipes/adapter-support.md (bounded)
- **Bounded test scope:** candidate lane recipe tests only
- **Cost/capacity:** ≤3 developer-days, one active task
- **Acceptance gates:** the §8.2 fault suite and review gates

**Operator decision:** adopted codex-cli 0.160.0 — recorded by owner.
"""

REJECTED_RECORD = """# Decision record: F13 admission

- [ ] **Adopt**
- [ ] **Defer confirmed**
- [x] **Reject** — owner retires the F13 expansion track

**Operator decision:** rejected — candidate withdrawn.
"""

MISMATCHED_RECORD = """# Decision record: F13 admission

- [x] **Adopt** — owner adopts a candidate
- [ ] **Defer confirmed**
- [ ] **Reject**

- **Adopted candidate/version:** other-cli 9.9.9
- **Support/trust recipe:** some recipe
- **Bounded test scope:** lane tests
- **Cost/capacity:** ≤3 developer-days
- **Acceptance gates:** the fault suite

**Operator decision:** adopted other-cli 9.9.9.
"""

UNNAMED_RECORD = """# Decision record: F13 admission

- [x] **Adopt** — owner adopts exactly one named candidate/version with
      support/trust recipe, bounded test scope, cost/capacity and
      acceptance gates recorded inline
- [ ] **Defer confirmed**
- [ ] **Reject**

**Operator decision:** adopted — the recorded candidate.
"""


class TestRecordedCheck(unittest.TestCase):
    """The committed archive re-judges to itself — the replayability
    contract — and the live tree's gates match the recorded ledger."""

    def test_live_run_is_complete_and_unmet(self):
        report = ap.run()
        self.assertEqual(report["verdict"],
                         "precondition-check-complete")
        self.assertEqual(report["breaches"], [])
        self.assertEqual(report["precondition"], "unmet")
        self.assertEqual(report["authorization"], "blocked")
        self.assertEqual(report["adapter_execution"], "none")
        self.assertIn("blocked", report["task_5_2"])

    def test_gate_states_on_this_tree(self):
        gates = ap.run()["gates"]
        self.assertEqual(gates["assessment_recorded"]["state"],
                         "passed")
        self.assertEqual(gates["pilot_continue_confirmed"]["state"],
                         "blocked")
        self.assertEqual(gates["operator_adoption"]["state"],
                         "pending")
        self.assertEqual(gates["adoption_scope"]["state"], "pending")
        self.assertEqual(gates["adopted_candidate_match"]["state"],
                         "pending")
        self.assertEqual(gates["supported_hooks"]["state"], "blocked")
        self.assertEqual(gates["effort_bound"]["state"], "blocked")
        self.assertEqual(gates["adapter_code_fence"]["state"],
                         "passed")

    def test_every_nonpassed_gate_is_a_named_reason(self):
        report = ap.run()
        named = {r["gate"] for r in report["unmet_reasons"]}
        non_passed = {g for g, v in report["gates"].items()
                      if v["state"] != "passed"}
        self.assertEqual(named, non_passed)
        self.assertEqual(len(non_passed), 6)
        self.assertEqual(
            non_passed,
            {"pilot_continue_confirmed", "operator_adoption",
             "adoption_scope", "adopted_candidate_match",
             "supported_hooks", "effort_bound"})

    def test_supported_hooks_names_unproven_rows(self):
        gate = ap.run()["gates"]["supported_hooks"]
        self.assertIn("probe:hermes_lifecycle_integration",
                      gate["no_go_rows"])
        self.assertIn("readiness_auth", gate["unproven_rows"])
        self.assertIn("cancel_descendants", gate["unproven_rows"])

    def test_recorded_archive_rejudges_consistently(self):
        recorded = json.loads(ARCHIVE.read_text())
        re = ap.reevaluate(copy.deepcopy(recorded))
        self.assertEqual(re["verdict"], "precondition-check-complete")
        self.assertEqual(re["precondition"], recorded["precondition"])
        self.assertEqual(re["authorization"],
                         recorded["authorization"])
        self.assertEqual(re["breaches"], [])

    def test_scope_and_never_authorized(self):
        report = ap.run()
        self.assertTrue(report["scope"]["one_active_task_per_project"])
        self.assertEqual(report["scope"]["additional_adapters"], 0)
        self.assertFalse(report["scope"]["dates_promised"])
        joined = " ".join(report["never_authorized"])
        self.assertIn("adapter code execution", joined)
        self.assertIn("second scheduler", joined)
        self.assertTrue(report["re_entry"])
        self.assertEqual(
            report["audits"]["secret_scan"]["outcome"], "clean")


class TestGatedEvidence(unittest.TestCase):
    """The unmet/blocked path must hold under pending, fabricated and
    mismatched fixtures — authorization never flips on edited text."""

    def test_all_gates_passed_authorizes_within_scope(self):
        report = ap.reevaluate(_fixture())
        self.assertEqual(report["precondition"], "met")
        self.assertEqual(report["authorization"],
                         "authorized-for-implementation")
        self.assertEqual(report["unmet_reasons"], [])
        # Still never an execution by this probe.
        self.assertEqual(report["adapter_execution"], "none")

    def test_pending_gate_stays_unmet(self):
        report = ap.reevaluate(
            _fixture({"operator_adoption": "pending"}))
        self.assertEqual(report["precondition"], "unmet")
        self.assertEqual(report["authorization"], "blocked")
        named = {r["gate"] for r in report["unmet_reasons"]}
        self.assertEqual(named, {"operator_adoption"})

    def test_fabricated_met_over_blocked_gate_is_a_breach(self):
        fx = _fixture({"supported_hooks": "blocked"})
        fx["precondition"] = "met"
        fx["authorization"] = "authorized-for-implementation"
        fx["unmet_reasons"] = []
        report = ap.reevaluate(fx)
        self.assertIn("no_authorization_on_unmet",
                      report["breaches"])
        self.assertIn("unmet_reason_coverage", report["breaches"])
        self.assertEqual(
            report["verdict"],
            "precondition-check-instrumentation-failed")

    def test_dropped_reason_is_a_breach(self):
        fx = _fixture({"effort_bound": "blocked",
                       "supported_hooks": "blocked"})
        fx["precondition"] = "unmet"
        fx["authorization"] = "blocked"
        fx["unmet_reasons"] = [
            {"gate": "effort_bound", "state": "blocked",
             "detail": "x"}]
        report = ap.reevaluate(fx)
        self.assertIn("unmet_reason_coverage", report["breaches"])

    def test_unevaluated_gate_is_a_denominator_breach(self):
        fx = _fixture()
        del fx["gates"]["effort_bound"]
        report = ap.reevaluate(fx)
        self.assertIn("gate_denominator", report["breaches"])

    def test_bad_gate_state_is_a_denominator_breach(self):
        fx = _fixture({"effort_bound": "green"})
        report = ap.reevaluate(fx)
        self.assertIn("gate_denominator", report["breaches"])

    def test_adoption_gate_without_named_candidate_is_a_breach(self):
        fx = _fixture()
        fx["adoption"] = {"checked": True, "disposition": "adopted",
                          "candidate": None, "candidate_version": None,
                          "fields": {}, "operator_decision": "adopted"}
        report = ap.reevaluate(fx)
        self.assertIn("adoption_consistency", report["breaches"])

    def test_missing_assessed_singleton_is_a_breach(self):
        fx = _fixture()
        fx["assessed_candidate"] = None
        report = ap.reevaluate(fx)
        self.assertIn("singleton_reference", report["breaches"])

    def test_asserted_adapter_execution_is_a_breach(self):
        fx = _fixture()
        fx["adapter_execution"] = "executed"
        report = ap.reevaluate(fx)
        self.assertIn("no_adapter_execution", report["breaches"])

    def test_adapter_code_while_unmet_is_a_breach(self):
        fx = _fixture({"operator_adoption": "pending"})
        fx["adapter_dirs_present"] = ["factory_kit/adapters"]
        fx["precondition"] = "unmet"
        fx["authorization"] = "blocked"
        fx["unmet_reasons"] = [
            {"gate": g, "state": "blocked", "detail": "x"}
            for g in ("operator_adoption", "adapter_code_fence")]
        fx["unmet_reasons"][0]["state"] = "pending"
        report = ap.reevaluate(fx)
        self.assertIn("unauthorized_execution", report["breaches"])
        self.assertEqual(
            report["gates"]["adapter_code_fence"]["state"],
            "blocked")

    def test_adapter_code_when_authorized_is_clean(self):
        fx = _fixture()
        fx["adapter_dirs_present"] = ["factory_kit/adapters"]
        report = ap.reevaluate(fx)
        self.assertNotIn("unauthorized_execution",
                         report["breaches"])
        self.assertEqual(
            report["gates"]["adapter_code_fence"]["state"], "passed")
        self.assertEqual(report["precondition"], "met")

    def test_banned_content_key_is_an_aggregate_breach(self):
        fx = _fixture()
        fx["adoption"]["fields"]["issue_body"] = "leaked"
        report = ap.reevaluate(fx)
        self.assertIn("aggregate_only", report["breaches"])

    def test_secret_canary_is_an_instrumentation_breach(self):
        fx = _fixture()
        fx["gates"]["effort_bound"]["summary"] = CANARY
        report = ap.reevaluate(fx)
        self.assertEqual(
            report["audits"]["secret_scan"]["outcome"], "leak")
        self.assertIn("secret_scan", report["breaches"])

    def test_wrong_kind_fixture_cannot_complete(self):
        with self.assertRaises(ap.AdapterPreconditionError):
            ap.reevaluate({"kind": "external-pilot"})

    def test_malformed_fixture_cannot_complete(self):
        with self.assertRaises(ap.AdapterPreconditionError):
            ap.reevaluate({"kind": "adapter-precondition"})


class TestAdoptionParsing(unittest.TestCase):
    """The F13 record parser: only a checked Adopt row plus post-marker
    field values counts — deferred-disposition prose and checkbox text
    never do."""

    def test_current_record_parses_pending(self):
        adoption = ap._parse_adoption(F13_RECORD.read_text())
        self.assertEqual(adoption["disposition"], "pending")
        self.assertFalse(adoption["checked"])
        self.assertIsNone(adoption["candidate"])
        self.assertIsNone(adoption["candidate_version"])
        self.assertFalse(any(adoption["fields"].values()))

    def test_adopted_record_parses_all_fields(self):
        adoption = ap._parse_adoption(ADOPTED_RECORD)
        self.assertEqual(adoption["disposition"], "adopted")
        self.assertEqual(adoption["candidate"], "codex-cli")
        self.assertEqual(adoption["candidate_version"], "0.160.0")
        self.assertTrue(all(adoption["fields"].values()))

    def test_rejected_record_parses_rejected(self):
        adoption = ap._parse_adoption(REJECTED_RECORD)
        self.assertEqual(adoption["disposition"], "rejected")
        self.assertFalse(adoption["checked"])

    def test_mismatched_candidate_parses_for_match_gate(self):
        adoption = ap._parse_adoption(MISMATCHED_RECORD)
        self.assertEqual(adoption["candidate"], "other-cli")
        self.assertEqual(adoption["candidate_version"], "9.9.9")

    def test_checked_box_without_candidate_is_unnamed(self):
        adoption = ap._parse_adoption(UNNAMED_RECORD)
        self.assertEqual(adoption["disposition"], "adopted")
        self.assertIsNone(adoption["candidate_version"])
        self.assertFalse(any(adoption["fields"].values()))


class TestLiveSourceEdges(unittest.TestCase):
    """Missing/unreadable sources fail closed on the live path — an
    absent record can never yield an unearned pass, and a recorded
    adoption of a different candidate is still unmet."""

    def _run_with_f13(self, text):
        with tempfile.TemporaryDirectory() as td:
            rec = Path(td) / "f13.md"
            rec.write_text(text)
            return ap.run(paths={"f13_record": str(rec)})

    def test_missing_record_keeps_pending(self):
        report = ap.run(paths={"f13_record": "/nonexistent-f13.md"})
        self.assertEqual(report["gates"]["operator_adoption"]
                         ["state"], "pending")
        self.assertEqual(report["precondition"], "unmet")
        self.assertEqual(report["authorization"], "blocked")

    def test_adopted_record_still_unmet_on_unproven_hooks(self):
        report = self._run_with_f13(ADOPTED_RECORD)
        gates = report["gates"]
        self.assertEqual(gates["operator_adoption"]["state"],
                         "passed")
        self.assertEqual(gates["adoption_scope"]["state"], "passed")
        self.assertEqual(gates["adopted_candidate_match"]["state"],
                         "passed")
        # Adoption is necessary, never sufficient: hooks and the
        # continue decision still fail closed.
        self.assertEqual(gates["supported_hooks"]["state"],
                         "blocked")
        self.assertEqual(gates["pilot_continue_confirmed"]["state"],
                         "blocked")
        self.assertEqual(report["precondition"], "unmet")

    def test_mismatched_adoption_blocks_candidate_gate(self):
        report = self._run_with_f13(MISMATCHED_RECORD)
        gate = report["gates"]["adopted_candidate_match"]
        self.assertEqual(gate["state"], "blocked")
        self.assertIn("out of scope", "; ".join(gate["blockers"]))
        self.assertEqual(report["precondition"], "unmet")

    def test_rejected_admission_blocks_not_pending(self):
        report = self._run_with_f13(REJECTED_RECORD)
        gate = report["gates"]["operator_adoption"]
        self.assertEqual(gate["state"], "blocked")
        self.assertIn("rejected", "; ".join(gate["blockers"]))

    def test_adoption_missing_fields_blocks_scope_gate(self):
        report = self._run_with_f13(UNNAMED_RECORD)
        self.assertEqual(report["gates"]["operator_adoption"]
                         ["state"], "passed")
        self.assertEqual(report["gates"]["adoption_scope"]["state"],
                         "blocked")
        self.assertEqual(report["precondition"], "unmet")

    def test_missing_assessment_is_unknown_not_pass(self):
        report = ap.run(
            paths={"assessment": "/nonexistent-assessment.json"})
        self.assertEqual(report["gates"]["assessment_recorded"]
                         ["state"], "unknown")
        self.assertEqual(report["gates"]["supported_hooks"]["state"],
                         "unknown")
        self.assertEqual(report["precondition"], "unmet")
        self.assertIn("singleton_reference", report["breaches"])

    def test_wrong_kind_assessment_is_unknown(self):
        report = ap.run(paths={"assessment": str(
            ROOT / "docs" / "measurements"
            / "dogfood-comparison-2026-10-05.json")})
        self.assertEqual(report["gates"]["assessment_recorded"]
                         ["state"], "unknown")
        self.assertEqual(report["precondition"], "unmet")


class TestCliContract(unittest.TestCase):
    """Exit-code vocabulary matches the sibling probes."""

    def test_clean_unmet_run_exits_zero(self):
        self.assertEqual(ap.main([]), 0)

    def test_fixture_recheck_exits_zero(self):
        self.assertEqual(ap.main(["--fixture", str(ARCHIVE)]), 0)

    def test_unreadable_fixture_exits_four(self):
        self.assertEqual(
            ap.main(["--fixture", "/nonexistent-xyz.json"]), 4)

    def test_fabricated_fixture_exits_one(self):
        with tempfile.TemporaryDirectory() as td:
            fx = _fixture({"supported_hooks": "blocked"})
            fx["precondition"] = "met"
            path = Path(td) / "fabricated.json"
            path.write_text(json.dumps(fx))
            self.assertEqual(ap.main(["--fixture", str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
