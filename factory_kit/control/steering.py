#!/usr/bin/env python3
"""Checkpointed scope steering — F09 (issue #25 / Task 4.1).

An allowlisted operator's scope change is a durable
``scope_revisions`` row *before* any acknowledgement (A1): target
generation, revised acceptance criteria, the restricted actor
reference and the command/steer audit identities. Application rides
the supported role/stage checkpoint machinery — never a live
injection, never a second scheduler:

- **Fence first (A2).** One commit writes the pending revision *and*
  ends the live attempt ``fenced`` — the fence is durable before
  termination runs and before any replacement obtains authority. A
  work with no in-flight attempt is already at its checkpoint: the
  revision applies inside the same commit.
- **Replacement authority (A2).** Apply binds the revised criteria
  fingerprint to the work context, records the *remaining* explicit
  budgets (active-time, fix-attempt and wall-time — consumed usage
  is retained on the same work key, never reset) and re-queues the
  work so the next attempt runs the revised criteria at the
  recorded checkpoint stage.
- **Evidence dies with the revision (A3).** Approvals, verified
  observations and preview evidence bound to the superseded context
  are invalidated; completion needs fresh matching evidence and a
  new revision-bound approval.
- **No revival (A4).** Canceled/fenced generations reject the
  command outright; a fenced attempt's late result is denied by
  ``accept_result`` (``attempt-fenced``); a ``live`` steering
  request on a runtime without the ``steering.live`` capability is
  answered with the checkpoint/restart behavior — no unsupported
  harness is dispatched.
- **Safe failure (A5).** A checkpoint that cannot be safely fenced
  (termination uncertain) or a replacement whose recorded remaining
  budget is exhausted parks with the durable reason — authority
  never overlaps.
"""

from __future__ import annotations

import hashlib
import json as _json
import uuid
from datetime import datetime

from factory_kit.durable.store import _utcnow
from factory_kit.execution import limits as _limits

__all__ = [
    "STEER_LIVE_CAPABILITY",
    "TERMINAL_WORK_STATES",
    "live_supported",
    "criteria_fingerprint",
    "new_steer_id",
    "checkpoint_stage",
    "remaining_budgets",
    "record_steer_tx",
    "apply_tx",
    "park_tx",
    "checkpoint_behavior_text",
]

#: The runtime capability a ``live`` steering request requires (F09
#: AC2). Absent from ``runtime.capabilities.allow``, live steering is
#: unsupported — the system explains checkpoint/restart behavior and
#: never enables an unsupported harness.
STEER_LIVE_CAPABILITY = "steering.live"

TERMINAL_WORK_STATES = _limits.TERMINAL_WORK_STATES


def _iso_to_epoch(ts) -> float:
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def live_supported(effective) -> bool:
    """True only when the validated config *allows* live steering —
    deny by default, exactly like every other capability gate."""
    caps = ((effective or {}).get("runtime") or {}).get(
        "capabilities") or {}
    return STEER_LIVE_CAPABILITY in (caps.get("allow") or []) and \
        STEER_LIVE_CAPABILITY not in (caps.get("deny") or [])


def criteria_fingerprint(criteria) -> str:
    """SHA-256 over the revised acceptance criteria — the context
    revision binding the replacement attempt's ``acceptance_ref``
    (persisted instead of an issue-body fingerprint, same rule as
    intake's context fingerprint)."""
    return hashlib.sha256(
        (criteria or "").encode("utf-8")).hexdigest()


def new_steer_id(command_id, work_key) -> str:
    """A stable audit identity: the command plus a short suffix —
    durable evidence survives the command that minted it."""
    del work_key  # identity is command-scoped; work is the row target
    return f"steer-{command_id}-{uuid.uuid4().hex[:6]}"


def checkpoint_stage(store, work_key, *, default="implementation"):
    """The supported role/stage checkpoint the replacement re-enters.

    With a live attempt it is that attempt's role — the fenced stage
    restarts under the revised criteria (a stage is the atomic unit;
    no mid-attempt injection). Once the fence lands the ledger shows
    no active attempt, so the most recent *fenced* attempt's role is
    the checkpoint the revision took effect at. Otherwise the durably
    held pause boundary, else the first stage.
    """
    records = store.attempt_record_rows(work_key)
    active = store.active_attempt_ids(work_key)
    if active:
        for rec in records:
            if rec["attempt_id"] == active[-1]:
                return rec["role"] or default
    pause = store.pause_info(work_key)
    if pause["pause_state"] in ("requested", "paused", "resumed") and \
            pause.get("paused_stage"):
        # The durable re-entry boundary is the checkpoint — a held or
        # resumed-at stage re-enters there under revised criteria.
        return pause["paused_stage"]
    if records:
        # Post-fence crash window: the most recent attempt's stage is
        # where the revision took effect.
        return records[-1]["role"] or default
    return default


def remaining_budgets(store, work_key, limits, now_epoch):
    """The recorded remaining explicit budgets a replacement receives
    (A2): bound limit minus durable consumption — measured active
    seconds, implementation attempts used and wall elapsed since the
    first attempt. Consumed usage lives on the same work key, so it is
    *retained*, never reset; unknown usage is reported honestly as a
    floor, matching the §7.1 measured-or-unknown rule."""
    measured, unknown = store.work_active_seconds(work_key)
    impl_used = store.count_attempts(work_key, role="implementation")
    starts = [_iso_to_epoch(r.get("started"))
              for r in store.attempt_record_rows(work_key)]
    starts = [s for s in starts if s]
    wall_elapsed = max(0.0, now_epoch - min(starts)) if starts else 0.0
    remaining = {
        "active_worker_seconds":
            max(0.0, float(limits.active_worker_seconds) - measured),
        "implementation_attempts":
            max(0, int(limits.implementation_attempts) - impl_used),
        "wall_seconds":
            max(0.0, float(limits.wall_seconds) - wall_elapsed),
    }
    return {
        "remaining": remaining,
        "consumed": {
            "active_worker_seconds": measured,
            "implementation_attempts": impl_used,
            "wall_seconds": wall_elapsed,
            "usage_unknown_attempts": unknown,
        },
        "exhausted": [name for name, value in remaining.items()
                      if value <= 0],
    }


def record_steer_tx(tx, store, *, cmd, work, fingerprint, received_at):
    """Persist the pending scope change inside the caller's
    ``transact`` — the second dedup layer under the control record:
    a duplicate command or a replayed delivery converges on the
    existing row (``one_steer_per_command``), so restart and double
    submission yield one accepted change (A1)."""
    prior = store.find_steer_by_command(cmd["command_id"])
    if prior is not None:
        return {"outcome": "duplicate", "steer": prior}
    work_key = work["work_key"]
    # Serialize inside the caller's BEGIN IMMEDIATE: a second steer
    # racing this one sees the committed pending row — at most one
    # un-applied scope change per work is admitted.
    if store.pending_steer(work_key) is not None:
        return {"outcome": "steer-pending", "steer":
                store.pending_steer(work_key)}
    revision = store.next_steer_revision(work_key)
    steer = store.record_steer(
        new_steer_id(cmd["command_id"], work_key),
        command_id=cmd["command_id"], work_key=work_key,
        authority_key=work["authority_key"],
        repo_id=cmd["repo_id"], issue=cmd["issue"],
        generation=cmd["generation"], revision=revision,
        criteria=cmd.get("criteria"),
        criteria_fingerprint=fingerprint,
        actor_ref=cmd["actor_ref"], chat_ref=cmd["chat_ref"],
        seq=tx.next_seq(), received_at=received_at)
    tx.record_event(
        "scope_steer_recorded", work_key=work_key,
        detail=_json.dumps({
            "steer_id": steer["steer_id"], "revision": revision,
            "generation": steer["generation"],
            "criteria_fingerprint": fingerprint,
            "mode": cmd.get("mode") or "checkpoint",
            "actor_ref": cmd["actor_ref"]}, sort_keys=True))
    return {"outcome": "recorded", "steer": steer}


def apply_tx(tx, store, steer, work, *, stage, effective, now=None):
    """Commit the checkpoint inside the caller's ``transact`` (A2/A3).

    Order is the contract: the old attempt's ledger rows fence
    *before* the revision binds — no replacement authority can ever
    predate the fence. Then the revised criteria fingerprint becomes
    the work's context, approvals + verified evidence die with the
    superseded revision, the recorded remaining budgets persist on the
    steer row, the checkpoint stage is stored and the work re-queues.
    An exhausted remaining budget parks instead — no replacement
    authority issues (A5).
    """
    work_key = work["work_key"]
    limits = _limits.LaneLimits.from_effective(effective)
    budgets = remaining_budgets(
        store, work_key, limits,
        (now() if callable(now) else now) or 0.0)
    tx.fence_attempts_tx(work_key, f"steer:{steer['steer_id']}")
    if budgets["exhausted"]:
        reason = "steer-budget-exhausted:" + ",".join(
            budgets["exhausted"])
        tx.update_steer(steer["steer_id"], state="parked",
                        reason=reason,
                        budgets=_json.dumps(budgets, sort_keys=True),
                        applied_at=_utcnow())
        tx.set_work_state(work_key, "parked", reason=reason)
        tx.enqueue_work(work_key, tx.next_seq(), state="parked",
                        reason=reason)
        tx.record_event("scope_steer_parked", work_key=work_key,
                        reason=reason,
                        detail=steer["steer_id"])
        return {"outcome": "parked", "reason": reason,
                "steer_id": steer["steer_id"]}
    fresh = tx.get_work(work_key) or work
    tx.update_work_context(
        work_key, steer["criteria_fingerprint"],
        fresh.get("issue_revision"))
    tx.invalidate_approvals_tx(work_key, "scope-steered")
    _stale_evidence_tx(tx, store, work_key, steer)
    tx.update_steer(steer["steer_id"], state="applied",
                    applied_stage=stage,
                    budgets=_json.dumps(budgets, sort_keys=True),
                    applied_at=_utcnow())
    # The checkpoint stage is durable via the pause-boundary channel:
    # ``resumed`` re-enters exactly there, and a held boundary keeps
    # its state while the stage is corrected to the fenced one.
    pause = store.pause_info(work_key)
    hold = pause["pause_state"] if pause["pause_state"] in (
        "requested", "paused") else "resumed"
    tx.set_pause(work_key, hold, stage=stage,
                 detail=f"steer {steer['steer_id']} checkpoint")
    tx.set_work_state(work_key, "pending", reason="steer-applied")
    tx.enqueue_work(
        work_key, tx.next_seq(),
        state="queued" if hold == "resumed" else "paused",
        reason="steer-applied" if hold == "resumed"
        else "steer-applied-held")
    tx.record_event(
        "scope_steer_applied", work_key=work_key,
        detail=_json.dumps({
            "steer_id": steer["steer_id"],
            "revision": steer["revision"], "stage": stage,
            "remaining": budgets["remaining"],
            "consumed": budgets["consumed"]}, sort_keys=True))
    return {"outcome": "applied", "steer_id": steer["steer_id"],
            "revision": steer["revision"], "stage": stage,
            "remaining": budgets["remaining"]}


def park_tx(tx, store, steer, work_key, *, reason, detail=None):
    """Park the pending change — the checkpoint could not be safely
    fenced (A5). The work parks with the durable reason and no
    replacement authority issues; the revision row stays auditable."""
    tx.update_steer(steer["steer_id"], state="parked", reason=reason,
                    applied_at=_utcnow())
    tx.set_work_state(work_key, "parked", reason=reason)
    tx.enqueue_work(work_key, tx.next_seq(), state="parked",
                    reason=reason)
    tx.record_event("scope_steer_parked", work_key=work_key,
                    reason=reason, detail=detail or steer["steer_id"])
    return {"outcome": "parked", "reason": reason,
            "steer_id": steer["steer_id"]}


def _stale_evidence_tx(tx, store, work_key, steer):
    """Mark the latest verified observation stale inside the caller's
    commit (A3) — same shape as ``VerificationService.mark_stale``:
    the lapsed row keeps its identities so the trail shows exactly
    which verified result the revision change voided."""
    latest = store.latest_evidence(work_key)
    if latest is None or latest["status"] != "verified":
        return None
    return tx.record_evidence(
        f"ev-steer-{uuid.uuid4().hex[:12]}", work_key=work_key,
        seq=tx.next_seq(),
        pr_number=latest["pr_number"], pr_url=latest["pr_url"],
        head_sha=latest["head_sha"], base_name=latest["base_name"],
        base_sha=latest["base_sha"],
        checks=_json.loads(latest["checks"])
        if latest["checks"] else None,
        review_id=latest["review_id"],
        review_session=latest["review_session"],
        artifacts=_json.loads(latest["artifacts"])
        if latest["artifacts"] else None,
        contract_digest=latest["contract_digest"],
        status="stale", reason="scope-steered")


def checkpoint_behavior_text(supported):
    """F09 AC2 — the honest capability sentence: what steering does on
    this runtime, never a live-injection claim."""
    if supported:
        return ("Steering applies at supported role/stage checkpoints "
                "and may also target a live worker the runtime "
                "declares supported.")
    return ("This runtime does not support live steering: the revised "
            "criteria apply at the next supported checkpoint — the "
            "current attempt is fenced and the replacement re-runs "
            "the stage within the remaining budgets. For immediate "
            "restart use 'cancel <repo> <issue> <gen>' then 'retry "
            "<repo> <issue>'.")
