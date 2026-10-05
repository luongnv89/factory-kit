---
name: factory-setup
description: "Reviewed additive setup for factory-kit: inspect a project, produce a reviewable plan, apply it only after explicit operator acceptance, run substantive readiness, and register one project. Use when adopting factory-kit into a repository or re-checking setup health."
license: MIT
compatibility: "Requires git, GitHub CLI (gh) and the factory-kit Python package (stdlib-only) importable — run from the kit checkout or an installed profile. Live readiness needs Hermes on PATH."
metadata:
  version: 0.1.0
  author: Luong NGUYEN <luongnv89@gmail.com>
  effort: high
---

# /factory-setup

Issue #7 / Task 2.2 · PRD §3.2 F01 · §4.1 install flow.

Adds the kit to a repository **additively and reviewably**: nothing local or
remote is mutated until the operator reads a plan and accepts its digest,
and registration never enables execution from executable presence alone —
it requires the accepted plan *and* passing substantive readiness.

## Flow

```
inspect → review plan → accept → apply → readiness → register
```

Each step maps to one command; the two gates (explicit acceptance,
passing readiness) are the safety contract.

### 1 — Inspect (read-only)

```bash
python3 -m factory_kit.setup plan --repo /path/to/project \
    [--manifest /path/to/candidate.factory-kit.yml] [--out plan.json]
```

- Produces the reviewable plan: `entries[]` carry every proposed byte
  inline (the `.factory-kit.yml` manifest content, the webhook
  subscription, the registration row, the approved-skill pin set), the
  `inventory` maps existing CI workflows and `.gitissue.yml` ownership,
  `preserved[]` checksums the unrelated bytes it promises not to touch,
  and `conflicts[]` names anything blocking apply.
- Exit `0` = appliable plan; exit `1` = reviewable but carries conflicts
  (invalid manifest, `.gitissue.yml` ownership overlap, secret canary) —
  fix the cause and re-inspect.
- Never writes: no stash, no reset, no repo file touched, no remote call.

### 2 — Review + accept

The operator reads the plan (entry bytes, the precedence mapping,
competing-automation findings). Acceptance is the deliberate act of
running `apply` with `--accepted-by` — the acceptance record binds
`plan_digest`, so apply executes the exact reviewed bytes.

### 3 — Apply (additive + idempotent)

```bash
python3 -m factory_kit.setup apply --repo /path/to/project \
    --plan plan.json --accepted-by <operator> \
    [--state setup-state.json] [--registrations registrations.json]
```

- Writes only planned paths inside the repo, never through symlinks; a
  drifted or unexpected target is a reported conflict, never an overwrite.
- Records ownership (sha256/size) per factory file, one
  registration/webhook/configuration/dependency entry, and re-verifies
  every preserved checksum after writing.
- Re-running the same accepted plan returns an empty `effective_diff`
  and no duplicates. No independent backlog scheduler or implicit
  auto-merge loop exists in the entry vocabulary.
- The registration row starts `readiness.verdict = "pending"` — present,
  enabled for nothing, until readiness passes.

### 4 — Readiness (substantive)

```bash
python3 -m factory_kit.setup readiness --repo /path/to/project \
    [--readings recorded.json] [--state …] [--registrations …]
```

Probes the selected recipe: Hermes executable + version, Kanban
start/liveness/result verbs + webhook, **cancellation** (block/reclaim),
the manifest's actual role models + provider authentication, pinned
skill revisions vs installed versions, competing automation owner, base
protection vs the manifest's required checks, Telegram transport, host.
Each failure is a named blocker (`missing-executable`,
`unsupported-version`, `model-auth-failed`, `model-unavailable`,
`skill-version-mismatch`, `conflicting-task-owner`, `base-unprotected`,
`verification-contract-unmet`, `cancellation-inadequate`,
`missing-transport`, `unsupported-interface`, `unsupported-runtime`,
`unsupported-host`) and `dispatch` stays `denied`; `dispatched` is
structurally `false`. The report records version set, permissions/scopes,
verification commands and cancellation capability — never token values.

### 5 — Register (gated)

`readiness` persists a `setup_checked` event (project ID, version set,
duration, per-prerequisite outcomes) and refreshes the pending
registration's readiness. `setup_enabled` is true only when both hold:
an accepted plan created the row, and every prerequisite passed. A failed
run registers nothing and remains diagnosable from the ledger.

```bash
python3 -m factory_kit.setup status [--state setup-state.json]
```

### 6 — Remove (ownership-aware, reviewed twice)

```bash
python3 -m factory_kit.setup uninstall --repo /path/to/project \
    --out removal.json [--state …] [--registrations …]
python3 -m factory_kit.setup remove --repo /path/to/project \
    --plan removal.json --history retain|export|delete \
    --accepted-by <operator> [--state …] [--registrations …]
```

`uninstall` is read-only: it emits a removal plan from the recorded
ownership ledger — only ledger-owned bytes plus recorded remote-effect
intents. `remove` applies the accepted plan: intake stops first, the
active-generation fence commits before termination, user edits and
shared infrastructure survive, provider outages leave a visible
cleanup-pending backlog, and the registration ends as an identity
tombstone. `--history` has no default — an undecided removal plan is
refused.

## Tested recipe

The single supported recipe — tested pins, every command exercised by
`tests/recipes/test_install_recipe.py`, operations/recovery vocabulary
and the backup/restore rehearsal — lives in `docs/recipes/`:

- [installation](../../docs/recipes/installation.md) — plan → apply →
  readiness → status → uninstall → remove, plus the negative
  walkthrough's named blockers
- [operations](../../docs/recipes/operations.md) — supervision, webhook
  transport, secrets, park/quarantine reasons, limits, ambiguous
  publication/merge read-back
- [backup-restore](../../docs/recipes/backup-restore.md) — the two-artifact
  durable pair, the restore rehearsal, corrupt/incomplete blockers
- [support-matrix](../../docs/recipes/support-matrix.md) — tested pins
  only; nothing else is claimed

No install command exists (no `pip install`, no `hermes plugins
install`) — public distribution is unresolved under Q8
(`docs/decisions/q8-distribution.md`).

## Guarantees

- `.gitissue.yml` is never written; existing CI is never replaced —
  integration is an explicit reviewed edit, and the default plan proposes
  none.
- Secrets exist only as approved-storage references; plans, reports and
  applied files are canary-scanned.
- Remote effects go through the recorded-intent ledger until live remote
  mutation lands; intents are honest pending records, not claims.
- One registration per repository identity; re-runs are idempotent.
