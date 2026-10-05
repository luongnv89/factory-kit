#!/usr/bin/env python3
"""``factory-setup`` CLI — the operator surface for reviewed setup.

PRD §4.1 install flow; issue #7 / Task 2.2. Wired by the
``skills/factory-setup`` skill; usable directly:

    python3 -m factory_kit.setup plan --repo /path/to/project
    python3 -m factory_kit.setup apply --repo . --plan plan.json \\
        --accepted-by <operator>
    python3 -m factory_kit.setup readiness --repo . [--readings rec.json]
    python3 -m factory_kit.setup status [--state setup-state.json]

Exit codes (shared gi-* vocabulary):

    0  ok — plan produced / applied / readiness ``ready``
    1  verdict — not appliable, apply refused, readiness ``not-ready``
    2  usage error
    3  invalid input — unparsable plan/manifest/state
    4  cannot complete — the command could not run to an answer

State paths default under the operator profile
(``~/.hermes/profiles/<profile>/factory-kit/``); ``--state`` and
``--registrations`` override them for tests and alternate profiles.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from factory_kit import VERSION
from factory_kit.config.registration import RegistrationStore
from factory_kit.config.schema import ConfigError, load_manifest_file
from . import apply as apply_mod
from . import ownership, plan as plan_mod, readiness as readiness_mod


def _emit(payload):
    print(json.dumps(payload, indent=2, sort_keys=True))


def _state_path(args):
    if args.state:
        return args.state
    root = Path.home() / ".hermes" / "profiles" / args.profile / \
        "factory-kit"
    return str(root / "setup-state.json")


def _registrations_path(args):
    if args.registrations:
        return args.registrations
    root = Path.home() / ".hermes" / "profiles" / args.profile / \
        "factory-kit"
    return str(root / "registrations.json")


def _load_plan(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ cannot read plan {path}: {exc}", file=sys.stderr)
        return None


def _load_effective(args):
    """The validated manifest behind a plan or readiness run."""
    manifest_path = args.manifest or \
        str(Path(args.repo) / plan_mod.MANIFEST_PATH)
    try:
        return load_manifest_file(manifest_path)
    except FileNotFoundError:
        print(f"✗ no manifest at {manifest_path} — supply --manifest",
              file=sys.stderr)
    except ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
    except Exception as exc:  # ParseError, UnicodeDecodeError
        print(f"✗ cannot parse {manifest_path}: {exc}", file=sys.stderr)
    return None


def cmd_plan(args):
    try:
        plan = plan_mod.inspect(
            args.repo,
            manifest_source=args.manifest)
    except (plan_mod.PlanError, OSError) as exc:
        print(f"✗ inspection failed: {exc}", file=sys.stderr)
        return 4
    plan["review_hint"] = (
        "review entries[] — every proposed byte is inline; accept with "
        "`python3 -m factory_kit.setup apply --plan <file> "
        "--accepted-by <you>`")
    if args.out:
        Path(args.out).write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"○ plan written to {args.out} "
              f"(digest {plan['plan_digest'][:12]}…)", file=sys.stderr)
    else:
        _emit(plan)
    return 0 if plan["appliable"] else 1


def cmd_apply(args):
    plan = _load_plan(args.plan)
    if plan is None:
        return 3
    try:
        accepted = plan_mod.accept(plan, args.accepted_by)
    except plan_mod.PlanError as exc:
        print(f"✗ plan refused: {exc}", file=sys.stderr)
        return 1
    store = ownership.SetupStore(_state_path(args))
    if plan["manifest"].get("effective"):
        try:
            store.set_project_id(
                plan["manifest"]["effective"]["identity"]["repo_id"])
        except ownership.OwnershipError as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 3
    registration_store = RegistrationStore(_registrations_path(args))
    schema_version = (plan["manifest"].get("effective") or {}) \
        .get("factory_kit", 1)
    try:
        result = apply_mod.apply_plan(
            accepted, args.repo, store,
            registration_store=registration_store,
            supported_versions=[f"factory-kit/{VERSION}",
                                f"manifest/{schema_version}"])
    except apply_mod.SetupError as exc:
        print(f"✗ apply refused: {exc}", file=sys.stderr)
        return 1
    except ownership.OwnershipError as exc:
        print(f"✗ setup state error: {exc}", file=sys.stderr)
        return 3
    _emit(result)
    return 0 if not result["effective_diff"] else 1


def cmd_readiness(args):
    effective = _load_effective(args)
    if effective is None:
        return 3
    readings = None
    if args.readings:
        try:
            readings = json.loads(
                Path(args.readings).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"✗ cannot read readings {args.readings}: {exc}",
                  file=sys.stderr)
            return 4
    store = ownership.SetupStore(_state_path(args))
    registration_store = RegistrationStore(_registrations_path(args))
    collector = None
    if readings is None:
        def collector():
            return readiness_mod.collect_readings(
                repo=args.repo_name,
                hermes_bin=args.hermes_bin,
                hermes_home=args.hermes_home)
    report = readiness_mod.run_setup_checked(
        project_id=effective["identity"]["repo_id"],
        effective=effective,
        store=store,
        registration_store=registration_store,
        supported_versions=[f"factory-kit/{VERSION}",
                            f"manifest/{effective['factory_kit']}"],
        readings=readings, collector=collector)
    _emit(report)
    return 0 if report["verdict"] == "ready" else 1


def cmd_status(args):
    try:
        store = ownership.SetupStore(_state_path(args))
    except ownership.OwnershipError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    report = {
        "project_id": store.project_id,
        "ownership": store.owned_files(),
        "remote_effects": store.effects(),
        "applied_plans": store.applied_plans(),
        "events": store.events(),
    }
    if args.repo:
        # Re-verify each owned checksum on disk: user-modified is a
        # finding (preserved work), never an error.
        report["ownership_state"] = {
            path: store.verify_file(args.repo, path)
            for path in store.owned_files()}
    _emit(report)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="factory-setup",
        description="Reviewed additive setup and substantive readiness "
                    "(issue #7 / F01)")
    parser.add_argument("--profile", default="default",
                        help="operator profile for state paths")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", help="inspect a repository and emit the "
                                    "reviewable additive plan (read-only)")
    p.add_argument("--repo", required=True, help="repository root")
    p.add_argument("--manifest", help="candidate .factory-kit.yml "
                                      "(default: <repo>/.factory-kit.yml)")
    p.add_argument("--out", help="write the plan JSON here instead of "
                                 "stdout")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="apply an accepted plan "
                                     "(additive + idempotent)")
    p.add_argument("--repo", required=True)
    p.add_argument("--plan", required=True, help="plan JSON from `plan`")
    p.add_argument("--accepted-by", required=True,
                   help="operator identity accepting the reviewed plan")
    p.add_argument("--state", help="setup-state.json override")
    p.add_argument("--registrations", help="registrations.json override")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("readiness", help="run substantive readiness and "
                                         "record setup_checked")
    p.add_argument("--repo", required=True)
    p.add_argument("--repo-name", help="owner/name for repo probes "
                                       "(default: origin remote)")
    p.add_argument("--manifest", help="manifest path")
    p.add_argument("--readings", help="recorded readings JSON "
                                      "(default: live collection)")
    p.add_argument("--hermes-bin", default="hermes")
    p.add_argument("--hermes-home")
    p.add_argument("--state")
    p.add_argument("--registrations")
    p.set_defaults(func=cmd_readiness)

    p = sub.add_parser("status", help="dump the setup ledger")
    p.add_argument("--repo", help="re-verify owned files against this "
                                  "repository root")
    p.add_argument("--state")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)
