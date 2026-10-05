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
  ownership checksums, remote-effect intents and the event trail
"""

from . import apply, ownership, plan, readiness

__all__ = ["apply", "ownership", "plan", "readiness"]
