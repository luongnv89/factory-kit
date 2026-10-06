#!/usr/bin/env python3
"""The merge-time condition gate — issue #17 / Task 3.3, F12 A1–A5.

:func:`evaluate_merge_guard` is the pure half of the guarded merge:
given the durable approval row it would spend, the work, the current
effective configuration and every *fresh* remote read the merge owner
just made, it returns the explicit blocker set. It re-checks nothing
from cache and trusts nothing from an earlier stage — the approval
service's ``consume_tx`` re-runs the durable lapse checks again inside
the intent transaction; this gate owns the *remote-side* conditions
the approval could not see:

- PR open + non-draft + mergeable, on the exact approved head and
  base (A1/A5);
- every required check context observed on the head with a permitted
  conclusion (the verification contract, evaluated now — A1);
- an armed auto-merge request on the PR, or repository-level
  auto-merge capability, is a *competing merge owner* — blocked
  outright; the kit never enables auto-merge (A5);
- the repository must *enforce* the contract the guard just read:
  branch protection present, required check contexts covering the
  contract, strict up-to-date base, and enforce-admins — without
  strict+enforce-admins the expected-head merge cannot close the
  base/check race and the merge is *unsupported* (A4);
- the bound preview still holds: verified, unexpired, smoke observed
  *passing* and younger than ``endpoint.merge.max_smoke_age_minutes``
  — or, under a declared ``endpoint.preview.provider: none``, the request
  binds exactly ``schema.NO_PREVIEW_BINDING`` and no preview row exists
  (A2).

The gate answers ``{"ok", "reason", "blockers", "bound"}`` — every
failing condition is named, ordered authoritative-first; ``bound``
carries the exact identities the intent commits on success.
"""

from __future__ import annotations

import json as _json

from factory_kit.config import schema
from factory_kit.durable.store import _iso_to_epoch
from factory_kit.execution.limits import TERMINAL_WORK_STATES
from factory_kit.verification.contract import (
    contract_from_effective,
    evaluate_checks,
)

__all__ = ["evaluate_merge_guard"]

#: The merge methods the remote's capability map can name.
_METHOD_CAPABILITY = {
    "squash": "allow_squash_merge",
    "merge": "allow_merge_commit",
    "rebase": "allow_rebase_merge",
}

#: GitHub ``mergeable`` values the remote may report.
_MERGEABLE_PENDING = "UNKNOWN"
_MERGEABLE_OK = "MERGEABLE"

#: GitHub ``mergeStateStatus`` values — only CLEAN is mergeable now.
_MERGE_STATE_OK = "CLEAN"
_MERGE_STATE_PENDING = "UNKNOWN"


def evaluate_merge_guard(*, request, work, effective, fence, pause,
                         evidence, preview, pr, base_sha, runs,
                         protection, capabilities, actor_login=None,
                         now, max_smoke_age_s):
    """Evaluate every merge-time condition against current state.

    All inputs are already-read rows/observations — this function is
    pure. Returns ``{"ok": True, "bound": {...}}`` or
    ``{"ok": False, "reason": <first blocker>, "blockers": [...]}``.
    """
    blockers = []

    # -- durable boundary (re-run inside the intent commit by
    #    ``consume_tx`` — checked here too so a remote read is never
    #    even consulted for an already-dead authority).
    if work is None:
        return {"ok": False, "reason": "unknown-work",
                "blockers": ["unknown-work"]}
    if request is None:
        return {"ok": False, "reason": "no-approved-request",
                "blockers": ["no-approved-request"]}
    if fence is not None and fence.get("fenced"):
        blockers.append("fenced")
    if work.get("state") in TERMINAL_WORK_STATES:
        blockers.append("work-terminal")
    if (pause or {}).get("pause_state") in ("requested", "paused"):
        blockers.append("task-paused")
    if now > float(request.get("expires_epoch") or 0):
        blockers.append("expired")
    if effective is None:
        blockers.append("stale-config")
    elif (request.get("config_digest") and
            schema.effective_digest(effective) !=
            request["config_digest"]) or \
            (request.get("policy_digest") and
             schema.policy_digest(effective) !=
             request["policy_digest"]):
        blockers.append("stale-config")

    # -- the bound evidence set: verified observation + independent
    #    review identity + the bound preview identity (A1).
    if evidence is None or evidence.get("status") != "verified":
        blockers.append("evidence-not-current")
    elif not evidence.get("review_id"):
        blockers.append("independent-review-missing")
    if effective is not None and not schema.preview_required(effective):
        # Declared no-preview contract: passes only when the request
        # binds exactly "no preview" and no preview row exists — a row
        # appearing or a request minted under another contract fails
        # closed. Smoke freshness has nothing to measure here.
        if preview is not None:
            blockers.append("preview-unexpected")
        elif request.get("preview_id") is not None or \
                request.get("preview_digest") != \
                schema.NO_PREVIEW_BINDING:
            blockers.append("preview-contract-mismatch")
    elif preview is None or \
            preview.get("preview_id") != request.get("preview_id"):
        blockers.append("preview-moved")
    elif preview.get("state") != "verified":
        blockers.append("preview-not-verified")
    else:
        smoke = preview.get("smoke_observed")
        try:
            smoke = _json.loads(smoke) if smoke else None
        except ValueError:
            # A blob that cannot parse is *unobserved* smoke — the
            # freshness/health gate fails closed, never crashes (A2).
            smoke = None
        if smoke is None:
            blockers.append("smoke-unobserved")
        elif not smoke.get("ok"):
            blockers.append("smoke-failed")
        age = now - _iso_to_epoch(preview.get("observed_at"))
        if age > float(max_smoke_age_s):
            blockers.append("smoke-stale")

    # -- the fresh authoritative PR read (A1/A5).
    method = request.get("merge_method")
    if pr is None:
        blockers.append("pr-not-found")
    else:
        state = str(pr.get("state") or "").upper()
        if state != "OPEN":
            blockers.append(f"pr-not-open:{state.lower() or 'unknown'}")
        if pr.get("draft"):
            blockers.append("pr-draft")
        if pr.get("head") != request.get("head_sha"):
            blockers.append("head-moved")
        if request.get("base_name") and \
                pr.get("base") != request["base_name"]:
            blockers.append("base-changed")
        mergeable = str(pr.get("mergeable") or "").upper()
        if mergeable == _MERGEABLE_PENDING:
            blockers.append("mergeable-pending")
        elif mergeable and mergeable != _MERGEABLE_OK:
            blockers.append("merge-conflict")
        merge_state = str(pr.get("merge_state") or "").upper()
        if merge_state == _MERGE_STATE_PENDING:
            blockers.append("merge-state-pending")
        elif merge_state and merge_state != _MERGE_STATE_OK:
            blockers.append(f"merge-state-{merge_state.lower()}")
        if pr.get("auto_merge"):
            # An armed auto-merge is a competing merge owner — the kit
            # never enables it and never merges over it (A5).
            blockers.append("auto-merge-armed")

    # -- base drift between the approved evidence and *now* (A4): the
    #    strict-up-to-date protection would refuse this merge anyway —
    #    name it before the remote does.
    if request.get("base_sha") and base_sha is not None and \
            base_sha != request["base_sha"]:
        blockers.append("base-moved")

    # -- required checks re-evaluated on the current head, now (A1).
    contract = contract_from_effective(effective) \
        if effective is not None else None
    if contract is None:
        blockers.append("verification-contract-missing")
    else:
        gate = evaluate_checks(runs, contract)
        if gate["status"] != "passed":
            blockers.extend(
                f"checks-{gate['status']}:{r}"
                for r in gate["reasons"])

    # -- repository-enforced protections (A4/A5): the expected-head
    #    merge is only safe when the repository itself refuses a stale
    #    merge — protection present, the contract's contexts required,
    #    strict up-to-date base, admins not bypassable.
    if not protection or not protection.get("protected"):
        blockers.append("protection-missing")
    else:
        if not protection.get("strict"):
            blockers.append("strict-base-unenforced")
        if not protection.get("enforce_admins"):
            blockers.append("bypassable-protection")
        required = set(protection.get("required_checks") or [])
        missing = set(contract.contexts) - required \
            if contract is not None else set()
        if missing:
            blockers.append(
                "required-checks-unprotected:"
                + ",".join(sorted(missing)))

    # -- repository capabilities (A5): auto-merge must never be
    #    enabled, and the selected method must be a supported one.
    if capabilities is not None:
        if capabilities.get("allow_auto_merge"):
            blockers.append("auto-merge-enabled")
        cap_key = _METHOD_CAPABILITY.get(method)
        if cap_key is not None and not capabilities.get(cap_key):
            blockers.append("method-unsupported")

    configured = ((effective or {}).get("endpoint") or {}) \
        .get("merge") or {}
    if not method or method != configured.get("method") or \
            method not in _METHOD_CAPABILITY:
        blockers.append("method-unsupported")

    if blockers:
        return {"ok": False, "reason": blockers[0],
                "blockers": blockers}
    return {"ok": True, "reason": None, "blockers": [],
            "bound": {
                "pr_number": str(pr.get("number")),
                "expected_head": request.get("head_sha"),
                "expected_base_name": pr.get("base"),
                "expected_base_sha":
                    base_sha or request.get("base_sha"),
                "merge_method": method,
                "actor_login": actor_login,
            }}
