# §1.4/§8.3 sequential dogfood cohort — issue #27 (task 4.3)

The proposed ten-issue internal-usefulness cohort, executed across two
sequential project registrations and compared against a recorded
ten-issue direct-IDD baseline. The executable harness is
`tools/probes/dogfood_cohort.py`; the cohort and baseline inputs are
`tests/fixtures/cohort/`; the recorded run below is
`docs/measurements/dogfood-comparison-2026-10-05.json` (`run_id`,
per-issue records, full metric inputs and both repo-scoped history
exports are in the archive).

**What this evidence is.** A *scripted-harness* execution of the cohort
protocol: every cohort issue drove the real merged services — durable
intake accept, attempt ledger with normalized usage, independent
review, remote PR + checks, the verification gate, preview deploy, the
approval decision path and the guarded merge — over the scripted
GitHub/preview/worker/issue-source ports, one active runtime task at a
time, project B's registration only after project A's cohort settled.
Operator intervention minutes and attempt usage are fixture-recorded
inputs written through the real durable trails
(`record_operator_effort`, `finish_execution_attempt`).

**What it is not.** Not a live 30-day dogfood: there is no real second
project, no live Hermes runtime, and no maintainer judgments on real
diffs. Fixture verdicts are never live-target claims — the open
live-run prerequisites are named under *Live-run prerequisites* below.

## A1 — protocol, recorded before selection

Recorded verbatim in `tests/fixtures/cohort/cohort.json` (the archive's
`protocol` block): trusted-maintainer selection; bounded inclusion
criteria (single PR-addressable issue, no secrets/personal-data scope,
checklist-shaped acceptance criteria, S/M size class comparable to a
direct-IDD fix); task mix 4 improvement / 3 feature / 2 bug /
1 documentation; the fixed endpoint
issue-accepted → preview-verified → human-approved → merged; and the
agreed target status — **Q9 proposed, pending owner acceptance**
(`docs/decisions/q9-acceptance.md`).

## A2 — sequential execution and the denominator

| Project | Identity | Issues | Sequential |
|---|---|---|---|
| project-a | `luongnv89/money-mind` (`R_kgDOQncOdA`) | c1–c5 | first leg — settled before B |
| project-b | `luongnv89/factory-kit-selftest` (`R_kgDOFkDogfoodB`) | c6–c10 | second leg |

The one-active-task audit sampled active work at every stage boundary —
0 violations across 22 samples. Every issue ends accountable: 7 carry
verified evidence whose head matches the remote head, the other 3 carry
explicit terminal states (`failed`/`canceled`/`blocked`) — nothing
dropped. Human declines are recorded separately (c4).

| Issue | Project | Class | Scripted path | Outcome | Intervention min |
|---|---|---|---|---|---|
| c1 | a | improvement | merged | merged | 13 |
| c2 | a | feature | merged | merged | 25 |
| c3 | a | improvement | merge-ready (awaiting decision) | merge_ready | 9 |
| c4 | a | bug | declined | declined (blocked: approval-rejected) | 21 |
| c5 | a | improvement | merged | merged | 6 |
| c6 | b | feature | merged | merged | 16 |
| c7 | b | bug | attempt failed | failed (blocked: attempts) | 20 |
| c8 | b | improvement | canceled | canceled | 10 |
| c9 | b | documentation | merge-ready | merge_ready | **missing** |
| c10 | b | feature | verification blocked | blocked | 17 |

## A3 — the proposed targets, reported exactly

Verdicts are over the recorded fixture; `live_status` stays
`pending-live-cohort` on every row.

| Proposed target (exact) | Measured | Verdict |
|---|---|---|
| at least 8 of 10 issues preview-verified and merge-ready | **7/10** | **fail** |
| at least 3 human-approved and merged within 30 days of MVP release across two sequential registrations | **4** | **pass** (fixture window) |
| at least 25% lower median intervention minutes per issue than the ten-issue comparable baseline within that 30-day window | **30.4% lower** (16.0 vs 23.0 min) | **inconclusive** — one missing effort record |

The preview-merge-ready target **failed on the fixture cohort** — a
legitimate study outcome, reported rather than excluded or replaced.

## A4 — operator burden

- **Intervention minutes per issue:** cohort median **16.0** (n=9
  known; c9 missing — counted, never zero-filled); baseline median
  **23.0** (n=10). Successful issues (merged/merge-ready) median 13.0;
  unsuccessful (declined/failed/canceled/blocked) median 18.5 — the
  burden of failures is measured, not assumed away.
- **Amortized overhead, separately:** setup 25 + 20 = 45 min across the
  two registrations, maintenance 30 min → **7.5 min/issue** amortized;
  total-burden median **23.5 min/issue** — a hair *above* the baseline
  median with overhead included, which is exactly the honest shape the
  §1.4 operator-time question asks for.
- **Wait classes, separated:** queue 150 s · model execution 8,230 s ·
  CI wait 1,260 s · human wait 15,360 s measured; each class also
  carries its unknown count (c10's two unknown-usage attempts → 2 per
  class).
- **Usage fields, distinct:** 18 attempts — tokens known 16, billed
  known 15 (c7's billed amount is honestly unknown), quota known 16,
  usage entirely unknown 2. None substitutes for the others.

## A5 — honest gaps and the reproducible export

- c9's operator effort is **missing** — reported `missing`, retained in
  the denominator, and it is what makes the reduction target
  `inconclusive` rather than `pass`.
- The reproducible metric inputs are the archive itself: per-issue
  records plus both projects' guarded history exports
  (`privacy.export.history_export` — detail columns absent, every
  value secret-scanned). `secret_scan: clean` over the whole report.
- Baseline comparability is checked, not assumed: size, same
  maintainer, same endpoint standard, same 30-day collection window,
  all minutes recorded — a failing check flips the comparison to
  `inconclusive`.

## Live-run prerequisites (named blockers)

The scripted cohort validates the instrumentation; the *live* §1.4
comparison stays open on:

1. **q9-adoption** — the numerical targets are still proposed; owner
   acceptance is pending (`docs/decisions/q9-acceptance.md`).
2. **second-project-registration** — no live second project with a live
   Hermes runtime exists; both legs ran on scripted ports.
3. **thirty-day-window** — the 30-day post-MVP-release observation
   window has not elapsed.
4. **human-decisions** — approvals/declines were scripted inputs through
   the real decision path, not maintainer judgments on real diffs.
5. **baseline-collection** — a live baseline needs ten comparable
   direct-IDD issues logged in-window.

## Reproduce

```bash
python3 tools/probes/dogfood_cohort.py --write \
    docs/measurements/dogfood-comparison-$(date +%F).json
python3 tools/probes/dogfood_cohort.py --fixture \
    docs/measurements/dogfood-comparison-2026-10-05.json  # re-check
python3 -m unittest tests.benchmarks.test_dogfood_cohort -v
```

Exit codes: `0` cohort-run-complete (audits clean — study verdicts are
data), `1` instrumentation-failed, `2` usage, `4` cannot-complete.
