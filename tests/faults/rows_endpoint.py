#!/usr/bin/env python3
"""§8.2 preview, approval and merge fault rows FAULT13–FAULT19
— issue #21 (task 3.7).

Each ``faultNN(world, ctx)`` drives one §8.2 endpoint row against the
merged services — exact-revision preview/approval/merge authority and
authoritative outcome recovery, including races at the committed
fencing and repository-enforced merge boundary. Assertions go through
``ctx["check"]``; bound identities go to ``ctx["ids"]``.

Row ↔ criterion map (issue #21):

- FAULT13 — A1: preview failure / wrong revision / expiry / provider
  outage → approval-ready/merge denied, error visible, old preview
  never reused as new evidence; one-preview limit, 24h lifetime,
  ≤60min cleanup and visible outage backlog.
- FAULT14 — A2: forged / replayed / expired / rejected / revoked
  approval → no merge, durable denial reason, no broader authority;
  actor/conversation/action mismatch, 60-min default expiry, no
  automatic re-prompt.
- FAULT15 — A3: head/base/policy changes after approval → approval
  invalidated, review/check/preview re-evaluated on the current
  compatible base, new approval required; missing/failed checks,
  stale smoke >10min, pause/cancel, actor revocation.
- FAULT16 — A4: concurrent/repeated approve + controller restart →
  durable request/decision retained, one approval consumed, ≤1 merge
  intent; no chat-only authority, no revival on resume.
- FAULT17 — A5: remote merge accepted then response lost →
  authoritative read-back identifies the actual merge SHA before any
  retry; unresolved parks without blind repeat or false
  failure/success.
- FAULT18 — A6: cancel/revoke races with preview/merge → no new
  effect after a committed fence, late preview callbacks rejected,
  pre-fence in-flight effects reconciled accurately; human merges
  record the actual actor; no automatic destructive reversal.
- FAULT19 — A7: dirty/incompatible base, draft/closed PR, competing
  automation or bypass-only credentials → merge blocked until
  explicit compatible policy/current protections; expected-head +
  server enforcement closes the race; unsupported enforcement fails
  the gate; auto-merge is never a shortcut.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.preview import ScriptedPreview  # noqa: E402
from factory_kit.publication.remote import RemoteAmbiguity  # noqa: E402

from tests.faults.harness import (  # noqa: E402
    BASE_B, BASE_SHA, BRANCH, CHAT, GREEN_RUNS, HEAD_A, HEAD_B,
    PR, USER, World)


def _ids(ctx, **pairs):
    for k, v in pairs.items():
        if v is None:
            continue
        bucket = ctx["ids"].setdefault(k, [])
        values = v if isinstance(v, (list, tuple, set)) else [v]
        for item in values:
            if item is not None and item not in bucket:
                bucket.append(item)


def _merge_calls(w):
    return [c for c in w.remote.calls if c["op"] == "pr-merge"]


def _pr_of(w, work_key):
    """The remote pull the work actually linked at publish — service-
    evidence flows auto-number the pull (100, 101, …); direct-evidence
    flows use the seeded pull. Never hardcode the number."""
    return str(w.store.get_work(work_key).get("linked_pr") or PR)


# ---------------------------------------------------------------------------
# FAULT13 — preview failure / wrong revision / expiry / provider outage
# ---------------------------------------------------------------------------

def fault13(w: World, ctx):
    check, ids = ctx["check"], _ids

    # -- provider outage: deploy fails visibly; no approval-ready and
    #    no merge outcome can be built on the missing preview. (A
    #    blocking deploy failure marks the work blocked — every
    #    scenario below gets its own accepted work, matching the
    #    production contract.)
    wk_out = w.accept(1301)
    w.evidence(wk_out, HEAD_A, pr_number="81")
    w.provider.outage = True
    out = w.deploy(wk_out, HEAD_A)
    check("provider-outage-denies-deploy",
          out["outcome"] in ("failed", "denied")
          and out["reason"] == "provider-outage",
          outcome=out["outcome"], reason=out["reason"])
    req = w.approval.request_approval(wk_out)
    check("outage-never-approval-ready",
          req["outcome"] == "denied",
          outcome=req["outcome"], reason=req["reason"])
    ids(ctx, work_keys=[wk_out])
    w.provider.outage = False

    # -- wrong revision: provider read-back head ≠ the bound head.
    class MovedHead(ScriptedPreview):
        def deploy(inner, spec, identity):
            dep = super().deploy(spec, identity)
            dep["head_sha"] = HEAD_B
            inner.deployments[dep["deployment_id"]]["head_sha"] = HEAD_B
            return dep

    wk_mv = w.accept(1305)
    w.evidence(wk_mv, HEAD_A, pr_number="82")
    w.provider = MovedHead(now=w._now)
    w._build_services()
    out2 = w.preview.deploy_preview(wk_mv, head_sha=HEAD_A)
    check("wrong-revision-fails",
          out2["outcome"] == "failed"
          and out2["reason"] == "revision-mismatch",
          outcome=out2["outcome"], reason=out2["reason"])
    ids(ctx, work_keys=[wk_mv])

    # -- happy deploy at HEAD_A: verified preview — the one-preview
    #    limit and the 24-hour bound are checked on it.
    w.provider = ScriptedPreview(now=w._now)
    w._build_services()
    work_key = w.accept(1306)
    w.evidence(work_key, HEAD_A, pr_number="83")
    ids(ctx, work_keys=[work_key])
    out3 = w.deploy(work_key, HEAD_A)
    check("deploy-verified", out3["outcome"] == "verified",
          outcome=out3["outcome"])
    dep_id = out3["deployment_id"]
    ids(ctx, preview_ids=[out3["preview_id"]], deployment_ids=[dep_id])

    # one-preview limit: the same-head redeploy converges; a second
    # active row is a structural impossibility, not a second slot.
    again = w.deploy(work_key, HEAD_A)
    check("one-preview-limit-converges",
          again["outcome"] == "converged"
          and len(w.provider.deployments) == 1,
          outcome=again["outcome"],
          deployments=len(w.provider.deployments))
    # 24-hour lifetime: the bound deployment's TTL is the manifest's
    # ttl_hours; an already-expired deployment at read-back is a
    # denial, never evidence.
    dep = w.provider.deployments[dep_id]
    ttl_h = dep["expires_epoch"] - dep["created_epoch"]
    check("preview-lifetime-24h",
          abs(ttl_h - 24 * 3600) < 2, ttl_hours=ttl_h / 3600)

    class ShortLived(ScriptedPreview):
        def deploy(inner, spec, identity):
            dep2 = super().deploy(spec, identity)
            inner.deployments[dep2["deployment_id"]][
                "expires_epoch"] = w.clock[0] - 1
            dep2["expires_epoch"] = w.clock[0] - 1
            return dep2

    w.provider = ShortLived(now=w._now)
    w._build_services()
    wk_exp = w.accept(1304)
    w.evidence(wk_exp, HEAD_A, pr_number="90")
    expired = w.preview.deploy_preview(wk_exp, head_sha=HEAD_A)
    check("expired-deployment-fails",
          expired["outcome"] == "failed"
          and expired["reason"] == "deployment-expired",
          outcome=expired["outcome"], reason=expired["reason"])
    ids(ctx, work_keys=[wk_exp])
    w.provider = ScriptedPreview(now=w._now)
    w._build_services()

    # -- old preview never reused as new evidence: a *new* revision's
    #    evidence cannot borrow the HEAD_A preview.
    w.clock[0] += 60
    work_key2 = w.accept(1302)
    w.evidence(work_key2, HEAD_B, pr_number="88")
    denied_reuse = w.preview.deploy_preview(work_key2, head_sha=HEAD_A)
    check("old-preview-never-satisfies-new-revision",
          denied_reuse["outcome"] == "denied",
          outcome=denied_reuse["outcome"],
          reason=denied_reuse["reason"])

    # -- outage at cleanup: a visible cleanup-pending backlog, never a
    #    false removal; the sweep drains it once the provider returns.
    wk3 = w.accept(1303)
    w.evidence(wk3, HEAD_A, pr_number="89")
    dep3 = w.deploy(wk3, HEAD_A)
    assert dep3["outcome"] == "verified", dep3
    ids(ctx, work_keys=[wk3], preview_ids=[dep3["preview_id"]],
        deployment_ids=[dep3["deployment_id"]])
    w.provider.outage = True
    w.preview.terminate(wk3)
    rec3 = w.store.get_preview(dep3["preview_id"])
    check("outage-cleanup-visible-backlog",
          rec3["state"] == "cleanup-pending"
          and dep3["deployment_id"] in w.provider.deployments
          and w.store.preview_cleanup_backlog(),
          state=rec3["state"],
          backlog=len(w.store.preview_cleanup_backlog()))
    w.provider.outage = False
    w.preview.sweep()
    rec3 = w.store.get_preview(dep3["preview_id"])
    check("cleanup-drains-after-outage",
          rec3["state"] == "removed"
          and dep3["deployment_id"] not in w.provider.deployments,
          state=rec3["state"])
    # The cleanup deadline was reachable: ≤ cleanup_minutes (60) from
    # the termination epoch, recorded on the row.
    dl = rec3.get("cleanup_deadline_epoch")
    check("cleanup-deadline-within-60min",
          dl is None or dl - w.clock[0] <= 60 * 60,
          cleanup_deadline_delta=(dl - w.clock[0]) if dl else None)


# ---------------------------------------------------------------------------
# FAULT14 — forged / replayed / expired / rejected / revoked approval
# ---------------------------------------------------------------------------

def fault14(w: World, ctx):
    check, ids = ctx["check"], _ids
    # A live request awaits its typed decision; every malformed,
    # mismatched, replayed, expired or revoked decision grants nothing
    # and leaves a durable reason — never a merge.
    work_key, rid = w.requested(1401)
    ids(ctx, work_keys=[work_key], request_ids=[rid])

    # forged actor — not on the allowlist.
    forged = w.approval.decide(
        request_id=rid, actor_ref="telegram:999999",
        chat_ref=f"telegram:{CHAT}", verdict="approve")
    check("forged-actor-denied",
          forged["outcome"] in ("denied", "rejected"),
          outcome=forged["outcome"], reason=forged.get("reason"))
    # conversation mismatch — right actor, wrong chat.
    wrong_chat = w.approval.decide(
        request_id=rid, actor_ref=f"telegram:{USER}",
        chat_ref="telegram:-999", verdict="approve")
    check("conversation-mismatch-denied",
          wrong_chat["outcome"] in ("denied", "rejected"),
          outcome=wrong_chat["outcome"],
          reason=wrong_chat.get("reason"))
    # forged request identity.
    ghost = w.approval.decide(
        request_id="apr-ghost", actor_ref=f"telegram:{USER}",
        chat_ref=f"telegram:{CHAT}", verdict="approve")
    check("forged-request-denied",
          ghost["outcome"] in ("denied", "rejected"),
          outcome=ghost["outcome"], reason=ghost.get("reason"))
    # rejected verdict — a durable denial reason; the work stays
    # human-blocked and no second prompt is minted automatically.
    rejected = w.approval.decide(
        request_id=rid, actor_ref=f"telegram:{USER}",
        chat_ref=f"telegram:{CHAT}", verdict="reject")
    req_row = w.store.get_approval_request(rid)
    reprompt = w.approval.request_approval(work_key)
    check("rejection-durable-no-reprompt",
          rejected["outcome"] in ("rejected", "denied", "recorded")
          and req_row["state"] in ("rejected", "denied", "invalidated",
                                   "expired")
          and reprompt["outcome"] != "requested",
          rejected=rejected["outcome"], state=req_row["state"],
          reprompt=reprompt["outcome"])

    # A fresh request on new work: approve it, then replay the same
    # decision — the grant is single-use. (requested() links a real
    # pull so the merge call below reaches the genuine gate.)
    wk2, rid2 = w.requested(1402)
    ids(ctx, work_keys=[wk2], request_ids=[rid2])
    first = w.approval.decide(
        request_id=rid2, actor_ref=f"telegram:{USER}",
        chat_ref=f"telegram:{CHAT}", verdict="approve")
    replay = w.approval.decide(
        request_id=rid2, actor_ref=f"telegram:{USER}",
        chat_ref=f"telegram:{CHAT}", verdict="approve")
    check("replayed-decision-denied",
          first["outcome"] == "approved"
          and replay["outcome"] in ("denied", "rejected"),
          first=first["outcome"], replay=replay["outcome"],
          reason=replay.get("reason"))
    # revoked after approval: invalidation kills the standing grant —
    # a later consume/merge path can never ride it.
    w.approval.invalidate(wk2, "operator-revoked")
    req_row2 = w.store.get_approval_request(rid2)
    merged2 = w.merge.merge(wk2)
    check("revoked-approval-no-merge",
          req_row2["state"] == "invalidated"
          and merged2["outcome"] == "denied",
          state=req_row2["state"], merge=merged2["outcome"],
          reason=merged2.get("reason"))

    # expired: the 60-minute default lapses; the decision and the
    # grant both die — a late approve is denied with a durable reason.
    wk3, rid3 = w.requested(1403)
    ids(ctx, work_keys=[wk3], request_ids=[rid3])
    w.clock[0] += 61 * 60
    late = w.approval.decide(
        request_id=rid3, actor_ref=f"telegram:{USER}",
        chat_ref=f"telegram:{CHAT}", verdict="approve")
    check("expired-decision-denied",
          late["outcome"] in ("denied", "rejected"),
          outcome=late["outcome"], reason=late.get("reason"))
    # no merge was ever attempted on any of the denied grants.
    check("no-merge-on-any-denial", len(_merge_calls(w)) == 0,
          merge_calls=len(_merge_calls(w)))
    # durable denial reasons exist for every decision class exercised.
    decisions = w.store._rows("approval_decisions")
    reasoned = [d for d in decisions if d.get("reason")
                or d.get("outcome")]
    check("durable-denial-reasons-retained",
          len(decisions) >= 4 and len(reasoned) == len(decisions),
          decisions=len(decisions), reasoned=len(reasoned))


# ---------------------------------------------------------------------------
# FAULT15 — head/base/policy changes after approval
# ---------------------------------------------------------------------------

def fault15(w: World, ctx):
    check, ids = ctx["check"], _ids
    # -- head moves after approval → stale evidence, invalidated grant.
    work_key, rid = w.approved(1501)
    ids(ctx, work_keys=[work_key], request_ids=[rid])
    w.remote.branches[BRANCH] = HEAD_B
    out = w.merge.merge(work_key)
    check("head-moved-merge-denied",
          out["outcome"] == "denied" and out["reason"] == "head-moved",
          outcome=out["outcome"], reason=out["reason"])
    stale = w.verifier.mark_stale(work_key, "head-moved")
    req_row = w.store.get_approval_request(rid)
    check("head-moved-invalidates-approval",
          stale["outcome"] == "stale"
          and req_row["state"] == "invalidated",
          stale=stale["outcome"], state=req_row["state"])

    # -- base moves after approval → denied before the send; re-
    #    evaluation on the current compatible base + NEW approval are
    #    required — the old grant never carries forward.
    wk2, rid2 = w.approved(1502)
    ids(ctx, work_keys=[wk2], request_ids=[rid2])
    w.remote.branches["main"] = BASE_B
    out2 = w.merge.merge(wk2)
    check("base-moved-merge-denied",
          out2["outcome"] == "denied"
          and out2["reason"] == "base-moved",
          outcome=out2["outcome"], reason=out2["reason"])
    # policy/config drift after approval: the request bound the OLD
    # digests — a changed effective config is caught by the digest
    # boundary itself (stale-config), and an explicit invalidation
    # kills the standing grant either way.
    w.eff["endpoint"]["merge"]["method"] = "rebase"
    out2b = w.merge.merge(wk2)
    w.eff["endpoint"]["merge"]["method"] = "squash"
    check("policy-drift-denied-stale-config",
          out2b["outcome"] == "denied"
          and out2b["reason"] == "stale-config",
          outcome=out2b["outcome"], reason=out2b["reason"])
    w.approval.invalidate(wk2, "policy-changed")
    req2_row = w.store.get_approval_request(rid2)
    out2c = w.merge.merge(wk2)
    check("policy-changed-approval-invalidated",
          req2_row["state"] == "invalidated"
          and out2c["outcome"] == "denied",
          state=req2_row["state"], merge=out2c["outcome"])
    w.remote.branches["main"] = BASE_SHA  # restore for the next PRs

    # -- missing/failed checks + stale smoke >10min pause/cancel and
    #    actor revocation each independently kill merge authority.
    wk3, rid3 = w.approved(1503)
    ids(ctx, work_keys=[wk3], request_ids=[rid3])
    w.remote.checks[HEAD_A] = [GREEN_RUNS[0]]      # missing check
    out3 = w.merge.merge(wk3)
    check("missing-check-merge-denied",
          out3["outcome"] == "denied"
          and str(out3["reason"]).startswith("checks-failed"),
          reason=out3["reason"])

    wk4, rid4 = w.approved(1504)
    ids(ctx, work_keys=[wk4], request_ids=[rid4])
    w.clock[0] += 11 * 60                      # past 10-min smoke bound
    out4 = w.merge.merge(wk4)
    req4_row = w.store.get_approval_request(rid4)
    check("stale-smoke-blocks-and-revokes",
          out4["outcome"] == "denied"
          and out4["reason"] == "smoke-stale"
          and req4_row["state"] == "invalidated",
          reason=out4["reason"], state=req4_row["state"])
    w.clock[0] -= 11 * 60

    wk5, rid5 = w.approved(1505)
    ids(ctx, work_keys=[wk5], request_ids=[rid5])
    with w.store.transact() as tx:
        tx.set_pause(wk5, "paused", stage="merge")
    out5 = w.merge.merge(wk5)
    w.store.fence_work(wk5, "canceled", 1)
    out5b = w.merge.merge(wk5)
    check("pause-blocks-merge",
          out5["outcome"] == "denied"
          and out5["reason"] == "task-paused",
          reason=out5["reason"])
    check("cancel-fences-merge",
          out5b["outcome"] == "denied"
          and out5b["reason"] == "fenced",
          reason=out5b["reason"])
    check("no-merge-under-any-drift", len(_merge_calls(w)) == 0,
          merge_calls=len(_merge_calls(w)))


# ---------------------------------------------------------------------------
# FAULT16 — concurrent/repeated approve + controller restart
# ---------------------------------------------------------------------------

def fault16(w: World, ctx):
    check, ids = ctx["check"], _ids
    work_key, rid = w.requested(1601)
    ids(ctx, work_keys=[work_key], request_ids=[rid])

    # Controller restart between request and decision: the durable
    # request survives attributable — chat history is never authority.
    w.reopen()
    row = w.store.get_approval_request(rid)
    check("request-survives-restart",
          row is not None and row["state"] == "awaiting"
          and row["head_sha"] == HEAD_A,
          state=(row or {}).get("state"))
    # Repeated + concurrent decisions on the one request: exactly one
    # lands; the rest deny. The durable row serializes the race.
    results, errors = [], []

    def decide():
        try:
            results.append(w.approval.decide(
                request_id=rid, actor_ref=f"telegram:{USER}",
                chat_ref=f"telegram:{CHAT}", verdict="approve"))
        except Exception as exc:  # pragma: no cover - diagnostic
            errors.append(type(exc).__name__)

    threads = [threading.Thread(target=decide) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    approved = [r for r in results if r["outcome"] == "approved"]
    check("concurrent-approvals-one-consumed",
          len(approved) <= 1 and not errors,
          approved=len(approved), errors=errors,
          outcomes=[r["outcome"] for r in results])
    again = w.approval.decide(
        request_id=rid, actor_ref=f"telegram:{USER}",
        chat_ref=f"telegram:{CHAT}", verdict="approve")
    check("repeated-approve-denied",
          again["outcome"] in ("denied", "rejected"),
          outcome=again["outcome"], reason=again.get("reason"))

    # At most one merge intent — concurrent merge calls converge on
    # the single durable live slot and one remote effect.
    merges, merr = [], []

    def do_merge():
        try:
            merges.append(w.merge.merge(work_key))
        except Exception as exc:  # pragma: no cover
            merr.append(type(exc).__name__)

    threads = [threading.Thread(target=do_merge) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    intents = w.store.merge_intent_rows(work_key)
    req_row = w.store.get_approval_request(rid)
    check("at-most-one-merge-intent",
          len(intents) <= 1 and len(_merge_calls(w)) <= 1
          and not merr,
          intents=len(intents), merge_calls=len(_merge_calls(w)),
          outcomes=[m["outcome"] for m in merges], errors=merr)
    # Exactly one approval was consumed — a merged intent spent the
    # single durable grant; the concurrent losers converged, none
    # minted a second intent. (A parked merge would hold the grant
    # approved-but-unspent — also correct, never double-spent.)
    succeeded = any(m["outcome"] == "merged" for m in merges)
    check("exactly-one-consumed-approval",
          req_row["state"] in ("approved", "consumed")
          and (not succeeded or req_row["state"] == "consumed"),
          state=req_row["state"], merged=succeeded)
    ids(ctx, intent_ids=[i["intent_id"] for i in intents])

    # No revival on resume: a canceled/fenced work's awaiting request
    # can never be approved after the boundary.
    wk2, rid2 = w.requested(1602)
    ids(ctx, work_keys=[wk2], request_ids=[rid2])
    w.store.fence_work(wk2, "canceled", 1)
    late = w.approval.decide(
        request_id=rid2, actor_ref=f"telegram:{USER}",
        chat_ref=f"telegram:{CHAT}", verdict="approve")
    check("no-revival-after-fence",
          late["outcome"] in ("denied", "rejected"),
          outcome=late["outcome"], reason=late.get("reason"))


# ---------------------------------------------------------------------------
# FAULT17 — remote merge accepted, response lost
# ---------------------------------------------------------------------------

def fault17(w: World, ctx):
    check, ids = ctx["check"], _ids
    work_key, rid = w.approved(1701)
    ids(ctx, work_keys=[work_key], request_ids=[rid])

    # The merge lands remotely; the response is lost. Authoritative
    # read-back identifies the actual merge SHA before any retry.
    w.remote.faults["pr-merge"] = "crash-after-create"
    out = w.merge.merge(work_key)
    intent = w.store.merge_intent_for_request(rid)
    pr = w.remote.read_pr(intent["pr_number"])
    check("lost-response-reconciles-actual-sha",
          out["outcome"] == "merged"
          and intent["state"] == "merged"
          and intent["merge_sha"] == pr["merge_sha"]
          and pr["merge_sha"]
          == w.remote.mergers[intent["pr_number"]]["sha"],
          outcome=out["outcome"], intent_state=intent["state"],
          merge_sha=intent["merge_sha"], remote_sha=pr["merge_sha"])
    check("no-blind-repeat", len(_merge_calls(w)) == 1,
          merge_calls=len(_merge_calls(w)))
    ids(ctx, intent_ids=[intent["intent_id"]],
        merge_shas=[intent["merge_sha"]])

    # The request was accepted remotely but BOTH the response and the
    # read-back are lost: the intent parks unresolved — never a blind
    # resend, never a false failure or false success.
    wk2, rid2 = w.approved(1702)
    ids(ctx, work_keys=[wk2], request_ids=[rid2])
    w.remote.faults["pr-merge"] = "crash-after-create"
    real_merge = w.remote.merge_pr

    def merge_then_blind(number, *, method, expected_head, identity):
        try:
            return real_merge(number, method=method,
                              expected_head=expected_head,
                              identity=identity)
        finally:
            # The read outage lands *with* the send — the post-effect
            # read-back cannot answer either.
            w.remote.faults["pr-read"] = "raise"

    w.remote.merge_pr = merge_then_blind
    out2 = w.merge.merge(wk2)
    intent2 = w.store.merge_intent_for_request(rid2)
    observed = [e for e in w.store.event_rows("merge_observed")
                if e.get("work_key") == wk2]
    check("unreadable-outcome-parks-honestly",
          out2["outcome"] == "parked"
          and intent2["state"] == "parked"
          and not intent2["merged"]
          and intent2["merge_sha"] in (None, ""),
          outcome=out2["outcome"], state=intent2["state"],
          merged=intent2["merged"])
    check("parked-emits-unknown-observation",
          bool(observed)
          and json.loads(observed[-1]["detail"])["outcome"]
          == "unknown",
          observed=len(observed))
    # Recovery resolves the parked intent by read-back — the remote's
    # own truth, never a second send. Exactly two remote merges ran
    # across the whole row (one per work), each observed once.
    w.remote.faults.clear()
    w.remote.merge_pr = real_merge
    outcomes = w.merge.reconcile_pending()
    intent2b = w.store.merge_intent_for_request(rid2)
    check("readback-resolves-before-retry",
          outcomes and outcomes[0]["outcome"] == "merged"
          and intent2b["state"] == "merged"
          and intent2b["merge_sha"]
          and len(_merge_calls(w)) == 2,
          resolved=outcomes[0]["outcome"] if outcomes else None,
          state=intent2b["state"], merge_calls=len(_merge_calls(w)))
    ids(ctx, intent_ids=[intent2["intent_id"]],
        merge_shas=[intent2b["merge_sha"]])


# ---------------------------------------------------------------------------
# FAULT18 — cancel/revoke races with preview or merge
# ---------------------------------------------------------------------------

def fault18(w: World, ctx):
    check, ids = ctx["check"], _ids
    # -- fence committed before the merge boundary: no new effect.
    work_key, rid = w.approved(1801)
    ids(ctx, work_keys=[work_key], request_ids=[rid])
    w.store.fence_work(work_key, "canceled", 1)
    out = w.merge.merge(work_key)
    check("fence-before-boundary-no-effect",
          out["outcome"] == "denied" and out["reason"] == "fenced"
          and len(_merge_calls(w)) == 0,
          outcome=out["outcome"], reason=out["reason"],
          merge_calls=len(_merge_calls(w)))
    # The unspent grant is not consumed by the denial.
    req_row = w.store.get_approval_request(rid)
    check("denied-merge-does-not-spend-grant",
          req_row["state"] == "approved", state=req_row["state"])

    # -- effect authorized before the fence: the in-flight merge lands,
    #    the outcome records the race accurately — no destructive
    #    reversal.
    wk2, rid2 = w.approved(1802)
    ids(ctx, work_keys=[wk2], request_ids=[rid2])
    real_merge = w.remote.merge_pr

    def cancel_during_merge(number, *, method, expected_head, identity):
        result = real_merge(number, method=method,
                            expected_head=expected_head,
                            identity=identity)
        w.store.fence_work(wk2, "canceled", 1)
        return result

    w.remote.merge_pr = cancel_during_merge
    out2 = w.merge.merge(wk2)
    intent2 = w.store.merge_intent_for_request(rid2)
    w.remote.merge_pr = real_merge
    check("in-flight-effect-reconciled-accurately",
          out2["outcome"] == "merged"
          and intent2["state"] == "merged"
          and intent2["reason"] == "cancellation-race",
          outcome=out2["outcome"], reason=intent2["reason"])
    pr2 = w.remote.read_pr(intent2["pr_number"])
    check("no-automatic-destructive-reversal",
          pr2["merged"] is True
          and w.remote.pulls[intent2["pr_number"]]["state"] != "closed",
          merged=pr2["merged"])

    # -- late preview callback after the committed fence is rejected.
    wk3 = w.accept(1803)
    w.evidence(wk3, HEAD_A, pr_number="89")
    dep3 = w.deploy(wk3, HEAD_A)
    dep_id = dep3["deployment_id"]
    w.store.fence_work(wk3, "canceled", 1)
    back = w.preview.observe_callback(dep_id)
    check("late-preview-callback-denied",
          back["outcome"] in ("denied", "rejected", "ignored"),
          outcome=back["outcome"], reason=back.get("reason"))

    # -- human external merge records the actual actor — never the
    #    factory approval's identity.
    wk4, rid4 = w.approved(1804)
    ids(ctx, work_keys=[wk4], request_ids=[rid4])

    def human_merge(number, *, method, expected_head, identity):
        w.remote.pr_states[str(number)] = "MERGED"
        w.remote.mergers[str(number)] = {
            "sha": "human-merge-sha", "by": "human-dev"}
        raise RemoteAmbiguity("merge response lost")

    w.remote.merge_pr = human_merge
    out4 = w.merge.merge(wk4)
    intent4 = w.store.merge_intent_for_request(rid4)
    w.remote.merge_pr = real_merge
    check("human-merge-records-actual-actor",
          out4["merge_actor_kind"] == "external"
          and intent4["merged_by"] == "human-dev"
          and intent4["merge_actor_kind"] == "external",
          kind=out4["merge_actor_kind"], by=intent4["merged_by"])


# ---------------------------------------------------------------------------
# FAULT19 — dirty/incompatible base, draft/closed PR, competing
#           automation, bypass-only credentials
# ---------------------------------------------------------------------------

def fault19(w: World, ctx):
    check, ids = ctx["check"], _ids
    # -- dirty/incompatible base: base moved after the PR snapshot →
    #    denied before the send; a mid-flight base race is refused by
    #    the repository's own strict protection and reconciles
    #    not-merged (expected-head + server enforcement close it).
    work_key, rid = w.approved(1901)
    ids(ctx, work_keys=[work_key], request_ids=[rid])
    w.remote.branches["main"] = BASE_B
    out = w.merge.merge(work_key)
    check("dirty-base-denied-before-send",
          out["outcome"] == "denied" and out["reason"] == "base-moved"
          and len(_merge_calls(w)) == 0,
          reason=out["reason"])
    w.remote.branches["main"] = BASE_SHA  # restore before the next PR

    wk2, rid2 = w.approved(1902)
    ids(ctx, work_keys=[wk2], request_ids=[rid2])
    real_merge = w.remote.merge_pr

    def racing_merge(number, *, method, expected_head, identity):
        w.remote.branches["main"] = BASE_B        # base races in
        return real_merge(number, method=method,
                          expected_head=expected_head,
                          identity=identity)

    w.remote.merge_pr = racing_merge
    out2 = w.merge.merge(wk2)
    intent2 = w.store.merge_intent_for_request(rid2)
    w.remote.merge_pr = real_merge
    w.remote.branches["main"] = BASE_SHA
    pr2 = w.remote.read_pr(intent2["pr_number"])
    check("base-race-closed-by-enforcement",
          out2["outcome"] == "not-merged"
          and intent2["state"] == "not-merged"
          and intent2["merged"] == 0 and not pr2["merged"],
          outcome=out2["outcome"], reason=out2["reason"],
          pr_merged=pr2["merged"])

    # -- draft / closed PR blocks (the factory's own linked pull).
    wk3, rid3 = w.approved(1903)
    w.remote.pr_drafts.add(_pr_of(w, wk3))
    out3 = w.merge.merge(wk3)
    check("draft-pr-blocked",
          out3["outcome"] == "denied" and out3["reason"] == "pr-draft",
          reason=out3["reason"])
    wk4, rid4 = w.approved(1904)
    w.remote.pr_states[_pr_of(w, wk4)] = "CLOSED"
    out4 = w.merge.merge(wk4)
    check("closed-pr-blocked",
          out4["outcome"] == "denied"
          and str(out4["reason"]).startswith("pr-not-open"),
          reason=out4["reason"])
    ids(ctx, work_keys=[wk3, wk4], request_ids=[rid3, rid4])

    # -- competing automation: armed auto-merge or the repo's own
    #    auto-merge capability means a competing merge owner — blocked.
    wk5, rid5 = w.approved(1905)
    w.remote.auto_merge = {"enabledBy": {"login": "dependabot"}}
    out5 = w.merge.merge(wk5)
    check("armed-auto-merge-blocked",
          out5["outcome"] == "denied"
          and out5["reason"] == "auto-merge-armed",
          reason=out5["reason"])
    wk6, rid6 = w.approved(1906)
    w.remote.auto_merge = None
    w.remote.capabilities["allow_auto_merge"] = True
    out6 = w.merge.merge(wk6)
    check("repo-auto-merge-capability-blocked",
          out6["outcome"] == "denied"
          and out6["reason"] == "auto-merge-enabled",
          reason=out6["reason"])
    ids(ctx, work_keys=[wk5, wk6], request_ids=[rid5, rid6])

    # -- unsupported enforcement fails the gate: weak/missing
    #    protection, bypassable admins, missing required-check
    #    protection and unsupported methods each deny explicitly —
    #    never a hopeful merge under bypass-only credentials.
    wk7, rid7 = w.approved(1907)
    w.remote.capabilities["allow_auto_merge"] = False
    w.remote.protection = {"protected": False}
    out7 = w.merge.merge(wk7)
    check("unprotected-base-fails-gate",
          out7["outcome"] == "denied"
          and out7["reason"] == "protection-missing",
          reason=out7["reason"])
    wk8, rid8 = w.approved(1908)
    w.remote.protection = {"protected": True, "strict": True,
                           "enforce_admins": False,
                           "required_checks": ["Code Quality & Build",
                                               "Security Scan"]}
    out8 = w.merge.merge(wk8)
    check("bypassable-admins-fails-gate",
          out8["outcome"] == "denied"
          and out8["reason"] == "bypassable-protection",
          reason=out8["reason"])
    wk9, rid9 = w.approved(1909)
    w.remote.protection["enforce_admins"] = True
    w.remote.capabilities["allow_squash_merge"] = False
    out9 = w.merge.merge(wk9)
    check("unsupported-method-fails-gate",
          out9["outcome"] == "denied"
          and out9["reason"] == "method-unsupported",
          reason=out9["reason"])
    ids(ctx, work_keys=[wk7, wk8, wk9],
        request_ids=[rid7, rid8, rid9])
    # auto-merge is never the shortcut — the factory never arms it.
    check("auto-merge-never-enabled",
          w.remote.auto_merge is None
          and not any(c["op"] == "auto-merge-enable"
                      for c in w.remote.calls),
          auto_merge=w.remote.auto_merge)


ROWS = {
    "FAULT13": fault13, "FAULT14": fault14, "FAULT15": fault15,
    "FAULT16": fault16, "FAULT17": fault17, "FAULT18": fault18,
    "FAULT19": fault19,
}
