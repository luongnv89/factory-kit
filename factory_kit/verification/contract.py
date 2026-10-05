#!/usr/bin/env python3
"""Verification contract — the operator-approved gate data (issue #11 /
Task 2.6, PRD §3.2 F04, §6.3 CFG05).

The contract is *data the manifest already validated*: the recorded
``verification`` block of the effective configuration (Task 2.1).
Two independent halves define it, and the union must be nonempty — a
project with no protected checks still needs explicit acceptance
commands, and contexts alone do not waive the acceptance commands:

- ``required_checks`` — the protected-check provider, the required
  check *contexts* (job names) and the *permitted terminal
  conclusions*. A skipped/neutral conclusion counts only when it is
  literally in ``conclusions`` — with the default ``[success]`` it
  does not (A4).
- ``acceptance_commands`` — the explicit project acceptance commands
  recorded at setup (CFG05).

A missing ``verification`` block — or one whose contexts and commands
are *both* empty — is not an empty gate: it is a missing contract,
and a missing contract blocks (A4). :func:`contract_from_effective`
therefore returns ``None`` rather than a vacuous pass, and it does so
even when handed a manifest that never went through
:func:`factory_kit.config.schema.validate` — verification fails closed,
never trusting the caller to have validated (A4).

Everything here is pure: no store, no remote — the evaluation of one
observed check-run set against the contract is a deterministic function
so the gate can be replayed and fixed (§7.1 reproducible evidence).
"""

from __future__ import annotations

import hashlib
import json

__all__ = [
    "VerificationContract",
    "contract_from_effective",
    "contract_digest",
    "evaluate_checks",
]


class VerificationContract:
    """The nonempty operator-approved contract (A4)."""

    def __init__(self, *, provider, contexts, conclusions,
                 acceptance_commands):
        self.provider = provider
        self.contexts = tuple(contexts)
        self.conclusions = frozenset(conclusions)
        self.acceptance_commands = tuple(acceptance_commands)

    def as_dict(self):
        return {
            "provider": self.provider,
            "contexts": list(self.contexts),
            "conclusions": sorted(self.conclusions),
            "acceptance_commands": list(self.acceptance_commands),
        }


def contract_from_effective(effective):
    """Build the contract from a validated effective configuration.

    Returns ``None`` when the ``verification`` block is missing or
    defines neither required contexts nor acceptance commands — the
    "missing contract" case A4 blocks on. A contract is otherwise
    returned with exactly the declared gate: a repo with no protected
    checks is covered by its acceptance commands, never waved through.
    """
    verification = (effective or {}).get("verification") or {}
    checks = verification.get("required_checks") or {}
    contexts = [c for c in (checks.get("contexts") or [])
                if isinstance(c, str) and c.strip()]
    conclusions = [c for c in (checks.get("conclusions") or [])
                   if isinstance(c, str) and c.strip()]
    commands = [c for c in (verification.get("acceptance_commands") or [])
                if isinstance(c, str) and c.strip()]
    if not contexts and not commands:
        return None
    return VerificationContract(
        provider=checks.get("provider") or "github",
        contexts=contexts,
        conclusions=conclusions or ("success",),
        acceptance_commands=commands)


def contract_digest(contract) -> str:
    """Digest the gate the observation was evaluated under — a
    protection/permission drift later is detectable because the
    evidence row carries the digest it was checked against (A6)."""
    canon = json.dumps(contract.as_dict(), sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def evaluate_checks(observed, contract):
    """Evaluate one observed check-run set against the contract.

    ``observed`` is the remote's ``check_runs(sha)`` list. Returns
    ``{"status": "passed"|"failed"|"blocked", "reasons": [...],
    "checks": [...]}`` where ``checks`` is the normalized per-run
    identity/conclusion view persisted as evidence.

    - every declared context must appear in the observed runs — a
      required check that never ran is ``failed`` (A3);
    - a context whose latest run has not finished is ``blocked`` —
      the gate cannot pass on a pending CI, and pending is not
      unknown (the remote answered fine);
    - a finished context's conclusion must be one of the contract's
      permitted conclusions — ``skipped``/``neutral`` count *only*
      when the contract names them (A4).
    """
    runs = list(observed or [])
    by_name = {}
    for run in runs:
        name = run.get("name")
        if name:
            by_name[name] = run      # latest observed run per context
    normalized = [{
        "id": run.get("id"),
        "name": run.get("name"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "app": run.get("app"),
        "details_url": run.get("details_url"),
    } for run in runs]

    reasons = []
    blocked = False
    for context in contract.contexts:
        run = by_name.get(context)
        if run is None:
            reasons.append(f"required-check-missing:{context}")
            continue
        if run.get("status") != "completed":
            reasons.append(
                f"check-incomplete:{context}={run.get('status')}")
            blocked = True
            continue
        conclusion = run.get("conclusion")
        if conclusion not in contract.conclusions:
            reasons.append(
                f"check-conclusion:{context}={conclusion}")
    if reasons:
        return {"status": "blocked" if blocked and
                all(r.startswith("check-incomplete:") for r in reasons)
                else "failed",
                "reasons": reasons, "checks": normalized}
    return {"status": "passed", "reasons": [], "checks": normalized}
