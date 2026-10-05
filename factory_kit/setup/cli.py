#!/usr/bin/env python3
"""``factory-setup`` CLI — the operator surface for reviewed setup.

PRD §4.1 install flow; issue #7 / Task 2.2. Wired by the
``skills/factory-setup`` skill; usable directly:

    python3 -m factory_kit.setup plan --repo /path/to/project
    python3 -m factory_kit.setup apply --repo . --plan plan.json \\
        --accepted-by <operator>
    python3 -m factory_kit.setup readiness --repo . [--readings rec.json]
    python3 -m factory_kit.setup status [--state setup-state.json]
    python3 -m factory_kit.setup uninstall --repo . [--out plan.json]
    python3 -m factory_kit.setup remove --repo . --plan plan.json \\
        --history retain|export|delete --accepted-by <operator>
    python3 -m factory_kit.setup upgrade --repo . --manifest new.yml \\
        [--out upgrade.json]         # read-only upgrade plan (F10)
    python3 -m factory_kit.setup migrate --repo . --plan upgrade.json \\
        --accepted-by <operator>     # fenced staged migration
    python3 -m factory_kit.setup repair --repo . --repaired-by <op>
    python3 -m factory_kit.setup rollback --repo . --rolled-back-by <op>

Exit codes (shared gi-* vocabulary):

    0  ok — plan produced / applied / readiness ``ready``
    1  verdict — not appliable, apply refused, readiness ``not-ready``
    2  usage error
    3  invalid input — unparsable plan/manifest/state
    4  cannot complete — the command could not run to an answer

State paths default under the operator profile
(``~/.hermes/profiles/<profile>/factory-kit/``); ``--state`` and
``--registrations`` override them for tests and alternate profiles.
``remove`` additionally accepts ``--intake-db`` for the durable intake
store — required when ``--history export|delete`` must touch task
history (F08 A4). ``upgrade``/``migrate`` accept the same flag so the
generation fence can commit over live work (F10 A2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from factory_kit import VERSION
from factory_kit.config.registration import RegistrationStore
from factory_kit.config.schema import ConfigError, load_manifest_file
from factory_kit.durable.store import IntakeStore

from . import apply as apply_mod
from . import ownership
from . import plan as plan_mod
from . import readiness as readiness_mod
from . import remove as remove_mod
from . import repair as repair_mod
from . import upgrade as upgrade_mod


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


def _intake_store(args):
    """Open the durable intake store when a path resolves, else None."""
    path = getattr(args, "intake_db", None)
    if not path:
        root = Path.home() / ".hermes" / "profiles" / args.profile / \
            "factory-kit"
        candidate = root / "intake.db"
        path = str(candidate) if candidate.is_file() else None
    if path is None:
        return None
    return IntakeStore(path)


def cmd_uninstall(args):
    """Read-only: emit the reviewable removal plan (F08 A1)."""
    try:
        store = ownership.SetupStore(_state_path(args))
    except ownership.OwnershipError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    try:
        plan = remove_mod.inspect_removal(
            args.repo, store,
            registration_store=RegistrationStore(
                _registrations_path(args)),
            intake_store=_intake_store(args))
    except (remove_mod.RemovalError, OSError) as exc:
        print(f"✗ removal inspection failed: {exc}", file=sys.stderr)
        return 4
    # Operator-supplied reviewed resolutions — stamped INTO the plan so
    # the digest re-keys over them and acceptance binds them (A2).
    for spec in args.resolution or []:
        if "=" not in spec:
            print(f"✗ --resolution expects entry-id=value, got "
                  f"{spec!r}", file=sys.stderr)
            return 2
        entry_id, resolution = spec.split("=", 1)
        matched = False
        for entry in plan["files"]:
            if entry["id"] == entry_id:
                entry["resolution"] = resolution
                matched = True
        if not matched:
            print(f"✗ --resolution names unknown entry {entry_id!r}",
                  file=sys.stderr)
            return 2
    if args.resolution:
        plan["removal_digest"] = remove_mod.removal_digest(plan)
    plan["review_hint"] = (
        "review files[]/remote_effects[]/previews[]/authority — apply "
        "with `python3 -m factory_kit.setup remove --plan <file> "
        "--history retain|export|delete --accepted-by <you>`")
    if args.out:
        Path(args.out).write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"○ removal plan written to {args.out} "
              f"(digest {plan['removal_digest'][:12]}…)", file=sys.stderr)
    else:
        _emit(plan)
    return 0 if plan["appliable"] else 1


def cmd_remove(args):
    plan = _load_plan(args.plan)
    if plan is None:
        return 3
    try:
        accepted = remove_mod.accept_removal(
            plan, args.accepted_by, history=args.history)
    except remove_mod.RemovalError as exc:
        print(f"✗ removal plan refused: {exc}", file=sys.stderr)
        return 1
    try:
        store = ownership.SetupStore(_state_path(args))
    except ownership.OwnershipError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    try:
        result = remove_mod.apply_removal(
            accepted, args.repo, store,
            registration_store=RegistrationStore(
                _registrations_path(args)),
            intake_store=_intake_store(args),
            export_path=args.export_path)
    except remove_mod.RemovalError as exc:
        print(f"✗ removal refused: {exc}", file=sys.stderr)
        return 1
    except ownership.OwnershipError as exc:
        print(f"✗ setup state error: {exc}", file=sys.stderr)
        return 3
    _emit(result)
    return 0 if result["outcome"] in ("removed", "nothing-to-remove") \
        else 1


def cmd_upgrade(args):
    """Read-only: emit the reviewable upgrade plan (F10 A1)."""
    try:
        store = ownership.SetupStore(_state_path(args))
    except ownership.OwnershipError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    # Operator-supplied reviewed resolutions — parsed BEFORE inspection
    # so they generate inside the plan and the digest binds them (A4).
    resolutions = {}
    for spec in args.resolution or []:
        if "=" not in spec:
            print(f"✗ --resolution expects entry-id=value, got "
                  f"{spec!r}", file=sys.stderr)
            return 2
        entry_id, resolution = spec.split("=", 1)
        resolutions[entry_id] = resolution
    try:
        plan = upgrade_mod.inspect_upgrade(
            args.repo, store,
            manifest_source=args.manifest,
            registration_store=RegistrationStore(
                _registrations_path(args)),
            intake_store=_intake_store(args),
            resolutions=resolutions or None)
    except (upgrade_mod.UpgradeError, OSError) as exc:
        print(f"✗ upgrade inspection failed: {exc}", file=sys.stderr)
        return 4
    known = {entry["id"] for entry in plan["files"]}
    for entry_id in resolutions:
        if entry_id not in known:
            print(f"✗ --resolution names unknown entry {entry_id!r}",
                  file=sys.stderr)
            return 2
    plan["review_hint"] = (
        "review files[]/entries[]/compatibility[]/migration_steps[]/"
        "rollback — apply with `python3 -m factory_kit.setup migrate "
        "--plan <file> --accepted-by <you>`")
    if args.out:
        Path(args.out).write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"○ upgrade plan written to {args.out} "
              f"(digest {plan['upgrade_digest'][:12]}…)",
              file=sys.stderr)
    else:
        _emit(plan)
    return 0 if plan["appliable"] else 1


def cmd_migrate(args):
    """Apply an accepted upgrade plan across durable boundaries (A2)."""
    plan = _load_plan(args.plan)
    if plan is None:
        return 3
    try:
        accepted = upgrade_mod.accept_upgrade(plan, args.accepted_by)
    except upgrade_mod.UpgradeError as exc:
        print(f"✗ upgrade plan refused: {exc}", file=sys.stderr)
        return 1
    try:
        store = ownership.SetupStore(_state_path(args))
    except ownership.OwnershipError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    try:
        result = upgrade_mod.apply_upgrade(
            accepted, args.repo, store,
            registration_store=RegistrationStore(
                _registrations_path(args)),
            intake_store=_intake_store(args))
    except upgrade_mod.UpgradeError as exc:
        print(f"✗ upgrade refused: {exc}", file=sys.stderr)
        return 1
    except ownership.OwnershipError as exc:
        print(f"✗ setup state error: {exc}", file=sys.stderr)
        return 3
    _emit(result)
    return 0 if result["outcome"] in ("upgraded", "already-upgraded") \
        else 1


def cmd_repair(args):
    """Restore interrupted migrations to their validated checkpoint —
    or park them with a specific conflict (F10 A3)."""
    try:
        store = ownership.SetupStore(_state_path(args))
    except ownership.OwnershipError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    try:
        result = repair_mod.repair_migration(
            args.repo, store,
            registration_store=RegistrationStore(
                _registrations_path(args)),
            migration_id=args.migration_id,
            repaired_by=args.repaired_by)
    except repair_mod.RepairError as exc:
        print(f"✗ repair: {exc}", file=sys.stderr)
        return 1
    except ownership.OwnershipError as exc:
        print(f"✗ setup state error: {exc}", file=sys.stderr)
        return 3
    _emit(result)
    outcomes = {r["outcome"] for r in result.get("repairs", [result])}
    return 0 if outcomes <= {"repaired", "already-sealed"} else 1


def cmd_rollback(args):
    """Restore the previous validated registration/configuration —
    the documented F10 rollback (A5)."""
    try:
        store = ownership.SetupStore(_state_path(args))
    except ownership.OwnershipError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3
    try:
        result = upgrade_mod.rollback_upgrade(
            args.repo, store,
            RegistrationStore(_registrations_path(args)),
            migration_id=args.migration_id,
            rolled_back_by=args.rolled_back_by)
    except (upgrade_mod.UpgradeError, ownership.OwnershipError) as exc:
        print(f"✗ rollback: {exc}", file=sys.stderr)
        return 1
    _emit(result)
    return 0 if result["outcome"] in ("restored",
                                      "already-rolled-back") else 1


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
        "migrations": store.migrations(),
        "open_migrations": sorted(store.open_migrations()),
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

    p = sub.add_parser("uninstall", help="emit the reviewable removal "
                                         "plan (read-only, F08)")
    p.add_argument("--repo", required=True)
    p.add_argument("--state")
    p.add_argument("--registrations")
    p.add_argument("--intake-db", help="durable intake store path "
                                       "(default: profile intake.db "
                                       "when present)")
    p.add_argument("--out", help="write the plan JSON here instead of "
                                 "stdout")
    p.add_argument("--resolution", action="append", metavar="ID=VALUE",
                   help="stamp a reviewed resolution onto a file "
                        "entry (e.g. file:.factory-kit.yml=discard) — "
                        "the digest re-keys over it so acceptance "
                        "binds the resolution")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("remove", help="apply an accepted removal plan "
                                      "(fenced + ownership-aware)")
    p.add_argument("--repo", required=True)
    p.add_argument("--plan", required=True,
                   help="removal plan JSON from `uninstall`")
    p.add_argument("--history", required=True,
                   choices=list(remove_mod.HISTORY_CHOICES),
                   help="explicit task-history choice (F08 A4)")
    p.add_argument("--accepted-by", required=True,
                   help="operator identity accepting the reviewed plan")
    p.add_argument("--export-path", help="where --history export writes "
                                         "the repo-scoped history JSON")
    p.add_argument("--state")
    p.add_argument("--registrations")
    p.add_argument("--intake-db")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("upgrade", help="emit the reviewable versioned "
                                       "upgrade plan (read-only, F10)")
    p.add_argument("--repo", required=True)
    p.add_argument("--manifest", help="candidate .factory-kit.yml "
                                      "(default: the installed bytes — "
                                      "a re-affirming no-change plan)")
    p.add_argument("--state")
    p.add_argument("--registrations")
    p.add_argument("--intake-db")
    p.add_argument("--out", help="write the plan JSON here instead of "
                                 "stdout")
    p.add_argument("--resolution", action="append", metavar="ID=VALUE",
                   help="stamp a reviewed resolution onto a file "
                        "entry (e.g. file:.factory-kit.yml=discard) — "
                        "the digest re-keys over it so acceptance "
                        "binds the resolution")
    p.set_defaults(func=cmd_upgrade)

    p = sub.add_parser("migrate", help="apply an accepted upgrade plan "
                                       "(fenced staged migration, F10)")
    p.add_argument("--repo", required=True)
    p.add_argument("--plan", required=True,
                   help="upgrade plan JSON from `upgrade`")
    p.add_argument("--accepted-by", required=True,
                   help="operator identity accepting the reviewed plan")
    p.add_argument("--state")
    p.add_argument("--registrations")
    p.add_argument("--intake-db")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("repair", help="restore interrupted migrations "
                                      "to the validated checkpoint or "
                                      "park with a conflict (F10)")
    p.add_argument("--repo", required=True)
    p.add_argument("--migration-id", help="repair only this migration "
                                          "(default: every open "
                                          "migration)")
    p.add_argument("--repaired-by", required=True,
                   help="operator identity performing the repair")
    p.add_argument("--state")
    p.add_argument("--registrations")
    p.set_defaults(func=cmd_repair)

    p = sub.add_parser("rollback", help="restore the previous validated "
                                        "registration/configuration "
                                        "(documented F10 rollback)")
    p.add_argument("--repo", required=True)
    p.add_argument("--migration-id", help="roll back this migration "
                                          "(default: the most recent "
                                          "checkpointed one)")
    p.add_argument("--rolled-back-by", required=True,
                   help="operator identity performing the rollback")
    p.add_argument("--state")
    p.add_argument("--registrations")
    p.set_defaults(func=cmd_rollback)

    args = parser.parse_args(argv)
    return args.func(args)
