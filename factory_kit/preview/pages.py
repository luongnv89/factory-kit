#!/usr/bin/env python3
"""GitHub Pages preview provider — sub-path previews on ``gh-pages``.

The repo serves Pages from branch ``gh-pages`` at path ``/``. Each
preview is one directory ``previews/<preview_id>/`` on that branch:
the controller exports the exact verified head (``git archive``,
never a dirty worktree), runs the manifest-declared build commands
with ``<base_env>`` set to the preview's sub-path so built asset URLs
resolve under it, commits the output plus an identity file
(``factory-preview.json``) and pushes to ``refs/heads/gh-pages``.

Trust model — building on the operator host is accepted under the
kit's local trust model: workers already run the repo's npm scripts
on this host during acceptance, and a preview deploy only happens
after verified evidence for the exact head. The build still gets a
*minimal* environment — PATH, HOME and TMPDIR/LANG when set, plus the
declared base env var — never the full ``os.environ``.

Ownership: the ``previews/<id>/`` directory is bound to the recorded
identity by ``factory-preview.json``; a directory whose identity file
is absent or names a different preview_id is *not* factory-owned and
is never removed (A5). The gh-pages worktree is kit-owned state — a
``worktree_dir`` resolving inside the repo's main working tree is
refused rather than risk resetting operator files.
"""

from __future__ import annotations

import base64
import json as _json
import os
import re
import shlex
import shutil
import subprocess
import time
from urllib.parse import urlparse

from .port import (  # noqa: F401
    PreviewAmbiguity,
    PreviewError,
    PreviewPort,
    _IDENTITY_META_KEYS,
    _contract_smoke,
    _export_head,
    _tree_sha,
)

__all__ = ["GitHubPagesPreview"]

#: Directory names Pages publishes under ``previews/`` — the
#: deployment id embeds ``<preview_id>@<pages commit[:12]>``.
_PREVIEW_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

#: ``git push`` rejection markers that mean "another writer won" —
#: worth one re-sync + republish, never a blind force-push.
_NON_FF_MARKERS = ("non-fast-forward", "fetch first", "[rejected]",
                   "stale info")


class GitHubPagesPreview(PreviewPort):
    """``gh api`` + ``git`` adapter for gh-pages sub-path previews.

    One injectable ``runner`` boundary receives ``(argv, env, timeout,
    cwd)``; every ``gh``/``git``/``curl``/build call crosses it, so
    tests replay live shapes without touching the network. The push
    uses the HTTPS remote — credentials come from the ambient ``gh``
    credential helper, never from argv or env.
    """

    def __init__(self, repo_root=None, full_name=None, worktree_dir=None,
                 build_commands=(), output_dir=None, base_env=None, *,
                 git_bin="git", gh_bin="gh", curl_bin="curl",
                 timeout_s=120, build_timeout_s=900, pages_wait_s=600,
                 serve_wait_s=180, poll_interval_s=10,
                 runner=None, sleep=None, now=None):
        self.repo_root = repo_root
        self.full_name = full_name
        self.worktree_dir = worktree_dir
        self.build_commands = tuple(build_commands or ())
        self.output_dir = output_dir
        self.base_env = base_env
        self.git_bin = git_bin
        self.gh_bin = gh_bin
        self.curl_bin = curl_bin
        self.timeout_s = timeout_s
        self.build_timeout_s = build_timeout_s
        self.pages_wait_s = pages_wait_s
        self.serve_wait_s = serve_wait_s
        self.poll_interval_s = poll_interval_s
        self._runner = runner or subprocess.run
        self._sleep = sleep or time.sleep
        self._now = now or time.time
        self._site_url = None

    def provider_name(self) -> str:
        return "github-pages"

    # -- argv boundary ---------------------------------------------------

    def _env(self, extra=None):
        """Minimal subprocess environment — PATH for binaries, HOME so
        ``gh``/``git`` find their config+credentials, TMPDIR/LANG when
        the host sets them, plus the declared base env var on builds."""
        env = {"PATH": os.environ.get("PATH", ""),
               "HOME": os.environ.get("HOME", "")}
        for key in ("TMPDIR", "LANG"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        if extra:
            env.update(extra)
        return env

    def _run(self, argv, *, env=None, timeout=None, cwd=None):
        argv = [str(a) for a in argv]
        try:
            proc = self._runner(argv, env=self._env(env),
                                timeout=timeout or self.timeout_s,
                                cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise PreviewAmbiguity(
                f"{argv[0]} timed out: {exc}") from exc
        except OSError as exc:
            raise PreviewError(f"{argv[0]}: {exc}") from exc
        if getattr(proc, "returncode", 1) != 0:
            raise PreviewError(
                f"{' '.join(argv[:2])} exited "
                f"{getattr(proc, 'returncode', '?')}: "
                f"{(getattr(proc, 'stderr', '') or '').strip()[:300]}")
        return getattr(proc, "stdout", "") or ""

    def _run_json(self, argv, **kw):
        out = self._run(argv, **kw)
        try:
            return _json.loads(out or "{}")
        except ValueError:
            raise PreviewError(
                f"{argv[0]} {argv[1]} returned non-JSON output — "
                "cannot bind deployment identity authoritatively")

    def _iso_now(self):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                             time.gmtime(self._now()))

    # -- Pages site ------------------------------------------------------

    def _site(self):
        """The Pages ``html_url`` (cached) — refuses unless the repo
        serves gh-pages at ``/``."""
        if self._site_url is not None:
            return self._site_url
        out = self._run_json([self.gh_bin, "api",
                              f"repos/{self.full_name}/pages"])
        source = out.get("source") or {}
        if source.get("branch") != "gh-pages" or \
                source.get("path") != "/":
            raise PreviewError(
                f"pages-not-configured: repos/{self.full_name}/pages "
                f"serves {source.get('branch')!r}"
                f":{source.get('path')!r} — expected the gh-pages "
                "branch at /")
        url = str(out.get("html_url") or "")
        if not url:
            raise PreviewError(
                "pages-not-configured: pages API returned no html_url")
        self._site_url = url if url.endswith("/") else url + "/"
        return self._site_url

    # -- kit-owned gh-pages worktree --------------------------------------

    def _sync(self):
        """(Re)create + hard-reset the kit-owned gh-pages worktree.

        Safe ONLY because ``worktree_dir`` is kit-owned state; a path
        resolving inside the repo's main working tree is refused."""
        root = os.path.realpath(self.repo_root)
        wt = os.path.realpath(self.worktree_dir)
        if wt == root or wt.startswith(root + os.sep):
            raise PreviewError(
                f"pages worktree {self.worktree_dir!r} resolves inside "
                "the repo's working tree — refusing to sync")
        if os.path.exists(wt):
            probe = self._runner(
                [self.git_bin, "-C", wt, "rev-parse", "--git-dir"],
                env=self._env(), timeout=self.timeout_s)
            if getattr(probe, "returncode", 1) != 0:
                raise PreviewError(
                    f"pages worktree {self.worktree_dir!r} exists but "
                    "is not a git worktree — refusing to take it over")
        else:
            self._run([self.git_bin, "-C", self.repo_root,
                       "fetch", "origin", "gh-pages"])
            # ``worktree add`` creates the leaf itself (and refuses an
            # existing one); only parents may be pre-made.
            parent = os.path.dirname(self.worktree_dir)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self._run([self.git_bin, "-C", self.repo_root,
                       "worktree", "add", "--detach",
                       self.worktree_dir, "origin/gh-pages"])
        self._run([self.git_bin, "-C", self.worktree_dir,
                   "fetch", "origin", "gh-pages"])
        self._run([self.git_bin, "-C", self.worktree_dir,
                   "reset", "--hard", "origin/gh-pages"])
        self._run([self.git_bin, "-C", self.worktree_dir,
                   "clean", "-fd"])

    def _push(self):
        self._run([self.git_bin, "-C", self.worktree_dir, "push",
                   f"https://github.com/{self.full_name}.git",
                   "HEAD:refs/heads/gh-pages"])

    @staticmethod
    def _non_ff(exc):
        text = str(exc)
        return any(marker in text for marker in _NON_FF_MARKERS)

    # -- publish/remove primitives ----------------------------------------

    def _publish(self, preview_id, out_root, head, tree, identity):
        """Sync, replace ``previews/<id>/`` with the build output,
        commit, push. Returns the gh-pages commit sha."""
        self._sync()
        target = os.path.join(self.worktree_dir, "previews",
                              preview_id)
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(out_root, target)
        meta = {"preview_id": preview_id,
                "work_key": (identity or {}).get("work_key"),
                "generation": (identity or {}).get("generation"),
                "head_sha": head, "tree_sha": tree,
                "created_at": self._iso_now()}
        with open(os.path.join(target, "factory-preview.json"),
                  "w", encoding="utf-8") as handle:
            _json.dump(meta, handle, indent=2, sort_keys=True)
            handle.write("\n")
        # Astro emits _astro/ assets — Jekyll drops them unless the
        # site opts out at the root.
        nojekyll = os.path.join(self.worktree_dir, ".nojekyll")
        if not os.path.exists(nojekyll):
            open(nojekyll, "a").close()
        self._run([self.git_bin, "-C", self.worktree_dir,
                   "add", "-A"])
        self._run([self.git_bin, "-C", self.worktree_dir, "commit",
                   "-m", f"preview {preview_id} for {head[:12]}"])
        commit = self._run([self.git_bin, "-C", self.worktree_dir,
                            "rev-parse", "HEAD"]).strip()
        self._push()
        return commit

    def _publish_retry(self, preview_id, out_root, head, tree,
                       identity):
        try:
            return self._publish(preview_id, out_root, head, tree,
                                 identity)
        except PreviewError as exc:
            if not self._non_ff(exc):
                raise
            # Another writer raced gh-pages — re-sync and redo the
            # copy/commit/push once; a second refusal is a clean
            # failure (nothing published either time).
            return self._publish(preview_id, out_root, head, tree,
                                 identity)

    def _rm_push(self, preview_id):
        self._sync()
        self._run([self.git_bin, "-C", self.worktree_dir,
                   "rm", "-r", "-q", f"previews/{preview_id}"])
        self._run([self.git_bin, "-C", self.worktree_dir, "commit",
                   "-m", f"remove preview {preview_id}"])
        self._push()

    # -- waits -------------------------------------------------------------

    def _covers(self, ancestor, sha):
        """``merge-base --is-ancestor`` in the repo — True when the
        pages build's commit is ours or a descendant."""
        self._run([self.git_bin, "-C", self.repo_root,
                   "fetch", "origin", "gh-pages"])
        proc = self._runner(
            [self.git_bin, "-C", self.repo_root,
             "merge-base", "--is-ancestor", ancestor, sha],
            env=self._env(), timeout=self.timeout_s)
        return getattr(proc, "returncode", 1) == 0

    def _wait_pages_build(self, commit):
        """Poll ``pages/builds`` until a build covering our gh-pages
        commit reports ``built`` (``errored`` fails; timeout is
        ambiguous — the build may still land)."""
        deadline = self._now() + self.pages_wait_s
        while True:
            try:
                rows = self._run_json(
                    [self.gh_bin, "api",
                     f"repos/{self.full_name}/pages/"
                     "builds?per_page=10"])
            except PreviewError:
                rows = []
            rows = rows if isinstance(rows, list) else []
            for row in rows:
                build_commit = str(row.get("commit") or "")
                if not build_commit:
                    continue
                try:
                    covered = self._covers(commit, build_commit)
                except PreviewError:
                    covered = build_commit == commit
                if not covered:
                    continue
                status = str(row.get("status") or "").lower()
                if status == "built":
                    return
                if status == "errored":
                    raise PreviewError(
                        f"pages build errored for "
                        f"{build_commit[:12]}: "
                        f"{(row.get('error') or '')[:200]}")
                break            # still building — keep polling
            if self._now() > deadline:
                raise PreviewAmbiguity(
                    "pages build not observed within pages_wait_s")
            self._sleep(self.poll_interval_s)

    def _wait_served(self, preview_id):
        """The identity file must serve HTTP 200 with the matching
        preview_id — the CDN edge proving the publish."""
        url = (f"{self._site()}previews/{preview_id}/"
               "factory-preview.json")
        deadline = self._now() + self.serve_wait_s
        while True:
            try:
                body = self._run([self.curl_bin, "-fsS", url])
                data = _json.loads(body)
                if data.get("preview_id") == preview_id:
                    return
            except (PreviewError, ValueError, AttributeError):
                pass
            if self._now() > deadline:
                raise PreviewAmbiguity(
                    f"preview {preview_id} not served within "
                    "serve_wait_s")
            self._sleep(self.poll_interval_s)

    # -- port verbs --------------------------------------------------------

    def deploy(self, spec, identity) -> dict:
        preview_id = str((identity or {}).get("preview_id") or "")
        if not _PREVIEW_ID_RE.match(preview_id):
            raise PreviewError(
                f"invalid preview_id {preview_id!r} — must match "
                "[A-Za-z0-9._-]{1,64}")
        head = str(spec.get("head_sha") or "")
        tree = _tree_sha(self._run, self.git_bin, self.repo_root, head)
        site = self._site()
        base = urlparse(site).path + f"previews/{preview_id}/"
        export_dir, tar_path = _export_head(
            self._run, self.git_bin, self.repo_root, head)
        try:
            for command in self.build_commands:
                argv = shlex.split(command)
                if not argv:
                    continue
                try:
                    self._run(argv, env={self.base_env: base},
                              timeout=self.build_timeout_s,
                              cwd=export_dir)
                except PreviewError as exc:
                    raise PreviewError(
                        f"build command {command!r} failed: {exc}") \
                        from exc
            out_root = os.path.join(export_dir, self.output_dir or "")
            if not os.path.isfile(
                    os.path.join(out_root, "index.html")):
                raise PreviewError(
                    f"build produced no "
                    f"{self.output_dir}/index.html — nothing to "
                    "publish")
            pages_commit = self._publish_retry(
                preview_id, out_root, head, tree, identity)
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)
            try:
                os.unlink(tar_path)
            except OSError:
                pass
        self._wait_pages_build(pages_commit)
        self._wait_served(preview_id)
        observed = self.inspect(f"{preview_id}@{pages_commit[:12]}")
        if observed is None:
            raise PreviewAmbiguity(
                "preview published but the identity read-back could "
                "not resolve it")
        return observed

    def _read_identity(self, preview_id):
        """The ``factory-preview.json`` row on gh-pages — None only
        when the file does not exist (404); a corrupt file yields ``{}``
        (never owned)."""
        try:
            out = self._run_json(
                [self.gh_bin, "api",
                 f"repos/{self.full_name}/contents/previews/"
                 f"{preview_id}/factory-preview.json?ref=gh-pages"])
        except PreviewError as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                return None
            raise
        content = out.get("content")
        if not isinstance(content, str):
            return {}
        try:
            meta = _json.loads(base64.b64decode(content))
        except (ValueError, TypeError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def _latest_pages_build(self):
        try:
            rows = self._run_json(
                [self.gh_bin, "api",
                 f"repos/{self.full_name}/pages/builds?per_page=1"])
        except PreviewError:
            return None
        rows = rows if isinstance(rows, list) else []
        return rows[0] if rows and isinstance(rows[0], dict) else None

    def _identity_commit(self, preview_id):
        """The gh-pages commit that last wrote this preview's identity
        file (``commits?path=…&sha=gh-pages``)."""
        try:
            rows = self._run_json(
                [self.gh_bin, "api",
                 f"repos/{self.full_name}/commits?sha=gh-pages"
                 f"&path=previews/{preview_id}/"
                 "factory-preview.json&per_page=1"])
        except PreviewError:
            return None
        rows = rows if isinstance(rows, list) else []
        return str(rows[0].get("sha") or "") \
            if rows and isinstance(rows[0], dict) else ""

    def _inspect_state(self, preview_id):
        """READY unless the *latest* pages build is newer than the
        identity file's commit — then the build's own status maps to
        BUILDING/ERROR."""
        latest = self._latest_pages_build()
        if latest is None:
            return "READY"
        build_commit = str(latest.get("commit") or "")
        identity_commit = self._identity_commit(preview_id)
        if build_commit and identity_commit:
            try:
                newer = self._covers(identity_commit, build_commit) \
                    and build_commit != identity_commit
            except PreviewError:
                newer = build_commit != identity_commit
            if newer:
                status = str(latest.get("status") or "").lower()
                if status == "errored":
                    return "ERROR"
                if status != "built":
                    return "BUILDING"
        return "READY"

    def inspect(self, deployment_id) -> dict:
        preview_id = str(deployment_id).split("@", 1)[0]
        meta = self._read_identity(preview_id)
        if meta is None:
            return None
        return {"deployment_id": str(deployment_id),
                "url": f"{self._site()}previews/{preview_id}/",
                "head_sha": meta.get("head_sha"),
                "artifact_identity": meta.get("tree_sha"),
                "state": self._inspect_state(preview_id),
                "expires_epoch": None,
                "owned": str(meta.get("preview_id") or "")
                         == preview_id}

    def find_deployments(self, identity) -> list:
        try:
            rows = self._run_json(
                [self.gh_bin, "api",
                 f"repos/{self.full_name}/contents/"
                 "previews?ref=gh-pages"])
        except PreviewError as exc:
            if "404" in str(exc) or "Not Found" in str(exc):
                return []
            raise
        rows = rows if isinstance(rows, list) else []
        wanted = {k: str(identity.get(k)) for k in _IDENTITY_META_KEYS
                  if identity.get(k) is not None}
        found = []
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "dir":
                continue
            preview_id = str(row.get("name") or "")
            meta = self._read_identity(preview_id)
            if meta is None:
                continue
            bound = {k: str(meta.get(k)) for k in _IDENTITY_META_KEYS}
            if wanted and all(bound.get(k) == v
                              for k, v in wanted.items()):
                found.append({
                    "deployment_id":
                        f"{preview_id}@"
                        f"{str(row.get('sha') or '')[:12]}",
                    "url": f"{self._site()}previews/{preview_id}/",
                    "head_sha": meta.get("head_sha"),
                    "artifact_identity": meta.get("tree_sha"),
                    "state": "READY",
                    "expires_epoch": None, "owned": True})
        return found

    def smoke(self, deployment_id, contract) -> dict:
        return _contract_smoke(self, deployment_id, contract)

    def remove(self, deployment_id, identity) -> dict:
        observed = self.inspect(deployment_id)
        if observed is None:
            return {"removed": True, "already_gone": True}
        if not observed.get("owned", True):
            raise PreviewError(
                f"refusing removal of unowned preview "
                f"{deployment_id!r}")
        preview_id = str(deployment_id).split("@", 1)[0]
        try:
            self._rm_push(preview_id)
        except PreviewError as exc:
            if not self._non_ff(exc):
                raise
            self._rm_push(preview_id)
        return {"removed": True}
