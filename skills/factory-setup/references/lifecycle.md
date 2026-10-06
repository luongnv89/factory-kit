# Lifecycle branches — upgrade, repair, rollback, remove

Read this file only on these branches; the adopt flow lives in SKILL.md.
Every branch uses SKILL.md's `$REPO`, `$STATE` and `ST` (Target repo and state) and keeps its two rules: the Repo Sync runs before a plan
command (`upgrade`, `uninstall`) and never before `repair` or `rollback`,
and the operator acceptance gate runs before every write. The tested
walkthroughs are `docs/recipes/upgrade-repair.md` and
`docs/recipes/installation.md` §5 in the factory-kit checkout.

Exit 1 with a JSON result is not always a refusal. Several outcomes below
exit 1 **after** side effects landed — always read and report `outcome`.

## Upgrade — sync → `upgrade` → gate → `migrate` → readiness

```bash
factory-setup upgrade --repo "$REPO" \
    --manifest new.factory-kit.yml --out "$STATE/upgrade.json" \
    "${ST[@]}" --intake-db "$STATE/intake.db"
factory-setup migrate --repo "$REPO" \
    --plan "$STATE/upgrade.json" --accepted-by <operator> \
    "${ST[@]}" --intake-db "$STATE/intake.db"
```

- `upgrade` is read-only. Exit 0: appliable. Exit 1: named conflicts — report each one and stop.
- Without `--manifest`, `upgrade` plans a no-change re-affirmation of the installed bytes.
- Show the operator `upgrade_digest` (first 12 characters), `compatibility[]`, each `entries[]` item with its `diff` (on `replace` entries), `files[]`, `conflicts[]` and `migration_steps[]`. Then apply the gate.
- `migrate` succeeds (exit 0) with `outcome: upgraded` or `already-upgraded`. Then run readiness (SKILL.md Step 4, with its health precondition). Dispatch resumes only when readiness passes on the new configuration.
- `outcome: failed` or `interrupted` (exit 1): the migration halted between durable stages and the result carries a `repair_hint`. Report the conflict, then take the repair branch through the gate.
- `outcome: quarantined` (exit 1): an unconfirmed worker parked the migration. Report the `quarantine.work_keys` and ask the operator to resolve the fence before any repair.

## Repair — gate → `repair` → readiness

```bash
factory-setup repair --repo "$REPO" \
    --repaired-by <operator> [--migration-id <id>] "${ST[@]}"
```

- Use it when `status` lists an entry under `open_migrations`.
- Success (exit 0): every repair reports `repaired` or `already-sealed`.
- If on-disk bytes match neither the checkpoint nor the recorded target, the migration parks on that conflict and exits 1. The bytes are preserved. Report the named path and ask the operator how to resolve it.

## Rollback — gate → `rollback` → readiness

```bash
factory-setup rollback --repo "$REPO" \
    --rolled-back-by <operator> [--migration-id <id>] "${ST[@]}"
```

- Restores the checkpointed owned bytes and the previous registration digests. Success (exit 0): `restored` or `already-rolled-back`. `parked` (exit 1) means unrecognized bytes blocked the restore — nothing was partially restored.
- Readiness stays denied until the readiness re-check passes.

## Remove — sync → `uninstall` → gate → `remove`

```bash
factory-setup uninstall --repo "$REPO" --out "$STATE/removal.json" \
    "${ST[@]}" --intake-db "$STATE/intake.db"
factory-setup remove --repo "$REPO" --plan "$STATE/removal.json" \
    --history <operator choice> --accepted-by <operator> [--export-path <file>] \
    "${ST[@]}" --intake-db "$STATE/intake.db"
```

- `uninstall` is read-only. It builds the removal plan from the ownership ledger: ledger-owned bytes plus recorded remote-effect intents.
- Show the operator `removal_digest` (first 12 characters), each `files[]` entry with its `action` (`remove`, `preserve`, `absent`), `remote_effects[]`, `previews[]` and `authority`. Then apply the gate.
- Ask the operator for `--history retain|export|delete`. It has no default; `remove` without it exits 2. A reply like "keep the history" fits both `retain` and `export` — confirm which.
- `export` and `delete` need the intake store; always pass `--intake-db "$STATE/intake.db"` — the CLI default is the profile's store, not this repo's. `export` writes to `--export-path`, default `<state dir>/task-history-<repo_id>.json`.
- `remove` stops intake, commits the active-generation fence before termination, and deletes only ledger-owned bytes. User edits and shared infrastructure survive, and the registration ends as an identity tombstone.
- Outcomes: `removed` or `nothing-to-remove` exit 0. `removed-with-pending` exits 1 after files were deleted and the row tombstoned — report the `pending[]` items (remote effects, previews, files still present) as the operator's cleanup backlog. `quarantined` exits 1 after intake already stopped — report `phases` and ask the operator how to settle the quarantined work.

## Branch edge cases

- A user-edited owned file: in an `uninstall` plan it is `action: preserve` and the plan stays appliable — the file survives removal unless the operator chooses `--resolution file:<path>=discard`. In an `upgrade` plan it is a named conflict; `discard` applies to the manifest and `adopt` to other owned files. Re-run the plan with the operator's choice so the digest binds it.
- An open or parked migration blocks a new `upgrade` until `repair` or `rollback` resolves it.
- An upgrade candidate outside schema `factory_kit: 1`, or with a different `identity.repo_id`, is a named conflict, never a guess.
- Without `--intake-db "$STATE/intake.db"`, live-work fencing cannot see this repo's work: a plan that binds live work rows refuses at apply rather than migrating or removing under live authority.
