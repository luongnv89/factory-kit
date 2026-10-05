#!/usr/bin/env python3
"""Durable registration records — PRD §6.4 REC01, §6.3 CFG-C02 (A7).

Kit-owned storage per the spike's Q6 split: Hermes owns task/attempt/fence
state in ``kanban.db``; the kit owns registration rows as per-profile JSON
that reference repository identity and can never dispatch by themselves.

A registration persists, per repository:

- **identity** — the immutable GitHub ``repo_id`` (authority key) plus
  display ``owner``/``name``; a display rename never re-keys authority;
- **configuration digest** — :func:`schema.effective_digest` of the
  validated effective configuration, bound into the active generation;
- **owner** — the single local registration owner;
- **supported version set** — the manifest/runtime versions accepted;
- **authorization policy digest** — :func:`schema.policy_digest`;
- **readiness outcome** — the substantive-readiness verdict snapshot.

Policy-change contract (CFG-C02, A7): a change to the policy surface can
never silently expand an active generation. Without an explicit
``authorized_by`` the change parks affected active work; with one it opens
a *new* generation bound to the new digests. Existing generation rows are
immutable — a later authorization never broadens a generation already
recorded.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from . import schema

__all__ = ["RegistrationError", "RegistrationStore"]


class RegistrationError(Exception):
    """A registration operation violated the contract."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class RegistrationStore:
    """JSON-backed registration rows (kit-owned, never dispatching)."""

    def __init__(self, path):
        self.path = str(path)
        self._data = self._load()

    # -- persistence --------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {"version": 1, "registrations": {}}
        except (json.JSONDecodeError, OSError) as exc:
            raise RegistrationError(
                f"cannot read registration store {self.path}: {exc}")
        if not isinstance(data, dict) or \
                not isinstance(data.get("registrations"), dict):
            raise RegistrationError(
                f"registration store {self.path} is not a valid store")
        return data

    def _save(self):
        """Atomic write — a torn registration row must never exist."""
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".registration-",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _registration(self, repo_id):
        record = self._data["registrations"].get(repo_id)
        if record is None:
            raise RegistrationError(
                f"no registration for repository {repo_id!r}")
        if record.get("tombstone"):
            raise RegistrationError(
                f"registration for {repo_id!r} was removed "
                f"(tombstone at {record.get('removed_at')!r} by "
                f"{record.get('removed_by')!r}) — a removed identity "
                "carries no work, generation or policy surface")
        return record

    @staticmethod
    def _active_generation(record):
        for gen in record["generations"]:
            if gen["generation"] == record["active_generation"]:
                return gen
        raise RegistrationError("registration has no active generation row")

    # -- register -----------------------------------------------------------

    def register(self, effective, *, readiness, supported_versions,
                 now=None):
        """Create (or idempotently return) the registration for a manifest.

        Keyed on the immutable ``repo_id`` — re-running registration with a
        renamed display ``owner``/``name`` updates display fields and
        returns the *same* registration (no duplicate, no authority move).
        """
        repo_id = effective["identity"]["repo_id"]
        existing = self._data["registrations"].get(repo_id)
        if existing is not None and existing.get("tombstone"):
            # Re-registration after a reviewed removal is a *new*
            # registration — the tombstone stays quoted on the fresh
            # record so the gravestone is audit, never silent state
            # resurrection (F08 A4).
            tombstone = existing
            existing = None
            resurrected_from = tombstone
        else:
            resurrected_from = None
        if existing is not None:
            if existing.get("removal"):
                # ``begin_removal`` committed the intake-stop — a setup
                # re-run cannot silently re-arm a registration mid-
                # removal; removal completes to the tombstone, then a
                # fresh registration starts clean (F08 A3).
                raise RegistrationError(
                    f"registration for {repo_id!r} is being removed "
                    f"(removal begun at "
                    f"{existing['removal'].get('begun_at')!r}) — "
                    "re-registration is only possible after the "
                    "reviewed removal completes")
            if existing.get("upgrade"):
                # ``begin_upgrade`` committed the dispatch fence — a
                # readiness run cannot silently re-arm a registration
                # mid-migration; the upgrade completes through
                # ``finish_upgrade`` or the checkpoint rollback, then
                # substantive readiness re-passes on the configuration
                # now on disk (F10 A3: no dispatch from a partially
                # migrated configuration).
                raise RegistrationError(
                    f"registration for {repo_id!r} is being upgraded "
                    f"(migration begun at "
                    f"{existing['upgrade'].get('begun_at')!r}) — "
                    "readiness re-passes only after the migration "
                    "completes or the checkpoint rollback restores the "
                    "previous validated configuration")
            display = effective["identity"]
            changed = (existing["display"]["owner"] != display["owner"] or
                       existing["display"]["name"] != display["name"])
            if changed:
                existing["display"] = {
                    "owner": display["owner"], "name": display["name"],
                }
            # Observational fields refresh on every re-run — they are not
            # authorization policy and never broaden the bound generation.
            if existing["readiness"] != dict(readiness) or \
                    existing["supported_versions"] != \
                    list(supported_versions):
                existing["readiness"] = dict(readiness)
                existing["supported_versions"] = list(supported_versions)
                changed = True
            if changed:
                self._save()
            active = self._active_generation(existing)
            if schema.policy_digest(effective) != active["policy_digest"]:
                # Re-registration under a drifted policy is a policy
                # change, not an idempotent no-op — it takes the same
                # unauthorized path as apply_policy_change: affected work
                # parks and a pending marker waits on authorization.
                result = self.apply_policy_change(
                    repo_id, effective, authorized_by=None)
                result["registration"] = existing
                return result
            return {"outcome": "existing", "registration": existing}

        record = {
            "repo_id": repo_id,
            "authority_key": schema.authority_key(effective["identity"]),
            "display": {
                "owner": effective["identity"]["owner"],
                "name": effective["identity"]["name"],
            },
            "owner": effective["registration"]["owner"],
            "supported_versions": list(supported_versions),
            "readiness": dict(readiness),
            "created_at": now or _utcnow(),
            "active_generation": 1,
            "generations": [{
                "generation": 1,
                "config_digest": schema.effective_digest(effective),
                "policy_digest": schema.policy_digest(effective),
                "authorized_by": effective["registration"]["owner"],
                "created_at": now or _utcnow(),
            }],
            "work": {},
        }
        if resurrected_from is not None:
            record["prior_tombstone"] = {
                "removed_at": resurrected_from.get("removed_at"),
                "removed_by": resurrected_from.get("removed_by"),
            }
        self._data["registrations"][repo_id] = record
        self._save()
        outcome = "re-registered" if resurrected_from is not None \
            else "registered"
        return {"outcome": outcome, "registration": record}

    def get(self, repo_id):
        return self._data["registrations"].get(repo_id)

    def is_tombstone(self, repo_id):
        """True when the row exists as a removal gravestone — identity
        retained for audit, no authority (F08 A4)."""
        record = self._data["registrations"].get(repo_id)
        return bool(record and record.get("tombstone"))

    # -- removal (Task 3.5 / F08) ---------------------------------------------
    #
    # Removal is a two-stage durable transition: ``begin_removal`` stops
    # intake *first* (readiness verdict ``removing``/dispatch ``denied`` —
    # intake's gate denies every later delivery), parking the generation's
    # bound work; ``remove`` then replaces the row with an identity
    # tombstone. The tombstone is the record that this repo_id was once
    # registered and deliberately removed — a display rename or a stale
    # cached row can never silently resurrect authority, and every
    # work-bound accessor (``record_work``, ``attempt_context``,
    # ``apply_policy_change``, ``authorize_generation``) refuses a
    # tombstoned identity via ``_registration``.

    def begin_removal(self, repo_id, *, removed_by, now=None):
        """Durably stop intake for this registration.

        Commits *before* any credential/state removal (F08 A3): from
        this write on, intake denies deliveries for the repo
        (``registration-not-ready``), and active bound work is parked so
        no new attempt can bind under the removal generation.
        """
        record = self._registration(repo_id)
        record["readiness"] = {
            "verdict": "removing",
            "dispatch": "denied",
            "detail": "reviewed removal in progress — intake stopped "
                      "and bound work parked",
        }
        record["removal"] = {"begun_at": now or _utcnow(),
                             "by": removed_by}
        parked = []
        for key, work in record["work"].items():
            if work["state"] == "active":
                work["state"] = "parked"
                work["parked_reason"] = "registration-removal"
                parked.append(key)
        self._save()
        return {"outcome": "removing", "repo_id": repo_id,
                "parked_work": parked}

    def remove(self, repo_id, *, removed_by, now=None):
        """Replace the registration with an identity tombstone.

        The tombstone keeps ``repo_id`` + ``authority_key`` +
        ``removed_at``/``removed_by`` so removal consequences are
        explainable (F08 A4): the row *proves* the identity is gone,
        intake denies it, and accessors refuse it. Shared Hermes
        infrastructure, IDD configuration, shared skills and tokens are
        untouched — they were never owned by this registration.
        """
        record = self._registration(repo_id)
        tombstone = {
            "repo_id": repo_id,
            "authority_key": record["authority_key"],
            "tombstone": True,
            "removed_at": now or _utcnow(),
            "removed_by": removed_by,
            "prior_owner": record["owner"],
            "generations_removed": len(record.get("generations") or []),
            "work_parked": sorted(
                key for key, work in record.get("work", {}).items()
                if work.get("state") == "parked"),
        }
        self._data["registrations"][repo_id] = tombstone
        self._save()
        return {"outcome": "tombstoned", "tombstone": tombstone}

    # -- upgrade fence (Task 4.2 / F10) ---------------------------------------
    #
    # Upgrade is a two-marker durable transition mirroring removal:
    # ``begin_upgrade`` fences dispatch *first* — readiness verdict
    # ``upgrading`` with ``dispatch: denied`` means intake denies every
    # delivery for the duration, and active bound work parks so no
    # attempt can bind mid-migration — *before* any owned file or
    # generation is touched (A2/A3). ``finish_upgrade`` clears the marker
    # into a still-denied terminal readiness — ``pending`` after a clean
    # upgrade (enabled only when substantive readiness re-passes on the
    # new generation), ``restored`` after a checkpoint rollback,
    # ``parked`` when a specific conflict blocked repair — so a
    # partially migrated configuration can never dispatch workers.

    def begin_upgrade(self, repo_id, *, upgraded_by, now=None):
        """Durably fence dispatch for a reviewed upgrade.

        Commits *before* the migration touches files or generations
        (F10 A2): from this write on, intake denies deliveries for the
        repo and active bound work is parked (``upgrade-in-progress``).
        A second ``begin_upgrade`` while a marker is open refuses — two
        migrations never interleave under one registration.
        """
        record = self._registration(repo_id)
        if record.get("upgrade"):
            raise RegistrationError(
                f"registration for {repo_id!r} already has an upgrade "
                f"in progress (begun at "
                f"{record['upgrade'].get('begun_at')!r} by "
                f"{record['upgrade'].get('by')!r}) — repair or roll "
                "back the open migration first")
        record["readiness"] = {
            "verdict": "upgrading",
            "dispatch": "denied",
            "detail": "reviewed upgrade in progress — intake denies "
                      "every delivery until the new generation "
                      "validates (F10 A2/A3)",
        }
        record["upgrade"] = {"begun_at": now or _utcnow(),
                             "by": upgraded_by}
        parked = []
        for key, work in record["work"].items():
            if work["state"] == "active":
                work["state"] = "parked"
                work["parked_reason"] = "upgrade-in-progress"
                parked.append(key)
        self._save()
        return {"outcome": "upgrading", "repo_id": repo_id,
                "parked_work": parked}

    def finish_upgrade(self, repo_id, *, outcome, supported_versions=None,
                       now=None):
        """Close the upgrade fence into a still-denied readiness gate.

        ``outcome`` is one of ``upgraded`` (new generation validated),
        ``rolled-back`` (checkpoint restored) or ``parked`` (a specific
        conflict blocked repair). Every terminal keeps
        ``dispatch: denied``: dispatch resumes only when substantive
        readiness passes on the configuration now on disk — never on a
        partially migrated one (A3). ``supported_versions`` refreshes
        the recorded pin set so the support matrix reflects what was
        applied.
        """
        record = self._registration(repo_id)
        details = {
            "upgraded": "upgrade applied — enabled only after "
                        "substantive readiness passes on the new "
                        "generation (F10 A2)",
            "rolled-back": "previous validated configuration restored "
                           "— enabled only after substantive readiness "
                           "re-passes on it (F10 A5)",
            "parked": "migration parked on a specific conflict — "
                      "dispatch stays denied; resolve the conflict "
                      "and repair or roll back",
        }
        if outcome not in details:
            raise RegistrationError(
                f"unknown upgrade outcome {outcome!r} — expected one of "
                f"{sorted(details)}")
        upgrade = record.pop("upgrade", None)
        if outcome == "parked":
            # A parked migration stays open — the fence marker persists
            # so ``register`` keeps refusing to re-arm dispatch until
            # repair restores the checkpoint or rollback seals it (A3).
            upgrade = dict(upgrade or {})
            upgrade["parked_at"] = now or _utcnow()
            record["upgrade"] = upgrade
        record["readiness"] = {
            "verdict": {"upgraded": "pending",
                        "rolled-back": "restored",
                        "parked": "parked"}[outcome],
            "dispatch": "denied",
            "detail": details[outcome],
        }
        if supported_versions is not None:
            record["supported_versions"] = list(supported_versions)
        record["upgrade_closed"] = {"outcome": outcome,
                                    "at": now or _utcnow()}
        self._save()
        return {"outcome": outcome, "was": upgrade,
                "repo_id": repo_id}

    # -- work binding ---------------------------------------------------------

    def record_work(self, repo_id, work_key):
        """Bind a work item to the active generation (one attempt surface)."""
        record = self._registration(repo_id)
        record["work"][work_key] = {
            "generation": record["active_generation"],
            "state": "active",
            "recorded_at": _utcnow(),
        }
        self._save()
        return record["work"][work_key]

    def attempt_context(self, repo_id, work_key, role):
        """The context every attempt carries: identity + generation + the
        immutable effective-configuration digest (CFG-C02)."""
        record = self._registration(repo_id)
        work = record["work"].get(work_key)
        if work is None:
            raise RegistrationError(
                f"no recorded work {work_key!r} under {repo_id!r}")
        if work["state"] != "active":
            raise RegistrationError(
                f"work {work_key!r} is {work['state']} — a parked item "
                "cannot attach a config digest to a new attempt")
        bound = None
        for gen in record["generations"]:
            if gen["generation"] == work["generation"]:
                bound = gen
                break
        if bound is None:
            raise RegistrationError(
                f"work {work_key!r} references unknown generation "
                f"{work['generation']}")
        return {
            "repo_id": repo_id,
            "work_key": work_key,
            "role": role,
            "generation": bound["generation"],
            "config_digest": bound["config_digest"],
            "policy_digest": bound["policy_digest"],
        }

    # -- policy change (CFG-C02) --------------------------------------------

    def apply_policy_change(self, repo_id, new_effective, *,
                            authorized_by=None, now=None):
        """Apply a changed effective configuration.

        - Display-only drift (``identity.owner``/``name``) updates display
          fields and touches nothing else — authority and generations stay.
        - Policy drift without ``authorized_by`` parks every active work
          item and records a ``parked-policy-change`` marker — nothing
          expands, nothing restarts.
        - With ``authorized_by``, a new generation is appended bound to the
          new digests; the prior generation row is left byte-identical, and
          parked work must be re-bound explicitly under the new generation
          via :meth:`record_work`.
        """
        record = self._registration(repo_id)
        generation = self._active_generation(record)
        new_policy = schema.policy_digest(new_effective)
        display = new_effective["identity"]
        # Display fields are never policy: refresh them on every path so a
        # rename riding alongside a policy change is not lost.
        record["display"] = {
            "owner": display["owner"], "name": display["name"],
        }

        if new_policy == generation["policy_digest"]:
            self._save()
            return {"outcome": "unchanged",
                    "generation": generation["generation"]}

        affected = [key for key, work in record["work"].items()
                    if work["state"] == "active"]

        if authorized_by is None:
            for key in affected:
                record["work"][key]["state"] = "parked"
                record["work"][key]["parked_reason"] = \
                    "policy-change-unauthorized"
            record["pending_policy"] = {
                "policy_digest": new_policy,
                "config_digest": schema.effective_digest(new_effective),
                "seen_at": now or _utcnow(),
                "affected": affected,
            }
            self._save()
            return {"outcome": "parked", "affected": affected,
                    "generation": generation["generation"]}

        pending = record.pop("pending_policy", None)
        record["generations"].append({
            "generation": generation["generation"] + 1,
            "config_digest": schema.effective_digest(new_effective),
            "policy_digest": new_policy,
            "authorized_by": authorized_by,
            "created_at": now or _utcnow(),
            "supersedes": generation["generation"],
        })
        record["active_generation"] = generation["generation"] + 1
        for key in affected:
            record["work"][key]["state"] = "parked"
            record["work"][key]["parked_reason"] = \
                "generation-superseded-awaiting-rebind"
        self._save()
        return {
            "outcome": "new-generation",
            "generation": generation["generation"] + 1,
            "parked_pending_rebind": affected,
            "pending_policy_was": pending,
        }

    # -- operator retry (Task 2.4 / F03 A5) ------------------------------------

    def authorize_generation(self, repo_id, effective, *, authorized_by,
                             reason="operator-retry", now=None):
        """Mint a new audited execution generation under *fresh*
        authorization — the operator-retry path.

        Unlike :meth:`apply_policy_change` this does not require a policy
        drift: an explicit, attributable ``authorized_by`` is itself the
        authority to supersede the current generation (the digests bind
        to whatever effective configuration is supplied — unchanged
        policy retries simply rebind the same digests under a new,
        separately audited generation). Every prior generation row is
        left byte-identical and every attempt/usage record under the old
        work key stays visible — a retry never hides history (A5).

        ``authorized_by`` is required and must be a non-empty principal:
        there is no silent retry path.
        """
        if not isinstance(authorized_by, str) or not authorized_by.strip():
            raise RegistrationError(
                "a retry requires an explicit authorized_by principal — "
                "fresh authorization is the point of the new generation")
        record = self._registration(repo_id)
        generation = self._active_generation(record)
        affected = [key for key, work in record["work"].items()
                    if work["state"] == "active"]
        record["generations"].append({
            "generation": generation["generation"] + 1,
            "config_digest": schema.effective_digest(effective),
            "policy_digest": schema.policy_digest(effective),
            "authorized_by": authorized_by,
            "authorized_reason": reason,
            "created_at": now or _utcnow(),
            "supersedes": generation["generation"],
        })
        record["active_generation"] = generation["generation"] + 1
        for key in affected:
            record["work"][key]["state"] = "parked"
            record["work"][key]["parked_reason"] = \
                "generation-superseded-awaiting-rebind"
        self._save()
        return {
            "outcome": "new-generation",
            "generation": generation["generation"] + 1,
            "authorized_by": authorized_by,
            "parked_pending_rebind": affected,
        }
