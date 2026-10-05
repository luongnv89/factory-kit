#!/usr/bin/env python3
"""factory_kit.recovery — restart recovery + periodic GitHub
reconciliation through Hermes ownership (issue #13 / Task 2.8, F06).

PRD §3.2 F06, §5.1 restart/reconciliation/backoff targets, §6.4
transition authority, §7.1 ``recovery_completed``, §7.3
reconciliation-unavailable alert.

Three cooperating pieces over the durable intake tables:

- :mod:`~factory_kit.recovery.backoff` — the §5.1 timing constants
  (≤60s nominal interval, 120s discovery bound, 5-minute retry cap and
  stale-remote threshold) and the deterministic bounded
  :class:`BackoffPolicy` that honors upstream retry guidance.
- :mod:`~factory_kit.recovery.poller` — the read-only issue observation
  port: :class:`GhIssuePoller` (production ``gh`` adapter) and
  :class:`ScriptedIssueSource` (fault-injecting fixture).
- :class:`~factory_kit.recovery.service.RecoveryService` — the
  coordinator: the bounded restart pass (repair bindings, reconcile
  intents by identity, sweep orphans, settle every known task resumed
  or explicitly parked inside 60s) and the scheduled reconcile tick
  (dropped-webhook discovery, revocation re-observation, linked-PR
  drift parks, the durable watermark and the deduplicated stale-remote
  alert that stops evidence-dependent completion).

Nothing here replays chat history, invents a scheduler or sends a
remote mutation — recovery converges durable identities through the
same store, intake, lane, broker and verification surfaces that
committed them.
"""

from .backoff import (
    BACKOFF_CAP_S,
    DISCOVERY_BOUND_S,
    NOMINAL_INTERVAL_S,
    RECOVERY_DEADLINE_S,
    STALE_REMOTE_S,
    BackoffPolicy,
)
from .poller import (
    GhIssuePoller,
    ScriptedIssueSource,
    issue_observation,
)
from .service import RecoveryError, RecoveryService

__all__ = [
    "BACKOFF_CAP_S",
    "DISCOVERY_BOUND_S",
    "NOMINAL_INTERVAL_S",
    "RECOVERY_DEADLINE_S",
    "STALE_REMOTE_S",
    "BackoffPolicy",
    "GhIssuePoller",
    "RecoveryError",
    "RecoveryService",
    "ScriptedIssueSource",
    "issue_observation",
]
