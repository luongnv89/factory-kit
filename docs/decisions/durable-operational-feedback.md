# Decision record: durable operational feedback (issue #18, Task 3.4, F07)

- **Status:** Accepted — implemented on
  `feat/18-3-4-deliver-durable-notifications`
- **Scope:** durable notification delivery, accessible diagnostics
  (local + Telegram), text-command parity, privacy-safe exports, and
  clock-controlled retention — all local-only, no centralized
  telemetry.
- **Evidence:** `tests/notifications/test_outbox.py` (12),
  `tests/privacy/test_retention.py` (11),
  `tests/diagnostics/test_views.py` (12); the full suite (670 tests)
  is green.

## Decision: a durable outbox beside the alerts table, never a bigger transport

The best-effort `alert_sink` loses the message when Telegram is down —
the alert row is durable but the delivery is not. Option 1 (selected)
adds a **notification outbox** as a first-class store table and a
`NotificationService` that commits the pending record *before* the
send — the same commit-before-effect discipline the rest of the system
already uses:

- `notification_outbox` persists notification/event IDs, the
  `telegram:<digits>` destination reference, the attempt count, the
  next-attempt schedule and the terminal outcome. A repeat signal —
  same `(kind, identity, severity)` triple — dedups onto the existing
  row; a *severity transition* is a new signal, matching the alert
  dedup contract.
- Telegram failure gets the initial attempt plus three bounded retries
  at +20s/+40s/+60s — at least 60 seconds end-to-end — then the row
  settles `failed`, `notification_failed` is emitted and an alert row
  records it. Offline monitoring never claims remote delivery.
- `RecoveryService._notify()` keeps emitting the durable alert first;
  when a notifier is wired it also enqueues to the outbox against the
  work's configured `authorization.telegram_chats` destination, and a
  missing destination enqueues nothing rather than inventing one.
  Outbox failures never roll back committed task/control state.

Option 2 (Telegram-only notifications) was rejected: outages lose both
the notification and the committed-state reporting the issue requires.

## Diagnostics: one extras computation for both surfaces

`diagnostics.views.status_extras()` computes the Task-3.4 fields —
queue age from the durable `work_queue.enqueued_at` stamp (a re-upsert
keeps the original stamp so a visible wait cannot be reset), the
reconciliation watermark (`remote_state` fresh/stale/unknown with the
explicit `STALE` label, never inferred), heartbeat and notification
tallies with the last terminal failure reason. `status_view` merges
them onto the existing `work_status` projection, `operations_view`
does the same over `diagnostic_report` for the installation-level
read, and `ControlService._status_text()` consumes the identical
extras — the Telegram surface and the local report can never disagree.
Unknown stays `unknown`; nothing is rendered as zero.

## Accessibility: the command reference is data

`COMMAND_REFERENCE` in `control.commands` is the single table every
button-equivalence surface renders: all seven actions with exact text
syntax, target scope, mutability and effect. It is keyed off the
parser's own `ACTIONS` tuple so the documentation and the grammar
cannot drift; `reference_text()` states verbatim that an ambiguous or
missing target is rejected and nothing executes on a guess. Rendered
text is ASCII-only — explicit state words, stable IDs, links.

## Privacy: prune detail, keep identity

`privacy.retention.cleanup()` runs one `BEGIN IMMEDIATE` pass against
an injected clock: liveness heartbeats for *terminal* work die at the
7-day worker-log cutoff; detail columns (`events.detail`,
`attempt_usage.usage`, evidence checks/artifacts, preview detail,
control detail, settled-outbox body/last_error) are NULLed at the
30-day post-completion audit cutoff measured from the work's terminal
transition. Identity rows never leave — `work`, `deliveries` dedup
records, control/intent identities, fences, tombstones — so a webhook
replayed after detail pruning still collides with dedup. Active and
parked work is never touched. Config floors clamp *up* (7d/30d are
minimums). `describe_retention()` renders the export-vs-delete
distinction both paths quote.

`privacy.export.history_export()` reuses `export_history`'s repo
scoping but drops every detail column by default; `expanded=True` is
the explicit operator selection that restores them — through the same
sensitive-key drop + credential-pattern redaction — and the payload
carries `secret_scan` plus a `secret_leaks()` verifier.

## No new trust boundary

Everything reads and writes the local SQLite store; the only egress is
the injected Telegram transport, which the allowlisted destination
gate and secret-scan sit in front of. No telemetry sink, dashboard or
remote endpoint was introduced.
