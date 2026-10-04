#!/usr/bin/env python3
"""Disposable remote fixture for the Task 1.2 authority spike (issue #3).

Stands in for the recipe's two remote authorities — GitHub (branches, pull
requests, generic API mutations) and the Vercel preview provider — as one
JSON state file under a scratch directory the run may delete. It is
*privileged* by construction, in two ways the tests actually exercise:

- Every mutation goes through a handle that must present the scoped broker
  capability token. A handle built without it refuses every write, which is
  what "the worker holds no privileged credential" means inside the fixture.
- The state file is created read-only (mode 0o400), so a process that skips
  the adapter and tries to write ``remote.json`` directly gets
  ``PermissionError`` — an OS-level refusal, not a polite error.

``submit_request`` / ``await_response`` are the worker side of the boundary:
a request is a JSON file dropped into ``requests/``; the broker answers in
``responses/`` and is the only writer the remote accepts. Nothing here is a
production broker — the spike demonstrates the *supported primitive's*
ordering and policy semantics, per the task's implementation notes.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

REMOTE_FILE = "remote.json"
REQUESTS_DIR = "requests"
RESPONSES_DIR = "responses"


class BoundaryViolation(PermissionError):
    """A privileged-effect attempt that never reached the remote."""


def _atomic_write(path: Path, payload: dict, mode: int | None = None) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    if mode is not None:
        os.chmod(path, mode)


class DisposableRemote:
    """JSON-backed stand-in for the remote authorities the broker guards."""

    def __init__(self, root, token: str | None = None):
        self.root = Path(root)
        self.token = token
        self.state_path = self.root / REMOTE_FILE

    @classmethod
    def initialize(cls, root, repository: str, default_branch: str = "main"):
        """Create the disposable remote state; returns an unprivileged handle."""
        root = Path(root)
        (root / REQUESTS_DIR).mkdir(parents=True, exist_ok=True)
        (root / RESPONSES_DIR).mkdir(parents=True, exist_ok=True)
        state = {
            "repository": repository,
            "default_branch": default_branch,
            "branches": {default_branch: "e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5e5"},
            "pulls": {},
            "deployments": {},
            "merges": [],
            "api_mutations": [],
            "events": [],
        }
        _atomic_write(root / REMOTE_FILE, state, mode=0o400)
        return cls(root)

    # -- privileged surface -------------------------------------------------

    def _require(self):
        if self.token is None:
            raise BoundaryViolation(
                "no scoped broker credential: privileged effect refused")

    def _load(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _store(self, state: dict, event: dict) -> None:
        event = dict(event)
        event["seq"] = len(state["events"]) + 1
        state["events"].append(event)
        _atomic_write(self.state_path, state, mode=0o400)

    def _effect(self, op: str, ctx: dict, apply) -> dict:
        self._require()
        state = self._load()
        apply(state)
        event = {"op": op, **{k: ctx.get(k) for k in
                              ("op_id", "repository", "generation", "actor")}}
        self._store(state, event)
        return event

    # The five permitted privileged operations the spike exercises (A2's
    # "each permitted write": branch/PR publication, preview deployment,
    # merge invocation, generic API mutation).
    def publish_branch(self, branch: str, sha: str, ctx: dict) -> dict:
        def apply(state):
            state["branches"][branch] = sha
        return self._effect("branch-publish", ctx, apply)

    def open_pr(self, number: int, head: str, base: str, title: str, ctx: dict) -> dict:
        def apply(state):
            state["pulls"][str(number)] = {
                "head": head, "base": base, "title": title, "state": "open"}
        return self._effect("pr-publish", ctx, apply)

    def deploy_preview(self, head_sha: str, ctx: dict) -> dict:
        def apply(state):
            dep_id = f"dpl-{len(state['deployments']) + 1:04d}"
            state["deployments"][dep_id] = {
                "head_sha": head_sha,
                "url": f"https://preview-{dep_id}.example.test",
            }
        return self._effect("preview-deploy", ctx, apply)

    def invoke_merge(self, pr: int, expected_head: str, ctx: dict) -> dict:
        def apply(state):
            pull = state["pulls"].get(str(pr)) or {}
            if pull.get("head") != expected_head:
                raise BoundaryViolation(
                    f"expected-head mismatch on PR {pr}: "
                    f"remote head is {pull.get('head')!r}, not {expected_head!r}")
            pull["state"] = "merged"
            state["merges"].append({"pr": pr, "head": expected_head})
        return self._effect("merge-invoke", ctx, apply)

    def api_mutation(self, path: str, payload: dict, ctx: dict) -> dict:
        def apply(state):
            state["api_mutations"].append({"path": path, "payload": payload})
        return self._effect("api-mutation", ctx, apply)

    # -- read side -----------------------------------------------------------

    def snapshot(self) -> dict:
        """Authoritative read-back: the remote state file, not a report."""
        return self._load()


# ---------------------------------------------------------------------------
# Worker side of the boundary — untrusted request drop-box. The broker is the
# only process that reads these and can reach the remote.
# ---------------------------------------------------------------------------

def _check_op_id(op_id) -> None:
    """An op_id names a drop-box file, so it must be a plain file name — a
    value containing a separator (or ``.``/``..``) would let the worker
    escape ``requests/``/``responses/`` and overwrite e.g. ``remote.json``
    through the very channel that is supposed to be unmediated."""
    if (not isinstance(op_id, str) or not op_id
            or op_id in (".", "..") or Path(op_id).name != op_id):
        raise ValueError("request needs a safe string op_id")


def submit_request(root, request: dict) -> Path:
    """Drop one publication/control request for the broker to evaluate."""
    _check_op_id(request.get("op_id"))
    out = Path(root) / REQUESTS_DIR / f"{request['op_id']}.json"
    _atomic_write(out, request)
    return out


def await_response(root, op_id: str, timeout: float = 10.0):
    """Poll for the broker's response file; None on timeout."""
    _check_op_id(op_id)
    target = Path(root) / RESPONSES_DIR / f"{op_id}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if target.is_file():
            try:
                return json.loads(target.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        time.sleep(0.05)
    return None
