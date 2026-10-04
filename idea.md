# Idea: factory-kit — Installable AI Engineering Factory

- **Captured:** 2026-10-04
- **Status:** validation completed (2026-10-04); verdict **Maybe** — [assessment and proposed roadmap](validate.md); not implemented.
- **Working name:** `factory-kit` (chosen by Luong).
- **Inspiration:** an AI factory diagram shared by Luong and attributed to Warp (warp.dev). Attribution is user-provided, not independently verified. See the [reference diagram](assets/warp-ai-factory-reference.jpg).

## Original Concept

An installable, skills-driven AI engineering factory for new and existing GitHub projects: GitHub webhooks initiate triage, issue resolution, PR review, merge, preview and production releases. Hermes coordinates work and communicates with the human over Telegram. Reuse IDD and the existing skills repository; allow existing host harnesses and role-specific models through adapters.

## Clarified Understanding

The pain is reliable coordination across development stages, not a lack of another coding agent. The kit must preserve existing project workflows, survive worker/host restarts, verify evidence, and allow bounded human steering. GitHub remains the source of truth for code and delivery; Telegram is a control surface, not the workflow database.

The intended differentiator is the integrated Hermes + Telegram + IDD experience, local harness reuse, and project-preserving setup. Harness flexibility, self-hosting, GitHub automation, and skills alone are not assumed unique; competitive research must test these claims.

## Target Audience

Start with Luong maintaining multiple GitHub projects using IDD, reusable skills, and local coding harnesses. The first external segment is similarly equipped solo developers and small OSS-maintainer teams, not enterprises seeking a managed platform.

## Goals & Objectives

**Confirmed user goal (6–12 months):** “Open-source kit used reliably in my projects, then adopted by other developers.”

Proposed validation milestones, not promises or user-confirmed numerical targets:

- Demonstrate repeatable, bounded issue-to-reviewed-PR delivery in an existing project.
- Measure operator time and recovery failures against the current IDD/manual workflow.
- Establish safe revision-bound approvals before enabling automated merge or production.
- Make installation reproducible outside the author's machine before seeking wider adoption.

## Technical Context

- **Stack:** Hermes for orchestration/Telegram; GitHub for source and CI; reuse IDD and existing skills. A thin supported plugin/integration is preferred over a second scheduler. Implementation language, manifest and deployment provider remain proposals.
- **Timeline / team (confirmed):** “Solo builder; a narrow MVP in 2–4 weeks.”
- **Budget / approach (confirmed):** “Bootstrapped side project; extend Hermes and reuse IDD/skills, with existing subscriptions.” No cash ceiling or proven per-task cost was supplied. Existing subscriptions do not establish unlimited usage or permission for every automated workload.
- **Assets:** the brainstorm below, IDD, the skills repository, a configured Hermes/Telegram host, and locally discovered harness executables. Discovery is not authentication/readiness proof.
- **Constraints:** narrow one-repository/one-harness scope; preserve existing CI and worktrees; durable evidence and bounded retries; no production secrets in implementation workers; no universal harness support promise.
- **Unresolved feasibility gate:** prove supported Hermes extension points can durably ingest/reconcile events and enforce approvals. External CLI worker lanes are not yet a paved integration path according to the linked Hermes documentation.
- **MVP boundary:** the earlier preview-plus-merge flow remains a recommendation, not an explicit product-policy choice. A verified-PR stop is a smaller candidate for the 2–4 week assessment.

## Discussion Notes

The original brainstorm is preserved below. Confirmed validation inputs were recovered from the user's submitted clarification responses; competitive findings and final recommendations are recorded in [validate.md](validate.md). The validated next step is a bounded reliability spike before committing to the full platform; the original expansion plan below remains historical design discussion.

**Confirmed direction after the overlap check (2026-10-04):** “Continue as a Hermes-native, Telegram-first, local kit reusing IDD and existing harnesses; avoid rebuilding generic factory infrastructure.” This selects the differentiation hypothesis, not permission to implement or proof of adoption.

### Summary

Build an installable engineering workflow for new and existing GitHub projects. GitHub events initiate work; Hermes orchestrates and monitors the factory; Telegram provides the communication and supervision channel. Workers reuse IDD and the existing skills repository to triage issues, implement changes, review PRs, verify results, merge, deploy previews, release to production, and investigate operational failures.

The factory must be harness-agnostic through explicit adapters, with a usable default setup and configurable harness/model choices per task and role. It should support both human-directed steering and bounded autonomous decisions.

The product is **a dependable engineering workflow installed into a project**, not a new coding harness or an unbounded swarm of agents.

### Original Intent

Luong proposed:

- Reuse the existing [IDD repository](https://github.com/luongnv89/idd) and [skills repository](https://github.com/luongnv89/skills).
- Make the factory easy to install into any new or existing project, ideally through a dedicated setup skill.
- Use Telegram as the human communication channel.
- Support existing harnesses on the host, with a default configuration and different models for different roles/tasks.
- Trigger triage, resolution, PR review, merge, preview releases, production releases, and related stages from GitHub webhooks.
- Let each stage use the existing skills it needs.
- Use Hermes for orchestration, monitoring, status reporting, and steering workers with or without fresh human instructions.

### Problem

Existing coding agents and skills can execute individual development tasks, but connecting them into a reliable unattended pipeline requires additional machinery:

- Work must survive chat resets, context compression, worker exits, and host restarts.
- Duplicate or out-of-order events must not create duplicate PRs or deployments.
- Implementation, review, CI, and deployment evidence must refer to the same code revision.
- Workers need isolated workspaces and narrowly scoped credentials.
- Human approval must be durable, attributable, and specific to an action and revision.
- Existing projects need additive setup rather than replacement of their CI, release tooling, or conventions.
- Operators need one place to understand what is running, why something is blocked, and how to intervene.

### Target Users

Initial target: a solo developer or small team maintaining several GitHub projects and already using coding agents and skills.

Later candidates include open-source maintainers and teams that want standardized, auditable agent workflows without committing to a single harness or model provider.

### Product Boundaries

Separate the system into three parts:

1. **Factory runtime:** receives events, maintains execution state, dispatches and monitors workers, enforces policy, and recovers interrupted work.
2. **Skills:** encode role-specific methods, including IDD workflows and specialist development/review skills.
3. **Project configuration:** defines repository routing, roles, harness/model choices, verification commands, deployment adapters, and autonomy policy.

The setup skill installs and configures these parts. It is not itself the long-running runtime. A conversation transcript must not be the only record of an active factory.

### Proposed Architecture

Use Hermes as the control plane and evaluate its existing durable Kanban system as the execution backbone. Add a thin factory integration/plugin instead of immediately building a second scheduler.

```text
GitHub webhook events                  Human on Telegram
          |                                    |
          +-----------------+------------------+
                            |
                 Hermes factory controller
                 - intake and event routing
                 - permissions and policy
                 - status and steering
                 - approvals and escalation
                            |
                   Durable task board
                            |
              +-------------+-------------+
              |             |             |
           Triage       Implementation  Review / QA
           worker          worker         worker
              +-------------+-------------+
                            |
                     GitHub PR and CI
                            |
                Preview deploy and verification
                            |
                     Merge policy gate
                            |
               Staging / production promotion
                            |
                    Health verification
                            |
                 Incident or follow-up issue
                            +-----------> intake
```

#### Sources of Truth

- **GitHub:** issues, PRs, code revisions, required checks, releases, and deployment records.
- **Factory task store:** execution attempts, claims, stage state, handoffs, approvals, and audit evidence.
- **Project manifest:** declared configuration and policy.
- **Telegram:** a human interaction surface, not the workflow database.

Define ownership clearly so factory state and GitHub state can be reconciled without two independent schedulers competing over the same work.

#### Existing Foundation and Known Gaps

During the brainstorm, the local IDD skills and official Hermes documentation were inspected:

- IDD provides issue creation/normalization, analysis, triage, resolution, PR review, and an auto-pilot loop. Its README describes these public workflows ([IDD README](https://github.com/luongnv89/idd#readme)).
- Hermes documents GitHub-compatible webhook signature validation, event filtering/coalescing, and Telegram delivery ([webhooks](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks)).
- Hermes documents a durable Kanban board with worker processes, review handoffs, attempt history, retries, and completion contracts ([Kanban](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban)).
- Hermes documents a worker-lane contract, but external CLI lanes are explicitly not yet a paved integration path ([worker lanes](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-worker-lanes)).
- Local executable discovery found Hermes, Claude Code, Codex, Pi, OpenCode, and Grok on PATH. This was an availability check, not authentication or end-to-end readiness verification. The installer must rediscover and test the target host rather than assume this roster.

Existing primitives are evidence of feasibility, not proof that the complete factory already exists. The integration, execution policy, deployment adapters, and recovery behavior still need implementation and testing.

### Roles, Harnesses, Models, and Skills

Keep four concepts separate:

- **Role:** the responsibility, such as implementation or review.
- **Harness:** the program executing the worker.
- **Model:** the selected model/provider, subject to that harness's capabilities.
- **Skills:** the approved procedures and specialist knowledge available to the worker.

A role must not be hard-wired to one vendor. Task-level overrides can refine role defaults, subject to project policy and verified model availability.

#### Candidate Role Mapping

- **Intake and triage:** `issue-creator`, `issue-triage`, and `issue-analysis`.
- **Specification:** refine acceptance criteria; use `prd-generator`, `tad-generator`, and `tasks-generator` only when the scope warrants them.
- **Implementation:** `issue-resolver` plus stack-specific development skills.
- **Review:** `issue-pr-review`, code review, security checks, and UI/UX review where applicable.
- **Verification:** tests, lint, type checks, builds, acceptance-criteria checks, and preview smoke tests.
- **Release:** defer to the project's existing release tool; use `release-manager` when appropriate.
- **Monitoring and incident response:** deterministic health signals, with an agent investigating exceptions and creating bounded follow-up work.

Load only the skills relevant to the task. Pin or record skill versions for reproducibility. Do not automatically execute newly discovered third-party skills without an approved installation policy.

#### Default Execution Path

Recommended starting point:

- Hermes controller and Hermes workers.
- Explicit per-role model configuration rather than fabricated or assumed model IDs.
- Optional external-harness adapters added incrementally.
- Independent implementation and review sessions; using a different review model/harness is an optional stronger policy.

The current IDD adaptation for Hermes must account for leaf subagents that cannot re-delegate: use an appropriate inline pipeline or full worker process rather than assuming nested Agent tools exist.

#### Harness Adapter Contract

Each adapter should expose:

- Readiness checks: executable, version, authentication, supported models, and required tools.
- Start and identify a worker run.
- Report status and heartbeat/liveness.
- Collect structured results, logs, and artifact references.
- Cancel a run and account for descendant processes.
- Resume or steer when supported; otherwise checkpoint and restart with revised instructions.
- Report capabilities honestly, including sandboxing and structured-output support.

Suggested task inputs include project/repository identity, role, issue/PR, revision, acceptance criteria, allowed skills, workspace, permissions, resource limits, and prior evidence. Outputs include a verdict, summary, revision, PR/artifact references, verification evidence, blockers, and measured usage where the harness exposes it.

A zero exit code or a worker's self-report is not sufficient evidence of task completion.

### Workflow and State

The normal path is a proposed state machine, not one giant prompt:

```text
Received -> Triaged -> Ready -> Implementing
-> Reviewing -> Verifying -> Ready to merge
-> Merged -> Production approved -> Deployed -> Healthy
```

Preview deployment and verification belong before merge when the project supports them. Some projects also need shared staging after merge.

Alternative states include:

- Needs clarification or specification approval.
- Blocked by dependency or external prerequisite.
- Changes requested.
- Parked or canceled.
- Failed or quarantined.
- Deployment unhealthy or rollback required.

#### Evidence Gates

Every transition requires checkable evidence appropriate to that stage: a linked issue, PR URL, commit SHA, review verdict, test/check result, deployment ID, artifact identity, or health-check result.

- Bind review, CI, preview verification, and approvals to the exact commit SHA.
- Invalidate stale evidence when new commits arrive.
- Recheck the current PR head and required checks immediately before merge.
- Promote the verified immutable artifact to production instead of rebuilding an unverified variant.
- Read back remote state after merges, releases, and deployments.
- Distinguish application failures from unavailable infrastructure; unknown evidence is not a passing result.
- For unsupported project stages, declare not applicable explicitly rather than claiming a deployment happened.

#### Single Merge Owner

The controller/policy gate owns merge decisions. Workers produce evidence; they do not independently race to merge.

IDD's current auto-pilot defaults to balanced mode, which merges clean PRs. Factory integration must explicitly align or suppress that behavior when the factory requires human merge approval. Avoid running a full autonomous backlog loop for every incoming webhook.

### GitHub Event Intake

Candidate events include issue creation/edits/labels, PR creation/updates/reviews, check and workflow completion, merges, and deployment status changes.

Intake should:

- Authenticate the sender and verify repository/project routing.
- Require opt-in policy for issue execution, such as a factory label applied by an authorized actor.
- Deduplicate deliveries durably and identify superseded work.
- Coalesce rapid updates without losing required events.
- Persist accepted work before acknowledging it as safely queued.
- Reconcile GitHub periodically to recover missed events and host downtime.
- Ignore irrelevant events and prevent feedback loops from factory-generated comments/issues.

Webhook authenticity does not make issue text, PR content, or repository files trusted instructions. Authorization comes from policy, not from prose inside a payload.

A scoped GitHub App is a candidate for durable installation/authentication; a narrowly scoped existing credential may be a simpler MVP path. The choice remains open.

### Telegram Control and Supervision

Support natural-language requests that resolve to validated, auditable actions:

- "What is running across all projects?"
- "Why is issue #42 blocked?"
- "Pause this project after the current step."
- "Stop this worker now."
- "Use Codex for the next implementation attempt."
- "Reduce the scope to the API change."
- "Approve production for this release."

Distinguish pause-at-boundary from immediate cancellation. Steering must honor harness capabilities: checkpoint/restart when live steering is unavailable.

Persist every accepted control action with its authorized actor, project, run, target revision, and outcome. Approval buttons/messages must bind to a specific action and revision; an old approval must not authorize a newer deployment. Human steering cannot silently bypass branch protection or grant broader credentials.

#### Notification Defaults

Notify on meaningful stage completion, blockers, approval requests, failures, budget limits, deployment results, and unhealthy production signals. Provide detailed logs on demand rather than streaming every tool call.

Hermes should explain blockers and choose safe bounded recovery without a fresh human instruction where policy permits. Escalate when recovery changes scope, requires new permission, or exhausts its budget.

### Installable Project Kit

#### Runtime / Plugin Package

Own the controller integration, harness adapters, GitHub intake, Telegram commands, policy enforcement, and deployment adapters. Prefer supported Hermes extension points over changes to Hermes core.

#### Setup Skill: `factory-setup` (proposed name)

The dedicated setup skill should:

1. Inspect the project, existing IDD config, CI, release tooling, and repository conventions.
2. Detect and verify host harnesses and their available capabilities.
3. Propose role defaults, autonomy policy, and integration choices.
4. Show an installation plan/diff before sensitive or remote changes.
5. Install dependencies and configure selected integrations after approval.
6. Exercise the setup end to end with bounded test work.
7. Produce a readiness report and record unresolved prerequisites honestly.
8. Support idempotent reruns, upgrades, repair, and uninstall.

Existing-project setup must preserve working files and existing workflows. New-project setup may offer scaffolding but must not silently impose a stack. Shared host infrastructure should be reusable across multiple registered projects without duplicating gateways.

#### Project Manifest

Keep `.gitissue.yml` as the IDD configuration. Use a separate factory manifest, tentatively `.factory-kit.yml`, for factory-specific settings:

- Project identity and repository routing.
- Telegram destination and authorized operators.
- Event subscriptions and execution opt-in policy.
- Role/harness/model mappings and task override rules.
- Skill availability and version policy.
- Verification commands and required evidence.
- Merge/release/deployment policy and adapters.
- Concurrency, retries, runtime, and spending limits.
- Monitoring and escalation behavior.

The filename/schema is a proposal, not a supported command or implemented interface. Keep credentials in approved secret stores, never in the committed manifest.

### Recommended Initial Autonomy

These defaults were proposed in the brainstorm and still need explicit product-policy confirmation:

- **Automatic:** triage, implementation of opted-in issues, bounded review/fix loops, verification, and approved preview deployment workflows.
- **Human-gated initially:** merge.
- **Human-gated by default:** production deployment, package publication, destructive operations, and permission changes.
- **Optional later:** automatic merge of low-risk work with current independent review and all required checks passing.

Missing or expired approval is a durable waiting state, not permission to proceed. Approval must be possible later without keeping the original chat or worker alive.

### Reliability and Security Requirements

- Durable accepted-event/task state, atomic claims, bounded retries, and reconciliation after crashes.
- Superseded-run fencing so an old worker cannot complete a new attempt.
- Isolated worktrees or sandboxes; never stash or rewrite the developer's working tree as a routine factory action.
- Dependency-aware scheduling and serialized/conflict-aware merge handling.
- Per-project and per-role credentials; implementation workers do not receive production secrets.
- Untrusted external contributions run without privileged secrets. Repository code and tests are executable input, not merely harmless text.
- Authorized Telegram operators and revision-bound approval records.
- Bounded runtime, concurrency, retry/fix cycles, and spending. Usage figures are measured where available, never invented.
- Secret-redacted logs and minimal sensitive data in durable task metadata.
- Health verification and project-specific rollback procedures. Rollback is not universally automatic: schema migrations and irreversible external side effects may require a human recovery plan.
- Incident deduplication and quarantine to prevent an endless alert -> issue -> failed fix loop.

### MVP and Expansion

#### First Working Loop

One repository, one default worker harness, one opted-in issue at a time:

> GitHub issue -> IDD resolution -> independent PR review -> CI -> preview verification -> Telegram merge approval -> merge.

Projects without meaningful preview support use an explicit alternate verification contract. Production release is a later milestone rather than silently included in the first merge approval.

#### Acceptance Criteria for the First Loop

- Install into an existing project without replacing its CI or modifying unrelated work.
- An opted-in issue produces one linked PR despite duplicate webhook delivery.
- Independent review and required checks refer to the current PR revision.
- Preview evidence is available when applicable.
- Telegram reports state, blockers, and an actionable revision-bound approval.
- Pause/cancel prevents unauthorized later transitions.
- A restart during work recovers or safely parks the run without duplicate publication.
- Merge is performed only by the authorized owner and verified by reading GitHub state.
- Logs explain what happened and what remains unresolved.

#### Subsequent Milestones

1. Production artifact promotion, deployment health verification, and recovery handling.
2. Additional harness adapters and role/model routing.
3. Parallel implementation with conflict-aware review/merge serialization.
4. Multiple projects, richer policies, and measured optimization of model cost versus delivery quality.

Do not add multi-harness parallelism before the single-project recovery and approval paths are proven.

### Open Decisions

- Default product boundary: verified PR only, or preview plus merge approval? The brainstorm recommended the latter, but no separate explicit choice was made.
- Whether factory-kit is a standalone package/repository with a Hermes plugin, and how much belongs in IDD versus an integration layer.
- Whether Hermes Kanban satisfies all required event-durability and policy boundaries through supported extension points.
- GitHub App versus scoped existing credentials for the MVP.
- Initial deployment provider/project type and its immutable-artifact promotion path.
- Concrete default model selections, based on supported harness authentication and actual availability.
- Exact names for the setup skill, manifest, and command surface.
- Preview credential/trust policy for external contributors.
- Budget enforcement when a subscription harness does not expose dollar-denominated usage.
- Skill dependency/version distribution across different harnesses.

### Next Step

Resolve the MVP boundary and default policy, then produce a PRD and technical architecture document. Before committing to a custom scheduler, run a bounded integration spike using Hermes webhooks, its task board, an IDD worker, and Telegram approval. This document records the concept; it does not authorize installation, repository-policy changes, deployment, or publishing.

### Related Ideas and References

- [GitIssue / IDD idea](https://github.com/luongnv89/ideas/blob/main/ideas/2026_03_19_gitissue_idd/idea.md).
- [Skill Server idea](https://github.com/luongnv89/ideas/blob/main/ideas/2026_05_30_skill_server/idea.md).
- [IDD repository](https://github.com/luongnv89/idd).
- [Existing skills repository](https://github.com/luongnv89/skills).
- [Hermes webhook documentation](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks).
- [Hermes Kanban documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban).
- [Hermes worker-lane contract](https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-worker-lanes).
