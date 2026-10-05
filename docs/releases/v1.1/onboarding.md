# v1.1 supported onboarding recipes — issue #31 (task 4.7, A5)

The single supported onboarding path for the recipe-scoped v1.1
deliverable: installation, checkpoint steering, versioned
upgrade/repair/rollback and removal — every leg exercised verbatim by
`tests/release/test_v1_1_walkthrough.py` against the disposable
repository fixture, on top of the per-recipe suites
(`tests/recipes/test_install_recipe.py`,
`tests/recipes/test_upgrade_recipe.py`,
`tests/control/test_steering.py`). Nothing outside this page is
advertised: there is **no install command, no package-manager
distribution, no public channel** — Q8 stays unresolved
(`docs/decisions/q8-distribution.md`).

- **Scope:** one repository, one operator host, one `hermes-kanban`
  runtime, one active task, internal use only.
- **Pins:** exactly the tested set in
  [../../recipes/support-matrix.md](../../recipes/support-matrix.md).
  Unsupported versions report a named blocker and never dispatch.

## 0 — Preconditions (verified by `plan`/`readiness`, not trusted)

Local operator host; Python 3.14.x stdlib only; `git`, `gh`
(scoped assignee login — `gh auth status`), `hermes` ≥ 0.21.5 on
`PATH`; model `openai-codex/gpt-6-luna` reachable; skills
`issue-resolver: 0.19.0` + `issue-pr-review: 0.19.0` installed;
secrets as *references* (`env:` / `hermes-secrets:`) — never literals.

## 1 — Install (additive, reviewed)

```bash
python3 -m factory_kit.setup plan --repo /path/to/project \
    --manifest /path/to/candidate.factory-kit.yml --out plan.json
python3 -m factory_kit.setup apply --repo /path/to/project \
    --plan plan.json --accepted-by <operator> \
    --state setup-state.json --registrations registrations.json
python3 -m factory_kit.setup readiness --repo /path/to/project \
    --readings readings.json --state setup-state.json \
    --registrations registrations.json
python3 -m factory_kit.setup status --repo /path/to/project \
    --state setup-state.json
```

`plan` is read-only (exit 0 = appliable, 1 = named conflicts);
`apply` writes only the reviewed bytes — `preserved[]` checksums
protect unrelated content and user edits byte-for-byte; registration
is created with `readiness: pending` and is enabled **only** when
substantive readiness passes (`verdict: ready`, `dispatch: allowed`).
Detail: [../../recipes/installation.md](../../recipes/installation.md)
— locked by `tests/recipes/test_install_recipe.py`.

## 2 — Checkpoint steering (F09)

Scope changes go through the allowlisted `steer` control command on
the durable channel — never a live injection, never a second
scheduler:

- the pending revision is committed *before* acknowledgement
  (target generation + revised criteria + actor/audit identity);
- a work already at a checkpoint applies inside the same commit; a
  live attempt is fenced **before** any replacement authority issues;
- the replacement binds the revised criteria fingerprint and the
  *remaining* explicit budgets — consumed usage is retained, never
  reset;
- evidence bound to the superseded revision is invalidated;
  completion needs fresh evidence and a fresh revision-bound
  approval;
- a `live`-mode request on a runtime without `steering.live` is
  answered with the checkpoint/restart behavior — no unsupported
  harness is dispatched.

Exercised end-to-end by `tests/control/test_steering.py` (F09 suite)
and as a walkthrough leg in
`tests/release/test_v1_1_walkthrough.py`.

## 3 — Upgrade (F10: plan → migrate → readiness re-pass)

```bash
python3 -m factory_kit.setup upgrade --repo /path/to/project \
    --manifest /path/to/candidate-v2.factory-kit.yml \
    --state setup-state.json --registrations registrations.json \
    --out upgrade.json
python3 -m factory_kit.setup migrate --repo /path/to/project \
    --plan upgrade.json --accepted-by <operator> \
    --state setup-state.json --registrations registrations.json
python3 -m factory_kit.setup readiness --repo /path/to/project \
    --readings readings.json --state setup-state.json \
    --registrations registrations.json
```

`upgrade` is read-only: named conflicts exit 1 and write nothing;
a tampered plan is refused. `migrate` is a fenced staged migration —
`readiness` is `pending`/`denied` until the re-check passes, so
dispatch never runs on a partially migrated configuration.

## 4 — Repair (interrupted migration)

```bash
python3 -m factory_kit.setup repair --repo /path/to/project \
    --repaired-by <operator> --state setup-state.json \
    --registrations registrations.json
```

Every interruption lands between durable stage boundaries: `repair`
restores the validated checkpoint (or parks on a named conflict),
seals the state and keeps dispatch denied until readiness re-passes.
With nothing open it is a named error, never a write.

## 5 — Rollback (F10)

```bash
python3 -m factory_kit.setup rollback --repo /path/to/project \
    --rolled-back-by <operator> --state setup-state.json \
    --registrations registrations.json
```

Restores the previous validated configuration/registration digests
under a fresh audited generation; the restored bytes are re-verified
(`file_violations: []`).

## 6 — Removal (F08: uninstall → remove, no orphaned authority)

```bash
python3 -m factory_kit.setup uninstall --repo /path/to/project \
    --out removal.json --state setup-state.json \
    --registrations registrations.json
python3 -m factory_kit.setup remove --repo /path/to/project \
    --plan removal.json --history retain --accepted-by <operator> \
    --state setup-state.json --registrations registrations.json
```

`uninstall` emits a reviewable removal plan (read-only); `remove`
fences authority first, deletes only recorded factory-owned entries,
preserves user-edited and unrelated files byte-for-byte, and refuses
to run without an explicit `--history retain|export|delete` choice.
The registration row tombstones; consent rows survive a registration.
Full detail:
[../../recipes/upgrade-repair.md](../../recipes/upgrade-repair.md),
[../../recipes/backup-restore.md](../../recipes/backup-restore.md),
[../../recipes/operations.md](../../recipes/operations.md).

## What the walkthrough proves (A5)

`tests/release/test_v1_1_walkthrough.py` runs every leg above on one
clean supported-version fixture — install → steer/cancel →
upgrade+migrate → rollback → interrupted-migration repair → removal —
and asserts, at the end:

- the disposable repo's pre-existing user edits (modified tracked file
  + untracked file) survive install, upgrade, rollback and removal
  **byte-for-byte**;
- the checkpoint steer's revision is durable and bound to its
  generation; the canceled work leaves a committed fence with no
  live attempt;
- `.factory-kit.yml` is removed and the registration is tombstoned —
  **no orphaned execution authority**;
- intake denies new deliveries once the registration is tombstoned.

## Reproduce

```bash
python3 -m unittest tests.release.test_v1_1_walkthrough -v   # A5 legs 1–6
python3 -m unittest tests.recipes.test_install_recipe -v      # install leg
python3 -m unittest tests.recipes.test_upgrade_recipe -v      # F10 legs
python3 -m unittest tests.control.test_steering -v            # F09 leg
```
