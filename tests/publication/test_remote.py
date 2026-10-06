#!/usr/bin/env python3
"""GhCliRemote argv contract — the production remote seam.

``cwd`` keeps ``git push <sha>:refs/heads/…`` inside the repository
whose object store holds the SHA; ``open_pr`` carries the ``Closes
#<issue>`` line when the broker bound the work's issue into the effect
identity. Everything is asserted on argv — no network.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.publication.remote import (  # noqa: E402
    GhCliRemote, RemoteError)

REPO = "luongnv89/factory-kit-website"
REPO_ID = "R_kgDOx"
SHA = "a" * 40


class Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class Runner:
    def __init__(self, respond=None):
        self.calls = []
        self.respond = respond or {}

    def __call__(self, argv, cwd=None, timeout=None):
        argv = [str(a) for a in argv]
        self.calls.append({"argv": argv, "cwd": cwd})
        key = " ".join(argv[:2])
        out = self.respond.get(key, self.respond.get(argv[1], "{}"))
        return Proc(out if isinstance(out, str) else json.dumps(out))


class TestGhCliRemote(unittest.TestCase):

    def _remote(self, runner=None, cwd=None):
        return GhCliRemote(REPO_ID, REPO, cwd=cwd,
                           runner=runner or Runner())

    def test_push_uses_configured_cwd_and_https_remote(self):
        runner = Runner()
        remote = self._remote(runner, cwd="/repo/root")
        remote.publish_branch("fk/issue-7-g1", SHA, {})
        call = runner.calls[0]
        self.assertEqual(call["cwd"], "/repo/root")
        argv = call["argv"]
        self.assertEqual(argv[:2], ["git", "push"])
        self.assertEqual(
            argv[2], f"https://github.com/{REPO}.git")
        self.assertEqual(argv[3],
                         f"{SHA}:refs/heads/fk/issue-7-g1")

    def test_open_pr_body_closes_issue(self):
        runner = Runner()
        runner.respond["gh pr"] = \
            "https://github.test/o/r/pull/17\n"
        remote = self._remote(runner)
        out = remote.open_pr("fk/issue-7-g1", "main", "t (#7)",
                             {"intent_id": "pi-1", "work_key": "w",
                              "authority_key": "a", "generation": 1,
                              "actor_ref": "github:x", "issue": 7})
        self.assertEqual(out["number"], "17")
        argv = runner.calls[0]["argv"]
        body = argv[argv.index("--body") + 1]
        self.assertIn("Closes #7", body)
        self.assertIn("pi-1", body)
        self.assertEqual(argv[argv.index("--repo") + 1], REPO)
        self.assertEqual(argv[argv.index("--head") + 1],
                         "fk/issue-7-g1")
        self.assertEqual(argv[argv.index("--base") + 1], "main")

    def test_open_pr_without_issue_omits_closes(self):
        runner = Runner()
        runner.respond["gh pr"] = \
            "https://github.test/o/r/pull/18\n"
        remote = self._remote(runner)
        remote.open_pr("b", "main", "t",
                       {"intent_id": "pi-2", "work_key": "w",
                        "authority_key": "a", "generation": 1,
                        "actor_ref": "github:x"})
        argv = runner.calls[0]["argv"]
        body = argv[argv.index("--body") + 1]
        self.assertNotIn("Closes #", body)

    def test_read_pr_maps_merge_guard_fields(self):
        runner = Runner({"gh pr": {
            "number": 5, "url": "u", "state": "OPEN",
            "isDraft": False, "headRefOid": SHA,
            "headRefName": "b", "baseRefName": "main",
            "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
            "autoMergeRequest": None,
            "mergeCommit": None, "mergedBy": None}})
        pr = self._remote(runner).read_pr("5")
        self.assertEqual(pr["head"], SHA)
        self.assertFalse(pr["auto_merge"])
        self.assertFalse(pr["merged"])
        self.assertIsNone(pr["merge_sha"])

    def test_actor_identity(self):
        runner = Runner({"gh api": {"login": "luongnv89"}})
        self.assertEqual(self._remote(runner).actor_identity()
                         ["login"], "luongnv89")

    def test_nonzero_exit_is_remote_error(self):
        runner = Runner()
        runner.respond["gh repo"] = Proc("", "gh: not found", 1)
        remote = GhCliRemote(REPO_ID, REPO,
                             runner=lambda a, cwd=None, timeout=None:
                             Proc("", "denied", 1))
        with self.assertRaises(RemoteError):
            remote.repository_identity()

    def test_retry_after_parsed_from_stderr(self):
        runner = lambda a, cwd=None, timeout=None: Proc(
            "", "API rate limit — retry after 42 seconds", 1)
        remote = GhCliRemote(REPO_ID, REPO, runner=runner)
        try:
            remote.repository_identity()
        except RemoteError as exc:
            self.assertEqual(exc.retry_after, 42.0)
        else:
            self.fail("expected RemoteError")


if __name__ == "__main__":
    unittest.main()
