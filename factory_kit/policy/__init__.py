#!/usr/bin/env python3
"""factory_kit.policy — execution-authority policy surface (Task 2.4).

This package is intentionally thin: the authorization policy itself is
data — the ``runtime`` / ``skills`` / ``limits`` / ``endpoint.disabled``
blocks of the effective configuration validated by
:mod:`factory_kit.config.schema` and digested via
:func:`~factory_kit.config.schema.policy_digest`. What lives here is
the documented split of who may decide:

- **Coordinator (Hermes/kit)** — commits all state transitions:
  acquire/release the lane, begin/finish attempts, fence, park,
  authorize new generations (``RegistrationStore.authorize_generation``
  requires an explicit ``authorized_by`` principal).
- **Worker (lane session)** — may only *request*: emit heartbeats and
  return a candidate ``WorkerResult`` through
  :meth:`IntakeStore.accept_result`. It holds no controller token, no
  store handle, no ambient authority — matching the spike's
  no-privileged-intents gate (GATE-S08).

The pre-dispatch evaluator consuming the policy data is
:func:`factory_kit.execution.preflight.dispatch_gate` — colocated with
the lane that enforces it.
"""

__all__ = []
