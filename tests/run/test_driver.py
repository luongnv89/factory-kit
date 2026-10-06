#!/usr/bin/env python3
"""The live driver's post-lane stage orchestration over scripted ports.

The composition is the same ``compose()`` the CLI uses — only the ports
are fixtures: a scripted remote (GitHub), a scripted preview provider
(Vercel), a scripted issue source, and a worker that mints a real git
worktree per dispatch so the driver's head/branch reads exercise real
git, not a mock's claim.

The staged walk asserts the full dogfood path in durable order:
opted-in issue → lane reviewed → review recorded → PR published+linked
→ pending checks wait → green → verified → preview verified → approval
requested → operator approve → merged → work completed + preview
terminated. Plus the serialization (a second issue never overlaps the
first), the registration-not-enabled refusal and the F12 stale-smoke →
resmoke → fresh-approval path.
"""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.approval import ApprovalService  # noqa: E402
from factory_kit.config import registration, schema  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.execution.worker import (  # noqa: E402
    SessionHandle, WorkerResult, WorkerPort)
from factory_kit.intake import service as intake_service  # noqa: E402
from factory_kit.preview import ScriptedPreview  # noqa: E402
from factory_kit.publication.remote import ScriptedRemote  # noqa: E402
from factory_kit.recovery.poller import ScriptedIssueSource  # noqa: E402
from factory_kit.run import cli as run_cli  # noqa: E402
from factory_kit.run.driver import Driver, compose  # noqa: E402

MANIFEST = ROOT / ".factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
READINGS = {
    "tools": {"git": "/usr/bin/git", "gh": "/x/gh",
              "hermes": "/x/hermes", "vercel": "/x/vercel"},
    "models": {"openai-codex": {"authenticated": True,
                                "available": ["gpt-6-luna"]}},
    "skills": {"issue-resolver": "0.19.0", "issue-pr-review": "0.19.0"},
}
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"
GREEN = [
    {"id": "1", "name": "Code Quality & Build", "status": "completed",
     "conclusion": "success", "app": "github-actions",
     "details_url": "https://ci.test/1"},
    {"id": "2", "name": "Security Scan", "status": "completed",
     "conclusion": "success", "app": "github-actions",
     "details_url": "https://ci.test/2"},
]
PENDING = [dict(r, status="in_progress", conclusion=None)
           for r in GREEN]


def _git(repo, *argv):
    env = dict(os.environ) if False else None
    proc = subprocess.run(["git", "-C", str(repo), *argv],
                          capture_output=True, text=True,
                          env=_GIT_ENV)
    assert proc.returncode == 0, (argv, proc.stderr)
    return proc.stdout.strip()


import os  # noqa: E402
_GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="fk", GIT_AUTHOR_EMAIL="f@f",
                GIT_COMMITTER_NAME="fk", GIT_COMMITTER_EMAIL="f@f")


class DriverWorker(WorkerPort):
    """A worker that behaves like the Hermes adapter's *observable*
    surface: each dispatch turns the lane's empty workspace dir into a
    real git repo on the ``fk/issue-<n>-g<g>`` branch with one commit
    (the implementation "work"), and ``result_for`` re-reads the stored
    per-attempt terminal record — the restart-recovery surface."""

    def __init__(self):
        self.started = []
        self.results = {}         # attempt_id -> info dict
        self.cleaned = []
        self.calls = 0

    def start(self, ctx):
        self.calls += 1
        ws = ctx.workspace
        if ctx.role == "implementation":
            _git(ws, "init", "-b",
                 f"fk/issue-{ctx.issue}-g{ctx.generation}")
            Path(ws, "change.txt").write_text(
                f"impl of issue {ctx.issue}\n")
            _git(ws, "add", "change.txt")
            _git(ws, "commit", "-m", f"implement #{ctx.issue}")
        # Review runs against the existing worktree the implementation
        # left — the same reuse contract the Hermes adapter asserts.
        head = _git(ws, "rev-parse", "HEAD")
        handle = SessionHandle(session_id=ctx.session_id,
                               attempt_id=ctx.attempt_id,
                               role=ctx.role, started_epoch=0.0,
                               runtime_ref=f"t-{ctx.attempt_id}")
        self.started.append(ctx)
        if ctx.role == "implementation":
            self.results[ctx.attempt_id] = {
                "kanban_task_id": handle.runtime_ref, "status": "done",
                "summary": "implemented", "observed_head": head,
                "metadata": {"head_sha": head, "acceptance": "passed"},
                "role": "implementation",
                "result": WorkerResult(verdict="completed",
                                       detail="implemented")}
        else:
            self.results[ctx.attempt_id] = {
                "kanban_task_id": handle.runtime_ref, "status": "done",
                "summary": "approved: looks right",
                "observed_head": head,
                "metadata": {"verdict": "approved",
                             "reviewed_sha": head, "findings": []},
                "role": "review",
                "result": WorkerResult(verdict="approved",
                                       detail="approved: looks right")}
        return handle

    def heartbeat(self, handle):
        pass

    def collect(self, handle):
        return self.results[handle.attempt_id]["result"]

    def terminate(self, handle):
        return "confirmed"

    def result_for(self, attempt_id):
        return self.results.get(attempt_id)

    def cleanup_workspace(self, workspace):
        self.cleaned.append(str(workspace))

    @property
    def model_calls(self):
        return self.calls


class DriverWorld(unittest.TestCase):
    """One wired driver world: temp state dir + scripted ports."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.clock = [time.time()]
        self.logs = []
        self.eff = schema.load_manifest_file(MANIFEST)
        self.eff["endpoint"]["merge"]["approval_channels"] = [
            "telegram", "operator-cli"]
        self.repo_id = self.eff["identity"]["repo_id"]
        self.full_name = (f"{self.eff['identity']['owner']}/"
                          f"{self.eff['identity']['name']}")
        self.registrations = registration.RegistrationStore(
            self.root / "registrations.json")
        self.registrations.register(self.eff, readiness=READY,
                                    supported_versions=VERSIONS)
        self.store = durable.IntakeStore(self.root / "intake.db")
        self.remote = ScriptedRemote(self.repo_id, self.full_name)
        self.remote.actor_login = "luongnv89"
        # CI starts as soon as the branch lands: unscripted heads read
        # back as *pending* (status blocked + check-incomplete) — the
        # driver's wait path — while explicit ``checks[sha]`` rows
        # still drive the green/failed cases.
        orig_check_runs = self.remote.check_runs

        def check_runs(sha):
            rows = orig_check_runs(sha)
            return rows if rows else [dict(r) for r in PENDING]
        self.remote.check_runs = check_runs
        self.provider = ScriptedPreview(now=self._now)
        self.source = ScriptedIssueSource(
            self.repo_id, self.full_name,
            issues={42: {"title": "Add the thing", "body": BODY,
                         "labels": ["factory-kit"], "state": "open",
                         "author": "luongnv89",
                         "updated_at": "2026-10-05"}})
        self.worker = DriverWorker()
        self.services = compose(
            store=self.store, registrations=self.registrations,
            effective=self.eff, worker=self.worker,
            remote=self.remote, preview_port=self.provider,
            issue_source=self.source,
            workspace_root=str(self.root / "ws"),
            readings=READINGS, now=self._now)
        self.driver = Driver(str(self.repo), store=self.store,
                             registrations=self.registrations,
                             effective=self.eff,
                             services=self.services,
                             worker=self.worker, remote=self.remote,
                             now=self._now, log=self.logs.append)

    def _now(self):
        return self.clock[0]

    def _accept(self, issue=42):
        env = {"delivery_id": f"d-{issue}",
               "channel": "reconciliation", "repo_id": self.repo_id,
               "repository": self.full_name, "issue": issue,
               "action": "reconcile", "sender": "luongnv89",
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.services.intake.deliver(env)
        assert ack["outcome"] == "accepted", ack
        return ack["work_key"]

    def _head(self, work_key):
        recs = self.store.attempt_record_rows(work_key)
        return _git(recs[-1]["workspace"], "rev-parse", "HEAD")


class TestLifecycle(DriverWorld):

    def test_full_stage_flow(self):
        work_key = self._accept(42)

        # Tick 1: lane dispatch (impl+review) → reviewed, then the
        # post-lane stages run immediately — review recorded, branch +
        # PR published and linked; checks pending → verify waits.
        report = self.driver.tick()
        self.assertEqual(report["lane"]["dispatched"], [work_key])
        self.assertEqual(self.store.queue_entry(work_key)["state"],
                         "reviewed")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "active")
        review = self.store.latest_review(work_key)
        self.assertIsNotNone(review)
        self.assertEqual(review["sha"], self._head(work_key))
        self.assertEqual(review["verdict"], "approved")
        work = self.store.get_work(work_key)
        self.assertTrue(work["linked_pr"])
        pr = self.remote.pulls[str(work["linked_pr"])]
        self.assertEqual(pr["head"], "fk/issue-42-g1")
        self.assertEqual(pr["base"], "main")
        self.assertEqual(pr["title"], "Add the thing (#42)")
        # Verification waits on pending checks — never parks.
        ev = self.store.latest_evidence(work_key)
        self.assertEqual(ev["status"], "blocked")
        self.assertIn("check-incomplete", ev["reason"])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "active")

        # Tick 2: checks green → verified → preview deployed+smoked →
        # approval requested and presented.
        self.remote.checks[self._head(work_key)] = list(GREEN)
        report = self.driver.tick()
        ev = self.store.latest_evidence(work_key)
        self.assertEqual(ev["status"], "verified")
        st = self.services.preview.status(work_key)
        self.assertEqual(st["status"], "verified")
        req = self.services.approval.status(work_key)["live_request"]
        self.assertIsNotNone(req)
        self.assertEqual(req["state"], "awaiting")
        self.assertEqual(
            self.store.queue_entry(work_key)["state"],
            "awaiting-approval")
        self.assertTrue(any("--request" in line or "approve" in line
                            for line in self.logs))

        # Operator decision through the CLI-shaped surface.
        dec = self.services.approval.decide_operator(
            request_id=req["request_id"], github_login="luongnv89",
            verified_login="luongnv89", verdict="approve",
            host="test-host")
        self.assertEqual(dec["outcome"], "approved")

        # Tick 3: merge lands — work completed + queue done, preview
        # terminated, workspace cleaned.
        report = self.driver.tick()
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "completed")
        self.assertEqual(self.store.queue_entry(work_key)["state"],
                         "done")
        intent = self.store.merge_intent_for_request(
            req["request_id"])
        self.assertEqual(intent["state"], "merged")
        self.assertTrue(intent["merge_sha"])
        self.assertTrue(self.worker.cleaned)
        self.assertFalse(self.provider.deployments)
        self.assertEqual(
            [c["op"] for c in self.provider.calls].count("remove"), 1)

    def test_second_issue_never_overlaps_in_flight(self):
        first = self._accept(42)
        self.source.issues[43] = {"title": "Second", "body": BODY,
                                  "labels": ["factory-kit"],
                                  "state": "open",
                                  "author": "luongnv89",
                                  "updated_at": "x"}
        env = {"delivery_id": "d-43", "channel": "reconciliation",
               "repo_id": self.repo_id, "repository": self.full_name,
               "issue": 43, "action": "reconcile",
               "sender": "luongnv89", "labels": ["factory-kit"],
               "issue_title": "s", "issue_body": BODY,
               "issue_revision": "x"}
        ack = self.services.intake.deliver(env)
        second = ack["work_key"]
        self.assertEqual(ack["outcome"], "accepted")

        # The first tick dispatches only issue 42 (max_dispatch=1) and
        # while it is in post-lane flight the second never starts.
        self.driver.tick()
        self.assertEqual(
            [c.role for c in self.worker.started],
            ["implementation", "review"])
        self.assertEqual(self._attempts(second), [])
        # Still in flight (awaiting checks): another tick leaves the
        # second untouched.
        self.driver.tick()
        self.assertEqual(self._attempts(second), [])

        # Land the first: green checks → approval → merge.
        self.remote.checks[self._head(first)] = list(GREEN)
        self.driver.tick()
        req = self.services.approval.status(first)["live_request"]
        self.services.approval.decide_operator(
            request_id=req["request_id"], github_login="luongnv89",
            verified_login="luongnv89", verdict="approve",
            host="t")
        self.driver.tick()
        self.assertEqual(self.store.get_work(first)["state"],
                         "completed")

        # Now the lane is free — the second dispatches on the next tick.
        report = self.driver.tick()
        self.assertEqual(report["lane"]["dispatched"], [second])
        self.assertEqual(self.store.queue_entry(second)["state"],
                         "reviewed")

    def _attempts(self, work_key):
        return self.store.attempt_record_rows(work_key)


class TestBuildRefusals(unittest.TestCase):

    def test_registration_not_enabled(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Path(tmp.name) / "repo"
        repo.mkdir()
        # A valid manifest but no registration row at all → refused
        # before any remote call.
        import shutil
        shutil.copy(MANIFEST, repo / ".factory-kit.yml")
        state = Path(tmp.name) / "state"
        args = type("A", (), {"repo": str(repo),
                              "state_dir": str(state),
                              "profile": "default",
                              "board": None})()
        with self.assertRaises(run_cli.Refusal) as cm:
            run_cli.build(args, recover=False)
        self.assertEqual(cm.exception.reason,
                         "registration-not-enabled")


class TestResmoke(DriverWorld):
    """F12: stale smoke evidence can never carry a merge — the driver
    re-observes the deployment and mints a *fresh* request."""

    def _to_approved(self):
        work_key = self._accept(42)
        self.driver.tick()
        self.remote.checks[self._head(work_key)] = list(GREEN)
        self.driver.tick()
        req = self.services.approval.status(work_key)["live_request"]
        dec = self.services.approval.decide_operator(
            request_id=req["request_id"], github_login="luongnv89",
            verified_login="luongnv89", verdict="approve", host="t")
        assert dec["outcome"] == "approved", dec
        return work_key, req

    def test_stale_smoke_resmokes_then_requires_fresh_approval(self):
        work_key, req = self._to_approved()
        # Age the smoke past max_smoke_age_minutes (10) but inside the
        # request's 60-minute expiry.
        self.clock[0] += 11 * 60
        self.driver.tick()
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "active")
        # The stale grant was invalidated and a NEW request is live.
        old = self.store.get_approval_request(req["request_id"])
        self.assertNotEqual(old["state"], "approved")
        live = self.services.approval.status(
            work_key)["live_request"]
        self.assertIsNotNone(live)
        self.assertNotEqual(live["request_id"], req["request_id"])
        self.assertEqual(live["state"], "awaiting")
        # No merge intent was ever committed under the dead grant.
        self.assertEqual(self.store.merge_intent_rows(work_key), [])
        # A fresh approval then merges.
        self.services.approval.decide_operator(
            request_id=live["request_id"], github_login="luongnv89",
            verified_login="luongnv89", verdict="approve", host="t")
        self.driver.tick()
        self.assertEqual(work["state"] if False else
                         self.store.get_work(work_key)["state"],
                         "completed")


if __name__ == "__main__":
    unittest.main()
