#!/usr/bin/env python3
"""Bounded-lane limits — F03/A4 budget accounting (issue #9 / Task 2.4).

The configured ``limits.*`` block (CFG06 — Q9-proposed, validated by
:mod:`factory_kit.config.schema`) is enforced here as *hard* boundaries:

- ``implementation_attempts`` — total implementation-role attempts
  (initial plus fix attempts); the default 2 means initial + one fix.
- ``active_worker_minutes`` — cumulative active worker execution across
  *all* roles; CI-wait time is excluded by construction (only worker
  ``active_seconds`` the runtime reports count).
- ``wall_hours`` — task wall time from first dispatch.

The 80%/100% contract (§7.3 active-execution-budget row): crossing 80%
of any budget emits one ``warning`` per (budget, work) identity — the
deduplicated alert row makes "warn once" structural — and reaching 100%
is ``exhausted``, which parks the work. A budget with *unknown* usage
(active_seconds never reported) is honestly reported as
``usage_unknown`` and never silently extended: an unmeasured attempt
cannot count *toward* exhaustion, and the gap is surfaced instead of
treated as zero.

Heartbeat and idle-audit constants are the §7.3/§5.1 targets, not
configuration: ``HEARTBEAT_TIMEOUT_S`` (60s without an expected beat
fences the generation) and ``IDLE_AUDIT_S`` (30 minutes without eligible
work ⇒ zero model calls reported).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "HEARTBEAT_TIMEOUT_S",
    "IDLE_AUDIT_S",
    "WARN_FRACTION",
    "TERMINAL_WORK_STATES",
    "ATTEMPT_OUTCOMES",
    "LaneLimits",
    "budget_status",
]

#: §7.3 — missing worker heartbeat: fence/quarantine before replacement.
HEARTBEAT_TIMEOUT_S = 60.0

#: §5.1 — idle audit window: 30 minutes with no eligible work and no
#: exceptions must record zero model calls.
IDLE_AUDIT_S = 30 * 60.0

#: §7.3 — warn once at 80%, stop/park at 100%.
WARN_FRACTION = 0.8

#: Work states that mean the lane's part is done or blocked — the work
#: can no longer receive active authority.
TERMINAL_WORK_STATES = ("completed", "parked", "quarantined", "blocked")

#: Attempt outcomes the ledger accepts at close.
ATTEMPT_OUTCOMES = ("completed", "failed", "fenced", "expired")


@dataclass(frozen=True)
class LaneLimits:
    """The dispatch-time limit snapshot bound to every attempt record.

    Built once from the validated effective configuration and persisted
    per attempt — a later config change can never retroactively widen
    the budget an already-running attempt was authorized under (A4).
    """

    active_tasks: int = 1
    implementation_attempts: int = 2
    active_worker_seconds: float = 60 * 60.0
    wall_seconds: float = 24 * 3600.0
    heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S
    warn_fraction: float = WARN_FRACTION

    @classmethod
    def from_effective(cls, effective) -> "LaneLimits":
        limits = (effective or {}).get("limits") or {}
        return cls(
            active_tasks=int(limits.get("active_tasks", 1)),
            implementation_attempts=int(
                limits.get("implementation_attempts", 2)),
            active_worker_seconds=float(
                limits.get("active_worker_minutes", 60)) * 60.0,
            wall_seconds=float(limits.get("wall_hours", 24)) * 3600.0)

    def as_dict(self) -> dict:
        return {
            "active_tasks": self.active_tasks,
            "implementation_attempts": self.implementation_attempts,
            "active_worker_seconds": self.active_worker_seconds,
            "wall_seconds": self.wall_seconds,
            "heartbeat_timeout_s": self.heartbeat_timeout_s,
            "warn_fraction": self.warn_fraction,
        }


def budget_status(*, measured_active_s, unknown_usage, attempts_used,
                  wall_elapsed_s, limits: LaneLimits) -> dict:
    """Evaluate the time budgets; return the worst applicable verdict.

    ``status`` is ``ok`` | ``warn`` | ``exhausted``. ``warnings`` names
    each budget at/above the 80% warn line (each is emitted at most once
    by the alert dedup); ``exceeded`` names each budget at/above 100%.
    ``usage_unknown`` is set whenever any attempt returned no measured
    usage — honest reporting instead of a silent zero (A4).

    The attempts boundary is deliberately *not* in ``exceeded``: it
    gates starting another implementation session only, never the
    review that must still follow the last permitted attempt — the lane
    enforces it stage-aware and reports ``attempts-exhausted`` itself.
    """
    warnings, exceeded = [], []

    checks = (
        ("active_worker_seconds", float(measured_active_s),
         limits.active_worker_seconds),
        ("wall_seconds", float(wall_elapsed_s), limits.wall_seconds),
    )
    for name, used, limit in checks:
        if limit <= 0:
            exceeded.append(name)
            continue
        ratio = used / limit
        if ratio >= 1.0:
            exceeded.append(name)
        elif ratio >= limits.warn_fraction:
            warnings.append(name)

    status = "ok"
    if warnings:
        status = "warn"
    if exceeded:
        status = "exhausted"
    return {
        "status": status,
        "warnings": warnings,
        "exceeded": exceeded,
        "usage_unknown": bool(unknown_usage),
        "measured_active_s": measured_active_s,
        "attempts_used": attempts_used,
        "wall_elapsed_s": wall_elapsed_s,
    }
