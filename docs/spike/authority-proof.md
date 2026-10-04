# Authority proof — fenced publication and cancellation (issue #3, Task 1.2)

Executable evidence that the boundary selected in Task 1.1 enforces what the
unattended run will rely on. The harness is `tools/probes/fenced_effects.py`;
the remote is the disposable capability-gated fixture
`tests/fixtures/disposable_repo.py`; the run below is recorded in
`docs/spike/evidence/fenced-effects-2026-10-05.json`.

**Modeling honesty.** The fence store is real SQLite (`BEGIN IMMEDIATE` +
`synchronous=FULL`), mirroring `kanban.db`'s one-lock ordering; the worker and
its detached descendant are real `sys.executable` process groups; secrets are
real files with `0600` modes and generated canary values. GitHub/Vercel
effects land on a disposable JSON remote — so a denied request can be proved
to have left the remote untouched, which a live endpoint cannot show without
risk. What this spike does **not** prove: same-UID sandboxing, live Hermes
`block`/`reclaim`, the real Telegram adapter, live GitHub tokens — those are
Task 1.3's endpoint demo against the pinned install (limits table below).

## How the boundary is exercised

```text
worker process ──submit_request──▶ requests/ ──▶ broker (scoped token)
      │                                     │  checks repo/actor/generation/op
      │ direct attempts (fail)              ▼  under BEGIN IMMEDIATE
      └─────────────────────▶ DisposableRemote ──▶ allowed effect + seq
      (no token, read-only file)            │  or redacted denial row
                                            ▼
                                   fence.db (intents, control, audit)
```

## Per-criteria evidence

| AC | Scenario (`--scenario`) | Executable proof |
|---|---|---|
| **A1** | `credential-surface` | Real subprocess with the actual worker env: zero forbidden names (no `GH_TOKEN`/`GH_CONFIG_DIR`/`GITHUB_TOKEN`/`VERCEL_*`/`TELEGRAM_*`/controller tokens); a token-less `DisposableRemote` handle refuses all five ops (`BoundaryViolation`); a raw write to the read-only state file raises `PermissionError` |
| **A1** | `scoped-publication` | Three allowed current-generation requests publish branch/PR/preview through the boundary; every `intents` row and remote event carries `op`/`repository`/`generation`/`op_id`/`seq` |
| **A2** | `wrong-repository` | `denied: wrong-repository`; remote snapshot byte-identical before/after; denied `audit` row retained |
| **A2** | `revoked-actor` | Request allowed before `revoke_actor`, denied `revoked-actor` after; the allowed branch exists, the denied one does not |
| **A2** | `superseded-generation` | After cancel→confirm→replacement, gen-1 request denied `superseded-generation`, gen-2 allowed |
| **A2** | `prompt-text-no-policy` | Instruction-shaped `notes` neither breaks an envelope-valid request nor rescues an invalid one — policy is evaluated on the envelope only |
| **A2** | `write-type-matrix` | All five write ops (`branch-publish`, `pr-publish`, `preview-deploy`, `merge-invoke`, `api-mutation`) denied on a wrong repo and allowed on the valid envelope; remote mutates only on allow (PR 7 exists and is merged; deployment and API mutation recorded once each) |
| **A3** | `cancel-fence-order` | `accept_control` commits `control.seq`/`fenced` **before** `proc.terminate()` is invoked (`fence_committed_at ≤ terminate_initiated_at`); a request landing between commit and kill is already `generation-fenced`; the delayed descendant's `branch-publish` and the worker's late `submit-result` are denied; `terminated=confirmed` recorded after `wait()` |
| **A3** | `quarantine-no-replacement` | Worker that ignores SIGTERM stays unconfirmed → `terminated=quarantined`; `begin_replacement` returns `termination-uncertain`/`quarantined` — uncertain termination cannot permit replacement execution |
| **A3** | (unit tests) | Forged actor `66006` cancel → `rejected`, generation stays `active`; untyped action `nuke` → `unsupported-action` |
| **A4** | `canary-hygiene` | A denied request carrying a planted `ghp_…` canary leaves only `<redacted:github_token>` in audit detail; no canary value appears in `outbox.jsonl`, `exports/diagnostics.json`, or the store dump; manifest contains secret *references* (`secrets/…`), never values; secret files are `0600` |
| **A4** | `transports-selected` | Fixture transports record exactly the recipe selections — `github: scoped-gh-profile`, `telegram: gateway-authz+numeric-allowlist`, `provider: vercel` |
| **A4** | `boundary-not-worktree` | With `worktree_isolation` + `branch_protection` flags set, a wrong-repo `merge-invoke` is still denied — isolation/protection are context, never the effect boundary |

Reproduce: `python3 tools/probes/fenced_effects.py --write <out>.json`
(exit `0` = `boundary-proven`; `1` = named failures; `4` = cannot run).
Unit coverage: `tests/test_fenced_effects.py` (22 tests).

## Limits carried to Task 1.3

| Limit | Disposition |
|---|---|
| Remote is a JSON fixture, not GitHub/Vercel | Task 1.3 runs the same checks against the live endpoint; the deny-checks cannot mutate production is *stronger* here |
| Worker/descendant share the broker UID (self-sandboxing limit noted in the task) | OS-user isolation is optional hardening; the credential surface + capability token + read-only state already enforce the contract |
| Fence durability is `synchronous=FULL` commit-order, not crash-tested | Task 1.4 fault suite covers crash/bisect |
| `TELEGRAM_ALLOWED_USERS` modeled as fixture actors table | Live adapter + numeric allowlist demonstrated in Task 1.3 |

## Day-2 checkpoint (A5)

Recorded in the evidence JSON under `report.day2_checkpoint`:

- **Covered:** A1–A4 at disposable-fixture semantics level — the ordering and
  policy the recipe requires is executable and green.
- **Open gates:** Task 1.3 endpoint demo (live Hermes/GitHub/Telegram
  read-back) and Task 1.4 fault suite remain unexecuted; per §8.1, **broad
  feature work stays blocked** until every stage-0/1 gate carries executable
  evidence.
- **Schedule reality:** the task file budgets tasks 1.1–1.4 at ~1d each; four
  serial working days cannot fit a two-working-day sprint — this outcome is
  recorded honestly as an unmet-target checkpoint, not silently extended.
- **Owner:** Luong. If Task 1.3 finds no durable typed-action/approval
  primitive or Task 1.4 cannot produce a kill/reclaim fence, the disposition
  is **explicit replan** (see `hermes-boundary-map.md` no-go list) — the kit
  does not invent a second scheduler, transition engine, or bespoke
  credential broker.
