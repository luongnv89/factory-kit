#!/usr/bin/env python3
"""Revision-bound preview lifecycle — deploy, smoke, invalidate, clean
up (issue #15 / Task 3.1, PRD §3.2 F11, §5.2, §6.4, §7.1).

:class:`PreviewService` is the coordinator-owned deployment boundary —
the one place a provider credential can produce an effect. It extends
the selected recipe (``docs/decisions/tested-recipe-selection.md`` —
Vercel preview deployments) into the full F11 lifecycle:

- **A1** — a preview is created only for an independently *verified*
  work row (the latest ``verification_evidence`` observation is
  ``verified`` on the exact head being deployed). The durable
  ``preview_records`` row commits *before* the provider call and
  carries the whole §6.4 evidence surface: selected provider,
  deployment id, artifact identity, exact head/base SHA, URL,
  visibility, the configured smoke command + expected result, the
  observed result + time, expiry and cleanup ownership.
- **A2** — smoke is run against the *recorded* deployment through the
  provider port (provider-correlated evidence, never worker
  self-report). The manifest's ``endpoint.preview.smoke`` contract —
  command + expected output + optional body marker — is required by
  schema validation: a task with no meaningful preview contract can
  never satisfy the endpoint.
- **A3** — every denial condition lands as ``preview_failed`` plus a
  visible blocker: failed smoke, provider outage, head/base mismatch,
  expired deployment, unknown artifact identity. ``status()``
  reports ``approval_ready`` — computed *now* from durable state, so
  old evidence can never substitute for the current revision, and
  nothing here ever grants a merge state.
- **A4** — every gate is re-evaluated under ``BEGIN IMMEDIATE`` at the
  boundary: fence, registration, active generation, configuration
  digests and the actor permit — the same recheck the publication
  broker runs. A fence landing mid-lifecycle invalidates the record;
  a late callback on a fenced or terminal record is denied and can
  never revive it.
- **A5** — one active preview per task (the partial unique index makes
  it un-raceable), a ≤24 h maximum lifetime enforced by the sweep, and
  removal confirmed within ``cleanup_minutes`` of confirmed task
  termination — a provider outage leaves a *visible*
  ``cleanup-pending`` backlog entry, never a false removal claim, and
  foreign deployments are never touched.
- **A6** — ``preview_verified`` / ``preview_failed`` typed events carry
  the deployment/artifact identity, head/base, the observed result,
  the outcome and the cleanup deadline.
"""

from __future__ import annotations

import json as _json
import re
import time
import uuid
from datetime import datetime, timezone

from factory_kit.config import schema
from factory_kit.durable.store import _iso_to_epoch, _utcnow
from factory_kit.execution.limits import TERMINAL_WORK_STATES
from factory_kit.preview.port import PreviewAmbiguity, PreviewError

__all__ = ["PreviewService"]

_REVISION_RE = re.compile(r"[0-9A-Fa-f]{4,64}")

#: Denial reasons that are *violations* — an unauthorized deployment
#: attempt rather than an ordinary fence/race. Violations quarantine
#: the affected work + alert high; ordinary denials just deny (the
#: publication broker's split, kept verbatim).
_VIOLATION_REASONS = {
    "unregistered-authority", "actor-revoked", "provider-mismatch",
}

#: Failure reasons that leave the work durably ``blocked`` — the
#: visible blocker A3 requires (a fence/cancel path does not re-block
#: a work the cancel already settled).
_BLOCKING_FAILURES = {
    "provider-outage", "deploy-outcome-lost", "deploy-response-lost",
    "revision-mismatch", "identity-mismatch", "unknown-deployment",
    "deployment-expired", "deployment-not-ready", "smoke-failed",
    "preview-contract-missing",
}


class PreviewService:
    """The scoped preview deployment boundary (§5.2, §6.4).

    - ``store`` — :class:`IntakeStore`; every verdict commits durable
      rows/events before it is reported.
    - ``preview_port`` — the provider seam
      (:class:`factory_kit.preview.port.PreviewPort`); the only code
      path that can reach deployment credentials.
    - ``registrations`` — :class:`RegistrationStore`; the active
      generation binding the preview must still hold.
    - ``configs`` — ``{repo_id: effective}`` validated manifests; the
      *current* config's digests are compared to the bound
      generation's so a rotation between dispatch and deploy denies.
    - ``now`` — injectable epoch clock shared with the store/fixtures
      so TTL and cleanup deadlines test deterministically.
    - ``alert_sink`` — optional operator-channel sink (list or
      callable); the durable alert row is the local visibility and is
      written first (§7.3).
    - ``approval`` — optional :class:`ApprovalService`; when wired,
      every record retirement voids approvals bound to the dead
      preview evidence (F12 A5 — an unhealthy, stale or superseded
      preview can never leave merge authority standing).
    """

    def __init__(self, store, preview_port, registrations, configs, *,
                 now=None, alert_sink=None, approval=None):
        self.store = store
        self.port = preview_port
        self.registrations = registrations
        self.configs = dict(configs)
        self._now = now or time.time
        self._alerts = alert_sink
        self._approval = approval

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _iso_now(self):
        """The service clock in the record's ISO shape. ``observed_at``
        feeds the merge guard's smoke-freshness math — which runs on
        ``self._now()`` — so the stamp must be written on the same
        clock, never wall time (A2)."""
        return datetime.fromtimestamp(
            self._now(), tz=timezone.utc).isoformat(
            timespec="milliseconds")

    def _alert(self, kind, identity, severity, detail=None):
        """Persist the alert row (deduplicated per
        (kind, identity, severity)) then best-effort emit on the
        operator channel — a lost push never loses the record."""
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

    def _effective(self, work):
        return self.configs.get(work["repo_id"])

    def _preview_contract(self, effective):
        """The endpoint's preview contract, or ``None`` when the
        effective config carries none — a missing contract can never
        satisfy the endpoint (A2's "not applicable" cannot pass)."""
        if effective is None:
            return None
        preview = (effective.get("endpoint") or {}).get("preview")
        if not preview:
            return None
        smoke = preview.get("smoke") or {}
        if not smoke.get("command") or not smoke.get("expect"):
            return None
        return preview

    def _cleanup_deadline(self, work, now):
        """Removal-confirmation deadline: ``cleanup_minutes`` after the
        termination being recorded — the A5 60-minute target, read from
        the bound contract at retire time."""
        effective = self._effective(work) if work else None
        preview = (effective or {}).get("endpoint", {}).get(
            "preview", {})
        minutes = preview.get(
            "cleanup_minutes", schema.DEFAULT_PREVIEW["cleanup_minutes"])
        return now + float(minutes) * 60.0

    def _event_props(self, rec, *, observed_at):
        """The §7.1 property set both preview events carry — deployment
        and artifact identity, revision binding, URL, expiry and the
        cleanup deadline (A6)."""
        return {
            "preview_id": rec.get("preview_id"),
            "deployment_id": rec.get("deployment_id"),
            "provider": rec.get("provider"),
            "artifact_identity": rec.get("artifact_identity"),
            "head_sha": rec.get("head_sha"),
            "base_sha": rec.get("base_sha"),
            "url": rec.get("url"),
            "observed_at": observed_at,
            "expires_epoch": rec.get("expires_epoch"),
            "cleanup_deadline_epoch": rec.get("cleanup_deadline_epoch"),
        }

    # ------------------------------------------------------------------ #
    # the authorization recheck (A4) — run under the write transaction
    # ------------------------------------------------------------------ #

    def _authorize(self, work, effective):
        """Re-evaluate every element of the permit at effect-commit
        time — fence, registration, active generation, bound config
        digests, work liveness and the generation's authorizing
        principal against the *current* manifest. Nothing is carried
        over from dispatch time."""
        fence = self.store.fence_state(work["work_key"])
        if fence is not None and fence["fenced"]:
            return {"ok": False, "reason": "fenced"}
        if effective is None:
            return {"ok": False, "reason": "unregistered-authority"}
        record = self.registrations.get(work["repo_id"])
        if record is None:
            return {"ok": False, "reason": "unregistered-authority"}
        if work.get("state") in TERMINAL_WORK_STATES:
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
            return {"ok": False, "reason": "stale-config"}
        reg_work = (record.get("work") or {}).get(work["work_key"])
        if reg_work is not None and reg_work.get("state") != "active":
            return {"ok": False, "reason": "revoked-work"}
        if schema.effective_digest(effective) != bound["config_digest"] \
                or schema.policy_digest(effective) != \
                bound["policy_digest"]:
            return {"ok": False, "reason": "stale-config"}
        principal = bound.get("authorized_by")
        if not schema.is_execution_authorized(
                effective, github_actor=principal):
            return {"ok": False, "reason": "actor-revoked"}
        return {"ok": True, "bound": bound, "record": record}

    def _deny_tx(self, tx, preview_id, *, work, contract, head_sha,
                 base_sha, base_name, reason, violation, actor_ref):
        """Persist one denied preview row as audit evidence (and, on a
        violation, quarantine the work) — inside the caller's
        ``transact``, mirroring the broker's denied intents."""
        tx.insert_preview(
            preview_id, work_key=work["work_key"], seq=tx.next_seq(),
            task_id=work.get("task_id"), generation=work.get("generation"),
            provider=(contract or {}).get("provider")
            or self.port.provider_name(),
            head_sha=head_sha, base_name=base_name, base_sha=base_sha,
            state="denied", reason=reason,
            cleanup_owner="factory", observed_at=self._iso_now(),
            detail=_json.dumps({"actor_ref": actor_ref},
                               sort_keys=True))
        if violation:
            tx.set_work_state(work["work_key"], "quarantined",
                              reason=reason)
            tx.record_event("work_quarantined",
                            work_key=work["work_key"], reason=reason,
                            detail="unauthorized preview attempt")
        return {"outcome": "denied", "reason": reason,
                "preview_id": preview_id, "violation": violation}

    # ------------------------------------------------------------------ #
    # retire — invalidate/fail one record and remove its owned resource
    # ------------------------------------------------------------------ #

    def _remove_owned(self, rec, work, now):
        """Attempt provider-confirmed removal of the record's owned
        deployment(s). On confirmation the row lands ``removed``; on a
        provider failure it lands ``cleanup-pending`` — the visible
        backlog, never a claimed removal (A5). A record with no
        recorded deployment id reconciles by identity first (a lost
        deploy response may still have created one); an outage during
        that rediscovery is *unconfirmed*, never "nothing to remove",
        so it parks in the same backlog rather than orphaning a
        deployment the provider may hold."""
        pending = False
        deployment_ids = [rec["deployment_id"]] \
            if rec.get("deployment_id") else []
        if not deployment_ids:
            try:
                found = self.port.find_deployments(
                    {"preview_id": rec["preview_id"],
                     "work_key": rec["work_key"]})
            except PreviewError:
                found = None
            if found is None:
                # The provider could not be asked — an outage is not a
                # negative answer, so the row parks in the visible
                # backlog like an unacknowledged removal (A5).
                pending = True
            else:
                deployment_ids = [d["deployment_id"] for d in found
                                  if d.get("deployment_id")]
        if not deployment_ids and not pending:
            if rec["state"] == "cleanup-pending":
                # Provider reachable and confirming *nothing* owned
                # remains — a confirmed absence drains the backlog
                # rather than leaving a row that can never settle.
                with self.store.transact() as tx:
                    tx.update_preview(
                        rec["preview_id"], state="removed",
                        removed_at=_utcnow())
                return "removed"
            # Nothing the provider confirms as ours — the record keeps
            # its terminal state; ``removed`` is only ever written on a
            # confirmed removal, never on absence (A5).
            return "no-resource"
        identity = {"preview_id": rec["preview_id"],
                    "work_key": rec["work_key"],
                    "generation": rec.get("generation")}
        for dep_id in deployment_ids:
            try:
                self.port.remove(dep_id, identity)
            except PreviewError:
                pending = True
        state = "cleanup-pending" if pending else "removed"
        with self.store.transact() as tx:
            tx.update_preview(
                rec["preview_id"], state=state,
                cleanup_deadline_epoch=self._cleanup_deadline(work, now),
                removed_at=None if pending else _utcnow())
        if pending:
            self._alert("preview-cleanup-backlog", rec["work_key"],
                        "medium",
                        f"preview {rec['preview_id']} deployment "
                        f"{','.join(deployment_ids) or 'unresolved'} "
                        "removal unconfirmed — provider unreachable; "
                        "backlog retained")
        return state

    def _retire(self, rec, state, reason, *, work=None,
                cleanup=True, smoke_observed=None, block=None):
        """One terminal transition: durable record + ``preview_failed``
        event in the same transaction, then (outside the write) the
        owned-resource removal. ``block`` blocks the work only for real
        denial conditions — never on the fence/cancel paths that already
        settled it."""
        work = work or self.store.get_work(rec["work_key"])
        now = self._now()
        observed = self._iso_now()
        if block is None:
            block = reason in _BLOCKING_FAILURES
        with self.store.transact() as tx:
            tx.update_preview(
                rec["preview_id"], state=state, reason=reason,
                smoke_observed=smoke_observed, observed_at=observed,
                cleanup_deadline_epoch=
                self._cleanup_deadline(work, now) if cleanup else
                rec.get("cleanup_deadline_epoch"))
            fresh = tx.get_preview(rec["preview_id"])
            tx.record_typed_event(
                "preview_failed", work_key=rec["work_key"],
                reason=reason,
                properties=self._event_props(fresh,
                                             observed_at=observed))
            if block and work is not None and \
                    work.get("state") not in TERMINAL_WORK_STATES:
                tx.set_work_state(work["work_key"], "blocked",
                                  reason=f"preview-failed:{reason}")
                tx.enqueue_work(work["work_key"], tx.next_seq(),
                                state="blocked",
                                reason=f"preview-failed:{reason}")
                tx.record_event("dispatch_blocked",
                                work_key=work["work_key"],
                                reason=f"preview-failed:{reason}")
        if self._approval is not None:
            # F12 A5 — the retired record's evidence is dead: approvals
            # bound to it invalidate immediately, not on a later sweep.
            self._approval.invalidate_for_preview(rec["work_key"])
        if block:
            self._alert("preview-unhealthy", rec["work_key"], "high",
                        f"preview {rec['preview_id']}: {reason}")
        if cleanup:
            return self._remove_owned(fresh, work, now)
        return state

    # ------------------------------------------------------------------ #
    # deploy + verify pipeline (A1–A4)
    # ------------------------------------------------------------------ #

    def deploy_preview(self, work_key, *, head_sha, base_sha=None,
                       base_name=None, actor_ref=None, evidence=None):
        """One revision-bound preview deployment for ``work_key``.

        Outcomes: ``verified`` (corroborated + smoke passed — the
        approval-request evidence), ``converged`` (the task's active
        preview already binds this head), ``denied`` (validation/
        authority/fence/verification gate — audit row only),
        ``failed`` (a denial condition — ``preview_failed`` + visible
        blocker), ``parked`` (deploy outcome uncertain — reconciliation
        by identity, never a blind retry).
        """
        work = self.store.get_work(work_key)
        if work is None:
            return {"outcome": "denied", "reason": "unknown-work"}
        work_key = work["work_key"]
        effective = self._effective(work)
        contract = self._preview_contract(effective)
        head_sha = str(head_sha or "").strip()
        preview_id = f"prev-{uuid.uuid4().hex[:12]}"
        now = self._now()

        def deny(reason, violation=False):
            with self.store.transact() as tx:
                return self._deny_tx(
                    tx, preview_id, work=work, contract=contract,
                    head_sha=head_sha, base_sha=base_sha,
                    base_name=base_name, reason=reason,
                    violation=violation, actor_ref=actor_ref)

        if not _REVISION_RE.fullmatch(head_sha):
            return deny("bad-revision")
        if contract is None:
            return deny("preview-contract-missing")
        if self.port.provider_name() != contract["provider"]:
            return deny("provider-mismatch", violation=True)

        # -- one commit: the whole permit recheck + the supersede + the
        #    durable record *before* any provider effect. The work row
        #    is re-read inside the transaction — the snapshot taken
        #    before BEGIN cannot see a concurrent state change.
        superseded = None
        denied = None
        with self.store.transact() as tx:
            work = tx.get_work(work_key)
            auth = self._authorize(work, effective)
            if not auth["ok"]:
                denied = self._deny_tx(
                    tx, preview_id, work=work, contract=contract,
                    head_sha=head_sha, base_sha=base_sha,
                    base_name=base_name, reason=auth["reason"],
                    violation=auth["reason"] in _VIOLATION_REASONS,
                    actor_ref=actor_ref)
            else:
                # A1 — preview evidence exists only *behind* a verified
                # observation of the exact head; a stale/denied/absent
                # observation can never be deployed over.
                ev = evidence or tx.latest_evidence(work_key)
                if ev is None or ev["status"] != "verified" or \
                        ev.get("head_sha") != head_sha:
                    denied = self._deny_tx(
                        tx, preview_id, work=work, contract=contract,
                        head_sha=head_sha, base_sha=base_sha,
                        base_name=base_name,
                        reason="verification-not-current",
                        violation=False, actor_ref=actor_ref)
                else:
                    if base_sha is None:
                        base_sha = ev.get("base_sha")
                    if base_name is None:
                        base_name = ev.get("base_name")
                    active = tx.active_preview(work_key)
                    if active is not None:
                        if active["head_sha"] == head_sha and (
                                active.get("base_sha") in
                                (None, base_sha)):
                            return {"outcome": "converged",
                                    "reason": "identity-owned",
                                    "preview_id": active["preview_id"],
                                    "deployment_id":
                                        active["deployment_id"],
                                    "url": active.get("url"),
                                    "state": active["state"],
                                    "evidence":
                                        self._evidence_payload(active)
                                        if active["state"] == "verified"
                                        else None}
                        # A4 — the bound revision moved: the old record
                        # is invalidated in the *same* commit that frees
                        # the slot, so the index never admits two.
                        superseded = active["preview_id"]
                        tx.update_preview(
                            active["preview_id"], state="invalidated",
                            reason="superseded", observed_at=self._iso_now(),
                            cleanup_deadline_epoch=
                            self._cleanup_deadline(work, now))
                        fresh = tx.get_preview(active["preview_id"])
                        tx.record_typed_event(
                            "preview_failed", work_key=work_key,
                            reason="superseded",
                            properties=self._event_props(
                                fresh, observed_at=self._iso_now()))
                        # F12 A5 — approvals bound to the superseded
                        # record's evidence die inside the same commit
                        # that retires it.
                        tx.invalidate_approvals_tx(
                            work_key, "preview-moved")
                    tx.insert_preview(
                        preview_id, work_key=work_key,
                        seq=tx.next_seq(), task_id=work.get("task_id"),
                        generation=work.get("generation"),
                        provider=contract["provider"],
                        head_sha=head_sha, base_name=base_name,
                        base_sha=base_sha,
                        visibility=contract.get("visibility"),
                        environment=contract.get("environment"),
                        smoke_command=contract["smoke"]["command"],
                        smoke_expected=contract["smoke"]["expect"],
                        state="deploying", cleanup_owner="factory",
                        expires_epoch=now + float(
                            contract["ttl_hours"]) * 3600.0,
                        detail=_json.dumps(
                            {"actor_ref": actor_ref,
                             "evidence_id": ev.get("evidence_id")},
                            sort_keys=True))
        if denied is not None:
            if denied.pop("violation"):
                self._alert("unauthorized-mutation", work_key, "high",
                            denied["reason"])
            return denied

        # Superseded deployment removal happens *after* the commit —
        # remote I/O never runs inside a write transaction.
        if superseded is not None:
            self._remove_owned(
                self.store.get_preview(superseded), work, now)

        # -- the provider effect (the only place one can happen) ------
        spec = {"head_sha": head_sha, "base_sha": base_sha,
                "base_name": base_name,
                "visibility": contract.get("visibility"),
                "environment": contract.get("environment"),
                "marker": contract["smoke"].get("marker"),
                "ttl_s": float(contract["ttl_hours"]) * 3600.0}
        identity = {"preview_id": preview_id, "work_key": work_key,
                    "generation": work.get("generation")}
        try:
            deployed = self.port.deploy(spec, identity)
        except PreviewAmbiguity as exc:
            # The deployment may exist; the response was lost. The row
            # stays ``deploying`` for identity-keyed reconciliation —
            # never a blind redeploy (A5).
            with self.store.transact() as tx:
                tx.update_preview(preview_id, reason="deploy-ambiguous",
                                  detail=str(exc)[:300])
                tx.set_work_state(work_key, "parked",
                                  reason="preview-ambiguous")
                tx.enqueue_work(work_key, tx.next_seq(),
                                state="parked",
                                reason="preview-ambiguous")
                tx.record_event("work_parked", work_key=work_key,
                                reason="preview-ambiguous",
                                detail=str(exc)[:300])
            self._alert("preview-unhealthy", work_key, "high",
                        "deploy outcome uncertain — reconciling by "
                        "identity")
            return {"outcome": "parked", "reason": "deploy-ambiguous",
                    "preview_id": preview_id}
        except PreviewError as exc:
            return self._fail(preview_id, "provider-outage",
                              detail=str(exc)[:300])

        # -- bind the provider's identity durably; a fence landing
        #    during the call invalidates the just-created deployment
        #    rather than certifying it (A4).
        with self.store.transact() as tx:
            fence = tx.fence_state(work_key)
            rec = tx.get_preview(preview_id)
            if fence is not None and fence["fenced"]:
                tx.update_preview(
                    preview_id, state="invalidated", reason="fenced",
                    deployment_id=deployed.get("deployment_id"),
                    url=deployed.get("url"),
                    artifact_identity=deployed.get("artifact_identity"),
                    observed_at=self._iso_now())
                fresh = tx.get_preview(preview_id)
                tx.record_typed_event(
                    "preview_failed", work_key=work_key,
                    reason="fenced",
                    properties=self._event_props(
                        fresh, observed_at=self._iso_now()))
                post = ("invalidate", fresh)
            else:
                tx.update_preview(
                    preview_id, state="deployed",
                    deployment_id=deployed.get("deployment_id"),
                    url=deployed.get("url"),
                    artifact_identity=deployed.get("artifact_identity"),
                    expires_epoch=deployed.get("expires_epoch")
                    or rec["expires_epoch"])
                post = ("observe", tx.get_preview(preview_id))
        if post[0] == "invalidate":
            self._remove_owned(post[1], work, now)
            return {"outcome": "denied", "reason": "fenced",
                    "preview_id": preview_id}
        return self._observe(post[1], work, contract)

    def _fail(self, preview_id, reason, *, detail=None, smoke=None,
              cleanup=True):
        """Terminal failure path — ``failed`` record, ``preview_failed``
        event, visible blocker, owned cleanup. Returns the outcome."""
        rec = self.store.get_preview(preview_id)
        observed = dict(smoke or {})
        if detail:
            observed["detail"] = detail
        state = self._retire(
            rec, "failed", reason,
            smoke_observed=_json.dumps(observed, sort_keys=True)
            if observed else None)
        return {"outcome": "failed", "reason": reason,
                "preview_id": preview_id,
                "cleanup": state}

    def _observe(self, rec, work, contract):
        """Authoritative read-back + configured smoke against the
        recorded deployment — provider-correlated evidence, never the
        deploy call's self-report (A1–A3)."""
        now = self._now()
        dep_id = rec["deployment_id"]
        try:
            observed = self.port.inspect(dep_id)
        except PreviewError as exc:
            return self._fail(rec["preview_id"], "provider-outage",
                              detail=str(exc)[:300])
        if observed is None or not observed.get("owned", True):
            return self._fail(rec["preview_id"], "unknown-deployment",
                              detail="provider cannot corroborate the "
                                     "recorded deployment identity")
        if str(observed.get("head_sha") or "") != str(rec["head_sha"]):
            return self._fail(
                rec["preview_id"], "revision-mismatch",
                detail=f"deployed head {observed.get('head_sha')} "
                       f"!= bound {rec['head_sha']}")
        if (observed.get("artifact_identity") and
                rec.get("artifact_identity") and
                observed["artifact_identity"] !=
                rec["artifact_identity"]):
            return self._fail(rec["preview_id"], "identity-mismatch",
                              detail="provider artifact digest differs "
                                     "from the recorded deployment")
        expires = observed.get("expires_epoch", rec["expires_epoch"])
        if expires is not None and float(expires) < now:
            return self._fail(rec["preview_id"], "deployment-expired")
        if str(observed.get("state") or "READY").upper() not in \
                ("READY", "BUILDING"):
            return self._fail(rec["preview_id"], "deployment-not-ready",
                              detail=f"provider state "
                                     f"{observed.get('state')}")

        # -- smoke: the configured contract, run by the provider port
        #    against the recorded URL.
        try:
            smoke = self.port.smoke(dep_id, contract["smoke"])
        except PreviewAmbiguity as exc:
            return self._fail(rec["preview_id"], "provider-outage",
                              detail=str(exc)[:300])
        except PreviewError as exc:
            return self._fail(rec["preview_id"], "provider-outage",
                              detail=str(exc)[:300])
        observed_at = self._iso_now()
        if not smoke.get("ok"):
            return self._fail(rec["preview_id"], "smoke-failed",
                              smoke=smoke)

        # -- the verified commit: the fence is rechecked in the same
        #    transaction that mints the evidence — a fence landing
        #    mid-lifecycle invalidates, never certifies (A4).
        work_key = rec["work_key"]
        with self.store.transact() as tx:
            fence = tx.fence_state(work_key)
            fresh = tx.get_preview(rec["preview_id"])
            if fence is not None and fence["fenced"]:
                tx.update_preview(
                    rec["preview_id"], state="invalidated",
                    reason="fenced", observed_at=observed_at)
                tx.record_typed_event(
                    "preview_failed", work_key=work_key,
                    reason="fenced",
                    properties=self._event_props(
                        tx.get_preview(rec["preview_id"]),
                        observed_at=observed_at))
                # F12 A5 — the fence voids standing approvals inside
                # the same commit.
                tx.invalidate_approvals_tx(work_key, "fenced")
                post = ("invalidate", tx.get_preview(rec["preview_id"]))
            else:
                tx.update_preview(
                    rec["preview_id"], state="verified",
                    observed_at=observed_at,
                    smoke_observed=_json.dumps(smoke, sort_keys=True),
                    cleanup_deadline_epoch=self._cleanup_deadline(
                        work, fresh["expires_epoch"] or now))
                verified = tx.get_preview(rec["preview_id"])
                tx.record_typed_event(
                    "preview_verified", work_key=work_key,
                    properties=self._event_props(
                        verified, observed_at=observed_at))
                post = ("verified", verified)
        if post[0] == "invalidate":
            self._remove_owned(post[1], work, now)
            return {"outcome": "denied", "reason": "fenced",
                    "preview_id": rec["preview_id"]}
        rec = post[1]
        return {"outcome": "verified", "preview_id": rec["preview_id"],
                "deployment_id": rec["deployment_id"],
                "url": rec["url"],
                "evidence": self._evidence_payload(rec)}

    def _evidence_payload(self, rec):
        """The approval-request attachment (A1/A2): provider-correlated
        deployment + artifact identity, exact head/base, URL,
        visibility, the configured smoke contract *and* its observed
        result/time, expiry and cleanup ownership."""
        return {
            "preview_id": rec["preview_id"],
            "provider": rec["provider"],
            "deployment_id": rec["deployment_id"],
            "artifact_identity": rec["artifact_identity"],
            "head_sha": rec["head_sha"],
            "base_name": rec["base_name"],
            "base_sha": rec["base_sha"],
            "url": rec["url"],
            "visibility": rec["visibility"],
            "environment": rec["environment"],
            "smoke": {
                "command": rec["smoke_command"],
                "expect": rec["smoke_expected"],
                "observed": (_json.loads(rec["smoke_observed"])
                             if rec.get("smoke_observed") else None),
            },
            "observed_at": rec["observed_at"],
            "expires_epoch": rec["expires_epoch"],
            "cleanup_owner": rec["cleanup_owner"],
            "cleanup_deadline_epoch": rec["cleanup_deadline_epoch"],
        }

    # ------------------------------------------------------------------ #
    # invalidation, termination, callbacks, sweep (A4/A5)
    # ------------------------------------------------------------------ #

    def invalidate(self, work_key, reason):
        """Invalidate the work's active preview because a later
        observation (reconciliation head/base drift, expiry, config
        change) voided its evidence — old evidence never substitutes
        for a new revision (A3/A4)."""
        rec = self.store.active_preview(work_key)
        if rec is None:
            return {"outcome": "no-preview", "work_key": work_key}
        state = self._retire(rec, "invalidated", reason, block=False)
        return {"outcome": "invalidated", "work_key": work_key,
                "preview_id": rec["preview_id"], "reason": reason,
                "cleanup": state}

    def terminate(self, work_key, *, reason="cancel"):
        """Committed-termination cleanup (A5): every live record for the
        work is invalidated and its owned deployment removed inside the
        configured cleanup window — provider outages leave the visible
        ``cleanup-pending`` backlog, never a removal claim."""
        outcomes = []
        for rec in self.store.preview_rows(work_key):
            if rec["state"] in self.store.PREVIEW_ACTIVE_STATES:
                state = self._retire(rec, "invalidated", reason,
                                     block=False)
                outcomes.append({"preview_id": rec["preview_id"],
                                 "state": state})
            elif rec["state"] in self.store.PREVIEW_CLEANUP_STATES:
                state = self._remove_owned(
                    rec, self.store.get_work(work_key), self._now())
                outcomes.append({"preview_id": rec["preview_id"],
                                 "state": state})
        return {"outcome": "terminated", "work_key": work_key,
                "previews": outcomes}

    def resmoke(self, work_key):
        """F12 staleness recovery: re-run the configured smoke against
        the work's *active verified* deployment and commit the fresh
        provider-side observation.

        A passing re-observation rewrites ``smoke_observed`` +
        ``observed_at`` — the preview digest rekeys, so every approval
        bound to the stale evidence invalidates through
        ``invalidate_for_preview`` and a fresh request must be minted.
        F12's rule is never "the old grant rides on new evidence":
        reverification produces a fresh evidence set and requires a
        fresh human approval."""
        rec = self.store.active_preview(work_key)
        if rec is None:
            return {"outcome": "denied", "reason": "no-active-preview",
                    "work_key": work_key}
        if rec["state"] != "verified":
            return {"outcome": "denied",
                    "reason": f"state-{rec['state']}",
                    "work_key": work_key}
        work = self.store.get_work(work_key)
        contract = self._preview_contract(self._effective(work))
        if contract is None:
            return {"outcome": "denied",
                    "reason": "preview-contract-missing",
                    "work_key": work_key}
        out = self._observe(rec, work, contract)
        if out.get("outcome") == "verified" and \
                self._approval is not None:
            # The fresh smoke rewrote the record — approvals bound to
            # the prior preview evidence die now (preview-moved), never
            # on a later sweep.
            self._approval.invalidate_for_preview(work_key)
        return out

    def observe_callback(self, deployment_id, *, payload=None):
        """A provider deployment callback (webhook) — binds by durable
        identity only. A callback for an unrecorded id, a fenced
        generation or a terminal record is denied: it can *narrow* an
        active record (invalidate on drift) but can never revive one
        (A4)."""
        rec = self.store.find_preview_by_deployment(deployment_id)
        if rec is None:
            return {"outcome": "denied", "reason": "unknown-deployment"}
        fence = self.store.fence_state(rec["work_key"])
        if fence is not None and fence["fenced"]:
            return {"outcome": "denied", "reason": "fenced",
                    "preview_id": rec["preview_id"]}
        if rec["state"] not in self.store.PREVIEW_ACTIVE_STATES:
            return {"outcome": "denied", "reason": "preview-terminal",
                    "preview_id": rec["preview_id"],
                    "state": rec["state"]}
        work = self.store.get_work(rec["work_key"])
        contract = self._preview_contract(self._effective(work))
        if contract is None:
            return self._fail(rec["preview_id"],
                              "preview-contract-missing")
        if rec["state"] == "deploying" and rec.get("deployment_id"):
            # The callback named the deployment the lost response
            # never returned — adopt it through the normal corroborated
            # observe path.
            with self.store.transact() as tx:
                tx.update_preview(rec["preview_id"], state="deployed")
            rec = self.store.get_preview(rec["preview_id"])
            return self._observe(rec, work, contract)
        # Deployed/verified: re-inspect — a drifted or expired
        # deployment narrows to failure; corroboration keeps the
        # record as-is (a callback alone never upgrades state).
        try:
            observed = self.port.inspect(deployment_id)
        except PreviewError as exc:
            return self._fail(rec["preview_id"], "provider-outage",
                              detail=str(exc)[:300])
        if observed is None or not observed.get("owned", True):
            return self._fail(rec["preview_id"], "unknown-deployment")
        if str(observed.get("head_sha") or "") != str(rec["head_sha"]):
            return self._fail(rec["preview_id"], "revision-mismatch",
                              detail="callback re-inspection saw a "
                                     "different head")
        expires = observed.get("expires_epoch", rec["expires_epoch"])
        if expires is not None and float(expires) < self._now():
            return self._fail(rec["preview_id"], "deployment-expired")
        return {"outcome": "observed", "preview_id": rec["preview_id"],
                "state": rec["state"]}

    def _reconcile_deploying(self, rec):
        """Crash-window reconciliation for a ``deploying`` record:
        rediscover the provider-side deployment by durable identity —
        adopt the one match and run the normal observe path; zero
        matches means the request truly never landed (failed); more
        than one is ambiguity — park, never guess (A5)."""
        work = self.store.get_work(rec["work_key"])
        identity = {"preview_id": rec["preview_id"],
                    "work_key": rec["work_key"],
                    "generation": rec.get("generation")}
        try:
            found = self.port.find_deployments(identity)
        except PreviewError:
            return "unresolved"      # outage — next pass retries
        fence = self.store.fence_state(rec["work_key"])
        if fence is not None and fence["fenced"]:
            if rec.get("deployment_id") is None and found:
                with self.store.transact() as tx:
                    tx.update_preview(
                        rec["preview_id"],
                        deployment_id=found[-1]["deployment_id"],
                        url=found[-1].get("url"))
            rec = self.store.get_preview(rec["preview_id"])
            self._retire(rec, "invalidated", "fenced", block=False)
            return "fenced"
        if not found:
            self._retire(rec, "failed", "deploy-outcome-lost")
            return "failed"
        if len(found) > 1:
            with self.store.transact() as tx:
                tx.update_preview(rec["preview_id"],
                                  reason="deploy-ambiguous")
                tx.set_work_state(rec["work_key"], "parked",
                                  reason="preview-ambiguous")
                tx.enqueue_work(rec["work_key"], tx.next_seq(),
                                state="parked",
                                reason="preview-ambiguous")
            self._alert("preview-unhealthy", rec["work_key"], "high",
                        f"{len(found)} deployments match preview "
                        f"{rec['preview_id']} — ambiguous")
            return "parked"
        with self.store.transact() as tx:
            tx.update_preview(
                rec["preview_id"], state="deployed",
                deployment_id=found[0]["deployment_id"],
                url=found[0].get("url"),
                artifact_identity=found[0].get("artifact_identity"))
        work = self.store.get_work(rec["work_key"])
        contract = self._preview_contract(self._effective(work))
        if contract is None:
            self._retire(rec, "failed", "preview-contract-missing")
            return "failed"
        out = self._observe(self.store.get_preview(rec["preview_id"]),
                            work, contract)
        return out["outcome"]

    def sweep(self):
        """The periodic lifecycle sweep (A5): interrupted deploys
        reconcile by identity, fenced generations lose their previews,
        TTL-expired records invalidate + clean up, and the
        ``cleanup-pending`` backlog retries provider removal — leaving
        a visible entry (never a false removal claim) when the provider
        stays unreachable."""
        report = {"expired": [], "deploying": [], "fenced": [],
                  "cleanup_removed": [], "cleanup_backlogged": [],
                  "errors": []}
        now = self._now()
        for rec in self.store.previews_deploying():
            try:
                outcome = self._reconcile_deploying(rec)
            except Exception as exc:      # surfaced, never dropped
                report["errors"].append(
                    {"preview_id": rec["preview_id"],
                     "error": type(exc).__name__})
                continue
            report["deploying"].append(
                {"preview_id": rec["preview_id"],
                 "outcome": outcome})
        # A fence that landed without terminate (heartbeat sweep,
        # restart quarantine) invalidates the active record — the
        # cancel path is not the only way to fence a generation.
        for rec in self.store.preview_rows():
            if rec["state"] in self.store.PREVIEW_ACTIVE_STATES:
                fence = self.store.fence_state(rec["work_key"])
                if fence is not None and fence["fenced"]:
                    self._retire(rec, "invalidated", "fenced",
                                 block=False)
                    report["fenced"].append(rec["preview_id"])
        for rec in self.store.previews_expired(now):
            self._retire(rec, "invalidated", "expired", block=False)
            report["expired"].append(rec["preview_id"])
        for rec in self.store.preview_cleanup_backlog():
            state = self._remove_owned(
                rec, self.store.get_work(rec["work_key"]), now)
            (report["cleanup_removed"] if state == "removed"
             else report["cleanup_backlogged"]).append(
                rec["preview_id"])
        return report

    # ------------------------------------------------------------------ #
    # status (A3/A6)
    # ------------------------------------------------------------------ #

    def status(self, work_key):
        """The work's current preview position — the latest durable
        record plus ``approval_ready``, computed *now*: a verified
        record grants approval evidence only while it is unexpired,
        unfenced and still backed by a ``verified`` observation of the
        same head. Anything else is a visible blocker, never a
        substitute (A3)."""
        rec = self.store.latest_preview(work_key)
        if rec is None:
            return {"status": "none", "reason": "no-preview",
                    "approval_ready": False, "merge_authorized": False}
        now = self._now()
        fence = self.store.fence_state(work_key)
        evidence = self.store.latest_evidence(work_key)
        expired = rec.get("expires_epoch") is not None and \
            float(rec["expires_epoch"]) < now
        approval_ready = (
            rec["state"] == "verified" and not expired
            and not (fence and fence["fenced"])
            and evidence is not None
            and evidence["status"] == "verified"
            and evidence.get("head_sha") == rec["head_sha"])
        smoke_age_s = (None if rec["state"] != "verified"
                       else now - _iso_to_epoch(rec.get("observed_at")))
        return {
            "status": rec["state"],
            "reason": rec.get("reason"),
            "preview_id": rec["preview_id"],
            "deployment_id": rec["deployment_id"],
            "url": rec.get("url"),
            "head_sha": rec.get("head_sha"),
            "base_sha": rec.get("base_sha"),
            "provider": rec.get("provider"),
            "visibility": rec.get("visibility"),
            "observed_at": rec.get("observed_at"),
            "smoke_age_s": smoke_age_s,
            "expires_epoch": rec.get("expires_epoch"),
            "cleanup_deadline_epoch": rec.get("cleanup_deadline_epoch"),
            "cleanup_owner": rec.get("cleanup_owner"),
            "removed_at": rec.get("removed_at"),
            "expired": bool(expired),
            "approval_ready": bool(approval_ready),
            "merge_authorized": False,
        }
