#!/usr/bin/env python3
"""VercelCliPreview against the probed Vercel CLI 55 shapes.

The deploy contract is asserted end-to-end: an immutable ``git
archive`` export of exactly the bound head (a dirty worktree can never
leak in), ``deploy --yes --format json`` with ``factory-*`` metadata,
token by env-name only, and both read-back surfaces (``vercel api``
and ``vercel inspect --format json``). ``ls`` must use ``--format
json`` — the probe proved ``--json`` is rejected by v55.
"""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.preview.port import (  # noqa: E402
    PreviewAmbiguity, PreviewError, VercelCliPreview)

HEAD = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"


class Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def make_repo(tmp):
    """A real git repo with one committed file plus an uncommitted
    dirty file — the export must carry only the committed tree."""
    repo = Path(tmp) / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t",
               GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")

    def git(*argv):
        return subprocess.run(["git", "-C", str(repo), *argv],
                              capture_output=True, text=True,
                              env=env, check=True).stdout.strip()
    git("init", "-b", "main")
    (repo / "index.html").write_text("<h1>committed</h1>")
    git("add", "index.html")
    git("commit", "-m", "init")
    head = git("rev-parse", "HEAD")
    (repo / "secret.txt").write_text("dirty worktree data")
    return repo, head


class Runner:
    """Routes ``git`` to the real repo and scripts ``vercel``. Records
    every call's argv/env/cwd so the export + env contract is
    asserted, not assumed."""

    def __init__(self, deploy_out=None, inspect_out=None,
                 ls_out=None, dep_id="dpl-1"):
        self.calls = []
        self.deploy_out = deploy_out
        self.inspect_out = inspect_out
        self.ls_out = ls_out if ls_out is not None else "[]"
        self.dep_id = dep_id
        self.deploy_cwd_files = None

    def __call__(self, argv, env=None, timeout=None, cwd=None):
        argv = [str(a) for a in argv]
        self.calls.append({"argv": argv, "env": dict(env or {}),
                           "cwd": cwd})
        if argv[0] == "git":
            proc = subprocess.run(argv, capture_output=True,
                                  text=True, cwd=cwd)
            return Proc(proc.stdout, proc.stderr, proc.returncode)
        verb = argv[1]
        if verb == "deploy":
            # The deploy cwd is the immutable export — snapshot its
            # file list so the test can prove the dirty file stayed out.
            self.deploy_cwd_files = sorted(
                str(p.relative_to(cwd))
                for p in Path(cwd).rglob("*") if p.is_file())
            out = self.deploy_out
            if out is None:
                out = json.dumps({"status": "success",
                                  "deployment": {"id": self.dep_id,
                                                 "url": "v.test"},
                                  "message": "ok", "next": []})
            return Proc(out if isinstance(out, str) else
                        json.dumps(out))
        if verb == "api":
            out = self.inspect_out
            if out is None:
                out = {"id": self.dep_id, "url": "v.test",
                       "readyState": "READY",
                       "meta": {}}
            return Proc(out if isinstance(out, str) else
                        json.dumps(out))
        if verb == "inspect":
            out = self.inspect_out
            if out is None:
                out = {"id": self.dep_id, "url": "v.test",
                       "readyState": "READY"}
            return Proc(out if isinstance(out, str) else
                        json.dumps(out))
        if verb == "ls":
            return Proc(self.ls_out)
        if verb == "rm":
            return Proc("")
        if verb == "list":                       # `vercel api` list
            return Proc("[]")
        return Proc("")


class TestDeploy(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo, self.head = make_repo(self.tmp.name)

    def _port(self, runner, **kw):
        kw.setdefault("inspect_source", "api")
        return VercelCliPreview(str(self.repo), project="proj-x",
                                project_id="prj_1", org_id="team_1",
                                runner=runner, **kw)

    def test_deploy_argv_and_env(self):
        runner = Runner()
        os.environ["VERCEL_TOKEN"] = "tok-123"
        try:
            out = self._port(runner).deploy(
                {"head_sha": self.head},
                {"preview_id": "p1", "work_key": "w1",
                 "generation": 1})
        finally:
            os.environ.pop("VERCEL_TOKEN", None)
        self.assertEqual(out["deployment_id"], "dpl-1")
        deploy = [c for c in runner.calls
                  if c["argv"][:2] == ["vercel", "deploy"]][0]
        argv = deploy["argv"]
        self.assertIn("--yes", argv)
        self.assertEqual(argv[argv.index("--format") + 1], "json")
        meta = [argv[i + 1] for i, a in enumerate(argv) if a == "-m"]
        self.assertIn("factory-preview_id=p1", meta)
        self.assertIn("factory-work_key=w1", meta)
        self.assertIn(f"factory-head_sha={self.head}", meta)
        self.assertTrue(any(m.startswith("factory-tree_sha=")
                            for m in meta))
        self.assertNotIn("--name", argv)
        # env: HOME/VERCEL_* present, token by name only.
        self.assertIn("HOME", deploy["env"])
        self.assertEqual(deploy["env"]["VERCEL_ORG_ID"], "team_1")
        self.assertEqual(deploy["env"]["VERCEL_PROJECT_ID"], "prj_1")
        self.assertEqual(deploy["env"]["VERCEL_TOKEN"], "tok-123")
        for c in runner.calls:
            self.assertNotIn("tok-123", " ".join(c["argv"]))
        # deploy ran inside the export dir, not the repo.
        self.assertNotEqual(str(deploy["cwd"]), str(self.repo))
        self.assertTrue(str(deploy["cwd"]).startswith(
            tempfile.gettempdir()))

    def test_export_is_immutable_head_tree(self):
        """The export contains exactly the committed tree — the dirty
        worktree file cannot leak into the deployment."""
        runner = Runner()
        self._port(runner).deploy({"head_sha": self.head},
                                  {"preview_id": "p1"})
        self.assertEqual(runner.deploy_cwd_files, ["index.html"])

    def test_deploy_plain_json_unwrapped(self):
        """Bare ``{id,url}`` output (non-interactive mode) parses."""
        runner = Runner(
            dep_id="dpl-plain",
            deploy_out=json.dumps({"id": "dpl-plain",
                                   "url": "plain.test"}))
        out = self._port(runner).deploy({"head_sha": self.head},
                                        {"preview_id": "p1"})
        self.assertEqual(out["deployment_id"], "dpl-plain")

    def test_deploy_error_json_raises(self):
        runner = Runner(deploy_out=json.dumps(
            {"status": "error", "reason": "bad_token",
             "message": "nope"}))
        with self.assertRaises(PreviewError):
            self._port(runner).deploy({"head_sha": self.head},
                                      {"preview_id": "p1"})

    def test_deploy_no_id_is_ambiguous(self):
        runner = Runner(deploy_out=json.dumps({"status": "success"}))
        with self.assertRaises(PreviewAmbiguity):
            self._port(runner).deploy({"head_sha": self.head},
                                      {"preview_id": "p1"})

    def test_head_mismatch_on_readback_is_ambiguous(self):
        runner = Runner(inspect_out={"id": "dpl-1", "url": "v.test",
                                     "readyState": "READY",
                                     "meta": {"factory-head_sha":
                                              "b" * 40}})
        with self.assertRaises(PreviewAmbiguity):
            self._port(runner).deploy({"head_sha": self.head},
                                      {"preview_id": "p1"})

    def test_temp_export_removed(self):
        runner = Runner()
        self._port(runner).deploy({"head_sha": self.head},
                                  {"preview_id": "p1"})
        deploy = [c for c in runner.calls
                  if c["argv"][:2] == ["vercel", "deploy"]][0]
        self.assertFalse(Path(deploy["cwd"]).exists())


class TestInspectAndList(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo, self.head = make_repo(self.tmp.name)

    def test_api_inspect_maps_fields(self):
        runner = Runner(inspect_out={
            "id": "dpl-9", "url": "p.test", "readyState": "READY",
            "meta": {"factory-head_sha": self.head,
                     "factory-tree_sha": "t1",
                     "factory-preview_id": "p1"}})
        port = VercelCliPreview(str(self.repo), runner=runner)
        out = port.inspect("dpl-9")
        self.assertEqual(out["deployment_id"], "dpl-9")
        self.assertEqual(out["url"], "https://p.test")
        self.assertEqual(out["head_sha"], self.head)
        self.assertEqual(out["artifact_identity"], "t1")
        self.assertEqual(out["state"], "READY")
        self.assertTrue(out["owned"])
        api = [c for c in runner.calls
               if c["argv"][:2] == ["vercel", "api"]][0]
        self.assertIn("/v13/deployments/dpl-9", api["argv"])

    def test_cli_inspect_maps_fields(self):
        runner = Runner(inspect_out={
            "id": "dpl-9", "url": "p.test", "readyState": "READY",
            "meta": {"factory-head_sha": self.head,
                     "factory-preview_id": "p1"}})
        port = VercelCliPreview(str(self.repo), runner=runner,
                                inspect_source="cli")
        out = port.inspect("dpl-9")
        self.assertEqual(out["deployment_id"], "dpl-9")
        self.assertTrue(out["owned"])
        ins = [c for c in runner.calls
               if c["argv"][:2] == ["vercel", "inspect"]][0]
        self.assertIn("--format", ins["argv"])

    def test_inspect_404_returns_none(self):
        def runner(argv, env=None, timeout=None, cwd=None):
            return Proc("", "Error: 404 not found", 1)
        port = VercelCliPreview(str(self.repo), runner=runner)
        self.assertIsNone(port.inspect("dpl-x"))

    def test_ls_uses_format_json_not_dashdash_json(self):
        rows = [{"uid": "dpl-1", "url": "v.test",
                 "state": "READY",
                 "meta": {"factory-preview_id": "p1",
                          "factory-head_sha": self.head}}]
        runner = Runner(ls_out=json.dumps(rows))
        port = VercelCliPreview(str(self.repo), project="proj-x",
                                runner=runner)
        found = port.find_deployments({"preview_id": "p1"})
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["deployment_id"], "dpl-1")
        ls = [c for c in runner.calls
              if c["argv"][:2] == ["vercel", "ls"]][0]
        self.assertIn("--format", ls["argv"])
        self.assertNotIn("--json", ls["argv"])
        self.assertIn("proj-x", ls["argv"])
        self.assertIn("factory-preview_id=p1", ls["argv"])

    def test_ls_filters_unowned(self):
        rows = [{"uid": "dpl-1",
                 "meta": {"factory-preview_id": "p1"}},
                {"uid": "dpl-2",
                 "meta": {"factory-preview_id": "other"}}]
        runner = Runner(ls_out=json.dumps(rows))
        port = VercelCliPreview(str(self.repo), runner=runner)
        found = port.find_deployments({"preview_id": "p1"})
        self.assertEqual([d["deployment_id"] for d in found],
                         ["dpl-1"])

    def test_remove_uses_rm_yes(self):
        runner = Runner(inspect_out={
            "id": "dpl-1", "url": "v.test", "readyState": "READY",
            "meta": {"factory-preview_id": "p1"}})
        port = VercelCliPreview(str(self.repo), runner=runner)
        out = port.remove("dpl-1", {"preview_id": "p1"})
        self.assertTrue(out["removed"])
        rm = [c for c in runner.calls
              if c["argv"][:2] == ["vercel", "rm"]][0]
        self.assertIn("dpl-1", rm["argv"])
        self.assertIn("--yes", rm["argv"])


if __name__ == "__main__":
    unittest.main()
