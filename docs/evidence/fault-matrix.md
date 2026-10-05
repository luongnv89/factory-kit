# §8.2 fault-matrix evidence — issues #20 + #21 (tasks 3.6/3.7)

Executable verification that the merged services hold the §8.2 fault
guarantees. The shared harness is `tests/faults/harness.py`; the 19 row
scenarios live in `tests/faults/rows_lifecycle.py` (FAULT01–FAULT12,
issue #20) and `tests/faults/rows_endpoint.py` (FAULT13–FAULT19, issue
#21). The runner/archiver is `tools/probes/fault_matrix.py`; the
unittest entry point is `tests/faults/test_matrix.py`. The recorded run
below is `docs/evidence/fault-matrix-2026-10-05.json`.

**What this evidence is.** Every row executes **three independent
repetitions** — each repetition on a fresh `World`: a fresh temp root,
a fresh `IntakeStore` (SQLite, `BEGIN IMMEDIATE` + `synchronous=FULL`),
and fresh scripted ports (`ScriptedRemote`, `ScriptedPreview`,
`ScriptedWorker`, `ScriptedIssueSource`) driven by one fake clock. No
network, no sleeps, no shared durable state between repetitions. The
merged services are exercised end-to-end; fault injection happens only
through the packaged ports' own seams (`remote.faults`,
`provider.outage`/`provider.faults`, `worker.terminations`,
`source.faults`, the notification transport seam, and mid-call
closures that race a fence or a base move across the committed
boundary).

**What it is not.** It is deterministic local-fixture evidence with
scripted-remote read-back where a row calls for authoritative
reconciliation — not a live-GitHub run and not a proof against every
possible security failure. It validates the finite §8.2 scenario set
the MVP commits to.

## Reproduce

```bash
python3 -m unittest tests.faults.test_matrix -v        # 19 rows × 3 reps
python3 tools/probes/fault_matrix.py --write out.json  # same run, JSON archive
python3 tools/probes/fault_matrix.py --row FAULT07     # one row
python3 tools/probes/fault_matrix.py --fixture out.json  # re-check verdict
```

A row counts verified only when **all three** repetitions pass every
named assertion and every release-gate audit. A crashed scenario is a
failed repetition; the gate fails closed.

## Row ↔ criterion map

### Issue #20 — lifecycle, recovery and trust (FAULT01–FAULT12)

| Row | Criterion exercised |
|-----|---------------------|
| FAULT01 | Repeated delivery (same delivery id), reordered revisions, webhook/poll overlap → one logical task, ≤1 active attempt (durable claim index), ≤1 PR (converged intent). |
| FAULT02 | Crash before intake commit → nothing persisted, clean redelivery; crash after intake/before task association → `pending` binding repaired on restart, redelivery deduplicates, no duplicate execution. |
| FAULT03 | `crash-after-create` on PR publish → existing PR discovered by read-back under recorded identity; ambiguous remote identity (two PRs on one head) parks, never republishes. |
| FAULT04 | Dead worker (heartbeat timeout) → generation fenced before replacement, unconfirmed exit quarantines, late result denied `generation-fenced`, replacement denied while uncertain, authorized retry mints a fresh generation. |
| FAULT05 | Cancel inside a live attempt → fence commits before termination, late descendant result denied, post-fence publish denied `fenced` with zero remote calls; unconfirmed exit → quarantine + visible alert, no replacement until resolved. |
| FAULT06 | Head moves after verification → old evidence `stale`/`head-moved`; the new SHA is never called verified off the old review. |
| FAULT07 | Missing required check → blocked/failed; failed check → failed; missing verification contract → `verification-contract-missing`. No worker self-report fallback. |
| FAULT08 | GitHub rate-limit honors `retry_after` (server-directed backoff, watermark `next_due_epoch`), outage accumulates durable failure counters; Telegram outage keeps the pending row durable, retries bounded, drains to delivered after recovery. |
| FAULT09 | Opt-in revoked after enqueue → work parked, zero dispatch, publish denied with zero remote effects, revoked identity never reopens. |
| FAULT10 | One active task at a time (visible occupancy); initial + exactly one fix attempt; 60 active-worker minutes park visibly with a budget alert; 24h wall-clock parks; usage is measured or honestly unknown. |
| FAULT11 | Permission-granting prose denied at intake (`permission-prose`); forged actor and untyped control actions denied; policy digest unchanged; secret canary absent from expanded exports (`secret_scan: clean`) and from notification bodies. |
| FAULT12 | Reinstall idempotent (only the owned file added); removal preserves user edits and unrelated content; active work fenced *before* removal; no orphaned execution authority. |

### Issue #21 — preview, approval and merge (FAULT13–FAULT19)

| Row | Criterion exercised |
|-----|---------------------|
| FAULT13 | Provider outage → visible deploy failure + no approval-ready; wrong-revision deploy → `revision-mismatch`; expired deployment → `deployment-expired`; one-preview limit converges; 24h TTL bound; old preview never satisfies a new revision (`verification-not-current`); outage at cleanup → visible `cleanup-pending` backlog that drains on recovery within the 60-minute bound. |
| FAULT14 | Forged actor, conversation mismatch, forged request id, rejected verdict, replayed decision, revoked grant, expired (60-min) request — all denied with durable reasons; zero merge calls under every denial; no automatic re-prompt. |
| FAULT15 | Head moved → `head-moved` + grant invalidated + evidence stale; base moved → `base-moved` denied before send; policy change → invalidated; missing check → `checks-failed`; stale smoke (>10 min) → `smoke-stale` + revocation; pause → `task-paused`; cancel → `fenced`. Re-approval required on the current compatible base. |
| FAULT16 | Durable request survives a controller restart attributable; 4 concurrent approvals → ≤1 consumed; repeated approve denied; 4 concurrent merges → ≤1 intent + ≤1 remote effect; fenced work's awaiting request can never be approved after the boundary. |
| FAULT17 | Merge lands remotely, response lost (`crash-after-create`) → authoritative read-back identifies the actual merge SHA, exactly one remote merge call; response *and* read-back both lost → intent parks `merge-outcome-unknown` with an `unknown` observation, then recovery read-back resolves it merged — never a blind resend or false verdict. |
| FAULT18 | Fence before merge boundary → denied `fenced`, zero remote calls, grant unspent; fence in-flight → actual outcome recorded `cancellation-race`, no destructive reversal; late preview callback after fence → denied `fenced`; human merge mid-flight → recorded `external` with the human's actor. |
| FAULT19 | Base moved → denied before send; base racing mid-send → repository strict protection refuses, intent reconciles `not-merged`; draft/closed PR → blocked; armed auto-merge / repo auto-merge capability → blocked (competing owner); missing/weak/bypassable protection and unsupported merge method → the gate fails explicitly; the factory never arms auto-merge. |

## Release-gate audits (every repetition)

Computed on the repetition's own durable rows plus the scripted remote's
authoritative state — never from the scenario's expectations:

- `duplicate_prs = 0` — no work item reaches two distinct remote pulls
  via publication intents.
- `unauthorized_merges = 0` — every factory-actor merge carries a merged
  durable intent bound to its work; external/human merges record the
  actual actor and are not counted.
- `accepted_fenced_results = 0` — no `accepted` result commits after
  its generation fence.
- `lost_acknowledged_tasks = 0` — every accepted/reconciled delivery
  resolves to a durable work row.
- `stale_counted_passing = 0` — a *latest* verified evidence row's head
  must still match the authoritative remote head.
- Verified-outcome audit — every `verified` evidence row carries an
  independent review id and the required check identities
  (`Code Quality & Build`, `Security Scan`), all green.

## Recorded run

`docs/evidence/fault-matrix-2026-10-05.json` — `fault-matrix-passed`,
19 rows / 57 repetitions / 387 named assertions, all passed, zero audit
violations. The archive records per-repetition assertions with measured
values, bound identities (work/attempt/intent/request/PR/preview/
deployment/notification ids, merge SHAs), the audit detail, and the
environment block (python/platform, harness version, manifest +
policy digests, supported versions, fixture identity, reproduce
command).
