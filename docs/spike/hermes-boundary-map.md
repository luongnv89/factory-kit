# Hermes supported-boundary map

Spike deliverable for issue #2 (PRD §8.1 stage-0 gates; requirement surface
ARCH-O01–O08, STATE01/02, GATE-S01/S02/S07). This map binds every PRD §6.1
owner and every §6.4 record/state to a **documented, observed** Hermes
interface, and records the gaps that are no-go for product features until a
supported primitive exists.

**Rule of the map:** an entry is `supported` only with a cited interface that
exists in the pinned install; `conditional` means the extension point exists
but the recipe's specific use is unproven; `no-go` means nothing supported
covers it and a second scheduler/transition engine must not be invented.

## Pinned recipe versions (probed 2026-10-05)

| Component | Version | Evidence |
|---|---|---|
| Hermes Agent | `v0.21.5+6718.g158fd63` (upstream `158fd638`) | `hermes --version`; `tools/probes/readiness.py` floor `0.21.5` |
| IDD / skills bundle | `v0.23.1-2-gd6a908f` (local `~/buildspace/luongnv89/idd`); installed `issue-resolver` skill `0.19.0` | `git describe`; `~/.agents/skills/*/SKILL.md` frontmatter |
| Hermes install | `~/.hermes/hermes-agent` | plugin dirs below |
| GitHub CLI | `2.100.0` | `gh --version` |
| Vercel CLI | `55.0.0` (authed `luongnv89`) | `vercel whoami` |

Live probe evidence: `docs/spike/evidence/readiness-2026-10-05.json`.

## §6.1 owner → supported interface

| §6.1 concern (owner) | Supported interface | Evidence | Status |
|---|---|---|---|
| Issues, commits, PR head/base, checks — **GitHub authoritative** | `gh` CLI + REST reads; Kanban `complete_task` gate re-reads branch protection, required contexts and exact-head check runs before card completion | `hermes-agent/website/docs/user-guide/features/kanban.md` §"PR completion contracts"; `hermes kanban … --completion-contract OWNER/REPO` | supported |
| Tasks, claims, attempts, generation fencing, transitions — **Hermes durable lifecycle** | Hermes Kanban: `~/.hermes/kanban.db` (SQLite), `hermes kanban create/claim/block/complete/reclaim`, gateway-embedded dispatcher (`kanban.dispatch_in_gateway`) reclaims stale/crashed claims; terminal writes recheck run/status/contract ownership under one SQLite lock | `kanban.md` §"Two surfaces" + §"Dispatcher"; `hermes kanban --help` verb list (45 verbs observed) | supported |
| Factory routing, authorization, evidence rules — **thin integration/plugin** | Native general plugin: `register(ctx)` with `ctx.register_tool` / `ctx.register_cli_command` / lifecycle hooks (`on_session_start/end`, `pre/post_tool_call`); discovery at `~/.hermes/plugins/<name>`; admission gate `hermes plugins validate`; optional `plugins.isolation: host` process isolation | `plugins/AGENTS.md` (plugin kinds + discovery); `hermes plugins --help` | supported |
| Roles and development method — **pinned IDD/skills** | Per-task `--skills` on `hermes webhook subscribe`; skill dirs under `~/.agents/skills/` with `version:` frontmatter; kanban `assign`/`set-model` pin role/model per task | `hermes webhook subscribe --skills`; installed skill frontmatter versions (probe `readings.idd.skills`) | supported |
| Project configuration — **repo-reviewed manifest + `.gitissue.yml`** | `.gitissue.yml` is IDD's existing per-repo config; `.factory-kit.yml` is **provisional only** (see decision record A5) | IDD `docs/config-schema.md`; §6.3 marks filename provisional | supported (.gitissue.yml) / provisional (.factory-kit.yml) |
| Commands and notification delivery — **Telegram/Hermes transport** | Telegram platform adapter plugin `plugins/platforms/telegram/`; gateway `authz_mixin` + `TELEGRAM_ALLOWED_USERS` numeric-actor allowlist; runner intercepts `/stop` `/approve` `/deny` mid-run; `hermes send`; `kanban notify-subscribe` | `plugins/platforms/telegram/`; `gateway/AGENTS.md` (two message guards, slash table); `~/.hermes/.env` keys present | supported transport; typed **factory** actions = conditional (see gaps) |
| Preview evidence — **provider-owned identity** | Provider deploy (Vercel) owns deployment ID/URL; kit records revision+URL+smoke into kanban task evidence (`kanban attach`/`comment`, `pr_acceptance`-style events) | `vercel.json` in money-mind; `hermes kanban attach/comment/show --json` | supported (provider) + kit-owned record rows |
| Merge authority — **one guarded owner + GitHub protections** | Human-approved `gh pr merge --squash` (expected head) executed by controller identity; Kanban `complete_task` enforces required-check presence before *card* completion; GitHub rulesets supply enforcement | `kanban.md` §"PR completion contracts" (exact-head check pagination, `pr_acceptance` events) | supported once `base-unprotected` blocker cleared |

## §6.4 required records → supported homes

"Reuse upstream fields rather than creating parallel tables" (§6.4). Kit-owned
rows are restricted to identity/authorization metadata that references Hermes
task IDs and can never dispatch by itself.

| §6.4 record | Supported home | Evidence | Status |
|---|---|---|---|
| Registration | Kit-owned: per-profile JSON under the plugin dir (`~/.hermes/factory-kit/` for `default`, `~/.hermes/profiles/<p>/factory-kit/` for named profiles, or repo `.factory-kit.yml` digest) | plugin discovery paths (`plugins/AGENTS.md`) | supported (kit-owned) |
| Accepted work (repo+issue+generation key, delivery refs, task ID) | Kanban task row (`create`) carrying the logical key in title/description; **delivery-ID dedup set is kit-owned** (no kanban unique constraint on external IDs) | `hermes kanban create/show --json` | conditional — dedup binding is kit storage, proven in Task 1.2/1.3 |
| Attempt (task, monotonic fence generation, role, workspace, model, heartbeats, limits) | Kanban task + run/attempt rows; atomic `claim`; dispatcher reclaim of stale claims; `heartbeat`; workspace modes `scratch`/`worktree`/`dir:` | `kanban.md` §"Core concepts"; `hermes kanban claim/heartbeat/runs` | supported |
| Evidence (PR, head/base, review identity, checks, observation time) | `pr_acceptance` durable events + `attach`/`comment`; `kanban_show --json` exposes persisted contract | `kanban.md` §"PR completion contracts" | supported |
| Control (command identity, actor, target, action, outcome) | Kit-owned command log referencing task ID; gateway provides actor identity at admission (`authz_mixin`), slash commands emit receipts | `gateway/AGENTS.md` authz + slash table | conditional — kit table, proven in Task 1.3 |
| Publication intent (stable op ID, target, expected revision, outcome) | Kanban `--completion-contract OWNER/REPO` + `pr_acceptance` events (URL, SHA, required contexts, classifications) | `kanban.md` §"PR completion contracts" | supported |
| Preview evidence (deployment/artifact ID, head/base, URL, smoke result, cleanup owner) | Kit-owned row + kanban task evidence attachment; provider ID comes from `vercel inspect` / deployments API | provider contract (verification-contract.md) | conditional — kit row + Task 1.3 demo |
| Approval (one-use IDs, actor, repo/PR, digests, expiry, consumed/revoked) | **No first-class one-use token primitive observed.** Compose on kanban `request-review`/review handoff + kit-owned approval table; atomic consume must be proven under the complete_task SQLite lock | gaps §below | **conditional → no-go if unproven at Task 1.3** |
| Merge outcome (approval/intent ID, method, merged flag, merge SHA, read-back) | GitHub read-back + kanban `pr_acceptance`/`complete` evidence rows | `kanban.md`; `gh pr view --json mergedAt,mergeCommit` | supported |

## Logical states → kanban states (no second transition engine)

| §6.4 state | Kanban mapping |
|---|---|
| received | webhook subscription intake (`hermes webhook subscribe`, HMAC secret, per-profile route `/p/<profile>/webhooks/<name>`) → pre-task intake log |
| queued | `todo` → dispatcher promotes to `ready` when links clear |
| needs-clarification | `triage` (verb list) or `blocked` with reason |
| implementing | `claim` → `running` (assigned profile, isolated workspace) |
| reviewing | `review` (`request-review` handoff, reviewer = separate profile/worker) |
| verifying / verified | `running`/`review` + `pr_acceptance` durable events (evidence, not a state) |
| previewing | task `running`/`blocked` + preview evidence rows (kit metadata) |
| awaiting-approval | `blocked` with approval-request metadata; durable across restart (SQLite) |
| merging / merged | `complete` under `complete_task` gate; merge observed via GitHub read-back |
| paused | `block` (fences the generation) + `schedule`/`unblock` for resume |
| canceled | `block` + terminal cancel reason; reclaim fences late workers |
| blocked / failed | `blocked` / auto-blocked after `kanban.failure_limit` |
| quarantined | `blocked` + `kanban diagnostics` entries + operator notification |

## Required lifecycle operations (A1 mapping)

| §6.5 operation | Interface | Status |
|---|---|---|
| readiness | `tools/probes/readiness.py` + `hermes auth status`, `--version`, gateway status | supported (this probe) |
| start | `kanban create` + atomic `claim` / dispatcher `dispatch` | supported |
| liveness | `kanban heartbeat`, `runs`, `tail`, `watch`, `stats` | supported |
| structured result | `kanban complete/block/request-review` + `attach`; `pr_acceptance` events | supported |
| cancel incl. descendants | `kanban block` (fence write) + `reclaim` (worker termination by dispatcher); cancellation acknowledgment = fence commit | supported |
| resume / live steering | optional capability — `unblock`/`reclaim`/`reassign` exist; live steering is **Should**, deferred | optional |

## Telegram typed actions

Supported chain: Telegram adapter (`plugins/platforms/telegram/`) → gateway
inbound → `session_identity.resolve_identity` → `authz_mixin` (numeric
`TELEGRAM_ALLOWED_USERS`) → slash-command table (`_command_handler_table`;
`_PLAIN_COMMANDS` works mid-run: `/stop` `/approve` `/deny` exist for
*execution* approvals). **Gap:** no factory-domain typed action (e.g.
`approve-merge <task>`) ships; the documented extension point is a
`gateway/builtin_hooks/` hook (currently none shipped) or a plugin-registered
tool invoked by the agent. Recorded **conditional**: Task 1.3 must demonstrate
a durable typed action before relying on it — natural-language interpretation
may never bypass authorization (F05).

## Named gaps / no-go list

| Primitive | State | Disposition |
|---|---|---|
| One-use durable approval token bound to action/revision/evidence digest | Not observed as a first-class primitive | **conditional → no-go** if Task 1.3 cannot prove atomic consume-before-merge within the kanban lock |
| Factory-domain Telegram typed action | Extension point exists (`builtin_hooks`, plugin tools); nothing shipped | conditional — must be demonstrated |
| External CLI worker lanes (claude/codex/opencode as workers) | **Not a paved path** per Hermes docs; only `hermes codex-runtime` app-server migration is documented | **no-go** — worker runtime is the Kanban profile worker |
| Remote/shared `kanban.db` over network | Docs state claim races; not supported | **no-go** — single local host |
| Second scheduler / transition engine | Forbidden by §6.1 | **no-go** — gateway-embedded dispatcher + `hermes cron` reconciliation only |
| `.factory-kit.yml` manifest | Filename provisional (§6.3) | provisional — schema owned by TAD (Task 2.x) |

## Boundaries that must not be rebuilt

- **Scheduler:** the gateway-embedded Kanban dispatcher is the only task
  scheduler; periodic reconciliation runs as `hermes cron` jobs.
- **Transition engine:** state changes go through kanban verbs only; kit rows
  record metadata and evidence, never task state.
- **Credential broker for workers:** assignee-profile `gh` identity
  (`GH_CONFIG_DIR`/`GH_TOKEN` in the profile `.env`) is the documented
  acceptance identity; the ambient login is refused for assigned cards.
