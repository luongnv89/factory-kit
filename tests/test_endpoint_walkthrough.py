#!/usr/bin/env python3
"""Unit tests for tools/probes/endpoint_walkthrough.py (issue #4 / Task 1.3).

Each test maps to one acceptance-criteria behavior of the endpoint spike:
signed durable intake + sequential session identity (A1), verify gate
semantics (A2), revision-bound preview + smoke + owned cleanup (A3),
durable one-use approval with restart/concurrency (A4), merge-owner
revalidation deny matrix (A5/A6), and authoritative read-back /
lost-response reconcile (A7). Live legs are never invoked here — unit
coverage is fixture-deterministic.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_endpoint_walkthrough.py
"""

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / "probes" / "endpoint_walkthrough.py"
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "preview_fixture.py"

spec = importlib.util.spec_from_file_location("endpoint_walkthrough", PROBE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

spec_fx = importlib.util.spec_from_file_location(
    "preview_fixture", FIXTURE_MOD_PATH)
fx_mod = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx_mod)


def fresh_fixture():
    return probe.build_fixture(tempfile.mkdtemp(prefix="fk-ep-test-"))


def fresh_store():
    td = tempfile.mkdtemp(prefix="fk-store-test-")
    return probe.EndpointStore(Path(td) / "endpoint.db")


class IntakeTests(unittest.TestCase):
    """A1 — signed event creates durable identity; denied creates none."""

    def setUp(self):
        self.fx = fresh_fixture()
        self.w = probe.Walkthrough(self.fx)
        self.leg = self.w.stage_intake({})
        self.ev = self.w.legs[0]["evidence"]

    def test_valid_event_creates_durable_work_identity(self):
        acc = self.ev["accepted"]
        self.assertEqual(acc["outcome"], "accepted")
        self.assertTrue(acc["work_id"].startswith("wk-"))
        self.assertEqual(self.ev["work_rows"], 1)

    def test_denied_legs_create_no_authority(self):
        names = [d["leg"] for d in self.ev["denied"]]
        self.assertEqual(names, ["invalid-signature", "wrong-repository",
                                 "unauthorized-optin"])
        for d in self.ev["denied"]:
            self.assertEqual(d["outcome"], "denied", json.dumps(d))

    def test_sequential_sessions_have_own_identities(self):
        s = self.ev["sessions"]
        self.assertTrue(s["sequential"])
        impl, rev = s["implementation"], s["reviewer"]
        self.assertNotEqual(impl["attempt_id"], rev["attempt_id"])
        self.assertNotEqual(impl["session_id"], rev["session_id"])
        self.assertEqual(rev["verdict"], "approved")
        self.assertEqual(rev["pr_head"], impl["pr_head"])


class VerifyGateTests(unittest.TestCase):
    """A2 — verify requires review + checks + read-back agreement."""

    def _review(self, head="h"*8):
        return {"verdict": "approved", "pr_head": head}

    def _obs(self, head="h"*8, **kw):
        return dict({"state": "open", "draft": False,
                     "head": head, "base": "main"}, **kw)

    def _checks(self):
        return [{"name": "Code Quality & Build", "status": "completed",
                 "conclusion": "success"},
                {"name": "Security Scan", "status": "completed",
                 "conclusion": "success"}]

    def test_happy_path_passes(self):
        v = probe.evaluate_verify(self._review(), self._obs(), self._checks())
        self.assertTrue(v["pass"], v["reasons"])

    def test_impl_self_report_never_substitutes(self):
        v = probe.evaluate_verify(self._review(), self._obs(),
                                  self._checks(), impl_self_report=True)
        self.assertFalse(v["pass"])
        self.assertIn("impl-self-report-substituted", v["reasons"])

    def test_moved_head_blocks(self):
        v = probe.evaluate_verify(self._review(),
                                  self._obs(head="moved"), self._checks())
        self.assertFalse(v["pass"])
        self.assertIn("head-moved-since-review", v["reasons"])

    def test_missing_checks_block(self):
        self.assertFalse(probe.evaluate_verify(
            self._review(), self._obs(), [])["pass"])

    def test_failed_check_blocks(self):
        bad = [{"name": "Security Scan", "status": "completed",
                "conclusion": "failure"}]
        v = probe.evaluate_verify(self._review(), self._obs(), bad)
        self.assertFalse(v["pass"])
        self.assertTrue(any(r.startswith("check-failed")
                            for r in v["reasons"]))


class PreviewGateTests(unittest.TestCase):
    """A3 — revision binding, fresh smoke, outage, owned cleanup."""

    def setUp(self):
        self.fx = fresh_fixture()
        self.prev = self.fx["previews"]
        self.head = "abc123" * 6

    def _deploy_smoke(self):
        dep = self.prev.deploy(self.head, ctx={"op_id": "t1",
                                               "actor": "worker-profile"})
        smoke = self.prev.smoke(dep["deployment_id"])
        insp = self.prev.inspect(dep["deployment_id"])
        return dep, insp, smoke

    def test_happy_path_passes(self):
        dep, insp, smoke = self._deploy_smoke()
        v = probe.evaluate_preview(insp, smoke, self.head, time.time())
        self.assertTrue(v["pass"], v["reasons"])
        self.assertTrue(dep["url"].startswith("https://preview-"))

    def test_wrong_revision_cannot_reuse(self):
        other = self.prev.deploy("older-head", ctx={"op_id": "t2",
                                                    "actor": "w"})
        v = probe.evaluate_preview(
            self.prev.inspect(other["deployment_id"]),
            self.prev.smoke(other["deployment_id"]),
            self.head, time.time())
        self.assertFalse(v["pass"])
        self.assertIn("wrong-revision", v["reasons"])

    def test_unknown_deployment_denied(self):
        with self.assertRaises(KeyError):
            self.prev.inspect("dpl-does-not-exist")
        v = probe.evaluate_preview(None, None, self.head, time.time())
        self.assertFalse(v["pass"])
        self.assertIn("unknown-deployment", v["reasons"])

    def test_stale_smoke_denied(self):
        _, insp, _ = self._deploy_smoke()
        old = {"ok": True, "observed_at": time.time() - 601}
        v = probe.evaluate_preview(insp, old, self.head, time.time())
        self.assertFalse(v["pass"])
        self.assertIn("stale-smoke", v["reasons"])

    def test_failed_smoke_denied(self):
        _, insp, _ = self._deploy_smoke()
        v = probe.evaluate_preview(insp, {"ok": False}, self.head,
                                   time.time())
        self.assertFalse(v["pass"])
        self.assertIn("failed-smoke", v["reasons"])

    def test_outage_backlogs_cleanup_never_false_removal(self):
        dep, _, _ = self._deploy_smoke()
        self.prev.inject_outage(True)
        r = self.prev.cleanup(dep["deployment_id"], ctx={"actor": "owner"})
        self.assertFalse(r["removed"])
        self.assertTrue(r["backlogged"])
        self.assertIn(dep["deployment_id"],
                      self.prev.snapshot()["cleanup_backlog"])
        self.prev.inject_outage(False)

    def test_cleanup_only_owned_deployments(self):
        with self.assertRaises(KeyError):
            self.prev.cleanup("dpl-foreign", ctx={"actor": "owner"})

    def test_tokenless_handle_refused(self):
        tokenless = fx_mod.PreviewRegistry(self.fx["root"] + "/provider")
        with self.assertRaises(fx_mod.BoundaryViolation):
            tokenless.deploy(self.head, ctx={"op_id": "x", "actor": "w"})
        with self.assertRaises(fx_mod.BoundaryViolation):
            tokenless.cleanup("dpl-x", ctx={"actor": "w"})


class ApprovalStoreTests(unittest.TestCase):
    """A4 — durable one-use approval: digests, restart, concurrency."""

    def setUp(self):
        self.store = fresh_store()
        self.actor = "77001"
        self.req = self.store.request_approval(
            self.actor, "repo#7", "rev", "ev", "pol")

    def test_request_binds_digests_and_expiry(self):
        row = self.store.db.execute(
            "SELECT revision_digest,evidence_digest,policy_digest,expiry,"
            "state FROM approvals WHERE request_id=?",
            (self.req["request_id"],)).fetchone()
        self.assertEqual(row[:3], ("rev", "ev", "pol"))
        self.assertEqual(row[4], "awaiting")
        self.assertAlmostEqual(row[3] - time.time(),
                               probe.APPROVAL_EXPIRY_S, delta=5)

    def test_consume_once_then_replay_denied(self):
        r1 = self.store.approve(self.req["request_id"], self.actor,
                                "head", [self.actor])
        self.assertEqual(r1["outcome"], "consumed")
        self.assertTrue(r1["merge_intent"].startswith("mi-"))
        r2 = self.store.approve(self.req["request_id"], self.actor,
                                "head", [self.actor])
        self.assertEqual(r2["outcome"], "denied")
        self.assertEqual(r2["reason"], "replayed")

    def test_wrong_actor_denied(self):
        r = self.store.approve(self.req["request_id"], "99999",
                               "head", [self.actor])
        self.assertEqual(r["outcome"], "denied")
        self.assertEqual(r["reason"], "wrong-actor")
        self.assertEqual(r["merge_intents_for_request"], 0)

    def test_expired_request_denied(self):
        old = self.store.request_approval(
            self.actor, "repo#8", "r", "e", "p",
            created=time.time() - probe.APPROVAL_EXPIRY_S - 60)
        r = self.store.approve(old["request_id"], self.actor,
                               "head", [self.actor])
        self.assertEqual((r["outcome"], r["reason"]), ("denied", "expired"))

    def test_concurrent_approvals_at_most_one_intent(self):
        """Two separate sessions race on the same file — BEGIN IMMEDIATE
        serializes; exactly one consumes, exactly one merge intent."""
        path = Path(self.store.db.execute("PRAGMA database_list")
                    .fetchone()[2])
        results, barrier = [], threading.Barrier(2)

        def racer():
            barrier.wait()
            s = probe.EndpointStore(path)
            try:
                results.append(s.approve(self.req["request_id"], self.actor,
                                         "head", [self.actor]))
            finally:
                s.db.close()

        ts = [threading.Thread(target=racer) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        consumed = [r for r in results if r["outcome"] == "consumed"]
        self.assertEqual(len(consumed), 1, json.dumps(results))
        intents = self.store.db.execute(
            "SELECT COUNT(*) FROM merge_intents WHERE request_id=?",
            (self.req["request_id"],)).fetchone()[0]
        self.assertEqual(intents, 1)

    def test_restart_keeps_awaiting_attributable(self):
        path = Path(self.store.db.execute("PRAGMA database_list")
                    .fetchone()[2])
        self.store.db.close()
        self.store.db = sqlite3.connect(str(path), isolation_level=None,
                                        check_same_thread=False)
        row = self.store.db.execute(
            "SELECT actor,state,target FROM approvals WHERE request_id=?",
            (self.req["request_id"],)).fetchone()
        self.assertEqual((row[0], row[1]), (self.actor, "awaiting"))


class MergeGuardTests(unittest.TestCase):
    """A5/A6 — every precondition revalidated; every change denies."""

    def _state(self, **kw):
        base = {"actor_authorized": True, "actor_revoked": False,
                "task_state": "active", "pr_open": True, "pr_draft": False,
                "pr_head": "h", "expected_head": "h", "pr_base": "main",
                "expected_base": "main", "mergeable": True,
                "checks_green": True, "base_protected": True,
                "strict_up_to_date": True, "auto_merge_enabled": False,
                "preview_fresh_healthy": True,
                "approval": {"state": "consumed",
                             "expiry": time.time() + 3600},
                "now": time.time()}
        base.update(kw)
        return base

    def test_happy_path_passes(self):
        self.assertTrue(probe.evaluate_merge_guard(self._state())["pass"])

    def test_deny_matrix(self):
        cases = {
            "head-changed": {"pr_head": "other"},
            "base-moved": {"expected_base": "moved"},
            "stale-preview": {"preview_fresh_healthy": False},
            "revoked-actor": {"actor_revoked": True},
            "fenced-task": {"task_state": "fenced"},
            "paused-task": {"task_state": "paused"},
            "expired-approval": {"approval": {"state": "consumed",
                                              "expiry": time.time() - 1}},
            "rejected-approval": {"approval": {"state": "rejected"}},
            "replayed-approval": {"approval": {"state": "awaiting"}},
            "missing-check": {"checks_green": False},
            "unprotected-base": {"base_protected": False},
            "non-strict-base": {"strict_up_to_date": False},
            "auto-merge-on": {"auto_merge_enabled": True},
        }
        for name, patch in cases.items():
            with self.subTest(deny=name):
                v = probe.evaluate_merge_guard(self._state(**patch))
                self.assertFalse(v["pass"], f"{name} should deny")

    def test_unauthorized_actor_denied(self):
        v = probe.evaluate_merge_guard(
            self._state(actor_authorized=False))
        self.assertFalse(v["pass"])
        self.assertIn("actor-unauthorized", v["reasons"])


class ReadbackTests(unittest.TestCase):
    """A7 — merged state only from authoritative read-back; reconcile."""

    def test_merge_invocation_and_readback(self):
        fx = fresh_fixture()
        w = probe.Walkthrough(fx)
        ctx = w.stage_intake({})
        w.stage_verify(ctx)
        w.stage_readback(ctx)
        leg = w.legs[-1]["evidence"]
        happy = leg["legs"][0]
        self.assertTrue(happy["authoritative"])
        self.assertEqual(happy["merge_sha"], ctx["impl_head"])
        recon = leg["legs"][1]
        self.assertEqual(recon["reconciled_sha"], ctx["impl_head"])
        self.assertFalse(recon["retried"])
        self.assertEqual(leg["defaults"]["approval_expiry_s"], 3600)
        self.assertEqual(leg["defaults"]["smoke_max_age_s"], 600)

    def test_expected_head_mismatch_refused(self):
        fx = fresh_fixture()
        remote = fx["remote"]
        remote.open_pr(9, "real-head", "main", "t",
                       ctx={"op_id": "p9", "repository": "spike/disposable",
                            "generation": 1, "actor": "w"})
        with self.assertRaises(Exception):
            remote.invoke_merge(9, "stale-head",
                                ctx={"op_id": "m9", "actor": "w"})


class CliTests(unittest.TestCase):
    """CLI surface: --self-test, --fixture re-evaluation, exit codes."""

    def test_self_test_exit_zero(self):
        r = subprocess.run(
            [sys.executable, str(PROBE), "--self-test"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_fixture_reevaluation(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "run.json"
            r = subprocess.run(
                [sys.executable, str(PROBE), "--no-live", "--root", td,
                 "--write", str(out)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(out.is_file())
            r2 = subprocess.run(
                [sys.executable, str(PROBE), "--fixture", str(out)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)

    def test_full_pipeline_all_gates_pass_fixture(self):
        fx = fresh_fixture()
        rep = probe.Walkthrough(fx).run()
        self.assertEqual(rep["verdict"], "endpoint-demonstrated",
                         json.dumps(rep["gates"]))
        self.assertTrue(all(s == "pass" for s in rep["gates"].values()))
        self.assertLessEqual(len(rep["store_dump"]["merge_intents"]), 1)
        # every linked identity class present (A8)
        for k in ("work_id", "task_id", "impl_attempt", "review_attempt",
                  "pr_head_reviewed", "deployment_id", "approval_request",
                  "merge_intent"):
            self.assertIn(k, rep["linked_identities"], k)


if __name__ == "__main__":
    unittest.main()
