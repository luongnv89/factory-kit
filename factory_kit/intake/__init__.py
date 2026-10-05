"""factory_kit.intake — authorized durable webhook + reconciliation intake.

PRD §3.2 F02, §5.1 intake/duplicate/persistence, §7.1 intake events;
issue #8 / Task 2.3.

Turns authenticated, opted-in GitHub issue events into durable work
exactly once: signature, registration readiness, opt-in and
content-shape checks run inside one durable transaction, delivery
deduplication stays distinct from repository/issue/execution-generation
identity, and the Hermes task association is reserved before the
"safely queued" acknowledgement is returned. No second queue or
scheduler is invented — the kanban dispatcher owns dispatch.

- :mod:`factory_kit.intake.envelope` — the canonical intake envelope:
  normalization, context fingerprinting and the content gates
  (acceptance criteria present, no permission-granting prose)
- :mod:`factory_kit.intake.service` — :class:`IntakeService`, the
  decision pipeline both channels converge through
- :mod:`factory_kit.intake.webhook` — the GitHub webhook transport
  adapter (HMAC signature + payload normalization)
- :mod:`factory_kit.intake.reconcile` — the polling/reconciliation
  channel adapter (``hermes cron`` observations → the same ``deliver``)
"""

from . import envelope, reconcile, service, webhook

__all__ = ["envelope", "reconcile", "service", "webhook"]
