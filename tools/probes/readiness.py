#!/usr/bin/env python3
"""factory-kit readiness probe — the executable half of issue #2 / A1.

Reads the environment of the *selected recipe* (repository, host, Hermes
install, IDD skill bundle, runtimes, model credentials, credential scope,
transport) and decides whether the controller may dispatch work. The probe is
read-only by construction: it never creates, claims, dispatches or merges
anything. Its verdict says what a dispatcher may do, and records *why not*
when the answer is no.

Exit codes (shared gi-* vocabulary):

    0  ready          — no blockers; ``dispatch: allowed`` in the verdict
    1  not ready      — named blockers exist; ``dispatch: denied``
                        (a verdict, not a failure)
    2  usage error    — malformed invocation
    4  cannot complete — the collector could not produce a verdict at all
                         (missing git repo, unreadable fixture, ...)

Usage:

    python3 tools/probes/readiness.py --repo owner/name [--write out.json]
    python3 tools/probes/readiness.py --fixture tests/fixtures/readiness/ready.json
    python3 tools/probes/readiness.py --self-test

``--fixture`` evaluates a recorded ``readings`` document instead of probing the
live host — this is how the spike runs "one deliberately unavailable
prerequisite" deterministically (see tests/fixtures/readiness/*.json).

Design notes:

- Stdlib only. Every collector degrades individually: a probe that cannot run
  ``vercel`` still emits a verdict with ``vercel`` recorded absent, because the
  point is to *name* what is missing, not to crash.
- Secret hygiene: the probe records credential *presence* and *scope* (token
  scopes, provider names, env var names) — never values.
- ``dispatched: false`` is always emitted: this binary has no dispatch path at
  all, so "a blocker dispatches nothing" is structurally true, not a promise.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "1.0.0"

# The tested recipe pins the Hermes version this spike actually exercised.
# Kanban (atomic claim / request-review / pr_acceptance), webhook
# subscriptions, plugins and the gateway adapters all ship in this train.
HERMES_MIN_VERSION = (0, 21, 5)

# Kanban verbs the lifecycle depends on, mapped to the §6.5 required
# operations. Absence of any of these is an ``unsupported-interface`` blocker.
KANBAN_OPS = {
    "start": ["create", "claim", "dispatch"],
    "liveness": ["heartbeat", "runs", "tail"],
    "result": ["complete", "request-review", "block", "attach"],
    "cancel": ["block", "reclaim"],
}

# Repository-side automation artifacts that would make a second writer own the
# merge/task lifecycle — a conflicting owner the recipe refuses to coexist
# with until ownership is explicit.
AUTOMATION_FILES = [
    ".mergify.yml",
    ".kodiak.toml",
    ".github/dependabot.yml",
    ".github/workflows/auto-merge.yml",
    ".github/workflows/automerge.yml",
]

BLOCKER_REMEDIATION = {
    "missing-executable": "install the missing executable or fix PATH",
    "unsupported-version": "",  # formatted at build time — needs HERMES_MIN_VERSION
    "model-unavailable": "authenticate `hermes auth <provider>` or restore the model endpoint",
    "conflicting-task-owner": "disable repo-side automation or assign factory-kit exclusive merge ownership",
    "unsupported-interface": "required Kanban/webhook verbs are absent — check the Hermes install, do not build a second scheduler",
    "base-unprotected": "enable required status checks on an up-to-date base with enforce_admins before the merge endpoint",
    "missing-transport": "configure the Telegram adapter (plugins/platforms/telegram) and gateway supervision",
}
BLOCKER_REMEDIATION["unsupported-version"] = (
    f"upgrade Hermes to >= {'.'.join(map(str, HERMES_MIN_VERSION))} "
    "or re-pin the recipe")


def _run(argv, timeout=15):
    """Run a command, returning {ok, code, out} — never raises."""
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
        return {"ok": p.returncode == 0, "code": p.returncode,
                "out": (p.stdout + p.stderr).strip()}
    except FileNotFoundError:
        return {"ok": False, "code": None, "out": f"{argv[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "out": "timed out"}
    except OSError as exc:
        return {"ok": False, "code": None, "out": str(exc)}


def _which(name):
    return _run(["/usr/bin/env", "which", name])["ok"]


def _semver(text):
    m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def _gh_json(args):
    r = _run(["gh"] + args, timeout=20)
    if not r["ok"]:
        return None
    try:
        return json.loads(r["out"])
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Collectors — each returns a readings fragment; failures are data, not exits.
# ---------------------------------------------------------------------------

def collect_repository(repo_arg):
    """Repository identity: git remote or --repo, then gh repo view."""
    nwo = repo_arg
    if not nwo:
        remote = _run(["git", "remote", "get-url", "origin"])
        m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", remote["out"])
        if not m:
            return {"error": "no --repo given and no usable origin remote"}
        nwo = m.group(1)
    info = _gh_json(["repo", "view", nwo,
                     "--json", "nameWithOwner,id,isPrivate,defaultBranchRef"])
    out = {"name": nwo}
    if info:
        out.update({
            "node_id": info.get("id"),
            "private": info.get("isPrivate"),
            "default_branch": (info.get("defaultBranchRef") or {}).get("name"),
        })
    else:
        out["error"] = f"gh repo view {nwo} failed"
    return out


def collect_repo_rules(repo):
    """Merge-owner / protection facts for the repo's default branch."""
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
        enforce_admins = bool((prot.get("enforce_admins") or {}).get("enabled"))
    found_auto = []
    for path in AUTOMATION_FILES:
        r = _run(["gh", "api", f"repos/{nwo}/contents/{path}",
                  "--jq", ".path"], timeout=15)
        if r["ok"] and r["out"]:
            found_auto.append(path)
    return {
        "default_branch": base,
        "allow_auto_merge": meta.get("allow_auto_merge"),
        "protected": bool(prot),
        "required_checks": sorted(c for c in checks if c),
        "strict_up_to_date": strict,
        "enforce_admins": enforce_admins,
        "automation_files": found_auto,
    }


def collect_host():
    u = platform.uname()
    return {
        "os": u.system, "release": u.release, "machine": u.machine,
        "python": platform.python_version(),
    }


def collect_hermes(hermes_bin):
    out = {"bin": hermes_bin}
    ver = _run([hermes_bin, "--version"])
    if not ver["ok"]:
        out["version_raw"] = None
        out["version"] = None
        out["error"] = ver["out"] or "hermes --version failed"
        return out
    out["version_raw"] = ver["out"].splitlines()[0] if ver["out"] else None
    sem = _semver(out["version_raw"])
    out["version"] = list(sem) if sem else None

    # Top-level surface inventory.
    help_out = _run([hermes_bin, "--help"])["out"]
    out["top_level"] = {
        name: (name in help_out)
        for name in ("kanban", "webhook", "cron", "gateway", "plugins",
                     "auth", "secrets", "egress", "send", "models")
    }

    kanban = _run([hermes_bin, "kanban", "--help"])
    verbs = []
    if kanban["ok"]:
        verbs = re.findall(r"^    ([a-z][a-z0-9-]*)", kanban["out"], re.M)
    out["kanban_verbs"] = sorted(set(verbs))

    wh = _run([hermes_bin, "webhook", "subscribe", "--help"])
    out["webhook_subscribe"] = wh["ok"] and "HMAC" in wh["out"]

    gw = _run([hermes_bin, "gateway", "status"], timeout=10)
    out["gateway"] = {
        "supervised": gw["ok"] and ("supervised" in gw["out"] or "Launchd" in gw["out"]),
        "raw": gw["out"].splitlines()[:3],
    }

    auth = _run([hermes_bin, "auth", "list"])
    providers = re.findall(r"^([a-z][\w-]*) \(\d+ credentials?\)", auth["out"], re.M)
    out["auth_providers"] = sorted(providers)

    # Best-effort: existing kanban tasks are evidence of an owner already
    # holding work identity for this host.
    kl = _run([hermes_bin, "kanban", "list", "--json"])
    try:
        out["kanban_tasks"] = json.loads(kl["out"]) if kl["ok"] else []
    except json.JSONDecodeError:
        out["kanban_tasks"] = []
    return out


def collect_idd(idd_repo):
    out = {"repo": str(idd_repo)}
    desc = _run(["git", "-C", str(idd_repo), "describe", "--tags", "--always"])
    if desc["ok"]:
        out["version"] = desc["out"]
    else:
        out["version"] = None
        out["error"] = f"idd repo not found at {idd_repo}"

    skills = {}
    skills_root = Path.home() / ".agents" / "skills"
    for skill_md in sorted(skills_root.glob("*/SKILL.md")) if skills_root.is_dir() else []:
        try:
            head = skill_md.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        m = re.search(r"^\s*version:\s*([\w.]+)", head, re.M)
        skills[skill_md.parent.name] = m.group(1) if m else "unversioned"
    out["skills_root"] = str(skills_root)
    out["skills"] = skills
    return out


def collect_runtimes():
    """Worker-adjacent runtimes. These are *harnesses*, not supported worker
    lanes — the recipe's worker runtime is a Hermes Kanban profile; these are
    recorded for provenance and for the unsupported-lane boundary."""
    runtimes = {}
    for name, cmd in (("claude", ["claude", "--version"]),
                      ("codex", ["codex", "--version"]),
                      ("opencode", ["opencode", "--version"]),
                      ("node", ["node", "--version"]),
                      ("npm", ["npm", "--version"])):
        r = _run(cmd)
        runtimes[name] = {"present": r["ok"],
                          "version": r["out"].splitlines()[0] if r["out"] else None}
    return runtimes


def _read_hermes_config(hermes_home):
    """Minimal YAML line-scan of ~/.hermes/config.yaml for the model block.
    We only need scalars — importing a yaml package is deliberately avoided."""
    cfg = hermes_home / "config.yaml"
    found = {}
    if not cfg.is_file():
        return found
    section = None
    for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith((" ", "\t")) and ":" in line:
            section = line.split(":", 1)[0].strip()
        elif section and ":" in line:
            key = line.strip().split(":", 1)[0].strip()
            val = line.strip().split(":", 1)[1].strip().strip("'\"")
            if section == "model" and key in ("provider", "default", "base_url",
                                              "api_mode"):
                found[f"model.{key}"] = val
            if section == "gateway" and key == "multiplex_profiles":
                found["gateway.multiplex_profiles"] = val
    return found


def collect_model(hermes_home, hermes_bin, model_url=None):
    cfg = _read_hermes_config(hermes_home)
    provider = cfg.get("model.provider")
    out = {"config": cfg, "provider": provider,
           "model": cfg.get("model.default"), "base_url": model_url or cfg.get("model.base_url")}
    if not provider:
        out["error"] = "no model.provider in hermes config"
        return out

    auth = _run([hermes_bin, "auth", "status", provider])
    out["auth_status"] = "logged in" if "logged in" in auth["out"].lower() else auth["out"]

    url = out["base_url"]
    if url and url.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(url.rstrip("/") + "/models",
                                         headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read(65536))
            ids = [m.get("id") for m in data.get("data", [])]
            out["endpoint"] = {
                "reachable": True,
                "models": ids[:20],
                "configured_model_listed": out["model"] in ids if out["model"] else None,
            }
        except Exception as exc:  # noqa: BLE001 — endpoint failure IS the signal
            out["endpoint"] = {"reachable": False, "error": type(exc).__name__}
    else:
        # OAuth-pooled providers have no self-serve /models probe; authenticated
        # presence is what `hermes auth status` reports and what we record.
        out["endpoint"] = {"reachable": None,
                           "note": "no http base_url — auth presence only"}
    return out


def collect_credentials():
    out = {}
    gh = _run(["gh", "auth", "status"])
    if gh["ok"]:
        scopes = re.findall(r"Token scopes?:\s*(.+)", gh["out"])
        acct = (re.search(r"account (\S+)", gh["out"])
                or re.search(r"as (\S+)", gh["out"]))
        out["github"] = {
            "authenticated": True,
            "token_scopes": [s.strip().strip("'")
                             for line in scopes for s in line.split(",")],
            "user": acct.group(1) if acct else None,
        }
    else:
        out["github"] = {"authenticated": False, "error": gh["out"].splitlines()[-1:]}
    vc = _run(["vercel", "whoami"], timeout=10)
    # whoami prints a version banner then the login; pick the plain login line.
    user = next((ln.strip() for ln in vc["out"].splitlines()
                 if ln.strip() and " " not in ln.strip()
                 and "vercel" not in ln.lower()
                 and not ln.strip().startswith(("╭", "╰", "│"))), None)
    out["vercel"] = {"authenticated": vc["ok"] and bool(user),
                     "user": user if vc["ok"] else None}
    return out


def collect_transport(hermes_home):
    telegram_dir = (Path(hermes_home) / "hermes-agent"
                    / "plugins" / "platforms" / "telegram")
    env_file = Path(hermes_home) / ".env"
    env_keys = set()
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^([A-Z][A-Z0-9_]+)=", line.strip())
            if m:
                env_keys.add(m.group(1))
    return {
        "telegram_adapter": telegram_dir.is_dir(),
        "telegram_env_keys": sorted(k for k in env_keys if k.startswith("TELEGRAM")),
        "env_file": str(env_file) if env_file.is_file() else None,
    }


# ---------------------------------------------------------------------------
# Evaluation — pure function over `readings`; the unit-testable verdict.
# ---------------------------------------------------------------------------

def evaluate(readings):
    """Return {verdict, dispatch, blockers[], operations{}, summary}."""
    blockers = []
    ops = {}

    # --- executables -------------------------------------------------------
    # git/gh/python3 presence is implicit in collectors having run at all;
    # the hermes binary gets an explicit check because it is the recipe's pin.
    hermes = readings.get("hermes") or {}
    if hermes.get("error") or not hermes.get("version"):
        blockers.append(_b("missing-executable",
                           f"hermes binary unusable: {hermes.get('error', 'no version')}"))

    hv = tuple(hermes.get("version") or (0, 0, 0))
    if hv < HERMES_MIN_VERSION and "missing-executable" not in [b["code"] for b in blockers]:
        blockers.append(_b(
            "unsupported-version",
            f"hermes {'.'.join(map(str, hv))} < pinned {'.'.join(map(str, HERMES_MIN_VERSION))}"))

    # --- lifecycle operations ----------------------------------------------
    verbs = set(hermes.get("kanban_verbs") or [])
    for op, required in KANBAN_OPS.items():
        missing = [v for v in required if v not in verbs]
        ops[op] = {"required_verbs": required,
                   "missing": missing,
                   "supported": not missing}
    if any(not o["supported"] for o in ops.values()) or not hermes.get("webhook_subscribe"):
        detail = "kanban verbs missing: " + ", ".join(
            f"{op}→{o['missing']}" for op, o in ops.items() if o["missing"])
        if not hermes.get("webhook_subscribe"):
            detail += "; webhook subscribe unavailable"
        blockers.append(_b("unsupported-interface", detail))

    # --- model --------------------------------------------------------------
    model = readings.get("model") or {}
    ep = model.get("endpoint") or {}
    auth_ok = "logged in" in str(model.get("auth_status", "")).lower()
    if model.get("error"):
        blockers.append(_b("model-unavailable", model["error"]))
    elif ep.get("reachable") is False:
        blockers.append(_b(
            "model-unavailable",
            f"{model.get('provider')}/{model.get('model')} endpoint "
            f"{model.get('base_url')} unreachable ({ep.get('error')})"))
    elif not auth_ok:
        blockers.append(_b(
            "model-unavailable",
            f"provider {model.get('provider')} not authenticated: "
            f"{model.get('auth_status')}"))
    elif ep.get("reachable") and ep.get("configured_model_listed") is False:
        blockers.append(_b(
            "model-unavailable",
            f"endpoint reachable but model '{model.get('model')}' not in /models listing"))

    # --- conflicting task/merge owner --------------------------------------
    rules = readings.get("repo_rules") or {}
    conflicts = []
    if rules.get("allow_auto_merge"):
        conflicts.append("repo allow_auto_merge=true")
    for f in rules.get("automation_files") or []:
        conflicts.append(f"automation config {f}")
    nwo = (readings.get("repository") or {}).get("name")
    for t in hermes.get("kanban_tasks") or []:
        blob = json.dumps(t)
        if nwo and nwo.split("/")[-1] in blob:
            conflicts.append(f"existing kanban task {t.get('id', '?')} references {nwo}")
    if conflicts:
        blockers.append(_b("conflicting-task-owner", "; ".join(conflicts)))

    # --- base protection (merge precondition) --------------------------------
    if rules and not rules.get("skipped"):
        if not rules.get("protected"):
            blockers.append(_b(
                "base-unprotected",
                f"{nwo or '<repo>'}:{rules.get('default_branch')} has no branch protection — "
                "required checks on an up-to-date base cannot be enforced"))
        elif not rules.get("required_checks"):
            blockers.append(_b(
                "base-unprotected",
                "branch protection exists but names no required status checks"))
        elif rules.get("strict_up_to_date") is not True:
            blockers.append(_b(
                "base-unprotected",
                f"{nwo or '<repo>'}:{rules.get('default_branch')} does not "
                "require an up-to-date base (strict=false) — a stale head "
                "could merge without fresh checks"))
        elif rules.get("enforce_admins") is False:
            blockers.append(_b(
                "base-unprotected",
                "enforce_admins disabled — credentials may bypass required checks"))

    # --- transport ------------------------------------------------------------
    transport = readings.get("transport") or {}
    if not transport.get("telegram_adapter"):
        blockers.append(_b("missing-transport", "telegram platform adapter not installed"))
    if not (hermes.get("gateway") or {}).get("supervised"):
        blockers.append(_b("missing-transport", "gateway is not supervised/running"))

    verdict = "ready" if not blockers else "not-ready"
    return {
        "verdict": verdict,
        "dispatch": "allowed" if not blockers else "denied",
        "dispatched": False,  # this probe has no dispatch path, by construction
        "blockers": blockers,
        "operations": ops,
        "blocker_codes": [b["code"] for b in blockers],
    }


def _b(code, detail):
    return {"code": code, "detail": detail,
            "remediation": BLOCKER_REMEDIATION.get(code, "")}


# ---------------------------------------------------------------------------
# Self-test fixtures — evaluate-only, no host access.
# ---------------------------------------------------------------------------

_SELFTEST = [
    ("ready", {
        "repository": {"name": "acme/demo", "node_id": "R_1", "default_branch": "main"},
        "hermes": {"version": [0, 21, 5],
                   "kanban_verbs": sorted({v for vs in KANBAN_OPS.values() for v in vs}),
                   "webhook_subscribe": True,
                   "gateway": {"supervised": True}, "kanban_tasks": []},
        "model": {"provider": "dspark", "model": "m",
                  "auth_status": "logged in",
                  "endpoint": {"reachable": True, "configured_model_listed": True}},
        "repo_rules": {"protected": True, "required_checks": ["CI"],
                       "strict_up_to_date": True, "enforce_admins": True,
                       "allow_auto_merge": False, "automation_files": []},
        "transport": {"telegram_adapter": True},
    }, "ready"),
    ("model-unavailable", {
        "repository": {"name": "acme/demo", "default_branch": "main"},
        "hermes": {"version": [0, 21, 5],
                   "kanban_verbs": sorted({v for vs in KANBAN_OPS.values() for v in vs}),
                   "webhook_subscribe": True,
                   "gateway": {"supervised": True}, "kanban_tasks": []},
        "model": {"provider": "dspark", "model": "m", "base_url": "http://x/v1",
                  "auth_status": "logged in",
                  "endpoint": {"reachable": False, "error": "TimeoutError"}},
        "repo_rules": {"protected": True, "required_checks": ["CI"],
                       "strict_up_to_date": True, "enforce_admins": True,
                       "allow_auto_merge": False, "automation_files": []},
        "transport": {"telegram_adapter": True},
    }, "not-ready"),
    ("conflicting-owner", {
        "repository": {"name": "acme/demo", "default_branch": "main"},
        "hermes": {"version": [0, 21, 5],
                   "kanban_verbs": sorted({v for vs in KANBAN_OPS.values() for v in vs}),
                   "webhook_subscribe": True,
                   "gateway": {"supervised": True}, "kanban_tasks": []},
        "model": {"provider": "x", "model": "m", "auth_status": "logged in",
                  "endpoint": {"reachable": None}},
        "repo_rules": {"protected": True, "required_checks": ["CI"],
                       "strict_up_to_date": True, "enforce_admins": True,
                       "allow_auto_merge": True, "automation_files": []},
        "transport": {"telegram_adapter": True},
    }, "not-ready"),
]


def _self_test():
    ok = True
    for name, readings, want in _SELFTEST:
        got = evaluate(readings)["verdict"]
        status = "PASS" if got == want else "FAIL"
        ok = ok and got == want
        print(f"{status}  {name}: verdict={got} (want {want}) "
              f"blockers={evaluate(readings)['blocker_codes']}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="factory-kit readiness probe (issue #2 / A1)")
    ap.add_argument("--repo", help="owner/name (default: parse git remote)")
    ap.add_argument("--hermes-bin", default="hermes")
    ap.add_argument("--hermes-home", default=str(Path.home() / ".hermes"))
    ap.add_argument("--idd-repo",
                    default=str(Path.home() / "buildspace" / "luongnv89" / "idd"))
    ap.add_argument("--model-url", help="override model base_url probe")
    ap.add_argument("--write", metavar="FILE",
                    help="write the collected readings + verdict JSON")
    ap.add_argument("--fixture", metavar="FILE",
                    help="evaluate a recorded readings JSON instead of probing")
    ap.add_argument("--self-test", action="store_true",
                    help="run built-in fixture cases through evaluate()")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.fixture:
        try:
            readings = json.loads(Path(args.fixture).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read fixture: {exc}", file=sys.stderr)
            return 4
        report = evaluate(readings)
    else:
        if not _which("git"):
            print("git not on PATH — cannot collect", file=sys.stderr)
            return 4
        readings = {
            "probe_version": VERSION,
            "repository": collect_repository(args.repo),
            "host": collect_host(),
            "hermes": collect_hermes(args.hermes_bin),
            "idd": collect_idd(Path(args.idd_repo)),
            "runtimes": collect_runtimes(),
            "model": collect_model(Path(args.hermes_home), args.hermes_bin,
                                   model_url=args.model_url),
            "credentials": collect_credentials(),
            "transport": collect_transport(Path(args.hermes_home)),
        }
        readings["repo_rules"] = collect_repo_rules(readings["repository"])
        report = evaluate(readings)

    if args.write:
        payload = {"readings": readings if not args.fixture else None,
                   "report": report}
        try:
            Path(args.write).write_text(json.dumps(payload, indent=2) + "\n")
        except OSError as exc:
            print(f"cannot write {args.write}: {exc}", file=sys.stderr)
            return 4

    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
