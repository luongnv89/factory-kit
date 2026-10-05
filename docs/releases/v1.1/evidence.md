# v1.1 evidence package — issue #31 (task 4.7, GATE-P03 / GATE-P06)

The reviewable conditional-release package for v1.1: reproducible
F09/F10 acceptance evidence, measured results *including the
failures*, the pinned support surface and the known-failure register.
The release decision lives in
[release-checklist.md](release-checklist.md); the onboarding
walkthrough in [onboarding.md](onboarding.md). The recorded gate audit
is `release-gate-2026-10-05.json` (runner:
`tools/probes/release_gate.py`).

**What this package is.** A *reviewable* release result assembled
under the issue's conditional-release contract. Every claim below
traces to a committed artifact a reviewer can re-run or re-check —
the same fixture identity and honesty rules as
[../../evidence/v1.0-gate.md](../../evidence/v1.0-gate.md).

**What it is not.** Not a published release — the gate audit records
**release-withheld** (recipe-scoped deliverable) on this tree. Not a
live-target claim: the cohort and pilot numbers are *scripted-fixture*
results under the recorded harnesses, never live measurements. Not an
expansion authorization: no concurrency, runtime, provider or adapter
capability is admitted by anything here.

## F09 — checkpointed scope steering (acceptance evidence)

Requirement: authorized revised criteria apply at supported
role/stage checkpoints, context revisions and generation identity are
immutable, execution continues only within remaining explicit budgets
(F09, GATE-P03 — issue #25 / task 4.1).

| Criterion | Evidence | Reproduce |
|---|---|---|
| Durable revision before ack; duplicate/restart replay → one change | `tests/control/test_steering.py` `TestA1RecordedBeforeAck` | `python3 -m unittest tests.control.test_steering -v` |
| Fence-before-replace at checkpoints; remaining budgets carried, never reset | `TestA2FenceThenReplace` | same |
| Old-revision evidence dies with the revision; fresh evidence required | `TestA3EvidenceInvalidation` | same |
| No revival of canceled/fenced generations; unsupported live steering answered with checkpoint behavior | `TestA4NoRevival` | same |
| Safe parking on uncertain termination / exhausted budget; no overlapping authority | `TestA5SafeParkingAndRaces` | same |
| Command surface denies missing/oversized/unchanged criteria | `TestSteerCommandSurface` | same |
| Implementation | `factory_kit/control/steering.py`, `factory_kit/control/service.py` (`_do_steer`) | — |

## F10 — versioned upgrade, repair and rollback (acceptance evidence)

Requirement: reviewed compatible upgrades and repairs using
owned-file provenance, validated checkpoints and explicit rollback,
preserving conventions, secrets and user changes (F10, GATE-P03 —
issue #26 / task 4.2).

| Criterion | Evidence | Reproduce |
|---|---|---|
| Read-only digest-bound upgrade plan; conflicts named, never written | `tests/recipes/test_upgrade_recipe.py` `test_upgrade_names_conflicts_and_never_writes`, `test_migrate_refuses_a_tampered_plan` | `python3 -m unittest tests.recipes.test_upgrade_recipe -v` |
| Fenced staged migration; dispatch denied until readiness re-pass | `test_upgrade_migrate_and_readiness_resume` | same |
| Interrupted migration → `repair` restores the validated checkpoint | `test_repair_restores_the_checkpoint`; interruption legs in `tests/test_setup_upgrade.py` | `python3 -m unittest tests.test_setup_upgrade tests.recipes.test_upgrade_recipe -v` |
| Rollback restores prior validated digests under a fresh audited generation | `test_rollback_then_removal_leaves_no_orphaned_authority` | same |
| Repair with nothing open is a named error, never a write | `test_repair_with_nothing_open_is_a_named_error` | same |
| Implementation | `factory_kit/setup/upgrade.py`, `factory_kit/setup/repair.py` | — |

Fault-class coverage (interrupted migration, conflicting owner,
reinstall/uninstall with user edits, fenced cancellation) is in the
§8.2 matrix: `docs/evidence/fault-matrix-2026-10-05.json` — 19 rows ×
3 repetitions, verdict `fault-matrix-passed` — re-check with
`python3 tools/probes/fault_matrix.py --fixture
docs/evidence/fault-matrix-2026-10-05.json`.

## Pinned supported versions / skills / dependencies

The single supported pin set is [../../recipes/support-matrix.md](../../recipes/support-matrix.md)
— only rows exercised by the suite are listed; anything unlisted is
unsupported, not merely unverified. Headlines: `factory-kit 0.1.0`,
manifest schema `factory_kit: 1`, Python 3.14.7 stdlib-only (no
third-party dependencies), `hermes-kanban` ≥ 0.21.5, model
`openai-codex / gpt-6-luna`, skills `issue-resolver: 0.19.0` +
`issue-pr-review: 0.19.0` (pinned, `auto_discover: false`), Vercel
previews only, human-approved squash merge, one active task.

## Measured results — consented aggregates (A3)

Verbatim from the recorded evaluation archive
(`docs/measurements/pilot-evaluation-2026-10-05.json`; aggregation
`tools/probes/pilot_evaluation.py`, verdict `evaluation-complete`).
Proposed status is *proposed* — Q9 owner acceptance stays pending;
every row's basis is scripted-fixture, `live_status` pending.

### Internal — §5.1 local targets (10/10 pass, selected host)

| Proposed target | Measured | Verdict |
|---|---|---|
| intake accept/reject p95 ≤ 2 s @ 1 ev/s × 5 min | p95 11.73 ms (n=300) | pass |
| 100 duplicate deliveries ≤ 10 s incl. restart → 1 task, ≤ 1 PR | 52.3 ms → 1 row, 0 PRs | pass |
| local status p95 ≤ 1 s | p95 0.24 ms (n=100) | pass |
| pause/cancel + fence persist ≤ 5 s | handler p95 4.0 ms | pass |
| worker exit/quarantine + notify ≤ 30 s | 2.9 ms / 3.4 ms | pass |
| reconcile ≤ 60 s; discovery ≤ 120 s | 60.0 s / ≈1.0 s | pass |
| recover/park known tasks ≤ 60 s | 6.1 ms (4/4) | pass |
| 0 lost acknowledged tasks | 0 in 57 fault reps | pass |
| 0 idle model calls (30 min) | 0 | pass |
| A6 structural bounds | 10/10 checks | pass |

### Internal cohort — §1.4 targets (the measured failure)

| Proposed target | Measured | Denominator | Verdict |
|---|---|---|---|
| ≥8 of 10 issues preview-verified and merge-ready | **7** | 10 cohort issues | **fail** |
| ≥3 human-approved merges within 30 days | 4 | 10 cohort issues | pass (fixture window) |
| ≥25% lower median intervention vs baseline | 30.4% lower (16.0 vs 23.0 min) | n=9 known / n=10 | **inconclusive** — c9 effort record missing, never zero-filled |
| total burden incl. setup/maintenance | **23.5 vs 23.0 min/issue** | amortized 7.5 min/issue | overhead **erases** the per-issue saving |

### External pilot — §1.4 targets (the measured failure)

| Proposed target | Measured | Denominator | Verdict |
|---|---|---|---|
| ≥3 install + first run within 60 d | 3 | 4 admitted | pass — obs-delta `unsupported-version` blocked, retained |
| ≥2 complete setup ≤45 min w/o takeover | 2 (38.0/44.0 min) | 4 admitted | pass — obs-gamma **takeover** excluded from independence, never hidden |
| ≥2 participants × 4 consecutive weekly runs in 90 d | **1** | 4 admitted | **fail** — dropout + blocked install + missing weeks retained |

### Observation limits (A3, disclosed not hidden)

Fixture-only evidence: no real external maintainers, no elapsed
60/90-day windows, simulated environments, fixture-recorded timing,
scripted human decisions, fixture baseline, missing c9 effort record,
obs-delta install never completed, obs-gamma weeks 3–4 missing, Q9
unadopted. The full unresolved register is `unresolved[]` in the
evaluation archive (13 items) — each blocks a live-target claim.

## Known failures and live limits

- **Failed targets (kept in denominators):** cohort
  preview-merge-ready 7/10 (<8); external repeat use 1/2 (<2);
  intervention reduction inconclusive on the missing c9 record;
  total burden 23.5 ≥ 23.0 baseline.
- **Pilot gaps:** obs-gamma author takeover (1 of 4 admitted);
  obs-beta clarification; obs-delta blocked at readiness
  (`unsupported-version`); denied-wl refused
  (`unsupported-workload:runtime-mirror`); nonconsent never
  collected.
- **v1.0 outstanding live items (from the gate audit):**
  `base-unprotected` (live-verified 2026-10-05, 404 on
  `money-mind:main` protection), `telegram-adapter-live`,
  `vercel-linkage` (live smoke marker miss), `kanban-live-write`.
- **Explicitly unsupported:** any package-manager/public install
  command (none exists — Q8 unresolved), external CLI harness lanes,
  concurrent multi-task execution, additional
  runtimes/providers/adapters, autonomous merge.

## Reproduce

```bash
python3 -m unittest discover -s tests                     # full suite
python3 -m unittest tests.control.test_steering -v        # F09
python3 -m unittest tests.test_setup_upgrade \
    tests.recipes.test_upgrade_recipe -v                  # F10
python3 -m unittest tests.release.test_v1_1_walkthrough -v  # A5 walkthrough
python3 tools/probes/release_gate.py \
    --write docs/releases/v1.1/release-gate-$(date +%F).json
python3 tools/probes/release_gate.py --fixture \
    docs/releases/v1.1/release-gate-2026-10-05.json       # re-judge
```

**Recorder:** issue-resolver run for #31 — package assembled,
disclosure kept aggregate-only, no gate waived, no missing evidence
marked achieved, no capability added.
