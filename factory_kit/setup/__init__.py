"""factory_kit.setup — reviewed additive setup and substantive readiness.

PRD §3.2 F01, §4.1 install flow, §6.1 integration ownership, §7.1
``setup_checked``; issue #7 / Task 2.2. The public surface:

- :mod:`factory_kit.setup.plan` — read-only inspection producing a
  reviewable inventory/diff of factory-owned additions and explicit
  integration edits, plus the ``accept`` gate
- :mod:`factory_kit.setup.apply` — additive idempotent apply of an
  accepted plan: writes only planned paths, preserves every unrelated
  byte (proven by checksum), records ownership and one
  registration/webhook/configuration/dependency entry
- :mod:`factory_kit.setup.readiness` — substantive readiness evaluation
  (executable capability, configured model/auth, supported
  host/Hermes/runtime/skill versions, cancellation, competing-owner and
  merge-precondition probes) and the ``setup_checked`` event path
- :mod:`factory_kit.setup.ownership` — the kit-owned per-project ledger:
  ownership checksums, remote-effect intents, the migration
  boundary/checkpoint records and the event trail
- :mod:`factory_kit.setup.upgrade` — reviewed versioned upgrade
  (F10, issue #26): compatibility-checked upgrade plan, fenced
  migration across durable stage boundaries, provenance pinning and
  documented rollback
- :mod:`factory_kit.setup.repair` — interrupted-migration recovery
  (F10, issue #26): restores the validated checkpoint byte-for-byte or
  parks with a specific conflict; dispatch never runs on a partially
  migrated configuration
- :mod:`factory_kit.setup.remove` — reviewed ownership-aware removal
  (F08, issue #19): uninstall plan, fenced authority settlement,
  explicit history choice, registration tombstone
"""

from . import apply, ownership, plan, readiness, remove, repair, upgrade

__all__ = ["apply", "ownership", "plan", "readiness", "remove",
           "repair", "upgrade"]
