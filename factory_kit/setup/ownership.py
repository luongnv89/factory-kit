#!/usr/bin/env python3
"""Setup ownership ledger — kit-owned state for additive installation.

PRD §3.2 F01 (A2/A5/A6), §6.4 REC01; issue #7 / Task 2.2.

The ledger is the durable record that makes setup *reviewable* and removal
(F08) possible later. It is kit-owned per-project JSON — like
:class:`factory_kit.config.registration.RegistrationStore`, it persists
under the profile state root and can never dispatch work by itself. One
file per managed project records:

- **ownership** — every factory-owned file the apply step wrote, with its
  sha256/size at write time and the plan digest that produced it. A file
  whose on-disk bytes no longer match is *user-modified*: preserved, never
  silently overwritten or deleted.
- **remote effects** — the webhook/configuration/dependency effects an
  accepted plan created. Live remote mutation is executed by an injected
  effects adapter; the default sink records ``recorded-intent`` rows so a
  pending remote change is diagnosable and idempotent rather than claimed.
- **events** — the append-only ``setup_checked`` / ``setup_applied`` trail
  (§7.1 EVT01): project identity, version set, duration and
  per-prerequisite outcomes for every readiness run.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

__all__ = ["OwnershipError", "SetupStore", "sha256_file", "sha256_bytes"]


class OwnershipError(Exception):
    """The setup-state ledger could not be read or was asked to do
    something outside the contract."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SetupStore:
    """JSON-backed ownership/effects/events ledger for one project.

    ``path`` lives under the operator's profile state root (never inside
    the managed repository — factory state is not repo-reviewed code and
    must not become part of the diff it describes).
    """

    def __init__(self, path):
        self.path = str(path)
        self._data = self._load()

    # -- persistence -------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return {
                "version": 1,
                "project_id": None,
                "ownership": {},
                "remote_effects": {},
                "applied_plans": [],
                "events": [],
            }
        except (json.JSONDecodeError, OSError) as exc:
            raise OwnershipError(
                f"cannot read setup state {self.path}: {exc}")
        if not isinstance(data, dict) or \
                not isinstance(data.get("ownership"), dict) or \
                not isinstance(data.get("remote_effects", {}), dict) or \
                not isinstance(data.get("applied_plans", []), list) or \
                not isinstance(data.get("events"), list):
            raise OwnershipError(
                f"setup state {self.path} is not a valid ledger")
        data.setdefault("version", 1)
        data.setdefault("project_id", None)
        data.setdefault("remote_effects", {})
        data.setdefault("applied_plans", [])
        return data

    def _save(self):
        """Atomic write — a torn ledger must never exist."""
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".setup-",
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

    # -- project identity ---------------------------------------------------

    def set_project_id(self, project_id):
        if self._data["project_id"] not in (None, project_id):
            raise OwnershipError(
                f"setup state {self.path} belongs to project "
                f"{self._data['project_id']!r}, not {project_id!r}")
        self._data["project_id"] = project_id
        self._save()

    @property
    def project_id(self):
        return self._data["project_id"]

    # -- ownership ------------------------------------------------------------

    def record_file(self, path, sha256, size, *, kind, plan_digest):
        """Record one factory-owned file and its checksum at write time."""
        self._data["ownership"][path] = {
            "sha256": sha256,
            "size": size,
            "kind": kind,
            "plan_digest": plan_digest,
            "recorded_at": _utcnow(),
        }
        self._save()

    def owned_files(self):
        return dict(self._data["ownership"])

    def verify_file(self, repo_root, path):
        """Compare a recorded checksum against the file on disk.

        Returns ``unchanged`` / ``user-modified`` / ``missing`` /
        ``unowned``. ``user-modified`` is a *finding*, never an error —
        developer edits to a factory-created file are preserved work.
        """
        record = self._data["ownership"].get(path)
        if record is None:
            return "unowned"
        target = os.path.join(str(repo_root), path)
        if not os.path.isfile(target):
            return "missing"
        try:
            if sha256_file(target) == record["sha256"]:
                return "unchanged"
        except OSError:
            return "missing"
        return "user-modified"

    # -- remote effects ---------------------------------------------------------

    def record_effect(self, effect_id, entry):
        """Record a remote-effect intent/result. Idempotent on
        ``effect_id``: an already-recorded effect returns ``False`` and is
        never duplicated (A2 — a second apply creates no second webhook).
        """
        if effect_id in self._data["remote_effects"]:
            return False
        self._data["remote_effects"][effect_id] = {
            "effect_id": effect_id,
            "entry": dict(entry),
            "recorded_at": _utcnow(),
        }
        self._save()
        return True

    def has_effect(self, effect_id):
        return effect_id in self._data["remote_effects"]

    def effects(self):
        return dict(self._data["remote_effects"])

    # -- applied plans ----------------------------------------------------------

    def mark_plan_applied(self, plan_digest):
        if plan_digest not in self._data["applied_plans"]:
            self._data["applied_plans"].append(plan_digest)
            self._save()

    def is_plan_applied(self, plan_digest):
        return plan_digest in self._data["applied_plans"]

    def applied_plans(self):
        return list(self._data["applied_plans"])

    # -- events (§7.1 setup_checked) ---------------------------------------------

    def record_event(self, name, payload):
        """Append one event to the durable trail; returns the event row."""
        event = {"event": name, "at": _utcnow(), **dict(payload)}
        self._data["events"].append(event)
        self._save()
        return event

    def events(self, name=None):
        events = self._data["events"]
        if name is None:
            return list(events)
        return [e for e in events if e.get("event") == name]
