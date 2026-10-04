# Development Tasks — factory-kit

Source PRD: [prd.md](prd.md), v1.0 (2026-10-04). Generated: 2026-10-04. Owner: Luong; solo implementation. This is a development plan: unchecked criteria describe future required evidence, not software tests already passed.

**33 tasks · 5 sprints · 57 developer-days total · 39 developer-days through internal MVP.** Dependency-only critical path: **45 days** for all admitted scope; **32 days** through MVP. All estimates are active development effort, excluding model/CI/network/human waits, recruitment and pilot observation.

## Sprint overview and release scope

| Sprint | Phase | Focus / exit gate | Tasks | Effort |
|---|---|---|---:|---:|
| 1 | POC | Supported full endpoint spike; all §8.1 gates and explicit timing replan | 4 | 4d |
| 2 | MVP Foundation | Validated setup, authorized intake, one execution lane, review, control and recovery | 9 | 16d |
| 3 | MVP Completion | Preview, approval, guarded merge, removal, fault evidence and internal v1.0 acceptance | 10 | 19d |
| 4 | Full Features | F09/F10, measured internal/external pilot and continue/narrow/stop decision | 7 | 13d |
| 5 | Full Features | Conditional one-adapter admission; deferred F14 design gates only | 3 | 5d |

Sprints group scope and do not promise calendar dates. Sprint 1 is a bounded POC demonstration using supported primitives, not production feature completion. Sprints 2–3 deliver the ten Must features F01–F08 and F11–F12: one existing repository/host/runtime/model/provider/merge method, one active task, independent sequential review, current checks, meaningful preview, durable revision-bound human approval and authoritative merge read-back. Verified is an intermediate observation; the selected successful endpoint ends at merged.

Sprint 4 is post-MVP Full Features: F09/F10 and onboarding pilot work require accepted internal v1.0. Should features may be explicitly deferred without weakening Must gates. Sprint 5 is conditional post-pilot scope: 5.1 may reject admission; 5.2 runs only after explicit adoption of exactly one candidate and a positive continue decision. Task 5.3 documents future policy gates; F14 implementation, production deployment, package publication as part of automated issue delivery, autonomous merge and parallel project execution remain deferred to separate future scope/adoption. Q8 public distribution decisions apply before any public kit release.

### Capacity and timing decisions

- The PRD requires the complete spike by working day 2 from unscheduled T0. The four serial 1d POC tasks require 4 solo developer-days. Task 1.2 records the day-2 stop/replan checkpoint; Task 1.4 must retain an unmet day-2 result if complete proof arrived later. Broad work stays gated until every spike criterion passes and the owner resolves the timing/base/primitive replan.
- The PRD proposes a 2–4-week solo MVP and correctness evidence by week 4. This plan requires 39 active solo developer-days through internal acceptance; even the dependency-only MVP path exceeds a normal 20-working-day window. Owner must replan capacity/timing before execution. Keep the selected preview/approval/merge endpoint and all Must/fault gates; these estimates cannot silently narrow it.
- Pilot task effort excludes the 30-day internal window, 60/90-day external targets, four weekly repeat-use observations and participant recruitment. Those elapsed dependencies may delay gates independently of development effort. No start date or release date is assumed.

## Dependencies and execution guidance

The canonical dependency helper validated 33 tasks and 69 directed edges with no cycles. `Depends On` is authoritative; `Blocks` below is its full inverse, including cross-sprint edges. Wave is deterministically `1 + max(prerequisite wave)` (roots are wave 1).

| Task | Depends On | Blocks | Wave | Effort |
|---|---|---|---:|---:|
| [1.1](#task-1-1) | None | [1.2](#task-1-2) | 1 | 1d |
| [1.2](#task-1-2) | [1.1](#task-1-1) | [1.3](#task-1-3) | 2 | 1d |
| [1.3](#task-1-3) | [1.2](#task-1-2) | [1.4](#task-1-4) | 3 | 1d |
| [1.4](#task-1-4) | [1.3](#task-1-3) | [2.1](#task-2-1) | 4 | 1d |
| [2.1](#task-2-1) | [1.4](#task-1-4) | [2.2](#task-2-2), [2.3](#task-2-3) | 5 | 1d |
| [2.2](#task-2-2) | [2.1](#task-2-1) | [2.3](#task-2-3), [2.4](#task-2-4), [3.5](#task-3-5) | 6 | 2d |
| [2.3](#task-2-3) | [2.1](#task-2-1), [2.2](#task-2-2) | [2.4](#task-2-4) | 7 | 2d |
| [2.4](#task-2-4) | [2.2](#task-2-2), [2.3](#task-2-3) | [2.5](#task-2-5), [2.7](#task-2-7) | 8 | 2d |
| [2.5](#task-2-5) | [2.4](#task-2-4) | [2.6](#task-2-6), [2.8](#task-2-8), [3.3](#task-3-3) | 9 | 2d |
| [2.6](#task-2-6) | [2.5](#task-2-5) | [2.8](#task-2-8), [2.9](#task-2-9), [3.1](#task-3-1) | 10 | 2d |
| [2.7](#task-2-7) | [2.4](#task-2-4) | [2.8](#task-2-8), [2.9](#task-2-9), [3.2](#task-3-2) | 9 | 2d |
| [2.8](#task-2-8) | [2.5](#task-2-5), [2.6](#task-2-6), [2.7](#task-2-7) | [2.9](#task-2-9), [3.1](#task-3-1), [3.3](#task-3-3), [3.5](#task-3-5), [3.6](#task-3-6) | 11 | 2d |
| [2.9](#task-2-9) | [2.6](#task-2-6), [2.7](#task-2-7), [2.8](#task-2-8) | [3.1](#task-3-1), [3.4](#task-3-4), [3.6](#task-3-6) | 12 | 1d |
| [3.1](#task-3-1) | [2.6](#task-2-6), [2.8](#task-2-8), [2.9](#task-2-9) | [3.2](#task-3-2), [3.5](#task-3-5) | 13 | 2d |
| [3.2](#task-3-2) | [2.7](#task-2-7), [3.1](#task-3-1) | [3.3](#task-3-3) | 14 | 2d |
| [3.3](#task-3-3) | [2.5](#task-2-5), [2.8](#task-2-8), [3.2](#task-3-2) | [3.4](#task-3-4), [3.7](#task-3-7) | 15 | 2d |
| [3.4](#task-3-4) | [2.9](#task-2-9), [3.3](#task-3-3) | [3.6](#task-3-6), [3.7](#task-3-7), [3.8](#task-3-8) | 16 | 2d |
| [3.5](#task-3-5) | [2.2](#task-2-2), [2.8](#task-2-8), [3.1](#task-3-1) | [3.6](#task-3-6), [3.7](#task-3-7), [3.9](#task-3-9) | 14 | 2d |
| [3.6](#task-3-6) | [2.8](#task-2-8), [2.9](#task-2-9), [3.4](#task-3-4), [3.5](#task-3-5) | [3.8](#task-3-8), [3.10](#task-3-10) | 17 | 3d |
| [3.7](#task-3-7) | [3.3](#task-3-3), [3.4](#task-3-4), [3.5](#task-3-5) | [3.8](#task-3-8), [3.10](#task-3-10) | 17 | 3d |
| [3.8](#task-3-8) | [3.4](#task-3-4), [3.6](#task-3-6), [3.7](#task-3-7) | [3.9](#task-3-9), [3.10](#task-3-10) | 18 | 1d |
| [3.9](#task-3-9) | [3.5](#task-3-5), [3.8](#task-3-8) | [3.10](#task-3-10) | 19 | 1d |
| [3.10](#task-3-10) | [3.6](#task-3-6), [3.7](#task-3-7), [3.8](#task-3-8), [3.9](#task-3-9) | [4.1](#task-4-1), [4.2](#task-4-2), [4.3](#task-4-3), [4.4](#task-4-4) | 20 | 1d |
| [4.1](#task-4-1) | [3.10](#task-3-10) | [4.3](#task-4-3), [4.7](#task-4-7) | 21 | 2d |
| [4.2](#task-4-2) | [3.10](#task-3-10) | [4.3](#task-4-3), [4.4](#task-4-4), [4.5](#task-4-5), [4.7](#task-4-7) | 21 | 2d |
| [4.3](#task-4-3) | [3.10](#task-3-10), [4.1](#task-4-1), [4.2](#task-4-2) | [4.5](#task-4-5), [4.6](#task-4-6) | 22 | 3d |
| [4.4](#task-4-4) | [3.10](#task-3-10), [4.2](#task-4-2) | [4.5](#task-4-5), [4.7](#task-4-7) | 22 | 2d |
| [4.5](#task-4-5) | [4.2](#task-4-2), [4.3](#task-4-3), [4.4](#task-4-4) | [4.6](#task-4-6), [4.7](#task-4-7) | 23 | 2d |
| [4.6](#task-4-6) | [4.3](#task-4-3), [4.5](#task-4-5) | [4.7](#task-4-7), [5.1](#task-5-1), [5.3](#task-5-3) | 24 | 1d |
| [4.7](#task-4-7) | [4.1](#task-4-1), [4.2](#task-4-2), [4.4](#task-4-4), [4.5](#task-4-5), [4.6](#task-4-6) | [5.1](#task-5-1), [5.3](#task-5-3) | 25 | 1d |
| [5.1](#task-5-1) | [4.6](#task-4-6), [4.7](#task-4-7) | [5.2](#task-5-2) | 26 | 1d |
| [5.2](#task-5-2) | [5.1](#task-5-1) | None | 27 | 3d |
| [5.3](#task-5-3) | [4.6](#task-4-6), [4.7](#task-4-7) | None | 26 | 1d |

### Execution waves

Waves show prerequisite readiness for developer planning. A solo developer executes ready tasks serially. Multiple developers could work on independent code/docs/fixtures within a wave after locking shared contracts; the product runtime still permits one active task and sequential implementation/review. Wave membership never supplies runtime concurrency, authorization, a passed release gate or an adopted optional scope.

| Wave | Ready task group after prerequisites |
|---:|---|
| 1 | [1.1](#task-1-1) |
| 2 | [1.2](#task-1-2) |
| 3 | [1.3](#task-1-3) |
| 4 | [1.4](#task-1-4) |
| 5 | [2.1](#task-2-1) |
| 6 | [2.2](#task-2-2) |
| 7 | [2.3](#task-2-3) |
| 8 | [2.4](#task-2-4) |
| 9 | [2.5](#task-2-5), [2.7](#task-2-7) |
| 10 | [2.6](#task-2-6) |
| 11 | [2.8](#task-2-8) |
| 12 | [2.9](#task-2-9) |
| 13 | [3.1](#task-3-1) |
| 14 | [3.2](#task-3-2), [3.5](#task-3-5) |
| 15 | [3.3](#task-3-3) |
| 16 | [3.4](#task-3-4) |
| 17 | [3.6](#task-3-6), [3.7](#task-3-7) |
| 18 | [3.8](#task-3-8) |
| 19 | [3.9](#task-3-9) |
| 20 | [3.10](#task-3-10) |
| 21 | [4.1](#task-4-1), [4.2](#task-4-2) |
| 22 | [4.3](#task-4-3), [4.4](#task-4-4) |
| 23 | [4.5](#task-4-5) |
| 24 | [4.6](#task-4-6) |
| 25 | [4.7](#task-4-7) |
| 26 | [5.1](#task-5-1), [5.3](#task-5-3) |
| 27 | [5.2](#task-5-2) |

The first parallel planning opportunity is Telegram control 2.7 alongside publication/review work 2.5–2.6 once 2.4 is complete. After internal acceptance, steering 4.1, upgrade/repair 4.2 and pilot admission preparation can be divided among developers as their exact prerequisites permit. Fault suites 3.6 and 3.7 may use independent fixtures when each is ready; integration on the selected runtime remains serialized and evidence must retain fixture identity. After the measured pilot, future design 5.3 is independent of admitted adapter implementation 5.2.

### Critical paths and bottlenecks

**Internal MVP critical path: 32 weighted developer-days.** [1.1](#task-1-1) → [1.2](#task-1-2) → [1.3](#task-1-3) → [1.4](#task-1-4) → [2.1](#task-2-1) → [2.2](#task-2-2) → [2.3](#task-2-3) → [2.4](#task-2-4) → [2.5](#task-2-5) → [2.6](#task-2-6) → [2.8](#task-2-8) → [2.9](#task-2-9) → [3.1](#task-3-1) → [3.2](#task-3-2) → [3.3](#task-3-3) → [3.4](#task-3-4) → [3.6](#task-3-6) → [3.8](#task-3-8) → [3.9](#task-3-9) → [3.10](#task-3-10).

**Full conditional plan critical path: 45 weighted developer-days.** [1.1](#task-1-1) → [1.2](#task-1-2) → [1.3](#task-1-3) → [1.4](#task-1-4) → [2.1](#task-2-1) → [2.2](#task-2-2) → [2.3](#task-2-3) → [2.4](#task-2-4) → [2.5](#task-2-5) → [2.6](#task-2-6) → [2.8](#task-2-8) → [2.9](#task-2-9) → [3.1](#task-3-1) → [3.2](#task-3-2) → [3.3](#task-3-3) → [3.4](#task-3-4) → [3.6](#task-3-6) → [3.8](#task-3-8) → [3.9](#task-3-9) → [3.10](#task-3-10) → [4.1](#task-4-1) → [4.3](#task-4-3) → [4.5](#task-4-5) → [4.6](#task-4-6) → [4.7](#task-4-7) → [5.1](#task-5-1) → [5.2](#task-5-2).

These are the deterministic helper’s longest weighted dependency paths, not solo elapsed schedules; resource contention and external waits can lengthen delivery. Full-plan duration assumes the conditional adapter is actually admitted. A critical-path delay can delay its gate if no schedule slack or scoped replan absorbs it.

- Task [2.8](#task-2-8), Implement restart and GitHub reconciliation through Hermes ownership: **5 direct dependents**, as reported by the helper. Mitigation: stabilize its contract/evidence early, avoid changes that invalidate downstream fixtures, and review readiness before committing downstream implementation effort.

### Cross-sprint and external gates

- 1.4 → 2.1: every Stage 0 supported-interface gate and explicit schedule replan precede broad MVP implementation.
- Sprint 2 records and deterministic authority feed preview 3.1, approval 3.2, merge 3.3 and safe removal 3.5. Intake 2.3 explicitly depends on ready setup 2.2; configuration alone cannot authorize it.
- Internal acceptance 3.10 → 4.1/4.2/4.3/4.4: internal release evidence is mandatory before pilot scope. Task 4.4 blocks external participant collection and unsupported workload admission.
- Measured pilot decision 4.6 and evidence package 4.7 → 5.1/5.3: these resolve Sprint 5 workers’ declared pilot prerequisite. An explicit continue plus adopted candidate is an additional behavioral gate for 5.2; completing predecessor tasks with a narrow/stop outcome does not authorize adapter execution.
- External prerequisites: scoped GitHub/Telegram/model/provider accounts and permissions, enforceable supported Hermes hooks, compatible up-to-date-base/check protections, meaningful preview project, owner decisions, specialist/consent input where applicable, recruited consented participants and observed repeat-use periods. Missing prerequisites block their task; task dependencies do not fabricate those resources.

## Flagged ambiguities and assumptions

Q1 is resolved: preview + revision-bound human approval + merge is the selected endpoint. Q2 is resolved for internal scope: implement privacy controls with no specific compliance commitment; reassessment before external participant data remains mandatory. Source IDs below preserve PRD §9.1 numbering, including Q10 appearing before Q7 in the PRD.

| PRD decision | Open question | Owner / due | Governing tasks |
|---|---|---|---|
| Q3 | Which repository, host/version and native Hermes worker/model constitute the initial tested support recipe? | Luong; Start of spike | [1.1](#task-1-1) |
| Q4 | Can supported Hermes interfaces durably associate intake, fence writes and publish evidence without a second scheduler? | Implementer; End of working day 2; failure blocks expansion | [1.1](#task-1-1), [1.2](#task-1-2), [1.4](#task-1-4) |
| Q5 | Scoped existing GitHub credentials or GitHub App; how are all worker/test writes mediated and fenced? | Implementer/operator; Spike security gate, before unattended execution | [1.1](#task-1-1), [1.2](#task-1-2), [2.5](#task-2-5) |
| Q6 | Final package language, storage extension point, manifest schema and command syntax? | Implementer; TAD after spike, before implementing public interfaces | [1.1](#task-1-1), [2.1](#task-2-1) |
| Q10 | Which preview provider, project type, artifact identity, smoke contract, cleanup mechanism and merge method meet F11/F12? | Luong/implementer; Select at spike start and demonstrate by day 2; no production credentials | [1.1](#task-1-1), [1.3](#task-1-3), [3.1](#task-3-1), [3.3](#task-3-3) |
| Q7 | What subscription automation terms, quota visibility and practical limits apply to the selected runtime/model? | Operator; Readiness; no assumption that existing subscriptions cover every usage | [1.1](#task-1-1), [2.2](#task-2-2), [2.4](#task-2-4) |
| Q8 | OSS license, distribution channel, support commitment and dependency notice process? | Luong; Before public release | [3.9](#task-3-9), [4.7](#task-4-7), [5.3](#task-5-3) |
| Q9 | Accept the proposed numerical targets, retention defaults and retry/time budgets, or adjust from measured baseline? | Luong; Before MVP acceptance and pilot recruitment | [3.8](#task-3-8), [3.10](#task-3-10), [4.3](#task-4-3), [4.6](#task-4-6) |
| Planning tension | 4d serial POC vs day-2 go/no-go; 39d solo MVP vs 2–4 weeks | Luong before kickoff / at day-2 checkpoint | 1.2, 1.4, 3.10 |

No language, storage API/schema, installation syntax, Telegram syntax, credential mechanism, host/version, provider or merge method is selected by this plan. Resolve them from the actual supported spike/TAD contract. Auxiliary ledgers/outboxes reference Hermes task IDs and never dispatch independently. Worktrees separate files; they do not prove sandboxing. The internal workload is explicitly selected trusted-maintainer issues on trusted project code; external contributor workloads need enforceable process/filesystem/network boundaries before admission.

All numerical values are PRD proposed targets/defaults subject to Q9 acceptance or recorded measured adjustment: one active task; initial plus one fix implementation; 60 cumulative active-worker minutes across roles excluding CI wait; 24h task wall time; one preview per task with 24h maximum lifetime; reachable-provider cleanup within 60m after confirmed termination; 60m approval expiry; smoke age ≤10m at merge; 7d logs / 30d post-completion detailed audit; minimal replay-resistant identity until explicit registration removal. Schedule pressure never waives authorization, identity, evidence or safety gates.

## Sprint 1: Supported endpoint spike and day-2 go/no-go

**Phase**: POC

**Tasks**: 4

**Total effort**: 4 developer-days; no calendar duration promised.

**Workstreams**: supported recipe (1.1); authority spike (1.2); endpoint spike (1.3); fault gate (1.4).

**Exit**: every §8.1 gate, complete endpoint/fault evidence and explicit timing replan; retain the day-2 checkpoint outcome separately.

<a id="task-1-1"></a>

### Task 1.1: Select the tested recipe and map supported Hermes boundaries

**Description**: Select one real trusted-maintainer repository with a meaningful preview, one host/runtime/model and one supported Hermes extension path. Map required lifecycle, authority, evidence and recovery information to existing supported operations so the spike can reject an infeasible thin kit before building product features.

**Acceptance Criteria**:

- [ ] **A1.** An executable readiness probe records repository ID, host and Hermes/IDD/runtime/model/skill versions, actual authenticated model availability, supported start/liveness/result/cancel operations, credential scope and transport; an unavailable model, unsupported version or conflicting task/merge owner yields a named blocker and dispatches nothing.
- [ ] **A2.** A boundary map names supported documented interfaces and evidence references for every §6.1 owner, required §6.4 record and state, durable intake-to-Hermes association, monotonic claim/fence transition, independent reviewer session, publication boundary, Telegram typed action and durable approval consumption; missing primitives are recorded as no-go, without inventing a second scheduler or transition engine.
- [ ] **A3.** Record selections for Q3/Q5/Q6/Q7/Q10: tested repository/host/runtime, scoped credential mechanism, language/package/storage extension and metadata ownership, model terms/quota visibility, preview provider and GitHub-supported merge method. Unresolved selections include an owner and explicitly block dependent demonstrations.
- [ ] **A4.** The reviewed spike recipe includes a nonempty verification contract preserving target-project CI, one non-production preview with agreed visibility/synthetic data/smoke command, and GitHub rules that enforce checks on an up-to-date base with non-bypass credentials; a missing meaningful preview or incompatible rules blocks the full endpoint.
- [ ] **A5.** The decision record preserves the confirmed preview plus human-approved merge endpoint and concrete internal privacy scope, marks provisional install syntax unsupported until tested, and separates role/runtime/model/skills from optional live steering or additional adapters.

**Effort**: 1 day

**Dependencies**: None

**Blocks**: Task 1.2

**Workstream**: supported recipe

**PRD Reference**: §3.2 F01; §5.3; §6.1–§6.2; §6.4–§6.5; §8.1; §9.1 Q3–Q7/Q10

**Feature coverage**: F01, F02, F03, F04, F05, F06, F11, F12. **Requirement coverage**: F01, NFR-C01, ARCH-O01, ARCH-O02, ARCH-O03, ARCH-O04, ARCH-O05, ARCH-O06, ARCH-O07, ARCH-O08, SPEC01, SPEC02, SPEC03, SPEC04, SPEC05, SPEC06, INTEG01, INTEG02, STATE01, STATE02, GATE-S01, GATE-S02, GATE-S07.

**Verification**: Run readiness against the selected recipe and one deliberately unavailable prerequisite; inspect source/documentation references and executable boundary probes. Record versions and fixture identities.

**Implementation notes**: Bounded 1-day discovery/probe of supported primitives, not package or durable lifecycle implementation. The developer host is only a candidate until tested. Auxiliary metadata references Hermes task IDs and never independently dispatches. Unsupported core changes trigger no-go; a successful prompt is insufficient.

<a id="task-1-2"></a>

### Task 1.2: Prove fenced publication and cancellation authority

**Description**: Exercise the selected runtime and deterministic privileged-effect boundary on a disposable trusted-code fixture. Prove current repository/generation/authorization checks for publication and cancellation before any unattended end-to-end run; demonstrate the existing supported primitive rather than implement a production broker.

**Acceptance Criteria**:

- [ ] **A1.** A coding worker and its test/descendant process receive no production secrets, controller tokens, preview credentials or unrestricted GitHub write credentials; direct branch/PR/API mutation attempts fail, while an allowed current-generation publication succeeds only through the scoped deterministic boundary and records operation/repository/generation identity.
- [ ] **A2.** Wrong-repository, revoked-actor and superseded-generation requests are rejected before each permitted write, including branch/PR publication, preview deployment and merge invocation; issue/prompt text cannot change policy. Rejected attempts retain redacted audit evidence and do not mutate remote state.
- [ ] **A3.** After an authorized typed Telegram cancel is durably accepted, the fence commits before termination starts; a delayed worker/descendant publication request and candidate result are rejected. Worker/descendant exit is confirmed or quarantine is surfaced, and uncertain termination cannot permit replacement execution.
- [ ] **A4.** Secret canaries remain absent from local exports/logs and Telegram notifications, secrets live outside the manifest with scoped access, and selected GitHub/Telegram/provider transport is verified. Worktree separation and branch protection alone are not accepted as the effect boundary.
- [ ] **A5.** At the end of working day 2 from T0, record an interim stop/replan decision: broad feature work remains blocked unless all §8.1 gates, including Tasks 1.3–1.4, already have executable evidence. Missing endpoint evidence or unsupported permission/durability hooks records no-go and a supported-primitive/base or schedule replan; do not silently extend the two-day target.

**Effort**: 1 day

**Dependencies**: Task 1.1

**Blocks**: Task 1.3

**Workstream**: authority spike

**PRD Reference**: §3.2 F03/F05; §5.2; §6.5; §8.1; §9.1 Q4/Q5

**Feature coverage**: F03, F05. **Requirement coverage**: F03, F05, NFR-S01, NFR-S02, NFR-S03, NFR-S04, GATE-S06.

**Verification**: Use scoped disposable repository/provider resources and canary credentials. Observe requests and authoritative remote read-back for allowed/denied writes, persist cancel ordering, and test a delayed child process.

**Implementation notes**: Use only the supported primitive identified in Task 1.1; no bespoke security control plane. Any unmediated mutation route fails the spike. Reconcile effects already accepted before the fence and report them accurately. Default control/termination targets are asserted fully in later MVP tasks; this spike proves authority ordering. Four sequential 1d tasks cannot satisfy a two-working-day full-spike target for a solo developer; the day-2 checkpoint stops expansion rather than claiming success.

<a id="task-1-3"></a>

### Task 1.3: Demonstrate the complete opted-in issue to approved merge endpoint

**Description**: Demonstrate one actual opted-in trusted issue from durable intake through sequential implementation and independent review, current-head CI, revision-bound preview, durable Telegram approval and guarded merge. Use supported primitives from the first two probes and retain executable evidence for every full endpoint gate.

**Acceptance Criteria**:

- [ ] **A1.** A signed registered-repository event authorized by the maintainer creates durable logical work/Hermes identity before acceptance; an invalid signature, wrong repository or unauthorized opt-in creates no execution authority. One implementation and a separate independent reviewer session execute sequentially and record their own task/attempt identity, review verdict and exact PR SHA.
- [ ] **A2.** Positive independent review, nonempty required checks and GitHub read-back agree on an open PR head/base and observation time; a moved head or failed/missing required check blocks verified status. Implementation self-report cannot substitute for review or authoritative checks.
- [ ] **A3.** The selected provider returns a deployment ID/URL with externally verifiable immutable source/artifact identity matching the reviewed head/base; configured meaningful smoke checks record result/time, and owned-preview cleanup is demonstrated after termination. Wrong revision, unknown identity, failed smoke, expiry or provider outage blocks approval-ready/merge and cannot reuse an older preview.
- [ ] **A4.** Telegram presents repository/PR, exact head/base, preview/smoke evidence, chosen merge method and expiry. A durable one-use request binds actor/target/action/revision/evidence/policy digests; after controller/chat restart awaiting-approval and the authorized decision remain attributable, and repeated or concurrent approve commands consume at most one request and create at most one merge intent.
- [ ] **A5.** The single merge owner revalidates authorization, task/fence state, open non-draft PR, head/base, mergeability, required checks/protections and fresh healthy preview before conditional expected-head merge. A head/base/policy change, stale preview, revoked actor, paused/canceled task or expired/rejected/replayed approval denies merge and invalidates affected authority; resume does not revive it.
- [ ] **A6.** A deliberate base movement or required-check change between observation and merge is rejected by enforced GitHub up-to-date-base/check rules (or proven equivalent atomic queue), with credentials unable to bypass. The reviewer/preview cover the candidate compatible with that base. Read-then-write and expected-head checking alone do not pass; conflicting merge automation and GitHub auto-merge shortcuts remain disabled.
- [ ] **A7.** The happy path reaches merged only after authoritative GitHub read-back captures the actual merge commit SHA, request/intent identity and actor. A merge accepted remotely with its response lost is reconciled before any retry; unknown state parks rather than reporting success or blindly sending another merge. Default approval expiry is 60 minutes and merge smoke age at most 10 minutes.
- [ ] **A8.** Every result links repository/task/attempt/PR/deployment/approval/merge identities and fixture/version evidence. Report elapsed time and outcome against MET01 honestly; this one-run primitive demonstration does not declare production F02/F04/F11/F12 complete.

**Effort**: 1 day

**Dependencies**: Task 1.2

**Blocks**: Task 1.4

**Workstream**: endpoint spike

**PRD Reference**: §1.4 MET01; §3.2 F02/F04/F11/F12; §4.2; §6.4; §8.1

**Feature coverage**: F02, F04, F11, F12. **Requirement coverage**: F02, F04, F11, F12, STATE03, GATE-S03, GATE-S04, GATE-S08, MET01.

**Verification**: Execute the full selected recipe once on a bounded issue using real GitHub/provider identity read-back and real Telegram actor validation; use disposable candidate revisions or fault fixtures for denied transitions and retain traces. Reuse existing supported controls rather than writing production features.

**Implementation notes**: Bounded 1-day assembled demo assumes Tasks 1.1–1.2 establish all supported primitives and ready fixture resources. If a required primitive or atomic base/check protection cannot be exercised, fail/no-go and replan rather than expanding this task into production implementation. Cleanup proves owned-resource identification and provider read-back; provider outage leaves visible backlog, never false removal. Completion timing does not rewrite the two-day proposed target.

<a id="task-1-4"></a>

### Task 1.4: Inject spike faults and record the day-2 go/no-go

**Description**: Inject the Stage 0 duplicate/restart/cancel and approval/merge uncertainty faults into the demonstrated thin slice. Publish a gate-by-gate supported-interface evidence record and explicit stop/replan disposition, including the contradiction between the PRD day-2 target and four serial developer-day estimates.

**Acceptance Criteria**:

- [ ] **A1.** Repeated deliveries including webhook/reconciliation overlap and a host restart converge on one logical task, at most one active attempt and one PR. Compare acknowledged identities with recovered durable records; a crash around remote PR creation discovers the existing PR or parks ambiguous state without blind republication.
- [ ] **A2.** Cancel-then-late-completion and expired-claim/dead-worker fixtures reject old-generation results/effects before replacement authority; uncertain descendants or remote outcomes yield quarantine/parked state, never overlapping execution authority or a false completion claim.
- [ ] **A3.** Approval survives controller/chat restart with its original actor, revision/evidence/policy, expiry and consumed state. Replayed/concurrent approval, changed head/base/check/policy, expired request or cancellation cannot schedule a second merge; an accepted merge followed by lost response is reconciled to its actual GitHub SHA or parked if unreadable.
- [ ] **A4.** A gate ledger lists GATE-S01–GATE-S08 separately with actual pass/fail, supported-interface references, versions, fixture identity and reproducible traces; the spike subset records zero duplicate PRs, unauthorized merges or accepted fenced results. The remaining §8.2 fault suite and three repetitions per release row remain explicit MVP work, so MET03 is not claimed complete from this subset.
- [ ] **A5.** The decision record distinguishes the required end-of-working-day-2 checkpoint from the 4d serial effort estimate for Tasks 1.1–1.4. If complete evidence was absent at that checkpoint, report the two-day target unmet, retain no-go for broad work, and record a reviewable schedule/base/primitive replan with owner; do not label a later four-day result a day-2 pass.
- [ ] **A6.** Broad MVP work starts only after every spike gate passes and the timing/scope replan is explicitly resolved. Missing supported durability/publication/approval or atomic merge protection names the missing upstream primitive or reconsidered base and blocks expansion; no safety gate is waived to preserve the solo 2–4-week window.

**Effort**: 1 day

**Dependencies**: Task 1.3

**Blocks**: Task 2.1

**Workstream**: fault gate

**PRD Reference**: §1.4 MET03; §3.2 F06; §4.3; §6.4; §8.1; §8.2; §9.1 Q4; §9.3

**Feature coverage**: F06. **Requirement coverage**: F06, STATE04, GATE-S05, MET03.

**Verification**: Run deterministic fault fixtures over the actual selected primitive path, asserting durable identities and authoritative read-back. Correlate traces by task/attempt/operation and report timing separately from model, network and human waits.

**Implementation notes**: Bounded 1-day fault probe and decision write-up, not implementation of a full recovery engine or full release matrix. Only supported primitives may pass. Do not equate a wave with simultaneous runtime work: one developer works serially and implementation/review remain sequential. A later successful spike can unlock expansion only with the recorded replan; unmet day-2 timing stays visible.

## Sprint 2: Durable identity, bounded execution and operational control

**Phase**: MVP Foundation

**Tasks**: 9

**Total effort**: 16 developer-days; no calendar duration promised.

**Workstreams**: configuration (2.1); setup (2.2); intake (2.3); execution (2.4); publication (2.5); verification (2.6); control (2.7); recovery (2.8); observability (2.9).


<a id="task-2-1"></a>

### Task 2.1: Finalize validated configuration and supported package contracts

**Description**: Finalize the spike-proven package/configuration and logical registration contract before public interfaces or broad implementation. Attach a validated immutable effective configuration digest to every attempt while preserving existing IDD configuration ownership (§6.3–6.4).

**Acceptance Criteria**:

- [ ] **A1.** An executable schema fixture validates immutable repository ID/display name, one registration owner, opt-in actors/roles and numeric Telegram actor/chat allowlists with deny-by-default behavior; renaming the display name cannot change repository authority.
- [ ] **A2.** Configuration tests require the selected supported runtime, available model per implementation/review role, tool/capability policy, approved skill IDs and immutable revisions; missing roles, unpinned/newly discovered skills, incompatible versions or unknown fields fail with actionable errors.
- [ ] **A3.** Validation requires a nonempty operator-approved acceptance command/check-provider/context/conclusion contract; empty contracts are rejected even with no protected checks. Defaults are one active task, two implementation attempts, 60 active-worker minutes and 24 wall hours, with bounded configurable values.
- [ ] **A4.** Endpoint policy records one selected preview provider and merge method, 60-minute approval expiry and 10-minute maximum merge smoke age; production deployment/package publication/autonomous merge are disabled and cannot be enabled by worker input.
- [ ] **A5.** Configuration contains observation freshness, event/log retention and export/redaction settings, 7-day worker logs, 30-day detailed audit retention and identity tombstones until explicit registration removal; literal secret canaries fail validation and only approved secret-storage references are accepted.
- [ ] **A6.** A migration/precedence/conflict fixture maps factory settings to existing .gitissue.yml without overwriting it; conflicting values fail and explain ownership. Package language/storage extension/schema/commands/filename are documented from spike evidence rather than treating .factory-kit.yml as already shipped.
- [ ] **A7.** Registration persists repository/configuration digest, owner, supported version set, authorization policy and readiness outcome; changed policy parks affected active work or requires an explicitly authorized fresh generation, and tests show it never broadens an existing generation.

**Effort**: 1 day

**Dependencies**: Task 1.4

**Blocks**: Task 2.2, Task 2.3

**Workstream**: configuration

**PRD Reference**: §6.3 CFG01–CFG09; §6.4 Registration; §8.1 go/no-go

**Feature coverage**: F01, F03, F04, F11, F12. **Requirement coverage**: CFG01, CFG02, CFG03, CFG04, CFG05, CFG06, CFG07, CFG08, CFG09, CFG-C01, CFG-C02, REC01.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: Entry gate: recorded all-pass POC decision must precede this task. Use supported storage/extension choices proved in Sprint 1; do not introduce another scheduler. Numerical defaults remain proposed under Q9 and are validated as configuration rather than claimed measured results.

<a id="task-2-2"></a>

### Task 2.2: Implement reviewed additive setup and substantive readiness

**Description**: Implement additive installation and substantive readiness against the finalized support recipe so registration cannot enable execution from executable presence alone. Preserve developer work and require an explicit reviewable accepted plan for integration or remote changes (F01).

**Acceptance Criteria**:

- [ ] **A1.** On a repository fixture containing existing CI, .gitissue.yml and dirty tracked/untracked developer files, inspection produces a reviewable inventory/diff of factory-owned additions and explicit integration edits; before operator plan acceptance, no local or remote mutation occurs.
- [ ] **A2.** Applying an explicitly accepted plan preserves all unrelated bytes and developer edits without automatic stash/reset, records ownership/checksums and creates one registration/webhook/configuration/dependency entry; a second run returns an empty effective diff and no duplicates.
- [ ] **A3.** Readiness runs executable capability, actual configured model/authentication, supported host/Hermes/runtime/skill-version and cancellation probes; missing executable, unavailable model, failed auth, unsupported version or inadequate cancellation capability is a named blocker and dispatch remains disabled.
- [ ] **A4.** A competing automation-owner fixture blocks activation; existing CI/IDD conventions are mapped by reviewed integration changes and no independent backlog scheduler or implicit auto-merge loop is installed.
- [ ] **A5.** The readiness report records permissions/scopes, selected recipe versions, pinned skill revisions, concrete verification commands and cancellation capability. Tokens/secret canaries are absent from committed additions, report and plan.
- [ ] **A6.** setup_checked events persist local project ID, version set, duration and per-prerequisite outcomes; registration is enabled only with the accepted plan and passing substantive readiness, and failed setup remains diagnosable without presenting queued work as authorized.

**Effort**: 2 days

**Dependencies**: Task 2.1

**Blocks**: Task 2.3, Task 2.4, Task 3.5

**Workstream**: setup

**PRD Reference**: §3.2 F01; §6.1 integration ownership; §7.1 setup_checked

**Feature coverage**: F01. **Requirement coverage**: F01, EVT01.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: Use fake scoped integrations for adverse tests and the selected recipe for substantive probes. Do not depend on production intake to create the setup registration: this task supplies the prerequisite registration needed by 2.3.

<a id="task-2-3"></a>

### Task 2.3: Implement authorized durable webhook and reconciliation intake

**Description**: Accept authenticated opted-in issues once through durable Hermes work association, using the ready registered identity supplied by setup. Keep delivery deduplication distinct from repository/issue/execution-generation identity so webhook, polling and restart agree (F02).

**Acceptance Criteria**:

- [ ] **A1.** A valid signed registered-repository event with a currently authorized opt-in actor creates durable logical repository/issue/explicit-generation identity, delivery reference, source context fingerprint, authorization evidence, configuration digest and Hermes task association before returning safely queued acknowledgement.
- [ ] **A2.** One event delivered 100 times in 10 seconds, across restart and overlapping reconciliation polling, produces one logical task and at most one active attempt and linked PR; distinct delivery IDs remain separately inspectable without creating new generations.
- [ ] **A3.** Invalid signature, wrong or unready registration, unauthorized label actor, revoked opt-in, missing acceptance criteria or permission-granting prose creates no execution authority; a redacted reason is persisted without secret/content bodies.
- [ ] **A4.** Pending issue edits update context/fingerprint; active edits require re-evaluation and cannot spawn parallel work. Reordered revoke/opt-in/edit delivery is reconciled against current GitHub state, and factory-generated notifications cannot trigger an intake loop.
- [ ] **A5.** Crash injection immediately before/after durable acceptance and Hermes task association retains every acknowledged identity and repairs association without double dispatch; ambiguous or unavailable durable writes never return a safely queued acknowledgement.
- [ ] **A6.** On the documented host with one repository, one active task, up to 100 queued tasks and 10,000 events, sustained 1 event/second for 5 minutes yields handler-to-durable accept/reject p95 ≤2 seconds; remote dependency latency is reported separately. work_accepted/work_rejected events include identity/delivery IDs, reason/config digest and exclude bodies.

**Effort**: 2 days

**Dependencies**: Task 2.1, Task 2.2

**Blocks**: Task 2.4

**Workstream**: intake

**PRD Reference**: §3.2 F02; §5.1 intake/duplicate/persistence; §6.4 Accepted work; §7.1 intake events

**Feature coverage**: F02. **Requirement coverage**: F02, REC02, EVT02, NFR-P01, NFR-P02, NFR-P09.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: Added 2.2 prerequisite to satisfy F02 → F01 and ready registered identity. Durable Hermes primitives were proved in the spike; no new independent queue/scheduler. Performance values are proposed Q9 targets, measured honestly and replanned if unmet.

<a id="task-2-4"></a>

### Task 2.4: Integrate one bounded sequential IDD execution lane

**Description**: Integrate selected IDD procedures as one bounded Hermes-owned execution lane with isolated workspace and separate sequential implementation/review sessions. Explicit limits and deterministic claims prevent overlapping or hidden extra execution (F03).

**Acceptance Criteria**:

- [ ] **A1.** A ready authorized task receives an isolated workspace, repository/issue acceptance criteria, monotonic task/attempt/fence identity, effective config digest, pinned skills, selected role/model/runtime and limits; a second ready task stays durably queued with a visible occupied-lane reason.
- [ ] **A2.** Implementation and independent review run in distinct sessions sequentially through supported Hermes claims/transitions; no IDD independent backlog loop, second scheduler, implicit auto-merge, provider/model fallback or unsupported subdelegation executes.
- [ ] **A3.** Missing tool, unavailable configured model, unsupported capability or unapproved skill blocks before dispatch; a worker can request but cannot commit state transitions or expand authority.
- [ ] **A4.** Boundary tests enforce two implementation attempts total (initial plus one fix), 60 minutes cumulative active worker execution across roles excluding CI wait, and 24-hour task wall time; warning occurs once at 80% and stop/park at 100%, with honest measured/unknown usage and no silent budget extension.
- [ ] **A5.** An explicit operator retry requires fresh authorization and starts a new audited execution generation without hiding prior attempts or usage. Another task cannot receive active authority until prior generation is fenced and termination is confirmed or quarantine resolved.
- [ ] **A6.** After 60 seconds without expected heartbeat, the old generation is fenced and quarantined before replacement eligibility; late output is rejected and process/descendant termination uncertainty remains visible.
- [ ] **A7.** Idle audit for 30 minutes with no eligible work/exceptions records zero model calls; attempt_started/attempt_finished persist task/attempt/generation, role, runtime/model, duration/verdict and measured usage or unknown. Heartbeat and budget alerts deduplicate per identity/severity and remain locally visible.

**Effort**: 2 days

**Dependencies**: Task 2.2, Task 2.3

**Blocks**: Task 2.5, Task 2.7

**Workstream**: execution

**PRD Reference**: §3.2 F03; §5.1 active/wall/idle targets; §6.4 Attempt; §7.1 attempt events; §7.3 heartbeat/budget

**Feature coverage**: F03. **Requirement coverage**: F03, REC03, EVT03, NFR-PC02, NFR-P08, ALERT01, ALERT04.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: The isolated workspace separates files; it is not a security boundary. Reuse the spike-proven credential/claim primitives and handoff through Hermes. No alternate harness is in MVP; timings/defaults remain Q9 acceptance targets.

<a id="task-2-5"></a>

### Task 2.5: Implement serialized publication intents and authorization rechecks

**Description**: Promote the spike-proven privileged boundary into serialized durable branch/PR publication intents. Validate current authorization and fence/config/revision on each effect so revocation, crashes and worker output cannot create duplicate or stale publication (§5.2, §6.4).

**Acceptance Criteria**:

- [ ] **A1.** Authorized current-generation publication writes a stable intent containing task/generation, repository, expected revision, permitted operation and target branch/PR before sending the effect; branch/PR read-back binds the remote result to that same identity.
- [ ] **A2.** Worker, test process and descendants receive no controller tokens, production secrets or unrestricted GitHub write credentials; a mutation fixture must cross the scoped deterministic broker and is denied for wrong repository/action, stale generation, changed config, expired claim or revoked actor authority.
- [ ] **A3.** Concurrent publication attempts serialize to one accepted operation/PR; crash after remote PR creation before response persistence discovers the existing PR by recorded work identity on recovery and never blindly republishes.
- [ ] **A4.** Cancellation/revocation committed before effect authorization prevents any new effect; in-flight effects authorized before the fence are reconciled and accurately linked instead of silently rolled back or reported absent.
- [ ] **A5.** Two matching remote PRs, uncertain repository identity or ambiguous API outcome parks the task for reconciliation; tests assert no new PR and no automatic close/delete/overwrite of user work.
- [ ] **A6.** A single fenced-result or unauthorized mutation attempt is rejected, retained as audit evidence, quarantines affected work and emits a high-severity deduplicated local/operator alert; more than one matching remote PR emits the specified high-severity identity alert.

**Effort**: 2 days

**Dependencies**: Task 2.4

**Blocks**: Task 2.6, Task 2.8, Task 3.3

**Workstream**: publication

**PRD Reference**: §5.2 deterministic authorization/credential boundary; §6.4 Publication intent; §7.3 fenced effects/duplicate PR alerts

**Feature coverage**: F03, F06. **Requirement coverage**: NFR-S01, NFR-S03, REC06, ALERT03, ALERT06.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: Guard every permitted write: prompts, worktrees and branch protection alone do not provide fencing. Reuse supported broker/storage primitives; no unrestricted credential handoff or destructive uncertainty cleanup.

<a id="task-2-6"></a>

### Task 2.6: Implement independent review and current-revision verification

**Description**: Attach separate-session review and authoritative required-check evidence to the current linked PR revision. Treat verified as a timestamped intermediate observation that becomes stale when head/base or checks change, never as permission to merge (F04).

**Acceptance Criteria**:

- [ ] **A1.** At implementation SHA A, a separate reviewer session inspects the diff against issue criteria and persists its own identity, verdict, findings and SHA A; self-reported implementation review cannot satisfy the independent review requirement.
- [ ] **A2.** For positive independent review, linked open PR and permitted successful required checks at A, verification reads GitHub and persists PR URL/number, current head/base SHA, check-run/provider identities/conclusions, review identity, artifact/log references and observation time only if the authoritative head still equals A.
- [ ] **A3.** Head changes A→B, failed/missing checks, unavailable GitHub or unexpected closed/draft PR yields stale, failed/blocked or unknown status with reason and never verified; worker green exit alone cannot satisfy the contract. Human-authored commits are never overwritten to restore A.
- [ ] **A4.** Protected-check contexts plus explicit project acceptance commands define a nonempty operator-approved contract; missing contract blocks. Skipped/neutral conclusions pass only when the contract explicitly permits them.
- [ ] **A5.** A review change request allows one fix only while cumulative attempt/time budget remains; the new SHA rebuilds all revision-dependent review/check evidence. Exhausted limits park rather than add another attempt.
- [ ] **A6.** Later observed revision/base/check-policy changes invalidate old observation; status always shows the verified SHA and timestamp and does not imply merge authorization. evidence_checked includes PR/revision/check/review identities, observation time and passing/stale/unknown reason; every verified result retains reproducible review/CI fixture evidence.

**Effort**: 2 days

**Dependencies**: Task 2.5

**Blocks**: Task 2.8, Task 2.9, Task 3.1

**Workstream**: verification

**PRD Reference**: §3.2 F04; §6.4 Evidence; §7.1 evidence_checked; §8.2 independent-review/CI gate

**Feature coverage**: F04. **Requirement coverage**: F04, REC04, EVT04, GATE-M06.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: Use recorded config contract from 2.1 and revision/fence authority from publication. Preview, human approval and guarded merge remain Sprint 3 prerequisites for endpoint completion, so this task must not mark merged or complete prematurely.

<a id="task-2-7"></a>

### Task 2.7: Implement typed Telegram status, pause and cancellation

**Description**: Implement typed authorized Telegram status/control against durable task and generation records. Distinguish persisted fencing, pause boundaries and confirmed process exit, and provide readable targets/actions with text alternatives (F05).

**Acceptance Criteria**:

- [ ] **A1.** An allowlisted numeric actor in the configured chat submits recognized status/pause/resume/cancel/retry with explicit repository/task/generation; command ID, restricted actor/chat reference, action, target, receipt/commit times and accepted/rejected outcome persist before attributable acknowledgement.
- [ ] **A2.** Pause during a stage records pause requested; after that stage completes the next stage cannot start until authorized resume. Status distinguishes pause requested, paused and resumed and shows task/attempt, blocker, SHA/time, limits, committed-effect links and last heartbeat/reconciliation.
- [ ] **A3.** Cancellation fences the active generation before termination starts and within 5 seconds of authenticated handler receipt; new effects and late completions are denied. Worker/descendants exit within 30 seconds or quarantine with local/operator notification within that window; replacement never dispatches while termination authority is uncertain.
- [ ] **A4.** Fencing acknowledgement and confirmed process exit are separate persisted outcomes. Effects committed before cancellation are linked and accurately reported without silent reversal; cancel is terminal for that generation and retry needs a freshly authorized audited generation.
- [ ] **A5.** Forged actor, wrong chat, ambiguous/missing target, duplicate command, stale generation or unsupported request is rejected or asks for a concrete target without broadening permissions/repeating effects; natural-language proposals still pass the same typed validator.
- [ ] **A6.** Responses/local reports use explicit state words, stable identifiers and authorized links with clear scope for screen-reader users; every button has a documented text command alternative and no result depends only on color/emoji. Transport delay/failure cannot undo committed control state.

**Effort**: 2 days

**Dependencies**: Task 2.4

**Blocks**: Task 2.8, Task 2.9, Task 3.2

**Workstream**: control

**PRD Reference**: §3.2 F05; §5.1 control/cancellation timing; §5.3 text accessibility; §6.1 command ownership; §6.4 Control; §7.1 control_recorded

**Feature coverage**: F05, F07. **Requirement coverage**: F05, REC05, EVT05, NFR-P04, NFR-P05, NFR-C02, ARCH-O06.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: Hermes owns Telegram conversation/runtime transport. The kit validates typed factory actions and target scope; exact command syntax follows spike decisions. Timing is authenticated handler to durable control, excluding Telegram network latency.

<a id="task-2-8"></a>

### Task 2.8: Implement restart and GitHub reconciliation through Hermes ownership

**Description**: Recover known durable work, claims, publication/control and notification identities through Hermes restart and periodic GitHub reconciliation. Remote ambiguity parks work; neither chat replay nor another scheduler grants authority (F06).

**Acceptance Criteria**:

- [ ] **A1.** Kill/restart after durable acceptance but before dispatch recovers or explicitly parks all known active tasks within 60 seconds of controller readiness through supported Hermes ownership; no Telegram history replay is needed and acknowledged work is retained.
- [ ] **A2.** Crash after remote PR create but before response record discovers the existing PR through intent/work identity before any creation; ambiguous or duplicate identity parks. Crash before/after task association repairs one task and does not duplicate execution.
- [ ] **A3.** Expired lease, unreachable worker or stale result fences the old generation before replacement eligibility; cancellation/control and notification identities survive restart, repeated results cannot advance state, and uncertain descendant termination remains quarantined.
- [ ] **A4.** Periodic reconciliation has nominal interval ≤60 seconds and discovers dropped-webhook eligible work within 120 seconds of restored GitHub connectivity absent server-directed backoff; webhook overlap still produces one task/attempt/PR and revoked permissions forbid dispatch/newly authorized effects.
- [ ] **A5.** GitHub network/rate-limit fixtures use bounded retry/backoff with a 5-minute nominal cap unless upstream asks longer, honor retry guidance and mark stale/unknown observations; no blind repeat mutation or evidence-dependent completion occurs during uncertainty.
- [ ] **A6.** Unexpected terminal PR state, human branch/head/base movement or irreconcilable identity parks visibly without new PR or overwritten human commits. Offline/readiness/process liveness and last successful reconciliation are observable; no always-on host SLA is claimed.
- [ ] **A7.** After 5 minutes with no successful GitHub observation, a deduplicated medium alert marks remote state stale and stops evidence-dependent completion. recovery_completed records task/attempt, source, reused identity and parked/resumed reason; critical alerts remain locally visible when Telegram is unavailable.

**Effort**: 2 days

**Dependencies**: Task 2.5, Task 2.6, Task 2.7

**Blocks**: Task 2.9, Task 3.1, Task 3.3, Task 3.5, Task 3.6

**Workstream**: recovery

**PRD Reference**: §3.2 F06; §5.1 reconciliation/restart/backoff; §6.4 transition authority; §7.1 recovery_completed; §7.3 reconciliation outage

**Feature coverage**: F06. **Requirement coverage**: F06, EVT06, NFR-P06, NFR-P07, ALERT02, STATE04.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: Reconcile via Hermes ownership and supported periodic facilities; do not add a second queue/scheduler or infer current state from chat. Target fixtures use the documented one-repo/one-active/100-pending/10,000-event host scale; report measured results and blockers.

<a id="task-2-9"></a>

### Task 2.9: Record local evidence, usage and operator effort events

**Description**: Implement append-only local evidence/usage/operator effort events and minimal summaries so v1.0 outcomes and baseline intervention cost can be inspected without centralized telemetry or invented usage (F07, §7).

**Acceptance Criteria**:

- [ ] **A1.** Terminal/waiting status includes task/attempt/generation, explicit outcome/blocker, current relevant revision, PR/check/review references, elapsed active-worker time and remaining limits; corrections append superseding evidence events without rewriting historical records.
- [ ] **A2.** Event schema tests persist attempt boundaries, evidence checks, restricted-actor control records, recovery outcomes and operator_effort_recorded with required identity/runtime/model/verdict/observation fields; manually supplied active minutes/intervention category retain attribution and missing effort remains unknown.
- [ ] **A3.** Token counts with provider/unit attribution, actual billed monetary values and subscription quota indicators occupy separate fields; absent runtime values stay unknown instead of zero/estimated currency. Queue, model-execution, CI wait and human wait durations are tracked separately.
- [ ] **A4.** Local diagnostic/status summaries report completed, blocked, failed and canceled counts alongside rates, installation attempts and baseline/intervention minutes; no generated-code-volume success proxy or centralized telemetry service is introduced.
- [ ] **A5.** Credential-canary/private-body export fixtures contain no seeded credentials and exclude raw issue bodies/code/full logs/chat/usernames by default; explicitly selected expanded export still redacts secrets. Telegram summaries are concise and link authorized evidence rather than embedding raw content.
- [ ] **A6.** Aggregate event properties contain no issue bodies, code, usernames, tokens or chat text; raw numeric actor IDs remain restricted references. Aggregate pilot export requires recorded participant agreement and cannot silently enable external collection.
- [ ] **A7.** Local append-only storage and summaries survive restart; seeded correction and unknown-usage fixtures retain truthful previous events, schema/version and fixture identity for reproducible measurements.

**Effort**: 1 day

**Dependencies**: Task 2.6, Task 2.7, Task 2.8

**Blocks**: Task 3.1, Task 3.4, Task 3.6

**Workstream**: observability

**PRD Reference**: §3.2 F07; §7.1 event contracts/measurement; §7.2 operational views

**Feature coverage**: F07. **Requirement coverage**: F07, EVT03, EVT04, EVT05, EVT06, EVT08, MEASURE01, MEASURE02.

**Verification**: Run deterministic unit checks for identity/policy/transition boundaries and targeted integration/fault fixtures for the criteria; retain host/runtime/skill versions, fixture identity, observations and failures.

**Implementation notes**: Uses upstream-supported durability chosen in the spike, not a separate analytics service. Privacy/retention enforcement and full diagnostic delivery fault tests are coordinated with Sprint 3; every event producer here must already emit minimal redacted properties. Q9 values/metric targets are proposed pending acceptance.

## Sprint 3: Preview, approval, guarded merge and reproducible v1.0 gates

**Phase**: MVP Completion

**Tasks**: 10

**Total effort**: 19 developer-days; no calendar duration promised.

**Workstreams**: endpoint (3.1, 3.2, 3.3); operations (3.4); ownership (3.5); verification (3.6, 3.7, 3.8); release (3.9, 3.10).

**Exit**: accepted internal v1.0; all ten Must features, all 19 fault rows ×3 and Q9 acceptance. Q8 public distribution decisions do not block an explicitly internal recipe.

<a id="task-3-1"></a>

### Task 3.1: Implement revision-bound preview, smoke verification and owned cleanup

**Description**: Extend the selected, proven preview adapter into the full F11 lifecycle so a human sees meaningful evidence for the actual proposed change. Persist authoritative deployment/artifact identity, recorded head/base, access scope and smoke evidence within supported Hermes metadata; enforce bounded factory-owned resource cleanup without granting coding or test workers deployment credentials.

**Acceptance Criteria**:

- [ ] **A1.** For a positively independently reviewed PR with all required checks passing, deployment records selected provider/deployment ID, immutable source or artifact identity, exact head/base SHA, URL, agreed visibility, configured smoke command/expected result, observed result/time, expiry and cleanup ownership; provider evidence corroborates identity rather than relying on worker self-report.
- [ ] **A2.** Run the meaningful smoke contract against the recorded deployment using synthetic test data in the selected non-production environment; successful evidence is attached to the approval request. A not-applicable preview cannot satisfy this endpoint.
- [ ] **A3.** Failed smoke, provider outage, mismatched head/base, expired deployment or unknown artifact identity produces preview_failed and a visible blocker; no approval-ready or merge state is granted, and an old preview never substitutes for a new revision.
- [ ] **A4.** Head/base movement or committed cancellation invalidates prior evidence; late deployment callbacks cannot revive fenced generations. Authorization/current generation and configuration are rechecked at the scoped deployment boundary; credentials never enter coding/test processes.
- [ ] **A5.** Enforce one active preview per task and a proposed maximum resource lifetime of 24 hours. When the provider is reachable, confirm removal of factory-owned resources within 60 minutes after confirmed task termination; provider outages retain a visible cleanup backlog and never claim removal.
- [ ] **A6.** Preserve all human-created/unowned provider resources, and emit preview_verified/preview_failed with deployment/artifact identity, head/base, observation, outcome and cleanup deadline. Integration tests cover happy smoke, each denial condition, cancellation callback and cleanup/outage recovery.

**Effort**: 2 days

**Dependencies**: Task 2.6, Task 2.8, Task 2.9

**Blocks**: Task 3.2, Task 3.5

**Workstream**: endpoint

**PRD Reference**: §3.2 F11; §6.1; §6.4 Preview evidence; §7.1; §7.3; §8.2

**Feature coverage**: F, 1, 1. **Requirement coverage**: F11, REC07, EVT09, ARCH-O07, ALERT07, FAULT13.

**Verification**: Use selected provider integration fixtures plus clock-controlled lifetime/cleanup assertions and authoritative provider read-back.

**Implementation notes**: Reuse the provider and supported persistence/fence boundary proven by the spike. Q10 selection is a prerequisite, not an invitation to invent a provider. TTL and cleanup targets remain proposed pending Q9 acceptance.

<a id="task-3-2"></a>

### Task 3.2: Persist one-use revision-bound approval decisions

**Description**: Implement F12 human decisions as durable, typed one-use authority rather than Telegram conversation state. Bind each request to the exact merge action, repository/PR revision, current review/check/preview evidence and effective configuration/policy digest so replay or changes cannot extend permission.

**Acceptance Criteria**:

- [ ] **A1.** When current independent review, CI and preview evidence are available, Telegram presents repository/PR link, head/base SHA, preview URL, smoke result, selected merge method and explicit expiry; persist one-use request ID, action=merge, target, evidence/preview/config digests and issuance/expiry times before displaying it.
- [ ] **A2.** Accept a decision only from a current allowlisted numeric Telegram actor in the configured conversation for the unambiguous request/target; record durable decision ID, restricted actor, action, target, receipt/outcome and consumed/revoked state with approval_decided or approval_invalidated.
- [ ] **A3.** Default expiry is 60 minutes. Forged, replayed, expired, rejected or revoked decisions grant no merge authority and retain explicit auditable rejection reasons. A rejected request leaves the task human-blocked without automatic repeated approval prompts.
- [ ] **A4.** Repeated or concurrent button/text approvals and controller/chat restarts produce one accepted durable decision and at most one consumable approval/merge intent; awaiting-approval survives restart without replaying chat history as authority.
- [ ] **A5.** Changed head/base or effective policy/config, new failed/missing required checks, unhealthy/stale preview, revoked actor or canceled/paused task invalidates affected approval. Resume alone cannot revive authority; reverification creates a fresh evidence set and requires a fresh human approval.
- [ ] **A6.** Exercise valid approval, reject, revoke, expiry boundaries, actor/chat mismatch, simultaneous duplicate commands and restart before/after decision persistence; assert decisions never broaden action, scope, revision or expiry.

**Effort**: 2 days

**Dependencies**: Task 2.7, Task 3.1

**Blocks**: Task 3.3

**Workstream**: endpoint

**PRD Reference**: §3.2 F12; §6.3; §6.4 Approval and state; §7.1; §8.2

**Feature coverage**: F, 1, 2. **Requirement coverage**: F12, REC08, EVT10, STATE03, FAULT14, FAULT15, FAULT16.

**Verification**: Clock-controlled identity/authorization tests and supported-durability crash tests; compare persisted request/decision and generation records before/after restart.

**Implementation notes**: Consume authority atomically with the merge intent in Task 3.3 using the proven supported storage primitive. Do not introduce a second transition engine. Decision history is append-only with correction/invalidation events.

<a id="task-3-3"></a>

### Task 3.3: Implement protected conditional merge and authoritative outcome reconciliation

**Description**: Complete F12 with a single deterministic guarded merge owner that consumes current durable approval only for compatible current evidence. Close head/base/check races using the supported expected-head merge operation plus repository-enforced current-base protections proven in the spike, then reconcile authoritative remote outcomes instead of inferring success from a sent request.

**Acceptance Criteria**:

- [ ] **A1.** Before consuming approval and every merge mutation, recheck current actor permission, claim/fence generation and effective config/policy digest; GitHub head/base, PR open and non-draft state, mergeability, required protections/check identities/conclusions, matching independent review/preview candidate and preview health must agree with the approved evidence.
- [ ] **A2.** Preview smoke observation must be no older than 10 minutes at merge and approval unexpired under its default 60-minute expiry. Failed smoke or age above 10 minutes blocks merge, revokes affected approval and triggers reverification/new human approval; changed base/check/policy also invalidates it.
- [ ] **A3.** Atomically consume one valid approval into one stable merge intent; use only the selected GitHub-supported merge method with expected-head condition, credentials unable to bypass required protections, and up-to-date-base rules or equivalent atomic queue enforcement proven in the spike. Concurrent invocations cannot create another intent.
- [ ] **A4.** Inject base movement/check invalidation between read and mutation: GitHub protection/atomic enforcement prevents incompatible merge. If the proven mechanism cannot close this race, readiness marks merge unsupported and release fails; read-then-write alone or auto-merge is not accepted.
- [ ] **A5.** Draft/closed PR, incompatible or dirty base, missing/failed required check, unsupported protection, bypass-only credentials or competing IDD/other merge owner produces an explicit blocker. Suppress competing automation or refuse readiness; never enable GitHub auto-merge that can outlive approval expiry.
- [ ] **A6.** Persist merge outcome with intent/approval identity, expected head/base, method, authoritative merged flag, actual merge commit SHA and read-back time; report merged only after GitHub read-back. An API timeout/lost response reconciles before any retry and parks unknown state, emitting merge_observed and an unknown-result alert.
- [ ] **A7.** Fence/cancel/revocation forbids new effects after its committed boundary; reconcile earlier accepted in-flight merge and record its actual outcome with cancellation-race explanation. Human GitHub merges retain actual actor and are not attributed to factory approval; no destructive reversal occurs without a separate human decision.

**Effort**: 2 days

**Dependencies**: Task 2.5, Task 2.8, Task 3.2

**Blocks**: Task 3.4, Task 3.7

**Workstream**: endpoint

**PRD Reference**: §3.2 F12; §6.1; §6.4 Merge outcome; §7.1; §7.3; §8.2

**Feature coverage**: F, 1, 2. **Requirement coverage**: F12, REC09, EVT11, ARCH-O08, ALERT08, FAULT17, FAULT18, FAULT19.

**Verification**: Integration tests against the selected protected repository configuration with remote read-back; assert atomic approval/intent uniqueness and no effect after committed fencing.

**Implementation notes**: Use the spike evidence for the supported merge and protection primitive; expected-head alone does not close the base/check race. Maintain GitHub as authoritative and Hermes as lifecycle owner.

<a id="task-3-4"></a>

### Task 3.4: Deliver durable notifications, accessible diagnostics and privacy cleanup

**Description**: Complete F07 operational feedback with a durable notification outbox, usable text status/local diagnostics and concrete privacy cleanup. Preserve committed task/control state through transport outages and retain replay-resistant identity while pruning proposed log/audit retention windows.

**Acceptance Criteria**:

- [ ] **A1.** Persist notification/event IDs, authorized destination reference, retry count and terminal outcome. Deduplicate by task/event identity and severity transition; Telegram failure gets three bounded retries over at least 60 seconds, retains the pending record and exposes local notification_failed without rolling back committed task/control state.
- [ ] **A2.** Local and Telegram views show installation attempts/completion/intervention minutes; task/attempt/generation, explicit state/blocker, current revision/PR/check/review/preview links and observation freshness, last heartbeat/reconciliation, queue age, elapsed worker time, remaining limits and notification failures. Stale remote observations and unknown usage are labelled explicitly.
- [ ] **A3.** Every button action has a documented equivalent text command. Stable identifiers, explicit state words, target scope and links remain understandable without color/emoji and with screen-reader text inspection; ambiguous targets request clarification without executing.
- [ ] **A4.** Redact known credentials before logs/Telegram/export and verify secure service transport and least-privilege secret-store access. Canary credentials are absent from all exports/messages and raw code/full logs/private content bodies are excluded by default; expanded export requires explicit operator content selection.
- [ ] **A5.** Clock-controlled cleanup honors proposed configurable retention of 7 days for worker logs and 30 days after completion for detailed audit metadata while retaining active task/control records and enough work/remote identities to prevent old webhook replay. Minimal identity tombstones persist until explicit registration removal; export/delete explains this distinction.
- [ ] **A6.** No centralized telemetry is enabled by default. Critical alerts remain locally visible and severity transitions are deduplicated; offline host monitoring does not claim remote delivery without a separately configured external monitor. Tests cover successful delivery, outage/restart, duplicate alerts, retention boundaries and replay after detail pruning.

**Effort**: 2 days

**Dependencies**: Task 2.9, Task 3.3

**Blocks**: Task 3.6, Task 3.7, Task 3.8

**Workstream**: operations

**PRD Reference**: §3.2 F07; §5.2–5.3; §7.1–7.3; §8.2

**Feature coverage**: F, 0, 7. **Requirement coverage**: F07, EVT07, VIEW01, ALERT05, ALERT-C01, NFR-S04, NFR-S05, NFR-P03, NFR-C02, GATE-M05.

**Verification**: Transport-failure/restart integration tests, clock-controlled retention and replay tests, canary scan and manual screen-reader/text-action review.

**Implementation notes**: Reuse supported metadata/outbox integration; auxiliary records cannot schedule work. Internal privacy scope is confirmed without certification claims; Q9 owns numerical retention acceptance.

<a id="task-3-5"></a>

### Task 3.5: Implement ownership-aware removal preserving edits and active authority

**Description**: Implement F08 reviewed ownership-aware removal for installations and remote previews. Stop intake and resolve execution authority before removing a registration, preserve edits and shared resources, and expose an explicit task-history retain/export/delete choice.

**Acceptance Criteria**:

- [ ] **A1.** Produce an operator-reviewable uninstall plan from recorded owned paths/integration blocks/checksums and owned provider resources; after approved application only those unchanged owned resources are removed and unrelated repository content remains byte-for-byte identical.
- [ ] **A2.** If a factory-created file or owned block has user edits, preserve it or request a specific reviewed resolution; never delete user changes, overwrite dirty work or automatically stash/reset. Reinstall remains idempotent and repeat setup reports no duplicate integrations.
- [ ] **A3.** Before credential/state removal, stop intake, commit an active-generation fence, and confirm worker/descendant exit. Unconfirmed termination blocks removal or surfaces quarantine with recovery instructions; active worktrees are never forcibly deleted and no orphaned execution authority remains.
- [ ] **A4.** Require explicit retain/export or delete history choice, explain identity tombstones and registration-removal consequences, and verify the selected outcome. Removing one registration leaves shared Hermes infrastructure, IDD configuration, shared skills and tokens used elsewhere intact.
- [ ] **A5.** Clean only factory-owned previews/hooks/registration/config and intended registration authority; preserve human PRs/branches/provider resources and shared credentials. Unreachable provider cleanup remains visibly pending; no false complete-removal claim.
- [ ] **A6.** Integration fixtures cover clean uninstall, edited owned file/block, unrelated dirty files, active descendant, unconfirmed termination, shared infrastructure and each history choice; compare filesystem and remote identities before/after.

**Effort**: 2 days

**Dependencies**: Task 2.2, Task 2.8, Task 3.1

**Blocks**: Task 3.6, Task 3.7, Task 3.9

**Workstream**: ownership

**PRD Reference**: §3.2 F01 and F08; §5.2; §8.2

**Feature coverage**: F, 0, 8. **Requirement coverage**: F08, GATE-M04, FAULT12.

**Verification**: Byte-level before/after comparison plus worker/fence and provider read-back in clean and interrupted uninstall/reinstall fixtures.

**Implementation notes**: Use ownership inventory from installation and preview adapter; setup/removal operate on reviewed diffs. Shared infrastructure ownership is not implied by a registration.

<a id="task-3-6"></a>

### Task 3.6: Verify lifecycle, recovery and trust faults three times

**Description**: Build and execute reproducible integration/fault tests for the first twelve §8.2 rows against the selected support recipe. Each row must pass three independent repetitions with deterministic identity/policy/transition assertions and authoritative remote read-back where applicable; this finite suite gates recovery correctness without claiming universal security proof.

**Acceptance Criteria**:

- [ ] **A1.** FAULT01 repeated delivery/reordered events/webhook-poll overlap: each of three runs has one logical task, at most one active attempt and one PR; FAULT02 crash before/after durable intake/task association: every acknowledged identity is retained and reconciled without duplicate execution.
- [ ] **A2.** FAULT03 crash after remote PR creation before response recording: discover existing PR before creation and park ambiguous identity; FAULT04 dead worker/expired claim/late result: fence old generation before replacement and accept no stale completion or overlapping authority. Each row passes three runs with remote identity read-back.
- [ ] **A3.** FAULT05 cancel with delayed descendant: effects fenced and exit confirmed or quarantine surfaced; FAULT06 head changes after review/checks: old evidence invalidated and new SHA never called verified. Execute each three times and retain fence/exit/revision assertions.
- [ ] **A4.** FAULT07 missing/failed CI or missing verification contract: blocked/failed with no worker-self-report fallback; FAULT08 GitHub outage/rate limit and Telegram outage: bounded retries respecting upstream guidance, remote state unknown and durable notification/recovery retained. Execute each row three times.
- [ ] **A5.** FAULT09 authorization revoked after enqueue: no dispatch or newly authorized effect; FAULT10 quota/runtime/fix-attempt limits: park visibly with measured/unknown usage, enforce one active task, initial plus one fix and 60 active-worker minutes across roles excluding CI wait plus 24-hour wall limit. Each row passes three runs.
- [ ] **A6.** FAULT11 injection/secret canary: deterministic policy unchanged, privileged action denied and canary absent from exports/messages; FAULT12 reinstall/uninstall with user edits and active work: unrelated content/edits preserved, no destructive cleanup or orphaned authority. Each row passes three runs.
- [ ] **A7.** For all 12 rows (36 row repetitions), archive exact host/runtime/model/skill/provider/repo configuration versions, fixture and work/attempt/remote IDs, measured assertions and pass/fail evidence. Zero duplicate PRs, unauthorized merges and accepted fenced results; any failed/missing repetition keeps the release gate closed.
- [ ] **A8.** Audit all verified outcomes in the fixtures: 100% have matching current head, independent review and required-check evidence; stale/unknown outcomes are never counted passing. Compare acknowledged and recovered work sets for zero lost acknowledged tasks.

**Effort**: 3 days

**Dependencies**: Task 2.8, Task 2.9, Task 3.4, Task 3.5

**Blocks**: Task 3.8, Task 3.10

**Workstream**: verification

**PRD Reference**: §1.4 Completion/recovery correctness; §3.2 F01–F08; §5.1–5.2; §8.2 fault rows 1–12

**Feature coverage**: F, 0, 1. **Requirement coverage**: FAULT01, FAULT02, FAULT03, FAULT04, FAULT05, FAULT06, FAULT07, FAULT08, FAULT09, FAULT10, FAULT11, FAULT12, MET02, MET03, GATE-M07.

**Verification**: Use integration/fault injection for lifecycle guarantees and focused identity/policy/transition unit assertions; preserve actual remote read-back and seeded fixtures per repetition.

**Implementation notes**: Build fixtures from the completed implementation; share existing deterministic helpers rather than adding a duplicate simulation-only scheduler. Do not waive a safety outcome to meet the calendar.

<a id="task-3-7"></a>

### Task 3.7: Verify preview, approval and merge faults three times

**Description**: Execute the remaining seven §8.2 endpoint fault rows, three repetitions per row, to validate exact-revision preview/approval/merge authority and authoritative outcome recovery. Include races at the committed fencing and repository-enforced merge boundary, rather than accepting happy demo evidence.

**Acceptance Criteria**:

- [ ] **A1.** FAULT13 preview failure/wrong revision/expiry/provider outage: three runs deny approval-ready/merge, show error and never reuse old preview for new evidence; cover one-preview limit, 24-hour lifetime and reachable cleanup within 60 minutes with visible outage backlog.
- [ ] **A2.** FAULT14 forged/replayed/expired/rejected/revoked approval: three runs produce no merge and durable denial reason without broader authority; exercise actor/conversation/action mismatch, default 60-minute expiry and no automatic repeated rejection prompt.
- [ ] **A3.** FAULT15 head/base/policy changes after approval: three runs invalidate approval, reevaluate review/check/preview against the current compatible base and require new approval. Include missing/failed checks, stale or unhealthy smoke (>10 minutes), pause/cancel and actor revocation.
- [ ] **A4.** FAULT16 concurrent/repeated approve and controller restart: three runs retain durable request/decision state, consume one approval and create at most one merge intent; no chat-only authority and no revival on resume.
- [ ] **A5.** FAULT17 remote merge accepted then response lost: three runs authoritative read-back identifies actual merge SHA before any retry; unresolved state parks without blind repeat or false failure/success.
- [ ] **A6.** FAULT18 cancel/revoke races with preview/merge: three runs deny new effects after committed fence, reject late preview callbacks and accurately reconcile effects already accepted before the fence. Human external merges record the actual actor; no automatic destructive reversal.
- [ ] **A7.** FAULT19 dirty/incompatible base, draft/closed PR, competing automation or bypass-only credentials: three runs block merge until explicit compatible policy/current protections. Inject base/check movement at mutation boundary and prove expected-head plus server enforcement closes the race; unsupported enforcement fails the gate and auto-merge is never a shortcut.
- [ ] **A8.** Archive all seven rows (21 repetitions) with version/fixture/request/approval/intent/deployment/work IDs, deterministic assertions and remote read-back. Assert zero unauthorized merges, duplicate PRs and accepted fenced results; any absent/failed evidence prevents internal release.

**Effort**: 3 days

**Dependencies**: Task 3.3, Task 3.4, Task 3.5

**Blocks**: Task 3.8, Task 3.10

**Workstream**: verification

**PRD Reference**: §1.4; §3.2 F11–F12; §6.4; §8.2 fault rows 13–19

**Feature coverage**: F, 1, 1. **Requirement coverage**: FAULT13, FAULT14, FAULT15, FAULT16, FAULT17, FAULT18, FAULT19, MET02, GATE-M02, GATE-M07.

**Verification**: Protected-repository/provider integration and deterministic crash/race injection, controlled clocks, authoritative read-back; log each repetition independently.

**Implementation notes**: Combine with Task 3.6 for all 19 PRD rows/57 row repetitions. These are scenario tests and do not certify all possible security failures. Keep failed endpoint gates visible.

<a id="task-3-8"></a>

### Task 3.8: Measure local targets and obtain acceptance of proposed defaults

**Description**: Measure all proposed §5.1 local targets on the documented selected host and submit Q9 numerical/default decisions to Luong before MVP acceptance. Separate external service latency and safety-fault outcomes from local timing so measured results, uncertainty and accepted adjustments remain auditable.

**Acceptance Criteria**:

- [ ] **A1.** Benchmark one registered repository/one active task with up to 100 pending tasks and 10,000 retained events; record host/runtime/config/fixtures and separate GitHub/model/Telegram network delay from local measurements.
- [ ] **A2.** Durable handler accept/reject at 1 event/second for 5 minutes has proposed p95 ≤2 seconds with no acknowledgement before persistence; 100 duplicate deliveries within 10 seconds, including restart, yield one logical task and ≤1 PR.
- [ ] **A3.** Over 100 persisted local-status queries proposed p95 is ≤1 second and stale remote observations are explicit; authenticated pause/cancel and fence persist within 5 seconds independently of Telegram delivery.
- [ ] **A4.** Long-running worker/descendant exits within 30 seconds of cancellation or quarantine and local notification occur within that window, without replacement while uncertain; nominal reconciliation interval ≤60 seconds discovers dropped eligible work within 120 seconds after GitHub returns absent upstream-directed backoff.
- [ ] **A5.** After controller readiness, every known active task recovers or explicitly parks within 60 seconds; defined crash tests lose zero acknowledged tasks; a 30-minute idle interval without eligible work/exceptions produces zero model calls.
- [ ] **A6.** Verify observable offline/liveness/last-reconciliation state, no always-on SLA claim, CI wait excluded from 60 active-worker minutes, 24-hour total wall-time parking, and nominal retry backoff cap of 5 minutes unless upstream requires longer delay.
- [ ] **A7.** Provide measured pass/fail and uncertainty for each target, plus proposed 7-day logs/30-day post-completion audit retention, attempt/time budgets, 24-hour preview lifetime, 60-minute cleanup/approval expiry and 10-minute smoke freshness; record Luong Q9 acceptance or explicit measured adjustment before MVP acceptance. Unmet/unaccepted targets remain blockers or explicit replan, never claimed achieved.

**Effort**: 1 day

**Dependencies**: Task 3.4, Task 3.6, Task 3.7

**Blocks**: Task 3.9, Task 3.10

**Workstream**: verification

**PRD Reference**: §5.1; §5.2; §3.2 F03/F11/F12; §9.1 Q9

**Feature coverage**: F, 0, 2. **Requirement coverage**: NFR-P01, NFR-P02, NFR-P03, NFR-P04, NFR-P05, NFR-P06, NFR-P07, NFR-P08, NFR-P09, NFR-PC01, NFR-PC02.

**Verification**: Instrument persistence/control/reconciliation boundaries and provider-call audit on selected host; use recorded benchmark fixtures and existing fault evidence, not calendar estimates.

**Implementation notes**: Q9 is an explicit acceptance decision pending evidence; this task does not silently finalize proposed defaults. Timing adjustments cannot weaken identity, authorization or safety gates.

<a id="task-3-9"></a>

### Task 3.9: Package tested installation, support and recovery recipes

**Description**: Package the implemented versioned integration/setup skill and a reproducible single-support-recipe guide with tested recovery, credential storage, webhook transport and backup/restore instructions. Publish only demonstrated commands and compatibility, preserving target-project CI as authoritative and recording component provenance/distribution decisions.

**Acceptance Criteria**:

- [ ] **A1.** A fresh selected supported host/runtime setup reproduces reviewed installation, readiness, one opted-in issue endpoint and removal using actual implemented commands; document compatible Hermes/IDD/runtime/model/skill pins, GitHub protections/permissions, selected provider/method and Telegram transport. No untested host/runtime/model support is claimed.
- [ ] **A2.** Document restart supervision, inbound webhook transport and scoped host secret mechanism, known failure/park/quarantine reasons, time/attempt/retention limits, ownership-aware removal and troubleshooting/recovery including ambiguous publication/merge read-back. Operator can follow the recipe without author takeover of undocumented commands.
- [ ] **A3.** Exercise a backup/restore rehearsal on the selected supported persistence mechanism: recover task/attempt/fence identities, delivery/notification records, approvals and remote intent associations; restore rechecks current remote state/authorization before dispatch and creates no duplicate task/PR or revived consumed/expired authority. Document safe backup scope and separate secret-store restoration.
- [ ] **A4.** Kit CI runs its own identity/lifecycle/security contracts while target-project checks remain authoritative; reviewed integration additions never silently replace existing CI. Capture compatibility test and recovery fixture/version evidence.
- [ ] **A5.** Record dependency/skill provenance and trust/license notice review plus Luong Q8 distribution license/channel/support/notice decisions before any public distribution; unresolved decisions block public publication without blocking an explicitly internal support recipe.
- [ ] **A6.** Negative walkthrough for unsupported version, missing executable/model/authentication, competing owner and corrupt/incomplete restore reports concrete blockers and does not dispatch; only implemented and tested install/removal commands are advertised.

**Effort**: 1 day

**Dependencies**: Task 3.5, Task 3.8

**Blocks**: Task 3.10

**Workstream**: release

**PRD Reference**: §5.3; §6.2; §6.5; §8.2; §9.1 Q8

**Feature coverage**: F, 0, 1. **Requirement coverage**: SPEC04, SPEC05, SPEC06, NFR-C01, GATE-M08.

**Verification**: Fresh-install and backup/restore rehearsal using fixture/remote identity comparisons; audit documented commands against executed outputs and dependency notices.

**Implementation notes**: Backup/restore exercise is the verification needed for the PRD infrastructure recipe, using the selected supported mechanism rather than inventing storage APIs. Public release authorization/decisions remain separate from internal v1.0 acceptance.

<a id="task-3-10"></a>

### Task 3.10: Assemble and accept the internal v1.0 evidence gate

**Description**: Assemble the reproducible internal v1.0 release evidence and obtain Luong acceptance only after all ten Must features and §8.2 gates pass. Treat verified as intermediate and require meaningful preview, revision-bound human approval and authoritative merge read-back for successful selected endpoint delivery; replan unresolved scope/schedule rather than weakening gates.

**Acceptance Criteria**:

- [ ] **A1.** Trace every F01–F08 and F11–F12 acceptance criterion to a reproducible passing result under exact selected host/runtime/model/skill/config/provider/repository fixture identity; missing, failed, stale or unknown evidence keeps GATE-M01/M03 closed.
- [ ] **A2.** Confirm GATE-M02/M06: selected preview/approval/merge endpoint executes with matching independent review and current required CI on every verified result, meaningful revision-bound smoke, unexpired one-use human approval, protected compatible-base expected-head merge and authoritative actual merge SHA read-back.
- [ ] **A3.** Confirm GATE-M04/M05: setup and repeat installation plus removal preserve unrelated content and edits, history choices/shared assets/active authority; local analytics/status and bounded deduplicated alerts work with concrete privacy/retention controls and no centralized telemetry.
- [ ] **A4.** Confirm GATE-M07: all 19 §8.2 fault rows have three independently recorded passing repetitions (57 total), zero duplicate PRs, unauthorized merges or accepted fenced results, and zero lost acknowledged work in defined crash tests; no safety criterion is waived for schedule.
- [ ] **A5.** Confirm GATE-M08: tested support/version pins, provenance, known limits/failures, installed commands, secrets/webhook/supervision and recovery/backup/restore/removal instructions are available; expose unresolved public Q8 distribution decisions separately.
- [ ] **A6.** Record Luong Q9 accepted numerical targets/defaults or explicit measured adjustments, internal privacy scope without compliance/certification claims, and owner release decision. Any failed gate or unresolved acceptance decision results in no-go/replan preserving the chosen endpoint and solo schedule risk.
- [ ] **A7.** Internal acceptance cannot authorize external collection, extra runtimes/providers, production publication/autonomous merge or parallel projects; v1.1 work starts only after internal gates pass and external participant data waits for obligation/consent review.

**Effort**: 1 day

**Dependencies**: Task 3.6, Task 3.7, Task 3.8, Task 3.9

**Blocks**: Task 4.1, Task 4.2, Task 4.3, Task 4.4

**Workstream**: release

**PRD Reference**: §1.3–1.4; §3.1–3.2 Must; §5; §8.2; §9.1

**Feature coverage**: F, 0, 1. **Requirement coverage**: F01, F02, F03, F04, F05, F06, F07, F08, F11, F12, GATE-M01, GATE-M02, GATE-M03, GATE-M04, GATE-M05, GATE-M06, GATE-M07, GATE-M08.

**Verification**: Requirement-to-evidence audit, selected-recipe end-to-end reproduction and owner gate review; use actual measured outputs rather than target claims.

**Implementation notes**: Acceptance is for internal v1.0 only. Do not turn the release evidence task into public distribution, production deployment or execution of deferred scope.

## Sprint 4: v1.1 checkpoint steering, repair and evidence-led pilot

**Phase**: Full Features

**Tasks**: 7

**Total effort**: 13 developer-days; no calendar duration promised.

**Workstreams**: runtime control (4.1); compatibility (4.2); internal validation (4.3); pilot admission (4.4); external validation (4.5); product decision (4.6); release docs (4.7).

**Entry**: passed internal v1.0. **Exit**: F09/F10 evidence, participant admission, actual measurements and an explicit continue/narrow/stop decision; observations may withhold release/expansion.

<a id="task-4-1"></a>

### Task 4.1: Add checkpointed scope steering with audited context generations

**Description**: Implement F09 only after the internal v1.0 gate. Apply authorized revised criteria at supported role/stage checkpoints, retain immutable context revisions and generation identity, and continue only within the remaining explicit task budgets.

**Acceptance Criteria**:

- [ ] **A1.** An allowlisted operator scope change persists target generation, revised acceptance criteria, authorization and audit identity before acknowledgment; duplicate commands and controller restart yield one accepted change.
- [ ] **A2.** At a supported checkpoint the old attempt is fenced before replacement obtains authority; replacement receives revised criteria and recorded remaining active-time, fix-attempt and wall-time budgets, with consumed usage retained rather than reset.
- [ ] **A3.** Changing scope or revision invalidates old review/check/preview/approval evidence; completion requires fresh matching evidence and a new revision-bound approval when applicable.
- [ ] **A4.** Canceled generations cannot be revived by steering, an old descendant result is rejected, and unsupported live steering displays checkpoint/restart behavior without dispatching an unsupported harness.
- [ ] **A5.** A runtime that cannot safely reach/fence a checkpoint, or a replacement with exhausted budget, parks with the reason and no overlapping authority; integration tests exercise retry, cancellation and restart races.

**Effort**: 2 days

**Dependencies**: Task 3.10

**Blocks**: Task 4.3, Task 4.7

**Workstream**: runtime control

**PRD Reference**: §3.2 F09; §3.2 F05–F06; §8.3

**Feature coverage**: F, 0, 9. **Requirement coverage**: F09, GATE-P03.

**Verification**: Use the selected runtime to inject checkpoint/restart/cancellation races, assert durable context and monotonically fenced generations, and verify stale evidence cannot authorize completion.

**Implementation notes**: Reuse supported Hermes lifecycle and F05/F06 authority contracts. No extra scheduler, live-injection claim, permission widening or new fix allowance. Implementation effort excludes upstream waits.

<a id="task-4-2"></a>

### Task 4.2: Implement versioned upgrade, repair and rollback recipes

**Description**: Implement F10 after v1.0 acceptance: plan reviewed compatible upgrades and repairs using owned-file provenance, validated checkpoints and explicit rollback while preserving project conventions, secrets and user changes.

**Acceptance Criteria**:

- [ ] **A1.** For a pinned supported installed version, dry-run displays compatibility checks, changed owned files, user-edit conflicts, migration steps and rollback instructions; no mutation precedes explicit plan acceptance.
- [ ] **A2.** A successful accepted upgrade fences active work, migrates owned files/registration, validates the new complete configuration and pins dependency/skill provenance before dispatch resumes.
- [ ] **A3.** Interrupting each migration boundary and restarting repair either restores a validated checkpoint or parks with a specific conflict; no worker dispatch occurs from partially migrated configuration.
- [ ] **A4.** An edited owned configuration, dirty unrelated file or concurrent user edit is preserved byte-for-byte and surfaced as a conflict; unsupported versions and changed checksum preconditions block application.
- [ ] **A5.** A failed compatibility/readiness check can restore the previous validated registration/configuration using documented rollback; integration fixtures demonstrate repair, rollback and removal without orphaned authority.
- [ ] **A6.** Before external pilot opening, publish a reproducible pinned support recipe, known limits and an independent install/upgrade/removal walkthrough.

**Effort**: 2 days

**Dependencies**: Task 3.10

**Blocks**: Task 4.3, Task 4.4, Task 4.5, Task 4.7

**Workstream**: compatibility

**PRD Reference**: §3.2 F10; §3.2 F01/F08; §8.3

**Feature coverage**: F, 1, 0. **Requirement coverage**: F10, GATE-P03.

**Verification**: Use old/new supported-version fixtures, interruption injection at every migration boundary, checksum conflicts and rollback read-back; assert dispatch remains blocked until a validated state exists.

**Implementation notes**: Retain ownership inventory and provenance. Never overwrite secrets or unrelated settings, automatically stash/reset user work, or resume active work using old policy. No package/command interface is invented before spike decisions.

<a id="task-4-3"></a>

### Task 4.3: Run the ten-issue two-project sequential dogfood comparison

**Description**: Instrument and analyze the proposed ten-issue internal usefulness cohort using trusted-maintainer selected issues across two sequential project registrations after v1.0 and F09/F10 pass. Compare operator burden against ten comparable direct-IDD baseline issues.

**Acceptance Criteria**:

- [ ] **A1.** Before selection, record bounded inclusion criteria, task mix, ten cohort issues and ten comparable baseline issues, fixed issue-to-preview-to-human-approved-merge endpoint and agreed Q9 target status.
- [ ] **A2.** Register the two projects sequentially with one active runtime task at all times; every issue retains matching revision evidence or explicit blocked/failed/canceled state, with those outcomes in the denominator and human declines separately recorded.
- [ ] **A3.** Report the proposed targets exactly: at least 8 of 10 issues preview-verified and merge-ready, at least 3 human-approved and merged within 30 days of MVP release across two sequential registrations, and at least 25% lower median intervention minutes per issue than the ten-issue comparable baseline within that 30-day window.
- [ ] **A4.** Measure active human intervention for successful and unsuccessful issues, report amortized setup/maintenance effort separately and in total-burden analysis, and separate queue/model/CI/human waits; known tokens, billed amounts and subscription quota remain distinct from unknown usage.
- [ ] **A5.** Missing records, delayed cohort completion, failed targets or a noncomparable baseline are reported as missing/failed/inconclusive rather than excluded or replaced; export reproducible metric inputs and computations without secret or identifying issue content.

**Effort**: 3 days

**Dependencies**: Task 3.10, Task 4.1, Task 4.2

**Blocks**: Task 4.5, Task 4.6

**Workstream**: internal validation

**PRD Reference**: §1.4; §7.1; §8.3

**Feature coverage**: F, 0, 9. **Requirement coverage**: GATE-P01, GATE-P02, MET04, MET05, MEASURE02.

**Verification**: Replay measurement fixtures including all outcomes and unknown values, verify reported medians/denominators against source records, and audit one-active-task traces and exact revision evidence.

**Implementation notes**: Estimate covers study preparation, instrumentation and analysis, not ten model runs or 30 calendar days of observation. Targets are proposed until Q9 adoption; achievement is never an implementation acceptance claim. Runtime stays one task and roles sequential.

<a id="task-4-4"></a>

### Task 4.4: Resolve pilot consent and verify external-workload trust admission

**Description**: Define the external-pilot admission gate before accepting participant data: resolve responsibilities and provider handling, collect appropriate consent through an approved process, and admit only workloads whose supported trust and publication boundaries are independently verified.

**Acceptance Criteria**:

- [ ] **A1.** Before any external participant information, reports, code or installation telemetry is collected, record applicable obligations, controller/processor responsibilities, model-provider data handling, minimization/retention/export/delete terms and an approved consent/admission process.
- [ ] **A2.** Opening collection for a participant requires recorded consent and accepted supported workload/trust classification; absent/revoked consent blocks new collection and aggregate export, with existing records handled according to the recorded procedure.
- [ ] **A3.** For external contributor code/tests, independent executable checks demonstrate enforced process/filesystem/network boundaries and absence of controller/production/unrestricted GitHub credentials; hostile fixtures attempting filesystem reads, outbound exfiltration and privileged writes are denied.
- [ ] **A4.** A missing or failed boundary denies the affected external workload and names the readiness blocker; a worktree, prompt rule or branch protection alone is never accepted as isolation proof.
- [ ] **A5.** Only agreed minimized aggregate fields enter exports, identifying content and secret canaries are absent, and preview admission verifies scoped credentials plus revision-bound smoke evidence without broadening v1.0 authority.

**Effort**: 2 days

**Dependencies**: Task 3.10, Task 4.2

**Blocks**: Task 4.5, Task 4.7

**Workstream**: pilot admission

**PRD Reference**: §5.2; §7.1; §8.3; §8.4

**Feature coverage**: F, 0, 9. **Requirement coverage**: NFR-S02, NFR-S06, GATE-P04, GATE-E02.

**Verification**: Use synthetic participant fixtures until admission is complete; validate denied pre-consent collection and revoke behavior, run hostile boundary probes and export canary tests, and independently review admission evidence.

**Implementation notes**: Internal no-specific-compliance commitment does not waive external assessment. Specialist/operator decisions may be external dependencies; unresolved responsibilities keep pilot collection blocked. Boundary evidence is workload-specific; do not broaden runtime support merely to recruit a cohort.

<a id="task-4-5"></a>

### Task 4.5: Observe three independent external installations and repeat use

**Description**: After v1.0, internal dogfood review and consent/admission gates, conduct the proposed independent onboarding and repeat-use study on the published support recipe without hidden author takeover.

**Acceptance Criteria**:

- [ ] **A1.** Three consented and admitted external maintainers follow the pinned install/first-run/removal walkthrough; record prerequisites, elapsed setup duration, blockers, author assistance and first-run outcome using the agreed minimized fields.
- [ ] **A2.** Report the proposed onboarding targets: three external maintainers complete installation and a first run within 60 days of pilot opening, with at least two completing setup in 45 minutes without author takeover; author takeover is explicitly recorded and cannot count toward the independence threshold.
- [ ] **A3.** Report the proposed repeat-use target: two external maintainers complete at least one run per week for four consecutive weeks within 90 days of pilot opening; retain failed-run reports and distinguish completed runs from attempts or download/star counts.
- [ ] **A4.** Nonconsent, unsupported workload, installation failure, dropout and missing weekly reports are blocked or labeled missing/inconclusive with denominators/time windows retained; no participant data collection bypasses Task 4.4 admission.
- [ ] **A5.** Separate active builder work from calendar observation/recruitment and participant/model/CI waits; retained evidence supports reproducible pass/fail/inconclusive classification without exposing identifying code/chat data.

**Effort**: 2 days

**Dependencies**: Task 4.2, Task 4.3, Task 4.4

**Blocks**: Task 4.6, Task 4.7

**Workstream**: external validation

**PRD Reference**: §1.4; §3.2 F10; §8.3

**Feature coverage**: F, 0, 9. **Requirement coverage**: GATE-P05, MET06, MET07.

**Verification**: Audit consent/admission ordering against collection timestamps, independently observe setup timing, replay weekly-report classifications and verify incomplete data cannot be counted as successful retention.

**Implementation notes**: Estimate covers protocol operation and evidence analysis; 60/90-day windows and four weekly observations are external elapsed dependencies rather than builder effort. Targets remain proposed subject to Q9 and evidence; no recruited participant is assumed.

<a id="task-4-6"></a>

### Task 4.6: Evaluate usefulness and decide continue, narrow or stop

**Description**: Evaluate measured internal and external evidence with the product owner and explicitly choose continue, narrow/consolidate into Hermes/IDD recipes, or stop. Prevent successful demonstrations from becoming unsupported expansion claims.

**Acceptance Criteria**:

- [ ] **A1.** Produce an evidence table for all proposed internal/external numerical targets, with source observations, exact windows, denominators, missing data and pass/fail/inconclusive labels; record Q9 adoption or adjustment without retroactively hiding baseline differences.
- [ ] **A2.** Compare intervention, setup and maintenance burden with the ten-issue baseline; quantify savings and uncertainty rather than equating generated code volume with value.
- [ ] **A3.** If repeated author intervention is required or integration maintenance erases savings, record a narrow/consolidate or stop decision with measured reasons and scoped follow-up; continue requires explicit owner decision against actual evidence.
- [ ] **A4.** Incomplete cohort/retention data or failed safety/admission evidence prevents a positive evidence claim or automatic expansion; record unresolved decisions and any required next observation instead of assuming target attainment.

**Effort**: 1 day

**Dependencies**: Task 4.3, Task 4.5

**Blocks**: Task 4.7, Task 5.1, Task 5.3

**Workstream**: product decision

**PRD Reference**: §1.4; §8.3; §9.1 Q9; §9.3

**Feature coverage**: F, 0, 9. **Requirement coverage**: GATE-P02, MET04, MET05, MET06, MET07.

**Verification**: Cross-check report cells to source metric records, test incomplete and unfavorable evidence scenarios, and verify the recorded decision does not silently add capability or waive release requirements.

**Implementation notes**: This task analyzes results, it does not guarantee favorable evidence. One active runtime task remains the support limit. Calendar observation may block evaluation despite small active effort.

<a id="task-4-7"></a>

### Task 4.7: Publish v1.1 evidence and supported onboarding recipes

**Description**: Prepare a conditional v1.1 release package containing F09/F10 acceptance evidence, measured failures and support/repair/removal documentation. Publish only when pilot, safety, provenance and owner decision gates permit it.

**Acceptance Criteria**:

- [ ] **A1.** The reviewable package includes reproducible F09 checkpoint/budget tests and F10 interrupted migration/conflict/rollback evidence, pinned supported versions/skills/dependencies, known failures and independent install/upgrade/removal recipes.
- [ ] **A2.** A release checklist confirms passed v1.0 gate, consent/trust admission, recorded pilot observations, explicit continue/narrow/stop decision and resolved Q8 license/distribution/support/notice decisions before any public release.
- [ ] **A3.** Consented aggregate results disclose proposed target status, actual pass/fail/inconclusive results and observation limits; no code, issue bodies, usernames, tokens or chat content is included.
- [ ] **A4.** If release conditions fail or the decision is narrow/stop, retain a reviewable draft or appropriately scoped recipe-only deliverable and record the withheld release reason; never mark missing evidence achieved or promise new concurrency/runtime/provider support.
- [ ] **A5.** A clean supported-version walkthrough reproduces installation, checkpoint steering, repair/rollback and removal using the package instructions, preserving user edits and leaving no orphaned execution authority.

**Effort**: 1 day

**Dependencies**: Task 4.1, Task 4.2, Task 4.4, Task 4.5, Task 4.6

**Blocks**: Task 5.1, Task 5.3

**Workstream**: release docs

**PRD Reference**: §3.2 F09/F10; §8.3; §9.1 Q8

**Feature coverage**: F, 0, 9. **Requirement coverage**: F09, F10, GATE-P03, GATE-P06.

**Verification**: Run one independent supported-version documentation walkthrough and audit release checklist/source evidence; use a failed-gate fixture to verify release remains withheld.

**Implementation notes**: Release is conditional, not assumed by task completion. This task creates a reviewable release result; public publication follows the session’s applicable authorization. Active effort excludes pilot waits.

## Sprint 5: Optional adapter admission and future design gates

**Phase**: Full Features

**Tasks**: 3

**Total effort**: 5 developer-days; no calendar duration promised.

**Workstreams**: admission (5.1); adapter (5.2); future design (5.3).

**Entry**: completed measured-pilot decision/evidence. **Conditional scope**: exactly one adopted F13 adapter if justified; F14 policy/design documentation only, never runtime delivery.

<a id="task-5-1"></a>

### Task 5.1: Assess demand and admit one optional additional harness only if justified

**Description**: After completed measured internal/external pilot and its explicit continue decision, assess whether one additional documented harness has enough demand and maintainable compatibility to justify F13. Produce a narrow operator admission decision; a completed assessment is not permission to implement until the operator explicitly adopts it (§8.4).

**Acceptance Criteria**:

- [ ] **A1.** A versioned assessment cites completed ten-issue/two-project sequential dogfood and three external installation/repeat-use measurements, operator-effort baseline/setup/maintenance cost and the actual continue/narrow/stop pilot decision; incomplete pilot, narrow/stop outcome or missing repeated-use evidence records blocked/deferred rather than admitting expansion.
- [ ] **A2.** Exactly one candidate harness/version is proposed with documented demand and incremental maintenance cost, host/model/skill provenance, capability and authority matrix covering substantive readiness/auth, start, liveness, structured results, independent role sessions and cancellation including descendants; resume/live steering are marked optional instead of presumed.
- [ ] **A3.** Candidate probes demonstrate current-generation authorization, credential separation, supported Hermes lifecycle integration and full existing preview/human-approved merge endpoint compatibility without a second scheduler. Unsupported primitives, isolation or cancellation capabilities reject admission with actionable evidence.
- [ ] **A4.** The operator explicitly records adopted candidate/version, support/trust recipe, bounded test scope, cost/capacity decision and acceptance gates before 5.2 may run; no decision or a rejected decision causes 5.2 to remain deferred with no adapter code execution.
- [ ] **A5.** Concurrency, cross-project isolation, maintenance cost and demand are reassessed using measured pilot evidence; the admitted scope retains one active task/project at a time, one additional adapter only and the selected preview provider, with no dates promised or broader harness/provider abstraction.

**Effort**: 1 day

**Dependencies**: Task 4.6, Task 4.7

**Blocks**: Task 5.2

**Workstream**: admission

**PRD Reference**: §3.1 F13; §6.5 runtime contract; §8.3 pilot gate; §8.4 expansion/admission/GATE-E04

**Feature coverage**: F13. **Requirement coverage**: F13, GATE-E04.

**Verification**: Review versioned decision/checklist evidence against PRD and completed pilot; adapter implementation additionally runs deterministic identity/policy unit checks plus the full selected-recipe integration/fault suite with fixture/version identity and authoritative read-back.

**Implementation notes**: Conditional Full Features planning task. Predecessor is the actual completed measured pilot continue/narrow/stop gate, to be resolved against Sprint 4 output. An assessment may conclude deferred; it must not claim F13 delivered or imply new support merely because the assessment is finished.

<a id="task-5-2"></a>

### Task 5.2: Implement and verify the explicitly admitted single harness adapter

**Description**: Only after the operator adopts 5.1, implement exactly one additional harness adapter through the same Hermes-owned lifecycle and deterministic credential/evidence boundaries, then publish support limited to its tested recipe (F13). This optional expansion never weakens the MVP endpoint or increases runtime concurrency.

**Acceptance Criteria**:

- [ ] **A1.** The explicitly admitted candidate/version is the only new adapter; missing/rejected/revoked admission, missing supported hooks or an effort estimate beyond three days blocks execution pending scoped replan rather than silently broadening this task.
- [ ] **A2.** Selected recipe tests exercise readiness with actual model/auth/capability checks, start, heartbeat/liveness, structured result collection and cancellation including descendants; roles, runtime, model and pinned skill versions remain distinct and separate implementation/reviewer sessions execute sequentially.
- [ ] **A3.** A successful authorized issue completes current-head independent review/required checks, immutable revision-bound preview and smoke, durable single-use human approval, conditional expected-head merge and authoritative merge read-back using the existing selected provider/method; provider fallback, implicit/autonomous merge and production authority remain disabled.
- [ ] **A4.** Full lifecycle/fault regression runs every §8.2 scenario three times with deterministic assertions and authoritative remote read-back where applicable, including restart, repeated/reordered intake, crash after publication, expired claims, cancel with late descendant, revoked authorization, current-SHA changes and replayed/stale approval; retain version/fixture evidence and fail support admission on any safety failure.
- [ ] **A5.** Test processes and workers have no production secrets, controller tokens or unrestricted GitHub mutation credentials; every permitted write traverses the scoped broker with repository/action/current-generation/authorization checks. Prompt instructions, worktrees and branch protections alone cannot substitute for the documented enforceable trust boundary.
- [ ] **A6.** Default limits remain one active task, initial plus one fix, 60 cumulative active-worker minutes excluding CI wait and 24-hour wall time; exhausted budgets park, unknown usage remains unknown and no provider/model/subdelegation fallback or second scheduler executes.
- [ ] **A7.** Unreachable/expired worker authority fences before replacement; descendant exit is confirmed within 30 seconds of cancellation or quarantine is surfaced without overlapping replacement authority. Existing human commits and uncertain remote results are preserved/reconciled, with no blind repeat mutation.
- [ ] **A8.** The published tested recipe documents supported host/harness/model/skill versions, credentials/permissions, trusted-maintainer/project-code scope, limits, known failures and recovery; unsupported external contributor workloads remain blocked pending enforceable process/filesystem/network boundaries and separate input-class adoption.

**Effort**: 3 days

**Dependencies**: Task 5.1

**Blocks**: None

**Workstream**: adapter

**PRD Reference**: §3.1 F13; §5.2 isolation/credential boundaries; §6.5 INTEG01; §8.2 fault suite; §8.4 capability-specific gates

**Feature coverage**: F13. **Requirement coverage**: F13, INTEG01, NFR-S02, NFR-S03.

**Verification**: Review versioned decision/checklist evidence against PRD and completed pilot; adapter implementation additionally runs deterministic identity/policy unit checks plus the full selected-recipe integration/fault suite with fixture/version identity and authoritative read-back.

**Implementation notes**: Conditional Full Features implementation, not a committed release date. Existing MVP must remain accepted before expansion; retain one path selected per task and one active task across projects. Three-day estimate assumes supported adapter hooks; otherwise split/replan before execution and never waive fault/evidence gates.

<a id="task-5-3"></a>

### Task 5.3: Document separately adopted future policy and design gates

**Description**: Document future admission and policy/design checklists for F14 only. Production promotion, autonomous merge and parallel projects stay deferred pending separate future PRD, explicit owner adoption and independent evidence; this task creates no runtime implementation or policy activation (§8.4).

**Acceptance Criteria**:

- [ ] **A1.** A reviewable future-scope document identifies F14 as deferred with separate future PRD/owner adoption and no date commitment; the current preview plus revision-bound human-approved merge endpoint and single active lane remain the prerequisites, and no production/autonomous-merge/concurrent-project code or enabled configuration is introduced.
- [ ] **A2.** Autonomous-merge admission checklist requires a separately explicitly adopted policy and reproducible evidence preserving current revision/review/check/preview contract, branch protections, deterministic authorization and single-owner enforcement; missing adoption or weakened prerequisites records no-go.
- [ ] **A3.** Additional preview-provider or external-contributor input-class checklist requires independent scoped-credential/trust-boundary and revision-bound smoke verification; unsupported process/filesystem/network isolation or privileged worker/test credentials records no-go rather than allowing the workload.
- [ ] **A4.** Production design checklist requires immutable artifact identity, scoped secrets, explicit approval, health checks and project-specific recovery; irreversible migration procedures require separately defined steps/owners and cannot be assumed recoverable by generic rollback.
- [ ] **A5.** Concurrency/parallel-project checklist requires measured demand, maintenance-cost/capacity review and enforceable cross-project isolation/single-owner analysis; no checklist result itself increases active-task limits or enables multiple concurrent projects.
- [ ] **A6.** Each future capability has a named decision owner, required evidence/fault scenarios and explicit go/no-go criteria in its own future PRD; unresolved Q8 distribution/license/support and Q9 targets are retained where applicable, and design completion is distinguished from delivery/adoption.

**Effort**: 1 day

**Dependencies**: Task 4.6, Task 4.7

**Blocks**: None

**Workstream**: future design

**PRD Reference**: §3.1 F14 deferred scope; §8.4 GATE-E01–GATE-E04; §9.1 future decisions

**Feature coverage**: F14. **Requirement coverage**: F14, GATE-E01, GATE-E02, GATE-E03, GATE-E04.

**Verification**: Review versioned decision/checklist evidence against PRD and completed pilot; adapter implementation additionally runs deterministic identity/policy unit checks plus the full selected-recipe integration/fault suite with fixture/version identity and authoritative read-back.

**Implementation notes**: Policy/design gates only. May run independently of the conditional adapter work after the completed pilot decision. This task does not implement F14, imply adoption or change the current registration/runtime policy. A separate future PRD/adoption is mandatory before any such implementation.

## Requirement coverage map

All 148 extracted PRD requirement IDs are mapped below. Feature-level tags do not replace acceptance coverage: the following table maps all 54 PRD §3.2 feature bullets to numbered task criteria. F13 is conditional admission/implementation; F14 coverage means deferred design gates, not delivered behavior. Implementation/release results remain outstanding.

### Feature acceptance coverage

| PRD feature / bullet | Required behavior from §3.2 | Task criterion ownership |
|---|---|---|
| F01 / 1 | Given an existing repository with CI, `.gitissue.yml`, and uncommitted developer work / When setup inspects it / Then it proposes only factory-owned additions and explicit integration edits without stashing, resetting, or modifying unrelated work. | Task [2.2](#task-2-2) A1, A2 |
| F01 / 2 | Given the approved setup plan / When installation runs twice / Then the second run creates no duplicate registration, webhook, configuration block, or dependency entry and reports an empty effective diff. | Task [2.2](#task-2-2) A2 |
| F01 / 3 | Given a missing executable, unavailable configured model, failed authentication, conflicting automation owner, or unsupported Hermes version / When readiness runs / Then the specific prerequisite is blocked and no issue is dispatched. | Task [2.2](#task-2-2) A3, A4 |
| F01 / 4 | The report records supported host/runtime versions, permissions, verification commands, skill revisions and cancellation capability. Presence on PATH alone is insufficient. Sensitive or remote setup changes require an explicit, reviewable operator plan; tokens are never committed. | Task [2.2](#task-2-2) A1, A5 |
| F02 / 1 | Given a valid signed event for the registered repository and an issue opted in by an authorized actor / When intake accepts it / Then durable logical work identity is established before it is reported as safely queued. | Task [2.3](#task-2-3) A1 |
| F02 / 2 | Given the same event delivered 100 times, including after restart / When webhook intake and reconciliation overlap / Then they converge on one logical task and at most one active attempt and one linked PR. | Task [2.3](#task-2-3) A2, A5 |
| F02 / 3 | Given an invalid signature, wrong repository, unauthorized label action, revoked opt-in, or an instruction embedded in issue prose / When it reaches intake / Then no new execution authority is granted and the reason is recorded without logging secrets. | Task [2.3](#task-2-3) A3, A4 |
| F02 / 4 | Persist delivery IDs separately from logical work identity. Edits update pending context or mark the active attempt for re-evaluation; they do not silently spawn parallel work. Factory notifications must not create intake loops. | Task [2.3](#task-2-3) A1, A2, A4 |
| F03 / 1 | Given a ready authorized task and a free execution lane / When the controller dispatches it / Then one worker receives an isolated workspace, repository/issue identity, acceptance criteria, pinned skills, model selection, attempt identity and resource limits. | Task [2.4](#task-2-4) A1 |
| F03 / 2 | Given another task arrives during an active task / When scheduling evaluates it / Then it remains durably queued with a visible reason; implementation and review execute sequentially. | Task [2.4](#task-2-4) A1, A2 |
| F03 / 3 | Given the default attempt or time budget is exhausted / When the next action is evaluated / Then work parks with an explanation rather than silently extending its limits or changing provider. | Task [2.4](#task-2-4) A4 |
| F03 / 4 | IDD procedures must run without an independent backlog loop or an implicit auto-merge policy. A missing tool, unusable model or unsupported subdelegation reports a blocker. Default limits: one active task, two implementation attempts total (initial plus one fix attempt), and 60 minutes of active worker execution per task across roles. Operator-requested retries are new audited generations with fresh authorization; they do not hide previous usage. | Task [2.4](#task-2-4) A2, A3, A4, A5 |
| F04 / 1 | Given an implementation result at SHA A / When review starts / Then a separate reviewer session inspects the diff against the issue criteria and records its own verdict, findings and SHA A. | Task [2.6](#task-2-6) A1 |
| F04 / 2 | Given positive review, successful required checks and a linked open PR at SHA A / When the controller evaluates completion / Then it reads GitHub, verifies the head still equals A, and records the PR, head/base SHA, review, check identities and observation time. | Task [2.6](#task-2-6) A2 |
| F04 / 3 | Given the head changes to SHA B, a required check fails/is missing, or GitHub is unavailable / When completion is evaluated / Then the result is stale, failed or unknown respectively and cannot be called verified. | Task [2.6](#task-2-6) A3 |
| F04 / 4 | Given a reviewer requests changes / When the task has budget remaining / Then one bounded fix attempt is permitted and all revision-dependent review/check evidence is rebuilt; otherwise the task parks. | Task [2.6](#task-2-6) A5 |
| F04 / 5 | Define required checks during setup from repository protection plus explicit project acceptance commands. A repository with no protected checks still needs a nonempty operator-approved verification contract. Skipped/neutral checks count only if the contract explicitly permits that conclusion. A green exit code from the worker is never the completion contract. | Task [2.6](#task-2-6) A4 |
| F04 / 6 | A verified PR is a point-in-time observation, not a merge guarantee. Later observed changes mark evidence stale; status always displays the verified SHA and observation time. Human-authored commits are never overwritten to restore an old result. | Task [2.6](#task-2-6) A3, A6 |
| F05 / 1 | Given a message from an allowed numeric Telegram actor in the configured conversation / When a recognized status or control action is submitted / Then the system persists its actor, repository, task/generation, action and result and returns an attributable acknowledgement. | Task [2.7](#task-2-7) A1 |
| F05 / 2 | Given a pause request / When the current stage finishes / Then the next stage does not start until an authorized resume; status distinguishes pause requested from paused. | Task [2.7](#task-2-7) A2 |
| F05 / 3 | Given a cancellation request / When it is accepted / Then the active generation is fenced before process termination begins, new effects are denied, and late results cannot advance state. | Task [2.7](#task-2-7) A3, A4 |
| F05 / 4 | Given a forged operator, ambiguous target, duplicate command or unsupported request / When it is processed / Then the controller rejects or requests clarification without broadening permissions or repeating the action. | Task [2.7](#task-2-7) A5 |
| F05 / 5 | Structured status/pause/resume/cancel commands are the required baseline; their exact syntax remains a design decision. Natural-language interpretation may propose the same validated actions, but prose cannot bypass policy. Cancellation acknowledges fencing separately from confirmed process exit. Effects already committed before cancellation are reported with links; they are not silently undone. | Task [2.7](#task-2-7) A1, A4, A5, A6 |
| F06 / 1 | Given the host stops after durable acceptance but before dispatch / When Hermes restarts / Then the task resumes or parks through supported lifecycle ownership without depending on Telegram history. | Task [2.8](#task-2-8) A1 |
| F06 / 2 | Given a crash after PR creation but before its response is recorded / When recovery reconciles / Then it locates the existing PR through the recorded work identity before attempting any creation and parks on ambiguous remote state. | Task [2.8](#task-2-8) A2 |
| F06 / 3 | Given a lease expires or a worker becomes unreachable / When recovery runs / Then the old generation is fenced before replacement work is eligible; late output cannot be accepted. | Task [2.8](#task-2-8) A3 |
| F06 / 4 | Given GitHub returns a rate limit or network error / When reconciliation retries / Then it uses bounded backoff, honors available retry guidance, reports stale/unknown state and performs no blind repeat mutation. | Task [2.8](#task-2-8) A5 |
| F06 / 5 | Periodic reconciliation recovers missed events and revoked permissions using GitHub state. An unexpected terminal PR state, human branch movement, or irreconcilable identity requires a visible parked state rather than a new PR. | Task [2.8](#task-2-8) A4, A6 |
| F07 / 1 | Given a task reaches a terminal or waiting state / When status is requested / Then it includes task/attempt identity, outcome, current blocker, relevant revision, PR/check references, elapsed worker time and remaining limits. | Task [2.9](#task-2-9) A1 |
| F07 / 2 | Given the runtime exposes token or monetary usage / When the result is recorded / Then measured values retain provider/unit attribution; unavailable usage is `unknown`, never zero or an invented currency estimate. | Task [2.9](#task-2-9) A3 |
| F07 / 3 | Given diagnostic export includes seeded credentials or private issue content / When it is generated / Then credential canaries are absent and content bodies are excluded by default, with explicit operator selection for any expanded export. | Task [2.9](#task-2-9) A5 |
| F07 / 4 | Persist an append-only event trail within the supported durability design. Correction events supersede old evidence without rewriting its history. Notifications contain short summaries and authorized links, not raw code, tokens or full logs. | Task [2.9](#task-2-9) A1, A5, A7 |
| F08 / 1 | Given a completed installation and unrelated repository content / When the operator reviews and applies uninstall / Then only recorded factory-owned integrations/files are eligible for removal and unrelated content remains byte-for-byte unchanged. | Task [3.5](#task-3-5) A1 |
| F08 / 2 | Given a factory-created file has since been edited by the user / When uninstall runs / Then it preserves the file or requests a specific reviewed resolution instead of deleting those edits. | Task [3.5](#task-3-5) A2 |
| F08 / 3 | Given an active or unconfirmed worker / When uninstall begins / Then intake stops, the worker is fenced and termination is resolved or surfaced before credentials/state are removed; active worktrees are not forcibly deleted. | Task [3.5](#task-3-5) A3 |
| F08 / 4 | Retain/export or delete task history through an explicit choice. Removing a registration must not remove shared Hermes infrastructure, IDD configuration, shared skills or tokens used elsewhere. | Task [3.5](#task-3-5) A4 |
| F09 / 1 | Given an authorized scope change for an active task / When steering is accepted / Then it is stored with the target generation, the old attempt is fenced at a supported boundary, and any replacement receives the revised criteria and remaining explicit budget. | Task [4.1](#task-4-1) A1, A2, A3 |
| F09 / 2 | Given the configured runtime lacks live steering / When the user requests it / Then the system explains checkpoint/restart behavior without claiming live injection or enabling an unsupported harness. | Task [4.1](#task-4-1) A4, A5 |
| F10 / 1 | Given a supported installed version / When upgrade is planned / Then the operator can inspect compatibility checks, changed owned files and rollback instructions before application. | Task [4.2](#task-4-2) A1 |
| F10 / 2 | Given an interrupted migration or a user-edited configuration / When repair runs / Then it either restores a validated checkpoint or parks with a specific conflict, preserves user edits and does not dispatch from a partially migrated configuration. | Task [4.2](#task-4-2) A3, A4, A5 |
| F10 / 3 | Publish a reproducible support recipe, pinned dependency/skill references, known limits and an independent-install walkthrough before the external pilot. | Task [4.2](#task-4-2) A6 |
| F11 / 1 | Given an independently reviewed PR with passing required checks / When preview deployment runs / Then the selected provider returns a deployment identity and URL attributable to the recorded head/base and immutable source or artifact identity; worker self-report alone is insufficient. | Task [3.1](#task-3-1) A1 |
| F11 / 2 | Given that deployment / When the configured smoke checks pass / Then the controller records the checks, observation time and deployment identity and makes that evidence available in the approval request. | Task [3.1](#task-3-1) A2 |
| F11 / 3 | Given a failed smoke check, unavailable provider, mismatched SHA, expired deployment or unknown artifact identity / When preview verification is evaluated / Then merge stays blocked and no old preview is accepted as evidence for the new change. | Task [3.1](#task-3-1) A3 |
| F11 / 4 | Given a changed head/base or canceled task / When reconciliation runs / Then superseded preview evidence is invalidated and factory-owned resources are eligible for bounded cleanup; late deployment callbacks cannot revive the attempt. | Task [3.1](#task-3-1) A4, A5 |
| F11 / 5 | The initial project must have a meaningful preview. A “not applicable” preview is not an acceptable substitute for the user-selected MVP endpoint. Select one provider and non-production environment during the spike; agree access visibility, synthetic test data and a smoke command/expected result. Default: one active preview per task, 24-hour maximum resource lifetime, and cleanup within 60 minutes of confirmed task termination when the provider is reachable. Provider outages create a visible cleanup backlog rather than a false removal claim. Preview credentials are confined to the scoped deployment boundary, never the coding worker. | Task [3.1](#task-3-1) A1, A2, A4, A5, A6 |
| F12 / 1 | Given current review, CI and preview evidence / When approval is requested / Then Telegram presents repository/PR, head/base SHA, preview URL and smoke result, merge method and expiry and creates a durable one-use request bound to that action, revision, evidence set and policy digest. | Task [3.2](#task-3-2) A1 |
| F12 / 2 | Given an authorized human approval / When its decision is accepted / Then actor, action, target, issuance and expiry are durably recorded and one merge intent can consume it; repeated buttons/messages cannot authorize additional effects. | Task [3.2](#task-3-2) A1, A2, A4 |
| F12 / 3 | Given changed head/base, changed policy, new failed/missing checks, stale/unhealthy preview, expired approval, revoked operator or a canceled/paused task / When merge eligibility is checked / Then merge is denied and affected approval is invalidated; resume alone does not revive it. | Task [3.2](#task-3-2) A5 |
| F12 / 4 | Given a valid unexpired approval and unchanged evidence / When the single merge owner executes / Then it rechecks GitHub head/base, PR open/non-draft status, mergeability, all required protections/checks and preview health, issues a merge conditional on the expected head, and reports merged only after GitHub read-back records the actual merge commit SHA. | Task [3.3](#task-3-3) A1, A3, A6 |
| F12 / 5 | Given an API timeout after a merge request / When recovery runs / Then it reconciles PR/merge state before any retry and parks if ambiguous; it never infers success from an approval or an HTTP request being sent. | Task [3.3](#task-3-3) A6 |
| F12 / 6 | Approval expires after 60 minutes by default; preview smoke evidence must be at most 10 minutes old at merge. Reverification produces a fresh evidence set and requires a fresh approval. Failed or expired requests remain auditable. Rejected approval leaves the task blocked for the human decision, with no automatic repeated approval prompt. | Task [3.2](#task-3-2) A3, A5; Task [3.3](#task-3-3) A2 |
| F12 / 7 | Select one GitHub-supported merge method in setup. Require repository protection/rules that enforce the verification contract against an up-to-date base (or equivalent atomic queue enforcement proven in the spike), and use credentials that cannot bypass those rules. If expected-head checks plus enforced protections cannot close the base/check race, mark merge unsupported and fail the spike; read-then-write alone is insufficient. Review/preview must cover the candidate compatible with that current base. Do not enable GitHub auto-merge as a shortcut that can outlive approval expiry. | Task [3.3](#task-3-3) A3, A4 |
| F12 / 8 | Suppress competing IDD/other factory merge automation or refuse readiness until one automated owner is established. A human merge through GitHub remains observable external activity, recorded with its actual actor; do not misattribute it to a factory approval. Workflow cancellation cannot undo a merge accepted before its fence committed. Destructive reversals require a separate human decision. | Task [3.3](#task-3-3) A5, A7 |
| F13 | §3.1/§8.4: one additional harness only after completed pilot and explicit capability-specific admission | 5.1 admission; 5.2 implementation/fault support gates |
| F14 | §3.1/§8.4: deferred production, autonomous merge and parallel projects; separate future PRD/policy/evidence | 5.3 design/admission checklists only |

### Feature, non-functional, architecture, configuration, monitoring and release requirements

| Requirement | PRD reference | Summary | Owning tasks |
|---|---|---|---|
| F01 | §3.1; §3.2 F01 | Additive setup and readiness | [1.1](#task-1-1), [2.2](#task-2-2), [3.10](#task-3-10) |
| F02 | §3.1; §3.2 F02 | Authorized durable intake | [1.3](#task-1-3), [2.3](#task-2-3), [3.10](#task-3-10) |
| F03 | §3.1; §3.2 F03 | One bounded IDD execution lane | [1.2](#task-1-2), [2.4](#task-2-4), [3.10](#task-3-10) |
| F04 | §3.1; §3.2 F04 | Independent review and current-head verification | [1.3](#task-1-3), [2.6](#task-2-6), [3.10](#task-3-10) |
| F05 | §3.1; §3.2 F05 | Telegram status, pause and cancellation | [1.2](#task-1-2), [2.7](#task-2-7), [3.10](#task-3-10) |
| F06 | §3.1; §3.2 F06 | Restart recovery and reconciliation | [1.4](#task-1-4), [2.8](#task-2-8), [3.10](#task-3-10) |
| F07 | §3.1; §3.2 F07 | Evidence, usage and diagnostic records | [2.9](#task-2-9), [3.4](#task-3-4), [3.10](#task-3-10) |
| F08 | §3.1; §3.2 F08 | Safe removal and configuration ownership | [3.5](#task-3-5), [3.10](#task-3-10) |
| F09 | §3.1; §3.2 F09 | Checkpointed scope steering | [4.1](#task-4-1), [4.7](#task-4-7) |
| F10 | §3.1; §3.2 F10 | Versioned upgrade/repair and pilot recipes | [4.2](#task-4-2), [4.7](#task-4-7) |
| F11 | §3.1; §3.2 F11 | Revision-bound preview verification | [1.3](#task-1-3), [3.1](#task-3-1), [3.10](#task-3-10) |
| F12 | §3.1; §3.2 F12 | Durable human approval and guarded merge | [1.3](#task-1-3), [3.2](#task-3-2), [3.3](#task-3-3), [3.10](#task-3-10) |
| F13 | §3.1; §8.4 | One additional supported harness adapter | [5.1](#task-5-1), [5.2](#task-5-2) |
| F14 | §3.1; §8.4 | Production promotion, autonomous merge and parallel projects | [5.3](#task-5-3) |
| NFR-P01 | §5.1 | Durable intake response | [2.3](#task-2-3), [3.8](#task-3-8) |
| NFR-P02 | §5.1 | Duplicate burst | [2.3](#task-2-3), [3.8](#task-3-8) |
| NFR-P03 | §5.1 | Local status | [3.4](#task-3-4), [3.8](#task-3-8) |
| NFR-P04 | §5.1 | Control action | [2.7](#task-2-7), [3.8](#task-3-8) |
| NFR-P05 | §5.1 | Cancellation termination | [2.7](#task-2-7), [3.8](#task-3-8) |
| NFR-P06 | §5.1 | Reconciliation | [2.8](#task-2-8), [3.8](#task-3-8) |
| NFR-P07 | §5.1 | Restart recovery | [2.8](#task-2-8), [3.8](#task-3-8) |
| NFR-P08 | §5.1 | Idle overhead | [2.4](#task-2-4), [3.8](#task-3-8) |
| NFR-P09 | §5.1 | Persistence | [2.3](#task-2-3), [3.8](#task-3-8) |
| NFR-PC01 | §5.1 | Proposed local targets assume one registered repository, one active task, up to 100 pending tasks, and 10,000 retained event records. Measure on the documented pilot host with working dependencies; report GitHub/model/Telegram latency separately. Fault behavior is tested independently of these timing targets. | [3.8](#task-3-8) |
| NFR-PC02 | §5.1 | No always-on service SLA is promised for a developer-owned host. Offline status, process liveness and last successful reconciliation must be observable. CI waiting does not consume the 60-minute active-worker budget; default total task wall time is 24 hours, after which work parks. Retry backoff has a 5-minute nominal cap unless upstream requires a longer delay. | [2.4](#task-2-4), [3.8](#task-3-8) |
| NFR-S01 | §5.2 | Authorization is deterministic and scoped to registered repositories, allowlisted operators and permitted actions. Validate webhook signatures using the configured secret; distinguish payload authenticity from untrusted issue content. Models cannot grant permissions, change policy, or reinterpret a canceled generation as active. | [1.2](#task-1-2), [2.5](#task-2-5) |
| NFR-S02 | §5.2 | Use isolated workspaces and a documented execution trust boundary. A worktree separates files but is not a sandbox. The internal MVP accepts only issues explicitly selected by the trusted maintainer on trusted project code; external contributor code and tests must not receive privileged credentials. Before expanding to that input class, demonstrate enforceable process/filesystem/network boundaries. Unsupported isolation is a readiness blocker for the affected workload. | [1.2](#task-1-2), [4.4](#task-4-4), [5.2](#task-5-2) |
| NFR-S03 | §5.2 | Coding and test processes receive no production secrets, controller tokens or unrestricted GitHub mutation credentials. PR/branch publication must cross a scoped, deterministic boundary that checks current generation and authorization. If the supported runtime cannot enforce that boundary, the integration spike fails; prompt instructions alone cannot establish cancellation safety. Branch protection is not a substitute for guarding every permitted write. | [1.2](#task-1-2), [2.5](#task-2-5), [5.2](#task-5-2) |
| NFR-S04 | §5.2 | Keep secrets outside the manifest in the host's configured secret mechanism with least-privilege file access. Verify service transport security, redact known credentials before logs/Telegram, and use secret canaries in export/notification tests. Recheck authorization before dispatch and privileged mutations; revocation stops future authority and is reconciled visibly. | [1.2](#task-1-2), [3.4](#task-3-4) |
| NFR-S05 | §5.2 | Default proposed retention is 7 days for worker logs and 30 days after task completion for detailed audit metadata, configurable before pilot use. Cleanup must preserve active-task records and enough work/remote identity to prevent old webhook replay from recreating work while the registration exists. Keep minimal identity tombstones until explicit registration removal. Export/delete behavior must explain this distinction. No centralized telemetry is enabled by default. | [3.4](#task-3-4) |
| NFR-S06 | §5.2 | **Confirmed internal scope (2026-10-04): no specific compliance commitment for the internal MVP.** Implement the privacy controls above without claiming GDPR, HIPAA, SOC 2 or other certification/compliance. Reassess applicable obligations, controller/processor responsibilities, model-provider data handling and pilot consent before accepting external participant data. This product-scope choice is not a determination that no law applies. | [4.4](#task-4-4) |
| NFR-C01 | §5.3 | Support exactly one host/runtime/version combination at MVP release, selected and recorded during the spike. The current developer host is a candidate, not a tested support promise. Hermes/IDD versions, chosen model access, GitHub repository configuration and Telegram transport must pass the published readiness recipe. Additional hosts, runtimes and model IDs are unsupported until tested. | [1.1](#task-1-1), [3.9](#task-3-9) |
| NFR-C02 | §5.3 | There is no separate web or mobile frontend in the MVP. Telegram responses and local reports use concise text, stable identifiers, links, and explicit state words; no outcome may depend only on color or emoji. Every action available via a button must have a documented text alternative. Scope and target must be clear to screen-reader users; underlying Telegram-client accessibility is outside the kit's control. | [2.7](#task-2-7), [3.4](#task-3-4) |
| ARCH-O01 | §6.1 | Issues, commits, PR head/base, checks | [1.1](#task-1-1) |
| ARCH-O02 | §6.1 | Tasks, claims, attempts, generation fencing, transitions | [1.1](#task-1-1) |
| ARCH-O03 | §6.1 | Factory routing, authorization and evidence rules | [1.1](#task-1-1) |
| ARCH-O04 | §6.1 | Roles and development method | [1.1](#task-1-1) |
| ARCH-O05 | §6.1 | Project configuration | [1.1](#task-1-1) |
| ARCH-O06 | §6.1 | Commands and notification delivery | [1.1](#task-1-1), [2.7](#task-2-7) |
| ARCH-O07 | §6.1 | Preview evidence | [1.1](#task-1-1), [3.1](#task-3-1) |
| ARCH-O08 | §6.1 | Merge authority | [1.1](#task-1-1), [3.3](#task-3-3) |
| SPEC01 | §6.2 | **Frontend:** existing Telegram interface plus local setup/readiness output; no new dashboard. | [1.1](#task-1-1) |
| SPEC02 | §6.2 | **Backend:** standalone supported Hermes integration/plugin plus setup skill. Implementation language and package structure are TBD pending extension compatibility; the sources do not select a language. | [1.1](#task-1-1) |
| SPEC03 | §6.2 | **Storage:** reuse Hermes task durability. The physical location/schema of a required persistent delivery ledger or notification outbox is TBD in the spike. Any auxiliary metadata must reference Hermes task IDs and cannot independently dispatch work. | [1.1](#task-1-1) |
| SPEC04 | §6.2 | **Infrastructure:** local operator-owned host with existing Hermes/Telegram setup and GitHub CI. Specify restart supervision, inbound webhook transport, credential storage and backup/restore in the technical architecture after feasibility is established. One preview provider must be selected during the spike; production hosting is out of scope. | [1.1](#task-1-1), [3.9](#task-3-9) |
| SPEC05 | §6.2 | **Distribution:** versioned integration with compatible dependency pins and a setup skill, provisionally named `factory-setup`. No install command is advertised until implemented and tested. | [1.1](#task-1-1), [3.9](#task-3-9) |
| SPEC06 | §6.2 | **CI:** the kit tests its own lifecycle/identity/security contracts in its repository. Target-project tests and existing required checks remain authoritative; setup may propose explicit integration additions but cannot silently replace CI. | [1.1](#task-1-1), [3.9](#task-3-9) |
| CFG01 | §6.3 | Identity | [2.1](#task-2-1) |
| CFG02 | §6.3 | Authorization | [2.1](#task-2-1) |
| CFG03 | §6.3 | Runtime and roles | [2.1](#task-2-1) |
| CFG04 | §6.3 | Skills | [2.1](#task-2-1) |
| CFG05 | §6.3 | Verification | [2.1](#task-2-1) |
| CFG06 | §6.3 | Limits | [2.1](#task-2-1) |
| CFG07 | §6.3 | Evidence and retention | [2.1](#task-2-1) |
| CFG08 | §6.3 | Secrets | [2.1](#task-2-1) |
| CFG09 | §6.3 | Endpoint policy | [2.1](#task-2-1) |
| CFG-C01 | §6.3 | The proposed filename `.factory-kit.yml` is not a shipped interface. The TAD must finalize schema, validation, migration and precedence. Factory settings belong here; existing IDD settings stay in `.gitissue.yml` with explicit mapping/conflict detection. | [2.1](#task-2-1) |
| CFG-C02 | §6.3 | Effective configuration is validated, versioned, and attached by digest to each attempt. A policy change cannot silently expand an active attempt; affected work parks or restarts under an explicitly authorized new generation. | [2.1](#task-2-1) |
| REC01 | §6.4 | Registration | [2.1](#task-2-1) |
| REC02 | §6.4 | Accepted work | [2.3](#task-2-3) |
| REC03 | §6.4 | Attempt | [2.4](#task-2-4) |
| REC04 | §6.4 | Evidence | [2.6](#task-2-6) |
| REC05 | §6.4 | Control | [2.7](#task-2-7) |
| REC06 | §6.4 | Publication intent | [2.5](#task-2-5) |
| REC07 | §6.4 | Preview evidence | [3.1](#task-3-1) |
| REC08 | §6.4 | Approval | [3.2](#task-3-2) |
| REC09 | §6.4 | Merge outcome | [3.3](#task-3-3) |
| STATE01 | §6.4 | Records below define required information; reuse upstream fields rather than creating parallel tables by default. | [1.1](#task-1-1) |
| STATE02 | §6.4 | Logical states: received, queued, needs-clarification, implementing, reviewing, verifying, verified, previewing, awaiting-approval, merging, merged, paused, canceled, blocked, failed and quarantined. Map them to supported Hermes states/metadata during the spike rather than inventing a second transition engine. Paused retains its next eligible stage; canceled is terminal for that generation. Quarantined means process authority or remote state is uncertain and no automatic redispatch is allowed. | [1.1](#task-1-1) |
| STATE03 | §6.4 | The verified state is an intermediate snapshot; successful MVP delivery ends at merged. Awaiting-approval is durable across host/chat restarts. An expired or rejected approval never schedules merge. A merge already accepted remotely before cancellation is recorded as merged with a cancellation-race explanation; cancellation cannot undo a committed remote action. | [1.3](#task-1-3), [3.2](#task-3-2) |
| STATE04 | §6.4 | Candidate results can request a transition but cannot commit it. Atomic claim/fence checks and read-back of remote state determine acceptance. Exactly-once network delivery is not assumed: persistent identities, serialized effects and reconciliation must produce the one-task/one-PR behavior. Ambiguity parks work rather than allowing a blind repeat. | [1.4](#task-1-4), [2.8](#task-2-8) |
| INTEG01 | §6.5 | Required runtime operations are readiness, start, liveness, structured result collection and cancellation including descendants. Resume/live steering are optional capabilities. Separate role, runtime, model and skill selection; the MVP uses one runtime with independent implementation/review sessions. Evaluate a documented supported alternative runtime only if the native path cannot meet the spike, and support one path initially. | [1.1](#task-1-1), [5.2](#task-5-2) |
| INTEG02 | §6.5 | The GitHub credential choice (App or scoped existing credential) remains TBD. Selection must demonstrate repository scope and a publication broker/boundary outside untrusted worker/test processes. Telegram commands must resolve to typed actions before authorization. Dependency versions and skill provenance are recorded; trust and licensing of individual bundled components require review before distribution. | [1.1](#task-1-1) |
| EVT01 | §7.1 | `setup_checked` | [2.2](#task-2-2) |
| EVT02 | §7.1 | `work_accepted` / `work_rejected` | [2.3](#task-2-3) |
| EVT03 | §7.1 | `attempt_started` / `attempt_finished` | [2.4](#task-2-4), [2.9](#task-2-9) |
| EVT04 | §7.1 | `evidence_checked` | [2.6](#task-2-6), [2.9](#task-2-9) |
| EVT05 | §7.1 | `control_recorded` | [2.7](#task-2-7), [2.9](#task-2-9) |
| EVT06 | §7.1 | `recovery_completed` | [2.8](#task-2-8), [2.9](#task-2-9) |
| EVT07 | §7.1 | `notification_delivered` / `notification_failed` | [3.4](#task-3-4) |
| EVT08 | §7.1 | `operator_effort_recorded` | [2.9](#task-2-9) |
| EVT09 | §7.1 | `preview_verified` / `preview_failed` | [3.1](#task-3-1) |
| EVT10 | §7.1 | `approval_decided` / `approval_invalidated` | [3.2](#task-3-2) |
| EVT11 | §7.1 | `merge_observed` | [3.3](#task-3-3) |
| MEASURE01 | §7.1 | Measure workflow outcomes and operator effort, not generated code volume. Local records are the default; exporting aggregate pilot results requires participant agreement. Do not put issue bodies, code, usernames, tokens or chat text into aggregate event properties. | [2.9](#task-2-9) |
| MEASURE02 | §7.1 | Use these records to calculate §1.4 metrics. Separate queue, model execution, CI wait and human wait times. Report completed, blocked, failed and canceled counts alongside completion rates. Known token usage, actual billed amounts and subscription quota indicators are separate fields; none substitutes for the others. | [2.9](#task-2-9), [4.3](#task-4-3) |
| VIEW01 | §7.2 | Telegram status and a local diagnostic report provide the MVP views. Product view: installation attempts, completion rate and intervention minutes. Technical view: task state, last heartbeat/reconciliation, queue age, remaining limits and notification failures. Evidence view: exact revision, checks, independent review and freshness. No separate dashboard service is required. | [3.4](#task-3-4) |
| ALERT01 | §7.3 | Missing worker heartbeat | [2.4](#task-2-4) |
| ALERT02 | §7.3 | Reconciliation unavailable | [2.8](#task-2-8) |
| ALERT03 | §7.3 | Fenced result or unauthorized mutation attempt | [2.5](#task-2-5) |
| ALERT04 | §7.3 | Active execution budget | [2.4](#task-2-4) |
| ALERT05 | §7.3 | Telegram delivery failure | [3.4](#task-3-4) |
| ALERT06 | §7.3 | Duplicate remote PR identity | [2.5](#task-2-5) |
| ALERT07 | §7.3 | Preview unhealthy or stale before merge | [3.1](#task-3-1) |
| ALERT08 | §7.3 | Merge result unknown | [3.3](#task-3-3) |
| ALERT-C01 | §7.3 | Deduplicate notifications by task/event identity and severity transition. Telegram outages must not lose task/control records. Critical alerts are also visible locally. Monitoring inactivity on an offline host cannot itself deliver a remote alert unless an external heartbeat monitor is separately configured. | [3.4](#task-3-4) |
| GATE-S01 | §8.1 | Select one repository, host and supported worker runtime; document versions and actual model/auth readiness. | [1.1](#task-1-1) |
| GATE-S02 | §8.1 | Map durable intake, task ownership, IDD execution, independent review and completion evidence to supported Hermes extension points. | [1.1](#task-1-1) |
| GATE-S03 | §8.1 | Select one preview provider and merge method; prove revision-to-deployment identity, smoke evidence and preview cleanup. | [1.3](#task-1-3) |
| GATE-S04 | §8.1 | Demonstrate one opted-in issue through independent review, CI, preview, Telegram revision-bound approval and guarded merge with authoritative read-back. | [1.3](#task-1-3) |
| GATE-S05 | §8.1 | Inject duplicate delivery, host restart and cancel-then-late-completion; prove one PR, no accepted stale result and durable recovery. | [1.4](#task-1-4) |
| GATE-S06 | §8.1 | Demonstrate the publication/credential boundary, including cancellation fencing of worker side effects. | [1.2](#task-1-2) |
| GATE-S07 | §8.1 | Record user-selected preview + approval + merge endpoint and no specific internal compliance commitment. | [1.1](#task-1-1) |
| GATE-S08 | §8.1 | Prove approval persistence, stale-approval rejection, single-use consumption and GitHub enforcement of merge preconditions. | [1.3](#task-1-3) |
| GATE-M01 | §8.2 | F01–F08 and F11–F12 meet acceptance criteria for the selected support recipe. | [3.10](#task-3-10) |
| GATE-M02 | §8.2 | Endpoint-specific requirements and fault tests are defined in F11/F12 and this release plan; execution evidence remains outstanding. | [3.7](#task-3-7), [3.10](#task-3-10) |
| GATE-M03 | §8.2 | Every Must feature has a reproducible verification result with version/fixture identity. | [3.10](#task-3-10) |
| GATE-M04 | §8.2 | Setup, repeat installation and removal preserve unrelated content and user edits. | [3.5](#task-3-5), [3.10](#task-3-10) |
| GATE-M05 | §8.2 | Local analytics/status and bounded alerts work without centralized telemetry. | [3.4](#task-3-4), [3.10](#task-3-10) |
| GATE-M06 | §8.2 | Independent review and CI evidence are attached to every verified result. | [2.6](#task-2-6), [3.10](#task-3-10) |
| GATE-M07 | §8.2 | All release fault scenarios below pass; no safety criterion is waived for schedule. | [3.6](#task-3-6), [3.7](#task-3-7), [3.10](#task-3-10) |
| GATE-M08 | §8.2 | Support limits, known failures, provenance and recovery instructions are documented. | [3.9](#task-3-9), [3.10](#task-3-10) |
| FAULT01 | §8.2 | Repeated delivery, reordered events and webhook/poll overlap | [3.6](#task-3-6) |
| FAULT02 | §8.2 | Crash before/after durable intake or task association | [3.6](#task-3-6) |
| FAULT03 | §8.2 | Crash after remote PR creation before recording response | [3.6](#task-3-6) |
| FAULT04 | §8.2 | Dead worker, expired claim or late stale result | [3.6](#task-3-6) |
| FAULT05 | §8.2 | Cancel while running with a delayed descendant | [3.6](#task-3-6) |
| FAULT06 | §8.2 | Head changes after review or checks | [3.6](#task-3-6) |
| FAULT07 | §8.2 | Missing/failed CI, missing verification contract | [3.6](#task-3-6) |
| FAULT08 | §8.2 | GitHub API outage/rate limit; Telegram outage | [3.6](#task-3-6) |
| FAULT09 | §8.2 | Authorization revoked after enqueue | [3.6](#task-3-6) |
| FAULT10 | §8.2 | Quota exhaustion, runtime limit, fix-attempt limit | [3.6](#task-3-6) |
| FAULT11 | §8.2 | Injection text or secret canary in input/logs | [3.6](#task-3-6) |
| FAULT12 | §8.2 | Reinstall/uninstall with user edits and active work | [3.5](#task-3-5), [3.6](#task-3-6) |
| FAULT13 | §8.2 | Preview failure, wrong revision, expiry or provider outage | [3.1](#task-3-1), [3.7](#task-3-7) |
| FAULT14 | §8.2 | Forged, replayed, expired, rejected or revoked approval | [3.2](#task-3-2), [3.7](#task-3-7) |
| FAULT15 | §8.2 | Head/base/policy changes after approval | [3.2](#task-3-2), [3.7](#task-3-7) |
| FAULT16 | §8.2 | Concurrent or repeated approve commands; controller restart | [3.2](#task-3-2), [3.7](#task-3-7) |
| FAULT17 | §8.2 | Merge accepted remotely then response lost | [3.3](#task-3-3), [3.7](#task-3-7) |
| FAULT18 | §8.2 | Cancellation/revocation races with preview or merge | [3.3](#task-3-3), [3.7](#task-3-7) |
| FAULT19 | §8.2 | Dirty base, draft/closed PR, conflicting merge automation or bypass-only permissions | [3.3](#task-3-3), [3.7](#task-3-7) |
| GATE-P01 | §8.3 | Complete the 10-issue dogfood cohort across two projects sequentially; keep concurrency at one. | [4.3](#task-4-3) |
| GATE-P02 | §8.3 | Compare operator effort against the baseline, including setup and maintenance cost. | [4.3](#task-4-3), [4.6](#task-4-6) |
| GATE-P03 | §8.3 | Deliver F09 and F10 with their own acceptance evidence. | [4.1](#task-4-1), [4.2](#task-4-2), [4.7](#task-4-7) |
| GATE-P04 | §8.3 | Resolve data-handling obligations and pilot consent before external data collection. | [4.4](#task-4-4) |
| GATE-P05 | §8.3 | Observe three external installations and measure repeat use against §1.4. | [4.5](#task-4-5) |
| GATE-P06 | §8.3 | Publish measured failures, supported versions and removal/upgrade recipes. | [4.7](#task-4-7) |
| GATE-E01 | §8.4 | Before considering autonomous merge, define a separate explicitly adopted policy and demonstrate it does not weaken current evidence, branch protections or single-owner enforcement. | [5.3](#task-5-3) |
| GATE-E02 | §8.4 | Before adding another preview provider or external contributor workload, independently verify trust boundaries, scoped credentials and revision-bound smoke evidence. | [4.4](#task-4-4), [5.3](#task-5-3) |
| GATE-E03 | §8.4 | For production, identify immutable artifacts, scoped secrets, explicit approval, health checks and a project-specific recovery plan; irreversible migrations need their own procedure. | [5.3](#task-5-3) |
| GATE-E04 | §8.4 | Reassess concurrency, cross-project isolation, maintenance cost and demand before broad runtime/provider support. | [5.1](#task-5-1), [5.3](#task-5-3) |
| MET01 | §1.4 | Spike viability | [1.3](#task-1-3) |
| MET02 | §1.4 | Completion correctness | [3.6](#task-3-6), [3.7](#task-3-7) |
| MET03 | §1.4 | Recovery correctness | [1.4](#task-1-4), [3.6](#task-3-6) |
| MET04 | §1.4 | Internal usefulness | [4.3](#task-4-3), [4.6](#task-4-6) |
| MET05 | §1.4 | Operator time | [4.3](#task-4-3), [4.6](#task-4-6) |
| MET06 | §1.4 | External onboarding | [4.5](#task-4-5), [4.6](#task-4-6) |
| MET07 | §1.4 | Repeat adoption | [4.5](#task-4-5), [4.6](#task-4-6) |

### Fault matrix and release evidence

Each §8.2 row requires **three independently recorded repetitions**, deterministic assertions and authoritative remote read-back where applicable: 19 rows / 57 row repetitions total. Task 3.6 owns rows 1–12 (36); Task 3.7 owns rows 13–19 (21); Task 3.10 checks complete evidence. Each record retains host/runtime/model/skill/config/provider/repository versions plus fixture/work/attempt/remote/request/approval/intent identities as applicable. The Stage 0 subset does not fulfill this release suite.

| Fault ID | Scenario | Required outcome | Test owner | Repetitions |
|---|---|---|---|---:|
| FAULT01 | Repeated delivery, reordered events and webhook/poll overlap | One logical task, one active attempt, at most one PR | Task [3.6](#task-3-6) | 3 |
| FAULT02 | Crash before/after durable intake or task association | Accepted work retained; association reconciles without duplicate task execution | Task [3.6](#task-3-6) | 3 |
| FAULT03 | Crash after remote PR creation before recording response | Existing PR discovered; ambiguous state parks instead of republishing | Task [3.6](#task-3-6) | 3 |
| FAULT04 | Dead worker, expired claim or late stale result | Old generation fenced; no stale completion or overlapping replacement authority | Task [3.6](#task-3-6) | 3 |
| FAULT05 | Cancel while running with a delayed descendant | Effects fenced; descendant termination confirmed or quarantine surfaced | Task [3.6](#task-3-6) | 3 |
| FAULT06 | Head changes after review or checks | Previous evidence invalidated; no verified outcome for the new SHA | Task [3.6](#task-3-6) | 3 |
| FAULT07 | Missing/failed CI, missing verification contract | Blocked/failed explicitly; no fallback to worker self-report | Task [3.6](#task-3-6) | 3 |
| FAULT08 | GitHub API outage/rate limit; Telegram outage | Bounded retries; remote state unknown; durable notification/recovery records retained | Task [3.6](#task-3-6) | 3 |
| FAULT09 | Authorization revoked after enqueue | No dispatch or newly authorized side effect under revoked permissions | Task [3.6](#task-3-6) | 3 |
| FAULT10 | Quota exhaustion, runtime limit, fix-attempt limit | Parked result with visible reason and honest measured/unknown usage | Task [3.6](#task-3-6) | 3 |
| FAULT11 | Injection text or secret canary in input/logs | Policy unchanged; privileged actions denied; canary absent from exports/messages | Task [3.6](#task-3-6) | 3 |
| FAULT12 | Reinstall/uninstall with user edits and active work | No unrelated changes, destructive cleanup or orphaned execution authority | Task [3.6](#task-3-6) | 3 |
| FAULT13 | Preview failure, wrong revision, expiry or provider outage | No approval-ready/merge outcome; error visible; old preview never reused as new evidence | Task [3.7](#task-3-7) | 3 |
| FAULT14 | Forged, replayed, expired, rejected or revoked approval | No merge; durable rejection reason and no broadened authority | Task [3.7](#task-3-7) | 3 |
| FAULT15 | Head/base/policy changes after approval | Approval invalidated; review/check/preview contract re-evaluated before new approval | Task [3.7](#task-3-7) | 3 |
| FAULT16 | Concurrent or repeated approve commands; controller restart | One consumed approval and at most one merge intent; no chat-only decision state | Task [3.7](#task-3-7) | 3 |
| FAULT17 | Merge accepted remotely then response lost | Read-back identifies actual merge commit; no blind repeat or false failure/success | Task [3.7](#task-3-7) | 3 |
| FAULT18 | Cancellation/revocation races with preview or merge | No new effect after committed fence; earlier in-flight effects reconciled and accurately reported | Task [3.7](#task-3-7) | 3 |
| FAULT19 | Dirty base, draft/closed PR, conflicting merge automation or bypass-only permissions | Merge blocked until explicit compatible policy and current protections are satisfied | Task [3.7](#task-3-7) | 3 |

Any missing/failed repetition keeps GATE-M07 closed. The finite suite validates the stated scenarios; it is not universal security proof. Completion correctness additionally audits every verified outcome for current-head independent-review/required-check agreement and preserves failures, stale and unknown results in reporting.

## Plan validation checklist

These checks validate this planning artifact only. They do not assert future implementation, product acceptance, target attainment, participant recruitment or release authorization.

- [x] `tasks.md` is beside the input `prd.md`; no prior `tasks.md` existed, so a backup is not applicable.
- [x] 5 explicitly phased sprints, 33 unique canonical tasks; sprint counts 4 / 9 / 10 / 7 / 3, each at least 3.
- [x] Every task has description, ≥2 testable criteria, explicit dependencies, effort of 1–3 days, PRD reference, feature/workstream and verification strategy.
- [x] Every dependency names an existing same/earlier-sprint task; full Blocks/Dependencies representations are exact inverses.
- [x] Canonical dependency helper succeeded for full and MVP graphs; no cycles; paths/bottlenecks consumed from its output without numerical overrides.
- [x] Dependency table includes all 33 tasks; deterministic waves and cross-sprint/conditional/external gates are explained.
- [x] All 148 extracted requirements and all 54 PRD feature acceptance bullets have task owners; all 19 fault scenarios have three-repetition test ownership.
- [x] Effort sums checked: 4 + 16 + 19 + 13 + 5 = 57 developer-days; internal MVP = 39.
- [x] PRD Q3–Q10 source numbering, resolved Q1/Q2 and timing conflicts are explicit; no unsupported stack/command/provider choice is invented.
- [x] F09/F10 follow internal MVP; F13 admission is conditional; F14 implementation stays deferred. Non-ideas repository: README index update not applicable.
- [ ] Obtain actual supported-interface, implementation/fault, measurement and owner acceptance evidence when executing this plan; these are future gates.

## Next steps

Review Sprint 1 and Wave 1 (Task 1.1) first. Before implementation, Luong should reconcile the day-2/4d spike and 2–4-week/39d MVP estimates, choose the actual tested recipe and T0, and assign unresolved decisions. Execute the day-2 gate honestly; expand only after complete supported-boundary evidence and an explicit replan if necessary. Keep later pilot/adapter work behind its stated acceptance/admission gates.
