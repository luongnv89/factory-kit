#!/usr/bin/env python3
"""Repair of interrupted migrations — F10 A3.

PRD §3.2 F10 / Task 4.2 (issue #26): *"an interruption at every
migration boundary leaves a state that repair can restore to a
validated checkpoint or park with a specific conflict; no dispatch
ever runs from a partially migrated configuration."*

:mod:`factory_kit.setup.upgrade` commits every migration boundary as one
durable write in the ownership ledger's ``migrations`` area, so a crash
can only land *between* stages — never inside one. This module is the
restart-side half of that contract:

- ``planned``/``fenced`` (no checkpoint committed): file writes can
  only run after the checkpoint boundary commits, so the tree provably
  still carries the pre-migration bytes — repair just closes the intake
  fence (readiness ``restored``/denied until re-validated) and seals
  the migration ``rolled-back``.
- ``checkpointed`` and later: the durable checkpoint holds every owned
  file's pre-migration bytes plus the registration's generation
  digests and effective map. Repair dry-runs the restore first — any
  owned file carrying bytes that are *neither* the checkpoint's nor the
  migration's recorded target is user work: preserved byte-for-byte
  and surfaced as the specific conflict the migration parks on
  (A3/A4). With no conflicts the checkpoint is written back, a fresh
  authorized generation re-binds the *previous* digests, and the
  restored state is re-verified rather than claimed.
- ``failed``: a post-migration validation failure halts in-flight —
  the same checkpoint restore applies.

Either way the migration ends at a terminal stage
(``rolled-back``/``parked``) and the registration's readiness stays
``denied`` — a partially migrated configuration never dispatches.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from factory_kit.config.registration import RegistrationError

from .ownership import SetupStore
from .upgrade import (
    _restore_checkpoint,
    _restore_conflicts,
    _verify_restore,
)

__all__ = ["RepairError", "open_migrations", "repair_migration"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RepairError(Exception):
    """Repair could not run — no such/open migration, or a ledger that
    records nothing repairable."""


def open_migrations(store):
    """The migrations an interruption left in-flight — what repair owns."""
    store = store if isinstance(store, SetupStore) else SetupStore(store)
    return store.open_migrations()


def _seal(repo_root, store, registration_store, migration, actor,
          *, outcome, detail, conflicts=None, restored=None,
          verification=None):
    """Common terminal close: seal the migration, close the fence,
    append the audited repair event."""
    migration_id = migration["migration_id"]
    repo_id = migration.get("repo_id")
    if conflicts:
        store.update_migration(migration_id,
                               conflict={"source": "repair",
                                         "detail": conflicts})
    terminal = {"repaired": "rolled-back", "parked": "parked"}[outcome]
    store.advance_migration(migration_id, terminal, detail=detail)
    if registration_store is not None and repo_id:
        try:
            registration_store.finish_upgrade(
                repo_id,
                outcome="rolled-back" if outcome == "repaired"
                else "parked",
                supported_versions=(
                    (migration.get("checkpoint") or {})
                    .get("registration", {})
                    .get("supported_versions")))
        except RegistrationError:
            # A registration row that vanished mid-migration is the
            # conflict the caller already surfaced — sealing the
            # migration is still the right close.
            pass
    store.record_event("setup_repaired", {
        "migration_id": migration_id,
        "repo_id": repo_id,
        "outcome": outcome,
        "detail": detail,
        "conflicts": [c.get("path") for c in (conflicts or [])
                      if c.get("path")],
        "repaired_by": actor,
        "at": _utcnow(),
    })
    return {"outcome": outcome, "migration_id": migration_id,
            "repo_id": repo_id, "stage_was": migration.get("stage"),
            "conflicts": conflicts or [], "restored": restored,
            "verification": verification,
            "note": "dispatch stays denied until substantive readiness "
                    "passes — a partially migrated configuration never "
                    "serves"}


def repair_migration(repo_root, store, registration_store=None, *,
                     migration_id=None, repaired_by) -> dict:
    """Restore one interrupted migration to its validated checkpoint —
    or park it with a specific conflict (A3).

    With ``migration_id=None`` repairs *every* open migration. Each
    result is ``repaired`` (checkpoint restored + re-verified, fence
    closed into denied readiness) or ``parked`` (a specific recorded
    conflict — user-modified bytes, missing registration, unrestorable
    checkpoint — preserved byte-for-byte).
    """
    store = store if isinstance(store, SetupStore) else SetupStore(store)
    repo_root = os.path.abspath(str(repo_root))

    if migration_id is not None:
        migration = store.migration(migration_id)
        if migration is None:
            raise RepairError(f"no migration {migration_id!r} in the "
                              "setup ledger")
        if migration["stage"] in ("complete", "rolled-back"):
            return {"outcome": "already-sealed",
                    "migration_id": migration_id,
                    "stage": migration["stage"]}
        targets = [migration]
    else:
        targets = list(store.open_migrations().values())
        targets.sort(key=lambda m: m.get("updated_at") or "")
        if not targets:
            raise RepairError(
                "no open migrations — nothing interrupted needs repair")

    results = [repair_one(repo_root, store, registration_store,
                          m, repaired_by=repaired_by)
               for m in targets]
    if len(results) == 1:
        return results[0]
    return {"outcome": "repaired" if all(
        r["outcome"] == "repaired" for r in results) else "mixed",
            "repairs": results}


def repair_one(repo_root, store, registration_store, migration, *,
               repaired_by):
    """Repair a single open migration record (all-or-park)."""
    # No checkpoint committed → the files boundary never ran, so the
    # tree provably still carries pre-migration bytes (writes only
    # happen after the checkpoint commits). Close the fence and seal.
    if not migration.get("checkpoint"):
        return _seal(
            repo_root, store, registration_store, migration,
            repaired_by, outcome="repaired",
            detail="interrupted before the checkpoint boundary — no "
                   "file mutation was committed; the intake fence is "
                   "closed and readiness stays denied until "
                   "re-validated")

    conflicts, _needs_reg = _restore_conflicts(
        repo_root, migration, registration_store)
    if conflicts:
        return _seal(
            repo_root, store, registration_store, migration,
            repaired_by, outcome="parked",
            detail="checkpoint restore blocked — conflicts are "
                   "preserved byte-for-byte and recorded on the "
                   "migration; resolve them and re-run repair or "
                   "rollback",
            conflicts=conflicts)

    restored = _restore_checkpoint(repo_root, store, migration,
                                   registration_store, repaired_by)
    verification = _verify_restore(repo_root, migration,
                                   registration_store)
    violations = (verification or {}).get("file_violations") or []
    if violations or verification.get("registration_restored") is False:
        return _seal(
            repo_root, store, registration_store, migration,
            repaired_by, outcome="parked",
            detail="restored bytes failed read-back verification — "
                   "the migration parks rather than claiming a state "
                   "it cannot prove",
            conflicts=[{"source": "verification",
                        "detail": f"{violations}; registration_restored"
                                  f"={verification.get('registration_restored')}"}],
            restored=restored, verification=verification)
    return _seal(
        repo_root, store, registration_store, migration, repaired_by,
        outcome="repaired",
        detail="checkpoint restored byte-for-byte, registration "
               "re-bound to the previous digests under a fresh "
               "generation, and the restored state re-verified",
        restored=restored, verification=verification)
