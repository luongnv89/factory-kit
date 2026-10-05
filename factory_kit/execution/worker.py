#!/usr/bin/env python3
"""Worker port — the seam between the lane and the supported runtime.

F03/A2 (issue #9 / Task 2.4): the lane drives exactly one runtime — the
Hermes Kanban profile worker — through *supported* claims/transitions
only (``claim`` / ``heartbeat`` / ``complete`` / ``block`` /
``reclaim``; boundary map §"Required lifecycle operations"). No second
scheduler, no IDD backlog loop, no provider/model fallback: the context
the lane hands over pins role, model, skills, config digests and limits,
and a worker's output is a *candidate result* — it may request a
transition but only the lane commits one (A3).

Two implementations ship:

- :class:`HermesKanbanWorker` — the production seam: thin argv mapping
  onto ``hermes kanban`` verbs. Deliberately not exercised live in unit
  tests (no Hermes on the test host); the command shape is asserted.
- :class:`ScriptedWorker` — the deterministic fixture port: a dict of
  callables returns canned results; ``model_calls`` counts session
  starts so the idle audit can prove zero calls (A7). It is the test
  double — production code never selects it implicitly.
"""

from __future__ import annotations

import secrets as _secrets
import subprocess
from dataclasses import dataclass

__all__ = [
    "DispatchContext",
    "SessionHandle",
    "WorkerResult",
    "WorkerPort",
    "HermesKanbanWorker",
    "ScriptedWorker",
]

#: Roles the lane may dispatch — the schema's REQUIRED_ROLES, kept here
#: so the port can refuse an unsupported role before any subprocess
#: starts (A2: no unsupported subdelegation).
SUPPORTED_ROLES = ("implementation", "review")


@dataclass(frozen=True)
class DispatchContext:
    """Everything an attempt carries — the §6.4 Attempt inputs.

    Frozen: the worker cannot mutate it, and the record persisted at
    attempt start is exactly what the worker saw (monotonic
    task/attempt/fence identity, digests, pins, limits).
    """

    work_key: str
    task_id: str
    issue: int
    generation: int
    attempt_id: str
    role: str
    session_id: str
    runtime: str
    model: str
    skills: dict
    config_digest: str
    policy_digest: str
    workspace: str
    limits: dict
    acceptance_ref: str          # repository/issue criteria reference
    tools_allow: tuple = ()


@dataclass(frozen=True)
class SessionHandle:
    """A live role session — one per attempt; implementation and review
    never share one (A2's "distinct sessions")."""

    session_id: str
    attempt_id: str
    role: str
    started_epoch: float


@dataclass(frozen=True)
class WorkerResult:
    """A worker's candidate outcome — a *request*, never a committed
    transition. ``active_seconds=None`` / ``usage=None`` report unknown;
    the lane records them honestly rather than zero-filling."""

    verdict: str                     # completed | changes-requested |
                                     # failed | blocked
    detail: str = ""
    active_seconds: float | None = None
    # Aggregate counters only — this map is persisted verbatim into the
    # durable usage row + attempt_finished event, which never carries
    # bodies, transcripts or credentials (§7.1 evidence rules).
    usage: dict | None = None        # {"tokens": …, "provider": …} etc.


class WorkerPort:
    """The runtime contract the lane consumes. Implementations raise
    :class:`WorkerError` on transport failure — the lane turns that into
    a blocked/parked outcome, never a silent retry loop."""

    def start(self, ctx: DispatchContext) -> SessionHandle:
        raise NotImplementedError

    def heartbeat(self, handle: SessionHandle) -> None:
        raise NotImplementedError

    def collect(self, handle: SessionHandle) -> WorkerResult:
        raise NotImplementedError

    def terminate(self, handle: SessionHandle) -> str:
        """``confirmed`` or ``uncertain`` — never a lie: the caller maps
        ``uncertain`` to quarantine."""
        raise NotImplementedError

    @property
    def model_calls(self):
        """Model invocations this port has issued — the idle-audit
        counter (``None`` = not measurable by this runtime)."""
        return None


class WorkerError(Exception):
    """The runtime could not perform the operation."""


class HermesKanbanWorker(WorkerPort):
    """Thin adapter over ``hermes kanban`` — the only paved worker path
    (boundary map: external CLI lanes are no-go).

    Each verb is one subprocess call; the adapter invents no scheduling
    of its own — dispatch stays on the kanban side, this port only
    translates the lane's claims/liveness/result/termination ops.
    """

    def __init__(self, hermes_bin="hermes", timeout_s=60):
        self.hermes_bin = hermes_bin
        self.timeout_s = timeout_s
        self._starts = 0

    def _run(self, *argv) -> dict:
        import json as _json
        try:
            proc = subprocess.run(
                [self.hermes_bin, *argv],
                capture_output=True, text=True, timeout=self.timeout_s)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkerError(f"hermes {' '.join(argv)}: {exc}") from exc
        if proc.returncode != 0:
            raise WorkerError(
                f"hermes {' '.join(argv)} exited {proc.returncode}: "
                f"{proc.stderr.strip()[:200]}")
        try:
            return _json.loads(proc.stdout or "{}")
        except ValueError:
            return {"raw": proc.stdout.strip()[:500]}

    def kanban_argv(self, ctx: DispatchContext, verb) -> list:
        """The supported-verb command shape — ``claim`` binds task +
        role + model + skills + workspace in one atomic operation
        (boundary map §"Core concepts")."""
        skills = ",".join(f"{k}@{v}" for k, v in
                          sorted((ctx.skills or {}).items()))
        return ["kanban", verb, ctx.task_id,
                "--attempt", ctx.attempt_id,
                "--role", ctx.role,
                "--model", ctx.model,
                "--workspace", ctx.workspace,
                "--skills", skills]

    def start(self, ctx: DispatchContext) -> SessionHandle:
        if ctx.role not in SUPPORTED_ROLES:
            raise WorkerError(f"unsupported role {ctx.role!r}")
        self._run(*self.kanban_argv(ctx, "claim"))
        self._starts += 1
        return SessionHandle(session_id=ctx.session_id,
                             attempt_id=ctx.attempt_id,
                             role=ctx.role,
                             started_epoch=0.0)

    def heartbeat(self, handle: SessionHandle) -> None:
        self._run("kanban", "heartbeat", handle.attempt_id)

    def collect(self, handle: SessionHandle) -> WorkerResult:
        out = self._run("kanban", "complete", handle.attempt_id,
                        "--json")
        return WorkerResult(
            verdict=str(out.get("verdict", "completed")),
            detail=str(out.get("detail", ""))[:400],
            active_seconds=out.get("active_seconds"),
            usage=out.get("usage"))

    def terminate(self, handle: SessionHandle) -> str:
        try:
            self._run("kanban", "reclaim", handle.attempt_id)
        except WorkerError:
            return "uncertain"
        return "confirmed"

    @property
    def model_calls(self):
        return self._starts


class ScriptedWorker(WorkerPort):
    """Deterministic fixture port for tests and local proofs.

    ``script`` maps ``role`` → callable ``(ctx) -> WorkerResult`` (or a
    plain result / list consumed one per start). ``terminations`` maps
    ``attempt_id`` → ``"confirmed"|"uncertain"`` (default confirmed).
    Every ``start`` increments :attr:`model_calls` — the counter the
    A7 idle audit reads.
    """

    def __init__(self, script=None, *, terminations=None):
        self.script = dict(script or {})
        self.terminations = dict(terminations or {})
        self.started = []          # DispatchContext per session, in order
        self.handles = {}          # attempt_id -> SessionHandle
        self.beats = []
        self._model_calls = 0

    def start(self, ctx: DispatchContext) -> SessionHandle:
        if ctx.role not in SUPPORTED_ROLES:
            raise WorkerError(f"unsupported role {ctx.role!r}")
        handle = SessionHandle(session_id=ctx.session_id,
                               attempt_id=ctx.attempt_id,
                               role=ctx.role,
                               started_epoch=0.0)
        self.started.append(ctx)
        self.handles[ctx.attempt_id] = handle
        self._model_calls += 1
        return handle

    def heartbeat(self, handle: SessionHandle) -> None:
        self.beats.append(handle.attempt_id)

    def collect(self, handle: SessionHandle) -> WorkerResult:
        spec = self.script.get(handle.role)
        if callable(spec):
            out = spec(self.started[-1])
        elif isinstance(spec, (list, tuple)):
            idx = sum(1 for c in self.started
                      if c.role == handle.role) - 1
            out = spec[min(max(idx, 0), len(spec) - 1)] if spec else \
                WorkerResult(verdict="completed")
        else:
            out = spec if isinstance(spec, WorkerResult) else \
                WorkerResult(verdict="completed")
        if isinstance(out, WorkerResult):
            return out
        return WorkerResult(verdict=str(out))

    def terminate(self, handle: SessionHandle) -> str:
        return self.terminations.get(handle.attempt_id, "confirmed")

    @property
    def model_calls(self):
        return self._model_calls


def new_session_id() -> str:
    """A fresh session identity per attempt — the concrete mechanism
    that keeps implementation and review in distinct sessions (A2)."""
    return f"sess-{_secrets.token_hex(6)}"
