# Recipe: backup/restore rehearsal (issue #23 / Task 3.9, A3/A6)

The supported persistence mechanism and the rehearsal that proves it.
The procedure is exercised verbatim by
`tests/recovery/test_backup_restore.py` (8 tests) — run it yourself:

```bash
python3 -m unittest tests.recovery.test_backup_restore -v
```

## Backup scope — exactly two artifacts

| Artifact | Path | What it carries |
|---|---|---|
| Intake store | `<state-dir>/in.db` (`~/.hermes/factory-kit/` for the `default` profile, `~/.hermes/profiles/<profile>/factory-kit/` for named profiles) | work/task/attempt/fence identities, delivery dedup, control records, event trail, approval requests + append-only decisions, publication intents, notification outbox, queue/lane state |
| Registration | `<state-dir>/registration.json` | per-repo registration row, active generation, readiness verdict, recorded work keys — the authority boundary |

- `in.db` is one SQLite file with `synchronous=FULL` and a rollback
  journal: **a cleanly closed file is the complete committed state** —
  no WAL sidecar, no extra files to chase.
- Hermes-owned state (task/attempt/kanban rows in `~/.hermes/kanban.db`)
  is outside kit backup scope by design — the F02 boundary keeps task
  identity in Hermes and delivery/approval/intent identity in the kit.

## Backup procedure (demonstrated)

1. Quiesce the writer — stop the controller process (the store holds an
   open connection; a live copy risks a torn journal).
2. Byte-copy `in.db` and `registration.json` into the backup location.
   `shutil.copy2` in the rehearsal; any faithful copy (cp/rsync) is the
   same contract.
3. Record the kit version + manifest digest alongside — the restored
   pair must run against the same schema generation.

## Secret store — restored separately, never in the pair

Secrets exist only as **references** (`env:FACTORY_KIT_WEBHOOK_SECRET`,
`hermes-secrets:factory-kit/telegram-bot-token`). The backup therefore
contains no secret bytes at all — that's the design. On restore,
re-provision the referenced values in the operator environment / Hermes
secret store *independently*; missing secret material surfaces as a
readiness/transport blocker, never as a silent half-restore.

## Restore procedure (demonstrated)

1. Copy `in.db` + `registration.json` into a fresh deployment directory.
2. Reopen and rebuild services; run `RecoveryService.recover()` — the
   startup pass settles every row.
3. Read the report: `resumed` / `parked` / `quarantined` / `deferred`,
   `intents` outcomes, `sweep.fenced`, `within_deadline`.

## What the rehearsal proves (all asserted in the test)

- **Identity preservation:** work_key, reserved/bound `task_id`,
  `authority_key`, attempt ID, fence state, queue position survive
  byte-for-byte.
- **Records survive:** delivery dedup (a replayed `delivery_id` returns
  `deduplicated`, never a second task), the pending notification row,
  approval requests and their append-only decision history, and
  publication intents.
- **Remote is rechecked before dispatch:** a pending publish intent
  reconciles against the remote by identity — one match links the
  existing PR (`linked_pr` set, intent `linked`), *zero* new `pr-publish`
  calls. Restored state alone is never proof of remote effect.
- **Ambiguity parks, never duplicates:** two remote objects claiming the
  identity → `parked: duplicate-remote-pr` + alert; none →
  `parked: ambiguous-outcome`.
- **Authority never revives:** a `consumed` approval denies replay
  (`reason: replayed`); an `approved` grant that expired across the
  backup window denies `expired` and records the lapse. Consumption
  re-evaluates expiry/fence/drift at spend time — the durable row is
  evidence, not authority.

## Corrupt and incomplete restores (A6)

| Damage | Demonstrated outcome |
|---|---|
| Truncated/garbage `in.db` | `IntakeStoreError: cannot open intake store <path>` — the restore refuses at open; no partial state is ever acted on |
| `in.db` restored without `registration.json` | Every durable row parks `generation-superseded` (registration is the authority boundary) + `recovery-parked` alert; nothing queues or dispatches |

Both legs report a concrete blocker; dispatch is structurally impossible
without the durable pair.
