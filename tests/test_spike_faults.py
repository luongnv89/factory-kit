#!/usr/bin/env python3
"""Unit coverage for tools/probes/spike_faults.py (issue #5 / Task 1.4).

One test class per fault leg. Every test builds the deterministic fixture
and exercises the same store/remote/fence objects the probe drives — the
probe's own leg assertions are checked end-to-end by the report-level
tests at the bottom.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools" / "probes" / "spike_faults.py"

spec = importlib.util.spec_from_file_location("spike_faults", MODULE)
sf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf)

endpoint_spec = importlib.util.spec_from_file_location(
    "endpoint_walkthrough",
    ROOT / "tools" / "probes" / "endpoint_walkthrough.py")
ep = importlib.util.module_from_spec(endpoint_spec)
endpoint_spec.loader.exec_module(ep)


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fk-faults-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fx = sf.build_fixture(self.root)
        self.store = self.fx["store"]
        self.remote = self.fx["remote"]
        self.fence = self.fx["fence"]
        self.key = self.fx["signing_key"]

    def sign(self, delivery, repo, issue):
        import hashlib
        import hmac
        return hmac.new(self.key.encode(),
                        f"{delivery}|{repo}|{issue}".encode(),
                        hashlib.sha256).hexdigest()

    def event(self, delivery, repo=sf.REGISTERED_REPO,
              issue=sf.ISSUE_NUMBER, sign=True):
        ev = {"delivery_id": delivery, "repository": repo, "issue": issue}
        ev["signature"] = (self.sign(delivery, repo, issue) if sign
                           else "0" * 64)
        return ev


# ---------------------------------------------------------------------------
# A1 — duplicate delivery / restart / convergence
# ---------------------------------------------------------------------------

class DuplicateDeliveryTests(FixtureCase):
    def test_first_delivery_accepted(self):
        r = self.store.deliver(self.event("d-1"), "webhook", self.key,
                               sf.REGISTERED_REPO)
        self.assertEqual(r["outcome"], "accepted")
        self.assertTrue(r["work_id"].startswith("wk-"))

    def test_redelivery_deduplicated(self):
        ev = self.event("d-1")
        r1 = self.store.deliver(dict(ev), "webhook", self.key,
                                sf.REGISTERED_REPO)
        r2 = self.store.deliver(dict(ev), "webhook", self.key,
                                sf.REGISTERED_REPO)
        self.assertEqual(r2["outcome"], "deduplicated")
        self.assertEqual(r2["work_id"], r1["work_id"])

    def test_reconciliation_overlap_converges(self):
        r1 = self.store.deliver(self.event("d-1"), "webhook", self.key,
                                sf.REGISTERED_REPO)
        r2 = self.store.deliver(self.event("d-2"), "reconciliation",
                                self.key, sf.REGISTERED_REPO)
        self.assertEqual(r2["outcome"], "reconciled")
        self.assertEqual(r2["work_id"], r1["work_id"])
        rows = self.store.db.execute(
            "SELECT COUNT(*) FROM work WHERE repo=? AND issue=?",
            (sf.REGISTERED_REPO, sf.ISSUE_NUMBER)).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_one_active_attempt_invariant(self):
        r1 = self.store.deliver(self.event("d-1"), "webhook", self.key,
                                sf.REGISTERED_REPO)
        a1 = self.store.begin_attempt(r1["work_id"], "implementation")
        a2 = self.store.begin_attempt(r1["work_id"], "implementation")
        self.assertEqual(a1["outcome"], "active")
        self.assertEqual(a2["outcome"], "denied")
        self.assertEqual(a2["reason"], "attempt-active")
        self.assertEqual(a2["active"], a1["attempt_id"])

    def test_denied_delivery_leaves_no_delivery_row(self):
        r = self.store.deliver(self.event("d-bad", sign=False), "webhook",
                               self.key, sf.REGISTERED_REPO)
        self.assertEqual(r["outcome"], "denied")
        self.assertIsNone(r["work_id"] if "work_id" in r else None)
        rows = self.store.db.execute(
            "SELECT COUNT(*) FROM deliveries").fetchone()[0]
        self.assertEqual(rows, 0)


class RestartRecoveryTests(FixtureCase):
    def test_redelivery_after_reopen_deduplicates(self):
        ev = self.event("d-1")
        self.store.deliver(dict(ev), "webhook", self.key,
                           sf.REGISTERED_REPO)
        self.store.db.close()
        self.store = sf.FaultStore(self.root / "endpoint.db")
        r = self.store.deliver(dict(ev), "webhook", self.key,
                               sf.REGISTERED_REPO)
        self.assertEqual(r["outcome"], "deduplicated")

    def test_new_delivery_after_reopen_converges(self):
        r1 = self.store.deliver(self.event("d-1"), "webhook", self.key,
                                sf.REGISTERED_REPO)
        self.store.db.close()
        self.store = sf.FaultStore(self.root / "endpoint.db")
        r2 = self.store.deliver(self.event("d-2"), "reconciliation",
                                self.key, sf.REGISTERED_REPO)
        self.assertEqual(r2["outcome"], "reconciled")
        self.assertEqual(r2["work_id"], r1["work_id"])

    def test_durable_rows_survive_reopen(self):
        self.store.deliver(self.event("d-1"), "webhook", self.key,
                           sf.REGISTERED_REPO)
        before = self.store.db.execute(
            "SELECT COUNT(*) FROM deliveries").fetchone()[0]
        self.store.db.close()
        store2 = sf.FaultStore(self.root / "endpoint.db")
        after = store2.db.execute(
            "SELECT COUNT(*) FROM deliveries").fetchone()[0]
        self.assertEqual(before, after)
        store2.db.close()


class PrCrashReconcileTests(FixtureCase):
    def _work(self):
        return self.store.deliver(self.event("d-1"), "webhook", self.key,
                                  sf.REGISTERED_REPO)["work_id"]

    def test_dispatched_then_reconciled(self):
        work = self._work()
        head = "a" * 40
        self.store.record_remote_op("op-1", work, "pr-publish", head)
        self.remote.open_pr(7, head, "main", "t",
                            ctx={"op_id": "op-1"})
        rec = self.store.recover_publication("op-1", self.remote)
        self.assertEqual(rec["outcome"], "reconciled")
        self.assertEqual(rec["pr_number"], 7)
        self.assertFalse(rec["republished"])

    def test_unreadable_remote_parks(self):
        work = self._work()
        self.store.record_remote_op("op-1", work, "pr-publish", "a" * 40)
        state = self.remote.state_path
        saved = state.read_bytes()
        import os
        os.chmod(state, 0o600)
        state.write_bytes(b"{corrupt")
        rec = self.store.recover_publication("op-1", self.remote)
        state.write_bytes(saved)
        os.chmod(state, 0o400)
        self.assertEqual(rec["outcome"], "parked")
        self.assertFalse(rec["republished"])

    def test_confirmed_absent_does_not_republish(self):
        work = self._work()
        self.store.record_remote_op("op-1", work, "pr-publish", "a" * 40)
        rec = self.store.recover_publication("op-1", self.remote)
        self.assertEqual(rec["outcome"], "absent")
        self.assertFalse(rec["republished"])
        self.assertEqual(len(self.remote.snapshot()["pulls"]), 0)

    def test_parked_is_reevaluable(self):
        work = self._work()
        head = "b" * 40
        self.store.record_remote_op("op-1", work, "pr-publish", head)
        self.remote.open_pr(9, head, "main", "t", ctx={"op_id": "op-1"})
        state = self.remote.state_path
        saved = state.read_bytes()
        import os
        os.chmod(state, 0o600)
        state.write_bytes(b"{corrupt")
        self.store.recover_publication("op-1", self.remote)
        state.write_bytes(saved)
        os.chmod(state, 0o400)
        rec = self.store.recover_publication("op-1", self.remote)
        self.assertEqual(rec["outcome"], "reconciled")
        self.assertEqual(rec["pr_number"], 9)

    def test_recover_unknown_op(self):
        rec = self.store.recover_publication("op-none", self.remote)
        self.assertEqual(rec["outcome"], "unknown-op")


# ---------------------------------------------------------------------------
# A2 — cancel / expired-claim / dead-worker
# ---------------------------------------------------------------------------

class CancelLateResultTests(FixtureCase):
    def test_cancel_fences_generation(self):
        out = self.fence.accept_control(
            {"action": "cancel", "actor": "77001", "chat": "ops",
             "target_generation": 1})
        self.assertTrue(out["accepted"])
        gen = self.fence.current_generation(sf.REGISTERED_REPO,
                                            sf.ISSUE_NUMBER)
        self.assertEqual(gen[1], "fenced")

    def test_late_result_denied(self):
        self.fence.accept_control(
            {"action": "cancel", "actor": "77001", "chat": "ops",
             "target_generation": 1})
        late = self.fence.submit_intent(
            {"op_id": "r-1", "repository": sf.REGISTERED_REPO, "issue": 0,
             "generation": 1, "actor": "worker-profile",
             "op": sf.fenced.RESULT_OP},
            lambda req: {"recorded": True}, lambda s: s)
        self.assertEqual(late["outcome"], "denied")
        self.assertEqual(late["reason"], "generation-fenced")

    def test_replacement_blocked_until_confirmed(self):
        self.fence.accept_control(
            {"action": "cancel", "actor": "77001", "chat": "ops",
             "target_generation": 1})
        blocked = self.fence.begin_replacement(sf.REGISTERED_REPO,
                                               sf.ISSUE_NUMBER)
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "termination-uncertain")
        self.fence.record_termination(1, "confirmed")
        ok = self.fence.begin_replacement(sf.REGISTERED_REPO,
                                          sf.ISSUE_NUMBER)
        self.assertTrue(ok["allowed"])
        self.assertEqual(ok["generation"], 2)

    def test_old_generation_superseded(self):
        self.fence.accept_control(
            {"action": "cancel", "actor": "77001", "chat": "ops",
             "target_generation": 1})
        self.fence.record_termination(1, "confirmed")
        self.fence.begin_replacement(sf.REGISTERED_REPO, sf.ISSUE_NUMBER)
        old = self.fence.submit_intent(
            {"op_id": "e-1", "repository": sf.REGISTERED_REPO, "issue": 0,
             "generation": 1, "actor": "worker-profile",
             "op": "branch-publish",
             "payload": {"branch": "x", "sha": "0" * 40}},
            lambda req: None, lambda s: s)
        self.assertEqual(old["outcome"], "denied")
        self.assertEqual(old["reason"], "superseded-generation")


class DeadWorkerTests(FixtureCase):
    def test_expired_claim_rejects_result(self):
        work = self.store.deliver(self.event("d-1"), "webhook", self.key,
                                  sf.REGISTERED_REPO)["work_id"]
        a = self.store.begin_attempt(work, "implementation")
        expired = self.store.expire_attempts(
            time.time() + sf.CLAIM_TTL_S + 1)
        self.assertIn(a["attempt_id"], expired)
        r = self.store.submit_result("res-1", a["attempt_id"])
        self.assertEqual(r["outcome"], "denied")
        self.assertEqual(r["reason"], "attempt-expired")

    def test_fresh_claim_after_expiry(self):
        work = self.store.deliver(self.event("d-1"), "webhook", self.key,
                                  sf.REGISTERED_REPO)["work_id"]
        dead = self.store.begin_attempt(work, "implementation")
        self.store.expire_attempts(time.time() + sf.CLAIM_TTL_S + 1)
        fresh = self.store.begin_attempt(work, "implementation")
        self.assertEqual(fresh["outcome"], "active")
        r = self.store.submit_result("res-2", fresh["attempt_id"])
        self.assertEqual(r["outcome"], "accepted")

    def test_quarantined_termination_blocks_replacement(self):
        self.fence.accept_control(
            {"action": "cancel", "actor": "77001", "chat": "ops",
             "target_generation": 1})
        self.fence.record_termination(1, "quarantined")
        repl = self.fence.begin_replacement(sf.REGISTERED_REPO,
                                            sf.ISSUE_NUMBER)
        self.assertFalse(repl["allowed"])
        self.assertTrue(repl.get("quarantined"))

    def test_result_dedup(self):
        work = self.store.deliver(self.event("d-1"), "webhook", self.key,
                                  sf.REGISTERED_REPO)["work_id"]
        a = self.store.begin_attempt(work, "implementation")
        r1 = self.store.submit_result("res-1", a["attempt_id"])
        r2 = self.store.submit_result("res-1", a["attempt_id"])
        self.assertEqual(r1["outcome"], "accepted")
        self.assertEqual(r2["outcome"], "deduplicated")


# ---------------------------------------------------------------------------
# A3 — approval persistence / single-use / drift / merge uncertainty
# ---------------------------------------------------------------------------

class ApprovalRestartTests(FixtureCase):
    def _request(self):
        return self.store.request_approval(
            "77001", f"{sf.REGISTERED_REPO}#7", "rev", "ev", "pol")

    def test_request_survives_reopen(self):
        req = self._request()
        self.store.db.close()
        store2 = sf.FaultStore(self.root / "endpoint.db")
        row = store2.db.execute(
            "SELECT actor, state, revision_digest FROM approvals "
            "WHERE request_id=?", (req["request_id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "77001")
        self.assertEqual(row[1], "awaiting")
        self.assertEqual(row[2], "rev")
        store2.db.close()


class ApprovalSingleUseTests(FixtureCase):
    def _request(self):
        return self.store.request_approval(
            "77001", f"{sf.REGISTERED_REPO}#7", "rev", "ev", "pol")

    def test_replay_denied(self):
        req = self._request()
        first = self.store.approve(req["request_id"], "77001",
                                   "h" * 40, ["77001"])
        replay = self.store.approve(req["request_id"], "77001",
                                    "h" * 40, ["77001"])
        self.assertEqual(first["outcome"], "consumed")
        self.assertEqual(replay["outcome"], "denied")
        self.assertEqual(replay["reason"], "replayed")
        intents = self.store.db.execute(
            "SELECT COUNT(*) FROM merge_intents WHERE request_id=?",
            (req["request_id"],)).fetchone()[0]
        self.assertEqual(intents, 1)

    def test_expired_request_denied(self):
        req = self.store.request_approval(
            "77001", f"{sf.REGISTERED_REPO}#7", "rev", "ev", "pol",
            created=time.time() - ep.APPROVAL_EXPIRY_S - 60)
        out = self.store.approve(req["request_id"], "77001",
                                 "h" * 40, ["77001"])
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "expired")

    def test_wrong_actor_denied(self):
        req = self._request()
        out = self.store.approve(req["request_id"], "88002",
                                 "h" * 40, ["77001", "88002"])
        self.assertEqual(out["outcome"], "denied")


class ApprovalDriftTests(FixtureCase):
    def _guard(self, head, **patch):
        state = {"task_state": "active", "actor_authorized": True,
                 "base_protected": True, "strict_up_to_date": True,
                 "pr_open": True, "pr_draft": False, "pr_head": head,
                 "expected_head": head, "pr_base": "main",
                 "expected_base": "main", "checks_green": True,
                 "mergeable": True, "preview_fresh_healthy": True,
                 "approval": {"state": "consumed",
                              "expiry": time.time() + 3600}}
        state.update(patch)
        return ep.evaluate_merge_guard(state)

    def test_in_contract_passes(self):
        self.assertTrue(self._guard("h" * 40)["pass"])

    def test_head_change_denies(self):
        v = self._guard("h" * 40, pr_head="x" * 40)
        self.assertFalse(v["pass"])
        self.assertIn("head-changed", v["reasons"])

    def test_base_change_denies(self):
        v = self._guard("h" * 40, pr_base="moved")
        self.assertFalse(v["pass"])
        self.assertIn("base-changed", v["reasons"])

    def test_check_drift_denies(self):
        v = self._guard("h" * 40, checks_green=False)
        self.assertFalse(v["pass"])
        self.assertIn("required-checks-not-green", v["reasons"])

    def test_stale_preview_denies(self):
        v = self._guard("h" * 40, preview_fresh_healthy=False)
        self.assertFalse(v["pass"])
        self.assertIn("stale-preview", v["reasons"])

    def test_policy_invalidated_approval_denied(self):
        req = self.store.request_approval(
            "77001", f"{sf.REGISTERED_REPO}#7", "rev", "ev", "pol")
        self.store.invalidate_approval(req["request_id"],
                                       "policy-changed")
        out = self.store.approve(req["request_id"], "77001",
                                 "h" * 40, ["77001"])
        self.assertEqual(out["outcome"], "denied")
        self.assertTrue(out["reason"].startswith("state-"))
        v = self._guard("h" * 40, approval={
            "state": "invalidated", "expiry": time.time() + 3600})
        self.assertFalse(v["pass"])
        self.assertIn("approval-invalidated", v["reasons"])


class MergeUncertaintyTests(FixtureCase):
    def test_lost_response_reconciles(self):
        head = "c" * 40
        self.remote.open_pr(7, head, "main", "t", ctx={"op_id": "m-1"})
        self.remote.invoke_merge(7, head, ctx={"op_id": "m-1",
                                               "actor": "merge-owner"})
        snap = self.remote.snapshot()
        self.assertEqual(snap["pulls"]["7"]["state"], "merged")
        self.assertEqual(snap["merges"][-1]["head"], head)

    def test_unreadable_remote_raises(self):
        state = self.remote.state_path
        saved = state.read_bytes()
        import os
        os.chmod(state, 0o600)
        state.write_bytes(b"{corrupt")
        with self.assertRaises(Exception):
            self.remote.snapshot()
        state.write_bytes(saved)
        os.chmod(state, 0o400)


# ---------------------------------------------------------------------------
# A4 — gate ledger + report shape
# ---------------------------------------------------------------------------

class GateLedgerTests(FixtureCase):
    def test_eight_gates_recorded(self):
        run = sf.FaultRun(self.fx)
        report = run.run()
        self.assertEqual(len(report["gate_ledger"]), 8)
        names = [g["gate"] for g in report["gate_ledger"]]
        self.assertEqual(names, [f"GATE-S{n:02d}" for n in range(1, 9)])

    def test_every_gate_has_required_fields(self):
        run = sf.FaultRun(self.fx)
        report = run.run()
        for gate in report["gate_ledger"]:
            for field in ("gate", "item", "owner_task", "status",
                          "supported_interface", "versions",
                          "fixture_identity", "evidence",
                          "evidence_present", "reproduce"):
                self.assertIn(field, gate, f"{gate['gate']} missing {field}")

    def test_prior_evidence_files_exist(self):
        for gate, info in sf.GATE_EVIDENCE.items():
            if gate in ("GATE-S05", "GATE-S08"):
                continue  # includes this run's own artifact
            for path in info["evidence"]:
                self.assertTrue((sf._REPO_ROOT / path).is_file(),
                                f"{gate} evidence missing: {path}")


class ReportTests(FixtureCase):
    def test_full_run_verdict(self):
        report = sf.FaultRun(self.fx).run()
        self.assertEqual(report["verdict"], "fault-suite-passed")
        self.assertEqual(report["schema"], "factory-kit/spike-faults@1")

    def test_all_legs_pass(self):
        report = sf.FaultRun(self.fx).run()
        self.assertTrue(report["legs"])
        for leg in report["legs"]:
            self.assertEqual(leg["status"], "pass", leg["name"])

    def test_zero_counters(self):
        report = sf.FaultRun(self.fx).run()
        self.assertEqual(report["zero_counters"]["duplicate_prs"], 0)
        self.assertEqual(report["zero_counters"]["unauthorized_merges"], 0)
        self.assertEqual(
            report["zero_counters"]["accepted_fenced_results"], 0)

    def test_leg_order(self):
        report = sf.FaultRun(self.fx).run()
        names = [l["name"] for l in report["legs"]]
        self.assertEqual(names, sf.FaultRun.ORDER)

    def test_day2_fields(self):
        report = sf.FaultRun(self.fx).run()
        d2 = report["day2"]
        self.assertIn("checkpoint", d2)
        self.assertIn("serial_estimate", d2)
        self.assertEqual(d2["replan_owner"], "Luong")
        self.assertTrue(d2["open_replan_items"])

    def test_met03_not_claimed(self):
        report = sf.FaultRun(self.fx).run()
        self.assertIn("not claimed", report["met03_note"])

    def test_reevaluate_recorded_run(self):
        report = sf.FaultRun(self.fx).run()
        out = sf.reevaluate(json.loads(json.dumps(report)))
        self.assertEqual(out["verdict"], "fault-suite-passed")
        self.assertEqual(len(out["gate_ledger"]), 8)

    def test_scenario_runs_subset(self):
        report = sf.FaultRun(self.fx).run("merge-uncertainty")
        names = [l["name"] for l in report["legs"]]
        self.assertIn("merge-uncertainty", names)
        self.assertNotIn("approval-drift", names)


class CliTests(FixtureCase):
    def test_self_test_exit_zero(self):
        self.assertEqual(sf.main(["--self-test"]), 0)

    def test_write_and_fixture_reeval(self):
        out = self.root / "run.json"
        rc = sf.main(["--root", str(self.root / "fx2"),
                      "--write", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.is_file())
        rc2 = sf.main(["--fixture", str(out)])
        self.assertEqual(rc2, 0)

    def test_scenario_flag(self):
        rc = sf.main(["--root", str(self.root / "fx3"),
                      "--scenario", "duplicate-delivery",
                      "--write", str(self.root / "s.json")])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
