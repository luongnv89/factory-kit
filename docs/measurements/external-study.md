# §1.4/§8.3 external pilot — independent installations and repeat use

Issue #29 (Task 4.5, GATE-P05 / MET06 / MET07). The proposed
independent-onboarding and repeat-use study on the published support
recipe, executed under the Task 4.4 consent/admission gate without
hidden author takeover. The executable harness is
`tools/probes/external_pilot.py`; fixtures live in
`tests/fixtures/pilot/`; the recorded run is
`docs/measurements/external-study-2026-10-05.json`; per-participant
minimized records are in
[docs/pilot/observations.md](../pilot/observations.md).

**What this evidence is.** A *scripted-harness* execution of the
external-pilot protocol on simulated distinct environments. The
consent gate, the `python3 -m factory_kit.setup` walkthrough legs, the
durable `pilot_observation_recorded` event trail and the consent-gated
aggregate export are real. Participants are fixtures; host
environments are simulated via recorded readiness readings plus
disposable repositories (macOS arm64, Linux x86_64, macOS x86_64, and
one Linux/aarch64 legacy host); elapsed minutes, assistance records,
first-run and weekly outcomes are fixture-recorded observations.

**What it is not.** Not a live external study: no real external
maintainers were recruited, no 60/90-day window elapsed, setup timing
is fixture-recorded rather than independently observed, and installs
never touched participant production environments. Fixture verdicts
are never live-target claims — the open prerequisites are named under
*Live-observation prerequisites* below.

## A1 — protocol and minimized fields, recorded before recruitment

Recorded verbatim in `tests/fixtures/pilot/participants.json`
(the archive's `protocol` block): the `pilot-collection` scope, the
pinned walkthrough
([docs/recipes/installation.md](../recipes/installation.md) —
`plan`, `apply`, `readiness`, `status`, then `uninstall` +
`remove --history retain`), the agreed minimized field set
(`environment_ref`, elapsed minutes, assistance class, blockers,
first-run and weekly outcomes), the 60/90-day observation windows, the
three proposed targets, and the rule that download/star counts are
never run outcomes.

## A2 — onboarding targets, reported exactly

| Target | Proposed | Measured | Verdict | Basis |
|---|---|---|---|---|
| `onboarding_completed_60d` | ≥3 complete install + first run within 60 d | 3 of 4 admitted | **pass** | obs-alpha d12 · obs-beta d19 · obs-gamma d27; obs-delta install-blocked |
| `independent_setup_2_in_45min` | ≥2 complete setup ≤45 min without takeover | 2 of 4 | **pass** | obs-alpha 38.0 · obs-beta 44.0 min |

**Takeover is excluded, not hidden.** `obs-gamma` completed setup in
55.0 min **with author takeover** — the completion counts toward the
onboarding leg but is excluded from the independence threshold
(`takeover_excluded: ["obs-gamma"]`). `obs-delta`'s install is a named
failure (`unsupported-version`), labeled and retained — it counts in
neither numerator.

## A3 — repeat-use target, reported exactly

| Target | Proposed | Measured | Verdict | Basis |
|---|---|---|---|---|
| `repeat_use_2x4wk_90d` | ≥2 participants, ≥1 completed run/week for 4 consecutive weeks within 90 d | **1** of 4 | **fail** | only obs-alpha holds a 4-week completed streak |

A `fail` on a proposed target is a legitimate study outcome: beta's
week-3 run *failed* (a retained attempt, never a completed run) and
gamma dropped out after week 2 (missing weeks labeled). Denominators
stay intact — 9 completed / 1 failed / 6 missing weekly reports across
the cohort, `failed_runs_retained: 1` on the target row.

## A4 — blocked, missing and inconclusive rows keep their denominators

| Row | Class | Recording |
|---|---|---|
| `denied-wl` | admission denied | durable denial row, blocker `unsupported-workload:runtime-mirror`; collection attempt blocked `admission-denied:…`; zero observation events |
| `nonconsent` | consent never presented | no admission row exists; collection attempt blocked `consent-missing`; zero observation events |
| `obs-delta` | installation failure | readiness exit 1, `blocker_codes: [unsupported-version]`; first run `not-attempted`, removal `not-applicable`, weeks `missing` — labeled, never dropped |
| `obs-gamma` | dropout | weeks 3–4 `missing`; `dropout.after_week: 2` recorded |

No participant data collection bypassed the Task 4.4 admission:
`open_collection` is re-verified at collection time, and the audit
replays durable seq ordering — every observation event follows its
consent event (0 violations), and the denied/never-presented hashes
carry zero events.

## A5 — builder work separated from calendar waits; replayable evidence

`study_effort` separates 51.0 active builder minutes from calendar
waits that never count as effort: recruitment wait (8/11/14/9 days)
and participant wait (30/28/24/6 days) per participant. The archived
report re-evaluates to identical verdicts
(`python3 tools/probes/external_pilot.py --fixture <archive>`), the
whole payload is canary-free (`secret_scan: clean`), and the
consent-gated `pilot_export` produced 35 aggregate-safe events with
the denied authority excluded (`ext-denied-wl`).

## Live-observation prerequisites

A scripted rehearsal cannot satisfy these — they stay open as named
blockers, exactly like the dogfood cohort's `live_prerequisites`:

- `q9-adoption` — the numerical targets are still proposed, pending
  owner acceptance (`docs/decisions/q9-acceptance.md`).
- `external-participants` — no real external maintainers were
  recruited; independence is fixture-recorded, not observed.
- `pilot-windows` — the 60-day onboarding and 90-day repeat-use
  windows have not elapsed.
- `independent-timing` — elapsed setup minutes are recorded
  observations, not independently measured wall-clock.
- `simulated-environments` — installs ran on disposable repos with
  recorded readings, never participant production hosts with live
  Hermes.
- `live-repeat-use` — four consecutive live reporting weeks remain
  unobserved.

## Verdict

`external-pilot-run-complete` — all instrumentation audits clean
(consent ordering, admission expectations, denominator integrity,
observation minimization, secret scan). Study-target verdicts are data
on the scripted fixture: onboarding **pass**, independent setup
**pass**, repeat use **fail** (1 of 2 required qualifiers). Exit 0.

Reproduce:

```bash
python3 tools/probes/external_pilot.py \
    --write docs/measurements/external-study-$(date +%F).json
python3 -m unittest tests.benchmarks.test_external_pilot -v
```
