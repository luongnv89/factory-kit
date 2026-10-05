#!/usr/bin/env python3
"""Intake decision pipeline — F02 authorized durable intake (issue #8).

:class:`IntakeService` is the single point where webhook and
reconciliation envelopes become durable accept/reject decisions. Every
check runs inside one :meth:`IntakeStore.transact` boundary, so the
dedup lookup, the authorization evaluation and the work/delivery/event
writes are one atomic durable record — "durable logical work identity
is established before it is reported as safely queued" (F02/A1).

Decision order (each deny persists only a redacted ``work_rejected``
event — reason code + identities + config digest, never bodies):

1. ``malformed-event`` — the envelope cannot be normalized (missing
   delivery ID, bad channel/action, non-integer issue, …). The denial
   still persists when a delivery identity is recoverable.
2. ``deduplicated`` — the delivery was already recorded; the recovered
   row answers, not the acknowledgement set.
3. ``invalid-signature`` — webhook channel only; the reconciliation
   channel is authenticated by the local poller's own credentials.
4. ``factory-self-event`` — a sender in ``factory_actors`` is the kit's
   own automation; its notifications can never trigger intake (A4).
5. ``unregistered-repository`` / ``registration-not-ready`` — no
   registration row, or a readiness verdict that is not ``ready`` /
   ``dispatch`` not ``allowed``.
6. ``issue-closed`` / ``not-opted-in`` — a ``closed`` action revokes
   unconditionally; otherwise the configured opt-in label's presence
   *now* decides. On an existing pending/active work row either is a
   revocation instead of a denial: the work parks (``work_parked``) and
   the delivery reconciles onto it.
7. ``opt-in-revoked`` / ``unauthorized-actor`` — deny-by-default via
   :func:`schema.is_execution_authorized`.
8. ``missing-acceptance-criteria`` / ``permission-prose`` — content
   gates: prose can never grant authority (A3).
9. Work exists for the logical key → ``reconciled`` (an ``edited`` action
   refreshes the pending fingerprint or flags active work for
   re-evaluation — never a second row, never parallel work).
10. Otherwise ``accepted``: work row + delivery + ``work_accepted``
    event + reserved Hermes task association, committed together.

Task association is a two-phase durable primitive (A5): ``reserve``
mints the deterministic ``task_id`` inside the accept transaction; the
``publish`` leg (the real ``kanban create`` adapter lands with the
execution lane) runs after commit, then ``bind_task`` marks it bound.
A crash between commit and publish leaves a ``pending`` binding that
:meth:`repair_task_associations` completes — the reserved ID is stable,
so repair can never double-dispatch.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json as _json

from ..config import schema as _schema
from ..durable.store import IntakeStoreError, work_key_for
from .envelope import EDIT_ACTIONS, REVOKE_ACTIONS, normalize

__all__ = [
    "IntakeError",
    "IntakeUnavailable",
    "LocalTaskAssociator",
    "IntakeService",
]


class IntakeError(Exception):
    """Intake could not evaluate the event (malformed input)."""


class IntakeUnavailable(IntakeError):
    """The durable write failed or was ambiguous — no acknowledgement
    is returned for it (A5)."""


def signature_payload(delivery_id, repo_id, issue) -> bytes:
    """The canonical signed payload — ``delivery|repo|issue`` — the same
    canonical form the spike probes signed, so webhook and reconciliation
    fixtures share one verification contract."""
    return f"{delivery_id}|{repo_id}|{issue}".encode()


def compute_signature(secret, delivery_id, repo_id, issue) -> str:
    return _hmac.new(secret.encode(), signature_payload(
        delivery_id, repo_id, issue), hashlib.sha256).hexdigest()


def signature_ok(secret, delivery_id, repo_id, issue, presented) -> bool:
    """Constant-time check of the presented HMAC-SHA256 (a ``sha256=``
    prefix is accepted, GitHub ``X-Hub-Signature-256`` style)."""
    if not secret or not isinstance(presented, str) or not presented:
        return False
    presented = presented.split("=", 1)[-1].strip()
    expected = compute_signature(secret, delivery_id, repo_id, issue)
    return _hmac.compare_digest(presented, expected)


class LocalTaskAssociator:
    """Deterministic local Hermes task association.

    ``reserve`` mints the task ID inside the accept transaction, so the
    association is durable with the work row; ``publish`` is the seam the
    execution lane's real ``hermes kanban create`` binding implements
    (Task 2.4). Publishing is keyed by the stable reserved ID — a retry
    after a crash re-publishes the *same* identity, never a second task.
    """

    def reserve(self, work_key, seq) -> str:
        return f"fk-task-{seq:06d}"

    def publish(self, task_id, work_key) -> None:
        """Confirm the reserved association. Local mode records nothing
        external — the kanban-side write is the execution lane's seam."""


class IntakeService:
    """Evaluate canonical envelopes into durable accept/reject decisions.

    - ``store`` — :class:`factory_kit.durable.store.IntakeStore`.
    - ``registrations`` — :class:`RegistrationStore` (setup's ready
      registered identity is the authority intake trusts).
    - ``configs`` — ``{repo_id: effective}`` mapping; the validated
      effective configuration whose authorization surface decides.
    - ``signing_secret`` — resolved ``secrets.webhook_signing_key``
      value (callers resolve the manifest's ``env:``-style reference;
      the secret never enters the store).
    - ``opt_in_label`` — the GitHub label that opts an issue in.
    - ``factory_actors`` — sender logins the kit's own automation uses;
      their events can never open work (intake-loop guard, A4).
    - ``associator`` — task-association port (default: deterministic
      local reservation).
    """

    def __init__(self, store, registrations, configs, *,
                 signing_secret=None, opt_in_label="factory-kit",
                 factory_actors=(), associator=None,
                 publish_inline=True):
        self.store = store
        self.registrations = registrations
        self.configs = dict(configs)
        self.signing_secret = signing_secret
        self.opt_in_label = opt_in_label
        self.factory_actors = set(factory_actors)
        self.associator = associator or LocalTaskAssociator()
        self.publish_inline = publish_inline

    # -- public entry point ---------------------------------------------------

    def deliver(self, fields) -> dict:
        """Normalize + evaluate one event; return the acknowledgement.

        Every outcome is decided inside one durable transaction. A denied
        event persists a redacted reason; an accepted event persists the
        full §6.4 record — logical identity, delivery reference, context
        fingerprint, authorization evidence, config digest and the
        reserved Hermes task association — *before* the safely-queued
        acknowledgement is returned. A store failure propagates as
        :class:`IntakeUnavailable` and produces no acknowledgement.
        """
        try:
            env = normalize(fields)
        except Exception as exc:
            # Malformed input still gets a persisted redacted rejection
            # when a delivery identity is recoverable — auditability of
            # denials does not depend on the payload being well formed.
            # The detail carries only the exception type name: exception
            # text could embed a payload fragment, and nothing derived
            # from untrusted content is ever persisted (A3).
            delivery = (fields or {}).get("delivery_id") \
                if isinstance(fields, dict) else None
            return self._deny(
                str(delivery or ""), str((fields or {}).get("channel", ""))
                if isinstance(fields, dict) else "",
                "malformed-event", detail=type(exc).__name__)

        try:
            ack = self._deliver_txn(env)
        except IntakeStoreError as exc:
            raise IntakeUnavailable(str(exc)) from exc
        if (ack.get("outcome") == "accepted"
                and ack.get("task_state") == "pending"
                and self.publish_inline):
            self.publish_task(ack["work_key"])
            ack["task_state"] = self.store.get_work(
                ack["work_key"])["task_state"]
        return ack

    # -- the transaction -------------------------------------------------------

    def _deliver_txn(self, env) -> dict:
        """The atomic decide-and-write. Runs the policy evaluation and the
        durable writes inside a single ``BEGIN IMMEDIATE`` boundary."""
        with self.store.transact() as tx:
            seq = tx.next_seq()
            delivery = env["delivery_id"]
            channel = env["channel"]

            prior = tx.find_delivery(delivery)
            if prior is not None:
                tx.record_event(
                    "delivery_deduplicated", delivery_id=delivery,
                    work_key=prior["work_key"],
                    detail=f"channel={channel}")
                return {"outcome": "deduplicated",
                        "work_key": prior["work_key"],
                        "original_outcome": prior["outcome"],
                        "seq": seq, "channel": channel}

            # -- pre-work denies: signature, factory loop, registration ----
            denied = self._evaluate_gate(env)
            if denied is not None:
                return self._deny_txn(tx, env, seq, denied)

            repo_id = env["repo_id"]
            reg = self.registrations.get(repo_id)
            generation = reg["active_generation"]
            authority = reg["authority_key"]
            work_key = work_key_for(authority, env["issue"], generation)
            existing = tx.get_work(work_key)

            # -- revocation: evaluated against the *current* state -------
            # A closed issue revokes unconditionally; otherwise the opt-in
            # label's presence *now* decides — a reordered unlabeled/
            # revoked delivery reconciles on what GitHub says now rather
            # than on arrival order (A4): existing pending/active work
            # parks; nothing re-arms it.
            closed = env["action"] in REVOKE_ACTIONS
            revoked = closed or self.opt_in_label not in env["labels"]
            if revoked:
                reason = "issue-closed" if closed else "opt-in-revoked"
                if existing is not None \
                        and existing["state"] in ("pending", "active"):
                    tx.park_work(work_key, reason)
                    tx.record_delivery(delivery, channel, work_key,
                                       "revoked", reason, seq)
                    tx.record_event(
                        "work_parked", delivery_id=delivery,
                        work_key=work_key, reason=reason,
                        config_digest=existing["config_digest"])
                    return {"outcome": "revoked", "work_key": work_key,
                            "reason": reason, "seq": seq,
                            "channel": channel}
                return self._deny_txn(
                    tx, env, seq,
                    (reason if closed else "not-opted-in", None))

            denied = self._evaluate_policy(env)
            if denied is not None:
                return self._deny_txn(tx, env, seq, denied)

            if existing is not None:
                return self._converge(tx, env, existing, seq)

            # -- durable accept -------------------------------------------------
            task_id = self.associator.reserve(work_key, seq)
            gen_row = self._active_gen_row(reg, generation)
            evidence = {
                "actor": env["sender"],
                "via": "github_actor",
                "opt_in_label": self.opt_in_label,
                "policy_digest": gen_row["policy_digest"],
            }
            tx.insert_work(
                work_key, authority, repo_id, env["issue"], generation,
                task_id, "pending", env["context_fingerprint"],
                env["issue_revision"], _json.dumps(evidence,
                                                   sort_keys=True),
                gen_row["config_digest"], gen_row["policy_digest"])
            tx.record_delivery(delivery, channel, work_key,
                               "accepted", None, seq)
            tx.record_event(
                "work_accepted", delivery_id=delivery, work_key=work_key,
                config_digest=gen_row["config_digest"],
                detail=f"channel={channel} task={task_id}")
            return {"outcome": "accepted", "work_key": work_key,
                    "task_id": task_id, "task_state": "pending",
                    "generation": generation, "seq": seq,
                    "channel": channel}

    # -- convergence on an existing work row -------------------------------------

    def _converge(self, tx, env, work, seq) -> dict:
        """A distinct delivery for a known logical identity converges on
        it — webhook/polling/restart all agree on the one row (A2)."""
        work_key = work["work_key"]
        channel = env["channel"]
        if env["action"] in EDIT_ACTIONS:
            if work["state"] == "pending":
                if env["context_fingerprint"] != \
                        work["context_fingerprint"]:
                    tx.update_work_context(
                        work_key, env["context_fingerprint"],
                        env["issue_revision"])
                    tx.record_event(
                        "work_updated", delivery_id=env["delivery_id"],
                        work_key=work_key,
                        detail="context-fingerprint")
            elif work["state"] == "active":
                if env["context_fingerprint"] != \
                        work["context_fingerprint"]:
                    tx.set_reevaluation(work_key)
                    tx.record_event(
                        "work_updated", delivery_id=env["delivery_id"],
                        work_key=work_key,
                        detail="reevaluation-required")
        tx.record_delivery(env["delivery_id"], channel, work_key,
                           "reconciled", None, seq)
        tx.record_event(
            "work_reconciled", delivery_id=env["delivery_id"],
            work_key=work_key, config_digest=work["config_digest"],
            detail=f"channel={channel}")
        return {"outcome": "reconciled", "work_key": work_key,
                "task_id": work["task_id"], "seq": seq,
                "channel": channel}

    # -- policy evaluation (inside the transaction) ------------------------------

    def _deny_txn(self, tx, env, seq, denied) -> dict:
        """Persist a redacted ``work_rejected`` event inside the open
        transaction and return the denial acknowledgement."""
        reason, detail = denied
        tx.record_event(
            "work_rejected", delivery_id=env["delivery_id"],
            reason=reason, config_digest=self._config_digest(env["repo_id"]),
            detail=detail or f"channel={env['channel']}")
        return {"outcome": "denied", "reason": reason, "seq": seq,
                "channel": env["channel"]}

    def _evaluate_gate(self, env):
        """Transport + registration denies (before any work lookup):
        signature, factory self-events, unregistered or unready
        registration, unavailable configuration."""
        if env["channel"] == "webhook" and not signature_ok(
                self.signing_secret, env["delivery_id"], env["repo_id"],
                env["issue"], env.get("signature")):
            return ("invalid-signature", None)
        if env["sender"] and env["sender"] in self.factory_actors:
            return ("factory-self-event", None)
        reg = self.registrations.get(env["repo_id"])
        if reg is None:
            return ("unregistered-repository", None)
        readiness = reg.get("readiness") or {}
        if readiness.get("verdict") != "ready" or \
                readiness.get("dispatch") != "allowed":
            return ("registration-not-ready",
                    f"verdict={readiness.get('verdict')}")
        if self.configs.get(env["repo_id"]) is None:
            return ("config-unavailable", None)
        return None

    def _evaluate_policy(self, env):
        """Authorization + content denies (after the opt-in label check):
        revoked opt-in, unauthorized actor, missing acceptance criteria,
        permission-granting prose."""
        effective = self.configs[env["repo_id"]]
        authz = effective.get("authorization") or {}
        if authz.get("execution_opt_in") is not True:
            return ("opt-in-revoked", None)
        if not _schema.is_execution_authorized(
                effective, github_actor=env["sender"]):
            return ("unauthorized-actor", None)
        if not env["has_acceptance_criteria"]:
            return ("missing-acceptance-criteria", None)
        if env["permission_prose"]:
            return ("permission-prose", env["permission_prose"])
        return None

    def _config_digest(self, repo_id):
        effective = self.configs.get(repo_id)
        if effective is None:
            return None
        try:
            return _schema.effective_digest(effective)
        except Exception:
            return None

    @staticmethod
    def _active_gen_row(reg, generation):
        for gen in reg.get("generations") or []:
            if gen["generation"] == generation:
                return gen
        return {"config_digest": None, "policy_digest": None}

    # -- task association repair (A5) --------------------------------------------

    def publish_task(self, work_key) -> dict:
        """Publish the reserved task association and mark it bound.

        Idempotent by construction: the ``task_id`` was reserved at
        accept time, so a retry after a crash re-confirms the same
        identity — never a second dispatch."""
        work = self.store.get_work(work_key)
        if work is None:
            raise IntakeError(f"no work row {work_key!r}")
        if work["task_state"] == "bound":
            return {"outcome": "already-bound", "task_id": work["task_id"]}
        try:
            self.associator.publish(work["task_id"], work_key)
        except Exception as exc:
            with self.store.transact() as tx:
                tx.record_event(
                    "task_bind_deferred", work_key=work_key,
                    reason="associator-unavailable",
                    detail=type(exc).__name__)
            return {"outcome": "pending", "task_id": work["task_id"]}
        with self.store.transact() as tx:
            result = tx.bind_task(work_key, work["task_id"])
            tx.record_event(
                "task_bound", work_key=work_key,
                detail=f"task={work['task_id']}")
        return result

    def repair_task_associations(self) -> dict:
        """Complete every pending binding — the durable crash-repair pass
        for "accepted but association publish was lost". Reserved IDs are
        deterministic, so this can never create a second task (A5)."""
        repaired, pending = [], []
        for row in self.store.pending_task_bindings():
            result = self.publish_task(row["work_key"])
            if result["outcome"] == "pending":
                pending.append(row["work_key"])
            else:
                repaired.append({"work_key": row["work_key"],
                                 "task_id": row["task_id"]})
        return {"repaired": repaired, "pending": pending}

    # -- a denial that survives malformed input ------------------------------------

    def _deny(self, delivery_id, channel, reason, detail=None) -> dict:
        """Persist a redacted ``work_rejected`` event outside the full
        pipeline (used when the envelope cannot even be normalized)."""
        try:
            with self.store.transact() as tx:
                seq = tx.next_seq()
                tx.record_event("work_rejected", delivery_id=delivery_id,
                                reason=reason, detail=detail)
        except IntakeStoreError as exc:
            raise IntakeUnavailable(str(exc)) from exc
        return {"outcome": "denied", "reason": reason, "seq": seq,
                "channel": channel}
