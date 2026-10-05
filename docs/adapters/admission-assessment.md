# §8.4/GATE-E04 additional-harness admission assessment — issue #32 (task 5.1)

**Version:** 1.0.0 · **Recorded:** 2026-10-05 · **Status:** complete —
disposition `admission-deferred`

The versioned assessment task 5.1 owes before any F13 adapter work:
whether *one additional documented harness* has enough demand and
maintainable compatibility to justify expansion. The executable gate
is `tools/probes/harness_admission.py`; the recorded run is
`docs/adapters/admission-assessment-2026-10-05.json`; the operator
record this feeds is
[docs/decisions/f13-admission.md](../decisions/f13-admission.md). The
single candidate under assessment is declared in
`tests/fixtures/adapters/candidate-codex-cli.json`.

**What this evidence is.** A read-only audit over the committed
recorded runs — the task-4.6 usefulness evaluation
(`pilot-evaluation-2026-10-05.json`), the scripted ten-issue
two-project dogfood cohort (`dogfood-comparison-2026-10-05.json`), the
scripted external-pilot study (`external-study-2026-10-05.json`), the
§8.2 fault matrix, the host readiness archive
(`readiness-2026-10-05.json`), the boundary map's no-go list, the
tested-recipe selection and the v1.1 release-gate ledger. Every
capability state is *derived* from those sources; the candidate
fixture's own claims are cross-checked and an over-claim is an
instrumentation breach.

**What it is not.** Not an admission — `admission-deferred` is the
honest outcome of the recorded evidence, and even
`admission-ready-for-owner` would execute nothing without the owner's
explicit act (§8.4). Not a capability probe run — no adapter code was
executed (`adapter_execution: none`, audited). Not a new measurement —
no study re-runs and no fixture verdict is promoted to a live-target
claim.

## A1 — pilot gate (the evidence the assessment cites)

| Requirement | Recorded | State |
|---|---|---|
| Ten-issue/two-project sequential dogfood complete | `cohort-run-complete`; project-a then project-b sequential; one-active-task 22/22 clean; 10/10 denominator | **passed** |
| Three external install/repeat-use measurements | `external-pilot-run-complete`; `onboarding_completed_60d` 3/4, `independent_setup_2_in_45min` 2/4, `repeat_use_2x4wk_90d` 1/2 | **passed** (measurements recorded — verdicts are data) |
| Operator-effort baseline + setup/maintenance cost | cohort median 16.0 vs baseline 23.0 min; overhead 75 min → 7.5 amortized; **total 23.5 ≥ 23.0** | **passed** |
| Actual continue/narrow/stop decision | **narrow-consolidate**, *pending owner confirmation* — a narrow outcome never authorizes §8.4 expansion | **blocked** |
| Repeated-use evidence favorable | repeat-use **fail** (1 < 2) — missing repeated-use evidence | **blocked** |

## A2 — exactly one candidate, documented

The candidate under assessment is **`codex-cli 0.160.0`** through
`hermes codex-runtime` — the only migration path the boundary map
documents; every other external CLI worker lane is a recorded no-go.
Provenance is pinned to the readiness archive: binary present on the
selected host (`codex-cli 0.160.0`), model provider `openai-codex` via
Hermes pooled OAuth, IDD skills bundle `v0.23.1-2-gd6a908f`.

| Requirement | Recorded | State |
|---|---|---|
| Exactly one candidate with provenance | 1 candidate, host/model/skill pinned | **passed** |
| Documented demand | **insufficient** — the only recorded alternate-runtime request (`denied-wl`, class `runtime-mirror`) was denied as unsupported; 0 cohort issues failed for want of a second harness; repeat-use failed | **blocked** |
| Incremental maintenance cost documented | surfaces enumerated (version tracking, fault-suite reruns, credential review, support row); effort bound ≤3 dev-days; measured context cited | **passed** |
| Capability/authority matrix complete + honest | all 6 required rows present; `resume_steering`/`live_steering` marked optional, never presumed; claims match derived states | **passed** |

### Capability/authority matrix — derived, not claimed

| Capability | Derived state | Evidence |
|---|---|---|
| readiness/auth | `conditional` | binary + version pinned; pooled `openai-codex` auth exists for the *model* lane — worker-lane readiness never probed |
| start | `unproven` | kanban create/claim/dispatch proven for the profile worker only |
| liveness | `unproven` | heartbeat/runs/tail/watch proven for the profile worker only |
| structured results | `unproven` | kanban complete/block/request-review + attach proven for the profile worker only |
| independent role sessions | `unproven` | separate implement/review profiles exist on the selected lane only |
| cancellation incl. descendants | `unproven` | block+reclaim generation fencing proven for the profile worker only |
| resume / live steering | `optional` | Should-tier capability, never presumed |

## A3 — compatibility probes

| Probe | Derived | Actionable evidence |
|---|---|---|
| Current-generation authorization | `unproven` | pooled OAuth authorizes the model lane; no worker-lane authorization for a codex-runtime lane is on record |
| Credential separation | `unproven` | `GH_CONFIG_DIR`/`GH_TOKEN` profile scoping is documented for kanban lanes; candidate worker credentials never separated or fenced |
| Supported Hermes lifecycle integration | **`no-go`** | boundary map: external CLI worker lanes are *not a paved path*; only `hermes codex-runtime` migration is documented and it has never been exercised against the kanban contract — a second scheduler stays forbidden |
| Preview/human-approved merge endpoint compatibility | `unproven` | the endpoint was exercised only through the selected lane, which itself carries open live blockers (`base-unprotected`, `telegram-adapter-live`, `vercel-linkage`, `kanban-live-write`) |

Unsupported primitives, isolation or cancellation capability reject
admission — that is exactly what `no-go`/`unproven` rows do here.

## A4 — operator adoption record

No adoption is recorded. The
[decision record](../decisions/f13-admission.md) carries the explicit
fields — adopted candidate/version: none; support/trust recipe: none;
bounded test scope: none; cost/capacity: documented-not-justified;
acceptance gates: the re-entry list. **No decision or a rejected
decision keeps task 5.2 deferred with no adapter code execution** —
`adapter_execution: none` is an audited field, and no
`factory_kit/adapters/` path exists.

## A5 — scope lock

One active task/project at a time, zero additional adapters admitted
(at most one is ever in scope), the selected preview provider only, no
dates promised, no broader harness/provider abstraction — emitted as
the `scope` block and audited against the tree.

## Recorded disposition

**`admission-deferred`** — 8 of 15 gate rows non-passed, every one
named in `deferred_reasons` (denominator-preserving). The deferred
outcome is a first-class result: the assessment completed, the
evidence does not justify F13, and nothing is admitted. Re-entry
conditions are enumerated in the decision record; a rerun after the
evidence moves re-judges the same gates deterministically.

## Aggregation audits

`gate_denominator`, `source_coverage`, `deferred_reason_coverage`,
`no_admission_on_gated`, `candidate_singleton`, `matrix_honesty`,
`no_adapter_execution`, `aggregate_only`, `secret_scan` — all clean on
the recorded run (`breaches: []`, verdict `assessment-complete`). The
audits are the replayable contract:
`tests/benchmarks/test_harness_admission.py` mutates the extracted
evidence to prove fabricated admissions, over-claimed capabilities,
dropped deferred reasons and asserted adapter execution each surface
as breaches — never an admissible verdict.

## Reproduce

```bash
python3 tools/probes/harness_admission.py --write \
    docs/adapters/admission-assessment-$(date +%F).json
python3 tools/probes/harness_admission.py --fixture \
    docs/adapters/admission-assessment-2026-10-05.json   # re-judge
python3 -m unittest tests.benchmarks.test_harness_admission -v
```

Exit codes: `0` assessment-complete, `1`
assessment-instrumentation-failed, `2` usage, `4` cannot-complete
(source archive or candidate fixture unreadable / wrong kind).
