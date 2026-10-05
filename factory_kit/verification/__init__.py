#!/usr/bin/env python3
"""factory_kit.verification — independent review and current-revision
verification (issue #11 / Task 2.6, PRD §3.2 F04).

Two cooperating pieces over the durable intake tables and the remote
port:

- :mod:`~factory_kit.verification.contract` — the operator-approved
  verification contract (protected-check contexts + explicit
  acceptance commands, CFG05) and the pure check-run evaluation the
  gate replays deterministically.
- :class:`~factory_kit.verification.service.VerificationService` —
  the F04 gate itself: the separate reviewer session's durable record
  (A1), the authoritative read of the linked PR's current head and
  required checks (A2), the stale/failed/blocked/unknown vocabulary
  that can never be called ``verified`` (A3), the nonempty-contract
  block (A4), the one-fix budget rule (A5) and the timestamped,
  never-merge-authority evidence trail (A6).
"""

from factory_kit.verification.contract import (
    VerificationContract,
    contract_digest,
    contract_from_effective,
    evaluate_checks,
)
from factory_kit.verification.service import (
    REVIEW_VERDICTS,
    VerificationService,
)

__all__ = [
    "REVIEW_VERDICTS",
    "VerificationContract",
    "VerificationService",
    "contract_digest",
    "contract_from_effective",
    "evaluate_checks",
]
