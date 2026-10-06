#!/usr/bin/env python3
"""``factory-run`` CLI — the live driver surface (``python3 -m
factory_kit.run``).

    python3 -m factory_kit.run tick --repo /path [--board slug]
                                  [--json]
    python3 -m factory_kit.run watch --repo /path [--board slug]
                                  [--interval 30] [--max-passes N]
                                  [--until-idle] [--json]
    python3 -m factory_kit.run status --repo /path
    python3 -m factory_kit.run approve --repo /path --request ID
                                       [--actor LOGIN]
    python3 -m factory_kit.run reject --repo /path --request ID
                                      [--actor LOGIN] [--reason TEXT]
    python3 -m factory_kit.run retry --repo /path --issue N
                                     [--actor LOGIN]

Exit codes (the setup vocabulary):

    0  ok — tick ran / approved / rejected
    1  refused or denied — build refusal, driver already running, a
       denied decision
    2  usage error
    3  invalid input — unparsable manifest/state
    4  unable — the command could not run to an answer

``--profile``/``--state-dir`` resolve the shared state directory through
:func:`factory_kit.config.paths.profile_state_dir` — the same
``registrations.json``/``intake.db`` ``factory-setup`` writes
(``--state-dir`` always wins). ``tick``/``watch`` take an exclusive
``flock`` on ``<state-dir>/run.lock``: a second driver exits
``driver-already-running`` rather than double-driving the lane.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from factory_kit.config.paths import profile_state_dir
from factory_kit.config.registration import RegistrationStore
from factory_kit.config.schema import (PREVIEW_PROVIDER_NONE, ConfigError,
                                       load_manifest_file)
from factory_kit.durable.store import IntakeStore
from factory_kit.execution.worker import HermesKanbanWorker
from factory_kit.preview.pages import GitHubPagesPreview
from factory_kit.preview.port import (NullPreview, PreviewError,
                                     VercelCliPreview)
from factory_kit.publication.remote import GhCliRemote, RemoteError
from factory_kit.recovery.poller import GhIssuePoller
from factory_kit.setup import readiness as readiness_mod

from .driver import Driver, compose


class Refusal(Exception):
    """A build-stage refusal — the named reason exits 1."""

    def __init__(self, reason, detail=None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _log(msg):
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}")


def _state_dir(args):
    return args.state_dir or str(profile_state_dir(args.profile))


def _manifest(repo):
    try:
        return load_manifest_file(
            os.path.join(repo, ".factory-kit.yml"))
    except FileNotFoundError:
        raise Refusal("manifest-missing",
                      f"{repo}/.factory-kit.yml does not exist")
    except ConfigError as exc:
        raise Refusal("manifest-invalid", str(exc))
    except Exception as exc:
        raise Refusal("manifest-unreadable", str(exc))


def _auth_providers(hermes_bin):
    """``hermes auth list`` → the authenticated provider set (the
    readiness parser's shape, kept in one place)."""
    try:
        import subprocess
        proc = subprocess.run([hermes_bin, "auth", "list"],
                              capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode != 0:
        return set()
    return set(re.findall(r"^([a-z][\w-]*) \(\d+ credentials?\)",
                          proc.stdout or "", re.M))


def _preflight_readings(effective, *, hermes_bin="hermes"):
    """Live readings for the lane's ``dispatch_gate``: tools via
    ``shutil.which``, models keyed by the providers the configured
    role pins name (``available=None`` — the gate only requires an
    authenticated provider), skills via the readiness collector."""
    tools = {"git": shutil.which("git"), "gh": shutil.which("gh"),
             "hermes": shutil.which(hermes_bin),
             "vercel": shutil.which("vercel"),
             "curl": shutil.which("curl")}
    for tool in ((effective.get("runtime") or {}).get("tools") or {}) \
            .get("allow") or []:
        tools.setdefault(tool, shutil.which(tool))
    providers = set()
    for spec in ((effective.get("runtime") or {}).get("roles") or {}) \
            .values():
        model = str((spec or {}).get("model") or "")
        if "/" in model:
            providers.add(model.split("/", 1)[0])
    authed = _auth_providers(hermes_bin)
    models = {p: {"authenticated": p in authed, "available": None}
              for p in providers}
    skills = readiness_mod._collect_idd(None).get("skills", {})
    return {"tools": tools, "models": models, "skills": skills}


def _open_store(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    return IntakeStore(os.path.join(state_dir, "intake.db"))


def build(args, *, log=None, recover=True):
    """Wire the live driver for ``--repo``; every refusal names its
    reason (:class:`Refusal`) and exits 1."""
    repo = os.path.abspath(args.repo)
    state_dir = _state_dir(args)
    effective = _manifest(repo)
    repo_id = effective["identity"]["repo_id"]
    full_name = (f"{effective['identity']['owner']}/"
                 f"{effective['identity']['name']}")

    registrations = RegistrationStore(
        os.path.join(state_dir, "registrations.json"))
    record = registrations.get(repo_id)
    readiness = (record or {}).get("readiness") or {}
    if record is None or record.get("tombstone") or \
            readiness.get("verdict") != "ready" or \
            readiness.get("dispatch") != "allowed":
        raise Refusal(
            "registration-not-enabled",
            f"repo {repo_id} is not registered+ready "
            f"(readiness={readiness.get('verdict')!r})")

    remote = GhCliRemote(repo_id, full_name, cwd=repo)
    try:
        identity = remote.repository_identity()
    except RemoteError as exc:
        raise Refusal("identity-unreadable", str(exc))
    if str(identity.get("repo_id")) != str(repo_id):
        raise Refusal(
            "identity-mismatch",
            f"remote serves {identity.get('repo_id')!r}, manifest "
            f"binds {repo_id!r}")

    profiles = {}
    hermes_home = Path.home() / ".hermes"
    for role, spec in ((effective.get("runtime") or {})
                       .get("roles") or {}).items():
        profile = (spec or {}).get("profile")
        if profile:
            # Hermes's default profile home is ~/.hermes itself —
            # only named profiles live under profiles/.
            home = hermes_home if profile == "default" else \
                hermes_home / "profiles" / str(profile)
            if not home.is_dir():
                raise Refusal(
                    "missing-worker-profile",
                    f"role {role}: {home} does not exist")
            profiles[role] = profile

    preview_cfg = ((effective.get("endpoint") or {})
                   .get("preview") or {})
    provider = preview_cfg.get("provider")
    if provider == PREVIEW_PROVIDER_NONE:
        # Declared no-preview contract: nothing to deploy; approval and
        # merge rest on required checks + independent review.
        preview_port = NullPreview()
    elif provider == "github-pages":
        build_cfg = preview_cfg.get("build") or {}
        preview_port = GitHubPagesPreview(
            repo_root=repo, full_name=full_name,
            worktree_dir=os.path.join(
                state_dir, "pages", effective["identity"]["name"]),
            build_commands=build_cfg.get("commands") or (),
            output_dir=build_cfg.get("output_dir"),
            base_env=build_cfg.get("base_env"))
        try:
            preview_port._site()
        except PreviewError as exc:
            raise Refusal("pages-not-configured", str(exc))
    else:
        link = Path(repo) / ".vercel" / "project.json"
        project_id = org_id = None
        if link.is_file():
            try:
                link_json = json.loads(
                    link.read_text(encoding="utf-8"))
                project_id = link_json.get("projectId")
                org_id = link_json.get("orgId")
            except (OSError, json.JSONDecodeError):
                pass
        if not project_id or not org_id:
            raise Refusal(
                "vercel-project-unlinked",
                f"{link} lacks projectId/orgId — run `vercel link`")
        preview_port = VercelCliPreview(repo, project_id=project_id,
                                        org_id=org_id)

    store = _open_store(state_dir)
    workspace_root = os.path.join(state_dir, "workspaces",
                                  effective["identity"]["name"])
    readings = _preflight_readings(effective)

    worker = HermesKanbanWorker(
        repo, full_name, profiles, board=args.board,
        acceptance_commands=((effective.get("verification") or {})
                             .get("acceptance_commands") or ()))
    issue_source = GhIssuePoller(repo_id, full_name)
    services = compose(
        store=store, registrations=registrations, effective=effective,
        worker=worker, remote=remote, preview_port=preview_port,
        issue_source=issue_source, workspace_root=workspace_root,
        readings=readings, now=time.time)
    # The lane's heartbeat records poll liveness — the worker owns its
    # beats; the controller only observes them.
    worker._on_poll = lambda h: services.lane.record_heartbeat(
        h.attempt_id)
    driver = Driver(repo, store=store, registrations=registrations,
                    effective=effective, services=services,
                    worker=worker, remote=remote, log=log or _log)
    if recover:
        driver.services.recovery.recover()
    return driver


def _locked(args, log):
    """Hold the exclusive run lock for tick/watch — a second driver
    refuses rather than double-driving the lane."""
    state_dir = _state_dir(args)
    os.makedirs(state_dir, exist_ok=True)
    lock_path = os.path.join(state_dir, "run.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Refusal("driver-already-running",
                      f"another driver holds {lock_path}")
    return fd            # held until process exit


def cmd_tick(args):
    lock_fd = _locked(args, None)
    driver = build(args, log=_log)
    report = driver.tick()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True,
                         default=str))
    return 0


def cmd_watch(args):
    lock_fd = _locked(args, None)
    driver = build(args, log=_log)
    passes = 0
    while True:
        report = driver.tick()
        passes += 1
        if args.json:
            print(json.dumps({"pass": passes, **report}, indent=2,
                             sort_keys=True, default=str))
        if args.max_passes and passes >= args.max_passes:
            return 0
        if args.until_idle and \
                (report.get("lane") or {}).get("outcome") == "idle" \
                and not report["stages"]:
            return 0
        time.sleep(args.interval)


def cmd_status(args):
    # Status is a read-only second process — it must never run the
    # recovery pass a live ``watch`` already owns under the run lock.
    driver = build(args, recover=False)
    print(json.dumps(driver.status(), indent=2, sort_keys=True,
                     default=str))
    return 0


def _decide(args, verdict):
    # Approve/reject run in a second process alongside ``watch`` —
    # recovery is the driver's once-per-process duty under the run
    # lock, never the decision CLI's.
    driver = build(args, recover=False)
    try:
        verified = (driver.remote.actor_identity() or {}).get("login")
    except RemoteError:
        verified = None
    actor = args.actor or verified
    out = driver.services.approval.decide_operator(
        request_id=args.request, github_login=actor,
        verified_login=verified, verdict=verdict,
        detail=getattr(args, "reason", None))
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if out.get("outcome") in ("approved", "rejected") else 1


def cmd_approve(args):
    return _decide(args, "approve")


def cmd_reject(args):
    return _decide(args, "reject")


def cmd_retry(args):
    """Operator-authorized generation retry — the same verified-login
    binding as approve/reject, then the lane's attributable retry.
    Recovery stays the running driver's duty (see _decide)."""
    driver = build(args, recover=False)
    try:
        verified = (driver.remote.actor_identity() or {}).get("login")
    except RemoteError:
        verified = None
    actor = args.actor or verified
    if not actor or \
            (verified and actor.lower() != str(verified).lower()):
        print(json.dumps({"outcome": "denied",
                          "reason": "forged-actor"},
                         indent=2, sort_keys=True))
        return 1
    repo_id = (driver.effective.get("identity") or {}).get("repo_id")
    try:
        out = driver.services.lane.request_retry(
            repo_id, args.issue, authorized_by=actor)
    except Exception as exc:
        print(json.dumps({"outcome": "denied",
                          "reason": type(exc).__name__,
                          "detail": str(exc)[:200]},
                         indent=2, sort_keys=True))
        return 1
    print(json.dumps(out, indent=2, sort_keys=True, default=str))
    return 0 if out.get("outcome") in ("retry-authorized",
                                      "already-queued") else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="factory-run",
        description="The live factory-kit driver — one bounded pass "
                    "over intake→lane→publish→verify→preview→approval→"
                    "merge→recovery")
    parser.add_argument("--profile", default="default",
                        help="operator profile for state paths")
    parser.add_argument("--state-dir",
                        help="state directory override (wins over "
                             "--profile)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("tick", help="one bounded pass")
    p.add_argument("--repo", required=True)
    p.add_argument("--board", help="kanban board slug")
    p.add_argument("--json", action="store_true",
                   help="also print the per-tick report")
    p.set_defaults(func=cmd_tick)

    p = sub.add_parser("watch", help="tick on an interval")
    p.add_argument("--repo", required=True)
    p.add_argument("--board", help="kanban board slug")
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--max-passes", type=int, default=0)
    p.add_argument("--until-idle", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("status", help="per-work durable status")
    p.add_argument("--repo", required=True)
    p.add_argument("--board", help="kanban board slug")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("approve", help="operator decision on a request")
    p.add_argument("--repo", required=True)
    p.add_argument("--board", help="kanban board slug")
    p.add_argument("--request", required=True, help="request id")
    p.add_argument("--actor", help="GitHub login (default: the "
                                   "credential's verified login)")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="operator rejection")
    p.add_argument("--repo", required=True)
    p.add_argument("--board", help="kanban board slug")
    p.add_argument("--request", required=True)
    p.add_argument("--actor")
    p.add_argument("--reason", help="rejection detail")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("retry", help="operator-authorized generation "
                                     "retry for an issue")
    p.add_argument("--repo", required=True)
    p.add_argument("--board", help="kanban board slug")
    p.add_argument("--issue", required=True, type=int)
    p.add_argument("--actor", help="GitHub login (default: the "
                                   "credential's verified login)")
    p.set_defaults(func=cmd_retry)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Refusal as exc:
        print(f"✗ {exc.reason}: {exc.detail or ''}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
