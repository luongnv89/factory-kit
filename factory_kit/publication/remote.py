#!/usr/bin/env python3
"""Remote port — the seam between the publication broker and the remote
authority (issue #10 / Task 2.5, PRD §5.2, §6.4).

The scoped credential lives *only* behind this port. Workers, tests and
descendant processes never see it: they submit intent requests to
:class:`factory_kit.publication.intents.PublicationBroker`, which
re-checks authorization on every effect and is the sole caller of this
port. That is the boundary the spike proved
(``tools/probes/fenced_effects.py``) and this task promotes into durable
serialized intents — prompts, worktrees and branch protection are never
the effect boundary.

Two implementations ship:

- :class:`GhCliRemote` — the production seam: thin argv mapping onto the
  scoped ``gh``/``git`` credential. Deliberately not exercised live in
  unit tests (no GitHub on the test host); the command shape is asserted.
- :class:`ScriptedRemote` — the deterministic fixture port: in-memory
  branches/pulls plus injectable faults (crash-after-create, ambiguous
  reads, identity drift) so the crash/ambiguity criteria test honestly.
"""

from __future__ import annotations

import json as _json
import subprocess

__all__ = [
    "RemoteError",
    "RemoteAmbiguity",
    "RemotePort",
    "GhCliRemote",
    "ScriptedRemote",
]


class RemoteError(Exception):
    """The remote refused or failed the operation (a clean answer)."""


class RemoteAmbiguity(RemoteError):
    """The remote call's outcome is *uncertain* — the effect may have
    landed (crash-after-create, timed-out response). An ambiguous
    outcome must be reconciled by identity, never retried blindly (A5).
    """


class RemotePort:
    """The remote authority contract the broker consumes.

    ``identity`` carries the bound work identity — ``intent_id``,
    ``work_key``, ``authority_key``, ``generation``, ``actor_ref`` — and
    is recorded with every remote effect so read-back binds the remote
    result to the same identity (A1).
    """

    def repository_identity(self) -> dict:
        """The remote's own view of the repository it serves —
        ``{"repo_id", "full_name"}``. Read-back compares it to the
        intent's authority; an uncertain identity parks (A5)."""
        raise NotImplementedError

    def publish_branch(self, branch, sha, identity) -> dict:
        """Publish ``sha`` to ``branch``; return the applied ref info."""
        raise NotImplementedError

    def open_pr(self, head, base, title, identity) -> dict:
        """Open a pull request; return ``{"number", "url", ...}``."""
        raise NotImplementedError

    def read_branch(self, branch):
        """Authoritative read-back: the branch's current SHA or None."""
        raise NotImplementedError

    def find_pull_requests(self, *, head=None, identity=None) -> list:
        """Authoritative read-back: PRs matching the intent's head
        branch (and work identity where the remote records it)."""
        raise NotImplementedError

    def read_pr(self, number):
        """Authoritative current-revision read (Task 2.6 / F04):
        ``{"number", "url", "state", "draft", "head", "head_branch",
        "base"}`` for PR ``number``, or ``None`` when it does not
        exist. ``head`` is the head *SHA* — the revision verification
        binds evidence to."""
        raise NotImplementedError

    def check_runs(self, sha):
        """Authoritative check-run observation for revision ``sha``:
        ``[{"id", "name", "status", "conclusion", "app",
        "details_url"}]`` — provider identity included so the evidence
        names *which* system reported each conclusion (F04 A2)."""
        raise NotImplementedError


class GhCliRemote(RemotePort):
    """Thin adapter over the scoped ``gh``/``git`` credential — the only
    paved remote path (the spike's "scoped gh profile" transport).

    Every method is one subprocess call with arguments built from the
    already-validated intent fields; the adapter invents no policy of
    its own and never widens scope — the broker's authorization recheck
    is what gates every call here.
    """

    def __init__(self, repo_id, full_name, *, gh_bin="gh",
                 git_bin="git", timeout_s=60):
        self._identity = {"repo_id": repo_id, "full_name": full_name}
        self.gh_bin = gh_bin
        self.git_bin = git_bin
        self.timeout_s = timeout_s

    def _run(self, argv) -> dict:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise RemoteAmbiguity(f"{argv[0]} timed out: {exc}") from exc
        except OSError as exc:
            raise RemoteError(f"{argv[0]}: {exc}") from exc
        if proc.returncode != 0:
            raise RemoteError(
                f"{' '.join(argv[:2])} exited {proc.returncode}: "
                f"{proc.stderr.strip()[:200]}")
        try:
            return _json.loads(proc.stdout or "{}")
        except ValueError:
            return {"raw": proc.stdout.strip()}

    def repository_identity(self) -> dict:
        out = self._run([self.gh_bin, "repo", "view",
                         self._identity["full_name"],
                         "--json", "id,nameWithOwner"])
        return {"repo_id": out.get("id"),
                "full_name": out.get("nameWithOwner",
                                     self._identity["full_name"])}

    def publish_branch(self, branch, sha, identity) -> dict:
        """Push the exact expected revision to the target branch —
        bound to the registered repository like every other method
        here, never to whatever ``origin`` happens to resolve to in
        the process cwd (the read-back can only *detect* a stray push,
        it cannot un-send it)."""
        self._run([self.git_bin, "push",
                   f"https://github.com/{self._identity['full_name']}.git",
                   f"{sha}:refs/heads/{branch}"])
        return {"branch": branch, "sha": sha}

    def open_pr(self, head, base, title, identity) -> dict:
        body = (f"factory-kit publication intent {identity['intent_id']}"
                f" (work {identity['work_key']},"
                f" generation {identity['generation']})")
        out = self._run([self.gh_bin, "pr", "create",
                         "--repo", self._identity["full_name"],
                         "--head", head, "--base", base,
                         "--title", title, "--body", body])
        url = str(out.get("url") or out.get("raw") or "").strip()
        number = url.rsplit("/", 1)[-1] if "/" in url else ""
        return {"number": number, "url": url, "head": head,
                "base": base}

    def read_branch(self, branch):
        try:
            out = self._run([self.gh_bin, "api",
                             f"repos/{self._identity['full_name']}"
                             f"/git/ref/heads/{branch}"])
        except RemoteError:
            return None
        obj = out.get("object") or {}
        return obj.get("sha")

    def find_pull_requests(self, *, head=None, identity=None) -> list:
        argv = [self.gh_bin, "pr", "list",
                "--repo", self._identity["full_name"],
                "--state", "all", "--json",
                "number,url,headRefName,baseRefName,state"]
        if head:
            argv += ["--head", head]
        out = self._run(argv)
        rows = out if isinstance(out, list) else []
        return [{"number": str(r.get("number")), "url": r.get("url"),
                 "head": r.get("headRefName"),
                 "base": r.get("baseRefName"),
                 "state": r.get("state")} for r in rows]

    def read_pr(self, number):
        # A non-zero ``gh`` exit is deliberately NOT mapped to None:
        # the caller cannot tell "PR does not exist" from "GitHub
        # unreachable", and verification must answer ``unknown`` for
        # the latter — never mistake an outage for a closed PR (A3).
        out = self._run([self.gh_bin, "pr", "view", str(number),
                         "--repo", self._identity["full_name"],
                         "--json",
                         "number,url,state,isDraft,headRefOid,"
                         "headRefName,baseRefName"])
        if not out or out.get("number") is None:
            return None
        return {"number": str(out.get("number")),
                "url": out.get("url"),
                "state": out.get("state"),
                "draft": bool(out.get("isDraft")),
                "head": out.get("headRefOid"),
                "head_branch": out.get("headRefName"),
                "base": out.get("baseRefName")}

    def check_runs(self, sha):
        out = self._run([self.gh_bin, "api",
                         f"repos/{self._identity['full_name']}"
                         f"/commits/{sha}/check-runs"])
        runs = out.get("check_runs") if isinstance(out, dict) else None
        return [{"id": str(c.get("id")),
                 "name": c.get("name"),
                 "status": c.get("status"),
                 "conclusion": c.get("conclusion"),
                 "app": (c.get("app") or {}).get("slug"),
                 "details_url": c.get("details_url") or
                 c.get("html_url")}
                for c in (runs or [])]


class ScriptedRemote(RemotePort):
    """Deterministic fixture remote — the DisposableRemote analogue for
    the packaged broker.

    - ``pulls`` records the PR identity it was created under, so
      read-back can bind remote results to the durable work identity.
    - ``faults`` injects the crash/ambiguity cases the criteria name:
      ``"crash-after-create"`` applies the effect then raises
      :class:`RemoteAmbiguity` (the response was lost — the classic
      crash window); ``"crash-before-create"`` loses the request;
      ``"raise"`` refuses cleanly — also on the read-only verbs
      ``"pr-read"``/``"check-runs"``, where it is the verification
      stage's GitHub-unavailable fixture.
    - ``identity_override`` simulates the uncertain-repository-identity
      case: read-back reports a *different* repo authority than the
      registration's, which must park rather than link.
    - ``checks`` maps ``{sha: [check-run dicts]}`` — the check-runs
      fixture for current-revision verification; ``pr_drafts`` holds
      PR numbers that read back as drafts.
    """

    def __init__(self, repo_id, full_name, *, faults=None):
        self._identity = {"repo_id": repo_id, "full_name": full_name}
        self.identity_override = None      # {"repo_id": …} or None
        self.branches = {
            "main": "e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5"}
        self.pulls = {}                    # str(number) -> record
        self.checks = {}                   # sha -> [check-run dicts]
        self.pr_drafts = set()             # PR numbers reported draft
        self.pr_states = {}                # PR number -> state override
        self.faults = dict(faults or {})
        self.calls = []                    # ordered (op, kwargs) log
        self._next_pr = 100

    def _call(self, op, apply, **kw):
        self.calls.append({"op": op, **{k: v for k, v in kw.items()
                                        if k != "identity"}})
        fault = self.faults.get(op)
        if fault == "raise":
            raise RemoteError(f"{op} refused by remote")
        if fault == "crash-before-create":
            # The request was lost before the effect — ambiguous to the
            # caller; reconciliation must find nothing and park (A5).
            raise RemoteAmbiguity(f"{op} request lost")
        result = apply(**kw)
        if fault == "crash-after-create":
            # The effect landed but the response was lost — precisely
            # the crash window A3/A5 require recovery to reconcile.
            raise RemoteAmbiguity(f"{op} response lost")
        return result

    def repository_identity(self) -> dict:
        return dict(self.identity_override or self._identity)

    def publish_branch(self, branch, sha, identity) -> dict:
        def apply(**kw):
            self.branches[branch] = sha
            return {"branch": branch, "sha": sha}
        return self._call("branch-publish", apply, branch=branch,
                          sha=sha, identity=identity)

    def open_pr(self, head, base, title, identity) -> dict:
        def apply(**kw):
            number = str(self._next_pr)
            self._next_pr += 1
            self.pulls[number] = {
                "number": number, "head": head, "base": base,
                "title": title, "state": "open",
                "url": f"https://example.test/{self._identity['full_name']}"
                       f"/pull/{number}",
                "identity": dict(identity)}
            return dict(self.pulls[number])
        return self._call("pr-publish", apply, head=head, base=base,
                          title=title, identity=identity)

    def read_branch(self, branch):
        return self.branches.get(branch)

    def find_pull_requests(self, *, head=None, identity=None) -> list:
        rows = [p for p in self.pulls.values()
                if head is None or p["head"] == head]
        if identity is not None:
            authority = identity.get("authority_key")
            keyed = [p for p in rows
                     if (p.get("identity") or {}).get("authority_key")
                     in (None, authority)]
            rows = keyed
        return [dict(p) for p in rows]

    def read_pr(self, number):
        def apply(**kw):
            pull = self.pulls.get(str(number))
            if pull is None:
                return None
            return {"number": str(pull["number"]), "url": pull["url"],
                    "state": self.pr_states.get(str(number),
                                                pull["state"]),
                    "draft": str(number) in self.pr_drafts,
                    "head": self.branches.get(pull["head"]),
                    "head_branch": pull["head"],
                    "base": pull["base"]}
        return self._call("pr-read", apply, number=number)

    def check_runs(self, sha):
        def apply(**kw):
            return [dict(c) for c in self.checks.get(sha, [])]
        return self._call("check-runs", apply, sha=sha)
