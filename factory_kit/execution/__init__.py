#!/usr/bin/env python3
"""factory_kit.execution — the one bounded sequential IDD execution
lane (issue #9 / Task 2.4, F03).

Three cooperating pieces over the durable intake tables:

- :mod:`~factory_kit.execution.preflight` — the fail-closed gate: tools,
  models, capabilities, skills and endpoint safety must verify before
  any session exists (A3).
- :mod:`~factory_kit.execution.worker` — the worker port: the Hermes
  kanban adapter (supported claims/transitions only) and the scripted
  test fixture. Implementation and review run in distinct sequential
  sessions (A2); worker output is a request, never a committed
  transition (A3).
- :mod:`~factory_kit.execution.limits` — the §7.3 budget arithmetic:
  attempts / active-worker-seconds / wall-clock boundaries, warn-once
  at 80%, park at 100%, honest "unknown" usage (A4).
- :class:`~factory_kit.execution.lane.ExecutionLane` — the lane itself:
  single-lane acquisition, durable queueing with visible occupied
  reasons, monotonic attempt/generation identity, heartbeat fencing
  before replacement (A6), operator-retry generations (A5) and the
  idle audit (A7).
"""

from .lane import ExecutionLane, LaneError
from .limits import LaneLimits, budget_status
from .preflight import dispatch_gate
from .worker import (DispatchContext, HermesKanbanWorker,
                     ScriptedWorker, SessionHandle, WorkerError,
                     WorkerPort, WorkerResult, new_session_id)

__all__ = [
    "DispatchContext",
    "ExecutionLane",
    "HermesKanbanWorker",
    "LaneError",
    "LaneLimits",
    "ScriptedWorker",
    "SessionHandle",
    "WorkerError",
    "WorkerPort",
    "WorkerResult",
    "budget_status",
    "dispatch_gate",
    "new_session_id",
]
