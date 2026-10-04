#!/usr/bin/env python3
"""Unit tests for tools/probes/fenced_effects.py (issue #3 / Task 1.2).

Each test maps to one acceptance-criteria behavior of the authority spike:
credential scoping and boundary-only publication (A1), pre-write
authorization checks (A2), cancel fencing order and quarantine (A3), and
canary/secret hygiene (A4).

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_fenced_effects.py
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tools" / "probes" / "fenced_effects.py"
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "disposable_repo.py"

spec = importlib.util.spec_from_file_location("fenced_effects", PROBE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

spec_fx = importlib.util.spec_from_file_location(
    "disposable_repo", FIXTURE_MOD_PATH)
fx_mod = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx_mod)


def fresh_fixture():
    return probe.build_fixture(tempfile.mkdtemp(prefix="fk-fx-test-"))


def req(op_id, op, **kw):
    return probe._req(op_id, op, **kw)


class CredentialSurfaceTests(unittest.TestCase):
    """A1 — the worker holds no privileged credential; direct writes fail."""

    def test_worker_env_has_no_privileged_credentials(self):
        result = probe.scenario_credential_surface()
        self.assertTrue(result["passed"], json.dumps(result["evidence"]))
        env_keys = result["evidence"]["env_keys"]
        for forbidden in probe.FORBIDDEN_WORKER_ENV:
            self.assertNotIn(forbidden, env_keys)

    def test_direct_mutation_attempts_fail(self):
        report = probe.scenario_credential_surface()["evidence"]
        attempts = {a["attempt"]: a["result"] for a in report["direct_attempts"]}
        self.assertEqual(
            attempts,
            {"adapter-publish": "denied", "adapter-pr": "denied",
             "raw-state-write": "denied"})

    def test_unprivileged_handle_refuses_every_op(self):
        fx = fresh_fixture()
        remote = fx_mod.DisposableRemote(fx["root"])  # no token
        for call in (
            lambda: remote.publish_branch("x", "0" * 40, {}),
            lambda: remote.open_pr(1, "x", "main", "t", {}),
            lambda: remote.deploy_preview("0" * 40, {}),
            lambda: remote.invoke_merge(1, "x", {}),
            lambda: remote.api_mutation("/x", {}, {}),
        ):
            with self.assertRaises(fx_mod.BoundaryViolation):
                call()
        # and the read-only state file refuses a raw bypass write
        with self.assertRaises(PermissionError):
            remote.state_path.write_text("{}", encoding="utf-8")

    def test_dropbox_refuses_op_id_path_traversal(self):
        """The worker-side channel is itself a direct-mutation surface: an
        op_id carrying a separator would escape requests/ and let the worker
        overwrite remote.json (or any *.json under the fixture root) without
        the broker — an unmediated mutation route, which must not exist."""
        fx = fresh_fixture()
        before = fx["remote"].snapshot()
        for bad in ("../remote", "..", ".", "/abs/path", "a/b", ""):
            with self.assertRaises(ValueError, msg=f"op_id={bad!r}"):
                fx_mod.submit_request(
                    fx["root"], req(bad, "branch-publish",
                                    payload={"branch": "x", "sha": "0" * 40}))
        self.assertEqual(fx["remote"].snapshot(), before,
                         "a refused op_id must leave the remote untouched")
        self.assertEqual(
            sorted(p.name for p in
                   (Path(fx["root"]) / fx_mod.REQUESTS_DIR).glob("*.json")),
            [], "refused op_ids must not drop request files")
        with self.assertRaises(ValueError):
            fx_mod.await_response(fx["root"], "../fixture-manifest")


class ScopedPublicationTests(unittest.TestCase):
    """A1 — allowed publication crosses the boundary and records identity."""

    def test_allowed_publication_records_op_repo_generation(self):
        result = probe.scenario_scoped_publication()
        self.assertTrue(result["passed"], json.dumps(result["evidence"]))
        for event in result["evidence"]["events"]:
            self.assertEqual(event["repository"], "spike/disposable")
            self.assertEqual(event["generation"], 1)
            self.assertIn("op_id", event)


class AuthorizationMatrixTests(unittest.TestCase):
    """A2 — checks run before each permitted write; denials keep evidence."""

    def test_wrong_repository_denied_no_remote_mutation(self):
        self.assertTrue(probe.scenario_wrong_repository()["passed"])

    def test_revoked_actor_denied(self):
        self.assertTrue(probe.scenario_revoked_actor()["passed"])

    def test_superseded_generation_denied(self):
        self.assertTrue(probe.scenario_superseded_generation()["passed"])

    def test_prompt_text_cannot_change_policy(self):
        self.assertTrue(probe.scenario_prompt_text_no_policy()["passed"])

    def test_every_permitted_write_checked(self):
        result = probe.scenario_write_type_matrix()
        self.assertTrue(result["passed"], json.dumps(result["evidence"]))
        self.assertEqual(len(result["evidence"]["denied"]), 5)

    def test_duplicate_op_id_is_serialized_not_reapplied(self):
        """§6.4 publication intent: a repeated op_id returns the recorded
        outcome without a second remote effect."""
        fx = fresh_fixture()
        broker = probe.Broker(fx)
        dup = req("dup-1", "branch-publish",
                  payload={"branch": "b", "sha": "1" * 40})
        res = [fx["store"].submit_intent(dup, broker._apply, broker.redact),
               fx["store"].submit_intent(dict(dup), broker._apply, broker.redact)]
        self.assertEqual(res[0]["outcome"], "allowed")
        self.assertEqual(res[1]["outcome"], "allowed")
        self.assertTrue(res[1].get("deduplicated"))
        events = fx["remote"].snapshot()["events"]
        self.assertEqual(len(events), 1, "a deduplicated intent must not "
                         "apply a second remote effect")


class CancelFenceTests(unittest.TestCase):
    """A3 — fence commits before termination; late effects are rejected."""

    def test_cancel_fences_before_termination(self):
        result = probe.scenario_cancel_fence_order()
        self.assertTrue(result["passed"], json.dumps(result["evidence"]))
        ev = result["evidence"]
        self.assertEqual(ev["worker_exit"], "confirmed")
        self.assertLessEqual(ev["fence_committed_at"],
                             ev["terminate_initiated_at"])
        for op_id in ("s8-during-kill", "s8-descendant", "s8-result"):
            self.assertEqual(ev["denied"][op_id], "generation-fenced")

    def test_forge_and_unknown_actor_control_rejected(self):
        fx = fresh_fixture()
        out = fx["store"].accept_control(
            {"actor": "66006", "chat": "-100", "action": "cancel",
             "target_generation": 1})
        self.assertFalse(out["accepted"])
        self.assertEqual(out["reason"], "unknown-actor")
        gen = fx["store"].current_generation("spike/disposable", 0)
        self.assertEqual(gen[1], "active", "a forged cancel must not fence")

    def test_unsupported_typed_action_rejected(self):
        fx = fresh_fixture()
        out = fx["store"].accept_control(
            {"actor": "77001", "chat": "-100", "action": "nuke",
             "target_generation": 1})
        self.assertFalse(out["accepted"])
        self.assertEqual(out["reason"], "unsupported-action")

    def test_unconfirmed_termination_quarantines_and_blocks_replacement(self):
        result = probe.scenario_quarantine_no_replacement()
        self.assertTrue(result["passed"], json.dumps(result["evidence"]))
        rep = result["evidence"]["replacement"]
        self.assertFalse(rep["allowed"])
        self.assertTrue(rep["quarantined"])


class CanaryHygieneTests(unittest.TestCase):
    """A4 — canaries stay out of exports/logs/notifications; secrets outside
    the manifest; transports are the selected ones; the boundary is the check."""

    def test_canaries_absent_from_exports_and_notifications(self):
        result = probe.scenario_canary_hygiene()
        self.assertTrue(result["passed"], json.dumps(result["evidence"]))
        self.assertEqual(result["evidence"]["leaked"], [])
        self.assertEqual(result["evidence"]["manifest_holds_values"], [])

    def test_denied_request_payload_is_redacted_in_audit(self):
        fx = fresh_fixture()
        broker = probe.Broker(fx)
        canary = fx["canaries"]["github_token"]
        fx_mod.submit_request(
            fx["root"], req("leak-1", "branch-publish", repo="other/repo",
                            notes=f"see token {canary}"))
        broker.process_pending()
        audit = fx["store"].dump()["audit"]
        detail = " ".join(a["detail"] or "" for a in audit)
        self.assertNotIn(canary, detail)
        self.assertIn("<redacted:github_token>", detail)

    def test_transports_are_the_selected_ones(self):
        self.assertTrue(probe.scenario_transports_selected()["passed"])

    def test_worktree_and_protection_are_not_the_boundary(self):
        self.assertTrue(probe.scenario_boundary_not_worktree()["passed"])


class ProbeCliTests(unittest.TestCase):
    """CLI surface: self-test, full run, fixture re-evaluation, exit codes."""

    def run_probe(self, *argv):
        return subprocess.run(
            [sys.executable, str(PROBE), *argv],
            capture_output=True, text=True, timeout=180)

    def test_self_test_passes(self):
        r = self.run_probe("--self-test")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_full_run_proven_and_writes_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "evidence.json"
            r = self.run_probe("--write", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            report = json.loads(r.stdout)
            self.assertEqual(report["verdict"], "boundary-proven")
            doc = json.loads(out.read_text())
            self.assertEqual(doc["report"]["verdict"], "boundary-proven")
            self.assertIn("day2_checkpoint", doc["report"])

    def test_fixture_reevaluation(self):
        doc = [{"scenario": "x", "ac": "A1", "passed": True}]
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "run.json"
            p.write_text(json.dumps({"results": doc}))
            r = self.run_probe("--fixture", str(p))
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)["verdict"],
                             "boundary-proven")

    def test_missing_fixture_exit_4(self):
        r = self.run_probe("--fixture", "/nonexistent/none.json")
        self.assertEqual(r.returncode, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
