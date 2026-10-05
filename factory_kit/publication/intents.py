#!/usr/bin/env python3
"""Publication broker — serialized, intent-recorded, fenced remote
effects (issue #10 / Task 2.5, PRD §5.2, §6.4, §7.1).

This is the authority boundary for publishing. The coordinator (the
execution lane) never touches the remote itself; every effect goes
through :class:`PublicationBroker`, which:

- rechecks authorization *at effect time* — repository authority,
  active generation, bound configuration digests, actor authority and
  the operation grant are all re-evaluated under ``BEGIN IMMEDIATE``
  immediately before the remote call — so a fence, revocation or
  config rotation landing between dispatch and the effect denies the
  effect (A2, A4);
- commits a durable intent — task/generation, repository, expected
  revision, permitted operation, target branch/PR — *before* sending
  any effect (A1);
- serializes the operation under ``BEGIN IMMEDIATE``: one
  (work, operation, target) identity, one live intent — concurrent
  attempts converge on the same intent instead of doubling the remote
  operation (A3);
- binds the remote result back to the recorded identity by
  authoritative read-back (repository identity + branch/PR identity),
  not by trusting the call's return value (A1, A5);
- reconciles crash windows on recovery: an intent left ``recorded`` or
  ``applied`` is looked up by work identity — the existing PR is
  discovered and linked rather than blindly republished (A3);
- parks on any uncertainty — two matching PRs, uncertain repository
  identity, ambiguous API outcome — and never closes, deletes or
  overwrites user work (A5);
- quarantines on an *unauthorized mutation attempt* (wrong repository
  or action, stale generation, changed config, expired claim, revoked
  actor): the denied intent is retained as audit evidence, the
  affected work is quarantined and a deduplicated high-severity alert
  fires locally + on the operator channel (A6). A denied attempt that
  is only a pre-existing fence (cancel/revocation already committed)
  records the denial without re-quarantining — the fence is already
  the stronger record.

Workers and descendants receive no broker handle, no controller token
and no unrestricted remote credential — the worker port contract
(policy §4.1, §5.2) keeps them candidate-producers; the mutation
fixture's *only* path to a remote effect is :meth:`request_mutation`
through this broker and its scoped deterministic remote port.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from factory_kit.config import schema
from factory_kit.durable.store import _utcnow
from factory_kit.publication.remote import (
    RemoteAmbiguity,
    RemoteError,
)

__all__ = [
    "OPERATION_BRANCH_PUBLISH",
    "OPERATION_PR_PUBLISH",
    "PublicationBroker",
    "verify_request",
]

#: The two operations the kit's paved endpoint permits. The remote
#: method each one maps to is fixed here — the broker never executes an
#: operation string it did not define, and a request outside the grant
#: is denied before any effect (A2).
OPERATION_BRANCH_PUBLISH = "branch-publish"
OPERATION_PR_PUBLISH = "pr-publish"

_OPERATIONS = {OPERATION_BRANCH_PUBLISH, OPERATION_PR_PUBLISH}

#: Authorization denials that are *violations* — an unauthorized
#: mutation attempt rather than an ordinary fence/race. Violations
#: quarantine the affected work + alert high; the rest just deny.
_VIOLATION_REASONS = {
    "wrong-repository", "operation-not-granted", "actor-revoked",
    "expired-claim", "unregistered-authority",
}

#: Value shapes allowed into a remote effect. ``target``/``base``/repo
#: fields land in ``gh``/``git`` argv, so they are constrained to
#: refname/repo-name charsets — a flag-shaped value can never reach an
#: argument position. ``expected_revision`` is a commit SHA.
_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_REPO_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_FULL_NAME_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}")
_REVISION_RE = re.compile(r"[0-9A-Fa-f]{4,64}")
_ACTOR_RE = re.compile(r"[A-Za-z0-9:_-]{1,100}")


def _shape(rx, value):
    return rx.fullmatch(value) is not None


def verify_request(request):
    """Shape validation for an intent request — a *candidate* only.

    A well-shaped request is not authorized: authorization is decided
    per intent by the broker against the durable registration and the
    bound generation's digests at effect-commit time. Workers never
    reach this layer's authority; they can only submit candidates.

    Returns ``{"ok": True, "value": normalized}`` or
    ``{"ok": False, "reason": code}``.
    """
    if not isinstance(request, dict):
        return {"ok": False, "reason": "malformed"}
    operation = request.get("operation")
    target = str(request.get("target") or "").strip()
    repository = request.get("repository")
    if repository is not None and not isinstance(repository, dict):
        # A non-dict repository value is malformed input — deny it, never
        # crash on it (the worker-facing surface must not raise).
        return {"ok": False, "reason": "bad-repository"}
    repo_id = str((repository or {}).get("repo_id") or "").strip()
    full_name = str((repository or {}).get("full_name") or "").strip()
    if operation not in _OPERATIONS:
        return {"ok": False, "reason": "unknown-operation"}
    if not target:
        return {"ok": False, "reason": "no-target"}
    if not _shape(_TARGET_RE, target) or ".." in target or \
            "//" in target or target.endswith(("/", ".")):
        return {"ok": False, "reason": "bad-target"}
    if not repo_id or not full_name:
        return {"ok": False, "reason": "no-repository"}
    if not _shape(_REPO_ID_RE, repo_id) or \
            not _shape(_FULL_NAME_RE, full_name):
        return {"ok": False, "reason": "bad-repository"}
    expected = str(request.get("expected_revision") or "").strip()
    if operation == OPERATION_BRANCH_PUBLISH and not expected:
        # A branch publish must state the exact revision it may put on
        # the remote — read-back binds on it (A1).
        return {"ok": False, "reason": "no-revision"}
    if expected and not _shape(_REVISION_RE, expected):
        return {"ok": False, "reason": "bad-revision"}
    base = str(request.get("base") or "").strip() or None
    if base is not None and (not _shape(_TARGET_RE, base) or
                             ".." in base or "//" in base):
        return {"ok": False, "reason": "bad-base"}
    actor = str(request.get("actor_ref") or "worker").strip()
    if not _shape(_ACTOR_RE, actor):
        return {"ok": False, "reason": "bad-actor"}
    claim = request.get("claim_expires_epoch")
    if claim is not None:
        try:
            claim = float(claim)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "bad-claim"}
    value = {
        "operation": operation,
        "target": target,
        "repository": {"repo_id": repo_id, "full_name": full_name},
        "expected_revision": expected or None,
        "title": str(request.get("title") or "").strip() or None,
        "base": base,
        "actor_ref": actor,
        "claim_expires_epoch": claim,
    }
    return {"ok": True, "value": value}


class PublicationBroker:
    """The scoped deterministic mutation boundary (§5.2, §6.4).

    ``effective`` is the coordinator's *current* validated manifest —
    its digests are compared to the bound generation's recorded
    digests so a configuration change between dispatch and effect is
    caught even when it has not yet opened a new generation.
    """

    def __init__(self, store, remote, registration, effective, *,
                 clock=None, alert_sink=None, now=None):
        self.store = store
        self.remote = remote
        self.registration = registration
        self.effective = effective
        self.clock = clock or (lambda: time.time())
        self._now = now or time.time
        self._alerts = alert_sink if alert_sink is not None else []

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _alert(self, kind, *, severity="high", work_key=None,
               detail=None):
        """Persist a durable alert + emit on the operator channel.
        Dedup lives in the store (``emit_alert`` ignores a repeated
        (kind, identity, severity)) — repeated denied/ambiguous
        mutations must not re-page (A6). The operator sink receives the
        emission outcome so tests can assert the notification."""
        result = self.store.emit_alert(
            kind, work_key or "-", severity, detail)
        self._alerts.append({"kind": kind, "severity": severity,
                             "work_key": work_key, "detail": detail,
                             "outcome": result["outcome"]})
        return result

    def _work_context(self, work_key):
        work = self.store.get_work(work_key)
        if work is None:
            return None
        return {"work": work, "task_id": work.get("task_id")}

    def _deny_tx(self, intent_id, *, request, ctx, state_reason,
                 violation=False):
        """Denial writes, inside the caller's ``transact`` — the denied
        intent and (on a violation) the quarantine commit atomically.

        The serialized (work, operation, target) identity may already
        carry a committed intent — a *repeated* denial on it converges
        on the existing row instead of violating the unique index (the
        request is still denied, and a violation still quarantines the
        work; the operator alert dedups downstream)."""
        work = ctx.get("work") if ctx else None
        operation = request.get("operation") or "-"
        target = request.get("target")
        repository = request.get("repository")
        repo_id = repository.get("repo_id") \
            if isinstance(repository, dict) else None
        if work is not None and target:
            existing = self.store.find_intent(
                work["work_key"], operation, target)
            if existing is not None:
                if violation:
                    self.store.set_work_state(
                        work["work_key"], "quarantined",
                        reason=state_reason)
                    self.store.record_event(
                        "work_quarantined",
                        work_key=work["work_key"],
                        reason=state_reason,
                        detail="unauthorized mutation attempt")
                return {"outcome": "denied", "reason": state_reason,
                        "intent_id": existing["intent_id"],
                        "violation": violation, "converged": True}
        self.store.insert_intent(
            intent_id,
            work_key=work["work_key"] if work else None,
            seq=None,
            task_id=ctx.get("task_id") if ctx else None,
            generation=work.get("generation") if work else None,
            authority_key=(work or {}).get("authority_key"),
            repo_id=repo_id,
            operation=request.get("operation") or "-",
            expected_revision=request.get("expected_revision"),
            target=request.get("target"),
            actor_ref=request.get("actor_ref"),
            claim_expires_epoch=request.get("claim_expires_epoch"),
            state="denied", reason=state_reason)
        if violation and work is not None:
            self.store.set_work_state(work["work_key"], "quarantined",
                                      reason=state_reason)
            self.store.record_event("work_quarantined",
                                    work_key=work["work_key"],
                                    reason=state_reason,
                                    detail="unauthorized mutation "
                                           "attempt")
        return {"outcome": "denied", "reason": state_reason,
                "intent_id": intent_id, "violation": violation}

    def _deny(self, *, request, ctx, state_reason, violation=False):
        """Persist a denied intent as audit evidence. A *violation*
        denial (unauthorized mutation attempt) also quarantines the
        affected work and alerts high, deduplicated (A6). The alert
        fires only after the evidence + quarantine commit."""
        intent_id = "pub-" + uuid.uuid4().hex[:12]
        with self.store.transact() as tx:
            result = self._deny_tx(intent_id, request=request, ctx=ctx,
                                   state_reason=state_reason,
                                   violation=violation)
        if violation and ctx and ctx.get("work") is not None:
            self._alert("unauthorized-mutation", severity="high",
                        work_key=ctx["work"]["work_key"],
                        detail=state_reason)
        result.pop("violation", None)
        return result

    def _authorize(self, ctx, req):
        """Effect-time authorization recheck — evaluated under the
        broker's serialized transaction in :meth:`publish`.

        Every element of the permit is re-evaluated here, never carried
        over from dispatch time:

        - **repository** — the request's repo must be the registered
          authority and the work's bound repo (wrong repo → violation);
        - **operation** — must be one of the paved operations and not a
          permanently disabled capability (wrong action → violation);
        - **generation** — the work's bound generation must still be
          the registration's active generation (stale → deny);
        - **configuration** — the current effective config's digests
          must equal the digests bound to that generation (changed
          config → deny);
        - **actor** — the requesting actor must still hold authority
          under the current manifest allowlists (revoked → violation);
        - **work** — still active in both the durable store and the
          registration record (parked/canceled → deny);
        - **fence** — the generation must not be fenced (deny, not a
          violation — the fence is already the record).
        """
        work = ctx["work"]
        fence = self.store.fence_state(work["work_key"])
        if fence is not None and fence["fenced"]:
            # The fence is the strongest signal: once the generation is
            # fenced every new effect denies as ``fenced`` — this is a
            # rejection, not a violation (the fence already recorded
            # the quarantine-equivalent).
            return {"ok": False, "reason": "fenced"}
        record = self.registration.get(req["repository"]["repo_id"])
        if record is None:
            return {"ok": False, "reason": "unregistered-authority"}
        if req["repository"]["repo_id"] != work.get("repo_id") or \
                schema.authority_key({"repo_id":
                                      req["repository"]["repo_id"]}) != \
                work.get("authority_key"):
            return {"ok": False, "reason": "wrong-repository"}
        if req["operation"] not in _OPERATIONS or \
                req["operation"] in \
                self.effective.get("endpoint", {}).get("disabled", ()):
            return {"ok": False, "reason": "operation-not-granted"}
        if work.get("state") in ("parked", "quarantined", "blocked",
                                 "canceled", "completed"):
            return {"ok": False, "reason": "work-not-active"}
        bound = None
        for gen in record["generations"]:
            if gen["generation"] == work.get("generation"):
                bound = gen
                break
        if bound is None or \
                work.get("generation") != record["active_generation"]:
            return {"ok": False, "reason": "stale-generation"}
        if record.get("pending_policy"):
            # An unauthorized policy change is parked pending explicit
            # authorization — its digests no longer describe the
            # permit surface this effect would run under.
            return {"ok": False, "reason": "stale-config"}
        reg_work = record.get("work", {}).get(work["work_key"])
        if reg_work is not None and reg_work.get("state") != "active":
            return {"ok": False, "reason": "revoked-work"}
        if schema.effective_digest(self.effective) != \
                bound["config_digest"] or \
                schema.policy_digest(self.effective) != \
                bound["policy_digest"]:
            return {"ok": False, "reason": "stale-config"}
        if not schema.is_execution_authorized(
                self.effective, github_actor=req["actor_ref"]):
            return {"ok": False, "reason": "actor-revoked"}
        return {"ok": True, "bound": bound, "record": record}

    # ------------------------------------------------------------------ #
    # the public surface
    # ------------------------------------------------------------------ #

    def publish(self, work_key, request):
        """One serialized publication attempt for ``work_key``.

        Outcomes: ``applied`` (remote effect confirmed + read-back
        bound), ``converged`` (a racing intent already owns the
        identity), ``denied`` (validation/authority/fence — retained as
        audit), ``parked`` (uncertainty — reconciliation required).
        """
        # 1 — shape only (candidate validation; never authority).
        check = verify_request(request)
        if not check["ok"]:
            ctx = self._work_context(work_key)
            return self._deny(request=request if
                              isinstance(request, dict) else {},
                              ctx=ctx, state_reason=check["reason"])
        req = check["value"]

        ctx = self._work_context(work_key)
        if ctx is None:
            return {"outcome": "denied", "reason": "unknown-work"}
        work = ctx["work"]
        generation = work.get("generation")

        # 2 — serialize the (work, operation, target) identity + run the
        #     permit rechecks under the write lock. Live intents
        #     (recorded/applied/linked/parked) own the identity and
        #     converge — never a second remote operation (A3). A
        #     terminally failed/denied intent is *re-decided*: the full
        #     authorization recheck runs again, and an authorized retry
        #     re-opens the same row — one remote operation still owns
        #     the identity, so a transient failure never permanently
        #     tombstones republication.
        denied = None
        claim = req.get("claim_expires_epoch")
        with self.store.transact() as tx:
            existing = tx.find_intent(work_key, req["operation"],
                                      req["target"])
            if existing is not None and existing["state"] in (
                    "recorded", "applied", "linked", "parked"):
                # A racing attempt already owns the identity: converge
                # on it — never a second remote operation (A3).
                return {"outcome": "converged",
                        "reason": "identity-owned",
                        "intent_id": existing["intent_id"],
                        "state": existing["state"],
                        "remote_ref": existing["remote_ref"]}
            if claim is not None and float(claim) < self.clock():
                # Expired claims deny as violations — the claim's
                # validity window is part of the permit, evaluated at
                # commit time (A2). _deny_tx converges on the existing
                # row when the identity already carries a terminal
                # intent, so a repeated denial never violates the
                # unique index.
                denied = self._deny_tx(
                    "pub-" + uuid.uuid4().hex[:12],
                    request=req, ctx=ctx, state_reason="expired-claim",
                    violation=True)
            else:
                auth = self._authorize(ctx, req)
                if not auth["ok"]:
                    denied = self._deny_tx(
                        "pub-" + uuid.uuid4().hex[:12],
                        request=req, ctx=ctx, state_reason=auth["reason"],
                        violation=auth["reason"] in _VIOLATION_REASONS)
                elif existing is not None:
                    # Terminal failed/denied intent — an authorized
                    # retry re-opens the same serialized identity with
                    # refreshed attempt-bound fields; the unique index
                    # still enforces one row per identity.
                    intent_id = existing["intent_id"]
                    tx.reopen_intent(
                        intent_id, task_id=ctx["task_id"],
                        generation=generation,
                        authority_key=work["authority_key"],
                        repo_id=req["repository"]["repo_id"],
                        expected_revision=req["expected_revision"],
                        actor_ref=req["actor_ref"],
                        claim_expires_epoch=req["claim_expires_epoch"])
                    tx.record_event(
                        "publication_intent_reopened",
                        work_key=work_key,
                        detail=json.dumps(
                            {"intent_id": intent_id,
                             "operation": req["operation"],
                             "target": req["target"],
                             "prior_state": existing["state"]},
                            sort_keys=True))
                else:
                    intent_id = "pub-" + uuid.uuid4().hex[:12]
                    tx.insert_intent(
                        intent_id,
                        work_key=work_key, seq=None,
                        task_id=ctx["task_id"], generation=generation,
                        authority_key=work["authority_key"],
                        repo_id=req["repository"]["repo_id"],
                        operation=req["operation"],
                        expected_revision=req["expected_revision"],
                        target=req["target"], actor_ref=req["actor_ref"],
                        claim_expires_epoch=req["claim_expires_epoch"],
                        state="recorded")
                    tx.record_event(
                        "publication_intent_recorded", work_key=work_key,
                        detail=json.dumps(
                            {"intent_id": intent_id,
                             "operation": req["operation"],
                             "target": req["target"]}, sort_keys=True))
        if denied is not None:
            if denied.pop("violation"):
                self._alert("unauthorized-mutation", severity="high",
                            work_key=work_key,
                            detail=denied["reason"])
            return denied

        # 3 — final pre-effect gate, one transaction: the fence recheck
        #     AND the claim window are re-evaluated in the transaction
        #     that marks the intent applied — no window where the
        #     intent says applied while the fence already landed or the
        #     claim already expired (A2/A4). An expired claim is a
        #     violation: deny + quarantine + deduplicated alert.
        #     A fence landing after this point was authorized before it
        #     — its outcome is reconciled and accurately linked (A4).
        expired = False
        with self.store.transact() as tx:
            fence = tx.fence_state(work_key)
            if fence is not None and fence["fenced"]:
                tx.update_intent(intent_id, state="denied",
                                 reason="fenced")
                return {"outcome": "denied", "reason": "fenced",
                        "intent_id": intent_id}
            if claim is not None and float(claim) < self.clock():
                tx.update_intent(intent_id, state="denied",
                                 reason="expired-claim")
                tx.set_work_state(work_key, "quarantined",
                                  reason="expired-claim")
                tx.record_event("work_quarantined",
                                work_key=work_key,
                                reason="expired-claim",
                                detail="claim expired before effect")
                expired = True
            else:
                tx.update_intent(intent_id, state="applied")
        if expired:
            self._alert("unauthorized-mutation", severity="high",
                        work_key=work_key, detail="expired-claim")
            return {"outcome": "denied", "reason": "expired-claim",
                    "intent_id": intent_id}

        # 4 — pre-effect read-back: the remote may already hold what
        #     this identity's operation would create (a prior lost
        #     response, a raced writer). Converge on one match, park on
        #     more-than-one — never send a second effect into existing
        #     remote state (A3/A5).
        try:
            converged = self._pre_read(intent_id, work_key, req)
        except RemoteError as exc:
            return self._park(intent_id, work_key,
                              "uncertain-repo-identity",
                              str(exc)[:300])
        if converged is not None:
            return converged

        # 5 — last permit check at the instant the effect is about to
        #     leave: the remote pre-read may have taken longer than the
        #     claim's remaining validity window (A2).
        if claim is not None and float(claim) < self.clock():
            with self.store.transact() as tx:
                tx.update_intent(intent_id, state="denied",
                                 reason="expired-claim")
                tx.set_work_state(work_key, "quarantined",
                                  reason="expired-claim")
                tx.record_event("work_quarantined",
                                work_key=work_key,
                                reason="expired-claim",
                                detail="claim expired before effect")
            self._alert("unauthorized-mutation", severity="high",
                        work_key=work_key, detail="expired-claim")
            return {"outcome": "denied", "reason": "expired-claim",
                    "intent_id": intent_id}

        # 6 — the remote effect itself. This is the ONLY place a remote
        #     mutation happens, and only after the durable intent
        #     committed and the fence + claim rechecks passed.
        try:
            result = self._send_effect(req, intent_id, work_key,
                                       generation,
                                       work["authority_key"])
        except RemoteAmbiguity as exc:
            # The effect may have landed; the response was lost. Never
            # republish — reconcile by recorded identity instead
            # (A3/A5).
            return self._reconcile_ambiguous(intent_id, work_key,
                                             req, str(exc))
        except RemoteError as exc:
            with self.store.transact() as tx:
                tx.update_intent(intent_id, state="failed",
                                 reason="remote-error",
                                 detail=str(exc)[:400])
            return {"outcome": "denied", "reason": "remote-error",
                    "intent_id": intent_id, "detail": str(exc)[:400]}

        # 7 — read-back: bind the remote result to the recorded
        #     identity (repository + work/branch/PR), then link.
        return self._confirm(intent_id, work_key, req, result)

    # ------------------------------------------------------------------ #
    # effect + read-back
    # ------------------------------------------------------------------ #

    def _pre_read(self, intent_id, work_key, req):
        """Authoritative pre-effect existence check. Returns None when
        nothing matching the intent's identity exists (the effect may
        be sent), or a terminal outcome dict when the remote already
        carries matching state."""
        identity = self._work_identity(work_key)
        if req["operation"] == OPERATION_PR_PUBLISH:
            prs = self.remote.find_pull_requests(
                head=req["target"], identity=identity)
            if len(prs) > 1:
                self._alert("duplicate-remote-pr", severity="high",
                            work_key=work_key,
                            detail=f"{len(prs)} PRs match head "
                                   f"{req['target']} pre-effect")
                return self._park(intent_id, work_key,
                                  "duplicate-remote-pr",
                                  f"{len(prs)} matching remote PRs "
                                  "before any new effect")
            if len(prs) == 1:
                # The identity's effect already exists remotely —
                # converge on it; no new PR (A3/A5).
                pr = prs[0]
                return self._link(
                    intent_id, work_key,
                    remote_ref=str(pr.get("number") or ""),
                    detail=json.dumps({"url": pr.get("url"),
                                       "converged": True}))
            return None
        seen = self.remote.read_branch(req["target"])
        if seen == req["expected_revision"]:
            return self._link(
                intent_id, work_key,
                remote_ref=f"refs/heads/{req['target']}",
                detail=json.dumps({"sha": seen, "converged": True}))
        if seen is not None:
            # A different revision already occupies the target — a
            # publish here would overwrite user work. Park (A5).
            return self._park(intent_id, work_key, "readback-mismatch",
                              f"branch {req['target']} at {seen}, "
                              f"expected {req['expected_revision']}")
        return None

    def _send_effect(self, req, intent_id, work_key, generation,
                     authority_key):
        identity = {"intent_id": intent_id, "work_key": work_key,
                    "authority_key": authority_key,
                    "generation": generation,
                    "actor_ref": req["actor_ref"]}
        if req["operation"] == OPERATION_BRANCH_PUBLISH:
            return self.remote.publish_branch(
                req["target"], req["expected_revision"], identity)
        return self.remote.open_pr(
            req["target"], req["base"] or "main",
            req["title"] or f"Publish {work_key}", identity)

    def _work_identity(self, work_key):
        work = self.store.get_work(work_key) or {}
        return {"work_key": work_key,
                "authority_key": work.get("authority_key"),
                "generation": work.get("generation")}

    def _confirm(self, intent_id, work_key, req, result):
        """Authoritative read-back binds the remote result to the intent
        identity: repository authority must match the registered repo,
        the pushed branch must report our exact revision, the PR must
        read back on the recorded head."""
        try:
            remote_repo = self.remote.repository_identity()
        except RemoteError as exc:
            return self._park(intent_id, work_key,
                              "uncertain-repo-identity", str(exc)[:300])
        expected_repo = req["repository"]["repo_id"]
        seen_repo = remote_repo.get("repo_id")
        if seen_repo is None or str(seen_repo) != str(expected_repo):
            # Uncertain or drifting repository authority — park, never
            # link what we cannot bind (A5).
            return self._park(
                intent_id, work_key, "uncertain-repo-identity",
                f"remote reported repo_id={seen_repo!r} "
                f"expected {expected_repo!r}")

        if req["operation"] == OPERATION_BRANCH_PUBLISH:
            seen = self.remote.read_branch(req["target"])
            if seen is None:
                return self._park(intent_id, work_key,
                                  "readback-miss",
                                  f"branch {req['target']} not visible")
            if seen != req["expected_revision"]:
                # Someone else's revision is on our target — never
                # overwrite user work; park for reconciliation (A5).
                return self._park(
                    intent_id, work_key, "readback-mismatch",
                    f"branch {req['target']} at {seen}, expected "
                    f"{req['expected_revision']}")
            return self._link(intent_id, work_key,
                              remote_ref=f"refs/heads/{req['target']}",
                              detail=json.dumps({"sha": seen}))

        # pr-publish — read back by head branch identity.
        prs = self.remote.find_pull_requests(
            head=req["target"], identity=self._work_identity(work_key))
        if len(prs) > 1:
            # >1 matching remote PR: the specified high-severity
            # identity alert + park (A5/A6). Never close or delete one.
            self._alert("duplicate-remote-pr", severity="high",
                        work_key=work_key,
                        detail=f"{len(prs)} PRs match head "
                               f"{req['target']}")
            return self._park(intent_id, work_key,
                              "duplicate-remote-pr",
                              f"{len(prs)} matching remote PRs")
        if not prs:
            return self._park(intent_id, work_key,
                              "readback-miss",
                              f"no remote PR on head {req['target']}")
        pr = prs[0]
        return self._link(intent_id, work_key,
                          remote_ref=str(pr.get("number") or
                                       pr.get("url") or ""),
                          detail=json.dumps(
                              {"url": pr.get("url"),
                               "head": pr.get("head")}))

    def _link(self, intent_id, work_key, *, remote_ref, detail=None):
        """Record the confirmed remote ref — the intent row and the
        work's linked PR/effect commit in one durable transaction."""
        with self.store.transact() as tx:
            tx.update_intent(intent_id, state="linked",
                             remote_ref=remote_ref, detail=detail)
            work = tx.get_work(work_key)
            if work is not None and remote_ref:
                if remote_ref.isdigit() and not work.get("linked_pr"):
                    tx._q("UPDATE work SET linked_pr=?, updated_at=?"
                          " WHERE work_key=?",
                          (remote_ref, _utcnow(), work_key))
                tx.record_event(
                    "pr_linked", work_key=work_key,
                    detail=json.dumps(
                        {"intent_id": intent_id,
                         "remote_ref": remote_ref}))
            return {"outcome": "applied", "intent_id": intent_id,
                    "remote_ref": remote_ref}

    def _park(self, intent_id, work_key, reason, detail=None):
        """Commit the intent outcome + park the work + alert — one
        durable transaction so the parked state and its evidence never
        diverge (A5)."""
        with self.store.transact() as tx:
            tx.update_intent(intent_id, state="parked", reason=reason,
                             detail=detail)
            tx.set_work_state(work_key, "parked", reason=reason)
            tx.record_event("work_parked", work_key=work_key,
                            reason=reason, detail=detail)
        if reason in ("duplicate-remote-pr", "uncertain-repo-identity"):
            self._alert(reason, severity="high", work_key=work_key,
                        detail=detail)
        else:
            self._alert("publication-parked", severity="high",
                        work_key=work_key, detail=detail)
        return {"outcome": "parked", "reason": reason,
                "intent_id": intent_id}

    def _reconcile_ambiguous(self, intent_id, work_key, req, detail):
        """Ambiguous remote outcome (response lost / timeout): reconcile
        by recorded identity — find what actually exists on the remote
        before declaring anything (A3/A5)."""
        if req["operation"] == OPERATION_PR_PUBLISH:
            prs = self.remote.find_pull_requests(
                head=req["target"],
                identity=self._work_identity(work_key))
            if len(prs) > 1:
                self._alert("duplicate-remote-pr", severity="high",
                            work_key=work_key,
                            detail=f"{len(prs)} PRs match head "
                                   f"{req['target']}")
                return self._park(intent_id, work_key,
                                  "duplicate-remote-pr",
                                  f"{len(prs)} matching remote PRs")
            if len(prs) == 1:
                pr = prs[0]
                return self._link(
                    intent_id, work_key,
                    remote_ref=str(pr.get("number") or ""),
                    detail=json.dumps({"url": pr.get("url"),
                                       "reconciled": True}))
            # Remote shows nothing — the effect did not land. The intent
            # stays recorded so a later authorized attempt converges on
            # the same identity — never a second remote op.
            return self._park(intent_id, work_key,
                              "ambiguous-outcome", detail)
        # branch-publish ambiguous: read the branch; if it carries our
        # revision the effect landed and is bound; anything else parks.
        seen = self.remote.read_branch(req["target"])
        if seen == req["expected_revision"]:
            return self._link(intent_id, work_key,
                              remote_ref=f"refs/heads/{req['target']}",
                              detail=json.dumps(
                                  {"sha": seen, "reconciled": True}))
        return self._park(intent_id, work_key,
                          "ambiguous-outcome", detail)

    # ------------------------------------------------------------------ #
    # crash-window recovery
    # ------------------------------------------------------------------ #

    def reconcile_pending(self):
        """Startup/recovery pass: every intent committed before an
        effect but never resolved is reconciled by identity — the
        remote is queried for what the recorded identity *would* have
        created; whatever exists is linked, whatever is ambiguous
        parks. Blind republish is impossible: no path here sends an
        effect (A3)."""
        outcomes = []
        for intent in self.store.pending_intents():
            work_key = intent["work_key"]
            if work_key is None:
                continue
            if intent["operation"] == OPERATION_PR_PUBLISH:
                prs = self.remote.find_pull_requests(
                    head=intent["target"],
                    identity=self._work_identity(work_key))
                if len(prs) == 1:
                    pr = prs[0]
                    outcomes.append(self._link(
                        intent["intent_id"], work_key,
                        remote_ref=str(pr.get("number") or ""),
                        detail=json.dumps(
                            {"url": pr.get("url"),
                             "recovered": True})))
                elif len(prs) > 1:
                    self._alert("duplicate-remote-pr",
                                severity="high", work_key=work_key,
                                detail=f"{len(prs)} PRs match head "
                                       f"{intent['target']}")
                    outcomes.append(self._park(
                        intent["intent_id"], work_key,
                        "duplicate-remote-pr",
                        f"{len(prs)} matching remote PRs"))
                else:
                    outcomes.append(self._park(
                        intent["intent_id"], work_key,
                        "ambiguous-outcome",
                        "no remote PR found on recovery"))
            else:
                seen = self.remote.read_branch(intent["target"])
                if seen is not None and \
                        seen == intent["expected_revision"]:
                    outcomes.append(self._link(
                        intent["intent_id"], work_key,
                        remote_ref=f"refs/heads/{intent['target']}",
                        detail=json.dumps(
                            {"sha": seen, "recovered": True})))
                elif seen is None:
                    outcomes.append(self._park(
                        intent["intent_id"], work_key,
                        "ambiguous-outcome",
                        "target branch absent on recovery"))
                else:
                    outcomes.append(self._park(
                        intent["intent_id"], work_key,
                        "readback-mismatch",
                        f"branch {intent['target']} at {seen}"))
        return outcomes

    # ------------------------------------------------------------------ #
    # mutation audit (A6 fixture path)
    # ------------------------------------------------------------------ #

    def request_mutation(self, request):
        """The mutation-fixture entry point — the *only* way a worker,
        test or descendant process can reach remote authority. Every
        denial is durable evidence; an authority denial quarantines the
        affected work and alerts high (A6)."""
        check = verify_request(request)
        work_key = str((request or {}).get("work_key") or "") \
            if isinstance(request, dict) else ""
        if not check["ok"]:
            ctx = self._work_context(work_key) if work_key else None
            return self._deny(
                request=request if isinstance(request, dict) else {},
                ctx=ctx, state_reason=check["reason"],
                violation=check["reason"] in
                ("unknown-operation", "no-repository"))
        if not work_key:
            return self._deny(request=check["value"], ctx=None,
                              state_reason="no-work")
        return self.publish(work_key, request)
