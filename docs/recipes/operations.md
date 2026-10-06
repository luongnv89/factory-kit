# Recipe: operations, supervision and recovery (issue #23 / Task 3.9, A2)

Operational guide for the tested single support recipe. Every mechanism
named here is implemented and exercised by the suite paths cited — no
undocumented commands are required to operate it.

## Supervision model — restart recovery through durable ownership

- **Authority is the durable pair**, never chat history or a second
  scheduler: the kit-owned SQLite intake store (`in.db`,
  `synchronous=FULL`, rollback journal) plus `registration.json`, under
  `~/.hermes/factory-kit/` for the `default` profile, `~/.hermes/profiles/<profile>/factory-kit/` for named profiles.
- **Recovery is the startup pass** (`RecoveryService.recover`,
  `factory_kit/recovery/service.py`): task-binding repair → pending
  publication-intent reconciliation → merge-intent reconciliation →
  attempt/fence sweep → resume/park/quarantine → recovery events.
  Exercised end-to-end by `tests/recovery/test_restart.py`,
  `test_reconcile.py` and the restore rehearsal
  `tests/recovery/test_backup_restore.py`.
- **Recovery bound:** the pass must settle inside
  `RECOVERY_DEADLINE_S = 60 s`; the report records `within_deadline`.
  Polling constants (`recovery/backoff.py`): nominal `60 s`, discovery
  bound `120 s`, remote-stale `300 s`, backoff cap `300 s` — measured
  boundedness in `docs/measurements/v1-targets.md`.

## Inbound transport — GitHub webhook intake

`factory_kit/intake/webhook.py` normalizes `issues` /
`issue_comment` events and verifies the webhook secret by HMAC. A
signature authenticates the delivery — it is **not** authorization to
execute its text (PRD F02). Execution requires the `factory-kit` opt-in
label *and* a sender inside the manifest's GitHub actor/role allowlists.
Denials persist only a redacted `work_rejected` event — never issue
bodies, signatures or secrets.

- Webhook secret is a **reference**: `env:FACTORY_KIT_WEBHOOK_SECRET`.
- Telegram bot token is a **reference**: `hermes-secrets:factory-kit/telegram-bot-token`.
- Scoped secret mechanism: secrets live in the operator environment /
  Hermes secret store; plans, reports and applied files are
  canary-scanned so a literal secret in a manifest produces a
  non-appliable plan.

## Failure, park and quarantine vocabulary

| State / reason | Meaning | Exit path |
|---|---|---|
| `parked: generation-superseded` | A new authorized generation replaced the work | Operator authorizes a new generation or removes |
| `parked: revoked-registration` | Registration-side revocation (policy/ownership change) | Re-register or remove |
| `parked: ambiguous-outcome` | Pending remote intent, no matching remote object — cannot prove what happened | Operator inspects remote, then re-recovers |
| `parked: duplicate-remote-pr` | More than one remote object claims the identity | Operator resolves the duplicates |
| `quarantined` | Fenced attempt whose descendant termination is uncertain | Explicit operator `resolved` before replacement eligibility |
| `upgrading` / `parked` (migration) | Reviewed upgrade in flight / migration parked on a named conflict — dispatch denied throughout | `repair` restores the validated checkpoint; `rollback` restores the previous digests; see `upgrade-repair.md` |
| `deferred: task-binding-unpublished` | Hermes kanban association still down | Next recovery pass retries the same reserved task ID |
| Readiness blockers | `missing-executable`, `unsupported-version`, `model-auth-failed`, `model-unavailable`, `skill-version-mismatch`, `conflicting-task-owner`, `base-unprotected`, `verification-contract-unmet`, `cancellation-inadequate`, `missing-transport`, `unsupported-interface`, `unsupported-runtime`, `unsupported-host` | Fix the named prerequisite; readiness re-runs |

## Limits and retention (manifest defaults, all enforced by tests)

| Bound | Value | Enforcer |
|---|---|---|
| Active tasks | 1 | single execution lane (`lane_state`, at-most-one active attempt) |
| Implementation attempts | 2 (initial + one fix) | attempt ledger |
| Active worker execution | 60 min per task | attempt liveness/usage records |
| Wall-clock task age | 24 h | work lifecycle sweep |
| Observation freshness | 15 min | evidence currency checks |
| Event retention | 30 days | retention sweep |
| Worker logs | 7 days | retention sweep |
| Audit | 30 days | retention sweep |
| Tombstones | until explicit registration removal | registration store |
| Merge approval expiry | 60 min | `ApprovalService` lapse checks |
| Merge smoke max age | 10 min | preview evidence check |

Disabled operations (never advertised): production deploy, package
publish, autonomous merge.

## Ambiguous publication / merge read-back (A2 troubleshooting)

`PublicationBroker.publish` commits the durable intent **before** the
remote mutation, then converges on the identity-owned remote object:

- **Crash after remote create, before link commit** → recovery finds the
  one matching remote PR by identity and *links* it — `pr-publish` is
  never re-sent (`test_existing_pr_discovered_by_identity_never_recreated`).
- **Zero or many matching remote objects** → parked `ambiguous-outcome`
  or `duplicate-remote-pr` plus an operator alert — never a guess, never
  a second mutation (`test_absent_remote_identity_parks_ambiguous`,
  `test_duplicate_remote_prs_park_and_alert`).
- **Merge intents** reconcile the same way: ambiguous commit/sha
  read-back is rechecked against the remote before any conclusion;
  authority lapses (expiry, moved head, drift) deny the merge.
- **Remote unavailable** → the pass defers honestly and retries with
  bounded backoff; intents stay `recorded`, nothing is claimed applied.

## Troubleshooting quick table

| Symptom | Check | Honest outcome |
|---|---|---|
| Setup seems ignored | `status` → registration `readiness.verdict` | `pending` until readiness passes |
| Dispatch denied after upgrade | `status` → migration `stage`, registration `readiness.verdict` | `pending`/`restored`/`parked` until `setup_checked` re-passes; a parked migration names its conflict |
| Readiness exit 1 | `blocker_codes` + `outcomes[]` | named blocker, `dispatch: denied` |
| Nothing dispatched after restart | `recovery_completed` events, `work.parked_reason` | resumed / parked / quarantined is explicit |
| Duplicate PRs on the remote | `duplicate-remote-pr` alert | parked; resolve remote, re-recover |
| Telegram silent | `notification_outbox` pending rows | outbox is durable; delivery retries are bounded |
| Approval never consumed | `approval_requests.state` + `reason` | expired/invalidated rows stay auditable |
