"""factory_kit.durable — kit-owned durable intake records.

PRD §6.4 (REC02 accepted-work), §5.1 persistence; issue #8 / Task 2.3.

Hermes owns task/attempt/fence state in ``kanban.db``; the kit owns the
intake-side convergence rows — logical work identity, delivery dedup and
the ``work_accepted``/``work_rejected`` event trail (§7.1) — persisted in
a SQLite store under the same discipline the Sprint-1 fault probe proved
(``BEGIN IMMEDIATE`` + ``synchronous=FULL`` + a single serialized writer).

- :mod:`factory_kit.durable.store` — :class:`IntakeStore`, the durable
  intake record the webhook and reconciliation channels converge on.
"""

from . import store

__all__ = ["store"]
