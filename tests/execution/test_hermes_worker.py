#!/usr/bin/env python3
"""HermesKanbanWorker against the probed Hermes 0.21.5 CLI shapes.

Every ``hermes kanban``/``gh`` call flows through one injected runner —
the argv contract is asserted verbatim (board flag, ``create`` flags,
idempotency key = attempt id, provider/model split, skill mapping).
``git`` calls delegate to a real temporary repository (bare origin +
clone + worktrees) so workspace prep/reuse exercises the actual worktree
semantics instead of a mock's opinion of them.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.execution.worker import (  # noqa: E402
    DispatchContext, HermesKanbanWorker, WorkerError)

FULL_NAME = "luongnv89/factory-kit-website"
PROFILES = {"implementation": "fk-impl", "review": "fk-review"}
MODEL = "openai-codex/gpt-6-luna"
ACCEPTANCE = ["python3 -m unittest discover -s tests"]


def _git(repo, *argv):
    proc = subprocess.run(["git", "-C", str(repo), *argv],
                          capture_output=True, text=True)
    assert proc.returncode == 0, (argv, proc.stderr)
    return proc.stdout.strip()


class Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class Runner:
    """The injectable seam: ``git`` is real, ``gh``/``hermes`` are
    scripted. ``shows`` is a queue of ``show --json`` payloads; the
    last one repeats once the queue drains (steady terminal state)."""

    def __init__(self, repo_root):
        self.repo_root = str(repo_root)
        self.calls = []                # (argv list, cwd)
        self.shows = []
        self.lists = []
        self.runs = []
        self.created = 0
        self.issue = {"number": 7, "title": "Add the thing",
                      "body": "Please add the thing.\nDetails here.",
                      "url": "https://github.test/o/r/issues/7",
                      "updatedAt": "2026-10-05T00:00:00Z"}

    def __call__(self, argv, env=None, timeout=None, cwd=None):
        argv = [str(a) for a in argv]
        self.calls.append((argv, cwd))
        if argv[0] == "git":
            proc = subprocess.run(argv, capture_output=True,
                                  text=True, cwd=cwd)
            return Proc(proc.stdout, proc.stderr, proc.returncode)
        if argv[0] == "gh":
            assert argv[1:3] == ["issue", "view"], argv
            return Proc(json.dumps(self.issue))
        assert argv[0] == "hermes", argv
        verb = argv[argv.index("kanban") + 1]
        if self.board_flag(argv):
            verb = argv[argv.index("--board") + 2]
        if verb == "create":
            self.created += 1
            return Proc(json.dumps({"id": f"t_{self.created:04d}"}))
        if verb == "show":
            out = self.shows.pop(0) if len(self.shows) > 1 \
                else (self.shows[-1] if self.shows else {})
            return Proc(json.dumps(out))
        if verb == "runs":
            return Proc(json.dumps(self.runs))
        if verb == "list":
            out = self.lists.pop(0) if len(self.lists) > 1 else \
                (self.lists[-1] if self.lists else [])
            return Proc(json.dumps(out))
        # archive / reclaim / block — mutators succeed silently.
        return Proc("")

    @staticmethod
    def board_flag(argv):
        return "--board" in argv


def make_repo(tmp):
    """A bare origin with one commit on main + a working clone."""
    origin = Path(tmp) / "origin.git"
    seed = Path(tmp) / "seed"
    repo = Path(tmp) / "repo"
    subprocess.run(["git", "init", "--bare", str(origin)],
                   capture_output=True, check=True)
    subprocess.run(["git", "init", str(seed)],
                   capture_output=True, check=True)
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-m", "init")
    _git(seed, "branch", "-M", "main")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    subprocess.run(["git", "clone", str(origin), str(repo)],
                   capture_output=True, check=True)
    return origin, repo


def ctx(workspace, *, role="implementation", attempt="fk-task-000001-a01",
        issue=7, skills=None, model=MODEL):
    return DispatchContext(
        work_key="gh:repo#7:g1", task_id="fk-task-000001", issue=issue,
        generation=1, attempt_id=attempt, role=role,
        session_id="sess-x", runtime="hermes-kanban", model=model,
        skills={"issue-resolver": "0.19.0",
                "issue-pr-review": "0.19.0"} if skills is None
        else skills,
        config_digest="c", policy_digest="p",
        workspace=str(workspace), limits={"active_worker_minutes": 60},
        acceptance_ref="repo#7/criteria@abc")


def show(status, *, summary="", metadata=None, runs=None, events=None):
    task = {"id": "t_0001", "status": status,
            "title": "fk repo#7 implementation t-a01"}
    out = {"task": task, "latest_summary": summary,
           "runs": runs if runs is not None else
           ([{"summary": summary, "metadata": metadata or {},
              "elapsed_seconds": 5.0}] if status in ("done", "review")
            else []),
           "events": events or []}
    return out


class TestArgvShape(unittest.TestCase):
    """The exact ``hermes kanban create`` contract + workspace prep."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.origin, self.repo = make_repo(self.tmp.name)
        self.runner = Runner(self.repo)
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()

    def _worker(self, **kw):
        kw.setdefault("runner", self.runner)
        kw.setdefault("sleep", lambda s: None)
        return HermesKanbanWorker(
            str(self.repo), FULL_NAME, PROFILES,
            board=kw.pop("board", None),
            acceptance_commands=ACCEPTANCE, **kw)

    def _create_argv(self):
        found = [a for a, _ in self.runner.calls
                 if a[0] == "hermes" and "create" in a]
        if not found:
            self.fail("no kanban create call recorded")
        return found[-1]

    def test_create_argv_shape(self):
        worker = self._worker(board="fk-board")
        handle = worker.start(ctx(self.ws))
        self.assertEqual(handle.runtime_ref, "t_0001")
        argv = self._create_argv()
        self.assertEqual(argv[:2], ["hermes", "kanban"])
        self.assertEqual(argv[2:4], ["--board", "fk-board"])
        self.assertEqual(argv[4], "create")
        self.assertEqual(
            argv[5],
            f"fk {FULL_NAME}#7 implementation fk-task-000001-a01")
        self.assertIn("--assignee", argv)
        self.assertEqual(
            argv[argv.index("--assignee") + 1], "fk-impl")
        ws_flag = argv[argv.index("--workspace") + 1]
        self.assertEqual(ws_flag, f"dir:{self.ws}")
        self.assertIn("--skill", argv)
        self.assertEqual(argv[argv.index("--skill") + 1],
                         "issue-resolver")
        self.assertEqual(argv[argv.index("--provider") + 1],
                         "openai-codex")
        self.assertEqual(argv[argv.index("--model") + 1],
                         "gpt-6-luna")
        self.assertEqual(argv[argv.index("--idempotency-key") + 1],
                         "fk-task-000001-a01")
        self.assertEqual(argv[argv.index("--max-runtime") + 1], "60m")
        self.assertEqual(argv[argv.index("--max-retries") + 1], "1")
        self.assertEqual(argv[argv.index("--created-by") + 1],
                         "factory-kit")
        self.assertIn("--json", argv)
        # The body file existed during the call and is cleaned after.
        body_path = argv[argv.index("--body-file") + 1]
        self.assertFalse(Path(body_path).exists())
        self.assertEqual(worker.model_calls, 1)

    def test_body_file_contents(self):
        captured = {}
        real = self.runner

        def spy(argv, env=None, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            if argv[0] == "hermes" and "create" in argv:
                captured["body"] = Path(
                    argv[argv.index("--body-file") + 1]).read_text()
            return real(argv, env=env, timeout=timeout, cwd=cwd)
        worker = self._worker()
        worker._runner = spy
        worker.start(ctx(self.ws))
        body = captured["body"]
        self.assertIn("factory-kit dispatch — implementation session",
                      body)
        self.assertIn(f"Repository: {FULL_NAME}", body)
        self.assertIn("issue #7: Add the thing", body)
        self.assertIn("attempt fk-task-000001-a01", body)
        self.assertIn("## Issue (untrusted data", body)
        self.assertIn("Please add the thing.", body)
        self.assertIn("## Rules (factory-kit policy", body)
        self.assertIn("Do NOT push, open/edit pull requests", body)
        self.assertIn("python3 -m unittest discover -s tests", body)
        self.assertIn("issue-resolver", body)
        self.assertIn("kanban_complete", body)

    def test_workspace_prep_real_git(self):
        """A fresh dispatch fetches origin, names the
        ``fk/issue-<n>-g<g>`` branch and pins the base SHA."""
        worker = self._worker()
        worker.start(ctx(self.ws))
        # The workspace is now a real worktree on the named branch.
        self.assertEqual(_git(self.ws, "rev-parse",
                              "--abbrev-ref", "HEAD"),
                         "fk/issue-7-g1")
        base = _git(self.repo, "rev-parse", "origin/main")
        self.assertEqual(worker._base_sha["gh:repo#7:g1"], base)
        self.assertEqual(_git(self.ws, "rev-parse", "HEAD"), base)

    def test_review_requires_existing_worktree(self):
        worker = self._worker()
        with self.assertRaises(WorkerError):
            worker.start(ctx(self.ws, role="review"))

    def test_review_reuses_worktree_and_maps_skill(self):
        worker = self._worker()
        worker.start(ctx(self.ws))
        # A fix commit on the branch, then the review dispatch reuses
        # the worktree rather than re-adding it.
        _git(self.ws, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "--allow-empty", "-m", "fix")
        head = _git(self.ws, "rev-parse", "HEAD")
        worker.start(ctx(self.ws, role="review",
                         attempt="fk-task-000001-a03"))
        argv = self._create_argv()
        title = argv[argv.index("create") + 1]
        self.assertIn(" review ", f" {title} ")
        self.assertTrue(title.endswith("fk-task-000001-a03"))
        self.assertEqual(argv[argv.index("--skill") + 1],
                         "issue-pr-review")
        self.assertEqual(argv[argv.index("--assignee") + 1],
                         "fk-review")
        # The review body carries the diff under review.
        self.assertEqual(_git(self.ws, "rev-parse", "HEAD"), head)

    def test_review_body_carries_stat_and_capped_diff(self):
        captured = {}
        real = self.runner

        def spy(argv, env=None, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            if argv[0] == "hermes" and "create" in argv:
                captured["body"] = Path(
                    argv[argv.index("--body-file") + 1]).read_text()
            return real(argv, env=env, timeout=timeout, cwd=cwd)
        worker = self._worker()
        worker._runner = spy
        worker.start(ctx(self.ws))
        # An implementation change plus a lockfile churn, then review.
        Path(self.ws, "code.py").write_text("print('hi')\n")
        Path(self.ws, "package-lock.json").write_text(
            "\n".join(f'"dep-{i}": "1.0"' for i in range(50)))
        _git(self.ws, "-c", "user.email=t@t", "-c", "user.name=t",
             "add", "-A")
        _git(self.ws, "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-m", "work")
        worker.start(ctx(self.ws, role="review",
                         attempt="fk-task-000001-a03"))
        body = captured["body"]
        self.assertIn("## Change under review", body)
        self.assertIn("code.py", body)                 # the stat names it
        self.assertIn("```diff", body)
        self.assertIn("print('hi')", body)             # real change shown
        self.assertNotIn('"dep-10"', body)             # lockfile excluded
        base = worker._base_sha["gh:repo#7:g1"]
        head = _git(self.ws, "rev-parse", "HEAD")
        self.assertIn(f"git diff {base}...{head}", body)

    def test_no_profile_refuses(self):
        worker = HermesKanbanWorker(
            str(self.repo), FULL_NAME, {}, runner=self.runner,
            sleep=lambda s: None)
        with self.assertRaises(WorkerError):
            worker.start(ctx(self.ws))

    def test_skill_omitted_when_not_granted(self):
        worker = self._worker()
        worker.start(ctx(self.ws, skills={}))
        argv = self._create_argv()
        self.assertNotIn("--skill", argv)


class TestCollectMapping(unittest.TestCase):
    """Task status → lane verdict, with on_poll and the timeouts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.origin, self.repo = make_repo(self.tmp.name)
        self.runner = Runner(self.repo)
        self.ws = Path(self.tmp.name) / "ws"
        self.ws.mkdir()
        self.clock = [1000.0]
        self.polls = []

    def _worker(self):
        w = HermesKanbanWorker(
            str(self.repo), FULL_NAME, PROFILES,
            runner=self.runner,
            sleep=lambda s: self.clock.__setitem__(
                0, self.clock[0] + s),
            clock=lambda: self.clock[0],
            poll_interval_s=15.0, dispatch_grace_s=60.0,
            acceptance_commands=(),
            on_poll=lambda h: self.polls.append(h.attempt_id))
        return w

    def _dispatch(self, **kw):
        worker = self._worker()
        handle = worker.start(ctx(self.ws, **kw))
        return worker, handle

    def test_done_maps_completed(self):
        worker, handle = self._dispatch()
        self.runner.shows = [
            show("running"),
            show("done", summary="added the thing",
                 metadata={"head_sha": "abc", "acceptance": "passed"})]
        result = worker.collect(handle)
        self.assertEqual(result.verdict, "completed")
        self.assertIn("added the thing", result.detail)
        self.assertEqual(result.active_seconds, 5.0)
        self.assertIn("fk-task-000001-a01", self.polls)

    def test_done_review_verdict_from_metadata(self):
        worker, handle = self._dispatch()
        # Dispatch as review: reuse the impl worktree.
        worker2 = self._worker()
        rhandle = worker2.start(ctx(self.ws, role="review",
                                    attempt="fk-task-000001-a02"))
        self.runner.shows = [
            show("done", summary="approved: looks right",
                 metadata={"verdict": "approved",
                           "reviewed_sha": "abc", "findings": []})]
        result = worker2.collect(rhandle)
        self.assertEqual(result.verdict, "approved")

    def test_review_status_archives_then_maps(self):
        """``kanban_request_review`` (status ``review``) is archived
        first — Hermes's own review lane must not dispatch — then maps
        the same as ``done`` for the role."""
        worker, handle = self._dispatch()
        self.runner.shows = [show("review", summary="impl done")]
        result = worker.collect(handle)
        # An implementation-role handle still maps ``done``/``review``
        # to the role's terminal verdict.
        self.assertEqual(result.verdict, "completed")
        verbs = [a[a.index("kanban") + 1] for a, _ in self.runner.calls
                 if a[0] == "hermes"]
        self.assertIn("archive", verbs)

    def test_review_missing_verdict_fails(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show("done", summary="finished")]
        # Re-dispatch as review with metadata lacking a verdict.
        worker2 = self._worker()
        rhandle = worker2.start(ctx(self.ws, role="review",
                                    attempt="fk-task-000001-a02"))
        self.runner.shows = [show("done", summary="finished")]
        result = worker2.collect(rhandle)
        self.assertEqual(result.verdict, "failed")
        self.assertEqual(result.detail, "review-verdict-missing")

    def test_blocked_maps_reason(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show(
            "blocked",
            events=[{"kind": "blocked",
                     "payload": json.dumps(
                         {"reason": "needs the API key"})}])]
        result = worker.collect(handle)
        self.assertEqual(result.verdict, "blocked")
        self.assertIn("API key", result.detail)

    def test_archived_maps_failed(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show("archived")]
        result = worker.collect(handle)
        self.assertEqual(result.verdict, "failed")
        self.assertEqual(result.detail, "archived-externally")

    def test_not_dispatched_after_grace(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show("ready")]
        with self.assertRaises(WorkerError) as cm:
            worker.collect(handle)
        self.assertIn("not-dispatched:ready", str(cm.exception))
        # terminate ran: reclaim + block attempted on the task.
        verbs = [a[a.index("kanban") + 1] for a, _ in self.runner.calls
                 if a[0] == "hermes"]
        self.assertIn("reclaim", verbs)
        self.assertIn("block", verbs)

    def test_runtime_timeout_maps_failed(self):
        worker, handle = self._dispatch()
        # 60m default limit + 60s grace: enough polls of "running"
        # to cross the deadline.
        self.runner.shows = [show("running", runs=[{"elapsed_seconds": 1}])]
        result = worker.collect(handle)
        self.assertEqual(result.verdict, "failed")
        self.assertEqual(result.detail, "runtime-timeout")

    def test_terminate_terminal_first_needs_no_fencing(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show("blocked")]
        self.assertEqual(worker.terminate(handle), "confirmed")
        verbs = [a[a.index("kanban") + 1] for a, _ in self.runner.calls
                 if a[0] == "hermes"]
        self.assertEqual(verbs[-1:], ["show"])   # no reclaim/block

    def test_terminate_fences_then_confirms(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show("running"), show("blocked")]
        self.assertEqual(worker.terminate(handle), "confirmed")
        verbs = [a[a.index("kanban") + 1] for a, _ in self.runner.calls
                 if a[0] == "hermes"]
        self.assertEqual(verbs[-4:], ["show", "reclaim", "block",
                                     "show"])

    def test_terminate_tolerates_reclaim_failure(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show("running"), show("blocked")]
        real = self.runner

        def flaky(argv, env=None, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            if argv[0] == "hermes" and "reclaim" in argv:
                return Proc(stderr="no active claim", returncode=1)
            return real(argv, env=env, timeout=timeout, cwd=cwd)
        worker._runner = flaky
        self.assertEqual(worker.terminate(handle), "confirmed")
        verbs = [a[a.index("kanban") + 1] for a, _ in real.calls
                 if a[0] == "hermes"]
        self.assertEqual(verbs[-3:], ["show", "block", "show"])

    def test_terminate_uncertain_when_still_running(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show("running")]
        self.assertEqual(worker.terminate(handle), "uncertain")

    def test_terminate_uncertain_when_final_show_fails(self):
        worker, handle = self._dispatch()
        real = self.runner
        shows = [0]

        def half_dead(argv, env=None, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            if argv[0] == "hermes" and "show" in argv:
                shows[0] += 1
                if shows[0] > 1:
                    return Proc(stderr="gone", returncode=1)
                return Proc(json.dumps(show("running")))
            return real(argv, env=env, timeout=timeout, cwd=cwd)
        worker._runner = half_dead
        self.assertEqual(worker.terminate(handle), "uncertain")

    def test_terminate_uncertain_on_error(self):
        worker, handle = self._dispatch()

        def dying(argv, env=None, timeout=None, cwd=None):
            class P:
                returncode = 1
                stdout = ""
                stderr = "connection refused"
            return P()
        worker._runner = dying
        self.assertEqual(worker.terminate(handle), "uncertain")

    def test_result_for_cached(self):
        worker, handle = self._dispatch()
        self.runner.shows = [show("done", summary="done",
                                  metadata={"head_sha": "x"})]
        worker.collect(handle)
        info = worker.result_for("fk-task-000001-a01")
        self.assertEqual(info["kanban_task_id"], "t_0001")
        self.assertEqual(info["result"].verdict, "completed")
        self.assertEqual(info["metadata"], {"head_sha": "x"})

    def test_result_for_re_reads_list(self):
        """A restarted process has no cache — the task is located by
        its ``… <attempt_id>`` title and re-mapped from ``show``."""
        worker = self._worker()
        self.runner.lists = [[
            {"id": "t_77", "title": "fk o/r#7 review "
                                    "fk-task-000001-a02"}]]
        self.runner.shows = [
            show("done", summary="approved: ok",
                 metadata={"verdict": "approved",
                           "reviewed_sha": "b" * 40})]
        info = worker.result_for("fk-task-000001-a02")
        self.assertEqual(info["kanban_task_id"], "t_77")
        self.assertEqual(info["role"], "review")
        self.assertEqual(info["result"].verdict, "approved")
        self.assertEqual(info["metadata"]["reviewed_sha"], "b" * 40)

    def test_result_for_unknown_returns_none(self):
        worker = self._worker()
        self.runner.lists = [[]]
        self.assertIsNone(worker.result_for("fk-task-000001-a99"))

    def test_fix_attempt_body_carries_findings(self):
        worker = self._worker()
        worker._findings["gh:repo#7:g1"] = ["missing test", "typo"]
        captured = {}
        real = self.runner

        def spy(argv, env=None, timeout=None, cwd=None):
            argv = [str(a) for a in argv]
            if argv[0] == "hermes" and "create" in argv:
                captured["body"] = Path(
                    argv[argv.index("--body-file") + 1]).read_text()
            return real(argv, env=env, timeout=timeout, cwd=cwd)
        worker._runner = spy
        worker.start(ctx(self.ws, attempt="fk-task-000001-a02"))
        self.assertIn("## Reviewer findings to address",
                      captured["body"])
        self.assertIn("- missing test", captured["body"])
        self.assertIn("- typo", captured["body"])


if __name__ == "__main__":
    unittest.main()
