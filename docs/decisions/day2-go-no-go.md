# Decision record: day-2 go/no-go and replan disposition (issue #5, Task 1.4)

- **Status:** Recorded — 2026-10-05 — **NO-GO for broad MVP work until the
  timing/scope replan is explicitly resolved**
- **Scope:** PRD §8.1 day-2 stop/replan checkpoint; A5/A6 of Task 1.4
- **Evidence:** `docs/spike/fault-ledger.md` +
  `docs/spike/evidence/faults-2026-10-05.json` (`report.gate_ledger`,
  `report.day2`)

## The required checkpoint vs the measured serial effort (A5)

PRD §8.1 requires a stop/replan decision **at the end of working day 2
from an unscheduled T0** — a calendar checkpoint, independent of how much
effort has actually landed. `tasks.md` estimates Tasks 1.1–1.4 at
**~1 developer-day each, ~4 serial developer-days total** — one developer
working serially (implementation/review stay sequential; a "wave" is never
simultaneous runtime work). Those are two different clocks:

| Clock | Value |
|---|---|
| Required checkpoint | end of working day 2 from T0 |
| Serial effort estimate | ~4 developer-days for 1.1–1.4 |
| Evidence-complete date | 2026-10-05 (this run) — **after** the checkpoint |

**Complete gate evidence was absent at the day-2 checkpoint** — Tasks 1.1
and 1.2 carried evidence then; the 1.3 endpoint demo and this 1.4 fault
suite did not exist. Per A5, the two-working-day target is therefore
reported **unmet**. The later completion (all eight gates now carry spike
evidence) is recorded as a *four-day outcome*, **not** relabeled a day-2
pass.

## Disposition

**No-go for broad MVP work is retained.** Per A6, expansion is gated on
*two* conditions, only one of which is now satisfied:

1. **Every Stage-0 gate passes** — now true at the spike-subset level:
   GATE-S01–S08 all record `pass` with supported-interface evidence (see
   the ledger). ✅
2. **The timing/scope replan is explicitly resolved** — **open**. ❌

Until the replan below is reviewed and resolved by its owner, Sprint 2
(issues #6 onward) stays blocked.

## Reviewable replan — owner: Luong

| Axis | Finding | Required resolution before broad work |
|---|---|---|
| **Schedule** | Measured velocity: 4 serial developer-days produced the 4 spike tasks — the 2-day §8.1 window and any schedule arithmetic assuming parallelism are refuted by evidence | Rebase the solo 2–4-week MVP window on measured serial velocity; re-derive the sprint plan from the ledger, not from the original estimate |
| **Base** | `base-unprotected` (Task 1.3 named blocker): `money-mind:main` lacks required checks + strict up-to-date protection — the enforced-merge-precondition half of GATE-S08 was proven on the fixture, not the real base | Enable the required protection on the real base, **or** record a reconsidered base; no safety gate is waived to preserve the window |
| **Primitive — delivered-at-probe-layer** | Delivery dedup + one-active-attempt are proven in `FaultStore` (kit-owned rows per Q6) but are **not landed** in the controller intake path the demonstrated slice uses | Land the convergence contract in the real intake path, or name the upstream primitive that provides it — Task 2.x scope |
| **Primitive — carried blockers** | `telegram-adapter-live`, `kanban-live-write`, `vercel-linkage` (Task 1.3 named blockers, owners on file) | Each resolves on its own evidence leg before its dependent gate counts on it |
| **MET03 honesty** | Spike subset only: the remaining §8.2 fault rows and three repetitions per release row are tasks 3.6/3.7 (issues #20/#21) | Explicitly scheduled, not absorbed silently |

## What this record does *not* do

- It does not invent a second scheduler, transition engine or credential
  broker to close a gap — missing primitives are named.
- It does not waive any safety gate (fencing, one-use approval, enforced
  merge preconditions, atomic expected-head check) to keep the solo
  2–4-week window attractive — A6 forbids it.
- It does not close issues #20/#21 or claim the full fault matrix.

## Sign-off path

Broad MVP work begins when: (a) the schedule rebase above is recorded in
the sprint plan, (b) `base-unprotected` is resolved on the real repository
or a reconsidered base is recorded, and (c) the three carried primitive
blockers have evidence or named upstream owners — all recorded under
owner **Luong**. This disposition is reviewable: every input is the
committed evidence JSON plus this file's tables.
