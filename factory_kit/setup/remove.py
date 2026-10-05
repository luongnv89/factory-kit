#!/usr/bin/env python3
"""Reviewed ownership-aware removal — F08 (issue #19 / Task 3.5).

The mirror of :mod:`factory_kit.setup.plan` / :mod:`factory_kit.setup.apply`
for teardown. Nothing is deleted on inspection:

- :func:`inspect_removal` builds the operator-reviewable uninstall plan
  from *recorded* ownership only — the :class:`SetupStore` ledger's owned
  paths/checksums and remote-effect intents, the registration row, and
  the intake store's work/preview inventory. Shared Hermes
  infrastructure, IDD configuration (``.gitissue.yml``), shared skills
  and tokens used elsewhere are never entries: shared ownership is not
  implied by a registration (A4/A5).
- :func:`accept_removal` binds the reviewed ``removal_digest`` *and* the
  explicit task-history choice — ``retain``/``export``/``delete`` — that
  A4 requires; there is no default, so a plan without a choice cannot be
  applied.
- :func:`apply_removal` executes in a fixed order: (1) stop intake —
  :meth:`RegistrationStore.begin_removal` flips readiness durably so
  every later delivery denies; (2) commit the active-generation fence
  for each live work row and confirm worker/descendant exit —
  unconfirmed termination quarantines and blocks all further removal
  (A3); (3) remove only owned files whose bytes still match their
  recorded checksum — ``user-modified`` files are preserved (A2); (4)
  record removal intents for remote effects and remove owned preview
  deployments — unreachable providers leave a visible ``cleanup-pending``
  backlog, never a false removal claim (A5); (5) honor the history
  choice scoped strictly to this identity's authority key; (6)
  tombstone the registration — an identity gravestone, never a silent
  delete; (7) verify: removed paths absent, unrelated bytes identical,
  provider read-back clean, tombstone present.

Reinstall stays idempotent: effect rows are *marked* removal-recorded
rather than dropped, so a repeat setup reports ``existing`` rather than
duplicating integrations (A2).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

from factory_kit.config.registration import RegistrationError
from factory_kit.durable.store import IntakeStoreError
from factory_kit.execution.limits import TERMINAL_WORK_STATES
from factory_kit.preview.port import PreviewError

from . import plan as plan_mod
from .apply import RecordingEffectSink, _safe_target
from .ownership import SetupStore, sha256_file

__all__ = [
    "HISTORY_CHOICES",
    "REMOVAL_VERSION",
    "RemovalError",
    "accept_removal",
    "apply_removal",
    "inspect_removal",
    "removal_digest",
]

REMOVAL_VERSION = 1

#: The explicit task-history choices A4 requires — no default exists.
HISTORY_CHOICES = ("retain", "export", "delete")

#: Consequences the plan prints verbatim for review (A4): what removing
#: a registration means, what a tombstone is, and what stays behind.
_HISTORY_CONSEQUENCES = (
    ("identity tombstone: the registration row is replaced by a "
     "gravestone (repo_id + authority_key + removed_at/by) so removal "
     "is provable and stale state can never silently resurrect "
     "authority; work-bound accessors refuse the tombstoned identity"),
    ("registration-removal consequence: intake denies deliveries "
     "(registration-not-ready), generations and bound work end, and "
     "re-registration is a fresh reviewed setup — never a resume"),
    ("retain: durable task history (work/deliveries/events/attempts/"
     "previews/approvals) stays in the intake store — inspectable but "
     "inert once the registration is tombstoned"),
    ("export: the repo-scoped history is written as JSON to the export "
     "path before tombstoning; the intake rows are kept"),
    ("delete: the repo-scoped history is deleted inside one durable "
     "transaction after tombstoning order is decided — other "
     "identities' rows and shared/global state are untouched"),
)

#: Resources a single registration never owns — listed on every plan so
#: review can verify they are *not* in scope (A4/A5).
_SHARED_NEVER_REMOVED = (
    ("Hermes kanban/task infrastructure (kanban.db) — shared runtime "
     "state, not registration-owned"),
    (".gitissue.yml — the repository's own IDD configuration; the "
     "factory never writes it and never removes it"),
    ("shared skills and provider/token credentials used elsewhere — "
     "referenced, never owned, by this registration"),
    ("human PRs, branches and provider resources — the provider port "
     "refuses removal of anything it did not record as owned"),
)


class RemovalError(Exception):
    """The removal precondition failed — unaccepted plan, digest
    mismatch, a missing explicit history choice, or a scope the apply
    path cannot honor. Never a partial removal."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical(node) -> str:
    return json.dumps(node, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def removal_digest(plan) -> str:
    """Digest over the reviewable surface — everything apply consumes.

    ``files``/``remote_effects``/``previews`` are the reviewed removal
    set; ``registration``/``authority`` carry the fence + tombstone the
    operator accepted; ``history`` carries the *required* choice
    vocabulary (never the choice itself — that binds at accept);
    ``preserved`` is the checksum set apply re-verifies. Mutating any of
    them re-keys the plan, so the acceptance bound to this digest cannot
    silently cover a wider scope or a weaker preservation proof.
    """
    surface = {k: plan[k] for k in
               ("files", "remote_effects", "previews", "registration",
                "authority", "history", "shared_infrastructure",
                "conflicts", "findings", "preserved")
               if k in plan}
    return hashlib.sha256(_canonical(surface).encode()).hexdigest()


def _authority_key(repo_id):
    return f"gh:{repo_id}" if repo_id else None


def _file_entries(store, repo_root):
    """One plan entry per recorded owned file, re-verified on disk."""
    entries = []
    for path, rec in sorted(store.owned_files().items()):
        state = store.verify_file(repo_root, path)
        action = {"unchanged": "remove",
                  "user-modified": "preserve",
                  "missing": "absent"}.get(state, "preserve")
        entries.append({
            "id": f"file:{path}",
            "kind": "file",
            "path": path,
            "owned_kind": rec.get("kind"),
            "sha256": rec.get("sha256"),
            "state": state,
            "action": action,
            "detail": {
                "remove": "recorded factory-owned bytes unchanged — "
                          "eligible for removal",
                "preserve": "user edits detected — preserved unless the "
                            "operator sets a specific reviewed "
                            "resolution (resolution: \"discard\")",
                "absent": "already gone — ownership row is dropped",
            }[action],
        })
    return entries


def _preview_entries(intake_store, work_keys):
    """Owned provider deployments recorded against this identity."""
    if intake_store is None or not work_keys:
        return []
    entries = []
    for rec in intake_store.preview_rows():
        if rec["work_key"] not in work_keys:
            continue
        if rec["state"] == "removed":
            continue
        entries.append({
            "id": f"preview:{rec['preview_id']}",
            "kind": "preview",
            "preview_id": rec["preview_id"],
            "deployment_id": rec.get("deployment_id"),
            "work_key": rec["work_key"],
            "provider": rec.get("provider"),
            "state": rec["state"],
            "action": "remove-owned",
            "detail": "factory-owned deployment — provider-confirmed "
                      "removal; an unreachable provider parks it in the "
                      "visible cleanup-pending backlog (A5)",
        })
    return entries


def inspect_removal(repo_root, store, *, registration_store=None,
                    intake_store=None, now=None):
    """Build the operator-reviewable uninstall plan (read-only).

    Reads the recorded ownership ledger, the registration row and the
    intake store's work/preview inventory for this identity; mutates
    nothing. ``store`` accepts a :class:`SetupStore` or a state path.
    """
    repo_root = os.path.abspath(str(repo_root))
    if not os.path.isdir(repo_root):
        raise RemovalError(
            f"repository root {repo_root!r} is not a directory")
    store = store if isinstance(store, SetupStore) else SetupStore(store)
    project_id = store.project_id

    plan = {
        "removal_version": REMOVAL_VERSION,
        "generated_at": now or _utcnow(),
        "repo_root": repo_root,
        "project_id": project_id,
    }
    notes, findings, conflicts = [], [], []

    # -- registration + authority scope -----------------------------------
    repo_id = project_id
    registration_entry = None
    authority_key = _authority_key(repo_id)
    if registration_store is not None and repo_id is not None:
        reg = registration_store.get(repo_id)
        if reg is None:
            findings.append({
                "source": "registration",
                "detail": f"no registration row for {repo_id!r} — "
                          "nothing to tombstone",
            })
        elif reg.get("tombstone"):
            registration_entry = {
                "id": f"registration:{repo_id}",
                "kind": "registration",
                "repo_id": repo_id,
                "authority_key": reg.get("authority_key"),
                "state": "tombstone",
                "action": "already-removed",
                "detail": f"identity tombstoned at "
                          f"{reg.get('removed_at')} by "
                          f"{reg.get('removed_by')}",
            }
            authority_key = reg.get("authority_key") or authority_key
        else:
            authority_key = reg.get("authority_key") or authority_key
            registration_entry = {
                "id": f"registration:{repo_id}",
                "kind": "registration",
                "repo_id": repo_id,
                "authority_key": authority_key,
                "state": "present",
                "action": "tombstone",
                "generations": len(reg.get("generations") or []),
                "bound_work": sorted(reg.get("work") or {}),
                "detail": "row replaced by an identity tombstone — "
                          "provable removal, never a silent delete (A4)",
            }
    elif repo_id is None:
        findings.append({
            "source": "ownership-ledger",
            "detail": "the ledger carries no project identity — removal "
                      "is scoped to recorded owned paths only",
        })

    # -- intake-side inventory ---------------------------------------------
    work_rows, live_attempts = [], []
    if intake_store is not None and authority_key is not None:
        for row in intake_store.work_rows_for_authority(authority_key):
            work_rows.append({
                "work_key": row["work_key"],
                "issue": row["issue"],
                "generation": row["generation"],
                "state": row["state"],
                "task_id": row.get("task_id"),
            })
            open_attempts = [
                r["attempt_id"] for r in
                intake_store.attempt_record_rows(row["work_key"])
                if not r.get("ended")]
            open_attempts += [
                r["attempt_id"] for r in intake_store.attempt_rows()
                if r["work_key"] == row["work_key"]
                and r["state"] == "active"]
            live_attempts.extend(sorted(set(open_attempts)))
        if live_attempts:
            findings.append({
                "source": "execution-authority",
                "detail": f"{len(live_attempts)} attempt(s) hold "
                          "un-ended records — removal fences the "
                          "generation first and confirms exit before "
                          "any state removal (A3)",
            })
    elif intake_store is None:
        findings.append({
            "source": "intake-store",
            "detail": "no intake store supplied — work/preview/history "
                      "inventory is unavailable; export/delete history "
                      "choices will refuse at apply",
        })

    # -- entries -------------------------------------------------------------
    files = _file_entries(store, repo_root)
    remote_effects = [{
        "id": f"effect:{effect_id}",
        "kind": "remote-effect",
        "effect_id": effect_id,
        "action": "remove",
        "effect": record.get("entry"),
        "detail": "removal intent recorded for the owned remote effect "
                  "(webhook/dependency) — live provider mutation stays "
                  "a carried blocker, so the record is honest pending "
                  "state, never a claimed deletion",
    } for effect_id, record in sorted(store.effects().items())]
    previews = _preview_entries(intake_store,
                                {w["work_key"] for w in work_rows})

    owned_paths = set(store.owned_files())
    preserved, truncated = plan_mod._walk(repo_root, owned_paths)

    history = {
        "required": True,
        "choices": list(HISTORY_CHOICES),
        "choice": None,
        "work_rows": len(work_rows),
        # Repo-scoped count (not the store-wide total) — review sees
        # exactly what retain/export/delete will govern (A4).
        "event_rows": len(intake_store.export_history(authority_key)
                          ["tables"]["events"]) if
        (intake_store is not None and authority_key) else None,
        "consequences": list(_HISTORY_CONSEQUENCES),
        "export_note": "export writes one JSON document of the "
                       "repo-scoped durable rows before tombstoning",
    }
    if repo_id is None:
        history["delete_note"] = \
            "unavailable — no repo identity to scope deletion to"

    nothing = (not files and not remote_effects and not previews and
               registration_entry is None and not work_rows)
    if nothing:
        notes.append("no recorded factory-owned state — the plan is a "
                     "reviewable nothing-to-remove record")

    plan["files"] = files
    plan["remote_effects"] = remote_effects
    plan["previews"] = previews
    plan["registration"] = registration_entry
    plan["authority"] = {
        "authority_key": authority_key,
        "work": work_rows,
        "live_attempts": live_attempts,
        "note": "the generation fence commits before termination is "
                "attempted, and before any credential/state removal; "
                "active worktrees are never forcibly deleted",
    }
    plan["history"] = history
    plan["shared_infrastructure"] = {
        "note": "shared Hermes/IDD/skill/token resources are not owned "
                "by a registration and are never in removal scope",
        "never_removed": list(_SHARED_NEVER_REMOVED),
    }
    plan["conflicts"] = conflicts
    plan["findings"] = findings
    plan["notes"] = notes
    plan["preserved"] = preserved
    plan["preserved_truncated"] = truncated
    plan["appliable"] = not conflicts
    plan["removal_digest"] = removal_digest(plan)
    plan["acceptance"] = None
    return plan


def accept_removal(plan, accepted_by, *, history, at=None):
    """Accept a reviewed removal plan; returns the accepted copy.

    ``history`` is the operator's explicit task-history choice
    (A4) — one of ``retain``/``export``/``delete``. There is no default:
    applying without the recorded choice is structurally impossible
    because the choice binds into the acceptance record alongside the
    ``removal_digest``.
    """
    if not plan.get("appliable"):
        raise RemovalError(
            "removal plan is not appliable — resolve its conflicts and "
            "re-inspect")
    if history not in HISTORY_CHOICES:
        raise RemovalError(
            f"an explicit history choice is required — one of "
            f"{', '.join(HISTORY_CHOICES)} (A4); the plan's history "
            "consequences list what each means")
    accepted = dict(plan)
    accepted["acceptance"] = {
        "accepted_by": accepted_by,
        "accepted_at": at or _utcnow(),
        "removal_digest": plan["removal_digest"],
        "history": history,
    }
    return accepted


# --------------------------------------------------------------------- #
# apply — the ordered, fenced, verifiable removal
# --------------------------------------------------------------------- #

def _validate(plan):
    acceptance = plan.get("acceptance") or {}
    if not acceptance:
        raise RemovalError(
            "removal plan is not accepted — apply requires explicit "
            "operator acceptance of the reviewed removal_digest (A1)")
    if plan.get("conflicts") or not plan.get("appliable"):
        # ``appliable`` is not digest-covered; re-derive the gate rather
        # than trusting a flag on a forged acceptance.
        raise RemovalError(
            "removal plan is not appliable — resolve its conflicts and "
            "re-inspect (A1)")
    if removal_digest(plan) != plan.get("removal_digest"):
        raise RemovalError(
            "removal digest does not match its contents — the plan was "
            "modified after generation; re-inspect")
    if acceptance.get("removal_digest") != plan.get("removal_digest"):
        raise RemovalError(
            "acceptance digest does not match this plan — the scope "
            "under review changed; re-accept the current plan")
    history = acceptance.get("history")
    if history not in HISTORY_CHOICES:
        raise RemovalError(
            f"acceptance carries no explicit history choice — expected "
            f"one of {', '.join(HISTORY_CHOICES)} (A4)")
    return acceptance


def _settle_work(intake_store, work_key, generation, *,
                 reason="uninstall"):
    """Fence-then-confirm when no execution lane is wired.

    The durable fence commits first — a racing effect or result loses to
    it (A3). Liveness truth is the durable attempt rows: an attempt with
    no ``ended`` marker may still run on the runtime side, so without a
    live terminator it is *uncertain* — quarantined, never silently
    confirmed.
    """
    fence = intake_store.fence_state(work_key)
    if not (fence and fence["fenced"]):
        intake_store.fence_work(work_key, reason, generation)
    open_attempts = [
        r["attempt_id"] for r in
        intake_store.attempt_record_rows(work_key) if not r.get("ended")]
    open_attempts += [
        r["attempt_id"] for r in intake_store.attempt_rows()
        if r["work_key"] == work_key and r["state"] == "active"]
    if open_attempts:
        intake_store.resolve_fence(
            work_key, "quarantined",
            "worker/descendant exit unconfirmed and no terminator is "
            "wired — process state uncertain")
        intake_store.set_work_state(work_key, "quarantined",
                                    reason="termination-uncertain")
        intake_store.emit_alert(
            "removal-quarantine", work_key, "high",
            detail=f"attempt(s) {sorted(set(open_attempts))} did not "
                   "confirm exit — removal blocked (A3)")
        return "quarantined"
    intake_store.resolve_fence(work_key, "confirmed",
                               "no live worker state recorded")
    work = intake_store.get_work(work_key)
    if work is not None and work["state"] not in TERMINAL_WORK_STATES:
        intake_store.set_work_state(work_key, "canceled",
                                    reason=reason)
    intake_store.release_lane("main", expected_work=work_key)
    return "confirmed"


def _terminate_authority(plan, intake_store, terminator, *,
                         deadline_s, reason="uninstall"):
    """Phase 2: fence every live generation, confirm worker exit.

    Returns (settled, quarantined). With a wired ``terminator`` (the
    execution lane) each work row goes through
    ``terminate_work`` — the fence commits inside the same ordering the
    control path uses. Without one, the local settle path applies and an
    un-ended durable attempt is *uncertain*, never claimed dead.
    """
    settled, quarantined = [], []
    for wrow in plan["authority"]["work"]:
        work_key = wrow["work_key"]
        work = intake_store.get_work(work_key)
        if work is None:
            settled.append({"work_key": work_key,
                            "termination": "confirmed",
                            "detail": "work row already absent"})
            continue
        # The active-generation fence commits HERE, before any
        # termination is attempted — a racing effect/result loses to the
        # durable fence regardless of what the terminator does (A3).
        fence = intake_store.fence_state(work_key)
        if not (fence and fence["fenced"]):
            intake_store.fence_work(work_key, reason,
                                    work["generation"])
        if terminator is not None:
            try:
                if hasattr(terminator, "terminate_work"):
                    out = terminator.terminate_work(
                        work_key, reason=reason, deadline_s=deadline_s)
                else:
                    out = terminator(work_key)
                outcome = out.get("termination") or out.get("outcome")
            except Exception as exc:  # noqa: BLE001 — terminator-defined
                # A terminator that cannot answer is precisely an
                # unconfirmed exit — quarantine, never claim (A3).
                outcome = "quarantined"
                intake_store.resolve_fence(
                    work_key, "quarantined",
                    f"terminator raised {type(exc).__name__} — exit "
                    "unconfirmed")
        elif work["state"] in TERMINAL_WORK_STATES:
            # A terminal row can hold no active authority; the fence
            # already committed above so the generation is provably
            # closed.
            intake_store.resolve_fence(
                work_key, "confirmed",
                "terminal work state — no live authority")
            outcome = "confirmed"
        else:
            outcome = _settle_work(intake_store, work_key,
                                   work["generation"], reason=reason)
        record = {"work_key": work_key, "termination": outcome}
        (quarantined if outcome != "confirmed" else settled) \
            .append(record)
    return settled, quarantined


def _remove_files(plan, repo_root, store):
    """Phase 3: unlink only owned files still matching their checksum.

    State is re-derived at apply time — a file the operator edited after
    review is ``user-modified`` and preserved, even if the plan called
    it ``remove`` (A2). A plan entry may carry an explicit reviewed
    ``resolution: "discard"`` — the operator's specific instruction to
    delete despite edits, bound by the accepted digest.
    """
    removed, preserved, absent = [], [], []
    for entry in plan["files"]:
        path = entry["path"]
        state = store.verify_file(repo_root, path)
        if state == "missing":
            store.forget_file(path)
            absent.append(entry["id"])
            continue
        resolution = entry.get("resolution")
        if state == "user-modified" and resolution != "discard":
            preserved.append({"id": entry["id"], "path": path,
                              "state": state,
                              "detail": "user edits preserved — remove "
                                        "by hand or re-run with a "
                                        "reviewed discard resolution"})
            continue
        if state not in ("unchanged", "user-modified"):
            preserved.append({"id": entry["id"], "path": path,
                              "state": state,
                              "detail": "unresolved ownership state — "
                                        "preserved"})
            continue
        target = _safe_target(repo_root, path, entry["id"])
        os.unlink(target)
        store.forget_file(path)
        removed.append(entry["id"])
    return {"removed": removed, "preserved": preserved,
            "absent": absent}


def _remove_effects(plan, effect_sink):
    """Phase 4a: record removal intents for owned remote effects."""
    results, pending = [], []
    for entry in plan["remote_effects"]:
        effect_id = entry["effect_id"]
        try:
            out = effect_sink.remove(effect_id, entry)
        except Exception as exc:  # noqa: BLE001 — adapter-defined errors
            pending.append({"id": entry["id"],
                            "reason": type(exc).__name__})
            continue
        results.append({"id": entry["id"], "status": out["status"]})
    return {"effects": results, "pending": pending}


def _remove_previews(plan, intake_store, preview_port):
    """Phase 4b: remove owned preview deployments via the provider port.

    Only recorded ``deployment_id`` values (or identity-keyed
    rediscovery) are removed — the port refuses unowned ids, so
    human-created resources are preserved (A5). A provider error lands
    the record in ``cleanup-pending``: the visible backlog, never a
    removal claim.
    """
    results, pending = [], []
    for entry in plan["previews"]:
        preview_id = entry["preview_id"]
        identity = {"preview_id": preview_id,
                    "work_key": entry["work_key"],
                    "generation":
                        (intake_store.get_work(entry["work_key"]) or {})
                        .get("generation")}
        if preview_port is None:
            pending.append({"id": entry["id"],
                            "reason": "provider-port-unavailable"})
            continue
        deployment_ids = [entry["deployment_id"]] \
            if entry.get("deployment_id") else []
        if not deployment_ids:
            try:
                found = preview_port.find_deployments(identity)
            except PreviewError:
                found = None
            if found is None:
                pending.append({"id": entry["id"],
                                "reason": "provider-unreachable"})
                continue
            deployment_ids = [d["deployment_id"] for d in found
                              if d.get("deployment_id")]
        if not deployment_ids:
            results.append({"id": entry["id"], "status": "no-resource"})
            continue
        failed = False
        for dep_id in deployment_ids:
            try:
                preview_port.remove(dep_id, identity)
            except PreviewError:
                failed = True
        if failed:
            pending.append({"id": entry["id"],
                            "reason": "provider-removal-unconfirmed"})
            if intake_store is not None:
                with intake_store.transact() as tx:
                    tx.update_preview(preview_id,
                                      state="cleanup-pending")
                intake_store.emit_alert(
                    "preview-cleanup-backlog", entry["work_key"],
                    "medium",
                    f"preview {preview_id} removal unconfirmed during "
                    "uninstall — provider unreachable")
        else:
            if intake_store is not None:
                with intake_store.transact() as tx:
                    tx.update_preview(preview_id, state="removed",
                                      removed_at=_utcnow())
            results.append({"id": entry["id"], "status": "removed",
                            "deployments": deployment_ids})
    return {"previews": results, "pending": pending}


def _export_history(intake_store, authority_key, export_path):
    """Write the repo-scoped durable history as one JSON document."""
    payload = intake_store.export_history(authority_key)
    directory = os.path.dirname(os.path.abspath(export_path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".history-",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, export_path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    # Verify the outcome (A4): the file must parse and carry the scoped
    # work keys the choice promised to export.
    with open(export_path, encoding="utf-8") as handle:
        written = json.load(handle)
    return {"path": str(export_path),
            "work_keys": written.get("work_keys") or [],
            "verified": written.get("authority_key") == authority_key}


def _verify(plan, repo_root, store, registration_store, files_result):
    """Phase 7: prove the removal rather than claim it (A1/A5).

    ``still_present`` catches a path the apply phase reported removed
    that still exists on disk; ``preserved_violations`` re-checksums the
    unrelated content the reviewed plan promised untouched.
    """
    by_id = {e["id"]: e for e in plan["files"]}
    still_present = []
    for entry_id in files_result["removed"]:
        target = os.path.join(repo_root, by_id[entry_id]["path"])
        if os.path.lexists(target):
            still_present.append(by_id[entry_id]["path"])
    violations = []
    for rec in plan["preserved"]:
        if rec.get("sha256") is None:
            continue
        target = os.path.join(repo_root, rec["path"])
        if not os.path.isfile(target):
            violations.append({"path": rec["path"],
                               "state": "missing"})
        elif sha256_file(target) != rec["sha256"]:
            violations.append({"path": rec["path"],
                               "state": "modified"})
    tombstone = None
    reg_entry = plan.get("registration") or {}
    if registration_store is not None and \
            reg_entry.get("repo_id"):
        rec = registration_store.get(reg_entry["repo_id"])
        tombstone = bool(rec and rec.get("tombstone"))
    return {"still_present": still_present,
            "preserved_violations": violations,
            "tombstone": tombstone}


def apply_removal(plan, repo_root, store, *, registration_store=None,
                  intake_store=None, terminator=None, effect_sink=None,
                  preview_port=None, export_path=None,
                  deadline_s=30.0) -> dict:
    """Apply an accepted removal plan; returns the per-phase report.

    ``terminator`` is the execution lane (``terminate_work``) or a
    ``callable(work_key) -> {"termination": ...}`` seam; without one a
    live durable attempt quarantines rather than being claimed dead
    (A3). ``effect_sink`` defaults to :class:`RecordingEffectSink`;
    ``preview_port`` is the provider seam — absent or unreachable leaves
    ``cleanup-pending`` backlog, never a false removal (A5).
    """
    acceptance = _validate(plan)
    store = store if isinstance(store, SetupStore) else SetupStore(store)
    repo_root = os.path.abspath(str(repo_root))
    accepted_by = acceptance.get("accepted_by")
    history = acceptance["history"]

    reg_entry = plan.get("registration") or {}
    repo_id = reg_entry.get("repo_id") or plan.get("project_id")
    authority_key = (plan.get("authority") or {}).get("authority_key") \
        or _authority_key(repo_id)

    if history in ("export", "delete") and intake_store is None:
        raise RemovalError(
            f"history choice {history!r} cannot be honored without the "
            "intake store — nothing is claimed deleted that was not "
            "verified (A4)")
    if plan["authority"]["work"] and intake_store is None:
        # The reviewed plan binds live authority; without the durable
        # store the generation fence cannot commit — refuse rather
        # than remove state under un-fenced work (A3).
        raise RemovalError(
            "the plan binds live work rows but no intake store was "
            "supplied — the generation fence cannot commit; refusing "
            "removal (A3)")
    if history == "export" and export_path is None:
        root = os.path.dirname(os.path.abspath(store.path))
        export_path = os.path.join(
            root, f"task-history-{repo_id or 'unknown'}.json")

    result = {"removal_digest": plan["removal_digest"],
              "repo_id": repo_id, "phases": {}}

    # -- Phase 1: stop intake (durable, before any state removal) ---------
    intake_stop = {"outcome": "no-registration"}
    if registration_store is not None and repo_id is not None:
        try:
            intake_stop = registration_store.begin_removal(
                repo_id, removed_by=accepted_by)
        # Absent or already-tombstoned — intake was already stopped.
        except RegistrationError as exc:
            intake_stop = {"outcome": "skipped",
                           "reason": str(exc)}
    result["phases"]["intake_stopped"] = intake_stop

    # -- Phase 2: commit generation fences, confirm worker exit -----------
    settled, quarantined = [], []
    if intake_store is not None and plan["authority"]["work"]:
        settled, quarantined = _terminate_authority(
            plan, intake_store, terminator, deadline_s=deadline_s)
    result["phases"]["authority"] = {
        "fenced": [w["work_key"] for w in plan["authority"]["work"]],
        "settled": settled, "quarantined": quarantined,
    }
    if quarantined:
        # A3 — unconfirmed termination blocks every destructive phase;
        # intake stays stopped, nothing else is removed, and the
        # quarantine carries its recovery instructions.
        outcome = "quarantined"
        result["outcome"] = outcome
        result["quarantine"] = {
            "work_keys": [q["work_key"] for q in quarantined],
            "recovery": "resolve the quarantined fence(s) "
                        "(operator confirms worker/descendant exit, "
                        "then resolve_fence 'resolved') and re-run "
                        "removal — no state was deleted",
        }
        store.record_event("setup_removed", {
            "repo_id": repo_id, "outcome": outcome,
            "removal_digest": plan["removal_digest"],
            "history": history, "removed_by": accepted_by,
            "pending": [q["work_key"] for q in quarantined],
        })
        return result

    # -- Phase 3: owned files — unchanged only, edits preserved -----------
    files = _remove_files(plan, repo_root, store)
    result["phases"]["files"] = files

    # -- Phase 4: owned remote effects + preview deployments --------------
    sink = effect_sink or RecordingEffectSink(store)
    result["phases"]["remote_effects"] = _remove_effects(
        plan, sink)
    result["phases"]["previews"] = _remove_previews(
        plan, intake_store, preview_port)

    # -- Phase 5: explicit history choice ----------------------------------
    history_result = {"choice": history}
    if intake_store is not None and authority_key is not None:
        if history == "export":
            history_result["export"] = _export_history(
                intake_store, authority_key, export_path)
            history_result["outcome"] = "exported"
        elif history == "delete":
            history_result["deleted"] = intake_store \
                .delete_repo_history(authority_key)["deleted"]
            history_result["outcome"] = "deleted"
        else:
            history_result["outcome"] = "retained"
    else:
        history_result["outcome"] = "no-history"
    result["phases"]["history"] = history_result

    # -- Phase 6: registration tombstone -------------------------------------
    tombstone = {"outcome": "skipped"}
    if registration_store is not None and repo_id is not None and \
            reg_entry.get("state") == "present":
        try:
            tombstone = registration_store.remove(
                repo_id, removed_by=accepted_by)
        except RegistrationError as exc:
            # A racing tombstone IS the desired end-state — report it
            # honestly rather than as a failure.
            if registration_store.is_tombstone(repo_id):
                tombstone = {"outcome": "already-tombstoned"}
            else:
                tombstone = {"outcome": "failed",
                             "reason": str(exc)}
    result["phases"]["registration"] = tombstone

    # -- Phase 7: verification — prove, never claim ---------------------------
    verification = _verify(plan, repo_root, store, registration_store,
                           files)
    result["verification"] = verification

    pending = (result["phases"]["remote_effects"]["pending"] +
               result["phases"]["previews"]["pending"])
    if verification["still_present"]:
        pending += [{"kind": "file",
                     "path": p} for p in verification["still_present"]]
    result["pending"] = pending

    if pending or verification["preserved_violations"]:
        outcome = "removed-with-pending"
    elif not plan["files"] and not plan["remote_effects"] and \
            not plan["previews"] and not plan["authority"]["work"] and \
            not reg_entry:
        outcome = "nothing-to-remove"
    else:
        outcome = "removed"
    result["outcome"] = outcome
    result["preserved_edits"] = files["preserved"]

    store.record_event("setup_removed", {
        "repo_id": repo_id, "outcome": outcome,
        "removal_digest": plan["removal_digest"],
        "history": history, "removed_by": accepted_by,
        "pending": [p.get("id") or p.get("path") for p in pending],
        "preserved_edits": [p["id"] for p in files["preserved"]],
    })
    if intake_store is not None and authority_key is not None and \
            history != "delete":
        # The intake-side twin — skipped for delete: the marker would
        # be an unscoped row on a store that no longer holds the
        # identity, so the SetupStore copy is the durable removal
        # record either way.
        try:
            with intake_store.transact() as tx:
                tx.record_typed_event(
                    "setup_removed",
                    properties={"repo_id": repo_id, "outcome": outcome,
                                "removal_digest":
                                    plan["removal_digest"],
                                "history": history,
                                "removed_by": accepted_by,
                                "pending": result["pending"] or None})
        except IntakeStoreError as exc:
            # The SetupStore copy above already committed — the
            # intake-side twin is best-effort, never silently fatal.
            result["phases"]["intake_event"] = \
                f"unrecorded: {type(exc).__name__}"
    return result
