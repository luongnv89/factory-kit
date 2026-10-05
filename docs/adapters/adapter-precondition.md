# Task 5.2 admission-precondition check — issue #33

**Version:** 1.0.0 · **Recorded:** 2026-10-05 · **Status:** complete —
precondition **`unmet`**, authorization **`blocked`**

The executable gate on the *only* act that may start task 5.2
(§3.1 F13, §8.4): a recorded operator adoption of exactly the single
candidate the GATE-E04 assessment admitted, plus the evidence
preconditions the issue's A1 names. The probe is
`tools/probes/adapter_precondition.py`; the recorded run is
`docs/adapters/adapter-precondition-2026-10-05.json`; the operator act
it verifies lives in
[docs/decisions/f13-admission.md](../decisions/f13-admission.md).

**What this evidence is.** A read-only audit over committed
artifacts — the recorded GATE-E04 assessment
(`admission-assessment-2026-10-05.json`, verdict
`assessment-complete`, disposition `admission-deferred`), the F13
operator decision record, the continue/narrow/stop decision record,
the pilot-evaluation archive and the documented candidate fixture
(`codex-cli 0.160.0` via `hermes codex-runtime`). Every row is
*derived* from those sources: an adoption claim is parsed out of the
decision record's own fields, and a checked box without the named
candidate/version, recipe, scope, cost/capacity and acceptance-gate
fields is not an adoption.

**What it is not.** Not an adapter — nothing here executes harness
code; `adapter_execution` is asserted `none` and audited. Not an
admission instrument — the owner's act is recorded in the F13
decision record, never here. Not a weakening of any gate —
`precondition: unmet` keeps adapter implementation unauthorized with
every unmet row named.

## Checklist — recorded states (2026-10-05)

| Gate | State | Reading |
|---|---|---|
| `assessment_recorded` | **passed** | GATE-E04 archive complete, instrumentation clean, singleton candidate |
| `pilot_continue_confirmed` | **blocked** | recorded disposition `narrow-consolidate`, owner confirmation pending — a narrow/stop outcome never authorizes §8.4 expansion |
| `operator_adoption` | **pending** | no `[x] **Adopt**` in the F13 record — adopted candidate `none`, decision `pending` |
| `adoption_scope` | **pending** | no recorded adoption to evaluate — the five A4 fields are absent |
| `adopted_candidate_match` | **pending** | no recorded adoption to compare against the assessed singleton |
| `supported_hooks` | **blocked** | all six required capability rows unproven on the candidate lane; `hermes_lifecycle_integration` is a recorded **no-go**; all four compatibility probes unproven/no-go |
| `effort_bound` | **blocked** | the recorded bound (`≤3 developer-days`) is flagged ungrounded — hooks unproven, so the estimate cannot be grounded today |
| `adapter_code_fence` | **passed** | no `factory_kit/adapters/`/`factory_kit/harnesses/` on disk |

**Precondition: `unmet` — authorization: `blocked`.** Task 5.2 stays
blocked pending scoped replan, exactly as A1 prescribes: missing
admission, missing supported hooks and an ungrounded estimate each
independently hold the gate. Six of eight checklist rows are
non-passed; every one is named in `unmet_reasons`
(denominator-preserving).

## Fail-closed contract

- **Missing/rejected/revoked admission** — no `[x] Adopt` →
  `pending`; `[x] Reject` → `blocked` (revoked admission); a checked
  box that never names a versioned candidate → `blocked`.
- **Wrong candidate** — an adoption naming anything but the assessed
  `codex-cli 0.160.0` is out of scope: `blocked`, scoped replan
  required, never a silent broadening.
- **Unproven hooks or ungrounded/out-of-bound effort** — `blocked`
  regardless of the adoption act; adoption is necessary, never
  sufficient.
- **Adapter code ahead of authorization** — adapter dirs on disk
  while unmet fail the fence *and* breach `unauthorized_execution`.
- **Fabricated verdicts** — `--fixture` re-judges recorded gates; a
  `met`/`authorized` claim over a non-passed gate is an
  instrumentation breach (`no_authorization_on_unmet`), as is a
  dropped unmet reason or an asserted `adapter_execution`.

## Re-entry conditions (what re-running requires)

1. Owner records a confirmed `continue` verdict in
   `docs/decisions/continue-narrow-stop.md`.
2. A live (not fixture) repeat-use observation meeting the proposed
   bound, or an adjusted target the owner accepts.
3. Documented demand: ≥2 favorable measured signals for the
   candidate runtime lane.
4. The candidate's capability/authority matrix proven row-by-row on
   its own lane — supported-for-the-selected-lane never transfers.
5. The `hermes codex-runtime` path demonstrated against the kanban
   contract, or a documented supported primitive replacing it.
6. Operator adoption recorded in `docs/decisions/f13-admission.md`
   naming the assessed candidate/version with support/trust recipe,
   bounded test scope, cost/capacity and acceptance gates.
7. A grounded effort estimate within the ≤3-developer-day bound — an
   estimate beyond three days or flagged ungrounded re-plans before
   execution (A1).

## Aggregation audits

`gate_denominator`, `source_coverage`, `unmet_reason_coverage`,
`no_authorization_on_unmet`, `adoption_consistency`,
`singleton_reference`, `no_adapter_execution`,
`unauthorized_execution`, `aggregate_only`, `secret_scan` — all clean
on the recorded run (`breaches: []`, verdict
`precondition-check-complete`). The audits are the replayable
contract: `tests/benchmarks/test_adapter_precondition.py` mutates the
extracted evidence to prove fabricated authorizations, unnamed
candidates, dropped unmet reasons, asserted adapter execution and
on-disk adapter code each surface as breaches — never an authorizing
verdict.

## Reproduce

```bash
python3 tools/probes/adapter_precondition.py --write \
    docs/adapters/adapter-precondition-$(date +%F).json
python3 tools/probes/adapter_precondition.py --fixture \
    docs/adapters/adapter-precondition-2026-10-05.json   # re-judge
python3 -m unittest tests.benchmarks.test_adapter_precondition -v
```

Exit codes: `0` precondition-check-complete, `1`
precondition-check-instrumentation-failed, `2` usage, `4`
cannot-complete (source archive or fixture unreadable / wrong kind).
