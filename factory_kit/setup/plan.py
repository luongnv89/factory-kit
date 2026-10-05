#!/usr/bin/env python3
"""Reviewable setup plans — inspect a repository, propose additive changes.

PRD §3.2 F01 (A1, A4, A5), §4.1 install flow, §6.1 integration ownership;
issue #7 / Task 2.2.

:func:`inspect` is strictly read-only: it enumerates the repository,
checksums every file it will later prove preserved (A2), maps existing
CI/IDD conventions (A4) and emits a *plan* — the reviewable inventory of
factory-owned additions and explicit integration edits. Nothing local or
remote is mutated before an operator accepts the plan
(:func:`accept`), and :func:`accept` itself only marks the reviewed bytes:
the acceptance record binds ``plan_digest`` so apply can verify the plan
being executed is the plan that was reviewed.

Entry kinds the plan can propose — and only these:

- ``configuration`` — the ``.factory-kit.yml`` manifest (repo-reviewed
  code); full proposed bytes and sha256 are inside the plan so review sees
  the actual content, never a description of it.
- ``integration-edit`` — an explicit edit to an *existing* file. The recipe
  proposes none by default: target CI stays authoritative
  (``docs/spike/verification-contract.md`` §1) and ``.gitissue.yml`` is
  never written by the factory (``factory_kit.config.precedence``). The
  inventory still *maps* those conventions so review can see them.
- ``webhook`` — the intake webhook subscription effect.
- ``registration`` — the kit-owned registration row (``RegistrationStore``)
  created pending-readiness; it enables nothing by itself (A6).
- ``dependency`` — one entry pinning the manifest's ``skills.approved``
  revision set (never per-skill duplicates, and never a scheduler).

A plan installs **no independent backlog scheduler and no implicit
auto-merge loop** (A4): the entry vocabulary contains no such kind, so an
accepted plan is structurally incapable of adding one.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

from factory_kit.config import precedence, schema, yamlmini
from .ownership import sha256_bytes, sha256_file

__all__ = [
    "PlanError",
    "MANIFEST_PATH",
    "GITISSUE_PATH",
    "AUTOMATION_FILES",
    "MAX_PRESERVED",
    "accept",
    "canary_scan",
    "inspect",
    "plan_digest",
]

PLAN_VERSION = 1
MANIFEST_PATH = ".factory-kit.yml"
GITISSUE_PATH = ".gitissue.yml"
MAX_PRESERVED = 5000

#: Repository-side automation artifacts that would own merge/task lifecycle
#: alongside the factory — the same competing-owner list the readiness spike
#: used (``tools/probes/readiness.py``). Presence is a *finding* on the plan
#: and a ``conflicting-task-owner`` blocker at readiness (A4).
AUTOMATION_FILES = (
    ".mergify.yml",
    ".kodiak.toml",
    ".github/dependabot.yml",
    ".github/workflows/auto-merge.yml",
    ".github/workflows/automerge.yml",
)

#: Webhook events the intake subscription needs (F02 durable intake).
WEBHOOK_EVENTS = ("issues", "issue_comment", "pull_request", "label")

_IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 "dist", "build"}


class PlanError(Exception):
    """Inspection could not produce a reviewable plan, or the plan state
    requested is outside the contract (e.g. accepting a non-appliable
    plan)."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _canonical(node) -> str:
    return json.dumps(node, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def plan_digest(plan) -> str:
    """Digest over the reviewable surface — everything apply consumes.

    ``entries`` and ``inventory`` are the reviewed diff itself; ``manifest``
    carries the *effective* map the registration entry binds; ``preserved``
    is the checksum set apply re-verifies; ``conflicts``/``findings`` are
    part of what the operator accepted. Mutating any of those re-keys the
    plan, so the acceptance bound to this digest cannot silently cover new
    content or a weaker preservation proof."""
    surface = {k: plan[k] for k in
               ("inventory", "entries", "conflicts", "findings",
                "manifest", "preserved")
               if k in plan}
    return hashlib.sha256(_canonical(surface).encode()).hexdigest()


def canary_scan(text) -> list:
    """Secret-canary matches inside proposed or emitted bytes (A5).

    Reuses the manifest schema's canary table — plans, reports and applied
    files must carry references to secret storage, never literals.
    """
    matches = []
    for rx in schema.SECRET_CANARY_RES:
        if rx.search(text or ""):
            matches.append(rx.pattern)
    return matches


def _git_status(repo_root) -> dict:
    """Developer work present in the tree — the work the plan promises to
    preserve. Not a git repo is a finding, not a failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain=v1", "-z"],
            capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return {"git_available": False, "dirty": [], "untracked": []}
    if proc.returncode != 0:
        return {"git_available": False, "dirty": [], "untracked": []}
    dirty, untracked = [], []
    for record in proc.stdout.decode("utf-8", "replace").split("\0"):
        if not record:
            continue
        xy, path = record[:2], record[3:]
        entry = {"path": path, "status": xy.strip()}
        if xy == "??":
            untracked.append(entry)
        else:
            dirty.append(entry)
    return {"git_available": True, "dirty": dirty, "untracked": untracked}


def _walk(repo_root, planned_targets):
    """Checksum every existing file not targeted by a plan entry.

    These are the *preserved* bytes (A2): apply re-verifies each one after
    writing so "all unrelated bytes and developer edits preserved" is a
    measured claim, not a promise. ``.git`` and dependency directories are
    excluded — the kit never promises to preserve transient state.
    """
    preserved = []
    truncated = False
    repo_root = os.path.abspath(str(repo_root))
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = sorted(d for d in dirnames if d not in _IGNORED_DIRS)
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, repo_root)
            if rel in planned_targets:
                continue
            if len(preserved) >= MAX_PRESERVED:
                truncated = True
                break
            try:
                preserved.append({
                    "path": rel,
                    "sha256": sha256_file(full),
                    "size": os.path.getsize(full),
                })
            except OSError:
                # Unreadable/special files are recorded by name so the
                # review still shows they exist and will not be touched.
                preserved.append({"path": rel, "sha256": None,
                                  "size": None, "unreadable": True})
        if truncated:
            break
    return preserved, truncated


def _read_yaml_map(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return yamlmini.load(handle.read())
    except FileNotFoundError:
        return None
    except (yamlmini.ParseError, OSError) as exc:
        return {"_unparsable": str(exc)}


def _unified_diff(old_text, new_text, path) -> str:
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}"))


def _configuration_entry(repo_root, manifest_bytes, manifest_effective):
    target = os.path.join(str(repo_root), MANIFEST_PATH)
    existing = None
    if os.path.isfile(target) and not os.path.islink(target):
        with open(target, "rb") as handle:
            existing = handle.read()
    sha = sha256_bytes(manifest_bytes)
    entry = {
        "id": f"configuration:{MANIFEST_PATH}",
        "kind": "configuration",
        "path": MANIFEST_PATH,
        "sha256": sha,
        "bytes": len(manifest_bytes),
        "payload": manifest_bytes.decode("utf-8"),
        "reason": "factory configuration manifest — repo-reviewed code "
                  "(CFG-C01); the only factory-owned file setup writes",
    }
    if existing is None:
        entry["action"] = "create"
    elif existing == manifest_bytes:
        entry["action"] = "keep"
        entry["reason"] += " — identical bytes already present"
    else:
        entry["action"] = "replace"
        entry["replaces_sha256"] = sha256_bytes(existing)
        entry["diff"] = _unified_diff(
            existing.decode("utf-8", "replace"),
            manifest_bytes.decode("utf-8"), MANIFEST_PATH)
        entry["reason"] += (
            " — replaces an existing manifest; review the diff")
    return entry, existing is not None


def _webhook_entry(effective):
    return {
        "id": "webhook:intake-events",
        "kind": "webhook",
        "action": "subscribe",
        "detail": {
            "events": list(WEBHOOK_EVENTS),
            "mechanism": "hermes webhook subscribe --skills <pins>",
            "signing": "HMAC secret from manifest secrets.webhook_signing_key"
                       " — reference only, the literal never appears",
        },
        "reason": "one durable intake subscription (F02); delivery IDs are "
                  "persisted separately from logical work identity",
    }


def _registration_entry(effective):
    identity = effective["identity"]
    return {
        "id": f"registration:{identity['repo_id']}",
        "kind": "registration",
        "action": "register",
        "detail": {
            "repo_id": identity["repo_id"],
            "authority_key": schema.authority_key(identity),
            "owner": effective["registration"]["owner"],
            "initial_readiness": "pending — enabled only after substantive "
                                 "readiness passes (A6)",
        },
        "reason": "one kit-owned registration row; re-running returns the "
                  "same row, never a duplicate",
    }


def _dependency_entry(effective):
    return {
        "id": "dependency:approved-skill-pins",
        "kind": "dependency",
        "action": "pin",
        "detail": {
            "skills": dict(effective["skills"]["approved"]),
            "auto_discover": effective["skills"]["auto_discover"],
        },
        "reason": "one dependency entry carrying the manifest's approved "
                  "skill pin set — skill revisions stay immutable (CFG04)",
    }


def inspect(repo_root, manifest_source=None, *, now=None):
    """Inspect ``repo_root`` and produce a reviewable, additive plan.

    ``manifest_source`` is the operator-prepared ``.factory-kit.yml``
    candidate (path or file-like). When absent, the repository's own
    manifest is used if present; without either, the plan carries no
    configuration entry and is not appliable — registration and webhook
    effects have no validated contract to bind to.

    This function is strictly read-only: it must be safe to run on a
    developer's dirty tree, and the tests assert byte-for-byte that nothing
    changed (A1).
    """
    repo_root = os.path.abspath(str(repo_root))
    if not os.path.isdir(repo_root):
        raise PlanError(f"repository root {repo_root!r} is not a directory")

    plan = {
        "plan_version": PLAN_VERSION,
        "generated_at": now or _utcnow(),
        "repo_root": repo_root,
    }

    # -- manifest ----------------------------------------------------------
    manifest_bytes = None
    source_desc = None
    if manifest_source is not None:
        if hasattr(manifest_source, "read"):
            manifest_bytes = manifest_source.read()
            if isinstance(manifest_bytes, str):
                manifest_bytes = manifest_bytes.encode("utf-8")
            source_desc = getattr(manifest_source, "name", "<stream>")
        else:
            with open(str(manifest_source), "rb") as handle:
                manifest_bytes = handle.read()
            source_desc = str(manifest_source)
    elif os.path.isfile(os.path.join(repo_root, MANIFEST_PATH)):
        with open(os.path.join(repo_root, MANIFEST_PATH), "rb") as handle:
            manifest_bytes = handle.read()
        source_desc = MANIFEST_PATH

    notes, conflicts, findings = [], [], []
    effective = None
    manifest_problems = None
    if manifest_bytes is None:
        notes.append(
            "no .factory-kit.yml manifest supplied or present — the plan "
            "is reviewable but not appliable until the operator prepares "
            "one")
    else:
        try:
            effective = schema.load_manifest(
                manifest_bytes.decode("utf-8"))
        except UnicodeDecodeError:
            manifest_problems = [{"path": MANIFEST_PATH,
                                  "message": "manifest is not UTF-8 text"}]
        except schema.ConfigError as exc:
            manifest_problems = exc.problems
        if manifest_problems:
            conflicts.append({
                "source": "manifest-validation",
                "detail": "candidate .factory-kit.yml failed validation",
                "problems": manifest_problems,
            })

    # -- inventory ----------------------------------------------------------
    gitissue = _read_yaml_map(os.path.join(repo_root, GITISSUE_PATH))
    if gitissue is None:
        gitissue_groups = None
        mapping = {"note": "no .gitissue.yml present — nothing to map"}
    elif "_unparsable" in gitissue:
        gitissue_groups = None
        mapping = {"error": f".gitissue.yml unparsable: "
                            f"{gitissue['_unparsable']}"}
        findings.append({"source": "gitissue",
                         "detail": mapping["error"]})
    else:
        gitissue_groups = sorted(set(gitissue))
        if effective is not None:
            try:
                mapping = precedence.check(effective, gitissue)
            except precedence.PrecedenceError as exc:
                mapping = {"error": str(exc)}
                for conflict in exc.conflicts:
                    conflicts.append({
                        "source": "precedence",
                        "detail": conflict["message"],
                        "key": conflict["key"],
                        "owner": conflict["owner"],
                    })
        else:
            mapping = {"note": "ownership mapping needs a valid manifest"}

    ci_workflows = []
    workflows_dir = os.path.join(repo_root, ".github", "workflows")
    if os.path.isdir(workflows_dir):
        ci_workflows = sorted(
            f".github/workflows/{name}"
            for name in os.listdir(workflows_dir)
            if name.endswith((".yml", ".yaml")))
    if ci_workflows:
        findings.append({
            "source": "ci",
            "detail": f"{len(ci_workflows)} existing CI workflow(s) stay "
                      "authoritative — setup adds no workflow edits",
        })

    automation = [rel for rel in AUTOMATION_FILES
                  if os.path.isfile(os.path.join(repo_root, rel))]
    for rel in automation:
        findings.append({
            "source": "competing-automation",
            "detail": f"{rel} is a competing automation owner — readiness "
                      "names it conflicting-task-owner; the plan never "
                      "edits or disables it (that is an operator decision)",
        })

    developer_work = _git_status(repo_root)
    if not developer_work["git_available"]:
        notes.append("not a git repository — dirty-tree preservation is "
                     "still verified by preserved-file checksums")

    # -- entries --------------------------------------------------------------
    entries = []
    manifest_target = os.path.join(repo_root, MANIFEST_PATH)
    manifest_is_link = os.path.islink(manifest_target)
    existing_manifest = os.path.isfile(manifest_target)
    if manifest_is_link:
        conflicts.append({
            "source": "manifest-target",
            "detail": f"{MANIFEST_PATH} is a symlink — setup never "
                      "writes through links; remove it and re-inspect",
        })
    planned_targets = set()
    if effective is not None:
        entry, _ = _configuration_entry(
            repo_root, manifest_bytes, effective)
        entries.append(entry)
        if entry["action"] != "keep":
            planned_targets.add(MANIFEST_PATH)
        entries.append(_webhook_entry(effective))
        entries.append(_registration_entry(effective))
        entries.append(_dependency_entry(effective))

    preserved, truncated = _walk(repo_root, planned_targets)

    # -- secret hygiene over every proposed byte (A5) ---------------------------
    # Scan the *content* fields — the bytes that would be committed — never
    # the metadata envelope: checksums and pinned 40-hex revisions are
    # legitimate hex blobs, not secrets.
    canaries = []
    for entry in entries:
        for field in ("payload", "diff"):
            if entry.get(field):
                canaries.extend(canary_scan(str(entry[field])))
    if canaries:
        conflicts.append({
            "source": "secret-canary",
            "detail": "a proposed payload matches a secret canary — store "
                      "secrets in approved storage and reference them",
            "patterns": len(canaries),
        })

    plan["manifest"] = {
        "source": source_desc,
        "sha256": sha256_bytes(manifest_bytes) if manifest_bytes else None,
        "effective": effective,
    }
    plan["inventory"] = {
        "ci_workflows": ci_workflows,
        "gitissue_groups": gitissue_groups,
        "integration_mapping": mapping,
        "automation_files": automation,
        "existing_manifest": existing_manifest,
        "developer_work": developer_work,
    }
    plan["entries"] = entries
    plan["conflicts"] = conflicts
    plan["findings"] = findings
    plan["notes"] = notes
    plan["preserved"] = preserved
    plan["preserved_truncated"] = truncated
    plan["secret_hygiene"] = {"clean": not canaries,
                            "canaries": len(canaries)}
    plan["scheduler_entries"] = []  # A4 — none can ever exist, by kind
    plan["appliable"] = not conflicts and effective is not None
    plan["plan_digest"] = plan_digest(plan)
    plan["acceptance"] = None
    return plan


def accept(plan, accepted_by, *, at=None):
    """Mark a plan as operator-accepted; returns the accepted copy.

    The acceptance binds ``plan_digest``: apply verifies the digest on the
    plan it is given still equals the accepted one, so bytes reviewed are
    the bytes executed. Accepting a non-appliable plan is refused — a plan
    carrying conflicts (invalid manifest, precedence violation, secret
    canary) must be re-inspected after the operator fixes the cause.
    """
    if not plan.get("appliable"):
        raise PlanError(
            "plan is not appliable — resolve its conflicts and re-inspect")
    accepted = dict(plan)
    accepted["acceptance"] = {
        "accepted_by": accepted_by,
        "accepted_at": at or _utcnow(),
        "plan_digest": plan["plan_digest"],
    }
    return accepted
