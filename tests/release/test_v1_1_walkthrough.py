#!/usr/bin/env python3
"""v1.1 supported-version onboarding walkthrough — issue #31 (task
4.7, A5).

One clean supported-version run of the package instructions in
``docs/releases/v1.1/onboarding.md`` — installation, checkpoint
steering (F09), interrupted-migration repair and rollback (F10), then
removal — on the disposable repository fixture
(``tests/fixtures/setup_repo.py``), asserting the two honesty
properties the walkthrough advertises end to end:

- user edits are preserved byte-for-byte across every leg (the
  fixture's dirty tracked file and untracked scratch file are
  checksummed before and after);
- removal leaves no orphaned execution authority — the registration
  tombstones, the canceled work's fence stays durable, no pending
  steer or live attempt survives, and intake denies new deliveries.

The setup legs run as real ``python3 -m factory_kit.setup``
subprocesses exactly as documented; the steering leg rides the real
``factory_kit.control`` service on the CLI-written registration,
exactly as ``tests/control/test_steering.py`` exercises it.

Run:  python3 -m unittest tests.release.test_v1_1_walkthrough -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "setup_repo.py"
READINESS_FIXTURES = ROOT / "tests" / "fixtures" / "readiness"

sys.path.insert(0, str(ROOT))
from factory_kit.config import registration, schema  # noqa: E402
from factory_kit.control import ControlService, steering  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.execution import (  # noqa: E402
    ExecutionLane, ScriptedWorker)
from factory_kit.intake import service  # noqa: E402
from factory_kit.setup import upgrade as upgrade_mod  # noqa: E402
from factory_kit.setup.ownership import SetupStore  # noqa: E402

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)

OPERATOR = "recipe-test-operator"
BODY = ("Walkthrough work item.\n\n## Acceptance Criteria\n\n"
        "- [ ] A1. it works\n")
CRITERIA_V2 = "Revised scope: documentation surface only."


class _PreviewStub:
    """Captures preview invalidation/termination calls — no provider."""

    def __init__(self):
        self.calls = []

    def invalidate(self, work_key, reason):
        self.calls.append(("invalidate", work_key, reason))
        return {"outcome": "invalidated", "work_key": work_key}

    def terminate(self, work_key, reason=None):
        self.calls.append(("terminate", work_key, reason))
        return {"outcome": "cleaned", "work_key": work_key}


class TestSupportedWalkthrough(unittest.TestCase):
    """Legs 1–6 of docs/releases/v1.1/onboarding.md on one fixture."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.repo = fx.build_repo(self.work / "repo")
        self.manifest = self.work / "candidate.factory-kit.yml"
        self.manifest.write_text(fx.manifest_text(), encoding="utf-8")
        self.candidate = self.work / "candidate-v2.factory-kit.yml"
        self.candidate.write_text(
            fx.manifest_text().replace("ttl_hours: 24",
                                       "ttl_hours: 48"),
            encoding="utf-8")
        self.candidate3 = self.work / "candidate-v3.factory-kit.yml"
        self.candidate3.write_text(
            fx.manifest_text().replace("ttl_hours: 24",
                                       "ttl_hours: 72"),
            encoding="utf-8")
        self.state = self.work / "setup-state.json"
        self.registrations = self.work / "registrations.json"
        # The preservation oracle — dirty tracked file + untracked
        # scratch file, checksummed before anything runs.
        self._pre_snapshot = fx.snapshot(self.repo)
        self.clock = [time.time()]
        self._cid = [0]

    # -- helpers ----------------------------------------------------------

    def cli(self, *args):
        """Run a published command exactly as the package writes it."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + \
            env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "factory_kit.setup", *args],
            capture_output=True, text=True, cwd=str(ROOT), env=env)

    def _readiness(self):
        return self.cli("readiness", "--repo", str(self.repo),
                        "--readings",
                        str(READINESS_FIXTURES / "ready.json"),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))

    def _install(self):
        """Leg 1 — plan → apply → readiness → status."""
        proc = self.cli("plan", "--repo", str(self.repo),
                        "--manifest", str(self.manifest),
                        "--out", str(self.work / "plan.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(
            json.loads((self.work / "plan.json").read_text())
            ["appliable"])
        proc = self.cli("apply", "--repo", str(self.repo),
                        "--plan", str(self.work / "plan.json"),
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self._readiness()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["verdict"], "ready")
        proc = self.cli("status", "--repo", str(self.repo),
                        "--state", str(self.state))
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def _wire_control(self):
        """Bind the real control service to the CLI-written
        registration — the checkpoint-steering surface leg 2 rides."""
        self.eff = schema.load_manifest_file(
            self.repo / ".factory-kit.yml")
        self.repo_id = self.eff["identity"]["repo_id"]
        self.reg_store = registration.RegistrationStore(
            self.registrations)
        self.store = durable.IntakeStore(self.work / "in.db")
        self.intake = service.IntakeService(
            self.store, self.reg_store, {self.repo_id: self.eff},
            signing_secret="s", opt_in_label="factory-kit")
        self.worker = ScriptedWorker()
        self.lane = ExecutionLane(
            self.store, self.reg_store, {self.repo_id: self.eff},
            worker=self.worker,
            workspace_root=self.work / "ws",
            readings={}, now=self._now)
        self.preview = _PreviewStub()
        self.alerts = []
        self.control = ControlService(
            self.store, self.lane, self.reg_store,
            {self.repo_id: self.eff}, preview=self.preview,
            alert_sink=self.alerts, now=self._now)
        self.lane.steer_post_apply = self.control._steer_post_apply

    def _now(self):
        return self.clock[0]

    def _generation(self):
        reg = json.loads(self.registrations.read_text())
        return reg["registrations"][self.repo_id]["active_generation"]

    def _accept(self, issue=1):
        env = {"delivery_id": f"walkthrough-d-{issue}",
               "channel": "reconciliation",
               "repo_id": self.repo_id,
               "repository": "testowner/testrepo", "issue": issue,
               "action": "reconcile", "sender": "testowner",
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.intake.deliver(env)
        assert ack["outcome"] == "accepted", ack
        return ack["work_key"]

    def _msg(self, action, *, issue=1, generation=None, **fields):
        self._cid[0] += 1
        msg = {"command_id": f"walkthrough-c-{self._cid[0]:03d}",
               "actor": {"user_id": 12345},
               "chat": {"chat_id": -100123},
               "action": action, "repo_id": self.repo_id,
               "issue": issue,
               "generation": generation if generation is not None
               else self._generation()}
        msg.update(fields)
        return msg

    def _steer(self, issue=1):
        return self.control.handle(
            self._msg("steer", issue=issue, criteria=CRITERIA_V2))

    def _upgrade_plan(self, candidate=None, out="upgrade.json"):
        return self.cli("upgrade", "--repo", str(self.repo),
                        "--manifest",
                        str(candidate or self.candidate),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations),
                        "--out", str(self.work / out))

    def _interrupt(self, stage, plan_path="upgrade.json"):
        """Leave the state a crash would leave at ``stage`` — the
        recipe's repair command then owns the recovery."""
        plan = json.loads((self.work / plan_path).read_text())
        accepted = upgrade_mod.accept_upgrade(plan, OPERATOR)
        return upgrade_mod.apply_upgrade(
            accepted, str(self.repo), SetupStore(str(self.state)),
            registration_store=registration.RegistrationStore(
                str(self.registrations)),
            stop_after=stage)

    # -- the walkthrough ----------------------------------------------------

    def test_clean_supported_version_walkthrough(self):
        # Leg 1 — installation: plan/apply/readiness/status, additive
        # and reviewed; readiness gates registration enablement.
        self._install()
        reg = json.loads(self.registrations.read_text())
        row = reg["registrations"]["R_TEST0001"]
        self.assertEqual(row["active_generation"], 1)
        self.assertEqual(row["readiness"]["verdict"], "ready")
        self.assertEqual(row["readiness"]["dispatch"], "allowed")

        # Leg 2 — checkpoint steering (F09): the durable revision lands
        # before the ack; queued work applies inside one commit at the
        # recorded checkpoint stage; cancel then fences the generation.
        self._wire_control()
        work_key = self._accept(1)
        out = self._steer(1)
        self.assertEqual(out["steer_state"], "applied")
        steer = self.store.steer_rows(work_key)[0]
        self.assertEqual(steer["applied_stage"], "implementation")
        self.assertEqual(steer["criteria_fingerprint"][:12],
                         steering.criteria_fingerprint(CRITERIA_V2)[:12])
        out = self.control.handle(self._msg("cancel", issue=1))
        self.assertEqual(out["outcome"], "accepted")
        fence = self.store.fence_state(work_key)
        self.assertTrue(fence["fenced"])

        # Leg 3 — upgrade (F10): read-only plan, audited migration,
        # readiness re-pass on the reviewed target.
        proc = self._upgrade_plan()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.cli("migrate", "--repo", str(self.repo),
                        "--plan", str(self.work / "upgrade.json"),
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["outcome"],
                         "upgraded")
        self.assertIn("ttl_hours: 48",
                      (self.repo / ".factory-kit.yml").read_text())
        proc = self._readiness()
        self.assertEqual(json.loads(proc.stdout)["verdict"], "ready")

        # Leg 4 — the documented rollback restores the prior digests.
        proc = self.cli("rollback", "--repo", str(self.repo),
                        "--rolled-back-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["outcome"], "restored")
        self.assertEqual(result["verification"]["file_violations"], [])
        self.assertNotIn("ttl_hours: 48",
                         (self.repo / ".factory-kit.yml").read_text())

        # Leg 5 — interrupted migration (F10): a crash-shaped state
        # left mid-migration is repair scope; repair restores the
        # validated checkpoint bytes and readiness re-passes.
        proc = self._upgrade_plan(self.candidate3, "upgrade3.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        interrupted = self._interrupt("files-migrated",
                                      "upgrade3.json")
        self.assertEqual(interrupted["outcome"], "interrupted")
        proc = self.cli("repair", "--repo", str(self.repo),
                        "--repaired-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["outcome"],
                         "repaired")
        self.assertIn('issue-resolver: "0.19.0"',
                      (self.repo / ".factory-kit.yml").read_text())
        self.assertNotIn("ttl_hours: 72",
                         (self.repo / ".factory-kit.yml").read_text())
        proc = self._readiness()
        self.assertEqual(json.loads(proc.stdout)["verdict"], "ready")

        # Leg 6 — removal: reviewable plan, explicit history choice,
        # tombstone — and no orphaned execution authority.
        proc = self.cli("uninstall", "--repo", str(self.repo),
                        "--out", str(self.work / "removal.json"),
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.cli("remove", "--repo", str(self.repo),
                        "--plan", str(self.work / "removal.json"),
                        "--history", "retain",
                        "--accepted-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["outcome"], "removed")

        # A5 tail — user edits preserved byte-for-byte; no orphaned
        # execution authority survives the removal.
        self.assertFalse((self.repo / ".factory-kit.yml").exists())
        post = fx.snapshot(self.repo)
        self.assertEqual(post.pop(".factory-kit.yml", None), None)
        self.assertEqual(post, self._pre_snapshot)
        reg = json.loads(self.registrations.read_text())
        row = reg["registrations"]["R_TEST0001"]
        self.assertTrue(row["tombstone"])
        self.assertTrue(self.store.fence_state(work_key)["fenced"])
        self.assertIsNone(self.store.pending_steer(work_key))
        # A service wired to the post-removal registration state —
        # the daemon's own store reload at startup — denies new
        # deliveries: no orphaned execution authority (F08 A4).
        fresh_regs = registration.RegistrationStore(self.registrations)
        intake_after = service.IntakeService(
            self.store, fresh_regs, {self.repo_id: self.eff},
            signing_secret="s", opt_in_label="factory-kit")
        denied = intake_after.deliver({
            "delivery_id": "walkthrough-d-post-removal",
            "channel": "reconciliation", "repo_id": self.repo_id,
            "repository": "testowner/testrepo", "issue": 2,
            "action": "reconcile", "sender": "testowner",
            "labels": ["factory-kit"], "issue_title": "t",
            "issue_body": BODY, "issue_revision": "2026-10-05"})
        self.assertEqual(denied["outcome"], "denied")

    def test_repair_with_nothing_open_is_a_named_error(self):
        """The repair leg's negative edge: repair is never a blind
        write — nothing open is a named error."""
        self._install()
        proc = self.cli("repair", "--repo", str(self.repo),
                        "--repaired-by", OPERATOR,
                        "--state", str(self.state),
                        "--registrations", str(self.registrations))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no open migrations", proc.stderr)


if __name__ == "__main__":
    unittest.main()
