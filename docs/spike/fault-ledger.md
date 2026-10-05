# Stage-0 gate ledger — fault-injection evidence record (issue #5, Task 1.4)

Executable record of every §8.1 Stage-0 gate after injecting the required
duplicate/restart/cancel and approval/merge-uncertainty faults into the
demonstrated thin slice. The harness is `tools/probes/spike_faults.py`;
the recorded run is `docs/spike/evidence/faults-2026-10-05.json`; the
go/no-go disposition it feeds is `docs/decisions/day2-go-no-go.md`.

**Modeling honesty.** The demonstrated slice (`EndpointStore`,
Task 1.3) records one work row per accepted event and has no delivery
dedup or active-attempt ledger. Those are kit-owned rows per the Q6
storage split (delivery-dedup/control/approval rows), so the probe's
`FaultStore` adds the convergence contract at the *same* store layer —
same `BEGIN IMMEDIATE` + `synchronous=FULL` discipline — instead of
rewriting published evidence. Landing that contract in the controller
intake path is recorded as MVP work, not waived. Remote effects still
land on the disposable capability-gated fixture; generation fencing
reuses Task 1.2's `FenceStore`; approval reuses `EndpointStore`.

## Gate-by-gate ledger (A4)

| Gate | §8.1 item | Task | Status | Supported interface | Evidence / reproduce |
|---|---|---|---|---|---|
| GATE-S01 | Probe readiness, select supported repo/runtime/model | 1.1 | **pass** | `tools/probes/readiness.py` + tested-recipe selection | `docs/spike/evidence/readiness-2026-10-05.json`, `docs/decisions/tested-recipe-selection.md`; `python3 tools/probes/readiness.py --fixture <f>` |
| GATE-S02 | Map durable primitives and boundary | 1.1 | **pass** | Hermes kanban/typed-action/approval surfaces per boundary map | `docs/spike/hermes-boundary-map.md` |
| GATE-S03 | Select preview provider, prove revision→deployment identity | 1.3 | **pass** | `tests/fixtures/preview_fixture.py` + live Vercel deploy/inspect leg | `docs/spike/evidence/endpoint-2026-10-05.json` (`preview`, `live-preview` legs) |
| GATE-S04 | Run end-to-end happy path on real fixture projects | 1.3 | **pass** | `tools/probes/endpoint_walkthrough.py` + live money-mind PR leg | `docs/spike/evidence/endpoint-2026-10-05.json`, `docs/spike/spike-traces.md` |
| GATE-S05 | Fault injection — Stage-0 fault subset | 1.4 | **pass** | `tools/probes/spike_faults.py` (this probe) | `docs/spike/evidence/faults-2026-10-05.json`; `python3 tools/probes/spike_faults.py --write <out>.json` |
| GATE-S06 | Verify capability/credential boundary | 1.2 | **pass** | `tools/probes/fenced_effects.py` + disposable remote | `docs/spike/evidence/fenced-effects-2026-10-05.json`, `docs/spike/authority-proof.md` |
| GATE-S07 | Record endpoint and data decisions | 1.1 | **pass** | tested-recipe decision record (Q1/Q2/Q10 preserved) | `docs/decisions/tested-recipe-selection.md` |
| GATE-S08 | Approval persistence/single-use + enforced merge preconditions | 1.3/1.4 | **pass** | durable one-use `approvals` + `merge_intents` + `evaluate_merge_guard` | `docs/spike/evidence/endpoint-2026-10-05.json` (`approval`, `merge-guard` legs); `docs/spike/evidence/faults-2026-10-05.json` (`approval-*`, `merge-uncertainty` legs) |

Every gate row carries probe/fixture versions, fixture identity
(`spike/disposable` + `DisposableRemote` remote.json + `endpoint.db` /
`fence.db` SQLite stores) and a reproduce command inside
`report.gate_ledger`.

## Spike-subset counters (A4)

Measured from the durable store dumps and the authoritative remote
snapshot, not asserted:

| Counter | Value | Meaning |
|---|---|---|
| `duplicate_prs` | **0** | no remote head owns more than one PR |
| `unauthorized_merges` | **0** | every `merge-invoke` event names the `merge-owner` actor |
| `accepted_fenced_results` | **0** | zero results accepted on non-live attempts, zero intents allowed on non-active generations |

## Per-criteria fault evidence

| AC | Leg (`--scenario`) | What the fixture proved |
|---|---|---|
| **A1** | `duplicate-delivery` | Webhook redelivery of one `delivery_id` → `deduplicated`; reconciliation overlap with a distinct delivery id → `reconciled` onto the same `(repo,issue)` work row; `work_rows=1`, `task_rows=1`; a second `begin_attempt` denied `attempt-active` with exactly one active attempt |
| **A1** | `restart-recovery` | Store reopened from disk (host-restart analogue): the same delivery dedups, a new-channel delivery reconciles — convergence is computed from recovered durable rows, not acknowledgements |
| **A1** | `pr-crash-reconcile` | Crash after remote PR creation, before the response was recorded: `recover_publication` discovers the existing PR via `remote.snapshot()` (`reconciled`, no republish). Remote unreadable → `parked`; parked state is re-evaluable and reconciles once readable; `total_pulls` stays 2 |
| **A2** | `cancel-late-result` | Typed cancel commits the fence first; a late `submit-result` and a late `branch-publish` on generation 1 are denied `generation-fenced`; replacement denied `termination-uncertain` until `record_termination(…, confirmed)`; post-replacement gen-1 effects denied `superseded-generation` |
| **A2** | `dead-worker-quarantine` | Claim expiry reaps the dead worker's attempt; its result denied `attempt-expired`. Cancel on the live generation + `quarantined` termination → `begin_replacement` denied `termination-uncertain/quarantined`; cleared only on confirmed evidence (gen 3 allowed); a fresh claim's result accepted |
| **A3** | `approval-restart` | `approvals` row survives store reopen with actor, target, action, revision/evidence/policy digests, expiry and `awaiting` state attributable |
| **A3** | `approval-single-use` | Consume once → replay denied `replayed`; two racing approve sessions consume exactly once (one merge intent per request); expired request denied `expired`; fenced task → merge-guard `task-fenced` and all pending intents invalidated (`pending=0`) |
| **A3** | `approval-drift` | Merge-guard denies `head-changed`, `base-changed`, `required-checks-not-green`, `stale-preview` on changed inputs; a `policy-changed` request is invalidated — `approve` denied `state-invalidated`, guard denied `approval-invalidated` |
| **A3** | `merge-uncertainty` | Accepted merge with lost response reconciles to the actual remote merge SHA (`matches_expected_head`, `retried: False`); unreadable remote → `parked`, no blind resend, no false success; post-restore read-back confirms exactly one `merge-invoke` event |
| **A4** | `gate-ledger` | S01–S08 each recorded separately with status, interface, versions, fixture identity, reproduce command — this document plus `report.gate_ledger` |
| **A5/A6** | — (decision record) | `docs/decisions/day2-go-no-go.md` |

## What this subset does *not* claim (A4/A6)

- The remaining §8.2 fault rows and the required **three repetitions per
  release row** are explicit MVP work — tasks 3.6/3.7 (issues #20/#21).
- `MET03` is **not** claimed complete from this subset; the report's
  `met03_note` says so.
- Live-endpoint fault legs (real GitHub/Vercel/Hermes faults) are out of
  the bounded spike scope; the Stage-0 subset runs over the supported
  primitive path only — per the task note, *only supported primitives may
  pass*.

Reproduce:

```bash
python3 tools/probes/spike_faults.py --self-test          # fixture + assert
python3 tools/probes/spike_faults.py --write <out>.json   # full suite → report
python3 tools/probes/spike_faults.py --scenario <leg>     # one leg set
python3 tools/probes/spike_faults.py --fixture <run>.json # re-evaluate a run
```

Exit `0` = `fault-suite-passed`; `1` = `fault-suite-failed` (a verdict,
not a crash). Unit coverage: `tests/test_spike_faults.py` (47 tests).
