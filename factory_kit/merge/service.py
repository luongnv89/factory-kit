#!/usr/bin/env python3
"""Protected conditional merge + authoritative outcome reconciliation
— issue #17 / Task 3.3, PRD §3.2 F12, §5.2, §6.4, §7.1, §7.3.

:class:`MergeService` is the single merge owner — the only code path
that can turn a durable human approval into a remote merge. It owns no
scheduler and no lifecycle transitions; Hermes/lifecycle control keeps
task state and fencing, and GitHub stays authoritative for the merge
outcome. The service's whole contract:

- **Revalidation (A1/A2/A5)** — immediately before any spend and again
  inside the spend commit, every condition is re-evaluated: work +
  fence + pause, active generation, bound config/policy digests, the
  deciding actor's current authorization, approval state + expiry, the
  bound evidence set and preview identity — and on the remote side the
  PR's open/non-draft/mergeable state, its exact head and base, every
  required check on that head, an armed auto-merge (a competing merge
  owner), and the repository's protection/capability surface. Stale or
  failed smoke older than ``max_smoke_age_minutes`` blocks the merge
  *and* revokes the approval (A2).
- **Atomic spend (A3)** — one ``BEGIN IMMEDIATE`` transaction consumes
  the one-use grant (``ApprovalService.consume_tx``) and commits the
  durable ``merge_intents`` row — request/decision identities,
  expected head/base, method and actor — in the same commit. The
  per-request unique index makes the grant's spend single; the
  live-slot partial index makes a second concurrent merge converge
  instead of minting a second effect.
- **Protected conditional send (A4)** — the only remote effect is the
  supported method's expected-head merge (``sha`` precondition), and
  it is only safe because the repository enforces strict up-to-date
  base + required checks + admin enforcement: the base/check race
  between the guard's read and the mutation is closed by the remote,
  not by hope. No protection → merge unsupported. Auto-merge is never
  enabled, bypassed, or used.
- **Authoritative outcome (A6)** — the merge call's response is never
  trusted. The PR is read back; ``merged`` requires the remote's own
  merged flag + merge commit SHA + recorded merge actor + read-back
  time. A lost response or refusal is reconciled by read-back *before*
  any retry; an unreadable or contradictory state parks the intent
  ``parked`` (holding the live slot), emits ``merge_observed``
  ``unknown`` and alerts — never a blind resend.
- **Cancellation races + human merges (A7)** — a fence/cancel/pause
  landing before the send boundary invalidates the intent with no
  effect; a fence landing *after* the effect records the actual remote
  outcome with the race explanation. A merge the remote attributes to
  a different actor is recorded as external — the actual actor is
  preserved and the factory approval never claims it.
"""

from __future__ import annotations

import time
import uuid

from factory_kit.config import schema
from factory_kit.durable.store import _utcnow
from factory_kit.execution.limits import TERMINAL_WORK_STATES
from factory_kit.publication.remote import (
    RemoteAmbiguity,
    RemoteError,
)

from .guard import evaluate_merge_guard

__all__ = ["MergeService"]

#: Guard blockers whose cause voids the approval itself (A2): a stale
#: or failed smoke/preview is dead evidence and an expired grant is
#: dead authority — the grant bound to it is revoked in the same
#: commit that records the denial, so retry requires reverification +
#: a fresh human approval.
_REVOKE_ON_DENY = {
    "expired",
    "smoke-stale", "smoke-failed", "smoke-unobserved",
    "preview-moved", "preview-not-verified", "preview-unexpected",
    "preview-contract-mismatch",
}


class MergeService:
    """The single deterministic merge owner (§5.2, §6.4, F12).

    - ``store`` — :class:`IntakeStore`; every intent, outcome and event
      commits durably before it is reported.
    - ``remote`` — :class:`RemotePort`; the only path to the merge
      credential. Workers and untrusted processes never receive it.
    - ``approval`` — :class:`ApprovalService`; ``consume_tx`` is the
      atomic grant-spend seam the intent transaction uses.
    - ``registrations`` — :class:`RegistrationStore`; the active
      generation binding is rechecked through the approval seam.
    - ``configs`` — ``{repo_id: effective}`` validated manifests; the
      *current* manifest supplies the merge contract (method, expiry,
      smoke age) and is never cached into the grant.
    - ``now`` — injectable epoch clock shared with the store/fixtures
      so expiry and smoke-age boundaries test deterministically.
    - ``alert_sink`` — optional operator-channel sink (list or
      callable); the durable alert row commits first — a lost push
      never loses the record (§7.3).
    """

    def __init__(self, store, remote, approval, registrations, configs,
                 *, now=None, alert_sink=None):
        self.store = store
        self.remote = remote
        self.approval = approval
        self.registrations = registrations
        self.configs = dict(configs)
        self._now = now or time.time
        self._alerts = alert_sink

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _alert(self, kind, identity, severity, detail=None):
        """Persist the alert row (deduplicated) then best-effort emit —
        the durable row is the local visibility (§7.3)."""
        result = self.store.emit_alert(kind, identity, severity, detail)
        sink = self._alerts
        try:
            if callable(sink):
                sink({"kind": kind, "identity": identity,
                      "severity": severity, "detail": detail,
                      "outcome": result["outcome"]})
            elif sink is not None:
                sink.append({"kind": kind, "identity": identity,
                             "severity": severity, "detail": detail,
                             "outcome": result["outcome"]})
        except Exception:
            pass            # the durable row is already committed
        return result

    def _denied(self, work_key, reason, *, request=None,
                intent_id=None, blockers=None, revoke=False):
        """One explicit durable denial: ``merge_denied`` event + (when
        the blocker voids the grant, A2) the approval's revocation in
        the same commit. No merge intent is minted — the grant was
        never spent."""
        with self.store.transact() as tx:
            tx.record_typed_event(
                "merge_denied", work_key=work_key, reason=reason,
                properties={
                    "request_id": (request or {}).get("request_id"),
                    "intent_id": intent_id,
                    "blockers": list(blockers) if blockers else None})
            if revoke and request is not None:
                fresh = tx.get_approval_request(request["request_id"])
                if fresh is not None and fresh["state"] in \
                        self.store.APPROVAL_LIVE_STATES:
                    tx.invalidate_approval_tx(
                        fresh, reason,
                        state="expired" if reason == "expired"
                        else "invalidated")
        return {"outcome": "denied", "reason": reason,
                "request_id": (request or {}).get("request_id"),
                "blockers": list(blockers or [reason])}

    # ------------------------------------------------------------------ #
    # the guarded merge (A1–A5)
    # ------------------------------------------------------------------ #

    def merge(self, work_key, *, request_id=None):
        """Attempt the approved merge for ``work_key``.

        Outcomes: ``merged`` (authoritative read-back confirmed the
        remote merge), ``not-merged`` (the remote refused — the intent
        resolved; a fresh approval may retry), ``parked`` (the outcome
        is unknown — the intent holds the live slot until
        reconciliation resolves it), ``denied`` (a guard/commit-time
        condition named explicitly — no remote effect left this
        process), ``converged`` (the approval's spend already minted
        its intent — the existing row reports or reconciles)."""
        work = self.store.get_work(work_key)
        if work is None:
            return {"outcome": "denied", "reason": "unknown-work",
                    "work_key": work_key}
        effective = self.configs.get(work["repo_id"])

        # -- the grant this call would spend: an explicit request_id
        #    binds only itself; otherwise the work's live *approved*
        #    request is the unambiguous target (A3).
        if request_id:
            request = self.store.get_approval_request(request_id)
            if request is not None and \
                    request["work_key"] != work_key:
                return self._denied(work_key, "target-mismatch",
                                    request=request)
        else:
            request = None
            for row in self.store.approval_request_rows(work_key):
                if row["state"] == "approved":
                    request = row
                    break

        # -- converge on the committed spend: a consumed grant's intent
        #    is the only merge the approval can ever mint, so a repeat
        #    call never re-enters the send path — a live intent
        #    resolves by read-back and a terminal one reports its
        #    recorded outcome (A3). The lookup is by the named/bound
        #    request first, then the work's live slot.
        intent = None
        if request is not None:
            intent = self.store.merge_intent_for_request(
                request["request_id"])
        if intent is None:
            # The work's one live merge slot converges *every* caller —
            # a fresh approved request included: while a recorded or
            # parked intent stands (even one a different request
            # minted), its owner resolves by read-back, and a second
            # spend must never land on the live-slot unique index.
            intent = self.store.live_merge_intent(work_key)
        if intent is None and request is None and request_id is None:
            # No spendable grant stands — a committed intent may still
            # own the merge: the last terminal outcome is the work's
            # merge position to report. A *fresh* approved request
            # never takes this path, so retry after ``not-merged``
            # still spends.
            rows = self.store.merge_intent_rows(work_key)
            intent = rows[-1] if rows else None
        if intent is not None:
            if intent["state"] in self.store.MERGE_LIVE_STATES:
                if intent["state"] == "recorded":
                    # The spend committed but the owner's send/resolve
                    # is still in flight — a second caller owns nothing
                    # it can read-back honestly (an unmerged read could
                    # just be mid-flight). Report the position; the
                    # owner resolves, and a genuinely orphaned intent
                    # settles on the recovery pass — never a resend.
                    return {"outcome": "converged",
                            "reason": "in-flight",
                            "request_id": intent["request_id"],
                            "intent_id": intent["intent_id"],
                            "state": intent["state"],
                            "work_key": work_key}
                # A parked intent's owner already finished its send —
                # resolving by read-back is the documented path (A6).
                resolved = self._resolve_intent(intent)
                resolved["converged"] = True
                return resolved
            return {"outcome": "converged", "reason": "intent-owned",
                    "request_id": intent["request_id"],
                    "intent_id": intent["intent_id"],
                    "state": intent["state"],
                    "merged": bool(intent.get("merged")),
                    "merge_sha": intent.get("merge_sha"),
                    "merged_by": intent.get("merged_by"),
                    "work_key": work_key}

        if request is None:
            # No committed intent either — name a live request's real
            # state when one stands (awaiting/reviewing), else deny.
            for row in self.store.approval_request_rows(work_key):
                if row["state"] in self.store.APPROVAL_LIVE_STATES:
                    return self._denied(
                        work_key, f"state-{row['state']}", request=row)
            return self._denied(work_key, "no-approved-request")
        if request["state"] != "approved":
            return self._denied(
                work_key, f"state-{request['state']}", request=request)

        merge_cfg = ((effective or {}).get("endpoint") or {}) \
            .get("merge") or {}
        max_smoke_age_s = float(merge_cfg.get(
            "max_smoke_age_minutes",
            schema.DEFAULT_ENDPOINT["max_smoke_age_minutes"])) * 60.0

        # -- remote reads, fresh (A1): the PR, its current base SHA,
        #    the head's check-runs, the base's protection, the repo's
        #    capabilities and the credential's identity. An unreadable
        #    remote means unprovable conditions — the merge blocks
        #    before any spend (A5); nothing was sent, so nothing is
        #    ambiguous and nothing parks.
        try:
            pr = self.remote.read_pr(request["pr_number"])
            base_sha = self.remote.read_branch(
                pr.get("base")) if pr else None
            runs = self.remote.check_runs(request["head_sha"])
            protection = self.remote.read_branch_protection(
                pr.get("base")) if pr else None
            capabilities = self.remote.repository_capabilities()
            actor_login = (self.remote.actor_identity() or {}) \
                .get("login")
        except (RemoteError, RemoteAmbiguity) as exc:
            return self._denied(
                work_key,
                f"remote-unavailable:{type(exc).__name__}",
                request=request, blockers=["remote-unavailable"])

        # -- the full merge-time gate (A1/A2/A4/A5).
        guard = evaluate_merge_guard(
            request=request, work=work, effective=effective,
            fence=self.store.fence_state(work_key),
            pause=self.store.pause_info(work_key),
            evidence=self.store.latest_evidence(work_key),
            preview=self.store.active_preview(work_key),
            pr=pr, base_sha=base_sha, runs=runs,
            protection=protection, capabilities=capabilities,
            actor_login=actor_login, now=self._now(),
            max_smoke_age_s=max_smoke_age_s)
        if not guard["ok"]:
            return self._denied(
                work_key, guard["reason"], request=request,
                blockers=guard["blockers"],
                revoke=guard["reason"] in _REVOKE_ON_DENY)
        bound = guard["bound"]

        # -- the atomic spend: one BEGIN IMMEDIATE transaction consumes
        #    the one-use grant and commits the durable intent — the
        #    approval can never mint two effects, and a concurrent
        #    merge converges on this row (A3). ``consume_tx`` re-runs
        #    the whole durable lapse set inside this commit.
        intent_id = f"mrg-{uuid.uuid4().hex[:12]}"
        denied = None
        with self.store.transact() as tx:
            consumed = self.approval.consume_tx(
                tx, request["request_id"])
            if consumed["outcome"] != "consumed":
                denied = consumed["reason"]
            else:
                tx.insert_merge_intent(
                    intent_id, work_key=work_key, seq=tx.next_seq(),
                    task_id=work.get("task_id"),
                    generation=work.get("generation"),
                    authority_key=work.get("authority_key"),
                    repo_id=work.get("repo_id"),
                    request_id=request["request_id"],
                    decision_id=consumed.get("decision_id"),
                    pr_number=bound["pr_number"],
                    expected_head=bound["expected_head"],
                    expected_base_name=bound["expected_base_name"],
                    expected_base_sha=bound["expected_base_sha"],
                    merge_method=bound["merge_method"],
                    actor_ref=consumed.get("actor_ref"),
                    state="recorded",
                    expires_epoch=consumed.get("expires_epoch"))
                tx.record_typed_event(
                    "merge_intent_recorded", work_key=work_key,
                    properties={
                        "intent_id": intent_id,
                        "request_id": request["request_id"],
                        "decision_id": consumed.get("decision_id"),
                        "pr_number": bound["pr_number"],
                        "expected_head": bound["expected_head"],
                        "expected_base_name":
                            bound["expected_base_name"],
                        "expected_base_sha":
                            bound["expected_base_sha"],
                        "merge_method": bound["merge_method"],
                        "actor_ref": consumed.get("actor_ref")})
        if denied is not None:
            # A concurrent merge consumed the grant first — the intent
            # it minted is the only effect this approval can ever have;
            # converge on it instead of reporting a false denial (A3).
            # A lapse denial with no committed intent still denies.
            existing = self.store.merge_intent_for_request(
                request["request_id"])
            if existing is not None:
                if existing["state"] == "recorded":
                    return {"outcome": "converged",
                            "reason": "in-flight",
                            "request_id": existing["request_id"],
                            "intent_id": existing["intent_id"],
                            "state": existing["state"],
                            "work_key": work_key}
                if existing["state"] in self.store.MERGE_LIVE_STATES:
                    resolved = self._resolve_intent(existing)
                    resolved["converged"] = True
                    return resolved
                return {"outcome": "converged",
                        "reason": "intent-owned",
                        "request_id": existing["request_id"],
                        "intent_id": existing["intent_id"],
                        "state": existing["state"],
                        "merged": bool(existing.get("merged")),
                        "merge_sha": existing.get("merge_sha"),
                        "merged_by": existing.get("merged_by"),
                        "work_key": work_key}
            return self._denied(work_key, denied, request=request)

        # -- the committed boundary (A7): one last durable gate between
        #    the spend and the wire — a fence/pause/expiry/terminal
        #    landing *after* this commit races the in-flight effect and
        #    is explained by read-back; landing *before* forbids the
        #    effect outright and invalidates the intent.
        race = None
        with self.store.transact() as tx:
            fence = tx.fence_state(work_key)
            work_now = tx.get_work(work_key)
            pause = tx.pause_info(work_key)
            now = self._now()
            if fence is not None and fence["fenced"]:
                race = "fenced"
            elif work_now is None or work_now.get("state") in \
                    TERMINAL_WORK_STATES:
                race = "work-terminal"
            elif pause.get("pause_state") in ("requested", "paused"):
                race = "task-paused"
            elif now > float(consumed.get("expires_epoch") or 0):
                race = "expired"
            if race is not None:
                tx.update_merge_intent(intent_id, state="invalidated",
                                       reason=race)
                tx.record_typed_event(
                    "merge_denied", work_key=work_key, reason=race,
                    properties={"request_id": request["request_id"],
                                "intent_id": intent_id})
            else:
                tx.update_merge_intent(intent_id, sent_at=_utcnow())
        if race is not None:
            return {"outcome": "denied", "reason": race,
                    "request_id": request["request_id"],
                    "intent_id": intent_id}

        # -- the one remote effect: the supported method's expected-
        #    head conditional merge under repository-enforced
        #    protections (A3/A4). Auto-merge is never enabled; the
        #    response is never the outcome.
        identity = {"intent_id": intent_id, "work_key": work_key,
                    "authority_key": work.get("authority_key"),
                    "generation": work.get("generation"),
                    "actor_ref": consumed.get("actor_ref")}
        send_status = "sent"
        send_detail = None
        try:
            self.remote.merge_pr(
                bound["pr_number"], method=bound["merge_method"],
                expected_head=bound["expected_head"],
                identity=identity)
        except RemoteAmbiguity as exc:
            send_status = "ambiguous"
            send_detail = str(exc)[:400]
        except RemoteError as exc:
            send_status = "refused"
            send_detail = str(exc)[:400]

        # -- the authoritative read-back resolves everything (A6).
        return self._resolve_intent(
            self.store.get_merge_intent(intent_id),
            send_status=send_status, send_detail=send_detail)

    # ------------------------------------------------------------------ #
    # authoritative outcome (A6) + recovery reconciliation
    # ------------------------------------------------------------------ #

    def _resolve_intent(self, intent, *, send_status=None,
                        send_detail=None):
        """Classify one live intent by authoritative read-back.

        ``merged`` is claimed only on the remote's own merged flag +
        merge commit SHA (A6); an unmerged read-back resolves the
        refusal honestly; an unreadable/contradictory read parks the
        intent ``parked`` with the ``unknown`` event + alert. The
        remote-recorded actor is always preserved — a merge somebody
        else landed is theirs, never the factory approval's (A7).

        ``send_status`` is what *this* call observed of the wire:
        ``"sent"`` (the merge call returned success), ``"refused"`` /
        ``"ambiguous"`` (it raised), or ``None`` (a reconciler — the
        send may never have run; ``intent.sent_at`` is the durable
        record of whether one ever did)."""
        work_key = intent["work_key"]
        request = self.store.get_approval_request(
            intent["request_id"]) or {}
        try:
            pr = self.remote.read_pr(intent["pr_number"])
        except (RemoteError, RemoteAmbiguity) as exc:
            return self._park_intent(
                intent, "merge-outcome-unknown",
                f"read-back unavailable: {type(exc).__name__} — "
                f"{str(exc)[:200]}", request=request)
        if pr is None:
            return self._park_intent(
                intent, "merge-outcome-unknown",
                "PR vanished on read-back — the remote cannot "
                "corroborate the intent's identity", request=request)

        merged = bool(pr.get("merged")) and bool(pr.get("merge_sha"))
        if merged:
            return self._resolve_merged(
                intent, pr, request, send_status=send_status,
                send_detail=send_detail)

        # Not merged per the remote — unless this call's wire answered
        # success, which contradicts the read: that contradiction is
        # unknown, parked (A6). A reconciler or a failed send with an
        # unmerged read-back is a clean ``not-merged``.
        if send_status == "sent":
            return self._park_intent(
                intent, "merge-outcome-unknown",
                "merge call reported success but the PR read-back "
                "shows it unmerged — outcomes disagree",
                request=request)
        reason = "merge-refused" if send_status == "refused" \
            else "merge-not-applied"
        return self._resolve_not_merged(
            intent, pr, request, reason=reason,
            detail=send_detail)

    def _resolve_merged(self, intent, pr, request, *,
                        send_status=None, send_detail=None):
        """The remote confirms the merge: persist the full §6.4 outcome
        — actual merge commit, remote-recorded actor and kind,
        read-back time — and emit ``merge_observed``. A non-factory
        actor is preserved verbatim and alerted: the merge happened,
        but under a human's authority, not the consumed grant (A7)."""
        work_key = intent["work_key"]
        try:
            actor_login = (self.remote.actor_identity() or {}) \
                .get("login")
        except (RemoteError, RemoteAmbiguity):
            # The credential's login is itself unreadable — the merged
            # outcome still records, with the actor honestly unknown.
            actor_login = None
        merged_by = pr.get("merged_by")
        actor_kind = ("factory" if merged_by == actor_login
                      else "external" if merged_by else "unknown")
        fence = self.store.fence_state(work_key)
        raced = fence is not None and fence["fenced"]
        reason = "cancellation-race" if raced else "merged"
        detail = None
        if raced:
            detail = ("fence/cancel committed while the conditional "
                      "merge was in flight — the remote outcome is "
                      "recorded, never reversed")
        if send_status in ("refused", "ambiguous"):
            detail = ((detail + "; ") if detail else "") + \
                f"send reported {send_status}: " \
                f"{(send_detail or '')[:200]}"
        with self.store.transact() as tx:
            tx.update_merge_intent(
                intent["intent_id"], state="merged", reason=reason,
                merged=1, merge_sha=pr.get("merge_sha"),
                merged_by=merged_by, merge_actor_kind=actor_kind,
                read_back_at=_utcnow(), detail=detail)
            if reason == "merged":
                # The lane's ``reviewed`` boundary left the work
                # ``active``; the authoritative merge is the one place
                # ``completed`` is reached — committed atomically with
                # the intent so the work and its merge outcome can
                # never diverge. A cancellation race keeps the fence's
                # terminal state.
                tx.set_work_state(work_key, "completed",
                                  reason="merged")
                tx.enqueue_work(work_key, tx.next_seq(),
                                state="done", reason="merged")
            tx.record_typed_event(
                "merge_observed", work_key=work_key, reason=reason,
                properties={
                    "intent_id": intent["intent_id"],
                    "request_id": intent["request_id"],
                    "pr_number": str(intent.get("pr_number")),
                    "expected_head": intent.get("expected_head"),
                    "outcome": "merged",
                    "merge_sha": pr.get("merge_sha"),
                    "merged_by": merged_by,
                    "merge_actor_kind": actor_kind,
                    "read_back_at": _utcnow()})
        if actor_kind == "external":
            self._alert(
                "merge-actor-external", work_key, "high",
                f"PR {intent.get('pr_number')} merged by "
                f"{merged_by!r} — not the factory credential "
                f"{actor_login!r}; the human merge is recorded, never "
                "attributed to the consumed approval")
        elif actor_kind == "unknown":
            self._alert(
                "merge-actor-unknown", work_key, "medium",
                f"PR {intent.get('pr_number')} merged but the remote "
                "recorded no merge actor")
        return {"outcome": "merged", "reason": reason,
                "intent_id": intent["intent_id"],
                "request_id": intent["request_id"],
                "merge_sha": pr.get("merge_sha"),
                "merged_by": merged_by,
                "merge_actor_kind": actor_kind,
                "work_key": work_key}

    def _resolve_not_merged(self, intent, pr, request, *, reason,
                            detail=None):
        """The remote's own read says the merge did not happen — a
        clean refusal or a request lost before the effect. Terminal
        for this intent; retryable only through a fresh approval (A6)."""
        work_key = intent["work_key"]
        with self.store.transact() as tx:
            tx.update_merge_intent(
                intent["intent_id"], state="not-merged", reason=reason,
                merged=0, read_back_at=_utcnow(), detail=detail)
            tx.record_typed_event(
                "merge_observed", work_key=work_key, reason=reason,
                properties={
                    "intent_id": intent["intent_id"],
                    "request_id": intent["request_id"],
                    "pr_number": str(intent.get("pr_number")),
                    "expected_head": intent.get("expected_head"),
                    "outcome": "not-merged",
                    "merge_actor_kind": "none",
                    "read_back_at": _utcnow()})
        return {"outcome": "not-merged", "reason": reason,
                "intent_id": intent["intent_id"],
                "request_id": intent["request_id"],
                "work_key": work_key}

    def _park_intent(self, intent, reason, detail, *, request=None):
        """Unknown outcome — park the intent (it keeps the work's live
        merge slot, so nothing races a second merge into an unresolved
        remote), emit ``merge_observed`` ``unknown`` and alert (A6).
        Reconciliation — never a blind resend — resolves it."""
        work_key = intent["work_key"]
        with self.store.transact() as tx:
            tx.update_merge_intent(
                intent["intent_id"], state="parked", reason=reason,
                detail=detail)
            tx.record_typed_event(
                "merge_observed", work_key=work_key, reason=reason,
                properties={
                    "intent_id": intent["intent_id"],
                    "request_id": intent["request_id"],
                    "pr_number": str(intent.get("pr_number")),
                    "expected_head": intent.get("expected_head"),
                    "outcome": "unknown"})
        self._alert("merge-outcome-unknown", work_key, "high",
                    f"intent {intent['intent_id']}: {reason} — "
                    f"{detail}")
        return {"outcome": "parked", "reason": reason,
                "intent_id": intent["intent_id"],
                "request_id": intent["request_id"],
                "work_key": work_key}

    # ------------------------------------------------------------------ #
    # recovery-facing reconciliation (the Hermes cadence calls in)
    # ------------------------------------------------------------------ #

    def reconcile_pending(self):
        """Resolve every committed-but-unresolved merge intent by
        remote read-back — the crash window (``recorded``) and the
        parked unknowns. No path here sends an effect: a ``recorded``
        intent whose send never ran resolves ``not-merged`` and the
        consumed grant stays spent — retry is a fresh approval (A6)."""
        outcomes = []
        for intent in self.store.pending_merge_intents():
            try:
                outcomes.append(self._resolve_intent(intent))
            except RemoteError as exc:
                outcomes.append({"outcome": "error",
                                 "intent_id": intent["intent_id"],
                                 "work_key": intent["work_key"],
                                 "reason": type(exc).__name__})
            except Exception as exc:
                outcomes.append({"outcome": "error",
                                 "intent_id": intent["intent_id"],
                                 "work_key": intent["work_key"],
                                 "reason": type(exc).__name__})
        return outcomes

    # ------------------------------------------------------------------ #
    # inspectability
    # ------------------------------------------------------------------ #

    def status(self, work_key):
        """The work's merge position — the live/terminal intent and the
        durable outcome fields §6.4 names. ``merged`` is reported only
        from the recorded read-back, never from a call's response."""
        intents = self.store.merge_intent_rows(work_key)
        live = self.store.live_merge_intent(work_key)
        last = intents[-1] if intents else None
        return {
            "work_key": work_key,
            "state": live["state"] if live else
            (last["state"] if last else "none"),
            "live_intent": live,
            "intents": intents,
            "merged": bool(last and last.get("merged")),
            "merge_sha": last.get("merge_sha") if last else None,
            "merged_by": last.get("merged_by") if last else None,
            "read_back_at":
                last.get("read_back_at") if last else None,
        }
