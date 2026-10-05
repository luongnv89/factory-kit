# Decision record: reviewed ownership-aware removal (issue #19, Task 3.5, F08)

- **Status:** Accepted — implemented on `feat/19-3-5-implement-ownership-aware-removal`
- **Scope:** uninstall/removal for installations, integrations, remote
  previews, registrations, active work, and task history.
- **Evidence:** `tests/test_setup_remove.py` (29 fixtures across
  A1–A6); CLI `python3 -m factory_kit.setup uninstall|remove`.

## Decision: mirror the setup plan/apply pipeline for teardown

Removal is the same two-gate discipline as setup (issue #7), run in
reverse with fences:

1. `inspect_removal` — read-only operator-reviewable plan built from
   *recorded* ownership only: `SetupStore` owned files + checksums,
   remote-effect intents, the registration row, and the intake store's
   work/preview inventory. Nothing derives from filesystem guesses; a
   path the ledger does not own can never be an entry.
2. `accept_removal` — binds the `removal_digest` *and* the explicit
   task-history choice (`retain`/`export`/`delete`). There is no
   default: a plan without a choice cannot apply (A4).
3. `apply_removal` — fixed phase order: stop intake durably
   (`RegistrationStore.begin_removal` → readiness `removing`/dispatch
   `denied`), commit the per-work generation fence *before* any
   termination attempt, then confirm worker/descendant exit through
   the terminator seam (the execution lane, or a `callable(work_key)`
   port). Unconfirmed exit → `quarantined` outcome: every destructive
   phase is blocked, the fence records `quarantined`, a
   `removal-quarantine` alert + recovery instructions persist.

## Ownership boundary (what can and cannot be removed)

- **Files:** only ledger-owned paths whose bytes still match the
  recorded checksum. `user-modified` is preserved unless the operator
  binds a specific reviewed `resolution: "discard"` onto the plan
  entry (re-keys the digest). `missing` drops the stale ownership row.
  `unowned`/forged entries are preserved — `_safe_target` also blocks
  traversal/symlink escapes.
- **Remote effects:** recorded removal intents, honestly — live
  provider mutation stays the carried `kanban-live-write` blocker, so
  the durable row is `removal-recorded`, never a claimed deletion.
- **Previews:** removed through the provider port; only recorded
  `deployment_id`s or identity-keyed rediscovery. The port refuses
  foreign resources. An unreachable provider parks the record in the
  visible `cleanup-pending` backlog with a `preview-cleanup-backlog`
  alert — outcome `removed-with-pending`, never a false claim (A5).
- **Registration:** `begin_removal` (intake stop, bound work parked)
  then `remove` → an *identity tombstone* (repo_id + authority_key +
  removed_at/by). The tombstone is provable removal — intake denies,
  every work-bound accessor refuses, and re-registration mints a fresh
  row that quotes `prior_tombstone` (never a silent resurrection).
  A `register()` racing a removal in progress raises — the intake
  stop cannot be silently re-armed.
- **Never in scope:** `.gitissue.yml`, shared Hermes kanban/task
  infrastructure, shared skills/tokens used elsewhere, human
  PRs/branches/provider resources — listed verbatim on every plan
  under `shared_infrastructure.never_removed` (A4).

## History choice (A4)

- `retain` — repo-scoped rows stay inspectable but inert.
- `export` — `IntakeStore.export_history(authority_key)` writes one
  verified JSON document (re-parsed and authority-checked) before the
  tombstone; rows are kept.
- `delete` — `IntakeStore.delete_repo_history(authority_key)` removes
  every row scoped to the one authority key inside a single durable
  transaction; other identities' rows and shared/global tables
  (`meta`, `reconcile_watermark`, `participant_agreements`) are
  untouched. The tombstone survives — it lives in the registration
  store, not the intake history.

## Verification, not claims (A1)

Phase 7 re-proves the outcome: every reported-removed file must be
absent; every `preserved` checksum is re-verified (an operator edit
between plan and apply lands as a `preserved_violation`, surfacing
`removed-with-pending`); the tombstone is read back. `setup_removed`
is recorded in both the setup ledger and (except for `delete`, which
would orphan the row) the intake typed-event trail.
