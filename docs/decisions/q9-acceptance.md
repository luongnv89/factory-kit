# Decision record: Q9 — proposed numerical targets, retention defaults and budgets (issue #22, Task 3.8)

- **Status:** Submitted for owner acceptance — pending Luong's decision
- **Owner:** Luong (PRD §9.1 Q9 — due before MVP acceptance and pilot recruitment)
- **Scope:** every proposed §5.1 local target and the concrete numerical
  defaults (attempt/time budgets, retention, preview lifetime,
  cleanup/approval expiry, smoke freshness, retry/backoff bounds)
- **Evidence:** `tools/probes/local_targets.py` run on the documented
  selected host → `docs/measurements/v1-targets.md` +
  `docs/measurements/v1-targets-2026-10-05.json`; crash/fault evidence →
  `docs/evidence/fault-matrix-2026-10-05.json`

This record does **not** silently finalize the proposed defaults. Q9 is an
explicit acceptance decision pending evidence; until the owner records a
verdict below, every value remains *proposed* — bounded by schema and
measured, never claimed as accepted. Unmet or unaccepted targets stay
blockers or require an explicit replan (issue A7).

## What the measurements established

Local boundary work measured on the selected host lands orders of
magnitude inside every proposed bound — see
`docs/measurements/v1-targets.md` for the full table. Headline margins:

| §5.1 target | Proposed | Measured (selected host) | Verdict |
|---|---|---|---|
| Durable intake accept/reject p95, sustained 1 ev/s 5 min | ≤ 2 s | p95 11.73 ms (n=300, paced 302.0 s wall) | **pass**, ~171× headroom |
| Duplicate burst: 100 repeats in 10 s incl. restart | 1 task, ≤ 1 PR | 52.3 ms burst → 1 task, 0 PRs | **pass** |
| Local status p95 (100 persisted queries) | ≤ 1 s | p95 0.20 ms | **pass** |
| Control pause/cancel + fence persist | ≤ 5 s of receipt | handler p95 4.0 ms; record commit p95 1.5 ms | **pass** |
| Worker exit/quarantine + local notify on cancel | ≤ 30 s | 2.9 ms confirmed / 3.4 ms quarantined | **pass** |
| Reconciliation nominal interval / dropped-webhook discovery | ≤ 60 s / ≤ 120 s | interval clamped at 60 s; discovery ≈1.0 s | **pass** |
| Restart recovery of all known active work | ≤ 60 s | 6.1 ms measured wall, 4/4 settled | **pass** |
| Lost acknowledged tasks in defined crash tests | 0 | 0 across 57 fault-matrix repetitions | **pass** |
| Model calls during 30 idle minutes | 0 | 0 (provider-call audit counter) | **pass** |

Uncertainty: single host, single run, scripted ports (no
GitHub/model/Telegram network in the measured path — network latency is
reported separately by construction). Numbers are local-boundary
measurements, not end-to-end service latencies; the retained-state scale
(100 pending tasks / 10,000 events) is the §5.1 fixture shape.

## Defaults submitted for acceptance

All values are the Q9-proposed defaults already enforced as validated
configuration bounds (`factory_kit.config.schema`,
`factory_kit.recovery.backoff`, `factory_kit.execution.limits`). The
measured evidence supports **accepting them as proposed** — no measured
adjustment is requested.

| Default | Proposed | Where enforced | Evidence |
|---|---|---|---|
| Active tasks | 1 | `DEFAULT_LIMITS.active_tasks` (bound 1–8) | fault-matrix FAULT10 single-lane occupancy |
| Implementation attempts | 2 (initial + one fix) | `DEFAULT_LIMITS.implementation_attempts` (1–5) | FAULT10 attempt boundary |
| Active-worker budget | 60 minutes, CI wait excluded | `active_worker_minutes` (5–480); `ci_wait_seconds` separate field | measured usage accounting (A6 checks) |
| Task wall time | 24 hours, then parks | `wall_hours` (1–168) | FAULT10 wall park |
| Worker log retention | 7 days | `worker_log_retention_days` (floor 7) | privacy/retention sweep |
| Detailed audit retention | 30 days post-completion | `audit_retention_days` (floor 30) | privacy/retention sweep |
| Event retention | 30 days | `event_retention_days` | schema |
| Observation freshness | 15 minutes | `observation_freshness_minutes` | schema |
| Preview lifetime | 24 hours maximum | `DEFAULT_PREVIEW.ttl_hours` (1–72) | FAULT13 TTL bound |
| Preview cleanup after termination | 60 minutes | `cleanup_minutes` (5–240) | FAULT13 backlog drain |
| Approval expiry | 60 minutes | `approval_expiry_minutes` (15–120) | FAULT14 expiry |
| Merge smoke freshness | 10 minutes | `max_smoke_age_minutes` (1–30) | FAULT15 `smoke-stale` |
| Reconciliation interval | ≤ 60 s nominal | `NOMINAL_INTERVAL_S` (clamped) | measured pass discovery |
| Dropped-work discovery | ≤ 120 s after GitHub returns | `DISCOVERY_BOUND_S` self-backoff cap | measured ≈1 s |
| Retry backoff cap | 5 min nominal, upstream guidance wins | `BACKOFF_CAP_S` + `retry_after` | measured cap/override |
| Restart recovery deadline | 60 s | `RECOVERY_DEADLINE_S` | measured 6.1 ms wall |
| Heartbeat liveness | 60 s to fence | `HEARTBEAT_TIMEOUT_S` | FAULT04 dead worker |
| Idle model calls | 0 per 30 min | `IDLE_AUDIT_S` + `idle_audit` | measured 0 |

## Decision record

- [ ] **Accepted as proposed** — all §5.1 targets and defaults above.
- [ ] **Adjusted from measured baseline** — record each adjustment in a
      row appended to the table above (old → new + rationale).
- [ ] **Rejected / replan** — targets that could not be measured or
      accepted remain blockers, never claimed achieved.

**Owner decision:** _pending — submitted to Luong with the measured
report on 2026-10-05_

**Recorder:** issue-resolver run for #22 (autonomous measurement +
submission; acceptance itself is the owner's act)

> This file is the §9.1 Q9 submission artifact. When Luong records the
> verdict, update the checkbox, the decision line and `tasks.md` task
> 3.8's status, and close the loop in the §8.2 evidence gate (task 3.10).
