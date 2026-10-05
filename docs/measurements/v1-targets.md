# §5.1 local-target measurements — issue #22 (task 3.8)

Measured pass/fail for every proposed §5.1 local target on the
documented selected host, feeding the Q9 defaults decision
(`docs/decisions/q9-acceptance.md`). The executable harness is
`tools/probes/local_targets.py`; the recorded run below is
`docs/measurements/v1-targets-2026-10-05.json`
(`run_id`, fixture scale and every sample are in the archive).

**What this evidence is.** Real wall-clock measurements
(`time.perf_counter`) of the merged services running on the shared
§8.2 `World` fixture stack — the same `IntakeStore` (SQLite,
`BEGIN IMMEDIATE` + `synchronous=FULL`), the same wired
intake/control/lane/recovery services, the same scripted ports. The
sustained intake measurement was **actually paced**: 300 deliveries at
a real 1 event/second for a measured 302.0 s wall — not a batch.

**What it is not.** External-service latency is absent *by
construction*: GitHub, the model provider, Telegram and the preview
provider are scripted in-process ports, so network delay can never
enter the measured path (it is reported as a separate 0 ms in-path
term — exactly the separation §5.1 requires). These are
local-boundary measurements, not end-to-end service claims. Fault
behavior is validated independently in
`docs/evidence/fault-matrix-2026-10-05.json`.

## Host, runtime and fixture

- **Host:** `M1` — macOS 27.0.1 arm64 (the recorded selected host in
  `docs/decisions/tested-recipe-selection.md`), Python 3.14.7; the run
  itself re-captures interpreter/platform in the JSON `environment`.
- **Repository under registration:** `luongnv89/money-mind`
  (`R_kgDOQncOdA`) — the Q3 selected repository, one active task.
- **Fixture scale (A1):** 100 pending work rows created through the
  real intake path, queued; the retained-event population topped up
  inside one transaction to **exactly 10,000 rows** (200 real
  accept/bind events + 9,800 labelled `benchmark_seed` fixture rows —
  the point is retained volume, and the marker kind is honestly
  named). Store file after seeding: ~1.5 MB.
- **Manifest/policy digests:** recorded in the JSON `environment`
  block alongside the fixture identity and reproduce command.

## Results (recorded run `v1-targets-2026-10-05.json`)

| # | §5.1 target (issue AC) | Proposed bound | Measured | Margin | Verdict |
|---|---|---|---|---|---|
| T1 | Durable intake accept/reject p95, 1 ev/s × 5 min (A1/A2) | ≤ 2 s | **p95 11.73 ms** (n=300; p50 5.34 ms, p99 21.74 ms, max 35.80 ms, mean 5.96 ms ±4.12 ms) | ~171× | **pass** |
| T2 | 100 duplicate deliveries ≤ 10 s incl. restart → 1 task, ≤ 1 PR (A2) | 10 s window | **52.3 ms** burst with mid-burst store rebuild → 1 work row, 0 remote PRs | ~191× | **pass** |
| T3 | Local status p95 over persisted rows (A3) | ≤ 1 s | **p95 0.24 ms** (n=100) | ~4200× | **pass** |
| T4 | Auth'd pause/cancel + fence persist ≤ 5 s, Telegram-independent (A3) | ≤ 5 s | **handler p95 4.0 ms**; durable record received→committed p95 1.5 ms (n=30: pause/resume/cancel) | ~1250× | **pass** |
| T5 | Worker/descendant exit or quarantine + local notify ≤ 30 s (A4) | ≤ 30 s | confirmed exit **2.9 ms**; quarantine + durable `termination-uncertain` alert **3.4 ms**; replacement denied while uncertain | ~8800× | **pass** |
| T6 | Reconcile interval ≤ 60 s; dropped work found ≤ 120 s after GitHub returns (A4) | ≤ 60 s / ≤ 120 s | interval clamped 60.0 s (a 600 s config was clamped, not honored); failed pass self-backoff **1.0 s**; discovery = backoff + one **2.5 ms** pass ≈ **1.0 s** | ~120× | **pass** |
| T7 | Recover/park all known active tasks ≤ 60 s of readiness (A5) | ≤ 60 s | **6.1 ms** wall; 4/4 mixed known-set settled (pending→resumed, live-heartbeat→resumed, orphan→quarantined, unbound-binding→repaired→resumed) | ~9800× | **pass** |
| T8 | Lost acknowledged tasks in defined crash tests (A5) | 0 | **0** across 57 fault-matrix repetitions (audit counter, recorded run) | — | **pass** |
| T9 | Model calls in 30 idle minutes without work/exceptions (A5) | 0 | **0** — provider-call audit counter + durable `idle_audit` agree | — | **pass** |
| T10 | A6 structural bounds (A6) | observable, no SLA claim, CI excluded, 24 h park, 5 min cap, stale remote explicit | all 10 mechanism checks pass — see below | — | **pass** |

**Verdict: `targets-passed`** — every proposed §5.1 local target met
with large headroom on the selected host.

### T10 detail (A6 mechanisms, measured not timed)

- `recovery.status()` exposes offline/liveness state:
  `remote_state`, `last_success_epoch`, `consecutive_failures`,
  `next_due_epoch`, `process_started_epoch`, lane occupancy, durable
  alerts — persisted, so a restarted process reports the same truth.
- Stale remote observations are *explicit* and *measured*: the run
  forced a wholly-unobserved pass (poll + targeted observe both down)
  >5 minutes past the last success — the watermark flipped to
  `remote_state: "stale"`, `status_view` labeled the remote block
  `stale: true`, and exactly one deduplicated `reconciliation` alert
  fired for the episode. The rendered operations text carries
  "Remote observation: …" and no `sla`/`uptime`/`always-on` wording.
- CI wait is excluded from the 60 active-worker minutes by
  construction: `normalize_usage` keeps `ci_wait_seconds` (and the
  other three wait classes) as separate fields; `budget_status`
  showed 25 h wall + 30 measured worker-minutes exhausting only the
  wall budget, never the worker budget.
- 24-hour wall bound parks: `wall_seconds` exhausted at 25 h elapsed.
- Retry backoff cap: self-imposed delay bounded at 300 s; upstream
  `retry_after` guidance may exceed it and wins (600 s honored).

## Uncertainty and limits

- Single host, single run, scripted ports. p95 figures have n=300
  (intake) / n=100 (status) / n=30 (control) samples; margins are so
  large that scheduler jitter on a shared host cannot plausibly close
  them — but these numbers are *this* host's evidence, not a claim
  for other hardware.
- `recovery.reported_duration_s` reads 0 on the injected fixture
  clock (the service's own deadline check runs on the fake clock);
  the honest measured wall is T7's `wall_s`. `within_deadline` is the
  service's own verdict.
- T8's zero is the *recorded* §8.2 audit counter over 57 repetitions,
  re-read from `docs/evidence/fault-matrix-2026-10-05.json` — the
  defined crash tests, not a new estimate.
- The intake p95 includes the task-association publish leg (local
  reservation + bind) — the conservative end of "handler receipt to
  durable accept/reject".
- No acknowledgement precedes persistence: `deliver()` returns only
  after `transact()` commits; a failed durable write raises
  `IntakeUnavailable` and produces no ack (structural — F02/A5,
  covered by FAULT02).

## Reproduce

```bash
python3 tools/probes/local_targets.py --write \
    docs/measurements/v1-targets-$(date +%F).json   # ~5 min paced run
python3 tools/probes/local_targets.py --quick      # 30 unpaced deliveries
python3 tools/probes/local_targets.py --fixture \
    docs/measurements/v1-targets-2026-10-05.json    # re-check verdict
python3 -m unittest tests.benchmarks.test_local_targets -v  # mechanics
```

Exit codes: `0` targets-passed, `1` targets-failed, `2` usage,
`4` cannot-complete.
