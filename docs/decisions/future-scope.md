# Decision record: F14 deferred scope — production promotion, autonomous merge and parallel projects (issue #34 / task 5.3)

- **Status:** Recorded — **deferred** is the standing disposition for
  every F14 capability; **no date commitment** (§3.1 *Won't*, §8.4)
- **Owner:** Luong — adoption of any F14 capability is an explicit owner
  act recorded in that capability's own future PRD, never self-recorded
  and never implied by this record
- **Scope:** §3.1 F14; §8.4 GATE-E01–GATE-E04; task 5.3 documents
  policy/design gates only — no runtime implementation, no policy
  activation, no enabled configuration
- **Evidence:** `docs/future/f14-gates.md` — the per-capability
  checklists; prerequisites derived from the current F03/F04/F11/F12
  contract and the recorded gate ledgers (`admission-assessment`,
  `adapter-precondition`, `release-gate` archives)

## Recorded position

F14 is deferred as a class. Each of its five capabilities — autonomous
merge, an additional preview provider, external-contributor input
classes, production promotion and parallel projects — stays deferred
pending **a separate future PRD for that capability, the owner's
explicit adoption act in it, and independent evidence** satisfying the
checklists in `docs/future/f14-gates.md`. The deferred disposition is
the honest reading of the recorded evidence: the pilot outcome is
**narrow/consolidate pending owner confirmation**, repeat use failed
its proposed bound, measured burden already erases the per-issue
saving at one lane, the F13 admission is `admission-deferred` with the
task-5.2 precondition `unmet`, and the v1.1 release is
`release-withheld`. Nothing in that evidence base supports opening any
F14 capability.

## Standing prerequisites — unchanged by this record

These remain the contract every future proposal must preserve; a
weakened row records no-go (see the gate semantics in
`docs/future/f14-gates.md`):

| Prerequisite | Standing contract |
|---|---|
| Merge endpoint | the current **preview plus revision-bound human-approved merge** endpoint — durable one-use approval, expected-head merge, authoritative read-back |
| Concurrency | **one active lane** — `active_tasks: 1`, sequential implement/review |
| Verification | independent review + required checks on the current revision; revision-bound preview smoke |
| Authorization | deterministic, brokered, single-owner — every write fenced by repository/action/generation |
| Protections | enforced GitHub branch protections; scoped, non-privileged credentials |

## Capability register

| Capability | Gate | Checklist | Decision owner | Future PRD required |
|---|---|---|---|---|
| Autonomous merge | GATE-E01 | `f14-gates.md` §GATE-E01 | Luong | yes — adopted policy + preservation evidence |
| Additional preview provider | GATE-E02 | `f14-gates.md` §GATE-E02 | Luong | yes — per named provider |
| External-contributor input classes | GATE-E02 | `f14-gates.md` §GATE-E02 | Luong | yes — per input class |
| Production promotion | GATE-E03 | `f14-gates.md` §GATE-E03 | Luong | yes — incl. Q8 resolution and per-migration procedures |
| Parallel projects | GATE-E04 | `f14-gates.md` §GATE-E04 | Luong | yes — measured demand + isolation/owner analysis |

Each future PRD names its own decision owner (defaulting to Luong
unless it records another), its required evidence and fault scenarios,
and explicit go/no-go criteria per the register in
`docs/future/f14-gates.md`. **Design completion — this record and the
checklists existing — is distinguished from delivery and adoption**:
task 5.3 ends here; no F14 capability has begun.

## What this record does *not* do

- It does not implement, admit, schedule or date any F14 capability,
  and it does not weaken any gate — `release-withheld`, the
  `admission-deferred` F13 track and the `unmet` task-5.2 precondition
  stand unchanged.
- It does not create code, configuration, limits, credentials or
  feature flags — there is nothing to activate.
- It does not resolve Q8 (all four rows `Unresolved` — public release
  stays blocked), accept Q9 (owner verdict pending), or confirm the
  narrow/consolidate pilot disposition — each keeps its own owner and
  record.
- It does not turn a checklist into permission: even an all-`passed`
  re-judgement under a future PRD authorizes nothing by itself — the
  owner's explicit adoption act does.

## Re-entry conditions (what re-opening requires)

- [ ] Owner selects **exactly one** capability and drafts its own
      future PRD — one capability at a time (§8.4), never the bundle
- [ ] That PRD carries the capability's named decision owner, required
      evidence/fault scenarios and explicit go/no-go criteria from the
      `f14-gates.md` register
- [ ] Independent evidence satisfies every applicable checklist row —
      no `blocked`/`pending`/`unknown` row survives into adoption
- [ ] The standing prerequisites are demonstrated preserved, not
      asserted — a weakened row records no-go
- [ ] Owner records the explicit adoption act in that capability's own
      decision record — only then does its implementation scope open,
      under the same admission-precondition model task 5.2 already
      carries

## Decision record

- [ ] **Deferred confirmed** — owner confirms the standing F14
      deferral; the checklists stay the standing gate
- [ ] **Revisit one capability** — owner opens exactly one
      capability's future PRD per the re-entry conditions; the other
      four stay deferred
- [ ] **Retire** — owner removes a capability from future scope,
      recorded with reasons

**Operator decision:** _pending — F14 is recorded deferred on
2026-10-05; confirmation, a single-capability revisit or retirement is
the owner's act._

**Recorder:** issue-resolver run for #34 — checklists enumerated
fail-closed; no gate weakened, no capability admitted, no code or
enabled configuration introduced.
