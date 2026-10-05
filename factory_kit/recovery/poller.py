#!/usr/bin/env python3
"""Issue observation port — the GitHub read seam for periodic
reconciliation (issue #13 / Task 2.8, PRD §3.2 F06, §5.1).

The reconciler never reads chat history and never invents a scheduler:
``hermes cron`` (per the boundary map) calls the pass, and this port is
the only way GitHub issue state enters it. Two implementations ship:

- :class:`GhIssuePoller` — thin ``gh`` adapter: ``issue list`` for the
  open-issue set (dropped-webhook discovery, A4) and ``issue view`` for
  the targeted re-observation of known work (revoked-permission and
  closed-issue discovery, A4/A6). Read-only — it carries no mutation
  verb, so a reconciliation pass can never become a remote effect.
- :class:`ScriptedIssueSource` — the deterministic fixture: in-memory
  issue rows plus injectable faults (outage, rate-limit with retry
  guidance) so the backoff/stale criteria test honestly.

Each observed issue normalizes into the same envelope shape the webhook
adapter produces, so the intake decision pipeline cannot tell the
channels apart — that is exactly what "webhook, polling and restart
agree" requires (F02/F06).
"""

from __future__ import annotations

import json as _json
import subprocess

from factory_kit.publication.remote import (
    RemoteError,
    _retry_after_hint,
)

__all__ = [
    "GhIssuePoller",
    "ScriptedIssueSource",
    "issue_observation",
]


def issue_observation(repo_id, full_name, issue, *, delivery_id):
    """Shape one polled issue row into the fields the reconciliation
    channel consumes — a closed observation reaches intake as the
    ``closed`` action so the revocation path lands (A4)."""
    labels = [l.get("name", "") for l in (issue.get("labels") or [])
              if isinstance(l, dict)]
    state = str(issue.get("state") or "").lower()
    author = (issue.get("author") or {}).get("login") or ""
    return {
        "delivery_id": delivery_id,
        "channel": "reconciliation",
        "repo_id": repo_id,
        "repository": full_name,
        "issue": issue.get("number"),
        # A closed observation is the revocation path; anything else is
        # the reconcile-as-edited path (current labels decide).
        "action": "closed" if state == "closed" else "reconcile",
        "sender": author,
        "labels": labels,
        "issue_title": issue.get("title") or "",
        "issue_body": issue.get("body") or "",
        "issue_revision": issue.get("updated_at")
        or issue.get("updatedAt") or "",
    }


class GhIssuePoller:
    """Read-only GitHub issue source over the scoped ``gh`` credential.

    Same discipline as :class:`GhCliRemote`: one subprocess per call,
    arguments built from validated fields, and no retry loop — the
    reconciler owns scheduling/backoff so this port stays honest about
    what one observation attempt did.
    """

    def __init__(self, repo_id, full_name, *, gh_bin="gh",
                 timeout_s=60):
        self.repo_id = repo_id
        self.full_name = full_name
        self.gh_bin = gh_bin
        self.timeout_s = timeout_s

    def _run(self, argv):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=self.timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise RemoteError(f"{argv[0]} timed out: {exc}") from exc
        except OSError as exc:
            raise RemoteError(f"{argv[0]}: {exc}") from exc
        if proc.returncode != 0:
            raise RemoteError(
                f"{' '.join(argv[:2])} exited {proc.returncode}: "
                f"{proc.stderr.strip()[:200]}",
                retry_after=_retry_after_hint(proc.stderr))
        try:
            return _json.loads(proc.stdout or "{}")
        except ValueError as exc:
            raise RemoteError(
                f"{argv[0]} unparsable output: {exc}") from exc

    def poll_issues(self, *, limit=200) -> list:
        """The current open-issue set — the dropped-webhook discovery
        read. One observation per issue; intake decides eligibility from
        the *current* label set, so a label removed since the webhook
        is seen revoked rather than skipped."""
        out = self._run([self.gh_bin, "issue", "list",
                         "--repo", self.full_name,
                         "--state", "open", "--limit", str(int(limit)),
                         "--json",
                         "number,title,body,labels,updatedAt,author"])
        return out if isinstance(out, list) else []

    def observe_issue(self, number) -> dict | None:
        """Targeted re-observation of one known issue — state, labels
        and revision *now*. ``None`` when the issue does not exist —
        a clean answer, distinct from an unreachable remote (which
        raises)."""
        out = self._run([self.gh_bin, "issue", "view", str(number),
                         "--repo", self.full_name,
                         "--json",
                         ("number,title,body,labels,state,updatedAt,"
                          "author")])
        if not isinstance(out, dict) or out.get("number") is None:
            return None
        out["state"] = str(out.get("state") or "").lower()
        return out


class ScriptedIssueSource:
    """Deterministic fixture issue source — the poller analogue for
    tests. ``issues`` maps ``{number: row}`` where a row carries
    ``title``, ``body``, ``labels`` (list of ``{"name": …}`` or plain
    strings), ``state`` (``open``/``closed``), ``updated_at`` and
    ``author`` (``{"login": …}`` or a string).

    ``faults`` injects failures per verb: ``"poll"`` / ``"observe"`` →
    ``"down"`` (unreachable), ``"raise"``, or ``{"kind": "rate-limit",
    "retry_after": N}``. A callable fault is invoked per call so a test
    can fail N times then recover — connectivity flapping is scripted,
    never random.
    """

    def __init__(self, repo_id, full_name, *, issues=None, faults=None):
        self.repo_id = repo_id
        self.full_name = full_name
        self.issues = dict(issues or {})
        self.faults = dict(faults or {})
        self.calls = []

    @staticmethod
    def _row(number, row):
        labels = [{"name": l} if isinstance(l, str) else l
                  for l in row.get("labels") or []]
        author = row.get("author")
        if isinstance(author, str):
            author = {"login": author}
        return {"number": number, "title": row.get("title") or "",
                "body": row.get("body") or "",
                "labels": labels,
                "state": str(row.get("state") or "open").lower(),
                "updated_at": row.get("updated_at") or "",
                "author": author or {"login": ""}}

    def _fault(self, op):
        fault = self.faults.get(op)
        if callable(fault):
            fault = fault()
        if isinstance(fault, dict):
            if fault.get("kind") == "rate-limit":
                raise RemoteError(
                    f"{op} rate-limited",
                    retry_after=fault.get("retry_after"))
            fault = fault.get("kind")
        if fault in ("down", "raise"):
            raise RemoteError(f"{op}: remote unreachable")
        return fault

    def poll_issues(self, *, limit=200) -> list:
        self.calls.append("poll")
        self._fault("poll")
        rows = [self._row(n, r) for n, r in sorted(self.issues.items())
                if str(r.get("state") or "open").lower() == "open"]
        return rows[: int(limit)]

    def observe_issue(self, number) -> dict | None:
        self.calls.append(("observe", number))
        self._fault("observe")
        row = self.issues.get(int(number))
        return self._row(int(number), row) if row is not None else None
