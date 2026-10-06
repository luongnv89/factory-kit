# Recipe: tested installation and removal (issue #23 / Task 3.9, A1/A6)

This is the **single supported support recipe**. Every command below was
executed against the implementation it names and is locked by
`tests/recipes/test_install_recipe.py` (12 tests: the full CLI walkthrough
plus every negative leg). Nothing else is advertised — there is no
`install` command, no package manager syntax, no public distribution
channel (see `docs/decisions/q8-distribution.md`).

- **Scope:** one repository, one operator host, one `hermes-kanban`
  runtime, internal use only.
- **Compatibility:** exactly the pin set in
  [support-matrix.md](support-matrix.md). Unsupported versions report a
  named blocker and never dispatch.
- **CI boundary:** the kit never replaces target-project CI. Apply writes
  only the planned `.factory-kit.yml`; existing workflows are inventoried,
  checksummed and preserved byte-for-byte.

## 0 — Preconditions (all verified by `plan`/`readiness`, not by trust)

| Slot | Tested value |
|---|---|
| Host | Local operator host M1 — macOS 27.0.0 arm64 (Darwin/Linux supported) |
| Python | 3.14.7, stdlib only — no dependencies to install |
| Tools | git 2.55.0, GitHub CLI 2.100.0 (`gh auth status` → scoped assignee login), Hermes ≥ 0.21.5 on `PATH` |
| Runtime | `hermes-kanban` profile worker — the only paved worker path |
| Model | `openai-codex / gpt-6-luna` (pooled OAuth logged in). `dspark` is optional-when-reachable only |
| Skills | `issue-resolver: 0.19.0`, `issue-pr-review: 0.19.0` |
| Credential | Assignee profile's own `gh` login; webhook secret via `env:` reference; Telegram token via `hermes-secrets:` reference — never literal values |
| Merge | Squash, human-approved, expected-head recheck; auto-merge disabled |

## 1 — Inspect (read-only; exit 0 = appliable, 1 = conflicts named)

```bash
python3 -m factory_kit.setup plan --repo /path/to/project \
    --manifest /path/to/candidate.factory-kit.yml --out plan.json
```

- `entries[]` carry every proposed byte inline (manifest content, webhook
  subscription, registration row, skill-pin set); `preserved[]` checksums
  unrelated bytes it promises not to touch; `conflicts[]` names blockers.
- A schema-incompatible manifest (e.g. `factory_kit: 99`) produces a
  reviewable but **non-appliable** plan — exit 1, nothing written
  (test `test_incompatible_manifest_not_appliable`).
- Tested result: plan digest issued, four entries, zero conflicts.

## 2 — Review, then apply (additive, idempotent; digest-bound)

```bash
python3 -m factory_kit.setup apply --repo /path/to/project \
    --plan plan.json --accepted-by <operator> \
    --state ~/.hermes/factory-kit/setup-state.json \
    --registrations ~/.hermes/factory-kit/registrations.json
  # (named profiles: ~/.hermes/profiles/<profile>/factory-kit/…)
```

- `--accepted-by` *is* the acceptance act: the acceptance record binds
  `plan_digest`; a plan file edited after review fails the digest check
  and apply refuses — exit 1, nothing written
  (test `test_apply_refuses_tampered_plan`).
- Tested result: `outcome: applied`, empty `effective_diff`, every
  preserved checksum re-verified, developer dirty/untracked work intact.
- Re-applying the same plan is a no-op — all entries report `unchanged`
  (test `test_reapply_is_idempotent`).

## 3 — Readiness (substantive; exit 0 = ready/dispatch allowed)

```bash
python3 -m factory_kit.setup readiness --repo /path/to/project \
    --readings /path/to/recorded-readings.json \
    --state <setup-state.json> --registrations <registrations.json>
```

Probes the real prerequisites — Hermes executable + version, Kanban
verbs + webhook surface, cancellation (block/reclaim), the manifest's
role models + provider authentication, pinned skill revisions, competing
automation owner, base protection vs required checks, Telegram transport,
host. Each failure is a named blocker; `dispatch` stays `denied` and
`dispatched` is structurally `false`.

Tested result on the `ready` fixture: `verdict: ready`,
`dispatch: allowed`, `setup_enabled: true`, registration flips
`pending → enabled`, `version_set` records `kit/runtime/schema/hermes`
pins.

## 4 — Status (operator ledger read-back)

```bash
python3 -m factory_kit.setup status --repo /path/to/project \
    --state <setup-state.json>
```

Reads back project identity, the ownership ledger, recorded remote-effect
intents and events. Tested result: `project_id`, `ownership` naming
`.factory-kit.yml`, non-empty `remote_effects` and `events`.

## 5 — Removal (ownership-aware; reviewable, then applied)

```bash
python3 -m factory_kit.setup uninstall --repo /path/to/project \
    --out removal.json --state <setup-state.json> \
    --registrations <registrations.json>

python3 -m factory_kit.setup remove --repo /path/to/project \
    --plan removal.json --history retain --accepted-by <operator> \
    --state <setup-state.json> --registrations <registrations.json>
```

- `uninstall` is read-only: it emits a removal plan from the recorded
  ownership ledger — ledger-owned files plus recorded remote-effect
  intents. `--history` has **no default**: a removal plan without
  `retain|export|delete` is refused with exit 2
  (test `test_remove_without_history_choice_refused`).
- `remove` stops intake first, commits the active-generation fence,
  removes only ledger-owned files (user edits and shared infrastructure
  survive), and ends the registration as an identity tombstone.
- Tested result: `outcome: removed`, `.factory-kit.yml` gone, repo CI and
  developer work untouched.

## 6 — Negative walkthrough (A6): every blocker is concrete

Each leg below is exercised in `TestA6NegativeWalkthrough`; all exit 1
with `verdict: not-ready`, `dispatch: denied` and the named code:

| Condition | `blocker_codes` |
|---|---|
| Hermes below the tested minimum | `unsupported-version` |
| `hermes` not on `PATH` | `missing-executable` |
| Role model endpoint unreachable / unlisted | `model-unavailable` |
| Role provider not authenticated | `model-auth-failed` |
| Another automation owner present | `conflicting-task-owner` |
| Base lacks required-check enforcement | `base-unprotected`, `verification-contract-unmet` |
| Kanban intake/cancellation surface missing | `unsupported-interface`, `cancellation-inadequate` |
| Manifest outside schema `factory_kit: 1` | non-appliable plan, named conflicts |

## Exit-code vocabulary (all commands)

`0` success / appliable / ready · `1` refused verdict (conflicts,
not-ready, tampered digest) · `2` usage error · `3` invalid input ·
`4` unable to complete.

## Opted-in issue endpoint boundary

The exercised endpoint is one opted-in issue delivery: `factory-kit`
label → HMAC-verified webhook/intake normalization → durable work
identity (`tests/test_endpoint_walkthrough.py`,
`tests/test_fenced_effects.py`, `test_backup_restore.py` redelivery leg).
The full issue→preview→approved-merge endpoint run is Task 3.10 scope and
is **not** claimed here.
