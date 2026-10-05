# Endpoint demo — opted-in issue → approved merge (issue #4, Task 1.3)

Executable evidence that the complete endpoint assembles on the primitives
Tasks 1.1–1.2 selected and proved. The harness is
`tools/probes/endpoint_walkthrough.py`; the preview authority is
`tests/fixtures/preview_fixture.py`; the remote/fence semantics are
Task 1.2's `tests/fixtures/disposable_repo.py`. The run below is recorded in
`docs/spike/evidence/endpoint-2026-10-05.json`; unit coverage is
`tests/test_endpoint_walkthrough.py` (30 tests).

**Modeling honesty.** The work/attempt/approval store is real SQLite
(`BEGIN IMMEDIATE` + `synchronous=FULL` + `busy_timeout`), the same
one-lock ordering `kanban.db` provides; denied transitions run on the
disposable remote and preview registry, where a denied write can be proved
to have left state untouched. **Live legs run real interfaces:** `gh`
read-back of `luongnv89/money-mind` (identity, protection, merge flags), a
real bounded PR with real CI check runs (`--live-pr`), a real Vercel
preview deploy + inspect + smoke + full cleanup (`--live-preview`), and
real Telegram adapter/env presence. What stays fixture-modeled and why is
in *Limits* — every gap is a named no-go with an owner, per the task's
"fail/no-go and replan" rule.

## Pipeline

```text
signed event ─▶ durable intake ─▶ impl session ─▶ reviewer session
     │  (denied: bad sig / wrong repo / no opt-in — zero authority)
     ▼
verify gate ◀── review verdict + nonempty checks + head/base read-back
     ▼
preview gate ◀── deployment id/url bound to reviewed head + fresh smoke
     ▼
approval ◀── one-use request (digests+expiry) survives restart; ≤1 consume
     ▼
merge-guard ◀── owner revalidates everything → expected-head merge
     ▼
read-back ──▶ merged ONLY from authoritative merge-SHA read-back
```

## Per-criteria evidence

| AC | Leg(s) | Executable proof |
|---|---|---|
| **A1** | `intake` | HMAC-signed event for the registered repo commits work `wk-*`/`task-*`/generation in one transaction; `invalid-signature`, `wrong-repository`, `unauthorized-optin` each commit a denied event row and **zero** work rows (`work_rows=1`). Implementation (`att-implementation-*`) and a *separate* reviewer session (`att-review-*`) execute sequentially with own attempt/session ids, verdict and exact head |
| **A2** | `verify` | `evaluate_verify` passes on approved review + nonempty green checks + head/base agreement; denies on `impl-self-report-substituted`, `head-moved-since-review`, `required-checks-missing`, `check-failed`. **Live:** real PR `money-mind#90` read-back — real head `d1f7a99d`, base `main`, 4 real check runs; the gate correctly **blocked** on the real `Security Scan` failure |
| **A3** | `preview` | PreviewRegistry deploy returns id+URL immutably bound to the reviewed head + revision digest; smoke records URL/200/marker/time; owned cleanup removes the deployment. Denied: `wrong-revision`, `unknown-deployment`, `stale-smoke` (>10 min), `expired`, `provider-outage` (blocks + visible cleanup backlog, never false removal). **Live:** real Vercel deploy `dpl_5EA6bM6s…` → `fk-spike-money-mind-*.vercel.app` target=preview, real `vercel inspect`, real smoke, `vercel rm` + `project rm` — nothing left behind |
| **A4** | `approval` | `request_approval` binds actor/target/action + revision/evidence/policy digests, 60 min expiry; presentation payload carries repo/PR/head/base/preview/method/expiry. Store close+reopen → awaiting-approval still attributable (`restart_survival`). **Two separate sessions race `approve` → exactly one `consumed`, exactly one `merge_intents` row**; replay → `replayed`, wrong actor → `wrong-actor`, unknown → `unknown-request` |
| **A5** | `merge-guard` | `evaluate_merge_guard` passes on full revalidation; 13-case deny matrix all deny (head/base change, stale preview, revoked actor, fenced/paused task, expired/rejected/replayed approval, missing checks, unprotected/non-strict base, auto-merge). Denies invalidate the affected authority (intent canceled) |
| **A6** | `merge-guard` live | **Real read-back on `money-mind:main` → `protected:false`** → the merge owner **refuses** the live merge (`live_merge_owner.decision=refused`, blockers `required-checks-not-green`+`base-unprotected`) — exactly the guarded behavior the recipe demands when the base cannot enforce the contract. `allow_auto_merge:false` confirmed live |
| **A7** | `readback` | `merged` only from remote snapshot after `invoke_merge` — merge SHA recorded authoritatively; `lost-response` reconciles to the actual SHA before any retry (never blind resend); unknown state parks. Defaults recorded: approval expiry 3600 s, smoke age ≤600 s |
| **A8** | `ledger` | `linked_identities` chains work/task/attempts/PR/deployment/approval/merge-intent + fixture versions; `elapsed_s` + MET01 note — this run does **not** declare production F02/F04/F11/F12 complete |

Reproduce: `python3 tools/probes/endpoint_walkthrough.py --write <out>.json`
(fixture legs) · `--live-pr --live-preview` (live legs) · `--fixture run.json`
re-evaluates a recording · `--self-test` · `--scenario <stage>` runs the
pipeline up to and including that stage.

## Live-interface findings (why they're valuable)

| Finding | Evidence | Consequence |
|---|---|---|
| `money-mind:main` unprotected | `repo_facts.protection.protected=false` | **Gate A6 no-go: `base-unprotected`** — owner Luong must enable required checks (`Code Quality & Build`, `Security Scan`) + strict up-to-date + `enforce_admins`; until then the live merge is correctly refused |
| Real `Security Scan` job fails on a real head | live PR #90 check-runs | The verify gate's `check-failed` deny is proven on **real CI**, not only on fixtures — a red required check genuinely blocks verified status |
| Vercel smoke 200 without shell marker | live preview `smoke.marker=false` | New-project preview sits behind **deployment protection**: unauthenticated smoke gets the interstitial, not the SPA. Contract needs the documented bypass or protection off for previews — named for the recipe owner |
| Telegram adapter + numeric allowlist keys present | `adapter_present=true`, `TELEGRAM_*` keys | Transport configured; the live message round-trip stays **no-go `telegram-adapter-live`** (needs the real chat; store/payload semantics proven at fixture level) |

## Named no-go / owner ledger

| Blocker | Owner | Status |
|---|---|---|
| `base-unprotected` (A6 live merge) | Luong — enable branch protection on `money-mind:main` | open — live merge refused until then |
| `telegram-adapter-live` (A4 message leg) | Operator — real chat round-trip | adapter+keys verified; store semantics proven |
| `vercel-linkage` | Luong — `vercel link`/GitHub integration | CLI deploy works (proven); linkage variant untested |
| `kanban-live-write` | Luong — real kanban rows | deliberately not written — the gateway dispatcher would consume probe tasks as real work |

## Limits carried to Task 1.4

- Sessions (implementation/reviewer) are durable identity records, not real
  kanban worker dispatches — same store semantics, no real execution spawn.
- The merge endpoint itself is demonstrated on the disposable remote with
  enforced protection; on the live repo the owner-side refusal is the
  demonstrated behavior until `base-unprotected` clears.
- Fault injection (duplicate delivery, restart/cancel races, merge
  uncertainty) is Task 1.4's scope — this run proves the happy-path
  assembly plus every gate's deny semantics once.

## Day-2 checkpoint (A8 / MET01)

- **Covered:** A1–A8 at assembled-run semantics — every gate executable,
  deny matrix green, live read-backs real, every artifact self-cleaned.
- **Open gates:** live guarded merge (blocked by `base-unprotected`), live
  Telegram message round-trip, Task 1.4 fault suite. Broad feature work
  stays blocked per §8.1 until the full gate ledger is green.
- **Schedule:** same honest note as Task 1.2 — the spike tasks exceed the
  2-day PRD target; recorded as unmet-target checkpoint, not silently
  extended. Owner: Luong — missing primitive → explicit replan, never a
  second scheduler/transition engine.
