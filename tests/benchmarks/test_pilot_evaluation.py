#!/usr/bin/env python3
"""§1.4/§8.3 usefulness evaluation — issue #30 (task 4.6).

CI-side mechanics tests for ``tools/probes/pilot_evaluation.py``: the
archive aggregation, the unified evidence table (every proposed
internal/external numerical target with source, window, denominator,
missing data and verdict), the A2 burden comparison, the aggregation
audits (cell cross-check, denominator integrity, missing-data
coverage, takeover retention, no positive claim on incomplete
evidence, Q9 verbatim), and the recorded continue/narrow/stop
disposition — including the issue's required *incomplete and
unfavorable evidence* scenarios, which mutate the extracted evidence
and prove the evaluation still cannot emit a positive claim.

Run:  python3 -m unittest tests.benchmarks.test_pilot_evaluation -v
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
PROBES = ROOT / "tools" / "probes"
if str(PROBES) not in sys.path:
    sys.path.insert(0, str(PROBES))

import pilot_evaluation as pe

from factory_kit.privacy import export as _export

_EVAL_DOC = ROOT / "docs" / "measurements" / "pilot-evaluation.md"
_DECISION_DOC = ROOT / "docs" / "decisions" / "continue-narrow-stop.md"
_ARCHIVE = ROOT / "docs" / "measurements" / "pilot-evaluation-2026-10-05.json"

_RUN = None


def _run_once():
    """The aggregation is read-only over committed archives — run it
    once per test process and reuse the report."""
    global _RUN
    if _RUN is None:
        _RUN = pe.run()
    return _RUN


def _evaluate_mutated(**mutations):
    """Evaluate a deep copy of the recorded evidence with caller
    mutations applied — the incomplete/unfavorable scenario driver."""
    report = copy.deepcopy(_run_once())
    evidence = report["evidence"]
    for path, value in mutations.items():
        slot = evidence
        parts = path.split(".")
        for part in parts[:-1]:
            slot = slot[part]
        slot[parts[-1]] = value
    return pe.evaluate({"evidence": evidence})


def _row(report, rid):
    return {r["id"]: r for r in report["targets"]["results"]}[rid]


class TestAggregationRun(unittest.TestCase):
    """The recorded-archive aggregation itself."""

    def test_run_completes_clean(self):
        report = _run_once()
        self.assertEqual(report["verdict"], "evaluation-complete")
        self.assertEqual(report["breaches"], [])
        self.assertEqual(report["instrumentation"], "clean")
        self.assertEqual(report["mode"], "archive-aggregation")
        self.assertEqual(report["kind"], "pilot-evaluation")

    def test_evidence_table_covers_every_proposed_target(self):
        rows = _run_once()["targets"]["results"]
        self.assertEqual(len(rows), 16)
        scopes = {}
        for r in rows:
            scopes[r["scope"]] = scopes.get(r["scope"], 0) + 1
        self.assertEqual(
            scopes,
            {"internal-local": 10, "internal-cohort": 3, "external-pilot": 3},
        )
        for r in rows:
            self.assertIn("source", r)
            self.assertIn(r["verdict"], ("pass", "fail", "inconclusive"))

    def test_cohort_cells_crosschecked(self):
        report = _run_once()
        self.assertEqual(report["audits"]["cell_crosscheck"]["mismatches"], [])
        pmr = _row(report, "cohort.preview_merge_ready")
        self.assertEqual(pmr["verdict"], "fail")
        self.assertEqual(pmr["measured"], 7)
        self.assertIn("8 of 10", pmr["proposed"])
        red = _row(report, "cohort.intervention_reduction")
        self.assertEqual(red["verdict"], "inconclusive")
        self.assertEqual(red["measured"], 30.43)
        self.assertEqual([m["key"] for m in red["missing"]], ["c9"])

    def test_pilot_cells_and_missing_labels(self):
        report = _run_once()
        self.assertEqual(_row(report, "pilot.repeat_use_2x4wk_90d")["verdict"], "fail")
        ind = _row(report, "pilot.independent_setup_2_in_45min")
        self.assertEqual(ind["verdict"], "pass")
        self.assertEqual([e["key"] for e in ind["excluded"]], ["obs-gamma"])
        rep = _row(report, "pilot.repeat_use_2x4wk_90d")
        missing_keys = {m["key"] for m in rep["missing"]}
        self.assertEqual(missing_keys, {"obs-delta", "obs-gamma"})
        self.assertEqual(rep["failed_runs_retained"], 1)

    def test_q9_recorded_pending_not_adjusted(self):
        report = _run_once()
        self.assertIn("pending", report["q9"]["status"])
        self.assertEqual(report["q9"]["adjustments"], [])
        self.assertEqual(report["audits"]["q9_recorded"]["problems"], [])


class TestBurdenComparison(unittest.TestCase):
    """A2 — intervention/setup/maintenance vs the ten-issue baseline,
    quantified rather than equating output volume with value."""

    def test_burden_cells(self):
        b = _run_once()["burden"]
        self.assertEqual(b["cohort_median_intervention_minutes"], 16.0)
        self.assertEqual(b["cohort_known_n"], 9)
        self.assertEqual(b["cohort_missing"], ["c9"])
        self.assertEqual(b["baseline_median_minutes"], 23.0)
        self.assertEqual(b["baseline_n"], 10)
        self.assertTrue(b["baseline_comparable"])
        self.assertEqual(b["reduction_pct"], 30.43)
        self.assertEqual(b["reduction_verdict"], "inconclusive")

    def test_overhead_erases_savings(self):
        b = _run_once()["burden"]
        self.assertEqual(b["overhead"]["total_overhead_minutes"], 75.0)
        self.assertEqual(b["overhead"]["amortized_per_issue_minutes"], 7.5)
        # 16.0 + 7.5 = 23.5 — a hair above the 23.0 baseline median:
        # the honest shape §1.4 asks for, savings erased.
        self.assertEqual(b["total_burden_median_minutes"], 23.5)
        self.assertEqual(b["net_vs_baseline_minutes"], 0.5)
        self.assertTrue(b["maintenance_erases_savings"])
        self.assertEqual(b["successful_median"], 13.0)
        self.assertEqual(b["unsuccessful_median"], 18.5)

    def test_uncertainty_and_waits_separated(self):
        b = _run_once()["burden"]
        self.assertTrue(b["uncertainty"])
        self.assertEqual(b["external_builder_active_minutes"], 51.0)
        self.assertIn("human_wait_seconds", b["waits"])
        self.assertEqual(b["usage"]["unknown"], 2)


class TestDisposition(unittest.TestCase):
    """A3/A4 — the recorded disposition and its honesty guards."""

    def test_recorded_disposition(self):
        reco = _run_once()["recommendation"]
        self.assertEqual(reco["disposition"], "narrow-consolidate")
        self.assertEqual(
            reco["decision_status"], "recorded — pending owner confirmation"
        )
        self.assertEqual(reco["expansion"], "not authorized")
        self.assertEqual(
            reco["failed_targets"],
            ["cohort.preview_merge_ready", "pilot.repeat_use_2x4wk_90d"],
        )
        self.assertEqual(
            reco["inconclusive_targets"], ["cohort.intervention_reduction"]
        )
        self.assertTrue(reco["scoped_followup"])
        self.assertTrue(reco["never_authorized"])

    def test_reasons_cite_measured_numbers(self):
        reasons = " ".join(_run_once()["recommendation"]["reasons"])
        self.assertIn("16.0", reasons)
        self.assertIn("23.5", reasons)
        self.assertIn("23.0", reasons)
        self.assertIn("obs-gamma", reasons)

    def test_safety_admission_clean_on_record(self):
        safety = _run_once()["safety_admission"]
        self.assertTrue(safety["clean"])
        self.assertEqual(safety["reasons"], [])
        self.assertEqual(safety["fault_matrix_verdict"], "fault-matrix-passed")

    def test_unresolved_named_not_assumed(self):
        ids = {u["id"] for u in _run_once()["unresolved"]}
        self.assertIn("q9-adoption", ids)
        self.assertIn("cohort.thirty-day-window", ids)
        self.assertIn("pilot.external-participants", ids)
        self.assertIn("pilot.live-repeat-use", ids)
        self.assertIn("missing.c9", ids)
        self.assertIn("missing.obs-gamma", ids)


class TestUnfavorableScenarios(unittest.TestCase):
    """The issue's verification: incomplete and unfavorable evidence
    must never produce a positive claim or silent expansion."""

    def test_failed_safety_evidence_forces_stop(self):
        report = _evaluate_mutated()
        report["evidence"]["fault_matrix"]["verdict"] = "fault-matrix-failed"
        again = pe.evaluate({"evidence": report["evidence"]})
        self.assertEqual(again["recommendation"]["disposition"], "stop")
        self.assertFalse(again["safety_admission"]["clean"])
        self.assertEqual(again["recommendation"]["expansion"], "not authorized")

    def test_failed_admission_evidence_forces_stop(self):
        report = _evaluate_mutated()
        audits = report["evidence"]["external"]["audits"]
        audits["consent_ordering"]["violations"] = [{"participant": "x"}]
        again = pe.evaluate({"evidence": report["evidence"]})
        self.assertEqual(again["recommendation"]["disposition"], "stop")

    def test_all_pass_still_never_self_authorizes_continue(self):
        """Even a fully green fixture yields only
        ``continue-eligible-pending-owner`` — the continue verdict is
        the owner's act, and expansion stays unauthorized."""
        report = _evaluate_mutated()
        ev = report["evidence"]
        for r in ev["cohort"]["targets"]:
            if r["id"] == "preview_merge_ready":
                r["verdict"] = "pass"
                r["measured"] = 8
            if r["id"] == "intervention_reduction":
                r["verdict"] = "pass"
        for r in ev["external"]["targets"]:
            if r["id"] == "repeat_use_2x4wk_90d":
                r["verdict"] = "pass"
                r["measured"] = 2
        # Clear the remaining triggers honestly: no missing effort,
        # overhead repaid, no takeover, no missing weeks, the blocked
        # install resolved, nothing unresolved.
        ev["cohort"]["metrics"]["intervention_minutes"]["per_issue"]["c9"] = 12.0
        ev["cohort"]["metrics"]["intervention_minutes"]["missing_keys"] = []
        ev["cohort"]["metrics"]["intervention_minutes"]["missing_n"] = 0
        ev["cohort"]["metrics"]["overhead"]["median_total_burden_minutes"] = 15.0
        ev["cohort"]["q9_target_status"] = "accepted"
        ev["external"]["q9_target_status"] = "accepted"
        ev["cohort"]["live_prerequisites"] = []
        ev["external"]["live_prerequisites"] = []
        for p in ev["external"]["participants"]:
            p["author_takeover"] = False
            p["author_assistance"] = "none"
            p["weekly_counts"]["missing"] = 0
            p["install_outcome"] = "completed"
            p["install_blocker"] = None
        ev["external"]["targets"][1]["takeover_excluded"] = []
        again = pe.evaluate({"evidence": ev})
        reco = again["recommendation"]
        self.assertEqual(reco["disposition"], "continue-eligible-pending-owner")
        self.assertEqual(
            reco["decision_status"], "recorded — pending owner confirmation"
        )
        self.assertEqual(reco["expansion"], "not authorized")

    def test_dropped_missing_label_is_an_instrumentation_breach(self):
        """A row that stops naming a gap the source cells still carry
        is caught by the coverage audit — gaps are named, never
        absorbed."""
        report = _evaluate_mutated()
        ev = report["evidence"]
        rows = (
            pe._local_rows(ev["local"])
            + pe._cohort_rows(ev["cohort"])
            + pe._pilot_rows(ev["external"])
        )
        # Simulate a producer regression: every row stops naming
        # obs-delta's gap while the participant record still carries
        # the blocked install and missing weeks.
        for r in rows:
            r["missing"] = [m for m in r["missing"] if m["key"] != "obs-delta"]
            r["excluded"] = [
                e for e in (r.get("excluded") or []) if e["key"] != "obs-delta"
            ]
        audit = pe._audit_missing_coverage(ev, rows)
        self.assertIn("obs-delta", audit["uncovered"])
        # obs-gamma stays covered — named on the repeat-use and
        # independence rows.
        self.assertNotIn("obs-gamma", audit["uncovered"])

    def test_unaccounted_participant_fails_safety_and_denominator(self):
        """A participant the denominator audit can't account for is
        failed admission evidence — disposition stops, never expands."""
        report = _evaluate_mutated()
        ev = report["evidence"]
        ev["external"]["audits"]["denominator_integrity"]["unaccounted"] = ["obs-gamma"]
        again = pe.evaluate({"evidence": ev})
        self.assertFalse(again["safety_admission"]["clean"])
        self.assertEqual(again["recommendation"]["disposition"], "stop")

    def test_tampered_median_is_a_crosscheck_breach(self):
        report = _evaluate_mutated()
        ev = report["evidence"]
        ev["cohort"]["metrics"]["intervention_minutes"]["median"] = 5.0
        again = pe.evaluate({"evidence": ev})
        self.assertIn("cell_crosscheck", again["breaches"])
        self.assertEqual(again["verdict"], "evaluation-instrumentation-failed")

    def test_hidden_takeover_is_a_breach(self):
        report = _evaluate_mutated()
        ev = report["evidence"]
        for r in ev["external"]["targets"]:
            if r["id"] == "independent_setup_2_in_45min":
                r["takeover_excluded"] = []
        again = pe.evaluate({"evidence": ev})
        audits = again["audits"]
        self.assertIn("obs-gamma", audits["takeover_retained"]["hidden"])
        self.assertIn("takeover_retained", again["breaches"])

    def test_incomplete_only_evidence_is_insufficient_not_positive(self):
        """No failures but still-incomplete data ⇒ the evaluation may
        not emit a positive or expansion claim."""
        report = _evaluate_mutated()
        ev = report["evidence"]
        for r in ev["cohort"]["targets"]:
            if r["verdict"] == "fail":
                r["verdict"] = "pass"
                r["measured"] = r.get("threshold", r["measured"])
        for r in ev["external"]["targets"]:
            if r["verdict"] == "fail":
                r["verdict"] = "pass"
                r["measured"] = 2
        # Leave the missing effort record + takeover + unresolved in
        # place — honest incompleteness, no positive claim.
        again = pe.evaluate({"evidence": ev})
        self.assertIn(
            again["recommendation"]["disposition"],
            ("narrow-consolidate", "insufficient-evidence"),
        )
        self.assertNotEqual(
            again["recommendation"]["disposition"],
            "continue-eligible-pending-owner",
        )
        self.assertEqual(again["recommendation"]["expansion"], "not authorized")


class TestExportBoundary(unittest.TestCase):
    """The report carries aggregate-safe content only."""

    def test_report_secret_scan_clean(self):
        report = _run_once()
        self.assertEqual(report["audits"]["secret_scan"], {"outcome": "clean"})
        self.assertEqual(_export.secret_leaks(report), [])


class TestRecordedArchive(unittest.TestCase):
    """The committed archive re-evaluates identically (--fixture)."""

    def test_archived_report_reevaluates(self):
        archived = json.loads(_ARCHIVE.read_text())
        again = pe.reevaluate(archived)
        self.assertEqual(again["verdict"], "evaluation-complete")
        self.assertEqual(
            again["recommendation"]["disposition"],
            archived["recommendation"]["disposition"],
        )
        self.assertEqual(
            [r["verdict"] for r in again["targets"]["results"]],
            [r["verdict"] for r in archived["targets"]["results"]],
        )


class TestDocs(unittest.TestCase):
    """The two durable artifacts exist and carry the honest markers —
    pending-owner decision, never a silent expansion."""

    def test_evaluation_doc_markers(self):
        text = _EVAL_DOC.read_text()
        self.assertIn("pilot-evaluation-2026-10-05.json", text)
        self.assertIn("pilot_evaluation.py", text)
        self.assertIn("inconclusive", text)
        self.assertIn("What this evidence is", text)

    def test_decision_doc_markers(self):
        text = _DECISION_DOC.read_text()
        self.assertIn("pending", text.lower())
        self.assertIn("narrow", text)
        # The continue option must be gated on the owner — never
        # recorded as granted by the evaluation itself.
        self.assertIn("owner", text.lower())


class TestCli(unittest.TestCase):
    def test_cli_write_and_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "eval.json"
            code = pe.main(["--write", str(out)])
            self.assertEqual(code, 0)
            saved = json.loads(out.read_text())
            self.assertEqual(saved["verdict"], "evaluation-complete")
            code = pe.main(["--fixture", str(out)])
            self.assertEqual(code, 0)

    def test_cli_missing_source(self):
        code = pe.main(["--v1-targets", "/nonexistent-xyz.json"])
        self.assertEqual(code, 4)

    def test_cli_wrong_kind_rejected(self):
        code = pe.main(["--external", str(pe.SOURCES["dogfood"])])
        self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
