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
        if existing is not None:
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
        self._data["registrations"][repo_id] = record
        self._save()
        return {"outcome": "registered", "registration": record}

    def get(self, repo_id):
        return self._data["registrations"].get(repo_id)

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
