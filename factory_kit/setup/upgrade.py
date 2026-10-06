#!/usr/bin/env python3
"""Reviewed versioned upgrade, checkpoint repair and rollback — F10.

PRD §3.2 F10 (with F01/F08 machinery), §8.3; issue #26 / Task 4.2.

The mirror of :mod:`factory_kit.setup.plan` / :mod:`factory_kit.setup.apply`
for moving a *pinned, supported* installed version to a reviewed target:

- :func:`inspect_upgrade` is strictly read-only: it detects the installed
  state from the ownership ledger and registration row, runs the
  compatibility checks (supported installed pins, recorded schema
  migration path, immutable repository identity, manifest validity,
  ownership checksum preconditions, secret hygiene) and emits an
  operator-reviewable *upgrade plan* carrying the changed owned files
  with diffs, every user-edit conflict, the ordered migration steps and
  the rollback instructions (A1). Nothing is mutated before
  :func:`accept_upgrade` binds the ``upgrade_digest``.
- :func:`apply_upgrade` executes the accepted plan behind durable stage
  boundaries (A2/A3): the intake fence commits **first** — readiness
  ``upgrading``/``denied`` plus parked bound work — then a checkpoint
  of every owned byte and the registration digests, then owned-file
  writes, then a new authorized generation bound to the new digests,
  then re-validation of the written bytes, then dependency/skill pin
  provenance — all before dispatch may resume (readiness stays gated by
  a fresh ``setup_checked`` on the new configuration).
- :func:`rollback_upgrade` is the documented rollback (A5): it restores
  the checkpointed owned bytes byte-for-byte and mints a new
  registration generation bound to the *previous* validated digests —
  history is appended, never rewritten — then re-verifies the restored
  state rather than claiming it.

Every stage transition is one durable write in the ``SetupStore``
``migrations`` area, so a crash can only land *between* committed
boundaries; :mod:`factory_kit.setup.repair` reads those records to
restore the validated checkpoint or park with a specific conflict, and
a partially migrated configuration never dispatches (A3).

Changed checksum preconditions block application (A4): apply re-verifies
each owned file's reviewed sha256 before the fence commits — drift means
refusal, not a guessed merge. User edits are preserved byte-for-byte:
they are conflicts at review time unless the operator stamps a specific
reviewed ``resolution: "discard"`` on the file entry (re-keying the
digest), and any unrecognized on-disk bytes during restore are a
conflict, never overwritten.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from factory_kit import SUPPORTED_SCHEMA_VERSIONS, VERSION
from factory_kit.config import migrations, schema, yamlmini
from factory_kit.config.registration import RegistrationError
from factory_kit.execution.limits import TERMINAL_WORK_STATES

from . import plan as plan_mod
from .apply import RecordingEffectSink, _safe_target
from .ownership import SetupStore, sha256_bytes, sha256_file

__all__ = [
    "MIGRATION_STAGES",
    "UPGRADE_VERSION",
    "UpgradeError",
    "accept_upgrade",
    "apply_upgrade",
    "inspect_upgrade",
    "migration_id_for",
    "rollback_upgrade",
    "upgrade_digest",
]

UPGRADE_VERSION = 1

#: Ordered migration boundaries. ``advance_migration`` commits each in
#: one durable write; an interruption lands strictly between them (A3).
#: The extra non-terminal stage ``failed`` marks a halted in-flight
#: migration (repair owns it). Terminal: ``complete``, ``rolled-back``,
#: ``parked`` (defined on the store's ``open_migrations``).
MIGRATION_STAGES = (
    "planned",
    "fenced",
    "checkpointed",
    "files-migrated",
    "registration-migrated",
    "validated",
    "provenance-pinned",
    "complete",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical(node) -> str:
    return json.dumps(node, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def upgrade_digest(plan) -> str:
    """Digest over the reviewable surface — everything apply consumes.

    ``installed``/``target`` are the version pair; ``files`` the reviewed
    byte transitions (payloads included); ``compatibility``/``conflicts``/
    ``findings`` what the operator accepted; ``migration_steps``/
    ``rollback`` the promised procedure; ``manifest`` the effective map
    the new generation binds; ``preserved`` the unrelated-bytes checksum
    set apply re-verifies. Mutating any of them re-keys the plan, so the
    acceptance bound to this digest cannot cover different content
    silently.
    """
    surface = {k: plan[k] for k in
               ("installed", "target", "files", "entries",
                "compatibility", "conflicts", "findings",
                "migration_steps", "rollback", "authority",
                "manifest", "preserved", "project_id")
               if k in plan}
    return hashlib.sha256(_canonical(surface).encode()).hexdigest()


def migration_id_for(digest) -> str:
    """Stable migration identity for an accepted plan."""
    return f"upgrade-{digest[:16]}"


def _dependency_effect_id(effective) -> str:
    """The dependency-pin effect is re-keyed per provenance set — a new
    policy digest mints a fresh recorded intent instead of silently
    reusing the install-time row (A2 provenance)."""
    return ("dependency:approved-skill-pins:"
            f"{schema.policy_digest(effective)[:16]}")


def _version_tuple(value):
    """Parse a version pin into a SemVer-orderable tuple.

    Prereleases sort before the corresponding final release, so a beta
    kit accepts older registrations but does not treat a same-numbered
    final release as compatible.
    """
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)"
        r"(?:-?([0-9A-Za-z][0-9A-Za-z.-]*))?"
        r"(?:\+[0-9A-Za-z.-]+)?",
        str(value or "").strip())
    if not match:
        return None
    major, minor, patch = (int(x) for x in match.groups()[:3])
    prerelease = match.group(4)
    if prerelease is None:
        return major, minor, patch, 1, ()
    identifiers = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in prerelease.split("."))
    return major, minor, patch, 0, identifiers


def _check(name, ok, detail):
    return {"check": name, "status": "pass" if ok else "fail",
            "detail": detail}


def _conflict(conflicts, source, detail, **extra):
    conflicts.append({"source": source, "detail": detail, **extra})


def _supported_pin_set(versions):
    """Validate a recorded ``supported_versions`` pin list.

    ``manifest/N`` must name a supported schema; ``factory-kit/X`` must
    be a parseable semver not newer than this kit — an install made by a
    *newer* kit is an unknown base, and upgrade never guesses (A4).
    """
    problems = []
    kit_seen = manifest_seen = False
    for pin in versions or []:
        name, _, value = str(pin).partition("/")
        if name == "manifest":
            manifest_seen = True
            try:
                ver = int(value)
            except ValueError:
                problems.append(f"unparseable manifest pin {pin!r}")
                continue
            if ver not in SUPPORTED_SCHEMA_VERSIONS:
                problems.append(
                    f"installed manifest schema {ver} is unsupported "
                    f"(supported: {list(SUPPORTED_SCHEMA_VERSIONS)})")
        elif name == "factory-kit":
            kit_seen = True
            parsed = _version_tuple(value)
            if parsed is None:
                problems.append(f"unparseable kit pin {pin!r}")
            elif parsed > _version_tuple(VERSION):
                problems.append(
                    f"installed kit {value} is newer than this kit "
                    f"{VERSION} — no recorded downgrade path")
        else:
            problems.append(f"unknown pin {pin!r}")
    if not manifest_seen:
        problems.append("no manifest/<schema> pin recorded")
    if not kit_seen:
        problems.append("no factory-kit/<version> pin recorded")
    return problems


def _owned_files(repo_root, store, *, candidate_bytes, resolutions):
    """Per-owned-file review state for the plan's ``files`` list.

    ``review_sha256`` is the on-disk checksum at review time — the
    checksum precondition apply re-verifies (A4). ``to_sha256`` /
    ``payload`` carry the reviewed target bytes for the configuration
    file (the digest binds them); unchanged owned files keep their
    recorded checksum as target. ``resolutions`` is the operator's
    reviewed per-entry instruction map — ``file:<path>=discard``
    overwrites a user-edited file with the reviewed candidate bytes,
    ``file:<path>=adopt`` keeps the edited bytes and re-records them
    as the new owned baseline — stamped into the plan so the digest
    re-keys over the decision.
    """
    candidate_sha = sha256_bytes(candidate_bytes) \
        if candidate_bytes is not None else None
    files = []
    for path, rec in sorted(store.owned_files().items()):
        state = store.verify_file(repo_root, path)
        target = os.path.join(repo_root, path)
        entry = {
            "id": f"file:{path}",
            "kind": rec.get("kind") or "configuration",
            "path": path,
            "recorded_sha256": rec.get("sha256"),
            "review_state": state,
            "action": "keep",
            "to_sha256": rec.get("sha256"),
        }
        if state in ("unchanged", "user-modified"):
            entry["review_sha256"] = sha256_file(target)
        resolution = resolutions.get(entry["id"])
        if resolution is not None:
            entry["resolution"] = resolution
        if path == plan_mod.MANIFEST_PATH and candidate_bytes is not None:
            if resolution == "discard" or state != "user-modified":
                entry["to_sha256"] = candidate_sha
                if entry.get("review_sha256") == candidate_sha:
                    entry["action"] = "keep"
                    entry["reason"] = "identical bytes already installed"
                else:
                    entry["action"] = "replace"
                    entry["payload"] = candidate_bytes.decode("utf-8")
                    entry["reason"] = (
                        "the factory-owned manifest migrates to the "
                        "reviewed candidate bytes (CFG-C01)")
            else:
                entry["reason"] = (
                    "user-edited manifest — kept byte-for-byte unless "
                    f"the operator stamps file:{path}=discard")
        elif state == "user-modified" and resolution == "adopt":
            entry["to_sha256"] = entry["review_sha256"]
            entry["reason"] = ("operator-adopted edit — the reviewed "
                               "bytes become the new owned baseline")
        else:
            entry["reason"] = ("owned file unchanged by this upgrade — "
                               "checksum re-verified at apply")
        files.append(entry)
    return files


def inspect_upgrade(repo_root, store, manifest_source=None, *,
                    registration_store=None, intake_store=None,
                    supported_versions=None, resolutions=None, now=None):
    """Inspect the installed state and emit a reviewable upgrade plan.

    Strictly read-only (A1): every compatibility check, changed owned
    file, user-edit conflict, migration step and rollback instruction is
    a datum for review — the plan mutates nothing, and ``appliable`` is
    true only when no conflict exists.
    """
    repo_root = os.path.abspath(str(repo_root))
    if not os.path.isdir(repo_root):
        raise UpgradeError(f"repository root {repo_root!r} is not a "
                           "directory")
    store = store if isinstance(store, SetupStore) else SetupStore(store)
    project_id = store.project_id

    plan = {
        "upgrade_version": UPGRADE_VERSION,
        "generated_at": now or _utcnow(),
        "repo_root": repo_root,
        "project_id": project_id,
    }
    checks, conflicts, findings, notes = [], [], [], []

    # -- installed state ----------------------------------------------------
    registration = None
    installed_effective = None
    manifest_path = os.path.join(repo_root, plan_mod.MANIFEST_PATH)
    manifest_owned = plan_mod.MANIFEST_PATH in store.owned_files()
    manifest_bytes = None
    if os.path.isfile(manifest_path) and not os.path.islink(manifest_path):
        with open(manifest_path, "rb") as handle:
            manifest_bytes = handle.read()
    if registration_store is not None and project_id:
        registration = registration_store.get(project_id)

    installed = {
        "repo_id": project_id,
        "manifest_owned": manifest_owned,
        "manifest_sha256": sha256_bytes(manifest_bytes)
        if manifest_bytes is not None else None,
        "supported_versions": None,
        "active_generation": None,
        "config_digest": None,
        "policy_digest": None,
        "schema_version": None,
        "skill_pins": {},
    }

    if project_id is None:
        _conflict(conflicts, "installed-state",
                  "the setup ledger carries no project identity — "
                  "run `plan`/`apply` to install before upgrading")
    elif not manifest_owned:
        _conflict(conflicts, "installed-state",
                  f"{plan_mod.MANIFEST_PATH} is not recorded as owned "
                  "— nothing installed to upgrade")
    if registration is None:
        if project_id:
            _conflict(conflicts, "installed-state",
                      f"no registration row for {project_id!r} — "
                      "upgrade requires a live registered install")
    elif registration.get("tombstone"):
        _conflict(conflicts, "installed-state",
                  f"registration for {project_id!r} is tombstoned — "
                  "removed installs re-register, they do not upgrade")
    elif registration.get("removal"):
        _conflict(conflicts, "installed-state",
                  f"registration for {project_id!r} is mid-removal — "
                  "complete the reviewed removal first")
    elif registration.get("upgrade"):
        _conflict(conflicts, "installed-state",
                  f"an upgrade for {project_id!r} is already in flight "
                  f"(begun {registration['upgrade'].get('begun_at')!r})"
                  " — repair or roll back the open migration")
    else:
        installed["supported_versions"] = list(
            registration.get("supported_versions") or [])
        installed["active_generation"] = registration.get(
            "active_generation")
        gen = next((g for g in registration.get("generations") or []
                    if g.get("generation") ==
                    installed["active_generation"]), None)
        if gen:
            installed["config_digest"] = gen.get("config_digest")
            installed["policy_digest"] = gen.get("policy_digest")
    installed_conflicts = sum(
        1 for c in conflicts if c["source"] == "installed-state")
    checks.append(_check(
        "installed-state", installed_conflicts == 0,
        "live registered install detected"
        if installed_conflicts == 0 else "no usable install base"))

    # -- installed version pins (A4: unsupported installs block) --------------
    if installed["supported_versions"]:
        pin_problems = _supported_pin_set(installed["supported_versions"])
        if pin_problems:
            _conflict(conflicts, "installed-version",
                      "; ".join(pin_problems))
        checks.append(_check(
            "installed-version", not pin_problems,
            "; ".join(pin_problems) if pin_problems else
            "installed pins supported: "
            f"{installed['supported_versions']}"))
    if manifest_bytes is not None and manifest_owned:
        try:
            installed_effective = schema.load_manifest(
                manifest_bytes.decode("utf-8"))
            installed["schema_version"] = \
                installed_effective["factory_kit"]
            installed["skill_pins"] = dict(
                installed_effective["skills"]["approved"])
        except Exception as exc:  # noqa: BLE001 — recorded, not hidden
            findings.append({"source": "installed-manifest",
                             "detail": f"installed manifest bytes do "
                                      f"not validate: {exc}"})

    # -- candidate manifest ------------------------------------------------------
    candidate_bytes = None
    source_desc = None
    if manifest_source is not None:
        if hasattr(manifest_source, "read"):
            candidate_bytes = manifest_source.read()
            if isinstance(candidate_bytes, str):
                candidate_bytes = candidate_bytes.encode("utf-8")
            source_desc = getattr(manifest_source, "name", "<stream>")
        else:
            try:
                with open(str(manifest_source), "rb") as handle:
                    candidate_bytes = handle.read()
            except OSError as exc:
                raise UpgradeError(
                    f"cannot read candidate manifest "
                    f"{manifest_source}: {exc}") from exc
            source_desc = str(manifest_source)
    elif manifest_bytes is not None:
        candidate_bytes = manifest_bytes
        source_desc = plan_mod.MANIFEST_PATH
        notes.append("no candidate manifest supplied — the plan targets "
                     "the installed bytes (a re-affirming no-change "
                     "upgrade)")

    new_effective = None
    candidate_sha = sha256_bytes(candidate_bytes) \
        if candidate_bytes is not None else None
    if candidate_bytes is None:
        _conflict(conflicts, "manifest",
                  "no candidate manifest — supply --manifest or install "
                  f"{plan_mod.MANIFEST_PATH} first")
        checks.append(_check("manifest", False, "no candidate"))
    else:
        try:
            new_effective = schema.load_manifest(
                candidate_bytes.decode("utf-8"))
            checks.append(_check(
                "manifest", True,
                f"candidate validates; schema "
                f"{new_effective['factory_kit']}, effective digest "
                f"{schema.effective_digest(new_effective)[:12]}…"))
        except UnicodeDecodeError:
            _conflict(conflicts, "manifest",
                      "candidate manifest is not UTF-8 text")
            checks.append(_check("manifest", False, "not UTF-8"))
        except yamlmini.ParseError as exc:
            _conflict(conflicts, "manifest",
                      f"candidate manifest does not parse: {exc}")
            checks.append(_check("manifest", False,
                                 f"parse error: {exc}"))
        except schema.ConfigError as exc:
            _conflict(conflicts, "manifest",
                      "candidate manifest failed validation",
                      problems=exc.problems)
            checks.append(_check("manifest", False,
                                 f"{len(exc.problems)} validation "
                                 "problem(s)"))

    # -- schema migration path (the versioned hop — F10) -------------------------
    hops = None
    if installed["schema_version"] is not None and \
            new_effective is not None:
        hops = migrations.migration_path(
            installed["schema_version"], new_effective["factory_kit"])
        if hops is None:
            _conflict(
                conflicts, "schema-path",
                f"no recorded migration path "
                f"{installed['schema_version']} → "
                f"{new_effective['factory_kit']} — supported schemas: "
                f"{list(SUPPORTED_SCHEMA_VERSIONS)}")
        checks.append(_check(
            "schema-path", hops is not None,
            "identity hop (same supported schema)" if hops == [] else
            f"{len(hops)} recorded hop(s)" if hops else
            "no recorded path"))
    elif new_effective is not None:
        findings.append({
            "source": "schema-path",
            "detail": "installed schema unreadable — the recorded "
                      "checksum preconditions still gate apply",
        })

    # -- identity stability (authority never re-keys) ------------------------------
    if new_effective is not None and registration is not None:
        new_repo = new_effective["identity"]["repo_id"]
        same = new_repo == project_id
        if not same:
            _conflict(conflicts, "identity",
                      f"candidate repo_id {new_repo!r} != installed "
                      f"{project_id!r} — an upgrade never moves "
                      "repository authority")
        checks.append(_check(
            "identity", same,
            f"repo_id {project_id!r} unchanged" if same else
            "candidate re-keys authority — refused"))

    # -- secret hygiene over candidate bytes ---------------------------------------
    canaries = []
    if candidate_bytes is not None:
        canaries = plan_mod.canary_scan(
            candidate_bytes.decode("utf-8", "replace"))
    if canaries:
        _conflict(conflicts, "secret-canary",
                  "candidate manifest matches a secret canary — store "
                  "secrets in approved storage and reference them",
                  patterns=len(canaries))
    checks.append(_check("secret-hygiene", not canaries,
                         f"{len(canaries)} canary match(es)"
                         if canaries else
                         "no literal secrets in candidate"))

    # -- ownership checksum preconditions + user-edit conflicts (A4) ----------------
    resolutions = dict(resolutions or {})
    files = _owned_files(repo_root, store, candidate_bytes=candidate_bytes,
                         resolutions=resolutions)
    for entry in files:
        if entry["review_state"] == "user-modified":
            resolution = entry.get("resolution")
            manifest = entry["path"] == plan_mod.MANIFEST_PATH
            adopted = resolution == "adopt" and not manifest
            discarded = resolution == "discard" and manifest and \
                entry["action"] == "replace"
            if not (adopted or discarded):
                hint = (
                    "stamp a reviewed resolution: "
                    f"file:{entry['path']}=discard overwrites with the "
                    "candidate bytes (digest re-keys), or re-inspect"
                    if manifest else
                    "stamp a reviewed resolution: "
                    f"file:{entry['path']}=adopt re-records the edited "
                    "bytes as the owned baseline (digest re-keys), or "
                    "restore the recorded bytes and re-inspect")
                _conflict(conflicts, "user-edit",
                          f"{entry['path']} was edited after install — "
                          "preserved byte-for-byte; " + hint,
                          path=entry["path"])
        elif entry["review_state"] == "missing":
            _conflict(conflicts, "ownership",
                      f"owned file {entry['path']} is missing — "
                      "restore it or repair the install before "
                      "upgrading",
                      path=entry["path"])
        elif entry["review_state"] == "unowned":
            _conflict(conflicts, "ownership",
                      f"{entry['path']} lost its ownership record — "
                      "repair the install before upgrading",
                      path=entry["path"])
    user_edited = [e["path"] for e in files
                   if e["review_state"] == "user-modified"]
    checks.append(_check(
        "ownership",
        not any(c["source"] in ("user-edit", "ownership")
                for c in conflicts),
        f"{len(files)} owned file(s) verified unchanged"
        if not user_edited else
        f"user edits on {user_edited} — conflicts"))

    # -- live work the upgrade fence must cover (A2) ---------------------------------
    authority_key = schema.authority_key({"repo_id": project_id}) \
        if project_id else None
    work_rows, live_attempts = [], []
    if intake_store is not None and authority_key:
        for row in intake_store.work_rows_for_authority(authority_key):
            work_rows.append({
                "work_key": row["work_key"],
                "issue": row["issue"],
                "generation": row["generation"],
                "state": row["state"],
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
                "detail": f"{len(live_attempts)} live attempt record(s)"
                          " — the upgrade fences the generation and "
                          "confirms exit before migrating (A3)",
            })
    elif intake_store is None:
        findings.append({
            "source": "intake-store",
            "detail": "no intake store supplied — the live-work fencing "
                      "inventory is unavailable; a plan binding work "
                      "rows refuses at apply without it",
        })
    if registration is not None:
        known = {w["work_key"] for w in work_rows}
        for key, work in sorted(
                (registration.get("work") or {}).items()):
            if work.get("state") == "active" and key not in known:
                work_rows.append({"work_key": key, "issue": None,
                                  "generation": work.get("generation"),
                                  "state": "active"})

    # -- reviewable entries: file transitions + effects + checkpoint ----------------
    entries = []
    for entry in files:
        item = {"id": entry["id"], "kind": entry["kind"],
                "action": entry["action"], "path": entry["path"],
                "sha256": entry["to_sha256"],
                "replaces_sha256": entry.get("review_sha256"),
                "reason": entry["reason"]}
        if entry["action"] == "replace":
            item["payload"] = entry["payload"]
            if manifest_bytes is not None:
                item["diff"] = plan_mod._unified_diff(
                    manifest_bytes.decode("utf-8", "replace"),
                    candidate_bytes.decode("utf-8", "replace"),
                    entry["path"])
        entries.append(item)
    if registration is not None and new_effective is not None:
        entries.append({
            "id": f"registration:{project_id}",
            "kind": "registration-migration",
            "action": "migrate",
            "detail": {
                "from_generation": installed["active_generation"],
                "from_config_digest": installed["config_digest"],
                "from_policy_digest": installed["policy_digest"],
                "to_config_digest": schema.effective_digest(
                    new_effective),
                "to_policy_digest": schema.policy_digest(new_effective),
                "mechanism": "a new authorized generation binds the new "
                             "digests — prior generation rows stay "
                             "byte-identical (CFG-C02)",
            },
            "reason": "migrate the registration under a fresh audited "
                      "generation — old policy never silently resumes",
        })
        entries.append({
            "id": _dependency_effect_id(new_effective),
            "kind": "dependency",
            "action": "repin",
            "detail": {
                "from": dict((installed_effective or {})
                             .get("skills", {}).get("approved", {})),
                "to": dict(new_effective["skills"]["approved"]),
                "auto_discover":
                    new_effective["skills"]["auto_discover"],
            },
            "reason": "dependency/skill provenance pins to the new "
                      "generation before dispatch resumes (A2)",
        })
    entries.append({
        "id": "checkpoint:migration",
        "kind": "checkpoint",
        "action": "record",
        "detail": {
            "covers": "every owned file's bytes plus the registration's "
                      "generation digests and effective map",
            "restore": "`repair` or `rollback` restores these bytes and "
                       "mints a rollback generation bound to them",
        },
        "reason": "the validated checkpoint every migration boundary "
                  "can restore (A3) and the documented rollback anchor "
                  "(A5)",
    })

    plan["installed"] = installed
    plan["target"] = {
        "manifest_sha256": candidate_sha,
        "schema_version": (new_effective or {}).get("factory_kit"),
        "effective_digest": schema.effective_digest(new_effective)
        if new_effective else None,
        "policy_digest": schema.policy_digest(new_effective)
        if new_effective else None,
        "supported_versions": list(
            supported_versions or
            [f"factory-kit/{VERSION}",
             f"manifest/{(new_effective or {}).get('factory_kit', '?')}"]),
        "kit_version": VERSION,
        "migration_hops": hops,
    }
    plan["files"] = files
    plan["entries"] = entries
    plan["compatibility"] = checks
    plan["migration_steps"] = [
        {"stage": "planned",
         "detail": "migration row committed to the ledger"},
        {"stage": "fenced",
         "detail": "readiness flips to upgrading/denied; bound work "
                   "parks; live work fences and confirms exit"},
        {"stage": "checkpointed",
         "detail": "owned bytes + registration digests/effective "
                   "committed as the restore anchor"},
        {"stage": "files-migrated",
         "detail": "owned files written to the reviewed bytes; the "
                   "ownership ledger re-checksums them"},
        {"stage": "registration-migrated",
         "detail": "a new authorized generation mints bound to the new "
                   "config/policy digests"},
        {"stage": "validated",
         "detail": "written bytes re-read and re-validated against the "
                   "reviewed digests"},
        {"stage": "provenance-pinned",
         "detail": "dependency/skill pin provenance recorded for the "
                   "new generation"},
        {"stage": "complete",
         "detail": "the fence closes into pending readiness — dispatch "
                   "resumes only after a fresh setup_checked passes"},
    ]
    plan["rollback"] = {
        "anchor": "checkpoint:migration",
        "instructions": [
            ("interrupted migration: `python3 -m factory_kit.setup "
             "repair --repo <r> --repaired-by <op>` — restores the "
             "validated checkpoint or parks with a specific conflict"),
            ("an accepted-but-unwanted upgrade: `python3 -m "
             "factory_kit.setup rollback --repo <r> --rolled-back-by "
             "<op>` — restores checkpointed bytes and mints a rollback "
             "generation bound to the previous digests"),
            ("neither command ever deletes user bytes — unrecognized "
             "on-disk content is a conflict, preserved byte-for-byte"),
        ],
    }
    plan["authority"] = {
        "authority_key": authority_key,
        "work": work_rows,
        "live_attempts": live_attempts,
        "note": "the upgrade fence commits before any mutation; "
                "unconfirmed worker exit parks the migration instead of "
                "migrating under live authority",
    }
    changed_targets = {e["path"] for e in files
                       if e["action"] != "keep"}
    preserved, truncated = plan_mod._walk(repo_root, changed_targets)
    plan["preserved"] = preserved
    plan["preserved_truncated"] = truncated
    plan["manifest"] = {
        "source": source_desc,
        "sha256": candidate_sha,
        "effective": new_effective,
    }
    plan["conflicts"] = conflicts
    plan["findings"] = findings
    plan["notes"] = notes
    plan["appliable"] = not conflicts and new_effective is not None
    plan["upgrade_digest"] = upgrade_digest(plan)
    plan["acceptance"] = None
    return plan


class UpgradeError(Exception):
    """The upgrade precondition failed — unaccepted/non-appliable plan,
    digest mismatch, changed checksum preconditions, or a fence the
    contract cannot commit. Never a partial migration."""


def accept_upgrade(plan, accepted_by, *, at=None):
    """Mark an upgrade plan operator-accepted; returns the accepted copy.

    Binds ``upgrade_digest`` exactly like :func:`plan_mod.accept` binds
    ``plan_digest`` — the bytes reviewed are the bytes migrated.
    """
    if not plan.get("appliable"):
        raise UpgradeError(
            "upgrade plan is not appliable — resolve its conflicts and "
            "re-inspect")
    accepted = dict(plan)
    accepted["acceptance"] = {
        "accepted_by": accepted_by,
        "accepted_at": at or _utcnow(),
        "upgrade_digest": plan["upgrade_digest"],
    }
    return accepted


class _Interrupt(Exception):
    """Interruption-injection seam: raised after committing the named
    stage, simulating a crash landing between durable boundaries (the
    A3 test harness)."""

    def __init__(self, stage):
        self.stage = stage
        super().__init__(stage)


def _validate(plan):
    acceptance = plan.get("acceptance") or {}
    if not acceptance:
        raise UpgradeError(
            "upgrade plan is not accepted — apply requires explicit "
            "operator acceptance of the reviewed upgrade_digest (A1)")
    if plan.get("conflicts") or not plan.get("appliable") or \
            not (plan.get("manifest") or {}).get("effective"):
        raise UpgradeError(
            "upgrade plan is not appliable — conflicts must be "
            "resolved and the repository re-inspected")
    if upgrade_digest(plan) != plan.get("upgrade_digest"):
        raise UpgradeError(
            "upgrade digest does not match its contents — the plan was "
            "modified after generation; re-inspect")
    if acceptance.get("upgrade_digest") != plan.get("upgrade_digest"):
        raise UpgradeError(
            "acceptance digest does not match this plan — the bytes "
            "under review changed; re-accept the current plan")
    return acceptance


def _check_preconditions(plan, repo_root):
    """Re-verify the reviewed checksums before anything mutates (A4).

    A changed checksum precondition blocks application — nothing has
    been fenced or written, so refusal is total, never partial.
    """
    drifted = []
    for entry in plan["files"]:
        target = os.path.join(repo_root, entry["path"])
        reviewed = entry.get("review_sha256")
        if reviewed is None:
            if os.path.exists(target):
                drifted.append({"path": entry["path"],
                                "state": "appeared"})
            continue
        if not os.path.isfile(target):
            drifted.append({"path": entry["path"], "state": "missing"})
            continue
        if sha256_file(target) != reviewed:
            drifted.append({"path": entry["path"],
                            "state": "modified-since-review"})
    if drifted:
        raise UpgradeError(
            "checksum preconditions changed since review — application "
            f"is blocked, nothing was mutated: {drifted}; re-inspect "
            "and accept a fresh plan")


def _settle_for_upgrade(intake_store, work_key, generation):
    """Fence + confirm a live work row, parking rather than canceling.

    Same ordering as removal's settle (the fence commits first so a
    racing effect loses), but the end state is ``parked`` — the work
    rebinds under the new generation once readiness passes, it is not
    terminated (A2).
    """
    fence = intake_store.fence_state(work_key)
    if not (fence and fence["fenced"]):
        intake_store.fence_work(work_key, "upgrade-in-progress",
                                generation)
    open_attempts = [
        r["attempt_id"] for r in
        intake_store.attempt_record_rows(work_key) if not r.get("ended")]
    open_attempts += [
        r["attempt_id"] for r in intake_store.attempt_rows()
        if r["work_key"] == work_key and r["state"] == "active"]
    if open_attempts:
        intake_store.resolve_fence(
            work_key, "quarantined",
            "worker/descendant exit unconfirmed during upgrade and no "
            "terminator is wired — process state uncertain")
        intake_store.set_work_state(work_key, "quarantined",
                                    reason="termination-uncertain")
        intake_store.emit_alert(
            "upgrade-quarantine", work_key, "high",
            detail=f"attempt(s) {sorted(set(open_attempts))} did not "
                   "confirm exit — upgrade blocked (A3)")
        return "quarantined"
    intake_store.resolve_fence(work_key, "confirmed",
                               "no live worker state recorded")
    work = intake_store.get_work(work_key)
    if work is not None and work["state"] not in TERMINAL_WORK_STATES:
        intake_store.set_work_state(work_key, "parked",
                                    reason="upgrade-in-progress")
    intake_store.release_lane("main", expected_work=work_key)
    return "confirmed"


def _fence_authority(plan, intake_store, terminator, *, deadline_s):
    """Commit the generation fence for every live work row (A2)."""
    settled, quarantined = [], []
    for wrow in plan["authority"]["work"]:
        work_key = wrow["work_key"]
        work = intake_store.get_work(work_key)
        if work is None:
            settled.append({"work_key": work_key,
                            "termination": "confirmed",
                            "detail": "no live work row"})
            continue
        if terminator is not None:
            fence = intake_store.fence_state(work_key)
            if not (fence and fence["fenced"]):
                intake_store.fence_work(work_key, "upgrade-in-progress",
                                        work["generation"])
            try:
                if hasattr(terminator, "terminate_work"):
                    out = terminator.terminate_work(
                        work_key, reason="upgrade-in-progress",
                        deadline_s=deadline_s)
                else:
                    out = terminator(work_key)
                outcome = out.get("termination") or out.get("outcome")
            except Exception as exc:  # noqa: BLE001 — terminator-defined
                outcome = "quarantined"
                intake_store.resolve_fence(
                    work_key, "quarantined",
                    f"terminator raised {type(exc).__name__} — exit "
                    "unconfirmed")
            if outcome == "confirmed" and \
                    work["state"] not in TERMINAL_WORK_STATES:
                intake_store.set_work_state(
                    work_key, "parked", reason="upgrade-in-progress")
                intake_store.release_lane("main",
                                          expected_work=work_key)
        elif work["state"] in TERMINAL_WORK_STATES:
            intake_store.fence_work(work_key, "upgrade-in-progress",
                                    work["generation"])
            intake_store.resolve_fence(
                work_key, "confirmed",
                "terminal work state — no live authority")
            outcome = "confirmed"
        else:
            outcome = _settle_for_upgrade(intake_store, work_key,
                                          work["generation"])
        record = {"work_key": work_key, "termination": outcome}
        (quarantined if outcome != "confirmed" else settled) \
            .append(record)
    return settled, quarantined


def _capture_checkpoint(repo_root, store, registration):
    """Snapshot the pre-migration validated state — the restore anchor
    repair and rollback share (A3/A5)."""
    files = {}
    for path in store.owned_files():
        target = os.path.join(repo_root, path)
        record = {"sha256": None, "size": None, "state": "missing"}
        if os.path.isfile(target):
            with open(target, "rb") as handle:
                payload = handle.read()
            record = {
                "sha256": sha256_bytes(payload),
                "size": len(payload),
                "state": "present",
                "payload_b64": base64.b64encode(payload).decode("ascii"),
            }
        files[path] = record
    reg_checkpoint = None
    if registration is not None:
        effective = None
        manifest = files.get(plan_mod.MANIFEST_PATH) or {}
        payload = manifest.get("payload_b64")
        if payload:
            try:
                effective = schema.load_manifest(
                    base64.b64decode(payload).decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 — recorded, not hid
                effective = {"_unrestorable": str(exc)}
        reg_checkpoint = {
            "repo_id": registration["repo_id"],
            "active_generation": registration.get("active_generation"),
            "generations": [
                {"generation": g.get("generation"),
                 "config_digest": g.get("config_digest"),
                 "policy_digest": g.get("policy_digest")}
                for g in registration.get("generations") or []],
            "effective": effective,
            "supported_versions": list(
                registration.get("supported_versions") or []),
            "readiness": dict(registration.get("readiness") or {}),
        }
    return {"created_at": _utcnow(), "files": files,
            "registration": reg_checkpoint}


def _file_targets(migration):
    """path -> byte checksums the migration itself produced.

    The checkpoint sha and the reviewed target sha are the only
    contents a restore may overwrite — anything else on disk is user
    work, preserved byte-for-byte (A4).
    """
    targets = {}
    for path, spec in (migration.get("files") or {}).items():
        allowed = {spec.get("from_sha256"), spec.get("to_sha256")}
        targets[path] = {s for s in allowed if s}
    checkpoint = migration.get("checkpoint") or {}
    for path, chk in (checkpoint.get("files") or {}).items():
        targets.setdefault(path, set())
        if chk.get("sha256"):
            targets[path].add(chk["sha256"])
    return targets


def _restore_conflicts(repo_root, migration, registration_store):
    """Dry pass over the checkpoint — restore is all-or-park (A3/A4).

    Returns ``(conflicts, needs_registration_restore)``: unrecognized
    on-disk bytes are user work — a specific conflict, preserved, never
    overwritten.
    """
    conflicts = []
    checkpoint = migration.get("checkpoint")
    if checkpoint is None:
        return ([{"source": "checkpoint",
                  "detail": "the migration committed no checkpoint — "
                            "no validated state exists to restore"}],
                False)
    targets = _file_targets(migration)
    for path, chk in (checkpoint.get("files") or {}).items():
        target = os.path.join(repo_root, path)
        if chk.get("state") == "missing":
            # Absent at checkpoint — restoring means the file must be
            # absent now too; unknown bytes that appeared are user work.
            if os.path.isfile(target) and \
                    sha256_file(target) not in targets.get(path, set()):
                conflicts.append({"source": "user-edit", "path": path,
                                  "detail": "file appeared after the "
                                            "checkpoint with "
                                            "unrecognized bytes — "
                                            "preserved"})
            continue
        if not os.path.isfile(target):
            continue  # absent — restore recreates the recorded bytes
        current = sha256_file(target)
        if current not in targets.get(path, set()):
            conflicts.append({"source": "user-edit", "path": path,
                              "detail": f"on-disk sha256 "
                                        f"{current[:12]}… matches "
                                        "neither the checkpoint nor "
                                        "the migration's recorded "
                                        "target — preserved "
                                        "byte-for-byte"})
    reg_cp = checkpoint.get("registration") or {}
    needs_reg = bool(reg_cp) and registration_store is not None
    if needs_reg:
        live = registration_store.get(reg_cp["repo_id"])
        if live is None or live.get("tombstone"):
            conflicts.append({"source": "registration",
                              "detail": "registration is absent or "
                                        "tombstoned — rollback cannot "
                                        "re-bind the previous "
                                        "generation"})
            needs_reg = False
        elif not isinstance(reg_cp.get("effective"), dict) or \
                "_unrestorable" in reg_cp["effective"]:
            conflicts.append({"source": "registration",
                              "detail": "the checkpoint's effective map "
                                        "is not restorable — the "
                                        "previous configuration cannot "
                                        "be re-validated"})
            needs_reg = False
    return conflicts, needs_reg


def _restore_checkpoint(repo_root, store, migration, registration_store,
                        actor):
    """Write back checkpointed bytes and re-bind the checkpointed
    registration digests under a fresh authorized generation."""
    checkpoint = migration["checkpoint"]
    restored, unchanged = [], []
    for path, chk in (checkpoint.get("files") or {}).items():
        target = _safe_target(repo_root, path, f"checkpoint:{path}")
        if chk.get("state") == "missing":
            if os.path.isfile(target):
                os.unlink(target)
                restored.append(path)
            continue
        payload = base64.b64decode(chk["payload_b64"])
        if os.path.isfile(target) and \
                sha256_file(target) == chk["sha256"]:
            unchanged.append(path)
            continue
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(payload)
        store.record_file(path, chk["sha256"], len(payload),
                          kind="configuration",
                          plan_digest=migration["upgrade_digest"])
        restored.append(path)

    reg_cp = checkpoint.get("registration") or {}
    gen_result = None
    if reg_cp and registration_store is not None:
        live = registration_store.get(reg_cp["repo_id"])
        if live is not None and not live.get("tombstone") and \
                isinstance(reg_cp.get("effective"), dict) and \
                "_unrestorable" not in reg_cp["effective"]:
            active = next(
                (g for g in live.get("generations") or []
                 if g.get("generation") ==
                 live.get("active_generation")), {})
            # Re-bind only when the active generation digests diverge —
            # ``authorize_generation`` mints a fresh audited generation
            # over the checkpointed digests (config AND policy — a
            # config-only drift still re-binds, which apply_policy_change
            # would skip as "unchanged").
            expected = schema.effective_digest(reg_cp["effective"])
            expected_policy = schema.policy_digest(reg_cp["effective"])
            if active.get("config_digest") != expected or \
                    active.get("policy_digest") != expected_policy:
                gen_result = registration_store.authorize_generation(
                    reg_cp["repo_id"], reg_cp["effective"],
                    authorized_by=actor, reason="checkpoint-restore")
    return {"files": {"restored": restored, "unchanged": unchanged},
            "registration": gen_result}


def _verify_restore(repo_root, migration, registration_store):
    """Read-back: restored bytes == checkpoint; the active generation
    binds the checkpoint digests — restore is proven, not claimed (A5).
    """
    checkpoint = migration["checkpoint"]
    violations = []
    for path, chk in (checkpoint.get("files") or {}).items():
        target = os.path.join(repo_root, path)
        expected = chk.get("sha256")
        if expected is None:
            if os.path.isfile(target):
                violations.append({"path": path,
                                   "state": "present-not-absent"})
            continue
        if not os.path.isfile(target):
            violations.append({"path": path, "state": "missing"})
        elif sha256_file(target) != expected:
            violations.append({"path": path, "state": "modified"})
    reg_cp = checkpoint.get("registration") or {}
    reg_ok = None
    if reg_cp and registration_store is not None:
        live = registration_store.get(reg_cp["repo_id"])
        if live is not None:
            active = next(
                (g for g in live.get("generations") or []
                 if g.get("generation") ==
                 live.get("active_generation")), {})
            expected_digest = None
            if isinstance(reg_cp.get("effective"), dict) and \
                    "_unrestorable" not in reg_cp["effective"]:
                expected_digest = schema.effective_digest(
                    reg_cp["effective"])
                expected_policy = schema.policy_digest(
                    reg_cp["effective"])
            else:
                expected_policy = None
            reg_ok = expected_digest is None or (
                active.get("config_digest") == expected_digest and
                active.get("policy_digest") == expected_policy)
    return {"file_violations": violations,
            "registration_restored": reg_ok}


def apply_upgrade(plan, repo_root, store, *, registration_store=None,
                  intake_store=None, terminator=None, effects=None,
                  deadline_s=30.0, stop_after=None) -> dict:
    """Apply an accepted upgrade plan across durable stage boundaries.

    ``terminator``/``deadline_s`` are the same seams removal uses for
    live-worker exit confirmation; ``effects`` the remote-effect sink.
    ``stop_after`` is the test-only interruption seam — it halts *after*
    committing the named stage, exactly where a crash lands (A3).

    Outcomes: ``upgraded`` · ``already-upgraded`` (idempotent) ·
    ``interrupted`` (stop_after seam) · ``quarantined`` (unconfirmed
    worker exit — the migration parks) · ``failed`` (post-migration
    validation failure — repair owns the open migration).
    """
    acceptance = _validate(plan)
    store = store if isinstance(store, SetupStore) else SetupStore(store)
    repo_root = os.path.abspath(str(repo_root))
    accepted_by = acceptance.get("accepted_by")
    plan_id = plan["upgrade_digest"]
    repo_id = plan.get("project_id")
    new_effective = plan["manifest"]["effective"]

    if plan["authority"]["work"] and intake_store is None:
        raise UpgradeError(
            "the plan binds live work rows but no intake store was "
            "supplied — the generation fence cannot commit; refusing "
            "upgrade (A3)")

    migration_id = migration_id_for(plan_id)
    existing = store.migration(migration_id)
    if existing is not None:
        if existing["stage"] == "complete":
            return {"outcome": "already-upgraded",
                    "migration_id": migration_id,
                    "upgrade_digest": plan_id}
        raise UpgradeError(
            f"migration {migration_id} already exists at stage "
            f"{existing['stage']!r} — an interrupted or concluded "
            "migration is repair/rollback scope, never re-applied")
    if store.open_migrations():
        raise UpgradeError(
            "open migrations present: "
            f"{sorted(store.open_migrations())} — repair or roll back "
            "the interrupted migration before applying another")

    _check_preconditions(plan, repo_root)

    store.record_migration(migration_id, {
        "upgrade_digest": plan_id,
        "repo_id": repo_id,
        "from_generation": (plan["installed"] or {})
        .get("active_generation"),
        "target": dict(plan.get("target") or {}),
        "files": {e["path"]: {"from_sha256": e.get("review_sha256"),
                              "to_sha256": e.get("to_sha256"),
                              "action": e["action"]}
                  for e in plan["files"]},
        "accepted_by": accepted_by,
    })

    def commit(stage, detail=None):
        store.advance_migration(migration_id, stage, detail=detail)
        if stop_after == stage:
            raise _Interrupt(stage)

    result = {"upgrade_digest": plan_id, "migration_id": migration_id,
              "repo_id": repo_id, "stages": []}
    try:
        commit("planned")
        result["stages"].append("planned")

        # -- fenced: dispatch denied + bound work parked before mutation ------
        fence_result = {"parked_work": []}
        if registration_store is not None and repo_id:
            fence_result = registration_store.begin_upgrade(
                repo_id, upgraded_by=accepted_by)
        settled, quarantined = [], []
        if intake_store is not None and plan["authority"]["work"]:
            settled, quarantined = _fence_authority(
                plan, intake_store, terminator, deadline_s=deadline_s)
        commit("fenced",
               detail=f"parked={fence_result.get('parked_work')}; "
                      f"settled={len(settled)}; "
                      f"quarantined={len(quarantined)}")
        result["stages"].append("fenced")
        result["fence"] = {"registration": fence_result,
                           "settled": settled,
                           "quarantined": quarantined}
        if quarantined:
            if registration_store is not None and repo_id:
                registration_store.finish_upgrade(repo_id,
                                                  outcome="parked")
            store.update_migration(migration_id, conflict={
                "source": "fence",
                "detail": "unconfirmed worker exit — the migration "
                          "parks rather than migrating under live "
                          "authority (A3)"})
            store.advance_migration(migration_id, "parked")
            store.record_event("setup_upgrade_parked", {
                "migration_id": migration_id, "reason": "quarantined",
                "work_keys": [q["work_key"] for q in quarantined]})
            result["outcome"] = "quarantined"
            result["quarantine"] = {
                "work_keys": [q["work_key"] for q in quarantined],
                "recovery": "resolve the quarantined fence(s) "
                            "(resolve_fence 'resolved'), then `repair` "
                            "restores the checkpoint or a fresh "
                            "upgrade re-inspects",
            }
            return result

        # -- checkpointed: the restore anchor commits before bytes change -----
        registration = registration_store.get(repo_id) \
            if (registration_store is not None and repo_id) else None
        checkpoint = _capture_checkpoint(repo_root, store, registration)
        store.update_migration(migration_id, checkpoint=checkpoint)
        commit("checkpointed",
               detail=f"{len(checkpoint['files'])} owned file(s) "
                      "snapshotted")
        result["stages"].append("checkpointed")

        # -- files-migrated: write the reviewed bytes; ledger re-checksums ----
        written, adopted = [], []
        for entry in plan["files"]:
            path = entry["path"]
            if entry["action"] == "keep" and \
                    entry.get("resolution") == "adopt":
                # Operator-adopted edit: the reviewed bytes stay on
                # disk; the ledger re-records them as the owned
                # baseline so future runs see an unchanged file.
                target = _safe_target(repo_root, path, entry["id"])
                if not os.path.isfile(target) or \
                        sha256_file(target) != \
                        entry.get("review_sha256"):
                    store.update_migration(migration_id, conflict={
                        "source": "checksum-drift", "path": path,
                        "detail": "adopted bytes changed "
                                  "mid-migration — halting; repair "
                                  "restores the checkpoint"})
                    store.advance_migration(migration_id, "failed")
                    result["outcome"] = "failed"
                    result["repair_hint"] = (
                        "python3 -m factory_kit.setup repair --repo "
                        f"{repo_root} --repaired-by <operator>")
                    return result
                store.record_file(path, entry["review_sha256"],
                                  os.path.getsize(target),
                                  kind=entry["kind"],
                                  plan_digest=plan_id)
                adopted.append(path)
                continue
            if entry["action"] != "replace":
                continue
            target = _safe_target(repo_root, path, entry["id"])
            if os.path.isfile(target) and \
                    sha256_file(target) != entry.get("review_sha256"):
                store.update_migration(migration_id, conflict={
                    "source": "checksum-drift", "path": path,
                    "detail": "bytes changed mid-migration — halting; "
                              "repair restores the checkpoint"})
                store.advance_migration(migration_id, "failed")
                result["outcome"] = "failed"
                result["repair_hint"] = (
                    "python3 -m factory_kit.setup repair --repo "
                    f"{repo_root} --repaired-by <operator>")
                return result
            data_bytes = entry["payload"].encode("utf-8")
            if sha256_bytes(data_bytes) != entry["to_sha256"]:
                raise UpgradeError(
                    f"entry {entry['id']}: payload bytes diverge from "
                    "the reviewed sha256 — the plan cannot carry what "
                    "it did not review; re-inspect")
            with open(target, "wb") as handle:
                handle.write(data_bytes)
            store.record_file(path, entry["to_sha256"], len(data_bytes),
                              kind=entry["kind"], plan_digest=plan_id)
            written.append(path)
        commit("files-migrated",
               detail=f"written={written}; adopted={adopted}")
        result["stages"].append("files-migrated")
        result["files_written"] = written
        result["files_adopted"] = adopted

        # -- registration-migrated: the new authorized generation -------------
        gen = None
        if registration_store is not None and repo_id:
            # ``authorize_generation`` — not apply_policy_change — because
            # operator acceptance IS the authorization, and the new
            # generation must bind *both* digests even when only the
            # configuration (not the policy surface) moved.
            gen = registration_store.authorize_generation(
                repo_id, new_effective, authorized_by=accepted_by,
                reason="reviewed-upgrade")
        commit("registration-migrated",
               detail=f"generation outcome="
                      f"{gen and gen['outcome']}")
        result["stages"].append("registration-migrated")
        result["registration"] = gen

        # -- validated: the new complete configuration re-verified ------------
        validation_errors = []
        for entry in plan["files"]:
            if entry["action"] != "replace":
                continue
            target = os.path.join(repo_root, entry["path"])
            if not os.path.isfile(target) or \
                    sha256_file(target) != entry["to_sha256"]:
                validation_errors.append(
                    f"{entry['path']} does not carry the reviewed "
                    "bytes")
        manifest_target = os.path.join(repo_root,
                                       plan_mod.MANIFEST_PATH)
        if not validation_errors and os.path.isfile(manifest_target):
            try:
                written_effective = schema.load_manifest_file(
                    manifest_target)
                if schema.effective_digest(written_effective) != \
                        schema.effective_digest(new_effective):
                    validation_errors.append(
                        "written manifest validates but digests "
                        "differ from the reviewed effective map")
            except Exception as exc:  # noqa: BLE001
                validation_errors.append(
                    f"written manifest failed validation: {exc}")
        if not validation_errors and \
                registration_store is not None and repo_id:
            live = registration_store.get(repo_id)
            active = next(
                (g for g in (live or {}).get("generations") or []
                 if g.get("generation") ==
                 live.get("active_generation")), {})
            if active.get("config_digest") != \
                    schema.effective_digest(new_effective):
                validation_errors.append(
                    "the active generation does not bind the reviewed "
                    "config digest")
        # Preservation proof (A2/A4): unrelated bytes re-verified after
        # the writes — violations are reported, never hidden.
        preserved_violations = []
        for rec in plan.get("preserved", []):
            if rec.get("sha256") is None:
                continue
            ptarget = os.path.join(repo_root, rec["path"])
            if not os.path.isfile(ptarget):
                preserved_violations.append(
                    {"path": rec["path"], "state": "missing"})
            elif sha256_file(ptarget) != rec["sha256"]:
                preserved_violations.append(
                    {"path": rec["path"], "state": "modified"})
        result["preserved_violations"] = preserved_violations
        if validation_errors:
            store.update_migration(migration_id, conflict={
                "source": "validation",
                "detail": "; ".join(validation_errors)})
            store.advance_migration(migration_id, "failed")
            store.record_event("setup_upgrade_failed", {
                "migration_id": migration_id,
                "errors": validation_errors})
            result["outcome"] = "failed"
            result["validation_errors"] = validation_errors
            result["repair_hint"] = (
                "python3 -m factory_kit.setup repair --repo "
                f"{repo_root} --repaired-by <operator>")
            return result
        commit("validated")
        result["stages"].append("validated")

        # -- provenance-pinned: dependency/skill set for the new generation ----
        sink = effects or RecordingEffectSink(store)
        provenance = []
        for entry in plan["entries"]:
            if entry["kind"] in ("dependency",):
                out = sink.apply(entry)
                provenance.append({"id": entry["id"],
                                   "status": out["status"]})
        commit("provenance-pinned",
               detail=f"{len(provenance)} provenance record(s)")
        result["stages"].append("provenance-pinned")
        result["provenance"] = provenance

        # -- complete: the fence closes into pending readiness ------------------
        closed = None
        if registration_store is not None and repo_id:
            closed = registration_store.finish_upgrade(
                repo_id, outcome="upgraded",
                supported_versions=plan["target"]["supported_versions"])
        commit("complete")
        result["stages"].append("complete")
        result["readiness"] = closed
        store.record_event("setup_upgraded", {
            "migration_id": migration_id,
            "repo_id": repo_id,
            "upgrade_digest": plan_id,
            "from_generation": (plan["installed"] or {})
            .get("active_generation"),
            "to_generation": (gen or {}).get("generation"),
            "written": written,
            "preserved_violations": preserved_violations,
            "upgraded_by": accepted_by,
        })
        result["outcome"] = "upgraded"
        return result
    except _Interrupt as exc:
        result["outcome"] = "interrupted"
        result["interrupted_at"] = exc.stage
        result["repair_hint"] = (
            "python3 -m factory_kit.setup repair --repo "
            f"{repo_root} --repaired-by <operator>")
        return result


def _pick_migration(store, migration_id):
    """The migration a repair/rollback acts on: the named one, else the
    most recently updated migration carrying a checkpoint."""
    if migration_id is not None:
        migration = store.migration(migration_id)
        if migration is None:
            raise UpgradeError(
                f"no migration {migration_id!r} in the setup ledger")
        return migration
    candidates = [m for m in store.migrations().values()
                  if m.get("checkpoint")]
    candidates.sort(key=lambda m: m.get("updated_at") or "")
    if not candidates:
        raise UpgradeError(
            "no migration with a checkpoint exists — nothing recorded "
            "a restorable pre-upgrade state")
    # The most recent checkpointed migration — a rolled-back row yields
    # the idempotent already-rolled-back answer from the caller.
    return candidates[-1]


def rollback_upgrade(repo_root, store, registration_store, *,
                     migration_id=None, rolled_back_by, now=None) -> dict:
    """Restore the previous validated registration/configuration (A5).

    The documented rollback: every checkpointed owned file is rewritten
    to its recorded bytes and the registration mints a new authorized
    generation bound to the *previous* digests — prior history stays
    byte-identical. Precomputed conflicts park the migration instead of
    partially restoring: unrecognized on-disk bytes are user work and
    are preserved, never overwritten. ``restored`` /
    ``already-rolled-back`` / ``parked``.
    """
    store = store if isinstance(store, SetupStore) else SetupStore(store)
    repo_root = os.path.abspath(str(repo_root))
    migration = _pick_migration(store, migration_id)
    migration_id = migration["migration_id"]
    if migration["stage"] == "rolled-back":
        return {"outcome": "already-rolled-back",
                "migration_id": migration_id}
    repo_id = migration.get("repo_id")

    conflicts, _needs_reg = _restore_conflicts(
        repo_root, migration, registration_store)
    if conflicts:
        store.update_migration(migration_id, conflict={
            "source": "rollback", "detail": conflicts})
        store.advance_migration(migration_id, "parked")
        if registration_store is not None and repo_id:
            try:
                registration_store.finish_upgrade(repo_id,
                                                  outcome="parked")
            except RegistrationError:
                pass
        store.record_event("setup_rollback", {
            "migration_id": migration_id, "repo_id": repo_id,
            "outcome": "parked",
            "conflicts": [c.get("path") for c in conflicts
                          if c.get("path")],
            "rolled_back_by": rolled_back_by})
        return {"outcome": "parked", "migration_id": migration_id,
                "conflicts": conflicts}

    restored = _restore_checkpoint(repo_root, store, migration,
                                   registration_store, rolled_back_by)
    verification = _verify_restore(repo_root, migration,
                                   registration_store)
    violations = (verification or {}).get("file_violations") or []
    if violations or verification.get("registration_restored") is False:
        # Same all-or-park contract repair enforces: a restore that
        # cannot prove the checkpoint bytes seals nothing — the
        # migration parks with the failed read-back as its conflict.
        store.update_migration(migration_id, conflict={
            "source": "verification",
            "detail": f"{violations}; registration_restored="
                      f"{verification.get('registration_restored')}"})
        store.advance_migration(migration_id, "parked")
        if registration_store is not None and repo_id:
            try:
                registration_store.finish_upgrade(repo_id,
                                                  outcome="parked")
            except RegistrationError:
                pass
        store.record_event("setup_rollback", {
            "migration_id": migration_id, "repo_id": repo_id,
            "outcome": "parked",
            "conflicts": [c.get("path") for c in violations
                          if c.get("path")],
            "rolled_back_by": rolled_back_by})
        return {"outcome": "parked", "migration_id": migration_id,
                "conflicts": violations,
                "verification": verification}
    if registration_store is not None and repo_id:
        registration_store.finish_upgrade(
            repo_id, outcome="rolled-back",
            supported_versions=(
                (migration.get("checkpoint") or {})
                .get("registration", {})
                .get("supported_versions")))
    store.advance_migration(migration_id, "rolled-back",
                            detail="checkpoint restored and verified")
    store.record_event("setup_rollback", {
        "migration_id": migration_id, "repo_id": repo_id,
        "outcome": "restored",
        "restored_files": restored["files"]["restored"],
        "rolled_back_by": rolled_back_by})
    return {
        "outcome": "restored",
        "migration_id": migration_id,
        "repo_id": repo_id,
        "restored": restored,
        "verification": verification,
        "note": "dispatch stays denied until substantive readiness "
                "passes on the restored configuration",
    }
