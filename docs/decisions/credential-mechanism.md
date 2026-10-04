# Decision record: credential mechanism at the effect boundary (issue #3)

- **Status:** Accepted for spike use — 2026-10-05
- **Scope:** confirms and sharpens the Q5 selection
  (`docs/decisions/tested-recipe-selection.md`) with the executable
  evidence from `tools/probes/fenced_effects.py` /
  `docs/spike/evidence/fenced-effects-2026-10-05.json`
- **Issue acceptance:** A1 (worker surface), A4 (secrets outside manifest,
  canary discipline, selected transports)

## Selection

**Scoped existing credentials behind a deterministic boundary** — unchanged
from Q5; this record pins down *who may hold which credential*:

| Principal | Credential surface | Evidence |
|---|---|---|
| Worker + descendants | A worker-scoped token for its own lane only (`FK_WORKER_TOKEN` analogue). **No** `GH_TOKEN`/`GH_CONFIG_DIR`/`GITHUB_TOKEN`, preview (`VERCEL_*`), Telegram, controller, or provider credentials | `credential-surface` scenario: env census of a real subprocess; `tests/test_fenced_effects.py` |
| Broker (boundary process) | Scoped capability token for the remote (fixture) → maps to the assignee profile's `gh` login / provider login on the controller host in production (Q5) | `scoped-publication`, `write-type-matrix` |
| Manifest | Secret **references** (`secrets/<name>`), never values; files `0600`, dir `0700` | `canary-hygiene` scenario |

Rules proven executable:

1. **Publication crosses the boundary or not at all.** A token-less handle
   refuses every privileged op, and the state file's `0400` mode refuses a
   raw bypass write. Worker-scoped credentials cannot widen into remote
   authority — scoping is enforced at the effect, not by convention.
2. **Untrusted text is never policy.** Request `notes`/`payload` carrying
   instruction-shaped content cannot alter authorization — the envelope
   (repo/actor/generation/op) is the entire decision input
   (`prompt-text-no-policy`).
3. **Denials are auditable without leaking.** Rejected intents persist
   redacted `audit` rows: a planted `ghp_…` canary survives only as
   `<redacted:github_token>`; exports and notification outbox stay clean
   (`canary-hygiene`).
4. **Transport assumptions are recorded, not implied:**
   `github = scoped-gh-profile`, `telegram = gateway-authz+numeric-allowlist`,
   `provider = vercel` — the Q5/Q10 selections — and worktree isolation /
   branch protection are context flags, never the effect boundary
   (`transports-selected`, `boundary-not-worktree`).

## Deferred / not selected

- **GitHub App installation (fine-grained, per-repo):** stays the Q5
  hardening fallback — evaluated only if scoped `gh` cannot satisfy F12's
  non-bypass requirement. Owner: Luong; blocks unattended merge automation,
  not this spike.
- **OS-user sandboxing for workers:** optional hardening beyond the spike;
  the same-UID limit is recorded in `authority-proof.md`.
- **Any bespoke credential broker for workers:** no-go — the profile's own
  scoped identity is the documented acceptance identity
  (`hermes-boundary-map.md`, "Boundaries that must not be rebuilt").

## Replan trigger

If Task 1.3 cannot show the scoped-`gh` path satisfying non-bypass merge, or
no durable typed-action hook exists, this mechanism is revisited **explicitly**
— recorded here per A5 rather than silently extended.
