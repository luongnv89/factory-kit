#!/usr/bin/env python3
"""§1.4/§8.3 sequential dogfood cohort harness — issue #27 (task 4.3).

CI-side mechanics tests for ``tools/probes/dogfood_cohort.py``: the
scripted cohort replay across two sequential project registrations, the
§1.4 metric computations (medians, denominators, wait-class separation,
known-vs-unknown usage), the measurement-contract audits and the
secret-free reproducible export — all on the packaged
``tests/fixtures/cohort/`` fixtures through the real services.

Run:  python3 -m unittest tests.benchmarks.test_dogfood_cohort -v
"""

from __future__ import annotations

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

import dogfood_cohort as dc

from factory_kit.privacy import export as _export
from tests.faults.harness import World

#: Canary convention — assembled, never a literal secret in source.
CANARY = "ghp_" + "b2" * 20

_RUN = None


def _run_once():
    """The full scripted cohort is fast (scripted ports, fake clocks) —
    run it once per test process and reuse the report."""
    global _RUN
    if _RUN is None:
        _RUN = dc.run()
    return _RUN


class TestCohortRun(unittest.TestCase):
    """The scripted run over the packaged fixtures."""

    def test_run_completes_clean(self):
        report = _run_once()
        self.assertEqual(report["verdict"], "cohort-run-complete")
        self.assertEqual(report["breaches"], [])
        self.assertEqual(report["instrumentation"], "clean")
        self.assertEqual(report["mode"], "scripted-replay")

    def test_denominator_integrity(self):
        report = _run_once()
        den = report["audits"]["denominator_integrity"]
        self.assertEqual(den["cohort_size"], 10)
        self.assertEqual(den["accounted"], 10)
        self.assertEqual(den["unaccounted"], [])
        # Every outcome class retained — blocked/failed/canceled stay
        # in the denominator and the decline is separately recorded.
        self.assertEqual(
            den["outcomes"],
            {
                "merged": 4,
                "merge_ready": 2,
                "declined": 1,
                "failed": 1,
                "canceled": 1,
                "blocked": 1,
            },
        )
        self.assertEqual(den["human_declines"], ["c4"])

    def test_two_sequential_registrations(self):
        report = _run_once()
        seq = report["audits"]["sequential_registrations"]
        self.assertTrue(seq["sequential"])
        self.assertEqual(seq["order"], ["project-a", "project-b"])
        repos = {p["repo_id"] for p in report["projects"]}
        self.assertEqual(len(repos), 2)
        for p in report["projects"]:
            self.assertTrue(p["registered"])
            self.assertEqual(p["lingering_active"], [])

    def test_one_active_task_audit(self):
        report = _run_once()
        audit = report["audits"]["one_active_task"]
        self.assertEqual(audit["violations"], [])
        self.assertGreaterEqual(audit["samples"], 20)

    def test_revision_evidence_audit(self):
        report = _run_once()
        audit = report["audits"]["revision_evidence"]
        self.assertEqual(audit["violations"], [])
        # 7 endpoint-path issues keep verified head-sha evidence that
        # matches the remote; the other 3 carry explicit terminal
        # states. Nothing is unaccountable.
        self.assertEqual(len(audit["verified_revision"]), 7)
        self.assertEqual(len(audit["explicit_terminal"]), 3)


class TestMetrics(unittest.TestCase):
    """§1.4 metric computation against recorded source rows."""

    def test_intervention_median_and_missing(self):
        m = _run_once()["metrics"]["intervention_minutes"]
        # c1..c10 known sums: 13,25,9,21,6,16,20,10,(missing),17
        self.assertEqual(m["median"], 16.0)
        self.assertEqual(m["known_n"], 9)
        self.assertEqual(m["missing_n"], 1)
        self.assertEqual(m["missing_keys"], ["c9"])
        self.assertIsNone(m["per_issue"]["c9"])
        self.assertEqual(m["successful_n"], 6)
        self.assertEqual(m["unsuccessful_n"], 4)

    def test_baseline_and_reduction(self):
        metrics = _run_once()["metrics"]
        # baseline minutes: 22,35,27,14,18,41,19,24,31,16 -> median 23
        self.assertEqual(metrics["baseline"]["median"], 23.0)
        self.assertEqual(metrics["baseline"]["n"], 10)
        self.assertEqual(metrics["intervention_reduction_pct"], 30.43)

    def test_overhead_and_total_burden(self):
        oh = _run_once()["metrics"]["overhead"]
        self.assertEqual(oh["setup_minutes"], {"project-a": 25.0, "project-b": 20.0})
        self.assertEqual(oh["maintenance_minutes"], 30.0)
        self.assertEqual(oh["total_overhead_minutes"], 75.0)
        self.assertEqual(oh["amortized_per_issue_minutes"], 7.5)
        # Total burden = median intervention + amortized overhead.
        self.assertEqual(oh["median_total_burden_minutes"], 23.5)

    def test_wait_classes_separated(self):
        waits = _run_once()["metrics"]["waits"]
        for field in (
            "queue_seconds",
            "model_execution_seconds",
            "ci_wait_seconds",
            "human_wait_seconds",
        ):
            self.assertIn(field, waits)
        self.assertGreater(waits["queue_seconds"]["measured_s"], 0)
        self.assertGreater(waits["model_execution_seconds"]["measured_s"], 0)
        # c10's two unknown-usage attempts count as unknown, never
        # zero-filled.
        self.assertEqual(waits["queue_seconds"]["unknown"], 2)
        self.assertEqual(waits["ci_wait_seconds"]["unknown"], 2)

    def test_usage_known_vs_unknown(self):
        usage = _run_once()["metrics"]["usage"]
        self.assertEqual(usage["attempts"], 18)
        self.assertEqual(usage["unknown"], 2)
        # tokens/billed/quota stay separately fielded; c7 billed=None
        # is a known-usage row with an unknown billed field.
        self.assertEqual(usage["known_tokens"], 16)
        self.assertEqual(usage["known_billed"], 15)
        self.assertEqual(usage["known_quota"], 16)


class TestTargets(unittest.TestCase):
    """The proposed §1.4 targets reported exactly — pass, fail and
    inconclusive are all legitimate study outcomes."""

    def test_target_verdicts(self):
        results = {r["id"]: r for r in _run_once()["targets"]["results"]}
        self.assertEqual(results["preview_merge_ready"]["verdict"], "fail")
        self.assertEqual(results["preview_merge_ready"]["measured"], 7)
        self.assertEqual(results["human_approved_merged_30d"]["verdict"], "pass")
        self.assertEqual(results["human_approved_merged_30d"]["measured"], 4)
        # One missing effort record makes the burden comparison
        # honestly inconclusive — the gap is reported, not excluded.
        self.assertEqual(results["intervention_reduction"]["verdict"], "inconclusive")
        self.assertEqual(results["intervention_reduction"]["measured"], 30.43)
        for r in results.values():
            self.assertEqual(r["basis"], "scripted-cohort-fixture")
            self.assertEqual(r["live_status"], "pending-live-cohort")

    def test_live_prerequisites_named(self):
        prereqs = _run_once()["live_prerequisites"]
        ids = {p["id"] for p in prereqs}
        self.assertEqual(
            ids,
            {
                "q9-adoption",
                "second-project-registration",
                "thirty-day-window",
                "human-decisions",
                "baseline-collection",
            },
        )

    def test_reevaluate_matches(self):
        report = _run_once()
        again = dc.reevaluate(json.loads(json.dumps(report)))
        self.assertEqual(again["verdict"], report["verdict"])
        self.assertEqual(
            [r["verdict"] for r in again["targets"]["results"]],
            [r["verdict"] for r in report["targets"]["results"]],
        )

    def test_failed_merged_target_detected(self):
        report = json.loads(json.dumps(_run_once()))
        for rec in report["issues"]:
            rec["merged_at"] = None
        again = dc.reevaluate(report)
        results = {r["id"]: r["verdict"] for r in again["targets"]["results"]}
        self.assertEqual(results["human_approved_merged_30d"], "fail")

    def test_noncomparable_baseline_inconclusive(self):
        baseline = json.loads((dc.FIXTURES / "baseline.json").read_text())
        baseline["issues"] = baseline["issues"][:7]  # n != 10
        comp = dc._comparability(baseline)
        self.assertFalse(comp["comparable"])
        report = json.loads(json.dumps(_run_once()))
        report["baseline_comparability"] = comp
        again = dc.reevaluate(report)
        results = {r["id"]: r["verdict"] for r in again["targets"]["results"]}
        self.assertEqual(results["intervention_reduction"], "inconclusive")


class TestExportBoundary(unittest.TestCase):
    """A5's export: reproducible inputs, no secret or identifying
    content — the canary self-check."""

    def test_report_secret_scan_clean(self):
        report = _run_once()
        self.assertEqual(report["audits"]["secret_scan"], {"outcome": "clean"})
        self.assertEqual(_export.secret_leaks(report), [])
        for slot in ("project-a", "project-b"):
            self.assertIn(slot, report["export"])
            self.assertIn("tables", report["export"][slot])

    def test_export_scrubs_canary(self):
        """A secret-shaped value planted on the durable trail must
        surface as ``<redacted>`` in the guarded export — and the
        export's own scan reports clean."""
        with tempfile.TemporaryDirectory() as tmp:
            wroot = Path(tmp) / "w"
            wroot.mkdir(parents=True)
            world = World(wroot)
            wk = world.accept(99)
            world.store.record_operator_effort(
                wk,
                actor_ref=f"telegram:{CANARY}",
                active_minutes=3.0,
                category="monitoring",
                source="canary-test",
            )
            payload = _export.history_export(world.store, f"gh:{world.repo_id}")
            blob = json.dumps(payload)
            self.assertNotIn(CANARY, blob)
            self.assertIn("<redacted>", blob)
            self.assertEqual(_export.secret_leaks(payload), [])
            self.assertEqual(payload["secret_scan"], "clean")


class TestCli(unittest.TestCase):
    def test_cli_write_and_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cohort.json"
            code = dc.main(["--write", str(out)])
            self.assertEqual(code, 0)
            saved = json.loads(out.read_text())
            self.assertEqual(saved["verdict"], "cohort-run-complete")
            code = dc.main(["--fixture", str(out)])
            self.assertEqual(code, 0)

    def test_cli_missing_fixture_dir(self):
        code = dc.main(["--fixtures", "/nonexistent-dir-xyz"])
        self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
