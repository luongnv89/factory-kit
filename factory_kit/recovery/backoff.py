#!/usr/bin/env python3
"""Bounded retry/backoff + the F06 timing contracts (issue #13 /
Task 2.8, PRD §5.1, §7.3).

Every number the recovery/reconciliation contract names lives here once:

- ``NOMINAL_INTERVAL_S`` (≤60s) — the periodic reconciliation cadence;
  a configured interval larger than this is clamped, never silently
  honored (§5.1 "nominal interval ≤ 60 seconds").
- ``DISCOVERY_BOUND_S`` (120s) — a dropped webhook's eligible work must
  be discovered within this window of restored GitHub connectivity
  *absent server-directed backoff*, so self-imposed retry delay never
  exceeds it; upstream ``Retry-After`` guidance may (§5.1).
- ``STALE_REMOTE_S`` (300s) — no successful GitHub observation for five
  minutes marks remote state stale, alerts once per episode and stops
  evidence-dependent completion (§7.3 reconciliation-unavailable row).
- ``BACKOFF_CAP_S`` (300s) — the nominal retry cap "unless upstream
  requires a longer delay" (§5.1).
- ``RECOVERY_DEADLINE_S`` (60s) — restart recovery must recover or
  explicitly park all known active tasks inside it (§5.1).

:class:`BackoffPolicy` is deliberately deterministic — the fixtures and
the §5.1 bounds are asserted on exact values, so no jitter is applied.
"""

from __future__ import annotations

__all__ = [
    "BACKOFF_CAP_S",
    "DISCOVERY_BOUND_S",
    "NOMINAL_INTERVAL_S",
    "RECOVERY_DEADLINE_S",
    "STALE_REMOTE_S",
    "BackoffPolicy",
]

NOMINAL_INTERVAL_S = 60.0
DISCOVERY_BOUND_S = 120.0
STALE_REMOTE_S = 300.0
BACKOFF_CAP_S = 300.0
RECOVERY_DEADLINE_S = 60.0


class BackoffPolicy:
    """Bounded exponential backoff for remote-observation retries.

    ``delay(failures, retry_after_s)`` returns:

    - ``min(cap_s, base_s * factor ** (failures - 1))`` — bounded, never
      above the nominal 5-minute cap;
    - raised to ``retry_after_s`` when the remote supplied guidance —
      upstream may ask *longer* than the cap, and that wins (§5.1);
    - ``failures`` counts consecutive failed observations, starting at 1.
    """

    def __init__(self, base_s=1.0, factor=2.0, cap_s=BACKOFF_CAP_S):
        if base_s <= 0 or factor < 1 or cap_s <= 0:
            raise ValueError("backoff requires base>0, factor>=1, cap>0")
        self.base_s = float(base_s)
        self.factor = float(factor)
        self.cap_s = float(cap_s)

    def delay(self, failures, retry_after_s=None):
        failures = max(1, int(failures))
        delay = min(self.cap_s,
                    self.base_s * self.factor ** (failures - 1))
        if retry_after_s is not None:
            try:
                guidance = float(retry_after_s)
            except (TypeError, ValueError):
                guidance = None
            if guidance is not None and guidance > delay:
                # Server-directed backoff overrides the nominal cap —
                # the remote knows its own recovery window (§5.1).
                delay = guidance
        return delay
