#!/usr/bin/env python3
"""GitHubPagesPreview against a real bare "origin" + clone.

``git`` calls execute for real — the push URL is rewritten to the
local bare origin so a publish actually lands and ``git show
gh-pages:…`` reads it back. ``gh api`` and ``curl`` are scripted
through the same injected runner; the curl stub serves whatever the
pushed gh-pages branch contains, so a 404 is real, not a fixture
opinion. The build command is a ``python3`` one-liner that writes
``dist/index.html`` carrying the smoke marker + ``$SITE_BASE``.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.preview.pages import GitHubPagesPreview  # noqa: E402
from factory_kit.preview.port import (  # noqa: E402
    PreviewAmbiguity, PreviewError)

FULL_NAME = "owner/pages-repo"
HTML_URL = "https://owner.github.io/pages-repo/"
MARKER = "factory-kit-smoke"
IDENTITY = {"preview_id": "prev-1", "work_key": "smoke",
            "generation": 0}

BUILD = ("python3 -c \"import os; os.makedirs('dist'); "
         "open('dist/index.html', 'w').write("
         "'<meta name=" + MARKER + ">' + os.environ['SITE_BASE'])\""
         )


def _git(cwd, *argv, check=True):
    proc = subprocess.run(["git", "-C", str(cwd), *argv],
                          capture_output=True, text=True)
    if check:
        assert proc.returncode == 0, (argv, proc.stderr)
    return proc.stdout.strip()


class Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def make_repo(tmp):
    """Bare origin with ``main`` (one file commit) and ``gh-pages``
    (``.nojekyll`` + placeholder), plus a working clone."""
    origin = Path(tmp) / "origin.git"
    seed = Path(tmp) / "seed"
    repo = Path(tmp) / "repo"
    subprocess.run(["git", "init", "--bare", str(origin)],
                   capture_output=True, check=True)
    subprocess.run(["git", "init", str(seed)],
                   capture_output=True, check=True)
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "app.py").write_text("print('x')\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    _git(seed, "branch", "-M", "main")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    _git(seed, "checkout", "--orphan", "gh-pages")
    _git(seed, "rm", "-rf", ".")
    (seed / ".nojekyll").write_text("")
    (seed / "index.html").write_text("<h1>placeholder</h1>\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "pages placeholder")
    _git(seed, "push", "origin", "gh-pages")
    _git(seed, "checkout", "main")
    subprocess.run(["git", "clone", str(origin), str(repo)],
                   capture_output=True, check=True)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return origin, repo


class Runner:
    """The single argv boundary. ``git`` is real (the github push URL
    rewrites to the bare origin); ``gh api`` answers from live repo
    state — the pages/builds rows resolve ``"TIP"`` to the current
    gh-pages tip so the simulated Pages build tracks real pushes;
    ``curl`` serves pushed gh-pages contents (a 404 is real)."""

    def __init__(self, origin, repo):
        self.origin = str(origin)
        self.repo = str(repo)
        self.calls = []
        self.build_env = None
        self.pages = {"html_url": HTML_URL,
                      "source": {"branch": "gh-pages", "path": "/"}}
        self.builds = "tip"          # "tip" | list of row templates
        self.fail_push = 0           # next N gh-pages pushes reject NFF
        self.serve = True            # curl serving switch
        self.pushes = 0

    def _tip(self):
        return _git(self.origin, "rev-parse", "gh-pages",
                    check=False) or None

    def _show(self, path):
        proc = subprocess.run(
            ["git", "-C", self.origin, "show", f"gh-pages:{path}"],
            capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else None

    def _contents(self, endpoint):
        path_ep = endpoint.split("?", 1)[0]
        suffix = path_ep.split("/contents/", 1)[1]
        if suffix == "previews":
            proc = subprocess.run(
                ["git", "-C", self.origin, "ls-tree",
                 "gh-pages:previews"],
                capture_output=True, text=True)
            if proc.returncode != 0:
                return Proc("", "404 Not Found", 1)
            rows = []
            for line in proc.stdout.splitlines():
                meta, name = line.split("\t", 1)
                _mode, typ, sha = meta.split()
                rows.append({"name": name,
                             "type": "dir" if typ == "tree" else "file",
                             "sha": sha})
            return Proc(json.dumps(rows))
        if suffix.endswith("factory-preview.json"):
            body = self._show(suffix)
            if body is None:
                return Proc("", "404 Not Found", 1)
            return Proc(json.dumps({
                "name": "factory-preview.json", "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(
                    body.encode()).decode(),
                "sha": "f" * 40}))
        return Proc("", "404 Not Found", 1)

    def _commits(self, endpoint):
        path = endpoint.split("path=", 1)[1].split("&", 1)[0]
        sha = _git(self.origin, "rev-list", "-1", "gh-pages", "--",
                   path, check=False)
        return Proc(json.dumps([{"sha": sha}] if sha else []))

    def _gh(self, argv):
        endpoint = argv[2]
        if "/pages/builds" in endpoint:
            if self.builds == "tip":
                tip = self._tip()
                rows = ([{"status": "built", "commit": tip}]
                        if tip else [])
            else:
                tip = self._tip()
                rows = [{**row,
                         "commit": (tip if row.get("commit") == "TIP"
                                    else row.get("commit"))}
                        for row in self.builds]
            return Proc(json.dumps(rows))
        if endpoint.endswith("/pages"):
            return Proc(json.dumps(self.pages))
        if "/contents/" in endpoint:
            return self._contents(endpoint)
        if "/commits" in endpoint:
            return self._commits(endpoint)
        return Proc("", f"unexpected endpoint {endpoint}", 1)

    def _curl(self, argv):
        url = argv[-1]
        if not self.serve or not url.startswith(HTML_URL):
            return Proc("", "curl: (22) 404", 22)
        path = url[len(HTML_URL):]
        if path.endswith("/"):
            path += "index.html"        # Pages serves the dir's index
        body = self._show(path)
        if body is None:
            return Proc("", "curl: (22) 404", 22)
        if "-o" in argv:            # the contract's -o /dev/null probe
            return Proc("200")
        return Proc(body)

    def __call__(self, argv, env=None, timeout=None, cwd=None):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        if argv[0] == "git":
            if "push" in argv and self.fail_push and any(
                    "refs/heads/gh-pages" in a for a in argv):
                self.fail_push -= 1
                self.pushes += 1
                return Proc("", "! [rejected] gh-pages "
                                "(non-fast-forward)", 1)
            if "push" in argv:
                self.pushes += 1
            argv = [self.origin
                    if a.startswith("https://github.com/") else a
                    for a in argv]
        elif argv[0] == "gh":
            return self._gh(argv)
        elif argv[0] == "curl":
            return self._curl(argv)
        else:
            # build commands run for real — env is the minimal set the
            # port supplies, asserted by the tests.
            self.build_env = dict(env or {})
        proc = subprocess.run(argv, capture_output=True, text=True,
                              env=env, timeout=timeout, cwd=cwd)
        return Proc(proc.stdout, proc.stderr, proc.returncode)


class PagesWorld(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.origin, self.repo = make_repo(self.tmp.name)
        self.runner = Runner(self.origin, self.repo)
        self.clock = [1_000.0]
        self.head = _git(self.repo, "rev-parse", "main")

    def _port(self, **kw):
        kw.setdefault("runner", self.runner)
        kw.setdefault("now", lambda: self.clock[0])
        kw.setdefault("sleep",
                      lambda s: self.clock.__setitem__(
                          0, self.clock[0] + s))
        kw.setdefault("pages_wait_s", 50)
        kw.setdefault("serve_wait_s", 30)
        kw.setdefault("poll_interval_s", 5)
        kw.setdefault("worktree_dir", str(
            Path(self.tmp.name) / "pages-wt"))
        kw.setdefault("build_commands", [BUILD])
        kw.setdefault("output_dir", "dist")
        kw.setdefault("base_env", "SITE_BASE")
        return GitHubPagesPreview(
            repo_root=str(self.repo), full_name=FULL_NAME, **kw)

    def _deploy(self, port=None, identity=None):
        port = port or self._port()
        return port.deploy({"head_sha": self.head},
                           identity or IDENTITY)

    def _pushed_files(self, path=""):
        proc = subprocess.run(
            ["git", "-C", str(self.origin), "ls-tree", "-r",
             "--name-only", f"gh-pages:{path}" if path else "gh-pages"],
            capture_output=True, text=True)
        return proc.stdout.split()


class TestDeploy(PagesWorld):

    def test_default_runner_captures_output(self):
        """Regression from the live smoke: a bare ``subprocess.run``
        inherits stdout — ``proc.stdout`` comes back ``None`` and every
        ``_run`` answer silently parses ``{}``. The default seam must
        capture."""
        from factory_kit.preview.port import _default_runner
        port = GitHubPagesPreview(repo_root=str(self.repo),
                                  full_name=FULL_NAME,
                                  worktree_dir="/tmp/x")
        self.assertIs(port._runner, _default_runner)
        proc = _default_runner(["python3", "-c", "print('x')"])
        self.assertEqual(proc.stdout.strip(), "x")

    def test_deploy_happy_path(self):
        result = self._deploy()
        self.assertTrue(result["deployment_id"].startswith("prev-1@"))
        self.assertEqual(
            result["url"], HTML_URL + "previews/prev-1/")
        self.assertEqual(result["head_sha"], self.head)
        self.assertEqual(result["state"], "READY")
        self.assertTrue(result["owned"])
        # The build saw the base env var and only the minimal env.
        self.assertEqual(self.runner.build_env["SITE_BASE"],
                         "/pages-repo/previews/prev-1/")
        self.assertEqual(
            set(self.runner.build_env) -
            {"PATH", "HOME", "TMPDIR", "LANG", "SITE_BASE"}, set())
        # The pushed gh-pages commit carries the preview tree, the
        # identity file and the root .nojekyll.
        files = self._pushed_files()
        self.assertIn(".nojekyll", files)
        self.assertIn("previews/prev-1/index.html", files)
        self.assertIn("previews/prev-1/factory-preview.json", files)
        meta = json.loads(self.runner._show(
            "previews/prev-1/factory-preview.json"))
        self.assertEqual(meta["preview_id"], "prev-1")
        self.assertEqual(meta["work_key"], "smoke")
        self.assertEqual(meta["head_sha"], self.head)
        self.assertEqual(
            meta["tree_sha"],
            _git(self.repo, "rev-parse", f"{self.head}^{{tree}}"))
        html = self.runner._show("previews/prev-1/index.html")
        self.assertIn(MARKER, html)
        self.assertIn("/pages-repo/previews/prev-1/", html)

    def test_provider_name(self):
        self.assertEqual(self._port().provider_name(), "github-pages")

    def test_invalid_preview_id(self):
        with self.assertRaises(PreviewError):
            self._deploy(identity={"preview_id": "bad/../x"})
        with self.assertRaises(PreviewError):
            self._deploy(identity={"preview_id": "x" * 65})

    def test_pages_not_configured(self):
        self.runner.pages["source"] = {"branch": "main", "path": "/"}
        with self.assertRaisesRegex(PreviewError,
                                    "pages-not-configured"):
            self._deploy()

    def test_build_failure_names_command(self):
        port = self._port(build_commands=[
            "python3 -c \"import sys; sys.exit(3)\""])
        with self.assertRaisesRegex(PreviewError, "sys.exit"):
            port.deploy({"head_sha": self.head}, IDENTITY)

    def test_missing_output_dir_refuses(self):
        port = self._port(build_commands=["python3 -c \"pass\""])
        with self.assertRaisesRegex(PreviewError, "index.html"):
            port.deploy({"head_sha": self.head}, IDENTITY)

    def test_non_fast_forward_retries_once(self):
        self.runner.fail_push = 1
        result = self._deploy()
        self.assertEqual(result["state"], "READY")
        self.assertEqual(self.runner.pushes, 2)
        self.assertIn("previews/prev-1/index.html",
                      self._pushed_files())

    def test_second_non_ff_fails_clean(self):
        self.runner.fail_push = 2
        with self.assertRaises(PreviewError):
            self._deploy()
        self.assertNotIn("previews/prev-1/index.html",
                         self._pushed_files())

    def test_pages_build_errored(self):
        self.runner.builds = [{"status": "errored", "commit": "TIP",
                               "error": "jekyll blew up"}]
        with self.assertRaisesRegex(PreviewError, "errored"):
            self._deploy()

    def test_pages_build_never_observed_is_ambiguous(self):
        self.runner.builds = []
        with self.assertRaises(PreviewAmbiguity):
            self._deploy()
        self.assertIn("previews/prev-1/index.html",
                      self._pushed_files())   # effect may have landed

    def test_building_then_built_waits(self):
        calls = [0]

        class Flapping(Runner):
            def _gh(self, argv):
                if "/pages/builds" in argv[2]:
                    calls[0] += 1
                    status = "building" if calls[0] == 1 else "built"
                    return Proc(json.dumps(
                        [{"status": status, "commit": self._tip()}]))
                return super()._gh(argv)
        self.runner.__class__ = Flapping
        result = self._deploy()
        self.assertEqual(result["state"], "READY")
        self.assertGreaterEqual(calls[0], 2)

    def test_serve_timeout_is_ambiguous(self):
        self.runner.serve = False
        with self.assertRaises(PreviewAmbiguity):
            self._deploy()

    def test_worktree_inside_repo_refused(self):
        port = self._port(worktree_dir=str(
            Path(self.repo) / "wt-inside"))
        with self.assertRaisesRegex(PreviewError, "inside"):
            port.deploy({"head_sha": self.head}, IDENTITY)


class TestInspectFindRemove(PagesWorld):

    def test_inspect_unknown_returns_none(self):
        self.assertIsNone(self._port().inspect("prev-9@abc"))

    def test_inspect_unowned(self):
        """An identity file naming a different preview_id is not
        ours — inspect answers owned=False."""
        # Land a foreign preview dir directly on gh-pages.
        seed = Path(self.tmp.name) / "seed"
        _git(seed, "checkout", "gh-pages")
        foreign = seed / "previews" / "other-1"
        foreign.mkdir(parents=True)
        (foreign / "factory-preview.json").write_text(json.dumps(
            {"preview_id": "not-other-1", "work_key": "w",
             "generation": 0, "head_sha": "x", "tree_sha": "y",
             "created_at": "z"}))
        _git(seed, "add", "-A")
        _git(seed, "commit", "-m", "foreign")
        _git(seed, "push", "origin", "gh-pages")
        _git(seed, "checkout", "main")
        observed = self._port().inspect("other-1@abc")
        self.assertIsNotNone(observed)
        self.assertFalse(observed["owned"])

    def test_find_deployments_matches_identity(self):
        deployed = self._deploy()
        found = self._port().find_deployments(
            {"work_key": "smoke", "generation": 0})
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["owned"])
        self.assertTrue(found[0]["deployment_id"]
                        .startswith("prev-1@"))
        found = self._port().find_deployments(
            {"work_key": "other", "generation": 0})
        self.assertEqual(found, [])

    def test_smoke_contract(self):
        deployed = self._deploy()
        result = self._port().smoke(
            deployed["deployment_id"],
            {"command": "curl -fsS -o /dev/null -w '%{http_code}' "
                        "{url}", "expect": "200",
             "marker": MARKER})
        self.assertTrue(result["ok"])
        self.assertEqual(result["http"], "200")
        self.assertTrue(result["marker"])

    def test_remove(self):
        deployed = self._deploy()
        out = self._port().remove(deployed["deployment_id"], IDENTITY)
        self.assertEqual(out, {"removed": True})
        self.assertNotIn("previews/prev-1/index.html",
                         self._pushed_files())
        self.assertIsNone(
            self._port().inspect(deployed["deployment_id"]))
        again = self._port().remove(deployed["deployment_id"],
                                    IDENTITY)
        self.assertTrue(again["already_gone"])

    def test_remove_unowned_refuses(self):
        seed = Path(self.tmp.name) / "seed"
        _git(seed, "checkout", "gh-pages")
        foreign = seed / "previews" / "other-2"
        foreign.mkdir(parents=True)
        (foreign / "factory-preview.json").write_text(json.dumps(
            {"preview_id": "foreign-owned", "work_key": "w",
             "generation": 0, "head_sha": "x", "tree_sha": "y",
             "created_at": "z"}))
        _git(seed, "add", "-A")
        _git(seed, "commit", "-m", "foreign")
        _git(seed, "push", "origin", "gh-pages")
        _git(seed, "checkout", "main")
        port = self._port()
        with self.assertRaisesRegex(PreviewError, "unowned"):
            port.remove("other-2@abc", IDENTITY)
        self.assertIn("previews/other-2/factory-preview.json",
                      self._pushed_files())


if __name__ == "__main__":
    unittest.main()
