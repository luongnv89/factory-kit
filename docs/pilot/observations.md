# External-pilot participant observations

Issue #29 (Task 4.5) — the per-participant minimized evidence log for
the proposed independent-onboarding and repeat-use study (PRD §1.4,
§8.3 GATE-P05 / MET06 / MET07). Target results and audits live in
[docs/measurements/external-study.md](../measurements/external-study.md);
the recorded run is
`docs/measurements/external-study-2026-10-05.json`; the executable
harness is `tools/probes/external_pilot.py` on the
`tests/fixtures/pilot/` fixtures.

**What this evidence is.** A *scripted-harness* rehearsal of the study
on simulated distinct environments: the consent/admission gate
(`factory_kit/privacy/consent.py`), the pinned
`python3 -m factory_kit.setup` walkthrough legs
([installation recipe](../recipes/installation.md)) and the durable
`pilot_observation_recorded` event trail are all real; participants,
host environments, elapsed minutes, assistance records and weekly
reports are fixture-recorded.

**What it is not.** Not a live external study — no real external
maintainers, no elapsed 60/90-day windows, no participant production
hosts. Live-observation prerequisites are named blockers in the study
report (`live_prerequisites`); nothing here is a live-target claim.

## 1. Admission state (gate first)

`consent.record_obligations` recorded the `pilot-collection` scope terms
before any participant row could exist. Each fixture participant then
passed through the real `consent.admit_participant` gate:

| Participant | Admission | Blocker |
|---|---|---|
| `obs-alpha` | admitted (`observation` / `external-reviewed`) | — |
| `obs-beta` | admitted (`observation` / `external-reviewed`) | — |
| `obs-gamma` | admitted (`observation` / `external-reviewed`) | — |
| `obs-delta` | admitted (`observation` / `external-reviewed`) | — |
| `denied-wl` | **denied** | `unsupported-workload:runtime-mirror` |
| `nonconsent` | never presented — no admission row | `consent-missing` |

Collection opened only for admitted rows — `open_collection` is
re-checked at collection time. Both blocked legs recorded zero
observation events; the durable trail carries `participant_hash` only,
never a raw participant reference.

## 2. Per-participant observation records (agreed minimized fields)

| Participant | Environment (simulated) | Install outcome | Elapsed | Assistance | First run | Removal |
|---|---|---|---|---|---|---|
| `obs-alpha` | `env-alpha` — macOS 25.1 arm64, py3.14.7 | completed (day 12) | 38.0 min | none | completed | completed |
| `obs-beta` | `env-beta` — Linux 6.8 x86_64, py3.13.4 | completed (day 19) | 44.0 min | clarification (recorded, not takeover) | completed | completed |
| `obs-gamma` | `env-gamma` — macOS 24.6 x86_64, py3.14.0 | completed (day 27) | 55.0 min | **takeover — recorded** | completed | not-attempted |
| `obs-delta` | `env-delta` — Linux 5.15 aarch64, py3.11.8, hermes 0.20.0 | **blocked** — `unsupported-version` | n/a | none | not-attempted | not-applicable |

Each completing participant ran the pinned legs as real CLI exits:
`plan` → `apply` → `readiness` → `status` → `uninstall` →
`remove --history retain`. `obs-gamma` stopped before removal (recorded
`not-attempted` — participant dropped out). `obs-delta` stopped at
`readiness`: exit 1, `blocker_codes: ["unsupported-version"]` — the
unsupported hermes pin refused, and nothing downstream ran. The
install-failure row is labeled and retained in every denominator.

**Author takeover is explicit:** `obs-gamma`'s setup was driven by the
author (55.0 min, `author_takeover: true`). The completion counts
toward the onboarding denominator but is excluded from the
independence threshold — recorded, not hidden.

## 3. Weekly repeat-use reports

| Participant | W1 | W2 | W3 | W4 | Longest completed streak |
|---|---|---|---|---|---|
| `obs-alpha` | completed | completed | completed | completed | 4 |
| `obs-beta` | completed | completed | **failed** | completed | 2 |
| `obs-gamma` | completed | completed | missing | missing | 2 (dropout after W2) |
| `obs-delta` | missing | missing | missing | missing | 0 |

Classification contract (A3): `completed` counts as a run; `failed` is
a retained attempt — visible in totals (9 completed / 1 failed /
6 missing), never counted as a run; `missing` is a labeled absence.
Download/star counts are not run outcomes and are never collected.

## 4. Consent-ordering and minimization audits

- `consent_ordering` — every `pilot_observation_recorded` event's
  durable seq follows its participant's `pilot_consent_recorded` seq;
  zero events exist under the denied or never-presented hashes.
  **0 violations.**
- `observation_minimization` — all 28 recorded beats pass the §7.1
  aggregate-safety check; no `participant_ref`, free text or
  content-bearing field entered the trail. **0 violations.**
- `denominator_integrity` — 6 recruited, 6 accounted, none dropped.
- `admission_gate` — every expected admission outcome (including the
  named denial blocker) matched the real gate. **0 mismatches.**
- `secret_scan` — the whole report payload is canary-free: **clean.**

## 5. Reproduce

```bash
python3 tools/probes/external_pilot.py \
    --write docs/measurements/external-study-$(date +%F).json
python3 tools/probes/external_pilot.py --fixture <archive>.json   # re-judge an archive
python3 -m unittest tests.benchmarks.test_external_pilot -v        # 21 mechanics tests
```
