#!/usr/bin/env python3
"""§5.1 local-target measurement harness — issue #22 (task 3.8).

CI-side mechanics tests for ``tools/probes/local_targets.py``: the
seeded A1 fixture (pending tasks + retained events), each measurement
function's contract and the target-level verdict — all on reduced
sample sizes so the suite stays fast. The full-paced 1 event/second
5-minute soak is the archived probe run
(``docs/measurements/v1-targets-*.json``), not a CI step.

Run:  python3 -m unittest tests.benchmarks.test_local_targets -v
"""

from __future__ import annotations

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

import local_targets as lt

from tests.faults.harness import World


def _world_at(root, name):
    path = Path(root) / name
    path.mkdir(parents=True, exist_ok=True)
    return World(path)


class TestStats(unittest.TestCase):
    def test_stats_empty(self):
        self.assertEqual(lt._stats([]), {"n": 0})

    def test_stats_percentiles(self):
        s = lt._stats([float(i) for i in range(1, 101)])
        self.assertEqual(s["n"], 100)
        self.assertEqual(s["min_s"], 1.0)
        self.assertEqual(s["max_s"], 100.0)
        self.assertAlmostEqual(s["p50_s"], 50.5, places=3)
        self.assertGreaterEqual(s["p95_s"], 94.0)
        self.assertLessEqual(s["p95_s"], 96.0)
        self.assertGreater(s["mean_s"], 0)

    def test_stats_single(self):
        s = lt._stats([0.5])
        self.assertEqual(s["p95_s"], 0.5)
        self.assertEqual(s["stdev_s"], 0.0)


class TestSeedFixture(unittest.TestCase):
    """The A1 fixture: registered repo, pending tasks, retained events."""

    def test_seed_load_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            fixture = lt.seed_load(world, pending=10, events=500)
            self.assertEqual(fixture["pending_tasks"], 10)
            self.assertEqual(fixture["total_events"], 500)
            self.assertGreater(fixture["db_bytes"], 0)
            states = {w["state"] for w in world.store.work_rows()}
            self.assertEqual(states, {"pending"})
            for wk in fixture["pending_work_keys"]:
                self.assertIsNotNone(world.store.queue_entry(wk))

    def test_seed_uses_real_intake_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            fixture = lt.seed_load(world, pending=5, events=50)
            for wk in fixture["pending_work_keys"]:
                work = world.store.get_work(wk)
                self.assertEqual(work["state"], "pending")
                self.assertEqual(work["task_state"], "bound")


class TestMeasurements(unittest.TestCase):
    """Each measurement function on reduced samples — real calls, real
    numbers, same bounds the full run asserts."""

    def test_intake_p95(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            lt.seed_load(world, pending=10, events=500)
            m = lt.m_intake(world, deliveries=20, rate_hz=0)
            self.assertEqual(m["deliveries"], 20)
            self.assertEqual(m["outcomes"].get("accepted"), 20)
            self.assertLessEqual(m["stats"]["p95_s"], lt.INTAKE_P95_S)

    def test_duplicate_burst_with_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            lt.seed_load(world, pending=5, events=50)
            m = lt.m_dup_burst(world, n=20, restart=True)
            self.assertEqual(m["work_rows"], 1)
            self.assertLessEqual(m["remote_prs"], 1)
            self.assertLessEqual(m["burst_wall_s"], lt.DUPLICATE_BURST_WINDOW_S)

    def test_status_p95_and_remote_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            fixture = lt.seed_load(world, pending=5, events=50)
            m = lt.m_status(world, fixture["pending_work_keys"][0], queries=20)
            self.assertEqual(m["queries"], 20)
            self.assertLessEqual(m["stats"]["p95_s"], lt.STATUS_P95_S)
            self.assertTrue(m["remote_block_present"])

    def test_control_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            lt.seed_load(world, pending=30, events=50)
            m = lt.m_control(world, issues=list(range(1, 10)))
            self.assertEqual(m["commands"], 9)
            self.assertLessEqual(m["stats"]["p95_s"], lt.CONTROL_PERSIST_S)
            self.assertTrue(all(o == "accepted" for o in m["outcomes"]))
            self.assertEqual(len(m["record_commit_deltas_s"]), 9)

    def test_cancel_confirmed_within_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            m = lt.m_cancel(world, uncertain=False)
            self.assertEqual(m["termination"], "confirmed")
            self.assertEqual(m["work_state"], "canceled")
            self.assertLessEqual(m["handler_wall_s"], lt.CANCEL_WINDOW_S)
            self.assertTrue(m["replacement_eligible"])

    def test_cancel_uncertain_quarantines(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            m = lt.m_cancel(world, uncertain=True)
            self.assertEqual(m["termination"], "quarantined")
            self.assertEqual(m["work_state"], "quarantined")
            self.assertTrue(m["local_notification_durable"])
            self.assertGreaterEqual(m["uncertain_alert_rows"], 1)
            self.assertFalse(m["replacement_eligible"])
            self.assertEqual(m["retry_after_uncertain"], "termination-uncertain")

    def test_reconcile_discovers_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            m = lt.m_reconcile(world, dropped_issue=77)
            self.assertFalse(m["observed_on_failed_pass"])
            self.assertLessEqual(m["interval_s"], lt.RECONCILE_INTERVAL_S)
            self.assertLessEqual(m["clamped_interval_s"], lt.RECONCILE_INTERVAL_S)
            self.assertLessEqual(m["self_backoff_s"], lt.DISCOVERY_BOUND_S)
            self.assertEqual(len(m["accepted"]), 1)
            self.assertIsNotNone(m["discovery_latency_s"])
            self.assertLessEqual(m["discovery_latency_s"], lt.DISCOVERY_BOUND_S)

    def test_recovery_settles_known_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            m = lt.m_recovery(world)
            self.assertTrue(m["within_deadline"])
            self.assertLessEqual(m["wall_s"], lt.RECOVERY_DEADLINE_S)
            self.assertEqual(m["settled"], m["expected"])
            self.assertEqual(m["unsettled"], [])
            self.assertEqual(m["errors"], [])

    def test_idle_audit_zero_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            m = lt.m_idle(world)
            self.assertEqual(m["model_calls"], 0)
            self.assertTrue(m["audit"]["idle"])
            self.assertEqual(m["audit"]["attempt_starts"], 0)
            self.assertEqual(m["audit"]["model_calls"], 0)
            self.assertEqual(m["window_s"], lt.IDLE_WINDOW_S)

    def test_observability_mechanisms(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = _world_at(tmp, "w")
            lt.m_reconcile(world, dropped_issue=88)
            m = lt.m_observability(world)
            failed = [c["name"] for c in m["checks"] if not c["pass"]]
            self.assertEqual(failed, [])
            self.assertEqual(m["status"]["remote_state"], "fresh")

    def test_fault_evidence_zero_loss(self):
        m = lt.m_fault_evidence()
        self.assertEqual(m["verdict"], "fault-matrix-passed")
        self.assertEqual(m["lost_acknowledged_tasks"], 0)
        self.assertEqual(m["passed_repetitions"], m["repetitions"])


class TestRunAndEvaluate(unittest.TestCase):
    """The assembled report: verdict, fixture and the Q9 defaults
    table — on --quick sizing so the suite stays fast."""

    def test_quick_run_passes(self):
        report = lt.run(quick=True)
        self.assertEqual(report["verdict"], "targets-passed")
        self.assertEqual(report["failed_targets"], [])
        self.assertEqual(
            [c["name"] for c in report["targets"]["results"]],
            report["targets"]["order"],
        )
        self.assertIn("environment", report)
        self.assertEqual(
            report["environment"]["network_latency_note"] is not None, True
        )

    def test_fixture_scale_recorded(self):
        report = lt.run(quick=True)
        self.assertEqual(report["fixture"]["pending_tasks"], lt.SEED_PENDING)
        self.assertEqual(report["fixture"]["total_events"], lt.SEED_EVENTS)

    def test_q9_defaults_table(self):
        d = lt.q9_defaults()
        self.assertEqual(d["limits"]["active_worker_minutes"], 60)
        self.assertEqual(d["limits"]["wall_hours"], 24)
        self.assertEqual(d["limits"]["implementation_attempts"], 2)
        self.assertEqual(d["evidence"]["worker_log_retention_days"], 7)
        self.assertEqual(d["evidence"]["audit_retention_days"], 30)
        self.assertEqual(d["endpoint"]["approval_expiry_minutes"], 60)
        self.assertEqual(d["endpoint"]["max_smoke_age_minutes"], 10)
        self.assertEqual(d["preview"]["ttl_hours"], 24)
        self.assertEqual(d["preview"]["cleanup_minutes"], 60)
        self.assertEqual(d["recovery"]["backoff_cap_s"], 300.0)
        self.assertEqual(d["recovery"]["nominal_interval_s"], 60.0)

    def test_reevaluate_fixture(self):
        report = lt.run(quick=True)
        again = lt.reevaluate(report)
        self.assertEqual(again["verdict"], report["verdict"])

    def test_failed_target_detected(self):
        report = lt.run(quick=True)
        report["measurements"]["intake"]["stats"]["p95_s"] = 99.0
        again = lt.reevaluate(report)
        self.assertEqual(again["verdict"], "targets-failed")
        self.assertIn("intake_p95", again["failed_targets"])

    def test_evaluate_binds_real_wall_quantities(self):
        """The real perf_counter quantities are bound-checked, not only
        the service's own (fixture-clock) verdicts: an over-bound wall
        or discovery latency must fail the target."""
        report = lt.run(quick=True)
        report["measurements"]["recovery"]["wall_s"] = 999.0
        again = lt.reevaluate(report)
        self.assertEqual(again["verdict"], "targets-failed")
        self.assertIn("restart_recovery", again["failed_targets"])

        report = lt.run(quick=True)
        report["measurements"]["reconcile"]["discovery_latency_s"] = 999.0
        again = lt.reevaluate(report)
        self.assertEqual(again["verdict"], "targets-failed")
        self.assertIn("reconcile_discovery", again["failed_targets"])

    def test_cli_quick(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.json"
            code = lt.main(["--quick", "--write", str(out)])
            self.assertEqual(code, 0)
            saved = __import__("json").loads(out.read_text())
            self.assertEqual(saved["verdict"], "targets-passed")


if __name__ == "__main__":
    unittest.main()
