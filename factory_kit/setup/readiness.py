#!/usr/bin/env python3
"""Substantive readiness — the gate registration can never bypass.

PRD §3.2 F01 (A3, A4, A5, A6), §7.1 ``setup_checked``; issue #7 /
Task 2.2.

Readiness is *substantive*: it evaluates real probes of the selected
support recipe — executable capability, the manifest's actual configured
models and their authentication, supported host/Hermes/runtime/skill
versions, cancellation capability, competing automation owners and the
merge-precondition protection — never executable presence alone. This is
the production evaluator the spike probe (``tools/probes/readiness.py``)
was evidence for; it keeps the probe's blocker vocabulary where the
conditions coincide and adds the manifest-aware checks (per-role models,
pinned skill revisions, verification-contract coverage) the spike could
not yet name because the configuration contract did not exist.

The report feeds two consumers (A5/A6):

- :class:`~factory_kit.config.registration.RegistrationStore` — its
  ``readiness`` field is this report; ``setup_enabled`` inside it is true
  only when every prerequisite passed against an accepted plan's pending
  registration.
- The :class:`~factory_kit.setup.ownership.SetupStore` event trail — each
  run appends ``setup_checked`` with project ID, version set, duration and
  per-prerequisite outcomes, so failed setup stays diagnosable.

The evaluator is pure: ``readings`` arrive from the live collectors below
or from a recorded fixture — the same shape the spike probe produced —
which is how adverse prerequisites (missing executable, unreachable model,
failed auth, competing owner, unsupported versions) are tested
deterministically.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from factory_kit import SUPPORTED_SCHEMA_VERSIONS, VERSION
from factory_kit.config import schema
from .plan import AUTOMATION_FILES, canary_scan

__all__ = [
    "ReadinessError",
    "HERMES_MIN_VERSION",
    "KANBAN_OPS",
    "SUPPORTED_HOST_OS",
    "BLOCKER_REMEDIATION",
    "collect_readings",
    "evaluate",
    "run_setup_checked",
]

#: The tested recipe pins the Hermes version the spike exercised
#: (``docs/decisions/tested-recipe-selection.md``).
HERMES_MIN_VERSION = (0, 21, 5)

#: Kanban verbs the lifecycle requires, by §6.5 operation — same mapping
#: the spike probe used. ``cancel`` is reported separately so an
#: inadequate cancellation capability is its own named blocker (A3).
KANBAN_OPS = {
    "start": ("create", "claim", "dispatch"),
    "liveness": ("heartbeat", "runs", "tail"),
    "result": ("complete", "request-review", "block", "attach"),
    "cancel": ("block", "reclaim"),
}

SUPPORTED_HOST_OS = ("Darwin", "Linux")

BLOCKER_REMEDIATION = {
    "missing-executable": "install the missing executable or fix PATH",
    "unsupported-version":
        f"upgrade Hermes to >= {'.'.join(map(str, HERMES_MIN_VERSION))} "
        "or re-pin the recipe",
    "unsupported-runtime": "set runtime.name to a supported runtime",
    "unsupported-host": "run setup on a supported host OS "
                        f"({'/'.join(SUPPORTED_HOST_OS)})",
    "unsupported-interface": "required Kanban/webhook verbs are absent — "
                             "check the Hermes install; do not build a "
                             "second scheduler",
    "cancellation-inadequate": "Hermes lacks the cancellation verbs "
                               "(block/reclaim) — upgrade or the recipe "
                               "cannot fence work",
    "model-auth-failed": "authenticate the configured provider "
                         "(`hermes auth <provider>`)",
    "model-unavailable": "restore the model endpoint or choose an "
                         "available configured model per role",
    "skill-version-mismatch": "install the approved skill pins the "
                              "manifest declares (skills.approved)",
    "conflicting-task-owner": "disable repo-side automation or assign "
                              "factory-kit exclusive merge ownership",
    "base-unprotected": "enable required status checks on an up-to-date "
                        "base with enforce_admins before the merge "
                        "endpoint",
    "verification-contract-unmet": "repository protection must enforce "
                                   "the manifest's required check "
                                   "contexts",
    "missing-transport": "configure the Telegram adapter "
                         "(plugins/platforms/telegram) and gateway "
                         "supervision",
}


class ReadinessError(Exception):
    """Readiness could not produce a verdict at all — distinct from a
    ``not-ready`` verdict, which is a successful evaluation."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _b(code, detail):
    return {"code": code, "detail": detail,
            "remediation": BLOCKER_REMEDIATION.get(code, "")}


def _outcome(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


def _semver(value):
    if isinstance(value, (list, tuple)):
        try:
            return tuple(int(x) for x in value[:3])
        except (TypeError, ValueError):
            return None
    match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", str(value or ""))
    return tuple(int(x) for x in match.groups()) if match else None


def _role_models(effective):
    """provider/model pairs the manifest requires per role."""
    out = {}
    for role, spec in (effective.get("runtime", {})
                     .get("roles", {})).items():
        model = spec.get("model", "")
        provider, _, name = model.partition("/")
        out[role] = {"model": model, "provider": provider or None,
                     "name": name or model}
    return out


def _skill_ok(pin, installed):
    """Does an installed version satisfy an approved pin (semver or SHA)?"""
    if installed is None:
        return False
    pin = str(pin).strip()
    installed = str(installed).strip()
    if re.fullmatch(r"[0-9a-f]{40}", pin):
        return pin in installed or installed in pin
    return pin.lstrip("v") == installed.lstrip("v")


# ---------------------------------------------------------------------------
# Evaluation — the substantive verdict (A3, A4, A5).
# ---------------------------------------------------------------------------

def evaluate(readings, effective, *, project_id=None, checked_at=None,
             duration_ms=None):
    """Evaluate recorded ``readings`` against the manifest ``effective``.

    Returns the readiness report. ``dispatch`` is ``denied`` whenever a
    blocker exists and ``dispatched`` is always ``False`` — this package
    has no dispatch path, so "a blocker dispatches nothing" is structural.
    """
    blockers, outcomes = [], []
    hermes = readings.get("hermes") or {}
    model = readings.get("model") or {}
    rules = readings.get("repo_rules") or {}
    idd = readings.get("idd") or {}
    host = readings.get("host") or {}
    transport = readings.get("transport") or {}
    credentials = readings.get("credentials") or {}

    # -- executable capability ------------------------------------------------
    hermes_version = _semver(hermes.get("version"))
    if hermes.get("error") or hermes_version is None:
        blockers.append(_b(
            "missing-executable",
            f"hermes binary unusable: "
            f"{hermes.get('error') or 'no version'}"))
        outcomes.append(_outcome("executable", "blocked",
                                 "hermes binary missing or unusable"))
    else:
        outcomes.append(_outcome("executable", "pass",
                                 f"hermes {'.'.join(map(str, hermes_version))}"))

    # -- supported Hermes / runtime / host / schema versions ---------------------
    if hermes_version is not None and hermes_version < HERMES_MIN_VERSION:
        blockers.append(_b(
            "unsupported-version",
            f"hermes {'.'.join(map(str, hermes_version))} < pinned "
            f"{'.'.join(map(str, HERMES_MIN_VERSION))}"))
        outcomes.append(_outcome("hermes-version", "blocked",
                                 "below recipe pin"))
    else:
        outcomes.append(_outcome(
            "hermes-version", "pass",
            f">= {'.'.join(map(str, HERMES_MIN_VERSION))}"))

    runtime = (effective.get("runtime") or {}).get("name")
    if runtime not in schema.SUPPORTED_RUNTIMES:
        blockers.append(_b("unsupported-runtime",
                           f"runtime {runtime!r} not supported"))
        outcomes.append(_outcome("runtime", "blocked", str(runtime)))
    else:
        outcomes.append(_outcome("runtime", "pass", runtime))

    host_os = host.get("os")
    if not host_os or host_os not in SUPPORTED_HOST_OS:
        blockers.append(_b(
            "unsupported-host",
            f"host OS {host_os!r}" if host_os else
            "host OS could not be determined — support is unverified"))
        outcomes.append(_outcome("host", "blocked", host_os or "unknown"))
    else:
        outcomes.append(_outcome(
            "host", "pass",
            f"{host_os}/{host.get('machine', '?')} "
            f"python {host.get('python', '?')}"))

    schema_version = effective.get("factory_kit")
    outcomes.append(_outcome(
        "schema-version",
        "pass" if schema_version in SUPPORTED_SCHEMA_VERSIONS
        else "blocked",
        f"factory_kit schema {schema_version}; kit {VERSION}"))
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        blockers.append(_b("unsupported-version",
                           f"manifest schema {schema_version} not in "
                           f"{list(SUPPORTED_SCHEMA_VERSIONS)}"))

    # -- kanban interface + cancellation -----------------------------------------
    verbs = set(hermes.get("kanban_verbs") or [])
    ops = {}
    for op, required in KANBAN_OPS.items():
        missing = [v for v in required if v not in verbs]
        ops[op] = {"required_verbs": list(required), "missing": missing,
                   "supported": not missing}
    non_cancel_missing = {
        op: o["missing"] for op, o in ops.items()
        if op != "cancel" and o["missing"]}
    if non_cancel_missing or not hermes.get("webhook_subscribe"):
        detail = "; ".join(f"{op}→{m}" for op, m in
                           non_cancel_missing.items())
        if not hermes.get("webhook_subscribe"):
            detail += "; webhook subscribe unavailable"
        blockers.append(_b("unsupported-interface",
                           f"kanban interface gaps: {detail.lstrip('; ')}"))
        outcomes.append(_outcome("kanban-interface", "blocked",
                                 detail.lstrip("; ")))
    else:
        outcomes.append(_outcome("kanban-interface", "pass",
                                 "start/liveness/result verbs + webhook"))

    if ops["cancel"]["missing"]:
        blockers.append(_b(
            "cancellation-inadequate",
            f"cancellation verbs missing: {ops['cancel']['missing']} — "
            "work could not be fenced"))
        outcomes.append(_outcome("cancellation", "blocked",
                                 "cannot fence/stop work"))
    else:
        outcomes.append(_outcome("cancellation", "pass",
                                 "block/reclaim available"))

    # -- configured model + authentication per role --------------------------------
    auth_providers = set(model.get("auth_providers") or [])
    configured_provider = model.get("provider")
    auth_status = str(model.get("auth_status", "")).lower()
    if configured_provider and "logged in" in auth_status:
        auth_providers.add(configured_provider)
    endpoint = model.get("endpoint") or {}
    role_models = _role_models(effective)
    model_blocked = False
    model_detail = []
    for role, spec in role_models.items():
        provider, name = spec["provider"], spec["name"]
        if provider and provider not in auth_providers:
            blockers.append(_b(
                "model-auth-failed",
                f"role {role}: provider {provider} not authenticated"))
            model_blocked = True
            model_detail.append(f"{role}: {provider} auth failed")
            continue
        if provider == configured_provider and \
                endpoint.get("reachable") is False:
            blockers.append(_b(
                "model-unavailable",
                f"role {role}: endpoint {model.get('base_url')} "
                f"unreachable ({endpoint.get('error')})"))
            model_blocked = True
            model_detail.append(f"{role}: endpoint unreachable")
            continue
        if endpoint.get("reachable") and endpoint.get("models") is not None \
                and provider == configured_provider \
                and name not in endpoint["models"]:
            blockers.append(_b(
                "model-unavailable",
                f"role {role}: model {name!r} not listed by endpoint"))
            model_blocked = True
            model_detail.append(f"{role}: {name} not listed")
            continue
        model_detail.append(f"{role}: {spec['model']} ok")
    if model.get("error") and not model_blocked:
        blockers.append(_b("model-unavailable", model["error"]))
        model_blocked = True
        model_detail.append(str(model["error"]))
    outcomes.append(_outcome(
        "model", "blocked" if model_blocked else "pass",
        "; ".join(model_detail) or "no roles configured"))

    # -- pinned skill revisions ------------------------------------------------------
    installed_skills = idd.get("skills") or {}
    approved = effective.get("skills", {}).get("approved", {})
    skill_gaps = []
    for skill, pin in approved.items():
        installed = installed_skills.get(skill)
        if installed is None:
            skill_gaps.append(f"{skill}: not installed")
        elif not _skill_ok(pin, installed):
            skill_gaps.append(
                f"{skill}: pin {pin} != installed {installed}")
    if skill_gaps:
        blockers.append(_b("skill-version-mismatch",
                           "; ".join(skill_gaps)))
        outcomes.append(_outcome("skill-versions", "blocked",
                                 "; ".join(skill_gaps)))
    else:
        outcomes.append(_outcome(
            "skill-versions", "pass",
            f"{len(approved)} approved pin(s) satisfied"))

    # -- competing automation owner (A4) ------------------------------------------------
    conflicts = []
    if rules.get("allow_auto_merge"):
        conflicts.append("repo allow_auto_merge=true")
    for path in rules.get("automation_files") or []:
        conflicts.append(f"automation config {path}")
    nwo = (readings.get("repository") or {}).get("name")
    for task in hermes.get("kanban_tasks") or []:
        if nwo and nwo.split("/")[-1] in json.dumps(task):
            conflicts.append(
                f"existing kanban task {task.get('id', '?')} "
                f"references {nwo}")
    if conflicts:
        blockers.append(_b("conflicting-task-owner",
                           "; ".join(conflicts)))
        outcomes.append(_outcome("automation-owner", "blocked",
                                 "; ".join(conflicts)))
    else:
        outcomes.append(_outcome("automation-owner", "pass",
                                 "factory is the sole automation owner"))

    # -- base protection + verification contract ------------------------------------------
    # An unverified protection surface is a blocker, not a pass: the
    # merge precondition is a named prerequisite (A3) and readiness
    # certifies only what it could check.
    if not rules or rules.get("skipped"):
        blockers.append(_b(
            "base-unprotected",
            f"branch protection could not be verified — "
            f"{(rules or {}).get('skipped', 'no repo_rules reading')}"))
        outcomes.append(_outcome("base-protection", "blocked",
                                 "protection unverified"))
    else:
        if not rules.get("protected"):
            blockers.append(_b(
                "base-unprotected",
                f"{nwo or '<repo>'}:{rules.get('default_branch')} has no "
                "branch protection"))
            outcomes.append(_outcome("base-protection", "blocked",
                                     "unprotected base"))
        elif not rules.get("required_checks"):
            blockers.append(_b(
                "base-unprotected",
                "branch protection names no required status checks"))
            outcomes.append(_outcome("base-protection", "blocked",
                                     "no required checks"))
        elif rules.get("strict_up_to_date") is not True:
            blockers.append(_b(
                "base-unprotected",
                "base does not require an up-to-date head (strict=false)"))
            outcomes.append(_outcome("base-protection", "blocked",
                                     "strict=false"))
        elif rules.get("enforce_admins") is False:
            blockers.append(_b(
                "base-unprotected",
                "enforce_admins disabled — credentials may bypass "
                "required checks"))
            outcomes.append(_outcome("base-protection", "blocked",
                                     "enforce_admins off"))
        else:
            outcomes.append(_outcome("base-protection", "pass",
                                     "checks enforced on up-to-date base"))

        required = set((effective.get("verification") or {})
                       .get("required_checks", {}).get("contexts") or [])
        observed = set(rules.get("required_checks") or [])
        if required and not required.issubset(observed):
            missing = sorted(required - observed)
            blockers.append(_b(
                "verification-contract-unmet",
                f"manifest requires check contexts absent from "
                f"protection: {missing}"))
            outcomes.append(_outcome("verification-contract", "blocked",
                                     f"missing contexts {missing}"))
        else:
            outcomes.append(_outcome(
                "verification-contract", "pass",
                f"{len(required)} manifest context(s) covered"))

    # -- transport -------------------------------------------------------------------------
    if not transport.get("telegram_adapter"):
        blockers.append(_b("missing-transport",
                           "telegram platform adapter not installed"))
        outcomes.append(_outcome("transport", "blocked",
                                 "no telegram adapter"))
    elif not (hermes.get("gateway") or {}).get("supervised"):
        blockers.append(_b("missing-transport",
                           "gateway is not supervised/running"))
        outcomes.append(_outcome("transport", "blocked",
                                 "gateway unsupervised"))
    else:
        outcomes.append(_outcome("transport", "pass",
                                 "telegram adapter + supervised gateway"))

    verdict = "ready" if not blockers else "not-ready"
    report = {
        "event": "setup_checked",
        "project_id": project_id,
        "checked_at": checked_at or _utcnow(),
        "duration_ms": duration_ms,
        "verdict": verdict,
        "dispatch": "allowed" if not blockers else "denied",
        "dispatched": False,   # no dispatch path exists here, by design
        "blockers": blockers,
        "blocker_codes": [b["code"] for b in blockers],
        "prerequisite_outcomes": outcomes,
        "operations": ops,
        "cancellation": {
            "supported": ops["cancel"]["supported"],
            "required_verbs": list(KANBAN_OPS["cancel"]),
            "present": sorted(
                set(KANBAN_OPS["cancel"]) - set(ops["cancel"]["missing"])),
        },
        "version_set": {
            "hermes": list(hermes_version) if hermes_version else None,
            "runtime": runtime,
            "schema": schema_version,
            "kit": VERSION,
            "skills": {skill: {"pin": pin,
                               "installed": installed_skills.get(skill)}
                       for skill, pin in approved.items()},
        },
        "recipe": {
            "runtime": runtime,
            "preview": (effective.get("endpoint") or {})
            .get("preview", {}).get("provider"),
            "merge": (effective.get("endpoint") or {})
            .get("merge", {}).get("method"),
        },
        "permissions": {
            "github": {
                "authenticated": (credentials.get("github") or {})
                .get("authenticated"),
                "user": (credentials.get("github") or {}).get("user"),
                "token_scopes": (credentials.get("github") or {})
                .get("token_scopes") or [],
            },
            "vercel": {
                "authenticated": (credentials.get("vercel") or {})
                .get("authenticated"),
                "user": (credentials.get("vercel") or {}).get("user"),
            },
            "telegram": {
                "env_keys": len(transport.get("telegram_env_keys") or []),
            },
        },
        "verification_commands": list(
            (effective.get("verification") or {})
            .get("acceptance_commands") or []),
    }
    # A5 — the emitted report itself must never carry a token: scan its
    # serialized form and say so on the record.
    blob = json.dumps(report, ensure_ascii=True)
    report["canary_scan"] = "clean" if not canary_scan(blob) else "FLAGGED"
    return report


# ---------------------------------------------------------------------------
# Live collectors — self-contained port of the spike probe's collection half.
# Every collector degrades to data, never an exit: a missing tool is a named
# reading, which is the point of the verdict.
# ---------------------------------------------------------------------------

def _run(argv, timeout=15):
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
        return {"ok": proc.returncode == 0, "code": proc.returncode,
                "out": (proc.stdout + proc.stderr).strip()}
    except FileNotFoundError:
        return {"ok": False, "code": None, "out": f"{argv[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "out": "timed out"}
    except OSError as exc:
        return {"ok": False, "code": None, "out": str(exc)}


def _gh_json(args):
    result = _run(["gh"] + args, timeout=20)
    if not result["ok"]:
        return None
    try:
        return json.loads(result["out"])
    except (json.JSONDecodeError, ValueError):
        return None


def _collect_hermes(hermes_bin):
    out = {"bin": hermes_bin}
    ver = _run([hermes_bin, "--version"])
    if not ver["ok"]:
        out["version"] = None
        out["error"] = ver["out"] or "hermes --version failed"
        return out
    sem = _semver(ver["out"].splitlines()[0] if ver["out"] else None)
    out["version"] = list(sem) if sem else None
    help_out = _run([hermes_bin, "--help"])["out"]
    out["top_level"] = {name: (name in help_out) for name in
                        ("kanban", "webhook", "gateway", "auth",
                         "secrets", "send", "models")}
    kanban = _run([hermes_bin, "kanban", "--help"])
    verbs = []
    if kanban["ok"]:
        verbs = re.findall(r"^    ([a-z][a-z0-9-]*)", kanban["out"], re.M)
    out["kanban_verbs"] = sorted(set(verbs))
    webhook = _run([hermes_bin, "webhook", "subscribe", "--help"])
    out["webhook_subscribe"] = webhook["ok"] and "HMAC" in webhook["out"]
    gateway = _run([hermes_bin, "gateway", "status"], timeout=10)
    out["gateway"] = {
        "supervised": gateway["ok"] and (
            "supervised" in gateway["out"]
            or "Launchd" in gateway["out"]),
        "raw": gateway["out"].splitlines()[:3],
    }
    auth = _run([hermes_bin, "auth", "list"])
    out["auth_providers"] = sorted(set(re.findall(
        r"^([a-z][\w-]*) \(\d+ credentials?\)", auth["out"], re.M)))
    tasks = _run([hermes_bin, "kanban", "list", "--json"])
    try:
        out["kanban_tasks"] = json.loads(tasks["out"]) \
            if tasks["ok"] else []
    except json.JSONDecodeError:
        out["kanban_tasks"] = []
    return out


def _read_hermes_config(hermes_home):
    cfg = Path(hermes_home) / "config.yaml"
    found = {}
    if not cfg.is_file():
        return found
    section = None
    for line in cfg.read_text(
            encoding="utf-8", errors="replace").splitlines():
        if not line.startswith((" ", "\t")) and ":" in line:
            section = line.split(":", 1)[0].strip()
        elif section and ":" in line:
            key, _, val = line.strip().partition(":")
            val = val.strip().strip("'\"")
            if section == "model" and key.strip() in (
                    "provider", "default", "base_url", "api_mode"):
                found[f"model.{key.strip()}"] = val
    return found


def _collect_model(hermes_home, hermes_bin, model_url=None):
    cfg = _read_hermes_config(hermes_home)
    provider = cfg.get("model.provider")
    out = {"config": cfg, "provider": provider,
           "model": cfg.get("model.default"),
           "base_url": model_url or cfg.get("model.base_url")}
    auth = _run([hermes_bin, "auth", "list"])
    out["auth_providers"] = sorted(set(re.findall(
        r"^([a-z][\w-]*) \(\d+ credentials?\)", auth["out"], re.M)))
    if not provider:
        out["error"] = "no model.provider in hermes config"
        return out
    status = _run([hermes_bin, "auth", "status", provider])
    out["auth_status"] = "logged in" \
        if "logged in" in status["out"].lower() else status["out"]
    url = out["base_url"]
    if url and url.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(
                url.rstrip("/") + "/models",
                headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read(65536))
            ids = [m.get("id") for m in data.get("data", [])]
            out["endpoint"] = {"reachable": True, "models": ids[:20],
                               "configured_model_listed":
                                   out["model"] in ids}
        except Exception as exc:  # noqa: BLE001 — failure IS the signal
            out["endpoint"] = {"reachable": False,
                               "error": type(exc).__name__}
    else:
        out["endpoint"] = {"reachable": None,
                           "note": "no http base_url — auth presence only"}
    return out


def _collect_repo(repo_arg):
    nwo = repo_arg
    if not nwo:
        remote = _run(["git", "remote", "get-url", "origin"])
        match = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$",
                          remote["out"])
        if not match:
            return {"error": "no repo given and no usable origin remote"}
        nwo = match.group(1)
    info = _gh_json(["repo", "view", nwo, "--json",
                     "nameWithOwner,id,isPrivate,defaultBranchRef"])
    out = {"name": nwo}
    if info:
        out.update({
            "node_id": info.get("id"),
            "private": info.get("isPrivate"),
            "default_branch": (info.get("defaultBranchRef") or {})
            .get("name"),
        })
    else:
        out["error"] = f"gh repo view {nwo} failed"
    return out


def _collect_repo_rules(repo):
    nwo = (repo or {}).get("name")
    base = (repo or {}).get("default_branch")
    if not nwo or not base:
        return {"skipped": "repository unknown"}
    meta = _gh_json(["api", f"repos/{nwo}"]) or {}
    prot = _gh_json(["api", f"repos/{nwo}/branches/{base}/protection"])
    checks, strict, enforce_admins = [], None, None
    if prot:
        rsc = prot.get("required_status_checks") or {}
        checks = [c if isinstance(c, str) else c.get("context", "")
                  for c in rsc.get("contexts", [])]
        checks += [c.get("context", "")
                   for c in rsc.get("checks", []) if isinstance(c, dict)]
        strict = rsc.get("strict")
        enforce_admins = bool(
            (prot.get("enforce_admins") or {}).get("enabled"))
    found = []
    for path in AUTOMATION_FILES:
        result = _run(["gh", "api", f"repos/{nwo}/contents/{path}",
                       "--jq", ".path"], timeout=15)
        if result["ok"] and result["out"]:
            found.append(path)
    return {
        "default_branch": base,
        "allow_auto_merge": meta.get("allow_auto_merge"),
        "protected": bool(prot),
        "required_checks": sorted(c for c in checks if c),
        "strict_up_to_date": strict,
        "enforce_admins": enforce_admins,
        "automation_files": found,
    }


def _collect_credentials():
    out = {}
    gh = _run(["gh", "auth", "status"])
    if gh["ok"]:
        scopes = re.findall(r"Token scopes?:\s*(.+)", gh["out"])
        account = (re.search(r"account (\S+)", gh["out"])
                   or re.search(r"as (\S+)", gh["out"]))
        out["github"] = {
            "authenticated": True,
            "token_scopes": [s.strip().strip("'")
                             for line in scopes
                             for s in line.split(",")],
            "user": account.group(1) if account else None,
        }
    else:
        out["github"] = {"authenticated": False,
                         "error": gh["out"].splitlines()[-1:]}
    vercel = _run(["vercel", "whoami"], timeout=10)
    user = next((ln.strip() for ln in vercel["out"].splitlines()
                 if ln.strip() and " " not in ln.strip()
                 and "vercel" not in ln.lower()
                 and not ln.strip().startswith(("╭", "╰", "│"))), None)
    out["vercel"] = {"authenticated": vercel["ok"] and bool(user),
                     "user": user if vercel["ok"] else None}
    return out


def _collect_idd(skills_root):
    skills = {}
    root = Path(skills_root) if skills_root else \
        Path.home() / ".agents" / "skills"
    if root.is_dir():
        for skill_md in sorted(root.glob("*/SKILL.md")):
            try:
                head = skill_md.read_text(
                    encoding="utf-8", errors="replace")[:4000]
            except OSError:
                continue
            match = re.search(r"^\s*version:\s*([\w.]+)", head, re.M)
            skills[skill_md.parent.name] = \
                match.group(1) if match else "unversioned"
    return {"skills_root": str(root), "skills": skills}


def _collect_transport(hermes_home):
    home = Path(hermes_home)
    adapter = home / "hermes-agent" / "plugins" / "platforms" / "telegram"
    env_file = home / ".env"
    env_keys = set()
    if env_file.is_file():
        for line in env_file.read_text(
                encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^([A-Z][A-Z0-9_]+)=", line.strip())
            if match:
                env_keys.add(match.group(1))
    return {
        "telegram_adapter": adapter.is_dir(),
        "telegram_env_keys": sorted(
            k for k in env_keys if k.startswith("TELEGRAM")),
        "env_file": str(env_file) if env_file.is_file() else None,
    }


def collect_readings(*, repo=None, hermes_bin="hermes",
                     hermes_home=None, skills_root=None,
                     model_url=None):
    """Collect live readiness readings for the selected recipe.

    Stdlib-only subprocess calls; every collector degrades to recorded
    data so the verdict names what is missing instead of crashing.
    """
    hermes_home = hermes_home or str(Path.home() / ".hermes")
    repo_info = _collect_repo(repo)
    return {
        "collector": "factory_kit.setup.readiness",
        "repository": repo_info,
        "host": {
            "os": platform.uname().system,
            "release": platform.uname().release,
            "machine": platform.uname().machine,
            "python": platform.python_version(),
        },
        "hermes": _collect_hermes(hermes_bin),
        "model": _collect_model(hermes_home, hermes_bin,
                                model_url=model_url),
        "idd": _collect_idd(skills_root),
        "credentials": _collect_credentials(),
        "repo_rules": _collect_repo_rules(repo_info),
        "transport": _collect_transport(hermes_home),
    }


# ---------------------------------------------------------------------------
# setup_checked — persist the verdict, gate registration (A6).
# ---------------------------------------------------------------------------

def run_setup_checked(*, project_id, effective, store=None,
                      registration_store=None, supported_versions=None,
                      readings=None, collector=None, duration_ms=None):
    """Run substantive readiness and persist its outcome.

    - ``readings`` may be a recorded fixture dict or ``None`` for live
      collection (``collector`` overrides the collector for tests).
    - The ``setup_checked`` event is appended to ``store`` with project
      ID, version set, duration and per-prerequisite outcomes — whether
      the verdict is ready or not, so failed setup stays diagnosable.
    - Registration is *enabled* only when a pending registration row
      already exists (created by an accepted plan) **and** the verdict is
      ready. A failing verdict refreshes an existing row's recorded
      readiness but never creates one — nothing is registered or
      presented as authorized on a failed setup.
    """
    started = time.monotonic()
    if readings is None:
        readings = (collector or collect_readings)()
    if duration_ms is None:
        duration_ms = int((time.monotonic() - started) * 1000)

    report = evaluate(readings, effective, project_id=project_id,
                      duration_ms=duration_ms)

    if store is not None and project_id:
        # Bind the ledger to this project before any event lands; a
        # ledger owned by another project refuses (OwnershipError).
        store.set_project_id(project_id)

    repo_id = (effective.get("identity") or {}).get("repo_id")
    if registration_store is not None and repo_id:
        existing = registration_store.get(repo_id)
        if existing is None:
            report["setup_enabled"] = False
            report["registration"] = {
                "status": "absent",
                "detail": "no registration row — apply an accepted plan "
                          "first; readiness alone registers nothing (A6)",
            }
        else:
            report["setup_enabled"] = report["verdict"] == "ready"
            # Re-registering refreshes the observational readiness field
            # and never broadens the bound generation; a drifted policy
            # surface takes the park/new-generation path inside
            # RegistrationStore itself.
            registration_store.register(
                effective, readiness=report,
                supported_versions=supported_versions or [])
            report["registration"] = {
                "status": "enabled" if report["setup_enabled"]
                else "not-ready",
                "repo_id": repo_id,
            }
    else:
        report["setup_enabled"] = False
        report["registration"] = {"status": "no-store"}

    if store is not None:
        store.record_event("setup_checked", {
            "project_id": project_id,
            "version_set": report["version_set"],
            "duration_ms": report["duration_ms"],
            "verdict": report["verdict"],
            "blocker_codes": report["blocker_codes"],
            "prerequisite_outcomes": report["prerequisite_outcomes"],
        })
    return report
