# Validation: factory-kit

- **Assessed:** 2026-10-04.
- **Goal:** an open-source kit reliable in Luong's projects first, then adopted by other developers.
- **Constraints confirmed by the user:** solo builder; narrow MVP in 2–4 weeks; bootstrapped side project; extend Hermes and reuse IDD/skills and existing subscriptions.
- **Direction confirmed after the overlap check:** Hermes-native, Telegram-first, local kit reusing IDD and existing harnesses; avoid rebuilding generic factory infrastructure.
- **Evidence boundary:** live product/documentation research and GitHub API metadata, not customer interviews or an executed factory integration. No implementation, installation or deployment was performed for this validation.

## Quick Verdict
**Maybe**

## Why

The internal-use problem is real and there is a credible reuse path, but the broad idea is already served by Warp Factories/Oz, Paperclip, OpenHands and GitHub's agents. Even Hermes adapters, local harnesses and Telegram are no longer individually distinctive. Proceed only with a bounded integration spike and a narrow verified-PR kit; do not commit to a generic software-factory platform until reliability and external onboarding are demonstrated.

### Market and demand

**Competition:** crowded and converging, with both paid platforms and maintained permissively licensed alternatives. Published paid plans and vendor customer testimonials establish an adjacent market, not willingness to pay for this particular kit. GitHub stars show attention, not active users, successful delivery or retention.

**Who has urgent pain:** the initial user already coordinates IDD skills and several coding harnesses across GitHub projects. Recovering interrupted work, checking current PR evidence and intervening away from the workstation are concrete operational needs. The severity and frequency of these problems for external Hermes/IDD users have not been measured.

**Market size:** no defensible TAM or reachable-user count was established. The initial segment is narrower than the market for coding agents: developers who prefer local execution, use Hermes/Telegram, and want issue-driven delivery. That is acceptable for an OSS utility but insufficient evidence for a standalone business.

**Demand validation:** compare against the existing IDD/manual process on real scoped issues. Then observe external maintainers install and use the kit without the author doing their setup. Interviews should investigate current failures and workarounds, not ask whether an appealing diagram sounds useful.

### Feasibility and technical risk

**A 2–4-week MVP is plausible only as a thin, one-repository, one-worker-runtime integration ending at a reviewed PR.** It is not a credible timeline for arbitrary harnesses, multi-project concurrency, durable merge approvals, preview adapters and safe production promotion together. This is a planning judgment, not a measured estimate.

Documented foundations are unusually relevant:

- Hermes webhooks provide HMAC validation, filtering, coalescing and Telegram delivery. The documentation describes a one-hour delivery-ID cache; this alone does not establish a restart-safe accepted-event ledger or exactly-once PR publication. Reconcile GitHub state and persist logical work identity. [S15]
- Hermes Kanban already owns durable task/run/event state, worker claims, bounded failure handling, review handoffs and exact-head PR completion contracts. Reuse those guards rather than coding a parallel scheduler or claiming factory-kit invented them. Completion is a snapshot, not a merge transaction or continuous monitor. [S16]
- Hermes's worker-lane documentation says arbitrary external CLI lanes are **not yet a paved path**. A pluggable spawn function is not a complete adapter: auth, cancellation, workspace mapping and lifecycle handoffs remain integration work. Cooperative lineage fencing is not OS sandboxing. [S17]
- Hermes separately documents an optional Codex app-server runtime that works with Kanban handoffs. Evaluate that supported route before inventing a direct Codex lane; it does not prove equivalent Claude/Pi/OpenCode integration or readiness on this host. [S18]
- Native plugins can bundle skills, tools, hooks and commands. This is a distribution path, not proof that every intake/approval requirement can be enforced without core changes. Third-party integrations should normally ship as standalone plugins. [S19]
- IDD already supplies triage, resolution, review and auto-pilot workflows, with per-role model/effort settings. Reuse them and explicitly suppress competing auto-merge ownership. [S20]

**Unresolved gate:** demonstrate a supported, deterministic path from authorized GitHub intake to a durable Kanban task, IDD execution, independent review, current-head CI evidence, and accurate Telegram status across a restart. If this requires undocumented core surgery, pause the standalone kit and contribute the missing primitive upstream. No end-to-end test has yet established this path.

**Highest operational risks:** a duplicate event opens a second PR; a stale worker publishes after cancellation; an old approval authorizes a new SHA; a trusted signature carries attacker-authored instructions; tests run with overbroad credentials; a worker is counted as successful because it exited cleanly; a subscription quota blocks the entire loop.

**Cost:** existing subscriptions reduce initial purchasing friction but are not unlimited capacity or universally permitted unattended usage. Discover supported authentication, applicable terms and quotas per adapter. Measure actual usage where exposed; enforce concurrency, runtime and attempt limits when dollar usage is unavailable. No per-task price or hosting budget was proven.

### Monetization

OSS adoption and saved operator time are the current success criteria, not revenue. Optional future income could come from installation/support, maintained adapters or managed hosting, but no customer has committed to pay. Paid competitors start at relatively accessible prices, while Paperclip and OpenHands provide substantial free alternatives. Do not sell this as a cheaper platform without measuring model usage, maintenance and support costs.

### Duplication and dependency exposure

The design duplicates existing capabilities if it creates a scheduler, generic task dashboard, provider router or new skills engine. Its defensible scope is project onboarding, tested IDD delivery recipes, configuration diagnostics and missing evidence/policy glue on top of existing Hermes ownership.

Hermes, IDD, the user's skills repo, Paperclip, oz-for-oss and the inspected OpenHands repositories declare MIT; Symphony declares Apache-2.0. Preserve applicable notices and audit each bundled dependency/skill independently. A repository's top-level license does not license vendor APIs or subscription usage. Fast-moving upstream APIs and per-harness semantics are a continuing maintenance burden. [S1, S7–S10, S17, S19–S21]

## Competitive Landscape

Seven product/project entries are assessed below; related repositories are grouped with their parent product. Feature gaps are comparisons with the reviewed documentation, not proof that a competitor cannot support a custom integration. Prices are published USD prices checked on the assessment date, not guaranteed workload costs.

| Competitor | Type | What They Do | Pricing / License | Traction / Health | Reuse Potential | Key Weakness |
|---|---|---|---|---|---|---|
| [Warp Factories / Oz + oz-for-oss](https://docs.warp.dev/factories) | Commercial + OSS workflow kit | Coordinated triage/spec/implementation/review agents, GitHub/webhook intake, skills and per-agent model/harness choices. | Factories early access; PAYG with 20% markup; Build starts $20/month; oz-for-oss MIT. [S1–S4] | Factories limited-team early access; OSS API snapshot: 313 stars, 41 forks; latest default-branch commit 2026-09-09. | Reference or selectively reuse MIT workflow/routing code; whole-kit port requires replacing Vercel/Oz integrations. | Self-hosted Factories execution is an eligible Enterprise feature; not the minimal local Hermes/IDD/Telegram install being proposed. |
| [Factory Droid Automations](https://factory.ai/product/automations) | Commercial | Trigger Droid from schedules, Slack, GitHub events and webhooks; templates include ticket-to-PR, review and incident triage. | Pro $20/month, Plus $100, Max $200; team and enterprise plans. [S5–S6] | Current official templates/docs and named customer testimonials; active-user count not independently established. | Workflow reference or optional executor integration; proprietary platform is not an OSS base. | Primarily Droid execution; webhook triggers are private preview. No turnkey Hermes/IDD recipe verified. |
| [GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent) | Commercial / native-platform substitute | Research, plan and change GitHub code in an Actions-backed environment, with optional PRs and custom agents. | All paid Copilot plans; Pro $10/month; AI credits and Actions minutes affect usage. [S11–S12] | Official available feature and maintained documentation; external retention/outcome data not collected. | Use GitHub issue/PR/check surfaces; compare the native path before adding a controller. | GitHub cloud workflow rather than the user's local Hermes control plane; copying issue-to-PR alone adds little. |
| [OpenHands / Agent Canvas + Automation](https://github.com/OpenHands/OpenHands) | OSS + commercial hosted options | Self-hosted coding-agent control center, ACP agent support, and scheduled/webhook automations. | Inspected core and Automation repos MIT; local free; hosted Individual free with BYOK/at-cost inference; enterprise custom. [S7–S8] | Core API snapshot: 89,938 stars, 11,882 forks; latest commit 2026-10-03. Automation service marks itself beta. | SDK/automation reference or optional runtime; adopting its whole backend adds a second control plane. | Not an existing Hermes/IDD-first install; multiple services/backend integration and beta automation APIs add setup burden. |
| [OpenAI Symphony](https://github.com/openai/symphony) | OSS / adjacent orchestrator | Issue-tracker polling, isolated per-issue workspaces, agent execution and repository-owned WORKFLOW.md policy. | Apache-2.0; compute/model usage extra. [S9] | API snapshot: 27,524 stars, 2,855 forks; latest commit 2026-09-15; README calls it an engineering preview for trusted environments. | Reference its work identity, reconciliation, workspace and versioned-policy contracts; do not add a second daemon. | Codex app-server protocol focus; does not restore exact in-memory scheduler state across restart or mandate a single approval/sandbox policy. |
| [Paperclip](https://github.com/paperclipai/paperclip) | OSS / direct-adjacent control plane | Persistent tasks, governance, budgets, multi-harness adapters, routines and experimental chat connectors. | MIT; self-hosting/model/runtime costs extra. [S10, S13] | API snapshot: 96,871 stars, 16,402 forks; latest commit 2026-10-04. README documents Hermes adapters and experimental Telegram connectors. | Strong alternative base if broad agent governance is needed; plugin/template contribution avoids generic-control-plane duplication. | Broader organization/task system than a small project kit; the exact IDD delivery and existing-repo migration recipe still needs integration. Telegram is experimental, not absent. |
| [GPT Pilot](https://github.com/Pythagora-io/gpt-pilot) | Source-available / abandoned predecessor | Multi-role agent-assisted app construction with human supervision. | FSL-1.1-MIT with version-specific future MIT conversion; do not assume current code is unrestricted MIT. [S14] | API snapshot: 33,658 stars; latest commit 2026-06-12 was security cleanup; README explicitly says no active maintenance. | Failure/security lessons only; not a production dependency recommendation. | Maintainer-reported credential-stealing code and abandoned maintenance; popularity did not establish safety. |

### Commercial Tools & Services

**Warp is the closest match, not merely inspiration.** Its current Factories docs describe a foreman, specialist agents, skills, factory definitions as code, approvals, per-agent harness/model selection and measurement. Default agents cover triage through review; custom agents extend the rest. Oz explicitly supports Claude Code, Codex and Warp execution and self-hosted options. Broad statements such as “nobody offers a multi-harness software factory” or “we win because it is self-hosted” would be false. Factories remains limited-team early access, and its documented enterprise self-hosting boundary leaves room for a small independently operated kit. [S2–S4]

Warp's MIT oz-for-oss repository is particularly relevant: the current implementation uses a Vercel webhook control plane, Oz dispatch, KV run state and cron application of results. This is real reusable wiring, not only a diagram. Porting it wholesale to Hermes/local execution would replace significant control/execution integration; selective code/reference reuse is more defensible than assuming a near-free fork. [S1]

**Factory already offers recurring engineering automations**, local/cloud/Actions execution, model settings, run feeds and failure-triggered pauses. Its dedicated setup flows and templates also weaken the claim that a setup skill alone is a durable moat. Published pricing and named testimonials are evidence of adjacent purchasing, not a verified addressable market for factory-kit. [S5–S6]

**GitHub already removes much of the issue-to-PR plumbing.** Its paid cloud agent has custom agents and model choice; the current pricing page also advertises third-party agent delegation in preview on higher plans. Compare the native experience fairly rather than describing it as a single-model autocomplete tool. The differentiator must be the user's preferred local project workflow and operator experience. [S11–S12]

### Open-source Alternatives & Reuse Potential

1. **Hermes + IDD — preferred foundation.** Both are MIT, with recent upstream activity verified via GitHub API. Hermes should retain task lifecycle ownership; IDD supplies procedures. Keep factory-specific configuration/install/diagnostics in a thin standalone plugin plus setup skill. Contribute missing generic durability or lifecycle primitives upstream. Do not fork Hermes merely to accelerate a demo. [S15–S21]
2. **Paperclip — strongest challenge to a new control plane.** It already lists Claude, Codex, Pi, OpenCode, Hermes and other runtimes; its adapter docs provide Hermes CLI and gateway integration, and its README describes experimental Telegram connectors. “Hermes + Telegram + many harnesses” is therefore a packaging preference, not established novelty. Use Paperclip instead if the need becomes company-wide governance, budgets and agent management; otherwise avoid operating both its scheduler and Hermes Kanban over the same tasks. [S10, S13]
3. **oz-for-oss — selective MIT reuse.** Its routing, concrete repository context and skills-backed workflow separation are useful references. Reuse only reviewed, pinned components with retained notices; revalidate trust, idempotency and policy under local execution. The Warp commercial control plane is not made open source by this repository's license. [S1–S4]
4. **OpenHands — substantial maintained alternative, not just a worker.** Its current README supports ACP agents and backend switching; the Automation service owns definitions, webhooks, scheduling and run history. Its core popularity does not prove the smaller beta Automation service is stable. Adding its full stack would increase deployment and state-ownership complexity; an optional executor or isolated reference experiment is a more bounded use. [S7–S8]
5. **Symphony — reference, not another scheduler dependency.** Its specification makes policy, workspace safety, reconciliation, retries and operator observability explicit. It also explicitly distinguishes restart recovery from persistence of live sessions/timers. Borrow that clarity while using Hermes's actual lifecycle guarantees. Its Apache-2.0 license entails its own notice obligations if code is copied. [S9]
6. **GPT Pilot — do not build on it.** Non-maintenance, source-license restrictions and the documented supply-chain incident outweigh a shortcut to multi-role orchestration. The failure lesson is dependency provenance and operational stewardship, not “autonomous coding is impossible.” [S14]

### White Space Analysis

The most credible gap is **an opinionated, low-burden installation and operating recipe for existing Hermes/IDD users**, with evidence that it survives real failures and preserves a project's established CI and release controls.

Candidate differentiators to test together:

- One project registration/setup flow that reuses existing IDD configuration and skills rather than introducing another team/task system.
- Accurate Telegram status and cancellation tied to durable GitHub/Kanban identities, not chat history.
- A small, reproducibly tested set of stack/harness recipes with known limits, upgrades and uninstall.
- Measured reductions in operator interventions and recovery work relative to using the existing tools directly.

This is a **hypothesis**, not an exclusive market gap established by exhaustive research. Competitors can add equivalent recipes or connectors. Installation quality, maintained compatibility and trust earned through failure tests are more defensible than a feature checklist.

### Differentiation Assessment

- **Not unique:** skills, GitHub webhooks, autonomous issue resolution, independent role agents, multi-model routing, local/self-hosted execution, status feeds or agent supervision.
- **Not unique by itself:** Hermes adapters or Telegram. Paperclip now documents both, with experimental status for chat connectors. [S10, S13]
- **Potentially useful but unproven:** a much smaller Hermes-native IDD project kit with reliable, additive onboarding and verifiable delivery gates.
- **Missing evidence:** successful restart/cancel/duplicate-event demonstrations; operator-time measurements; independent installations; external repeat usage.

The user was shown the Warp overlap and explicitly chose the Hermes-native/local/IDD direction. That is a reason to validate the narrower utility, not evidence that differentiation has already been achieved.

### Build vs. Base Recommendation

**Build on Hermes and IDD; do not build a new factory runtime from scratch.** Ship only integration recipes, readiness diagnostics, setup/uninstall and genuinely missing event/evidence/policy glue. Prefer normal Hermes workers initially; evaluate documented Codex runtime support before bespoke lanes. Keep existing CI authoritative and human merge/release ownership unchanged in the MVP.

Use Paperclip as the alternative if the need expands to broad agent governance, or if its existing integrations make the desired result materially cheaper to operate. Use oz-for-oss and Symphony as references or selectively reuse compatible code; do not introduce a second state machine simply because it exists. If Hermes lacks an essential supported boundary, upstream that primitive before expanding factory-kit.

### Failed Predecessors

GPT Pilot's own README states that it is no longer actively maintained and that a malicious credential-stealing loader persisted until security cleanup in June 2026. The maintainers explicitly say the cleanup is not resumed development. API metadata shows it is not formally archived; neither an unarchived flag nor a recent cleanup commit should be mistaken for maintenance health. [S14]

**Established:** abandoned maintenance and a maintainer-reported security incident. **Not established:** that the company failed commercially, why development originally stopped, or that its architecture alone caused abandonment. Do not invent a shutdown or causal post-mortem.

Lessons for factory-kit: pin reviewed releases/skills; preserve provenance and revocation paths; restrict execution permissions; never put production secrets in coding workers; define who maintains adapters; treat maintenance health separately from star counts.

## Similar Products

- Warp Factories/Oz is the closest commercial standing software-factory workflow.
- oz-for-oss is the closest inspected skills-and-webhook reference implementation.
- Paperclip is the strongest OSS control-plane alternative, including Hermes and experimental Telegram overlap.
- OpenHands is a maintained self-hosted multi-agent/automation alternative.
- GitHub Copilot is the simplest native substitute for users who only need delegated issue-to-PR work.
- Symphony provides a clear issue-dispatch/reconciliation specification.
- GPT Pilot is an abandoned source-available predecessor, not a recommended foundation.

## Differentiation

Position factory-kit as **“the tested project kit for running IDD delivery workflows through your existing Hermes and Telegram setup.”** Do not claim a novel universal factory or universal harness support.

The adoption argument should be: less setup and intervention than assembling the same components manually, without replacing existing project workflows. Demonstrate that argument on real issues and on a second developer's environment; otherwise the kit is merely another configuration layer.

## Strengths

1. **Immediate dogfood user and assets.** The author already has the relevant projects, IDD procedures, skills and Hermes/Telegram environment; learning can be grounded in real maintenance work rather than speculative demand.
2. **Strong foundation reuse.** Hermes already has durable worker/review machinery and remote PR evidence gates; IDD covers much of the development procedure. A small integration layer can avoid large greenfield engineering costs.
3. **Correct reliability focus.** Additive setup, bounded execution, untrusted-input handling and explicit evidence ownership address the failures that turn successful demos into unreliable unattended tools.

## Concerns

1. **Weak feature novelty and unvalidated external demand.** Warp, Paperclip, OpenHands and GitHub overlap heavily. Remedy: target existing Hermes/IDD users, compare their present workflow, and require independent onboarding and repeat usage before broadening.
2. **Scope exceeds the solo timeline unless cut hard.** The full triage-to-production/multi-harness vision cannot credibly ship as a reliable 2–4-week MVP. Remedy: stop at a verified PR with one repo, one worker runtime and one opted-in task; defer merge/deploy, concurrency and new adapters.
3. **Unproven integration and trust boundaries.** Event acceptance, cancellation fencing, adapter lifecycle completion and subscription budgets are not solved merely by composing prompts. Remedy: run deterministic failure tests, reuse existing guards, narrow credential scope, and escalate any missing upstream primitive rather than bypassing it.

## Ratings

- **Creativity: 5/10** — useful synthesis for this ecosystem, but most architectural features already exist; packaging alone is easy to copy.
- **Feasibility: 7/10** — the narrow verified-PR integration has credible foundations; the full original factory does not fit the stated solo MVP window. Score is provisional until the integration spike passes.
- **Market Impact: 5/10** — plausible value for a specific OSS segment; adoption, reachable audience and external demand remain unmeasured.
- **Technical Execution: 6/10** — sound proposed safety/evidence boundaries, but no implementation or restart/cancel test exists yet. This scores the proposed execution strategy, not demonstrated code quality.

These are qualitative judgments, not calibrated probabilities, measured benchmark results or a computed investment score.

## How to Strengthen

1. **Publish a narrow support promise.** One supported host path, one repository type and one worker runtime; list capabilities and unsupported stages explicitly. “Any existing project/harness” is a long-term aspiration.
2. **Define deterministic authority.** Policy decides actor/repository authorization, run identity, allowed side effects and current evidence. The LLM chooses bounded work and explains blockers, but cannot waive these checks through prose.
3. **Make setup earn trust.** Detect existing CI/IDD setup; show a diff; refuse conflicting merge owners; test readiness; rerun idempotently; provide repair, upgrade and uninstall. A setup skill guides the process; deterministic components perform validation and state changes.
4. **Test failures before more features.** Duplicate events, host restart, dead worker, stale result, revoked authorization, failed required CI, API outage, quota exhaustion and cancel-then-late-completion must produce safe, visible states.
5. **Measure the workflow, not generated code volume.** Record current-head evidence, attempts, elapsed time, human intervention minutes, rework and outcomes. Separate subscription quota usage from actual dollar billing; unknown spend is not zero spend.
6. **Avoid expensive idle reasoning.** Route and filter events deterministically, notify only at useful boundaries, and ask the model to investigate exceptions. Bound concurrency and attempts.
7. **Distribute versioned recipes.** Pin compatible Hermes/IDD versions and record skill revisions; avoid copying independently changing workflows into every repo. Generic core improvements belong upstream, project templates in the kit.
8. **Validate adoption directly.** Recruit a few existing Hermes/IDD users, observe setup without taking over, and learn why they would choose this over direct IDD, Paperclip or GitHub agents. Do not substitute stars for retained use.

## Enhanced Version

**factory-kit: a local-first, Hermes-native project integration for bounded IDD issue delivery, operated through Telegram.**

- A setup skill discovers project conventions and proposes an additive installation.
- A thin supported integration registers authorized GitHub work with Hermes's durable board; periodic reconciliation recovers missed events.
- One default worker runtime executes pinned IDD workflows in isolated workspaces.
- An independent reviewer and current-head CI evidence produce a linked, reviewable PR.
- Telegram exposes accurate status, blockers, pause/cancel and subsequent bounded steering.
- The first version neither merges autonomously nor deploys production. Existing humans and branch protection retain those decisions.

Additional harness adapters, preview verification and revision-bound merge/release approvals are later capabilities, admitted only after their own readiness and failure tests. Do not run a second general-purpose task scheduler beside Hermes Kanban.

## Implementation Roadmap

All work below is proposed future work; this validation does not authorize or claim it was executed. The timing assumes the narrow scope and available supported extension points.

### Stage 0 — Bounded integration spike (first 1–2 days)

Prove one authorized event/reconciled issue creates one durable task, runs one IDD implementation/review sequence and reports actual PR evidence over Telegram. Reuse supported Hermes lifecycle/PR completion boundaries. Test a restart and duplicate delivery; demonstrate cancellation cannot authorize later side effects.

**Exit gate:** no duplicate PR, no chat-only state, no stale result accepted, and a documented supported extension path. If the gate fails, pause feature development and contribute the missing Hermes primitive or reconsider an existing base. This is the most important next step.

### Stage 1 — Narrow internal MVP (remainder of the 2–4-week window)

Deliver additive setup/readiness diagnostics, one-repo intake/reconciliation, one worker runtime, independent review, existing CI acceptance, PR evidence, Telegram status/cancel and bounded retries. Preserve current human merge policy. No production or universal adapters.

**Acceptance:** on scoped issues, every successful run links the correct current-head PR/check evidence; failures remain blocked or safely retryable; restart/duplicate/cancel tests pass; reinstall and uninstall preserve unrelated files. No safety test may be waived to hit the calendar.

### Stage 2 — Reliability and onboarding pilot

Proposed go/no-go targets, not achieved results or user-approved commitments: run at least 10 bounded real issues across two of the author's projects; have at least three external users complete setup and try repeat work; seek two who continue using it weekly for four weeks. Require no unauthorized merge/deployment in failure tests and fewer manual interventions than the existing workflow. A small pilot cannot establish security or product-market fit, but it can disprove easy-install/reliability claims quickly.

Measure operator time with a comparable task mix; publish failures and limits alongside successes. If integration maintenance exceeds the time saved, consolidate into IDD/Hermes configuration instead of growing a platform.

### Stage 3 — Add one capability at a time

Add one external harness adapter or one preview provider only after the narrow loop is reliable. Next, introduce durable revision-bound human merge approval with one merge owner. Production requires artifact identity, scoped secrets, explicit authorization, health checks and a project-specific recovery plan; do not assume database migrations can be rolled back automatically.

### Stage 4 — OSS distribution and optional services

Publish compatible versions, install/upgrade/uninstall recipes, an explicit support matrix, provenance/security policy and measured case studies. Consider paid setup/support or hosted operations only after external users demonstrate repeated value. Multi-project concurrency, adaptive model routing and autonomous production are not prerequisites for a useful OSS release.

### Research method and source ledger

Live searches covered commercial, OSS, native/adjacent and abandoned alternatives. Initial queries:

- `Warp Oz AI agent orchestration software factory GitHub triggers pricing`
- `Factory Droid GitHub automation skills agent review pricing`
- `GitHub Copilot coding agent issue pull request pricing agent workflow`
- `OpenHands open source autonomous software development GitHub issues license`
- `OpenAI Symphony GitHub issue orchestration agents WORKFLOW.md`
- `Paperclip AI agents orchestration Telegram approvals open source`
- `AI coding agent startup shutdown abandoned GPT Pilot archived smol developer`

Follow-up queries: `site:factory.ai pricing Droid automations GitHub official`; `site:github.com/paperclipai/paperclip adapters approvals budgets MIT`.

Primary sources below were inspected on 2026-10-04. Product features/prices are vendor documentation, not independent performance verification. GitHub `repos/{owner}/{repo}` and `commits?per_page=1` API reads supplied exact license/archive/star/fork/latest-commit snapshots; `open_issues_count` was not treated as issue-only because it includes PRs. Default-branch commit dates were used for activity, not repository push timestamps. GPT Pilot's actual LICENSE was read because GitHub reported `NOASSERTION`.

- **[S1]** [oz-for-oss README](https://github.com/warpdotdev/oz-for-oss) and [architecture](https://github.com/warpdotdev/oz-for-oss/blob/main/docs/architecture.md). API-observed head: `a2bb45f231fd56ea28c1b381999d11b277a0b0e2`.
- **[S2]** [Warp Factories overview](https://docs.warp.dev/factories) — early access, foreman/specialists, definitions, skills, harness choices and infrastructure boundaries.
- **[S3]** [Warp/Oz](https://www.warp.dev/oz) — supported harnesses, routing, visibility and execution options. Animated page counters extracted as zero were not used as traction evidence.
- **[S4]** [Warp pricing](https://www.warp.dev/pricing) — monthly plan starting prices and separate Factories billing/enterprise conditions; annual prices not mixed into monthly comparisons.
- **[S5]** [Factory Automations](https://factory.ai/product/automations) — triggers, templates, execution targets, private-preview webhooks and failure pauses.
- **[S6]** [Factory pricing](https://factory.ai/pricing) — published plans and vendor customer testimonials.
- **[S7]** [OpenHands README](https://github.com/OpenHands/OpenHands) — current Agent Canvas/ACP/backends and repository boundaries. API-observed head: `a6bba78ffd5a8b31620770f52383b1a2c0477fcd`.
- **[S8]** [OpenHands Automation](https://github.com/OpenHands/automation) and [pricing](https://www.openhands.dev/pricing) — beta service responsibilities and local/cloud pricing.
- **[S9]** [Symphony README](https://github.com/openai/symphony) and [specification](https://github.com/openai/symphony/blob/main/SPEC.md) — trusted engineering preview, Apache-2.0, policy and restart boundaries. API-observed head: `be10a1b79df723d6d7612b5651c8522704dafb2e`.
- **[S10]** [Paperclip README](https://github.com/paperclipai/paperclip/blob/master/README.md) — multi-runtime roster, governance and experimental Telegram/chat connectors. API-observed head: `994d6edcdd4e15d5f9cc5cf8c135ac599104b86a`.
- **[S11]** [GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent) — availability, workflow and AI-credit/Actions usage.
- **[S12]** [GitHub Copilot plans](https://github.com/features/copilot/plans) — monthly starting prices and preview third-party agent delegation.
- **[S13]** [Paperclip external adapters](https://github.com/paperclipai/paperclip/blob/master/docs/adapters/external-adapters.md) — built-in Hermes CLI/gateway types and plugin extension contract.
- **[S14]** [GPT Pilot maintenance/security notice](https://github.com/Pythagora-io/gpt-pilot) and [LICENSE](https://github.com/Pythagora-io/gpt-pilot/blob/main/LICENSE) — source-available restrictions, abandonment and maintainer-reported incident. API-observed head: `9b763fdaf0020c7d8abacc7b58b2b09e57494623`.
- **[S15]** [Hermes webhooks](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks) — auth, filtering, coalescing, delivery cache and untrusted-content warning.
- **[S16]** [Hermes Kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban) — durable lifecycle, independent review and PR completion contracts.
- **[S17]** [Hermes worker lanes](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-worker-lanes) — external CLI integration gaps and cooperative-vs-OS isolation boundary.
- **[S18]** [Hermes Codex app-server runtime](https://hermes-agent.nousresearch.com/docs/user-guide/features/codex-app-server-runtime) — optional supported execution and Kanban tool handoffs.
- **[S19]** [Hermes plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) — bundling, extension surfaces, distribution and trust posture.
- **[S20]** [IDD README](https://github.com/luongnv89/idd) — existing public skills, model/effort overrides and installation. API-observed head: `451f4b8101b5f3879935d889a78075d4952fa5bf`; MIT; latest default-branch commit 2026-10-02.
- **[S21]** [Hermes repository](https://github.com/NousResearch/hermes-agent) and [user's skills repository](https://github.com/luongnv89/skills) — API license/activity verification. MIT; observed heads `8b66a51036c1e20920a17cdd049fdf55c968d683` and `1973ed2501e4ba734d57a2c887b65cb7a4686798`, respectively. Each bundled third-party skill still requires individual provenance/license review.
