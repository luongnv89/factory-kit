# Decision record: tested recipe selection (issue #2, Task 1.1)

- **Status:** Accepted for spike use — 2026-10-05
- **Scope:** PRD §9.1 selections Q3, Q5, Q6, Q7, Q10; preserves Q1/Q2 outcomes (A5)
- **Evidence:** `tools/probes/readiness.py` live run →
  `docs/spike/evidence/readiness-2026-10-05.json`; boundary map →
  `docs/spike/hermes-boundary-map.md`; verification contract →
  `docs/spike/verification-contract.md`

## Q3 — tested repository, host, runtime

| Slot | Selection | Basis |
|---|---|---|
| Repository | `luongnv89/money-mind` (public; node ID `R_kgDOQncOdA`; default `main`) | Real React 19/TypeScript/Vite SPA; deterministic demo data (`lib/demoData.ts`, fixed-seed PRNG); existing `vercel.json`; existing `CI Quality Checks` workflow (`Code Quality & Build` + `Security Scan` jobs); no backend/database; trusted maintainer |
| Host | Local operator host `M1` — macOS 27.0.0 arm64, Python 3.14.7, git 2.55.0, gh 2.100.0, Node 26.7.0, npm 11.19.0 | Probe `readings.host`, `readings.runtimes` |
| Runtime (worker) | **Hermes Kanban profile worker** — named profile claimed atomically by the gateway-embedded dispatcher, isolated `scratch`/`worktree`/`dir:` workspace | `kanban.md` §Dispatcher; the only paved worker path |
| Runtime (harness lanes) | claude `2.1.288`, codex `0.160.0`, opencode `2.0.22` present but **not selected** — external CLI lanes are explicitly not a paved integration path | boundary map no-go list |
| Model | Primary available: `openai-codex / gpt-6-luna` (pooled OAuth, `hermes auth status` → logged in). Preferred-when-reachable: `dspark / montimage-dgx-spark` (self-hosted `http://100.117.100.54:8001/v1`, `chat_completions`) — **unreachable at probe time → named blocker `model-unavailable`, correctly denied dispatch** | probe `readings.model`, live verdict |

Role separation: implementation and independent review are separate kanban
tasks/profiles (`assign`, `request-review`), never one session playing both.

## Q5 — scoped credential mechanism

Selection: **scoped existing credentials**, not a GitHub App for the spike.

- GitHub writes run as the **assignee profile's own `gh` login** —
  `GH_CONFIG_DIR`/`GH_TOKEN` inside that profile's `.env`, per the kanban
  acceptance contract; ambient shell tokens are refused for assigned cards
  (`kanban.md` §PR completion contracts). Probe: `gh auth status` →
  `luongnv89`, scopes `repo`, `read:org`, `gist`, `admin:public_key`.
- Preview credentials are confined to the deployment boundary:
  `vercel` CLI login `luongnv89` on the controller host only; no
  `VERCEL_TOKEN` is handed to workers.
- Model auth: Hermes pooled OAuth (`hermes auth list` → `openai-codex`,
  `nous`, `copilot`, `xai-oauth`); worker sees provider indirection, not raw
  tokens. Optional hardening (not required for spike): `hermes secrets`
  (Bitwarden/1Password) and `hermes egress` (iron-proxy token swap).
- **Remaining gap → owner:** a fine-grained GitHub App installation
  (per-repo scope, no bypass) is evaluated only if the scoped-`gh` path
  cannot satisfy F12's non-bypass requirement — owner: Luong; blocks:
  unattended merge automation, not this spike.

## Q6 — language, package, storage, metadata ownership

| Decision | Selection |
|---|---|
| Language | Python 3 (matches Hermes plugin host; stdlib-only helpers follow the `gi-*.py` convention) |
| Package | Native general plugin `factory-kit` at `~/.hermes/plugins/factory-kit/` (also pip-installable later); admission gate `hermes plugins validate`; `register(ctx)` wires tools/CLI commands/hooks |
| Storage | **Hermes owns** task/attempt/fence state in `~/.hermes/kanban.db` — kit touches it only through `hermes kanban` CLI/tools, never direct writes. **Kit owns** registration, delivery-dedup, control-command and approval rows under the profile home, every row referencing Hermes task IDs and incapable of dispatching alone (§6.2/§6.4) |
| Metadata ownership | Repo-reviewed manifest `.factory-kit.yml` + existing `.gitissue.yml` (IDD settings stay there with explicit mapping); manifest schema is **provisional** — owned by the TAD (Q6 due: after spike, before public interfaces) |

## Q7 — model terms and quota visibility

- `openai-codex` runs on a ChatGPT **subscription** (pooled OAuth): no
  per-token meter — quota visibility is `hermes auth status` plus provider
  rate-limit errors surfaced at runtime; subscription terms apply.
- `dspark` is self-hosted: no external terms, quota = endpoint capacity;
  availability is probe-verified each readiness run.
- Usage recording follows F07: measured values keep provider/unit
  attribution; unknown usage is recorded `unknown`, never zero or an
  invented estimate.

## Q10 — preview provider and merge method

| Decision | Selection |
|---|---|
| Preview provider | **Vercel** preview deployments — CLI `55.0.0` authenticated as `luongnv89`; repo ships `vercel.json` (static SPA, security headers, `/typesafe-api` rewrite) |
| Artifact/revision identity | `vercel inspect`/deployments API → deployment ID + URL bound to PR head SHA |
| Smoke | `curl -fsS -o /dev/null -w '%{http_code}' <preview-url>` → `200`, plus marker grep (contract: verification-contract.md) |
| Cleanup | ≤24 h TTL per preview, cleanup ≤60 min after task termination (F11 defaults) |
| Merge method | **Squash merge via `gh pr merge --squash`** by the human-approved merge owner with expected-head recheck; repo permits squash (`allow_squash_merge: true`), auto-merge disabled (`allow_auto_merge: false`) |

**Amendment (2026-10-06) — declared no-preview contract.** A project with
nothing to deploy (factory-kit itself, a library) may declare
`endpoint.preview: {provider: none}` — that key alone; any other preview
key is rejected. It is reviewed policy bound into the effective/policy
digests, never inferred from a missing preview row: approval binds the
constant `schema.NO_PREVIEW_BINDING`, the merge guard accepts it only when
the bound config says `none` and no preview row exists, and every other
combination fails closed (`preview-unexpected`, `preview-contract-mismatch`,
`preview-moved`). Approval and merge then rest on the required checks and
the independent review alone. Pilot admission (`privacy/consent.py`) still
requires a smoke-verified preview. Tests: `tests/test_config_no_preview.py`,
`tests/run/test_no_preview.py`.

## Unresolved selections — owners and blocking relationships

| Open item | Owner | Blocks |
|---|---|---|
| Vercel project linkage for `money-mind` (no GitHub-deployments entries observed; `vercel link`/GitHub integration unproven) | Luong | §8.1 "select preview provider … prove revision-to-deployment identity" demo; A4 full endpoint |
| `main` branch protection on money-mind — required checks `Code Quality & Build` + `Security Scan`, strict up-to-date, `enforce_admins` | Luong | Merge endpoint demo (probe blocker `base-unprotected`) |
| dspark endpoint availability | Operator | Nothing — fallback `openai-codex` is authenticated; re-run probe to re-enable |
| Telegram numeric actor allowlist values for the approval chat | Operator (already configured keys present) | F05/F12 live demo |

## A5 — preserved decisions and separations

- **Confirmed endpoint (Q1, 2026-10-04):** preview verification plus
  revision-bound **human-approved merge** — the merge endpoint is GitHub's
  human merge path (`gh pr merge`), never autonomous merge or GitHub
  auto-merge.
- **Privacy scope (Q2, 2026-10-04):** internal use, concrete privacy
  controls, **no specific compliance commitment**; external obligations are
  a pre-pilot gate. Previews carry only deterministic synthetic data
  (`lib/demoData.ts`); no real financial data leaves the repo.
- **Provisional install syntax:** `hermes plugins install factory-kit` /
  `factory-setup` names are **unsupported until implemented and tested** —
  no install command is advertised (§6.2 distribution).
- **Separation:** role (implementer/reviewer), runtime (Kanban profile
  worker), model (`openai-codex`/`dspark`) and skills (pinned IDD bundle)
  are independent selections; **live steering (F09) and additional
  adapters (Sprint 5) are optional and out of this recipe's scope**.
