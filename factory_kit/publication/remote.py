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
import re
import subprocess

__all__ = [
    "RemoteError",
    "RemoteAmbiguity",
    "RemotePort",
    "GhCliRemote",
    "ScriptedRemote",
]


class RemoteError(Exception):
    """The remote refused or failed the operation (a clean answer).

    ``retry_after`` carries upstream retry guidance in seconds when the
    remote supplied it (``Retry-After`` / rate-limit reset hints); the
    reconciler's bounded backoff honors it — including values beyond the
    nominal 5-minute cap, which upstream guidance overrides (Task 2.8
    A5, §5.1). ``None`` means no guidance was given.
    """

    def __init__(self, message="", retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


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
        binds evidence to. Merge-guard fields (Task 3.3 / F12):
        ``mergeable``, ``merge_state``, ``auto_merge`` (an armed
        auto-merge request = a competing merge owner), ``merged``,
        ``merge_sha`` (the actual merge commit — the authoritative
        outcome) and ``merged_by`` (the remote-recorded actor)."""
        raise NotImplementedError

    def check_runs(self, sha):
        """Authoritative check-run observation for revision ``sha``:
        ``[{"id", "name", "status", "conclusion", "app",
        "details_url"}]`` — provider identity included so the evidence
        names *which* system reported each conclusion (F04 A2)."""
        raise NotImplementedError

    def read_branch_protection(self, branch):
        """Authoritative protection read for ``branch`` (Task 3.3 /
        F12): ``{"protected", "strict", "enforce_admins",
        "required_checks"}`` — ``strict`` is the up-to-date-base
        enforcement and ``enforce_admins`` the no-bypass rule the
        guarded merge requires; ``{"protected": False}`` when the
        branch carries no protection."""
        raise NotImplementedError

    def repository_capabilities(self):
        """Authoritative repository merge capability read (Task 3.3):
        ``{"allow_auto_merge", "allow_squash_merge",
        "allow_merge_commit", "allow_rebase_merge"}`` — the merge
        owner requires ``allow_auto_merge`` off (a queued auto-merge
        can outlive approval expiry) and the selected method enabled."""
        raise NotImplementedError

    def actor_identity(self):
        """The remote-recorded login of the scoped credential —
        ``{"login": str}``. A merged PR's ``merged_by`` equal to this
        login is a factory merge; anything else is external activity
        attributed to its actual actor, never to a factory approval
        (F12 A7)."""
        raise NotImplementedError

    def merge_pr(self, number, *, method, expected_head, identity) -> dict:
        """The one supported merge effect (Task 3.3 / F12): a
        conditional merge that applies only when the remote head still
        equals ``expected_head`` — the expected-head precondition —
        under the repository-enforced protections the guard verified
        (required checks on an up-to-date base, admins not bypassed).
        Returns the remote's answer; the authoritative outcome is
        *always* the read-back, never this response (A6)."""
        raise NotImplementedError


#: Upstream retry guidance the GitHub CLI surfaces on stderr — a
#: ``Retry-After: N`` header echo or a "retry after N seconds" message.
#: Parsed so the reconciler can honor server-directed backoff instead of
#: guessing (§5.1/A5). Never raises; absent guidance returns ``None``.
_RETRY_AFTER_RE = re.compile(
    r"retry[- ]after[:\s]+(\d+)", re.IGNORECASE)


def _retry_after_hint(text):
    match = _RETRY_AFTER_RE.search(text or "")
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


class GhCliRemote(RemotePort):
    """Thin adapter over the scoped ``gh``/``git`` credential — the only
    paved remote path (the spike's "scoped gh profile" transport).

    Every method is one subprocess call with arguments built from the
    already-validated intent fields; the adapter invents no policy of
    its own and never widens scope — the broker's authorization recheck
    is what gates every call here.
    """

    def __init__(self, repo_id, full_name, *, gh_bin="gh",
                 git_bin="git", cwd=None, runner=None, timeout_s=60):
        self._identity = {"repo_id": repo_id, "full_name": full_name}
        self.gh_bin = gh_bin
        self.git_bin = git_bin
        #: Repo-local cwd — ``git push <sha>:refs/heads/…`` must run in
        #: the repository whose object store actually holds ``sha`` (a
        #: worktree's object store is shared with the main checkout, so
        #: the repo root works for both).
        self.cwd = cwd
        self.timeout_s = timeout_s
        #: Injectable subprocess seam — tests assert exact argv without
        #: a network; it receives ``(argv, cwd, timeout)`` and returns a
        #: ``CompletedProcess``-compatible object.
        self._runner = runner

    def _run(self, argv) -> dict:
        try:
            if self._runner is not None:
                proc = self._runner(argv, cwd=self.cwd,
                                    timeout=self.timeout_s)
            else:
                proc = subprocess.run(argv, capture_output=True,
                                      text=True,
                                      timeout=self.timeout_s,
                                      cwd=self.cwd)
        except subprocess.TimeoutExpired as exc:
            raise RemoteAmbiguity(f"{argv[0]} timed out: {exc}") from exc
        except OSError as exc:
            raise RemoteError(f"{argv[0]}: {exc}") from exc
        if proc.returncode != 0:
            raise RemoteError(
                f"{' '.join(argv[:2])} exited {proc.returncode}: "
                f"{proc.stderr.strip()[:200]}",
                retry_after=_retry_after_hint(proc.stderr))
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
        issue = identity.get("issue")
        if issue is not None:
            # GitHub's ``Closes`` keyword auto-closes the issue on
            # merge — the intake's opt-in issue stays the audit anchor.
            body += f"\n\nCloses #{issue}"
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
        # The merge fields (Task 3.3) ride the same authoritative read:
        # mergeable/mergeStateStatus for the guard, autoMergeRequest for
        # the competing-owner check, and mergeCommit/mergedBy for the
        # authoritative outcome read-back.
        out = self._run([self.gh_bin, "pr", "view", str(number),
                         "--repo", self._identity["full_name"],
                         "--json",
                         "number,url,state,isDraft,headRefOid,"
                         "headRefName,baseRefName,mergeable,"
                         "mergeStateStatus,autoMergeRequest,"
                         "mergeCommit,mergedBy"])
        if not out or out.get("number") is None:
            return None
        merge_commit = out.get("mergeCommit") or {}
        merged_by = out.get("mergedBy") or {}
        return {"number": str(out.get("number")),
                "url": out.get("url"),
                "state": out.get("state"),
                "draft": bool(out.get("isDraft")),
                "head": out.get("headRefOid"),
                "head_branch": out.get("headRefName"),
                "base": out.get("baseRefName"),
                "mergeable": out.get("mergeable"),
                "merge_state": out.get("mergeStateStatus"),
                "auto_merge": out.get("autoMergeRequest") is not None,
                "merged": str(out.get("state") or "").upper() == "MERGED",
                "merge_sha": merge_commit.get("oid"),
                "merged_by": merged_by.get("login")}

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

    def read_branch_protection(self, branch):
        # The protected-branch API answers 404 when the branch carries
        # no protection — that is a real answer (unprotected), not an
        # outage. Any other failure propagates so the guard can defer
        # rather than decide on an unreadable contract.
        try:
            out = self._run([self.gh_bin, "api",
                             f"repos/{self._identity['full_name']}"
                             f"/branches/{branch}/protection"])
        except RemoteError as exc:
            if "404" in str(exc) or "not protected" in str(exc).lower():
                return {"protected": False}
            raise
        rsc = out.get("required_status_checks") or {}
        contexts = [c if isinstance(c, str) else c.get("context")
                    for c in (rsc.get("contexts") or [])]
        contexts += [c.get("context") for c in (rsc.get("checks") or [])
                     if isinstance(c, dict)]
        return {"protected": True,
                "strict": bool(rsc.get("strict")),
                "enforce_admins": bool(
                    (out.get("enforce_admins") or {}).get("enabled")),
                "required_checks": sorted(c for c in contexts if c)}

    def repository_capabilities(self):
        out = self._run([self.gh_bin, "api",
                         f"repos/{self._identity['full_name']}"])
        return {"allow_auto_merge": bool(out.get("allow_auto_merge")),
                "allow_squash_merge":
                    bool(out.get("allow_squash_merge")),
                "allow_merge_commit":
                    bool(out.get("allow_merge_commit")),
                "allow_rebase_merge":
                    bool(out.get("allow_rebase_merge"))}

    def actor_identity(self):
        out = self._run([self.gh_bin, "api", "user"])
        return {"login": out.get("login")}

    def merge_pr(self, number, *, method, expected_head, identity) -> dict:
        # The supported expected-head merge operation: the REST merge
        # endpoint's ``sha`` precondition makes the merge conditional on
        # the remote head still equalling the approved revision — and
        # the repository's own protection (required checks + strict
        # up-to-date base + enforce-admins) is what refuses the call
        # atomically when base/checks moved between the guard's read
        # and this write (A4). Auto-merge is never enabled.
        out = self._run([self.gh_bin, "api",
                         f"repos/{self._identity['full_name']}"
                         f"/pulls/{number}/merge",
                         "-X", "PUT",
                         "-f", f"merge_method={method}",
                         "-f", f"sha={expected_head}"])
        return {"merged": bool(out.get("merged")),
                "sha": out.get("sha"),
                "message": out.get("message")}


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
        # Task 3.3 seams — the guarded merge's remote contract:
        # ``mergeable``/``merge_state`` ride ``read_pr`` for the guard's
        # mergeability check; ``auto_merge`` arms a competing-owner
        # read; ``mergers`` records the remote's merge outcome
        # {number: {"sha", "by"}} — set directly to simulate a
        # human-originated merge; ``protection``/``capabilities``/
        # ``actor_login`` are the protection/capability/identity reads.
        self.mergeable = "MERGEABLE"
        self.merge_state = "CLEAN"
        self.auto_merge = None             # armed auto-merge request
        self.mergers = {}                  # str(number) -> {sha, by}
        self.protection = {"protected": True, "strict": True,
                           "enforce_admins": True,
                           "required_checks": ["Code Quality & Build",
                                               "Security Scan"]}
        self.capabilities = {"allow_auto_merge": False,
                             "allow_squash_merge": True,
                             "allow_merge_commit": False,
                             "allow_rebase_merge": False}
        self.actor_login = "factory-bot"
        self.faults = dict(faults or {})
        self.calls = []                    # ordered (op, kwargs) log
        self._next_pr = 100

    def _call(self, op, apply, **kw):
        self.calls.append({"op": op, **{k: v for k, v in kw.items()
                                        if k != "identity"}})
        fault = self.faults.get(op)
        # Fault values may also be dicts — ``{"kind": "rate-limit",
        # "retry_after": N}`` injects the upstream-guidance path the
        # bounded backoff must honor (Task 2.8 A5).
        if isinstance(fault, dict):
            kind = fault.get("kind")
            if kind == "rate-limit":
                raise RemoteError(
                    f"{op} rate-limited by remote",
                    retry_after=fault.get("retry_after"))
            if kind == "down":
                raise RemoteError(f"{op}: remote unreachable")
            fault = fault.get("kind")
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
                # The PR's base snapshot — strict up-to-date protection
                # refuses a merge once the live base branch moved past
                # it; that is the base/check race enforcement the
                # guarded merge relies on (Task 3.3 A4).
                "base_sha": self.branches.get(base),
                "title": title, "state": "open",
                "url": f"https://example.test/{self._identity['full_name']}"
                       f"/pull/{number}",
                "identity": dict(identity)}
            return dict(self.pulls[number])
        return self._call("pr-publish", apply, head=head, base=base,
                          title=title, identity=identity)

    def read_branch(self, branch):
        def apply(**kw):
            return self.branches.get(kw["branch"])
        return self._call("branch-read", apply, branch=branch)

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
            num = str(kw["number"])
            pull = self.pulls.get(num)
            if pull is None:
                return None
            state = self.pr_states.get(num, pull["state"])
            merger = self.mergers.get(num)
            merged = merger is not None or state.upper() == "MERGED"
            return {"number": str(pull["number"]), "url": pull["url"],
                    "state": state,
                    "draft": num in self.pr_drafts,
                    "head": self.branches.get(pull["head"]),
                    "head_branch": pull["head"],
                    "base": pull["base"],
                    "mergeable": self.mergeable,
                    "merge_state": self.merge_state,
                    "auto_merge": self.auto_merge is not None,
                    "merged": merged,
                    "merge_sha": (merger or {}).get("sha"),
                    "merged_by": (merger or {}).get("by")}
        return self._call("pr-read", apply, number=number)

    def check_runs(self, sha):
        def apply(**kw):
            return [dict(c) for c in self.checks.get(sha, [])]
        return self._call("check-runs", apply, sha=sha)

    def read_branch_protection(self, branch):
        def apply(**kw):
            return dict(self.protection)
        return self._call("protection-read", apply, branch=branch)

    def repository_capabilities(self):
        def apply(**kw):
            return dict(self.capabilities)
        return self._call("capabilities-read", apply)

    def actor_identity(self):
        def apply(**kw):
            return {"login": self.actor_login}
        return self._call("actor-read", apply)

    def merge_pr(self, number, *, method, expected_head, identity) -> dict:
        def apply(**kw):
            num = str(kw["number"])
            pull = self.pulls.get(num)
            if pull is None:
                raise RemoteError(f"merge: PR {num} not found")
            state = self.pr_states.get(num, pull["state"])
            if str(state).lower() != "open":
                raise RemoteError("merge refused: PR not open")
            head = self.branches.get(pull["head"])
            # The conditional merge's expected-head precondition — the
            # effect applies only while the remote head still equals the
            # approved revision (A4).
            if head != expected_head:
                raise RemoteError(
                    "merge refused: sha precondition failed")
            # Repository-enforced strict up-to-date protection: when
            # the base branch moved since the PR's snapshot the merge
            # is refused — the base/check race the remote itself closes
            # (A4). Tests move ``branches[base]`` to exercise it.
            if self.protection.get("protected") and \
                    self.protection.get("strict") and \
                    pull.get("base_sha") is not None and \
                    self.branches.get(pull["base"]) != \
                    pull.get("base_sha"):
                raise RemoteError(
                    "merge refused: base branch is not up to date "
                    "(strict protection)")
            merge_sha = f"merge-{num}-{str(head)[:8]}"
            self.pr_states[num] = "MERGED"
            self.mergers[num] = {"sha": merge_sha,
                                 "by": self.actor_login}
            return {"merged": True, "sha": merge_sha}
        return self._call("pr-merge", apply, number=number,
                          method=method, expected_head=expected_head,
                          identity=identity)
