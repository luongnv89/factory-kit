# Decision record: continue / narrow / stop (issue #30 / Task 4.6)

- **Status:** Recorded — **narrow/consolidate** is the evidence-supported
  disposition; **pending owner confirmation** (2026-10-05)
- **Owner:** Luong — `continue` is never self-recorded; A3 requires an
  explicit owner verdict against the assembled evidence
- **Scope:** §1.4 usefulness decision (MET04/MET05/MET06/MET07) with the
  §8.3 GATE-P02 operator-burden comparison and §9.1 Q9 status; PRD
  §9.3's "consolidate instead of expanding if results fail" trigger
- **Evidence:** `docs/measurements/pilot-evaluation.md` +
  `docs/measurements/pilot-evaluation-2026-10-05.json` (aggregation:
  `tools/probes/pilot_evaluation.py`, verdict `evaluation-complete`),
  over `v1-targets-2026-10-05.json`,
  `dogfood-comparison-2026-10-05.json`,
  `external-study-2026-10-05.json` and
  `fault-matrix-2026-10-05.json`

## Recorded evidence position

| Signal | Measured | Reading |
|---|---|---|
| Internal §5.1/Q9 local targets | 10/10 **pass** on the selected host | Mechanics proven — local-boundary, scripted ports |
| Cohort preview-merge-ready | **7/10 < 8** | **failed** on the fixture cohort |
| Cohort human-approved merges | 4 ≥ 3 | pass — fixture window, scripted human decisions |
| Intervention reduction vs baseline | 30.4% lower median | **inconclusive** — c9 effort record missing |
| Total burden incl. setup/maintenance | **23.5 vs 23.0** min/issue | amortized overhead **erases** the per-issue saving |
| External onboarding / independent setup | 3/4 and 2/4 | pass on fixtures — obs-gamma needed author takeover |
| External repeat use | **1/2 < 2** | **failed** — one 4-week streak, one dropout, one blocked install |
| Safety/admission evidence | clean | consent ordering, admission gate, denominators, fault matrix all clean |

## Recorded disposition — narrow/consolidate, with measured reasons

A3's triggers hold on the recorded evidence:

1. **Failed proposed targets** — internal usefulness (7/10
   merge-ready) and external repeat adoption (1/2 maintainers) both
   miss their proposed bounds on the fixture studies.
2. **Author intervention was required** — `obs-gamma`'s setup needed
   author takeover (1 of 4 admitted), and `obs-beta` a recorded
   clarification; cohort intervention categories include correction
   and investigation work, and unsuccessful issues cost a median
   18.5 min vs 13.0 for successes.
3. **Integration maintenance erases savings** — amortized
   setup+maintenance (7.5 min/issue) lifts total burden to 23.5
   min/issue, **above** the 23.0 baseline median: the measured
   30.4% per-issue reduction does not survive overhead accounting.

Per PRD §9.3 and the §8.3 gate, the recorded disposition is therefore
**narrow/consolidate**: fold the proven mechanics into the supported
Hermes/IDD recipes rather than expand a platform the evidence does
not support. This is a measured recommendation recorded for owner
confirmation — not a fabricated acceptance, and not a `stop`: the
local mechanics passed every bound and the safety/admission evidence
is clean.

## Scoped follow-up (narrow path)

- Consolidate the proven mechanics into the supported recipes —
  reviewed additive setup/readiness/removal, versioned
  upgrade/repair/rollback, the revision-bound verification contract,
  one-use approval + guarded merge (`docs/recipes/` is the package).
- Keep the **one-active-task** support limit; no concurrency,
  runtime, provider or adapter capability is admitted.
- Task 5.1's optional-harness admission stays **blocked** — a narrow
  outcome never authorizes §8.4 expansion work.
- Task 4.7 ships the **recipe-scoped** v1.1 deliverable under its A4
  release-withheld path — evidence and support recipes publish,
  expansion claims do not.

## What this record does *not* do

- It does not record `continue` — that is the owner's explicit act
  against this evidence.
- It does not claim live-target attainment from fixture verdicts, and
  does not hide the failed targets, the missing c9 effort record, the
  takeover, or the missing weeks.
- It does not authorize expansion, external data collection beyond
  the admitted pilot scope, or publication claims (Q8 stays
  unresolved).
- It does not waive any release requirement — GATE-M02 and Q9 keep
  their own owners and evidence bars.

## Required next observation (if the owner revisits toward continue)

Every unresolved item from the evaluation stays named: Q9's owner
verdict; the live cohort prerequisites (second project, 30-day
window, real human decisions, live baseline); the live pilot
prerequisites (external participants, 60/90-day windows, independent
timing, real environments, four live reporting weeks); and the
missing-data items (c9 effort, obs-delta install, obs-gamma weeks).
An owner `continue` verdict against *fixture-only* evidence would
itself be recorded here as a decision override — the evidence does
not support it today.

## Decision record

- [ ] **Narrow/consolidate confirmed** — fold proven mechanics into
      the Hermes/IDD recipes per the scoped follow-up (the
      evidence-supported disposition recorded by this evaluation)
- [ ] **Continue** — owner grants against the assembled evidence;
      each overridden unresolved item named in a comment row
- [ ] **Stop** — owner retires the platform track; recipes remain as
      reference documentation

**Owner decision:** _pending — the evaluation recorded
narrow/consolidate on measured evidence 2026-10-05; confirmation,
override or stop is the owner's act._

**Recorder:** issue-resolver run for #30 — evidence aggregated and
cross-checked; no target waived, no gap hidden, no capability added.
