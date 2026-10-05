# Recipe: versioned upgrade, repair and rollback (issue #26 / Task 4.2, F10)

The supported walkthrough for moving a *pinned, supported* install to a
reviewed target — and for recovering when a migration is interrupted.
Every command below is executed end-to-end by
`tests/recipes/test_upgrade_recipe.py` against the disposable
repository fixture; nothing else is advertised.

- **Scope:** one repository, one operator host, manifest schema
  `factory_kit: 1` (the only supported schema — see
  [support-matrix.md](support-matrix.md)); internal use only.
- **Invariant:** the operator reviews a digest-bound plan; the applied
  bytes are exactly the reviewed bytes; every interruption lands between
  durable stage boundaries and `repair` restores the validated
  checkpoint or parks on a named conflict — **dispatch never runs on a
  partially migrated configuration**.

## 0 — Preconditions (verified, not trusted)

- The install was applied through `plan`/`apply` (the ownership ledger
  records `.factory-kit.yml` and its checksum) and registered
  (`registrations.json` carries the row with
  `supported_versions: [factory-kit/<pinned>, manifest/1]`).
- The candidate manifest validates against `schema_version 1` and keeps
  the same `identity.repo_id` — an upgrade never moves authority.
- Installed pins must be *supported*: `manifest/N` inside
  `SUPPORTED_SCHEMA_VERSIONS` and `factory-kit/X` not newer than this
  kit. Anything else is a named `installed-version` conflict.

## 1 — Inspect the upgrade (read-only; exit 0 = appliable)

```bash
python3 -m factory_kit.setup upgrade --repo /path/to/project \
    --manifest /path/to/candidate-v2.factory-kit.yml \
    --state <setup-state.json> --registrations <registrations.json> \
    --out upgrade.json
```

The plan shows, before anything mutates:

- `compatibility[]` — every named check: `installed-state`,
  `installed-version`, `manifest`, `schema-path`, `identity`,
  `secret-hygiene`, `ownership`;
- `files[]` — the changed owned files (`action: replace` carries the
  unified `diff`; `action: keep` files are re-checksummed at apply);
- `conflicts[]` — user edits on owned files, missing owned files,
  unsupported pins, identity drift — each named, none silently merged;
- `migration_steps[]` — the durable boundaries the apply commits in
  order (`planned → fenced → checkpointed → files-migrated →
  registration-migrated → validated → provenance-pinned → complete`);
- `rollback.instructions` — the repair/rollback commands below;
- `preserved[]` — checksums of unrelated bytes the apply re-verifies.

Exit codes: `0` appliable · `1` conflicts named · `3` invalid input ·
`4` unable to inspect. A `--resolution file:<path>=discard` (manifest)
or `file:<path>=adopt` (other owned files) is stamped *into* the plan so
the digest binds the operator's reviewed decision for a user-edited
file; without one, an edited owned file is a conflict and the bytes are
preserved.

## 2 — Accept and migrate (fenced, staged, digest-bound)

```bash
python3 -m factory_kit.setup migrate --repo /path/to/project \
    --plan upgrade.json --accepted-by <operator> \
    --state <setup-state.json> --registrations <registrations.json> \
    [--intake-db <intake.db>]
```

- `--accepted-by` *is* the acceptance act — the acceptance binds
  `upgrade_digest`; a plan edited after review fails the digest check
  (exit 1, nothing mutates).
- `fenced` commits **first**: the registration's readiness flips to
  `upgrading`/`denied` and bound work parks *before* any byte moves;
  live intake work rows are fenced and exit-confirmed (an unconfirmed
  worker quarantines the migration — it parks rather than migrating
  under live authority).
- `checkpointed` snapshots every owned file's bytes plus the
  registration's generation digests/effective map — the restore anchor
  for repair and rollback.
- `registration-migrated` mints a new authorized generation bound to the
  reviewed digests (`authorized_by: <operator>`); prior generation rows
  stay byte-identical.
- `validated` re-reads the written bytes and re-validates the manifest
  against the reviewed digests — a mismatch fails the migration into
  repair scope, never into service.
- `provenance-pinned` records the dependency/skill pin set for the new
  generation (`dependency:approved-skill-pins:<policy>` remote-effect
  intent).
- `complete` closes the fence into `pending`/denied readiness: dispatch
  resumes **only** when a fresh `setup_checked` passes on the new
  configuration.

Tested result: `outcome: upgraded`, manifest carries the reviewed bytes,
`active_generation` advanced, readiness `pending`/`denied`.

## 3 — Re-pass readiness (dispatch resumes only here)

```bash
python3 -m factory_kit.setup readiness --repo /path/to/project \
    --readings /path/to/recorded-readings.json \
    --state <setup-state.json> --registrations <registrations.json>
```

On the `ready` fixture: `verdict: ready`, `dispatch: allowed` — the
upgrade is only dispatchable after the substantive gate re-passes on
the migrated configuration.

## 4 — Repair an interrupted migration

```bash
python3 -m factory_kit.setup repair --repo /path/to/project \
    --repaired-by <operator> --state <setup-state.json> \
    --registrations <registrations.json> [--migration-id <id>]
```

An interruption can only land *between* committed stage boundaries —
each boundary is one durable write. `repair` reads the migration record:

- **before `checkpointed`** — no file mutation was committed; the fence
  closes and the migration seals `rolled-back`;
- **`checkpointed` or later** — the checkpoint bytes are written back
  byte-for-byte and a fresh generation re-binds the *previous*
  digests, then the restored state is re-verified (`repaired`);
- **unrecognized on-disk bytes** (user work that is neither checkpoint
  nor recorded target) — the migration **parks** with that specific
  conflict; bytes are preserved and the parked fence marker keeps
  `register` from re-arming dispatch until the conflict is resolved
  and repair/rollback re-runs.

Tested: every stage boundary repairs to the validated state; a
concurrent unrecognized edit parks with `user-edit` on the named path.

## 5 — Roll back a completed upgrade

```bash
python3 -m factory_kit.setup rollback --repo /path/to/project \
    --rolled-back-by <operator> --state <setup-state.json> \
    --registrations <registrations.json> [--migration-id <id>]
```

The documented rollback (A5): checkpointed owned bytes restored
byte-for-byte; the registration mints a new audited generation bound to
the previous config/policy digests (`checkpoint-restore`); readiness
closes to `restored`/denied until the re-check passes; the migration
seals `rolled-back`. Re-running is idempotent (`already-rolled-back`);
a checkpoint blocked by unrecognized bytes parks instead of partially
restoring.

## 6 — Removal after upgrade/rollback

```bash
python3 -m factory_kit.setup uninstall --repo /path/to/project \
    --out removal.json --state <setup-state.json> \
    --registrations <registrations.json>
python3 -m factory_kit.setup remove --repo /path/to/project \
    --plan removal.json --history retain --accepted-by <operator> \
    --state <setup-state.json> --registrations <registrations.json>
```

Removal is unchanged: the ownership ledger reflects whichever bytes the
last sealed migration left, edited owned files are preserved unless the
operator stamps a reviewed `discard`, and the registration tombstones —
no orphaned authority whether the last operation was an install, an
upgrade or a rollback (tested end-to-end in
`test_upgrade_then_remove_leaves_no_orphaned_authority`).

## Known limits (named, not hidden)

- Schema migrations: `factory_kit: 1` is the only supported schema;
  the registry (`factory_kit/config/migrations.py`) has no v1→v2
  transform because v2 does not exist — an out-of-schema candidate is a
  named conflict, never a guess.
- Upgrades target one install at a time; an open or parked migration
  blocks any further upgrade until `repair`/`rollback` resolves it.
- `adopt` resolutions re-checksum edited bytes into the ledger; only the
  manifest can be `discard`-ed (the ledger stores checksums, not old
  content — non-manifest originals live only in the checkpoint taken at
  migration time).
- Live-work fencing needs `--intake-db`; without it a plan that binds
  live work rows refuses at apply rather than migrating under live
  authority.
- Rollback restores *recorded* state; it never reconstructs bytes that
  were never checkpointed (e.g. files created during the migration
  window that are not owned stay exactly where they are).

## Exit-code vocabulary

`0` appliable / upgraded / repaired / restored · `1` refused verdict
(conflicts, not appliable, quarantined, parked) · `2` usage error ·
`3` invalid input · `4` unable to complete.
