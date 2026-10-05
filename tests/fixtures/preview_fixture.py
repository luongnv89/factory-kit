#!/usr/bin/env python3
"""Disposable preview-provider fixture for the Task 1.3 endpoint demo (issue #4).

Stands in for the recipe's preview authority — Vercel preview deployments —
as a JSON registry under a scratch directory. Mirrors the capability-gated
design of ``disposable_repo.py``: deployments are only created/removed
through a privileged handle presenting the scoped broker token, and the
registry file is read-only (0o400) against direct writes.

What the fixture proves about the provider contract (issue AC-A3):

- Every deployment carries an **immutable revision binding** — the
  ``head_sha`` it was deployed from plus a content digest. A preview for an
  older revision can never be re-presented as covering a newer head:
  ``claim_revision`` only answers the revision it was actually built for.
- Smoke results record URL + HTTP status + marker hit + observation time —
  the evidence the approval gate consumes. ``smoke_max_age_s`` is the
  recipe's 10-minute freshness contract (A7).
- ``expire``/TTL, injected ``outage`` and unknown-deployment lookup all
  produce *denied* verdicts that block approval-ready/merge — provider
  failure degrades the gate, never waves it through.
- ``cleanup`` only removes deployments this registry owns; a foreign
  deployment id is refused outright. An *owned* id under provider outage
  is pushed to a visible backlog — never a false removal claim — and the
  later successful cleanup drains that entry (F11).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

REGISTRY_FILE = "previews.json"
SMOKE_MARKER = "MoneyMind"          # the contract's SPA shell marker
DEFAULT_TTL_S = 24 * 3600           # recipe: one preview, 24 h TTL


class BoundaryViolation(PermissionError):
    """A privileged preview effect attempted without the scoped token."""


def _atomic_write(path: Path, payload: dict, mode: int | None = None) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if mode is not None:
        os.chmod(path, mode)


class PreviewRegistry:
    """JSON-backed stand-in for the Vercel deployments authority."""

    def __init__(self, root, token: str | None = None, now=None):
        self.root = Path(root)
        self.token = token
        self.state_path = self.root / REGISTRY_FILE
        self._now = now or time.time       # injectable clock for TTL tests
        self.outage = False                # provider-outage injection
        self.artifact_store: dict[str, str] = {}   # deployment_id -> html body

    @classmethod
    def initialize(cls, root, repository: str, now=None):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        state = {"repository": repository, "deployments": {}, "events": [],
                 "cleanup_backlog": []}
        _atomic_write(root / REGISTRY_FILE, state, mode=0o400)
        return cls(root, now=now)

    # -- internals ----------------------------------------------------------

    def _require(self):
        if self.token is None:
            raise BoundaryViolation(
                "no scoped broker credential: preview effect refused")

    def _load(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _store(self, state: dict, event: dict) -> None:
        event = dict(event)
        event["seq"] = len(state["events"]) + 1
        state["events"].append(event)
        _atomic_write(self.state_path, state, mode=0o400)

    def _check_outage(self):
        if self.outage:
            raise ConnectionError("provider outage injected — deploy/read refused")

    # -- privileged surface --------------------------------------------------

    def deploy(self, head_sha: str, ctx: dict, artifact: str | None = None) -> dict:
        """Create a preview bound immutably to ``head_sha``. ``artifact`` is
        the deployable content digest input; defaults to the SPA shell +
        marker (the smoke path has to find ``SMOKE_MARKER`` in the body)."""
        self._require()
        self._check_outage()
        state = self._load()
        seq = len(state["deployments"]) + 1
        dep_id = f"dpl-{seq:04d}-{hashlib.sha256(head_sha.encode()).hexdigest()[:8]}"
        body = artifact if artifact is not None else (
            f"<html><title>{SMOKE_MARKER}</title><body>{SMOKE_MARKER} preview "
            f"of {head_sha[:12]}</body></html>")
        state["deployments"][dep_id] = {
            "head_sha": head_sha,
            "revision_digest": hashlib.sha256(
                (head_sha + (artifact or "")).encode()).hexdigest()[:16],
            "url": f"https://preview-{dep_id}.example.test",
            "created": self._now(),
            "expires": self._now() + DEFAULT_TTL_S,
            "state": "READY",
        }
        self.artifact_store[dep_id] = body
        ev = {"op": "preview-deploy", **{k: ctx.get(k) for k in
                                        ("op_id", "repository", "actor")},
              "deployment_id": dep_id, "head_sha": head_sha}
        self._store(state, ev)
        return {"deployment_id": dep_id,
                "url": state["deployments"][dep_id]["url"],
                "head_sha": head_sha}

    def inspect(self, deployment_id: str) -> dict:
        """Externally verifiable identity read-back — the ``vercel inspect``
        analogue. Unknown identity is an error, never a silent pass."""
        self._check_outage()
        dep = self._load()["deployments"].get(deployment_id)
        if not dep:
            raise KeyError(f"unknown deployment id {deployment_id!r}")
        if dep["expires"] < self._now():
            dep = dict(dep, state="EXPIRED")
        return dep

    def smoke(self, deployment_id: str, ctx: dict | None = None) -> dict:
        """The contract's meaningful-preview check: HTTP-200 + shell marker,
        with result and observation time recorded as evidence."""
        try:
            dep = self.inspect(deployment_id)
            body = self.artifact_store.get(deployment_id, "")
            ok = dep["state"] == "READY" and SMOKE_MARKER in body
            out = {"deployment_id": deployment_id, "url": dep["url"],
                   "http": 200 if ok else 503,
                   "marker": SMOKE_MARKER in body,
                   "state": dep["state"],
                   "ok": ok, "observed_at": self._now()}
        except KeyError:
            out = {"deployment_id": deployment_id, "ok": False, "http": 404,
                   "marker": False, "state": "UNKNOWN",
                   "observed_at": self._now()}
        except ConnectionError:
            out = {"deployment_id": deployment_id, "ok": False, "http": None,
                   "marker": None, "state": "OUTAGE",
                   "observed_at": self._now()}
        if ctx is not None:
            state = self._load()
            ev = {"op": "preview-smoke", **{k: ctx.get(k) for k in
                                           ("op_id", "actor")},
                  "deployment_id": deployment_id, "ok": out["ok"]}
            self._store(state, ev)
        return out

    def cleanup(self, deployment_id: str, ctx: dict) -> dict:
        """Owned-preview removal. Only deployments this registry owns are
        removed; under outage the id is pushed to a *visible* backlog — never
        a false removal claim."""
        self._require()
        state = self._load()
        if deployment_id not in state["deployments"]:
            raise KeyError(f"refusing cleanup of foreign deployment "
                           f"{deployment_id!r}")
        if self.outage:
            if deployment_id not in state["cleanup_backlog"]:
                state["cleanup_backlog"].append(deployment_id)
                self._store(state, {"op": "cleanup-backlog",
                                    "deployment_id": deployment_id,
                                    "actor": ctx.get("actor")})
            return {"deployment_id": deployment_id, "removed": False,
                    "backlogged": True}
        del state["deployments"][deployment_id]
        if deployment_id in state["cleanup_backlog"]:
            state["cleanup_backlog"].remove(deployment_id)
        self.artifact_store.pop(deployment_id, None)
        ev = {"op": "preview-cleanup", "deployment_id": deployment_id,
              "actor": ctx.get("actor")}
        self._store(state, ev)
        return {"deployment_id": deployment_id, "removed": True}

    def inject_outage(self, on: bool = True) -> None:
        self.outage = on

    def snapshot(self) -> dict:
        return self._load()
