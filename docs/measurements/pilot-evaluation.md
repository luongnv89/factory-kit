# §1.4/§8.3 usefulness evaluation — issue #30 (task 4.6)

The aggregated evidence table the continue / narrow / stop decision
rests on: every proposed internal and external numerical target,
cross-checked cell-by-cell against the recorded source archives.
The executable aggregation is `tools/probes/pilot_evaluation.py`;
the recorded run below is
`docs/measurements/pilot-evaluation-2026-10-05.json`; the decision
record this feeds is
[docs/decisions/continue-narrow-stop.md](../decisions/continue-narrow-stop.md).

**What this evidence is.** A read-only audit over committed recorded
runs — the §5.1 local-target run (`v1-targets-2026-10-05.json`), the
scripted ten-issue sequential dogfood cohort
(`dogfood-comparison-2026-10-05.json`), the scripted external-pilot
observation study (`external-study-2026-10-05.json`) and the §8.2
fault matrix (`fault-matrix-2026-10-05.json`). Every emitted cell is
re-derived from its archive and cross-checked (`cell_crosscheck`
audit): medians, reduction percentages, takeover exclusions and
qualified counts are recomputed, not transcribed.

**What it is not.** Not a new measurement — nothing re-runs a study,
and no fixture verdict is promoted to a live-target claim. Not the
owner's decision — the disposition is a recorded *recommendation
with measured reasons*; `continue` stays the owner's act (issue A3).
Not an expansion authorization of any kind.

## A1 — the unified evidence table

Verdicts are over the recorded fixtures; `live_status` stays pending
on every study row. Missing data is named per row, never absorbed.

### Internal — §5.1 local targets (Q9 evidence)

Source: `v1-targets-2026-10-05.json` (`targets-passed`), selected host
M1 macOS 27.0.1 arm64, Python 3.14.7, scripted ports — local-boundary
measurements, not end-to-end service claims.

| # | Proposed target | Measured | Window | Denominator | Verdict |
|---|---|---|---|---|---|
| T1 | intake accept/reject p95 ≤ 2 s at 1 ev/s | p95 11.73 ms | 5 min paced (302.0 s wall) | n=300 deliveries | **pass** |
| T2 | 100 duplicate deliveries ≤ 10 s incl. restart → 1 task, ≤1 PR | 52.3 ms → 1 row, 0 PRs | 10 s | 100 deliveries | **pass** |
| T3 | local status p95 ≤ 1 s | p95 0.24 ms | — | n=100 queries | **pass** |
| T4 | pause/cancel + fence persist ≤ 5 s | handler p95 4.0 ms; commit p95 1.5 ms | — | n=30 actions | **pass** |
| T5 | worker exit/quarantine + notify ≤ 30 s | 2.9 ms / 3.4 ms | 30 s bound | — | **pass** |
| T6 | reconcile ≤ 60 s; discovery ≤ 120 s | interval clamped 60.0 s; discovery ≈1.0 s | 60/120 s bounds | — | **pass** |
| T7 | recover/park known tasks ≤ 60 s | 6.1 ms wall | 60 s deadline | 4/4 settled | **pass** |
| T8 | 0 lost acknowledged tasks | 0 | — | 57 fault reps | **pass** |
| T9 | 0 idle model calls | 0 | 30 idle min | — | **pass** |
| T10 | A6 structural bounds | 10/10 checks | — | 10 checks | **pass** |

Missing data: none. Uncertainty: single host, single run, scripted
ports — margins are large but this is *this host's* evidence.

### Internal — §1.4 cohort targets (MET04 / MET05 / GATE-P02)

Source: `dogfood-comparison-2026-10-05.json` (`cohort-run-complete`),
ten scripted issues across two sequential registrations, recorded
ten-issue direct-IDD baseline.

| Proposed target (exact) | Measured | Window | Denominator | Missing | Verdict |
|---|---|---|---|---|---|
| ≥8 of 10 issues preview-verified and merge-ready | **7** | 30-day post-MVP window | 10 cohort issues | — | **fail** |
| ≥3 human-approved and merged within 30 days | **4** | 30 days | 10 cohort issues | — | **pass** (fixture window) |
| ≥25% lower median intervention minutes vs baseline | **30.4% lower** (16.0 vs 23.0 min) | 30 days | cohort n=9 known / baseline n=10 | **c9 effort record** — retained, never zero-filled | **inconclusive** |

### External — §1.4 pilot targets (MET06 / MET07)

Source: `external-study-2026-10-05.json`
(`external-pilot-run-complete`), scripted distinct-environment
rehearsal under the Task 4.4 consent/admission gate.

| Proposed target (exact) | Measured | Window | Denominator | Missing / excluded | Verdict |
|---|---|---|---|---|---|
| ≥3 complete install + first run within 60 d | **3** | 60 days | 4 admitted | obs-delta `unsupported-version` install-blocked — retained in denominator; denied-wl / nonconsent excluded pre-admission | **pass** |
| ≥2 complete setup ≤45 min without takeover | **2** (38.0 / 44.0 min) | setup | 4 admitted | obs-gamma **takeover** — excluded from the independence numerator, never hidden | **pass** |
| ≥2 participants, ≥1 completed run/week × 4 consecutive weeks within 90 d | **1** | 90 days | 4 admitted | obs-gamma wk3–4 missing (dropout), obs-delta wk1–4 missing; 1 failed run retained | **fail** |

### Q9 — adoption status, recorded not adjusted

`docs/decisions/q9-acceptance.md` remains **proposed — pending owner
acceptance** (verbatim from both study archives). Every §5.1 local
target passed on the selected host, so the measured evidence supports
acceptance — but no verdict is recorded here, and no baseline or
proposed bound is retroactively edited. The adjustment table stays
empty: there is nothing measured to adjust *against*.

## A2 — operator burden vs the ten-issue baseline

Intervention minutes are human-recorded active time; generated code
volume is never equated with value.

| Quantity | Cohort | Baseline |
|---|---|---|
| median intervention min/issue | **16.0** (n=9 known; c9 missing) | **23.0** (n=10) |
| successful issues median | 13.0 (merged/merge-ready) | — |
| unsuccessful issues median | 18.5 (declined/failed/canceled/blocked) | — |
| setup + maintenance | 45 + 30 = 75 min → **7.5 min/issue** amortized | — |
| **total burden median** | **23.5 min/issue** | 23.0 min/issue |

- Headline reduction: **30.4%** lower per-issue intervention —
  **inconclusive** on one missing effort record, and a fixture
  medians comparison only.
- With setup/maintenance amortized, the median total burden is
  **0.5 min/issue above** baseline — integration overhead erases the
  measured per-issue saving on the recorded fixture.
- External study effort is honestly separated: **51.0 active builder
  minutes** vs calendar waits (recruitment 8–14 d, participant
  6–30 d) that never count as effort.
- Uncertainty: scripted ports remove network/CI jitter from the
  measured path; takeover/assistance minutes are fixture-recorded
  observations; n=9 vs n=10 medians with one gap.

## A3 — intervention signals (the continue/narrow/stop triggers)

- **Author takeover occurred:** `obs-gamma` completed setup in
  55.0 min *with author takeover* — 1 of 4 admitted participants;
  `obs-beta` needed a recorded clarification. The takeover counts in
  the onboarding denominator but is excluded from independence.
- **Maintenance erases savings:** total burden 23.5 ≥ baseline 23.0
  (measured above).
- **Failed targets:** cohort `preview_merge_ready` (7 < 8), pilot
  `repeat_use_2x4wk_90d` (1 < 2).

Each trigger is present in the recorded evidence → the evaluation
records **narrow/consolidate** as the evidence-supported disposition
with measured reasons and scoped follow-up — see the decision record.
`continue` is never computed here: it requires the owner's explicit
verdict against this evidence.

## A4 — safety, admission and unresolved items

**Safety/admission evidence is clean on the recorded fixtures:**
pilot consent-ordering 0 violations, admission-gate 0 mismatches,
observation minimization 0 violations, denominators 6/6 and 10/10
accounted, both secret scans clean, fault matrix
`fault-matrix-passed` (19 rows × 57 reps). Blocked admissions
(`denied-wl` unsupported workload, `nonconsent` never presented)
collected zero observation events and are named, not dropped.

**Unresolved — each blocks a positive claim, none is assumed away:**
Q9 adoption (owner verdict pending); all eleven live-observation
prerequisites carried from the two studies (live second project,
30-day window, real human decisions, live baseline, external
participants, pilot windows, independent timing, simulated
environments, live repeat use); and the c9 effort gap plus obs-delta
/ obs-gamma missing weeks recorded in the table above.

## Aggregation audits

`cell_crosscheck`, `denominator_integrity`, `missing_data_coverage`,
`takeover_retained`, `no_positive_claim_on_incomplete`,
`q9_recorded`, `secret_scan` — all clean on the recorded run
(`breaches: []`, verdict `evaluation-complete`). The audits are the
replayable contract: `tests/benchmarks/test_pilot_evaluation.py`
mutates the extracted evidence to prove unfavorable verdicts, hidden
takeovers, dropped missing-labels and failed admission evidence each
surface as breaches or a `stop`/`insufficient-evidence` disposition —
never a positive claim.

## Reproduce

```bash
python3 tools/probes/pilot_evaluation.py --write \
    docs/measurements/pilot-evaluation-$(date +%F).json
python3 tools/probes/pilot_evaluation.py --fixture \
    docs/measurements/pilot-evaluation-2026-10-05.json  # re-judge
python3 -m unittest tests.benchmarks.test_pilot_evaluation -v
```

Exit codes: `0` evaluation-complete, `1`
evaluation-instrumentation-failed, `2` usage, `4` cannot-complete
(source archive unreadable or wrong kind).
