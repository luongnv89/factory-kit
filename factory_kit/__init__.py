"""factory-kit — thin Hermes integration/plugin (issue #6 / Task 2.1).

Package contract finalized from spike evidence (PRD §6.2/§6.3, decision
record ``docs/decisions/package-config-contract.md``):

- **Language:** Python 3, standard library only (the ``gi-*.py`` convention
  proved across the Sprint-1 probes).
- **Package shape:** native general plugin ``factory-kit`` discovered at
  ``~/.hermes/plugins/factory-kit/``; ``register(ctx)`` wiring lands with the
  public interfaces in later Task 2.x issues — this package ships the
  configuration and registration contracts those interfaces consume.
- **Storage:** Hermes owns task/attempt/fence state in ``kanban.db``; the kit
  owns registration, delivery-dedup, control and approval rows (Q6 split),
  persisted as per-profile JSON that can never dispatch by itself.
"""

VERSION = "0.1.0"

#: Manifest schema versions this package can validate. Bump only with a
#: recorded migration; the version is part of the registration record.
SUPPORTED_SCHEMA_VERSIONS = (1,)
