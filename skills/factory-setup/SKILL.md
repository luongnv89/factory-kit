---
name: factory-setup
description: "Set up factory-kit in a repo via a reviewed, operator-accepted plan and readiness checks; also upgrade, repair, roll back or remove it. Use when adopting factory-kit or re-checking setup health. Don't use for running the factory, .gitissue.yml or CI."
license: MIT
compatibility: "Requires git, GitHub CLI (gh) and the factory-kit Python package (stdlib-only) importable — run from the kit checkout or an installed profile. Live readiness needs Hermes on PATH."
effort: high
metadata:
  version: 0.4.0
  author: "Luong NGUYEN <luongnv89@gmail.com>"
---

# Factory setup

Issue #7 / Task 2.2 · PRD §3.2 F01 · §4.1 install flow · F08 removal · F10 upgrade.

Adds factory-kit to the repository the skill is invoked in, **additively
and reviewably**, through the `factory-setup` CLI, and upgrades, repairs,
rolls back or removes it the same way. Each repository keeps its own
factory-kit state in `$REPO/.factory-kit/`. factory-kit writes no repository file and records
no remote effect until the operator reads a plan and accepts its digest.
Registration enables nothing until substantive readiness passes.

## When to use

- Adopt factory-kit into a repository (the install flow).
- Re-check setup health: a ledger read-back plus readiness.
- Upgrade to a reviewed manifest, repair an interrupted migration, roll back an upgrade, or remove factory-kit.

Not for: running or dispatching the factory (the `factory_kit.run`
driver), installing or configuring Hermes, writing `.gitissue.yml`, or
editing CI workflows. Setup never writes `.gitissue.yml` and never
replaces existing CI; a CI integration edit appears only as an explicit
plan entry, and the default plan proposes none.

## Prerequisites

- Run `factory-setup --help`. It lists 10 subcommands. If the launcher is not on `PATH`, use `python3 -m factory_kit.setup` from the kit checkout. If both fail, stop and report the error. Driver commands use `factory-run` the same way.
- git, `gh` logged in as the scoped assignee, and Hermes ≥ 0.21.5 on `PATH` for live readiness.

## Target repo and state

Set the target and its state folder first:

```bash
REPO="$(git rev-parse --show-toplevel)"   # the repo the skill is invoked in
STATE="$REPO/.factory-kit"
ST=(--state "$STATE/setup-state.json" --registrations "$STATE/registrations.json")
```

- If the operator names another repository, set `REPO` to that path. If the current directory is not inside a git repository and none is named, stop and ask.
- The CLIs default to the profile state in `~/.hermes/factory-kit/`, which is not this repository's. Pass the state on every command: `"${ST[@]}"` for setup subcommands (`status` takes only `--state`), plus `--intake-db "$STATE/intake.db"` for `upgrade`, `migrate`, `uninstall` and `remove`; `factory-run --state-dir "$STATE"` for the driver. Never pass `--profile`.
- If `$STATE` is missing, create it with its self-ignoring `.gitignore` before Step 1. Read `references/state.md` for the layout, for tracking state in git so it moves with the repo, and for moving machines.

## Repo Sync Before Edits (mandatory)

Sync the **target** repository once, immediately before the plan command
that precedes a write (`plan`, `upgrade`, `uninstall`), so the plan
checksums current bytes:

```bash
branch="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
git -C "$REPO" fetch origin && git -C "$REPO" pull --rebase origin "$branch"
```

- Check `git -C "$REPO" status --porcelain` first. If it prints anything, stop and ask the operator to choose: stash → sync → pop, or skip the sync. factory-setup never stashes operator work on its own. If the operator tracks `.factory-kit/` state and it shows as modified, ask them to commit it first.
- Check `git -C "$REPO" log --oneline @{u}..` too. If it lists unpushed commits, stop and ask — the rebase would rewrite them.
- If `origin` is missing or the pull conflicts, stop and ask the operator.
- Never sync between a plan and its `apply`, `migrate` or `remove`. Pulled bytes are not caught by the exit code — `apply` still exits 0 and only lists them under `preserved.violations`. Re-run the plan instead.
- Skip the sync before `repair` and `rollback`. They restore checkpointed bytes, and pulled changes read as unrecognized bytes that park the migration.

## Operator acceptance gate

Every write is an operator act. Never accept on the operator's behalf.

1. After a plan command, stop and show the operator the plan summary (Step 2).
2. Wait for a reply that explicitly accepts the shown digest. If the reply does not give the operator identity to record, ask for it.
3. Before the write, check that the plan file's digest still equals the digest the operator accepted. If it differs, re-show the plan.
4. Pass the identity as `--accepted-by` (`--repaired-by` for repair, `--rolled-back-by` for rollback).
5. If the reply is anything else — a question, an edit request, no reply — run nothing that writes.

Take the `remove --history retain|export|delete` choice from the operator too. It has no default.

## Choose the branch

| Operator goal | Run |
|---|---|
| Adopt factory-kit | Workflow Steps 1–5 |
| Re-check health | `status` first, then Step 4 if the health precondition holds |
| Upgrade, repair, roll back, or remove | Read `references/lifecycle.md`, then follow its branch |

**Health precondition.** Readiness writes state: it binds the ledger's
`project_id` and refreshes the registration, and on a removed repository
it creates a fresh registration from the tombstone. Run `status` first (Step 5 command). Run Step 4 only when `ownership` lists `.factory-kit.yml`
and `open_migrations` is empty. Otherwise route the operator: no owned
manifest → the adopt flow; an open migration → `references/lifecycle.md`
(repair or rollback).

## Workflow

### Step 1 — Inspect (read-only)

```bash
factory-setup plan --repo "$REPO" \
    [--manifest /path/to/candidate.factory-kit.yml] --out "$STATE/plan.json"
```

- Exit 0: `appliable: true`. Go to Step 2.
- Exit 1: reviewable but blocked. Report every `conflicts[]` item (invalid manifest, `.gitissue.yml` ownership overlap, secret canary) and every `notes[]` line, then stop. With no manifest, `conflicts[]` is empty and `notes[]` says so: ask the operator for a candidate manifest and re-run with `--manifest`.
- Exit 4: report stderr and stop.

`plan` writes nothing: no stash, no reset, no repo file, no remote call.

### Step 2 — Review and accept

Show the operator, in this order:

1. `plan_digest` (first 12 characters) and the `$STATE/plan.json` path.
2. Each `entries[]` item: id, kind and target. Print the proposed `.factory-kit.yml` bytes in full.
3. The `inventory`: existing CI workflows and `.gitissue.yml` ownership.
4. The `preserved[]` count and any `conflicts[]`.

Then apply the operator acceptance gate. The acceptance record binds
`plan_digest`, so apply executes exactly the reviewed bytes.

### Step 3 — Apply (additive + idempotent)

```bash
factory-setup apply --repo "$REPO" --plan "$STATE/plan.json" \
    --accepted-by <operator> "${ST[@]}"
```

- Exit 0: `effective_diff` is empty and `outcome` is `applied` (first run) or `idempotent` (re-run: every entry `unchanged`, no duplicate rows). Check `preserved.violations` anyway: a path outside `.factory-kit/` means bytes outside the plan changed — report it. Paths under `.factory-kit/` are the kit's own state writes; list them separately as expected.
- Exit 1 before any write: digest or acceptance refused. Re-plan.
- Exit 1 with a JSON result (`outcome: conflict` or `partial`): some entries already applied — the registration row and effect intents may exist. Report each entry's `status` plus `conflicts`, then re-plan. Never edit a target to force it — drifted bytes are preserved work.
- Exit 3: unreadable plan, or the ledger belongs to another repository (see Prerequisites).
- Writes land only at planned paths, never through symlinks. Apply never commits: `.factory-kit.yml` stays uncommitted for the operator.
- The registration row starts at `readiness.verdict = "pending"`: present, enabled for nothing.

### Step 4 — Readiness (substantive)

```bash
factory-setup readiness --repo "$REPO" \
    --repo-name <owner>/<name> "${ST[@]}"
```

Always pass `--repo-name` (from `git -C "$REPO" remote get-url origin`):
without it, readiness reads `origin` from the current directory — the kit
checkout — and probes the wrong repository's protection and automation.
It probes Hermes and its version, Kanban verbs including cancellation
(block/reclaim), role models and provider auth, pinned skill revisions,
competing automation, base protection, Telegram transport, and the host.

- Exit 0: `verdict: ready`, `dispatch: allowed`.
- Exit 1: `verdict: not-ready`, `dispatch: denied`. List each `blocker_codes` entry with its `remediation` text. `dispatched` is always `false`. A Python traceback instead of JSON (`RegistrationError`) means a migration or removal is in progress — run `status`; it is not a `not-ready` verdict.
- Exit 3: no manifest at `$REPO/.factory-kit.yml`, or an invalid one. The repository is not set up: route to the adopt flow. Never point readiness at another manifest to get past it.
- `--readings <file>` replays recorded readings and writes their verdict into the registration store. Use it only with scratch `--state` and `--registrations` paths, never `$STATE`; label its verdict as a fixture result.

### Step 5 — Register and read back

Readiness records a `setup_checked` event and refreshes the pending
registration. Check the fields, not only the exit code: `setup_enabled:
true` and `registration.status: enabled` hold only when an accepted plan
created the row **and** every prerequisite passed. `registration.status:
absent` means no accepted plan was applied.

```bash
factory-setup status --repo "$REPO" --state "$STATE/setup-state.json"
```

`--repo` re-verifies each owned file. A user-modified file is preserved
work, not an error.

## Exit codes

| Code | Meaning (all subcommands) |
|---|---|
| 0 | ok — appliable plan, applied, `ready`, or the branch's success outcome |
| 1 | verdict — conflicts, refused digest, `not-ready`, parked, pending |
| 2 | usage error — missing flag, malformed `--resolution` |
| 3 | invalid input — unparsable plan, manifest or state |
| 4 | cannot complete — inspection or readings failed |

Exit 1 with a JSON result is not always a refusal: read `outcome` to learn
what already happened.

## Step reports and final summary

After each step, print a Step Completion Report. Use the exit code and the
step's JSON fields as the checks, never a judgment.

Expected output — example after Step 3:

```
◆ Apply (step 3 of 5)
··································································
  Exit code:          √ 0
  outcome:            √ applied
  effective_diff:     √ [] (empty)
  preserved:          √ 14 checked, 0 violations
  ____________________________
  Result:             PASS
```

Close the run with a concise text summary in the terminal. Keep plans and
reports as JSON files and cite their paths. Lead with the result:

- **Result** — what changed, and whether the goal is complete, partial or blocked.
- **Evidence** — each command run, its exit code, and the fields read (`plan_digest`, `outcome`, `verdict`, `blocker_codes`, `setup_enabled`).
- **Uncertainty** — what was not verified live. Webhook and skill-pin effects stay `recorded-intent` rows until live remote mutation lands: never report them as subscribed upstream. The registration is a local row, not a remote one. Recorded readings prove the fixture, not the host. A `pending` registration is not enabled.
- **Decision** — the pending acceptance (with digest) or `--history` choice, or "No approval needed". Then name each remaining operator action: a blocker's remediation, and committing `.factory-kit.yml` after a first apply.

## Edge cases

- A plan edited after review fails the digest check: apply exits 1 and writes nothing. Re-run `plan`.
- A drifted planned target is a conflict; drift elsewhere shows only in `preserved.violations`. Re-run `plan` in either case; never overwrite.
- A secret canary hit makes the plan non-appliable. Secrets exist only as approved-storage references (`env:`, `hermes-secrets:`); never print a secret or token value.
- A request to `pip install` the kit or `hermes plugins install` it: no install command exists — public distribution is unresolved under Q8 (`docs/decisions/q8-distribution.md`). Say so; never improvise one.
- Branch-specific cases (edited owned files, open migrations, removal outcomes) are in `references/lifecycle.md`.

## Acceptance criteria

To test or grade a run, read `references/evaluation.md`: the correctness
checks, the four understanding criteria, and five test prompts.

## Tested recipe

The recipes hold the tested pins and full walkthroughs (paths relative to
the kit checkout). `tests/recipes/test_install_recipe.py` and
`tests/recipes/test_upgrade_recipe.py` exercise every command, readiness
only with recorded readings — a live readiness run is untested ground:

- [installation](../../docs/recipes/installation.md) — plan → apply →
  readiness → status → uninstall → remove, plus the negative
  walkthrough's named blockers
- [upgrade-repair](../../docs/recipes/upgrade-repair.md) — upgrade,
  migrate, repair, rollback, and their known limits
- [operations](../../docs/recipes/operations.md) — supervision, webhook
  transport, secrets, park/quarantine reasons, limits
- [backup-restore](../../docs/recipes/backup-restore.md) — the
  two-artifact durable pair and the restore rehearsal
- [support-matrix](../../docs/recipes/support-matrix.md) — tested pins
  only; nothing else is claimed
