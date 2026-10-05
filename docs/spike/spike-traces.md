# Spike traces — denied/allowed transition matrix (issue #4, Task 1.3)

Every transition the endpoint can take, the leg that exercised it, and the
evidence row it produced. Source of truth:
`docs/spike/evidence/endpoint-2026-10-05.json` (`legs[]` per gate,
`store_dump` for committed rows). Verdict vocabulary: `allowed` /
`denied:<reason>` / `no-go:<blocker>` / `refused` (owner decision).

## A1 — durable intake

| Transition | Result | Evidence |
|---|---|---|
| Signed event, registered repo, opted-in | `allowed` → `wk-0004`/`task-0004`/gen-1 | `legs.intake.accepted`; `store_dump.work` 1 row |
| Invalid signature | `denied:invalid-signature`, no work row | `legs.intake.denied[0]`; events row |
| Wrong repository | `denied:wrong-repository`, no work row | `legs.intake.denied[1]` |
| Unauthorized opt-in | `denied:unauthorized-optin`, no work row | `legs.intake.denied[2]` |
| Implementation session | `att-implementation-*`, verdict `completed`, exact head | `legs.intake.sessions` |
| Independent reviewer session | `att-review-*`, verdict `approved`, same head, different session id | `legs.intake.sessions` |

## A2 — verify gate

| Transition | Result | Evidence |
|---|---|---|
| Approved review + green nonempty checks + head agreement | `allowed` (verified) | `legs.verify.legs[happy-path]` |
| Implementation self-report substituted | `denied:impl-self-report-substituted` | `legs.verify.legs[self-report-no-substitute]` |
| Head moved since review | `denied:head-moved-since-review` | `legs.verify.legs[head-moved]` |
| No check runs | `denied:required-checks-missing` | `legs.verify.legs[check-missing]` |
| Failed required check | `denied:check-failed:Security Scan` | `legs.verify.legs[check-failed]` |
| **Live:** real PR #88 on money-mind | `denied:check-failed:Security Scan` on **real** CI (plus Socket checks green) | `legs.verify.live`; `live_pr.checks` |

## A3 — revision-bound preview

| Transition | Result | Evidence |
|---|---|---|
| Deploy bound to reviewed head + fresh smoke | `allowed` (preview-ready) | `legs.preview.deployment`/`smoke` |
| Older preview reused for newer head | `denied:wrong-revision` | `legs.preview.legs[wrong-revision-reuse]` |
| Unknown deployment id | `denied:unknown-deployment` (inspect raises) | `legs.preview.legs[unknown-deployment]` |
| Smoke older than 10 min | `denied:stale-smoke` | `legs.preview.legs[stale-smoke]` |
| Provider outage | blocked + cleanup **backlogged** (visible, never false-removed) | `legs.preview.legs[provider-outage]` |
| Owned cleanup after termination | `removed:true` | `legs.preview.owned_cleanup` |
| **Live:** real Vercel preview deploy | `dpl_49enFvq4…`, target=preview, inspect ok, smoke 200/marker-miss (deployment-protection interstitial), `vercel rm` + `project rm` ok | `live.preview` |

## A4 — durable one-use approval

| Transition | Result | Evidence |
|---|---|---|
| Request created | binds actor/target/action + revision/evidence/policy digests, expiry +3600 s | `legs.approval.request`; `store_dump.approvals` |
| Controller/chat restart | awaiting-approval + authorized decision remain attributable | `legs.approval.restart_survival.attributable=true` |
| Two concurrent approve sessions | **exactly 1 `consumed`, exactly 1 merge intent** | `legs.approval.legs[concurrent-approve]` |
| Replay of consumed request | `denied:replayed`, 0 new intents | `legs.approval.legs[replay]` |
| Wrong actor | `denied:wrong-actor` | `legs.approval.legs[wrong-actor]` |
| Unknown request id | `denied:unknown-request` | `legs.approval.legs[unknown-request]` |
| **Live:** adapter + allowlist presence | `adapter_present=true`, `TELEGRAM_ALLOWED_USERS`/`BOT_TOKEN`/`HOME_CHANNEL` keys | `live.telegram`; live round-trip = no-go `telegram-adapter-live` |

## A5/A6 — guarded merge

| Transition | Result | Evidence |
|---|---|---|
| Full revalidation | `allowed` | `legs.merge-guard.legs[happy-path]` |
| Head changed | `denied:head-changed` + intent invalidated | deny matrix |
| Base moved | `denied:base-changed` | deny matrix |
| Stale preview | `denied:stale-preview` | deny matrix |
| Revoked actor | `denied:revoked-actor` + intent invalidated | deny matrix |
| Fenced / paused task | `denied:task-fenced` / `denied:task-paused` + invalidated | deny matrix |
| Expired / rejected / replayed approval | `denied:approval-expired` / `approval-rejected` / `approval-awaiting` | deny matrix |
| Missing required checks | `denied:required-checks-not-green` | deny matrix |
| Unprotected / non-strict base | `denied:base-unprotected` | deny matrix |
| Auto-merge enabled | `denied:auto-merge-enabled` | deny matrix |
| **Live:** `money-mind:main` read-back | `protected:false` → owner **`refused`** (no live merge attempted) | `legs.merge-guard.live_merge_owner`; `repo_facts` |

## A7 — authoritative read-back

| Transition | Result | Evidence |
|---|---|---|
| Merge invoked through boundary | `allowed`, recorded w/ expected-head check | `store_dump` remote events |
| Merged status | only from remote snapshot — actual merge SHA | `legs.readback.legs[happy-readback]` |
| Merge accepted, response lost | `reconciled` to actual SHA before retry — `retried:false` | `legs.readback.legs[lost-response-reconcile]` |
| Remote state unknown | `parks` — never success, never blind resend | `legs.readback.legs[unknown-state-parks]` |
| Defaults | approval expiry 3600 s · smoke age ≤600 s | `legs.readback.defaults` |

## A8 — identity chain

`linked_identities` (run `endpoint-2026-10-05`): `wk-0004` → `task-0004` →
`att-implementation-*`/`att-review-*` → PR head `e5e5e5…` (+ live PR #88
head `665a79ef`) → `dpl-0001-*` (+ live `dpl_49enFvq4…`) → `apr-*` →
`mi-0005`. Fixture versions: `endpoint_walkthrough 1.0.0`,
`preview_fixture` schema v1, `disposable_repo` schema v1.
