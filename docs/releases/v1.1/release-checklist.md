# v1.1 release checklist — issue #31 (task 4.7, A2/A4)

The conditional-release checklist. Each gate is evaluated by
`tools/probes/release_gate.py` against the committed source evidence —
the recorded run is `release-gate-2026-10-05.json`, re-judgeable with
`--fixture`. A gate is `passed` only on positive verification;
`blocked`, `pending` and `unknown` all keep the release withheld.
Nothing here publishes anything: publication follows the session's
applicable authorization and remains the owner's act.

## Gate ledger (recorded 2026-10-05)

| Gate | Requirement | State | Evidence / named blocker |
|---|---|---|---|
| `f09_f10_evidence` | GATE-P03 — reproducible F09 checkpoint/budget + F10 interrupted-migration/conflict/rollback evidence | **passed** | `tests/control/test_steering.py`, `tests/test_setup_upgrade.py`, `tests/recipes/test_upgrade_recipe.py`; fault-matrix verdict `fault-matrix-passed` (19 rows × 57 reps) |
| `support_recipes` | GATE-P06 inputs — pinned versions/skills/dependencies + install/upgrade/removal recipes | **passed** | `docs/recipes/` (installation, upgrade-repair, operations, backup-restore, support-matrix) — each locked by its recipe suite |
| `package_docs` | A1/A4 — reviewable package present | **passed** | `evidence.md`, `onboarding.md`, this checklist + recorded gate archive |
| `v1_0_gate` | A2 — passed v1.0 gate | **blocked** | GATE-M02 closed on the v1.0 audit ledger (`base-unprotected`, `telegram-adapter-live`, `vercel-linkage`, `kanban-live-write` outstanding); owner acceptance + Q9 verdicts pending |
| `consent_trust_admission` | A2 — consent/trust admission | **passed** | `docs/pilot/consent-admission.md`; study audits clean (consent-ordering 0 violations, admission-gate 0 mismatches, denominators 6/6) |
| `pilot_observations` | A2 — recorded pilot observations | **passed** | `docs/pilot/observations.md`; archive verdict `external-pilot-run-complete` (fixture study — live prerequisites disclosed, never claimed) |
| `owner_decision` | A2 — explicit continue/narrow/stop decision | **pending** | `docs/decisions/continue-narrow-stop.md` records **narrow/consolidate** with measured reasons — a *recommendation pending owner confirmation*, not a verdict |
| `q8_resolved` | A2 — Q8 license/distribution/support/notice resolved | **blocked** | all four Q8 rows `Unresolved` in `docs/decisions/q8-distribution.md` (no root LICENSE; no channel; no support commitment; no notice process) |

## Recorded outcome — release withheld (A4)

- **Disposition:** `release-withheld` — the checklist does not permit
  a public v1.1 release today.
- **Deliverable scope:** `recipe-scoped` — the package in this
  directory is the appropriately scoped deliverable under A4's
  withheld path: evidence and support recipes are reviewable;
  expansion claims are not made. (Recorded by the task-4.6
  evaluation: "Task 4.7 ships the recipe-scoped v1.1 deliverable
  under its A4 release-withheld path.")
- **Withheld reasons (denominator-preserving — every non-passed gate
  is named):**
  1. `v1_0_gate` blocked — GATE-M02 closed; owner acceptance pending.
  2. `owner_decision` pending — narrow/consolidate is recorded as the
     evidence-supported *recommendation*; the owner verdict is not.
  3. `q8_resolved` blocked — four distribution decisions unresolved.

### What publication requires (enumerated, not implied)

- [ ] Owner records a verdict in `docs/decisions/continue-narrow-stop.md`
      (confirm narrow/consolidate, override to continue with each
      overridden item named, or stop).
- [ ] Owner records internal-v1.0 acceptance in
      `docs/decisions/v1.0-acceptance.md` — which requires GATE-M02's
      outstanding live items cleared first.
- [ ] All four Q8 rows resolved in `docs/decisions/q8-distribution.md`.
- [ ] Live-observation prerequisites stay named — fixture verdicts
      are never promoted to live-target attainment (the 13-item
      `unresolved[]` register in
      `docs/measurements/pilot-evaluation-2026-10-05.json`).

### What this package never does (A4)

- Never marks missing/pending evidence as achieved — the failed
  targets (cohort 7/10, repeat-use 1/2, inconclusive intervention
  reduction, burden 23.5 ≥ 23.0) stay failed in every table.
- Never promises new concurrency, runtime, provider or adapter
  support; the one-active-task limit is retained verbatim.
- Never treats the withheld outcome as a failure of the audit — the
  honest record *is* the deliverable.

## Reproduce / verify

```bash
python3 tools/probes/release_gate.py            # audit this tree
python3 tools/probes/release_gate.py --write \
    docs/releases/v1.1/release-gate-$(date +%F).json
python3 tools/probes/release_gate.py --fixture \
    docs/releases/v1.1/release-gate-2026-10-05.json   # re-judge archive
python3 -m unittest tests.release.test_release_gate -v
```

The `test_release_gate.py` suite mutates the gate inputs to prove the
checklist fails closed: a failed-gate fixture stays `release-withheld`,
a fixture claiming `release-ready` over a non-passed gate is an
instrumentation breach, and a missing source evaluates `unknown` —
withheld — rather than passing silently.
