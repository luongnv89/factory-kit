# Decision record: F13 additional-harness admission (issue #32 / Task 5.1)

- **Status:** Recorded — **admission-deferred** is the
  evidence-supported disposition; **pending owner confirmation**
  (2026-10-05)
- **Owner:** Luong — adoption is never self-recorded; A4 requires an
  explicit operator record before task 5.2 may run
- **Scope:** §3.1 F13 (one additional supported harness adapter),
  §8.4 GATE-E04 expansion admission; the §8.3 pilot gate and §6.5
  runtime contract supply the evidence bar
- **Evidence:**
  `docs/adapters/admission-assessment.md` +
  `docs/adapters/admission-assessment-2026-10-05.json` (probe:
  `tools/probes/harness_admission.py`, verdict
  `assessment-complete`), over
  `pilot-evaluation-2026-10-05.json`,
  `dogfood-comparison-2026-10-05.json`,
  `external-study-2026-10-05.json`,
  `readiness-2026-10-05.json` and the boundary map's no-go list

## Recorded evidence position

The completed measured pilot does not support expansion today:

| Gate | Reading | Source |
|---|---|---|
| Pilot decision | **narrow/consolidate**, owner confirmation pending — a narrow outcome never authorizes §8.4 expansion | `pilot-evaluation` recommendation; `continue-narrow-stop.md` |
| Repeated use | **failed** — 1 of the proposed 2 external maintainers held a 4-week streak | external-study `repeat_use_2x4wk_90d` |
| Operator burden | total burden **23.5 ≥ 23.0** min/issue — amortized setup+maintenance erases the per-issue saving *before* a second lane exists | `pilot-evaluation` burden block |
| Documented demand | **insufficient** — the only recorded alternate-runtime request was denied as `unsupported-workload:runtime-mirror`; no cohort issue failed for want of a second harness | external-study `admissions.denied-wl`; cohort outcomes |
| Candidate capability | exactly one documented candidate — `codex-cli 0.160.0` via `hermes codex-runtime` — but every substantive matrix row is **unproven** and Hermes lifecycle integration is a recorded **no-go** | candidate fixture + boundary map + readiness archive |
| Endpoint compatibility | **unproven** — the selected lane itself still carries open live blockers (`base-unprotected`, `telegram-adapter-live`, `vercel-linkage`, `kanban-live-write`) | release-gate ledger |

## Recorded disposition — admission deferred, with measured reasons

Per task 5.1 A1's own rule — an incomplete pilot, a narrow/stop
outcome or missing repeated-use evidence records blocked/deferred
rather than admitting expansion — three independent triggers hold
(narrow outcome pending owner confirmation; repeat-use target failed;
no documented demand). The A3 probes additionally reject admission
with actionable evidence: no supported lifecycle integration path for
the candidate exists today.

The recorded disposition is therefore **admission-deferred**:

- **Adopted candidate/version:** none — no candidate is proposed for
  adoption.
- **Support/trust recipe:** none — the existing v1.1 recipe-scoped
  package (`docs/recipes/`, `docs/releases/v1.1/`) is unchanged.
- **Bounded test scope:** none — task 5.2 remains deferred; no adapter
  code executes (`adapter_execution: none`, audited).
- **Cost/capacity decision:** incremental maintenance is documented
  but unjustified — the measured maintenance burden already erases
  per-issue savings at one lane.
- **Acceptance gates:** the re-entry conditions below are the gate
  list any future adoption must satisfy.

## Scope lock (A5 — reassessed on measured pilot evidence)

- One active task/project at a time — retained (cohort audited 22/22
  samples clean).
- Zero additional adapters admitted; at most one is ever in scope, and
  only after explicit adoption.
- The selected preview provider stays the only provider; no broader
  harness/provider abstraction is introduced.
- No dates promised; concurrency and cross-project isolation
  unchanged.

## Re-entry conditions (what re-opening requires)

- [ ] Owner records a confirmed `continue` verdict in
      `docs/decisions/continue-narrow-stop.md`
- [ ] A live (not fixture) repeat-use observation meeting the proposed
      bound, or an owner-accepted adjusted target
- [ ] Documented demand: ≥2 favorable measured signals for the
      candidate runtime lane
- [ ] The candidate's capability/authority matrix proven row-by-row on
      its own lane — supported-for-the-selected-lane never transfers
- [ ] `hermes codex-runtime` demonstrated against the kanban contract,
      or a documented supported primitive replacing it
- [ ] Operator adoption recorded here with bounded test scope,
      cost/capacity and acceptance gates — only then may 5.2 start

## What this record does *not* do

- It does not claim F13 delivered, does not imply new support, and
  does not execute adapter code — a completed assessment is not
  permission to implement (§8.4).
- It does not fabricate demand, waive the failed repeat-use target,
  or promote fixture verdicts to live-target attainment.
- It does not weaken the MVP endpoint, the one-active-task limit or
  any release gate — `release-withheld` and the open v1.0 ledger items
  stand unchanged.

## Decision record

- [ ] **Adopt** — owner adopts exactly one named candidate/version
      with support/trust recipe, bounded test scope, cost/capacity and
      acceptance gates recorded inline; only this authorizes 5.2
- [ ] **Defer confirmed** — owner confirms the evidence-supported
      deferral; the re-entry conditions stay as the standing gate
- [ ] **Reject** — owner retires the F13 expansion track; the assessed
      candidate is recorded as rejected with reasons

**Operator decision:** _pending — the assessment recorded
admission-deferred on measured evidence 2026-10-05; adoption,
confirmed deferral or rejection is the owner's act._

**Recorder:** issue-resolver run for #32 — evidence audited and
cross-checked; no gate waived, no demand fabricated, no adapter code
executed.
