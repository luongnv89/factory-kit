#!/usr/bin/env python3
"""Apply an accepted setup plan — additive, idempotent, preserve-first.

PRD §3.2 F01 (A2, A4), §4.1 install flow; issue #7 / Task 2.2.

:func:`apply_plan` is the only writer in the setup package, and it only
executes a plan whose ``acceptance`` record binds the same ``plan_digest``
the plan still carries — the review gate (A1: no mutation before explicit
acceptance).

Preservation contract (A2):

- File writes happen only for ``configuration``/``integration-edit``
  entries, only at their planned path inside the repository, and never
  through a symlink. A target whose bytes drifted since review, or that
  exists with unexpected content, is a ``conflict`` — reported, never
  overwritten. User edits are preserved work, not an obstacle.
- No stash, no reset, no delete. After writing, every ``preserved[]``
  path the plan checksummed is re-verified; a violation is reported in
  the result, proving (rather than promising) unrelated bytes survived.
- Remote effects (webhook, registration, dependency pins) go through an
  injected adapter. The default :class:`RecordingEffectSink` persists
  ``recorded-intent`` rows in the :class:`SetupStore` — honest pending
  records, since live remote mutation (``kanban-live-write``) remains a
  named carried blocker. Registration uses the real
  :class:`RegistrationStore` when supplied: one row per ``repo_id``,
  ``readiness.verdict = "pending"`` — present but enabled for nothing
  until substantive readiness passes (A6).

Idempotency (A2): a second apply of the same accepted plan returns every
entry ``unchanged``, an empty ``effective_diff`` and no duplicated
effects, ownership rows or registration rows.
"""

from __future__ import annotations

import os

from . import plan as plan_mod
from .ownership import SetupStore, sha256_file

__all__ = ["SetupError", "RecordingEffectSink", "apply_plan"]

_PENDING_READINESS = {
    "verdict": "pending",
    "detail": "registration created by an accepted plan; enabled only "
              "when substantive readiness passes (A6)",
}


class SetupError(Exception):
    """The apply precondition failed — unaccepted plan, non-appliable
    plan, or a target that violates the preservation contract."""


class RecordingEffectSink:
    """Default remote-effect adapter: records intents in the ledger.

    Each effect is idempotent on the plan entry's stable ``id`` — a second
    run returns ``existing`` rather than recording a duplicate.
    """

    def __init__(self, store: SetupStore):
        self.store = store

    def apply(self, entry) -> dict:
        effect_id = entry["id"]
        if self.store.has_effect(effect_id):
            return {"effect_id": effect_id, "status": "existing"}
        self.store.record_effect(effect_id, {
            "kind": entry["kind"],
            "action": entry["action"],
            "detail": entry.get("detail", {}),
            "status": "recorded-intent",
        })
        return {"effect_id": effect_id, "status": "recorded-intent"}


def _safe_target(repo_root, rel_path, entry_id):
    """Resolve a plan path inside the repo; refuse traversal/symlinks.

    ``abspath`` catches ``..`` escapes lexically; ``realpath`` catches
    symlinked *parent* directories that would resolve the write outside
    the repository even when the leaf is not itself a link.
    """
    root = os.path.abspath(str(repo_root))
    target = os.path.abspath(os.path.join(root, rel_path))
    if not (target == root or target.startswith(root + os.sep)):
        raise SetupError(
            f"entry {entry_id}: path {rel_path!r} escapes the repository")
    real_root = os.path.realpath(root)
    real_target = os.path.realpath(target)
    if not (real_target == real_root or
            real_target.startswith(real_root + os.sep)):
        raise SetupError(
            f"entry {entry_id}: path {rel_path!r} resolves outside the "
            "repository via a symlinked parent")
    if os.path.islink(target):
        raise SetupError(
            f"entry {entry_id}: {rel_path!r} is a symlink — setup never "
            "writes through links")
    return target


def _apply_file_entry(entry, repo_root, store, plan_id) -> dict:
    path = entry["path"]
    target = _safe_target(repo_root, path, entry["id"])
    planned_sha = entry["sha256"]
    payload = entry["payload"].encode("utf-8")
    exists = os.path.isfile(target)
    current_sha = sha256_file(target) if exists else None

    if entry["action"] == "keep":
        if current_sha == planned_sha:
            store.record_file(path, planned_sha, len(payload),
                              kind=entry["kind"], plan_digest=plan_id)
            return {"id": entry["id"], "status": "unchanged",
                    "detail": "identical bytes already present"}
        return {"id": entry["id"], "status": "conflict",
                "detail": f"{path} exists but does not match the reviewed "
                          "bytes — re-inspect and accept a new plan"}

    if entry["action"] == "replace" and not exists:
        return {"id": entry["id"], "status": "conflict",
                "detail": f"{path} existed at review and is now gone — "
                          "the base the replace was reviewed against "
                          "drifted; re-inspect to proceed"}
    if exists:
        if current_sha == planned_sha:
            store.record_file(path, planned_sha, len(payload),
                              kind=entry["kind"], plan_digest=plan_id)
            return {"id": entry["id"], "status": "unchanged",
                    "detail": "already applied"}
        if entry["action"] == "replace" and \
                current_sha == entry.get("replaces_sha256"):
            pass  # the reviewed base is intact — the replace is safe
        else:
            return {"id": entry["id"], "status": "conflict",
                    "detail": f"{path} drifted since review — preserved "
                              "as developer work; re-inspect to proceed"}

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "wb") as handle:
        handle.write(payload)
    store.record_file(path, planned_sha, len(payload),
                      kind=entry["kind"], plan_digest=plan_id)
    return {"id": entry["id"], "status": "applied",
            "detail": f"{entry['action']}d {path}"}


def _apply_registration(entry, plan, registration_store,
                        supported_versions) -> dict:
    if registration_store is None:
        return {"id": entry["id"], "status": "skipped",
                "detail": "no registration store configured — effect "
                          "recorded as intent"}
    effective = plan["manifest"]["effective"]
    result = registration_store.register(
        effective,
        readiness=dict(_PENDING_READINESS),
        supported_versions=supported_versions or [])
    outcome = result["outcome"]
    if outcome == "registered":
        return {"id": entry["id"], "status": "applied",
                "detail": "registration row created (readiness pending)"}
    return {"id": entry["id"], "status": "unchanged",
            "detail": f"registration already exists (outcome={outcome})"}


def apply_plan(plan, repo_root, store, *, registration_store=None,
               supported_versions=None, effects=None) -> dict:
    """Apply an accepted plan. Returns the per-entry result report.

    Raises :class:`SetupError` when the plan is unaccepted, non-appliable,
    or carries a digest/acceptance mismatch — those are review-gate
    failures, never partial applies.
    """
    acceptance = plan.get("acceptance") or {}
    if not acceptance:
        raise SetupError(
            "plan is not accepted — apply requires explicit operator "
            "acceptance of the reviewed plan_digest (A1)")
    if plan_mod.plan_digest(plan) != plan.get("plan_digest"):
        raise SetupError(
            "plan digest does not match its contents — the plan was "
            "modified after generation; re-inspect")
    if acceptance.get("plan_digest") != plan.get("plan_digest"):
        raise SetupError(
            "acceptance digest does not match this plan — the bytes under "
            "review changed; re-accept the current plan")
    # ``appliable`` is a derived flag, not digest-covered — re-derive it
    # from the fields that are (conflicts + the effective manifest), so a
    # hand-edited flag cannot smuggle a conflicted plan through.
    if not plan.get("appliable") or plan.get("conflicts") or \
            not (plan.get("manifest") or {}).get("effective"):
        raise SetupError(
            "plan is not appliable — conflicts must be resolved and the "
            "repository re-inspected")
    store = store if isinstance(store, SetupStore) else SetupStore(store)
    effects = effects or RecordingEffectSink(store)
    repo_root = os.path.abspath(str(repo_root))
    plan_id = plan["plan_digest"]
    # Bind the ledger to this project — a ledger already owned by a
    # different repo_id refuses (OwnershipError), so state can never be
    # silently mixed across projects.
    effective = (plan.get("manifest") or {}).get("effective") or {}
    repo_id = (effective.get("identity") or {}).get("repo_id")
    if repo_id:
        store.set_project_id(repo_id)

    results, applied, unchanged, conflicts = [], [], [], []
    for entry in plan["entries"]:
        kind = entry["kind"]
        if kind in ("configuration", "integration-edit"):
            result = _apply_file_entry(entry, repo_root, store, plan_id)
        elif kind == "registration":
            result = _apply_registration(
                entry, plan, registration_store, supported_versions)
            if result["status"] == "skipped":
                outcome = effects.apply(entry)
                result["status"] = "applied" \
                    if outcome["status"] != "existing" else "unchanged"
                result["detail"] += f" ({outcome['status']})"
        elif kind in ("webhook", "dependency"):
            outcome = effects.apply(entry)
            status = "unchanged" if outcome["status"] == "existing" \
                else "applied"
            result = {"id": entry["id"], "status": status,
                      "detail": f"{kind} effect {outcome['status']}"}
        else:
            result = {"id": entry.get("id", "?"), "status": "conflict",
                      "detail": f"unknown entry kind {kind!r}"}
        results.append(result)
        if result["status"] == "applied":
            applied.append(entry["id"])
        elif result["status"] == "unchanged":
            unchanged.append(entry["id"])
        elif result["status"] == "conflict":
            conflicts.append(result)

    # -- preservation proof (A2) ---------------------------------------------
    violations = []
    checked = 0
    for rec in plan.get("preserved", []):
        if rec.get("sha256") is None:
            continue
        target = os.path.join(repo_root, rec["path"])
        checked += 1
        if not os.path.isfile(target):
            violations.append({"path": rec["path"], "state": "missing"})
            continue
        if sha256_file(target) != rec["sha256"]:
            violations.append({"path": rec["path"],
                               "state": "modified"})

    effective_diff = [r["id"] for r in results
                      if r["status"] not in ("applied", "unchanged")]
    if not effective_diff:
        store.mark_plan_applied(plan_id)

    outcome = "applied" if applied and not effective_diff else \
        "idempotent" if unchanged and not effective_diff else \
        "conflict" if conflicts else "partial"
    store.record_event("setup_applied", {
        "plan_digest": plan_id,
        "outcome": outcome,
        "applied": applied,
        "unchanged": unchanged,
        "conflicts": [c["id"] for c in conflicts],
        "preserved_violations": violations,
    })
    return {
        "outcome": outcome,
        "entries": results,
        "applied": applied,
        "unchanged": unchanged,
        "conflicts": conflicts,
        "preserved": {"checked": checked, "violations": violations},
        "effective_diff": effective_diff,
        "plan_digest": plan_id,
    }
