# F14 future policy and design gates — issue #34 (task 5.3)

**Version:** 1.0.0 · **Recorded:** 2026-10-05 · **Status:** complete —
all five F14 capabilities `deferred`; every checklist fail-closed by
construction

The §8.4 GATE-E01–GATE-E04 admission and policy/design checklists for
the F14 *Won't*-tier capabilities (§3.1): production promotion,
autonomous merge and parallel projects. The companion deferred-scope
record is
[docs/decisions/future-scope.md](../decisions/future-scope.md).

**What this evidence is.** A versioned, reviewable enumeration of the
gates any future F14 proposal must pass: each capability carries a
named decision owner, the evidence and fault scenarios its own future
PRD must produce, and explicit go/no-go criteria. Gate and evidence
states reuse the standing vocabulary — `passed` / `blocked` /
`pending` / `unknown` for checklist rows, `proven` / `conditional` /
`unproven` / `no-go` for capability evidence — so these checklists read
exactly like the executable gates they mirror
(`tools/probes/harness_admission.py`,
`tools/probes/adapter_precondition.py`,
`tools/probes/release_gate.py`). Where a recorded state is cited, it is
derived from the committed ledgers of those probes; nothing is claimed
beyond what those archives record.

**What it is not.** Not an implementation, an admission, a policy
activation or a date commitment: this document creates no runtime code,
no enabled configuration and no implied adoption, and no checklist row
here can itself increase an active-task limit, admit a provider or
authorize a merge mode. Every capability stays deferred until its *own*
future PRD, the owner's explicit adoption act and independent evidence
exist (§8.4; task 5.3 implementation notes). Not a weakening of the
standing contract: the current preview plus revision-bound
human-approved merge endpoint and the single active lane remain the
prerequisites every checklist preserves. Design completion — these
checklists existing — is not delivery and not adoption.

## Gate semantics — fail-closed, deferred-by-default

Five rules, the same semantics the executable probes enforce:

1. **Only `passed` admits.** A checklist row reports `passed`,
   `blocked`, `pending` or `unknown`; anything but `passed` records
   **no-go**. Capability evidence reports `proven` / `conditional` /
   `unproven` / `no-go` — never an unearned pass.
2. **Absence fails closed.** Missing adoption, missing evidence or an
   unreadable source records `pending`/`unknown` — a gap is a named
   blocker, not a pass.
3. **A weakened prerequisite is a no-go.** Admission is conjunctive:
   any row that would weaken the current revision/review/check/preview
   contract, branch protections, deterministic authorization or
   single-owner enforcement records no-go even when every other row
   passes.
4. **The checklist never authorizes.** A completed checklist produces
   a disposition, not permission: the owner's explicit adoption act in
   the capability's own future PRD is the only authorizing act, and its
   evidence is re-judged, never inherited — the same rule
   `adapter_precondition.py` enforces for task 5.2.
5. **Reasons are denominator-preserving.** Every non-passed row is
   named in the capability's no-go list; dropping a reason is an
   instrumentation breach, not a smaller gate.

## Standing prerequisites — the contract no checklist may weaken

Derived from the current implementation and its recorded ledgers. A
future proposal that cannot preserve every row is a no-go under rule 3.

| Prerequisite | Current contract | Source |
|---|---|---|
| One bounded execution lane | `active_tasks: 1`, one issue attempt, sequential implement/review; dogfood cohort audited 22/22 samples clean | F03 · `factory_kit/execution/limits.py` · `docs/measurements/dogfood-comparison.md` |
| Independent review on the current revision | separate reviewer session inspects the diff against issue criteria at one SHA; required checks tied to it | F04 · `factory_kit/verification/` |
| Revision-bound preview + smoke | preview bound to the recorded revision; smoke runs against the recorded deployment, never self-report; TTL-bound, owned cleanup | F11 · `factory_kit/preview/` |
| Durable one-use human approval | the approval authorizes only the verified revision — expiry-fenced, CAS-enforced, never inherited by later changes | F12 · `factory_kit/approval/service.py` |
| Expected-head guarded merge + read-back | merge only on the approved head; authoritative outcome reconciliation, no blind repeat mutation | F12 · `factory_kit/merge/` |
| Deterministic authorization, single-owner enforcement | every write traverses the scoped broker with repository/action/generation/authorization checks | intake→publication contract · `factory_kit/` |
| Enforced branch protections | GitHub protections enforced; prompts, worktrees and conventions never substitute | F12 · `docs/spike/hermes-boundary-map.md` |
| Scoped, non-privileged credentials | no production secrets or unrestricted mutation credentials in workers/tests; every permitted write fenced | `docs/pilot/consent-admission.md` boundary probes |

## GATE-E01 — autonomous merge admission checklist

§8.4: *"Before considering autonomous merge, define a separate
explicitly adopted policy and demonstrate it does not weaken current
evidence, branch protections or single-owner enforcement."* Issue A2.

| Checklist row | Required evidence | Recorded state |
|---|---|---|
| `adopted_policy` | a separate policy document explicitly adopted by the owner in its own future PRD — drafted is not adopted | **pending** — no policy exists; none may be drafted into effect by this document |
| `revision_contract_preserved` | reproducible evidence the autonomous path preserves the revision-bound review/check/preview contract on every merge | **no-go** — no such evidence exists; F11/F12 evidence covers the human-approved endpoint only |
| `branch_protections_preserved` | enforced protections demonstrated intact under the autonomous path — expected-head merge, one-use approval semantics or their documented equivalent | **no-go** — weakening protections fails rule 3; no equivalent demonstrated |
| `deterministic_authorization` | deterministic (non-probabilistic) authorization for every merge write, auditable per repository/action/generation | **no-go** — the current brokered-authorization contract has no autonomous-mode evidence |
| `single_owner_enforcement` | single-owner authority enforcement preserved under automation | **no-go** — unproven for any autonomous path |
| `reproducible_evidence` | the demonstration is reproducible — fixture identity, version pins and authoritative read-back retained | **pending** — undefined until the future PRD names it |

**Recorded disposition: `deferred` — no-go.** Missing adoption or any
weakened prerequisite independently records no-go (rows above name all
six). Autonomous merge is not considered until its own future PRD
carries an adopted policy plus the reproducible evidence this
checklist enumerates.

## GATE-E02 — additional preview provider / external-contributor input classes

§8.4: *"Before adding another preview provider or external contributor
workload, independently verify trust boundaries, scoped credentials and
revision-bound smoke evidence."* Issue A3. Both classes share the gate;
each is admitted separately, and admitting neither is the standing
disposition.

| Checklist row | Required evidence | Recorded state |
|---|---|---|
| `scoped_credentials` | independently verified scoped credentials for the new provider/input class — never a widening of the selected lane's scopes | **no-go** — the selected provider is the only verified credential scope on record |
| `trust_boundary` | an enforceable process/filesystem/network boundary demonstrated for the workload — a worktree, prompt rule or branch protection is never evidence (`docs/pilot/consent-admission.md` boundary probes) | **no-go** — the boundary probes deny external code without demonstrated isolation; the recorded `unsupported-workload:runtime-mirror` admission was denied on exactly this class |
| `revision_bound_smoke` | revision-bound smoke verification through the new provider/class, replayable with fixture identity | **unproven** — smoke evidence exists for the selected provider only |
| `isolation_supported` | process, filesystem and network isolation supported and demonstrated on the tested recipe | **no-go** — unsupported isolation records no-go *rather than allowing the workload* |
| `no_privileged_credentials` | worker/test lanes carry no privileged, controller or unrestricted-mutation credentials | **no-go** — privileged worker/test credentials record no-go by construction; the current lanes hold none and a new class must prove the same |
| `single_provider_singleton` | at most one provider/class under admission at a time — matching the F13 singleton rule | **passed** (scope rule) — zero additional providers admitted today |

**Recorded disposition: `deferred` — no-go.** An additional preview
provider and each external-contributor input class each need their own
future PRD with the evidence above; the unsupported-isolation and
privileged-credential rows record no-go rather than permitting the
workload on partial evidence.

## GATE-E03 — production promotion design checklist

§8.4: *"For production, identify immutable artifacts, scoped secrets,
explicit approval, health checks and a project-specific recovery plan;
irreversible migrations need their own procedure."* Issue A4.

| Checklist row | Required evidence | Recorded state |
|---|---|---|
| `immutable_artifact_identity` | content-addressed or digest-pinned artifact identity from build to deploy | **unproven** — no production artifact pipeline exists |
| `scoped_secrets` | production secrets scoped, referenced never literal, with a documented rotation path | **no-go** — no production secret scope defined; the v1.1 package is recipe-scoped |
| `explicit_approval` | an explicit human approval gate on promotion, bound to the immutable artifact identity | **no-go** — only the revision-bound PR approval exists today |
| `health_checks` | defined health checks and their failure semantics pre/post promotion | **unproven** — undefined |
| `project_specific_recovery` | a project-specific recovery plan with named steps and owners | **unproven** — the tested `repair`/`rollback` recipes cover the kit's own owned bytes, not a production service |
| `irreversible_migration_procedure` | each irreversible migration carries separately defined steps/owners and rehearsal evidence; it is **never assumed recoverable by generic rollback** | **no-go** — no such procedure is defined; generic `rollback` covers the kit's checkpointed registration only |
| `publication_prerequisites` | Q8 distribution/license/support/notice resolved and the v1.1 release gate itself open | **blocked** — Q8's four rows are `Unresolved` and the recorded disposition is `release-withheld` (`docs/decisions/q8-distribution.md`, `docs/releases/v1.1/release-checklist.md`) |

**Recorded disposition: `deferred` — no-go.** Production promotion is
further than the other capabilities from evidence: the release gate
itself is withheld today, and every design row above is unproven or
undefined. A production future PRD must define each row with named
steps/owners before any promotion procedure is considered.

## GATE-E04 — concurrency / parallel projects checklist

§8.4: *"Reassess concurrency, cross-project isolation, maintenance cost
and demand before broad runtime/provider support."* Issue A5. The same
§8.4 reassessment gated the F13 optional-harness question in task 5.1 —
recorded `admission-deferred` on exactly the demand/cost rows below
(`docs/adapters/admission-assessment.md`) — and task 5.3 applies it
here to the concurrency/parallel-project class.

| Checklist row | Required evidence | Recorded state |
|---|---|---|
| `measured_demand` | measured demand for parallel projects — not extrapolated enthusiasm | **no-go** — the only measured signals are the fixture cohort (sequential by construction) and a failed repeat-use target (1 of proposed 2) |
| `maintenance_cost_review` | maintenance-cost/capacity review showing parallel lanes do not erase savings | **no-go** — measured burden already reads 23.5 ≥ 23.0 min/issue at *one* lane (`docs/measurements/pilot-evaluation.md`) |
| `cross_project_isolation` | enforceable cross-project isolation analysis — credentials, lanes, state and artifacts separated by mechanism, not convention | **unproven** — never analyzed; single-project support only |
| `single_owner_analysis` | single-owner enforcement analysis across projects — attribution and authorization cannot blur between projects | **unproven** — never analyzed |
| `limit_preservation` | the checklist result itself cannot raise `active_tasks` or enable concurrent projects — a limit change is a separate authorized act | **passed** (scope rule) — `active_tasks: 1` stands; this document changes no configuration |

**Recorded disposition: `deferred` — no-go.** No checklist result —
including a hypothetical all-`passed` re-judgement under a future PRD —
increases active-task limits or enables multiple concurrent projects by
itself; only the owner's explicit adoption in that capability's future
PRD, backed by the evidence rows above, could.

## Per-capability future-PRD register

Each F14 capability is deferred **pending its own separate future
PRD** carrying the owner, evidence and go/no-go criteria named here —
a register row is a promise about what that PRD must contain, never a
schedule or a commitment to write it. "Fault scenarios" reference the
§8.2-style scenario classes the capability's evidence must cover.

| Capability | Gate | Decision owner | Required evidence / fault scenarios | Explicit go/no-go criterion |
|---|---|---|---|---|
| Autonomous merge | GATE-E01 | Luong | adopted policy document; reproducible revision/review/check/preview-preservation evidence incl. approval-replay, stale-head and protection-weakening fault scenarios; deterministic authorization + single-owner proofs | go only when every GATE-E01 row is `passed` under an adopted policy; any missing adoption or weakened row = no-go |
| Additional preview provider | GATE-E02 | Luong | independent scoped-credential + trust-boundary verification; revision-bound smoke replay on the candidate provider; provider-outage and stale-preview fault scenarios | go only on `passed` rows for exactly one named provider; unsupported isolation or privileged credentials = no-go |
| External-contributor input classes | GATE-E02 | Luong | per-class enforceable process/filesystem/network boundary proof; non-privileged worker/test credential audit; hostile-boundary fault scenarios matching `factory_kit/privacy/boundary.py` probe classes | go per class only on `passed` rows; unsupported isolation or privileged credentials = no-go rather than allowing the workload |
| Production promotion | GATE-E03 | Luong | immutable artifact identity + scoped secrets design; explicit-approval gate design; health-check definitions; project-specific recovery plan; per-migration irreversibility procedures with steps/owners and rehearsal | go only when every GATE-E03 row is `passed`; an irreversible migration without its own procedure = no-go; Q8 resolution is a precondition |
| Parallel projects | GATE-E04 | Luong | measured demand; maintenance-cost/capacity review vs the recorded single-lane baseline; enforceable cross-project isolation + single-owner analysis; cross-project fault scenarios (credential bleed, lane starvation, artifact collision) | go only on `passed` rows; no checklist result itself raises `active_tasks` or enables concurrency |

## Retained open questions

Unresolved §9.1 items apply wherever a future PRD touches their scope —
they are retained here, not resolved by this document:

- **Q8** — license, distribution channel, support commitment and
  dependency notice process: all four rows `Unresolved`
  (`docs/decisions/q8-distribution.md`), blocking any public release —
  a precondition for production promotion and for any public-facing
  F14 surface.
- **Q9** — proposed numerical targets, retention defaults and budgets:
  submitted, owner verdict pending (`docs/decisions/q9-acceptance.md`)
  — retained wherever a checklist names a numerical bound (limits,
  TTLs, budgets); a future PRD may propose new bounds but may not
  silently inherit unaccepted ones.

## What this document does *not* do

- It does not implement, admit or schedule any F14 capability — F14
  stays a §3.1 *Won't* and every disposition above is `deferred`.
- It does not activate a policy: no configuration value, feature flag,
  limit or credential scope is created or changed by this document.
- It does not weaken the current endpoint — the revision-bound preview,
  durable one-use human approval, expected-head guarded merge and
  single active lane are the prerequisites, and the endpoint's own open
  live items (`base-unprotected`, `telegram-adapter-live`,
  `vercel-linkage`, `kanban-live-write` on the v1.0 ledger) stand
  unchanged; a future gate re-judges against the *then-current*
  contract, never against a weaker memory of it.
- It does not equate design completion with delivery or adoption —
  these checklists being written is task 5.3 done, not F14 begun.
- It does not resolve Q8, Q9, the pending owner confirmation of the
  recorded narrow/consolidate disposition, or the F13 adoption track —
  each keeps its own owner and record.

## Reproduce / review

Reviewable by reading this document against `prd.md` §3.1/§8.4/§9.1
and `tasks.md` task 5.3; every recorded state above names its source
ledger. The executable gates this vocabulary mirrors are re-judgeable:

```bash
python3 tools/probes/harness_admission.py --fixture \
    docs/adapters/admission-assessment-2026-10-05.json
python3 tools/probes/adapter_precondition.py --fixture \
    docs/adapters/adapter-precondition-2026-10-05.json
python3 tools/probes/release_gate.py --fixture \
    docs/releases/v1.1/release-gate-2026-10-05.json
```
