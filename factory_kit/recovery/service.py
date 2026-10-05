#!/usr/bin/env python3
"""Restart recovery + periodic GitHub reconciliation through Hermes
ownership — F06 (issue #13 / Task 2.8, PRD §3.2 F06, §5.1, §6.4, §7.1,
§7.3).

:class:`RecoveryService` is the coordinator-side owner of the two F06
surfaces:

- **Restart recovery** (:meth:`recover`) — after durable acceptance the
  controller may die at any point. On readiness this pass repairs
  interrupted task bindings, reconciles publication intents left
  between commit and response, sweeps orphaned attempts through the
  lane's existing crash fences, and settles every non-terminal work row
  into *resumed* or *explicitly parked* — never invented from chat, and
  never inside a second scheduler. The pass is bounded by
  ``deadline_s`` (§5.1's 60-second restart target) and reports against
  it.

- **Periodic reconciliation** (:meth:`tick` / :meth:`reconcile_once`) —
  an external cadence (``hermes cron`` per the boundary map) calls
  :meth:`tick`; the service decides when a pass is due from the durable
  watermark, so a restart cannot lose the schedule. Each pass polls the
  open-issue set for dropped-webhook work, re-observes every known
  non-terminal issue for revocations, reconciles pending publication
  intents by identity, and re-reads linked PRs for human movement or
  unexpected terminal state. Bounded backoff with retry guidance covers
  GitHub failures; five minutes without a successful observation marks
  remote state stale, alerts once per episode and stops
  evidence-dependent completion.

Ownership boundaries honoured here:

- **No second scheduler** — ``tick`` is called by the host cadence; the
  service only answers "due or not" from durable state.
- **No Telegram/chat replay** — recovery reads durable rows only;
  control records, pause boundaries and alert identities survive the
  restart by construction.
- **Ambiguity parks** — an unresolvable identity, unreachable remote or
  uncertain descendant termination lands in a visible parked /
  quarantined state with the durable reason; nothing is silently
  retried into a possibly-committed remote effect.
- **Remote read-only** — reconciliation *observes*; the only remote
  mutation path in the kit remains the publication broker's serialized
  intents, and recovery never sends one.
"""

from __future__ import annotations

import json
import time
import uuid

from factory_kit.execution.limits import HEARTBEAT_TIMEOUT_S, TERMINAL_WORK_STATES
from factory_kit.intake.reconcile import reconcile_observations
from factory_kit.intake.service import IntakeUnavailable
from factory_kit.publication.remote import RemoteError

from .backoff import (
    DISCOVERY_BOUND_S,
    NOMINAL_INTERVAL_S,
    RECOVERY_DEADLINE_S,
    STALE_REMOTE_S,
    BackoffPolicy,
)
from .poller import issue_observation

__all__ = ["RecoveryError", "RecoveryService"]


class RecoveryError(Exception):
    """A recovery/reconciliation pass could not commit its outcome."""


class RecoveryService:
    """Hermes-owned restart recovery + periodic GitHub reconciliation.

    - ``store`` — :class:`IntakeStore`; every outcome commits durable
      rows/events before it is reported.
    - ``registrations`` — :class:`RegistrationStore`; the active
      generation binding every recovered row must still hold.
    - ``configs`` — ``{repo_id: effective}`` validated manifests.
    - ``intake`` — :class:`IntakeService`; observed issues re-enter the
      *same* decision pipeline webhooks use.
    - ``lane`` — :class:`ExecutionLane`; owns the stale-attempt sweep
      and lane recovery (crash fences stay the lane's logic).
    - ``broker`` — :class:`PublicationBroker`; ``reconcile_pending``
      owns intent-identity rediscovery.
    - ``verification`` — :class:`VerificationService`; optional, used to
      stale verified evidence when the remote becomes unobservable or
      an observed head/base moves.
    - ``issue_source`` — the issue-observation port
      (:class:`GhIssuePoller` in production).
    - ``remote`` — the read-only remote port for linked-PR drift
      reads (defaults to the broker's).
    - ``alert_sink`` — optional operator-channel sink (list or
      callable); a failed delivery never loses the durable alert row —
      that row *is* the local visibility (§7.3).
    - ``now`` — injectable epoch clock shared with the lane/store so
      every boundary tests deterministically.
    """

    def __init__(self, store, registrations, configs, *, intake=None,
                 lane=None, broker=None, verification=None,
                 issue_source=None, remote=None,
                 opt_in_label="factory-kit",
                 interval_s=NOMINAL_INTERVAL_S,
                 deadline_s=RECOVERY_DEADLINE_S,
                 liveness_window_s=HEARTBEAT_TIMEOUT_S,
                 backoff=None, now=None, alert_sink=None):
        self.store = store
        self.registrations = registrations
        self.configs = dict(configs)
        self.intake = intake
        self.lane = lane
        self.broker = broker
        self.verification = verification
        self.issue_source = issue_source
        self.remote = remote if remote is not None else \
            getattr(broker, "remote", None)
        self.opt_in_label = opt_in_label
        # §5.1 nominal interval ≤60s — a configured larger value is
        # clamped to the bound, never honored silently.
        self.interval_s = min(float(interval_s), NOMINAL_INTERVAL_S)
        self.deadline_s = float(deadline_s)
        self.liveness_window_s = float(liveness_window_s)
        self.backoff = backoff or BackoffPolicy()
        self._now = now or time.time
        self._alert_sink = alert_sink
        self._process_started_epoch = self._now()
        self._notify_failures = 0

    # ------------------------------------------------------------------ #
    # durable alert + operator notification
    # ------------------------------------------------------------------ #

    def _notify(self, kind, identity, severity, detail=None,
                work_key=None):
        """Persist the alert row (deduplicated) then best-effort emit to
        the operator channel. The durable row is the *local* visibility —
        a Telegram outage can lose the push but never the record, and a
        repeated (kind, identity, severity) can never re-page (§7.3) —
        so only the freshly-emitted row reaches the channel."""
        result = self.store.emit_alert(kind, identity, severity, detail)
        if result["outcome"] != "emitted":
            return result
        sink = self._alert_sink
        try:
            if callable(sink):
                sink({"kind": kind, "identity": identity,
                      "severity": severity, "detail": detail,
                      "work_key": work_key,
                      "outcome": result["outcome"]})
            elif sink is not None:
                sink.append({"kind": kind, "identity": identity,
                             "severity": severity, "detail": detail,
                             "work_key": work_key,
                             "outcome": result["outcome"]})
        except Exception:
            # Notification delivery is best-effort: the durable row
            # already committed, and §7.3 keeps failures *locally*
            # visible rather than rolling back committed state.
            self._notify_failures += 1
        return result

    def _recovered_event(self, work_key, source, action, *, reason,
                         task_id=None, attempt_id=None,
                         reused_identity=None):
        """§7.1 ``recovery_completed`` — task/attempt identity, the
        recovery source, the reused remote identity and the
        parked/resumed reason, all durable before the pass reports."""
        self.store.record_event(
            "recovery_completed", work_key=work_key, reason=reason,
            detail=json.dumps({
                "task_id": task_id, "attempt_id": attempt_id,
                "source": source, "action": action,
                "reused_identity": reused_identity},
                sort_keys=True))

    def _park(self, work_key, reason, *, source, detail=None,
              alert_kind="recovery-parked", severity="medium"):
        """One durable park: state + queue visibility + event + alert,
        then the §7.1 recovery outcome row."""
        with self.store.transact() as tx:
            tx.set_work_state(work_key, "parked", reason=reason)
            tx.enqueue_work(work_key, tx.next_seq(), state="parked",
                            reason=reason)
            tx.record_event("work_parked", work_key=work_key,
                            reason=reason, detail=detail)
        self._notify(alert_kind, work_key, severity, detail or reason,
                     work_key=work_key)
        self._recovered_event(work_key, source, "parked",
                              reason=reason)
        return {"work_key": work_key, "outcome": "parked",
                "reason": reason}

    def _quarantine_orphaned(self, work, source, attempt_id):
        """Fence the orphaned generation and quarantine it in ONE
        durable commit — post-restart there is no live handle, so
        descendant termination is ``quarantined`` (uncertain), never
        claimed confirmed (A3)."""
        work_key = work["work_key"]
        with self.store.transact() as tx:
            tx.fence_work_tx(work_key, "restart-orphan",
                             work["generation"])
            tx.resolve_fence_tx(
                work_key, "quarantined",
                "termination unconfirmed — controller restarted with "
                "no live handles")
            tx.set_work_state(work_key, "quarantined",
                              reason="termination-uncertain")
            tx.enqueue_work(work_key, tx.next_seq(), state="parked",
                            reason="termination-uncertain")
        self._notify("termination-uncertain", work_key, "high",
                     "attempt(s) unconfirmed dead after restart",
                     work_key=work_key)
        self._recovered_event(work_key, source, "quarantined",
                              reason="termination-uncertain",
                              task_id=work.get("task_id"),
                              attempt_id=attempt_id)
        return {"outcome": "quarantined", "work_key": work_key,
                "attempt_id": attempt_id,
                "reason": "termination-uncertain"}

    # ------------------------------------------------------------------ #
    # restart recovery (A1–A3)
    # ------------------------------------------------------------------ #

    def recover(self, *, source="restart"):
        """Settle every known non-terminal work row after a restart.

        Order matters: task-binding repair and intent reconciliation
        restore identities *before* the attempt sweep decides what is
        still live — the sweep's fences are the last word on liveness.
        Returns the recovery report; ``within_deadline`` records whether
        the pass itself fit inside the §5.1 60-second bound.
        """
        started = self._now()
        report = {"source": source, "started_epoch": started,
                  "deadline_s": self.deadline_s,
                  "resumed": [], "parked": [], "quarantined": [],
                  "deferred": [], "bindings": None, "intents": [],
                  "sweep": None, "errors": []}
        remote_attempted = False
        remote_ok = True

        # 1 — task associations: a crash before/after the association
        # publish left ``task_state='pending'``; the reserved identity
        # is stable so repair binds exactly one task per work (A2).
        if self.intake is not None:
            bindings = self.intake.repair_task_associations()
            report["bindings"] = bindings
            for repaired in bindings["repaired"]:
                self._recovered_event(
                    repaired["work_key"], source, "repaired",
                    reason="task-binding",
                    task_id=repaired["task_id"])

        # 2 — publication intents committed before their remote effect
        # resolved: the broker discovers what the recorded identity
        # actually created — never a blind republish (A2). A remote
        # failure leaves the intents recorded for the next pass.
        if self.broker is not None:
            remote_attempted = any(
                i.get("work_key") for i in self.store.pending_intents())
            try:
                outcomes = self.broker.reconcile_pending()
                report["intents"] = outcomes
            except RemoteError as exc:
                remote_ok = False
                report["errors"].append(
                    {"stage": "intents",
                     "error": type(exc).__name__})
            else:
                for out in outcomes:
                    intent = self.store.get_intent(
                        out.get("intent_id")) or {}
                    work_key = intent.get("work_key")
                    if work_key is None:
                        continue
                    self._recovered_event(
                        work_key, source,
                        "resumed" if out.get("outcome") == "applied"
                        else "parked",
                        reason=out.get("reason") or "intent-reconciled",
                        task_id=intent.get("task_id"),
                        reused_identity=out.get("remote_ref")
                        or out.get("intent_id"))

        # 3 — orphaned attempts + lane occupancy: the lane's existing
        # crash fences — a stale active attempt is fenced and its
        # termination is *uncertain* post-restart (no live handles), so
        # it quarantines; a stale lane occupant is released honestly.
        if self.lane is not None:
            try:
                report["sweep"] = self.lane.sweep()
            except Exception as exc:
                # The sweep's own writes are durable; a mid-sweep error
                # is reported, never silently dropped — the next pass
                # re-derives the same fences from durable state.
                report["errors"].append(
                    {"stage": "sweep", "error": type(exc).__name__})

        # 4 — settle every non-terminal row: each becomes explicitly
        # resumed, parked or quarantined with the durable reason (A1).
        heartbeat_cutoff = self._now() - self.liveness_window_s
        for work in self.store.work_rows():
            if work["state"] in TERMINAL_WORK_STATES:
                continue
            outcome = self._settle_work(work, source, heartbeat_cutoff)
            bucket = {"resumed": "resumed", "parked": "parked",
                      "quarantined": "quarantined",
                      "deferred": "deferred"}.get(outcome["outcome"])
            if bucket is not None:
                report[bucket].append(
                    {k: v for k, v in outcome.items()
                     if k != "outcome"})

        # 5 — remote bookkeeping: a restart pass only moves the
        # watermark when it actually *attempted* a GitHub read; a
        # locally-settled pass is not an observation and must not reset
        # the staleness window (A7). The periodic next-due schedule is
        # preserved — restart recovery owns settling, not rescheduling.
        now = self._now()
        if remote_attempted:
            wm = self.store.reconcile_watermark() or {}
            next_due = wm.get("next_due_epoch")
            if remote_ok:
                self._mark_success(now, next_due=next_due)
            else:
                failures, stale_since = self._failure_counters(now)
                self._mark_failure(now, failures, stale_since,
                                   next_due)
        self._stale_check(now)
        report["finished_epoch"] = self._now()
        report["duration_s"] = report["finished_epoch"] - started
        report["within_deadline"] = \
            report["duration_s"] <= self.deadline_s
        self.store.record_event(
            "recovery_completed",
            detail=json.dumps({"source": source, "scope": "pass",
                               "duration_s": report["duration_s"],
                               "within_deadline":
                                   report["within_deadline"],
                               "resumed": len(report["resumed"]),
                               "parked": len(report["parked"]),
                               "quarantined":
                                   len(report["quarantined"])},
                              sort_keys=True))
        return report

    def _settle_work(self, work, source, heartbeat_cutoff):
        """One non-terminal row → resumed | parked | quarantined |
        deferred. The durable state — never chat history — decides."""
        work_key = work["work_key"]
        repo_id = work["repo_id"]
        reg = self.registrations.get(repo_id)

        # Superseded-generation work can never dispatch again — the
        # honest recovery outcome is an explicit park (replacement
        # takes a new authorized generation, A5/F03).
        if reg is None or work["generation"] != reg["active_generation"]:
            return self._park(work_key, "generation-superseded",
                              source=source)

        # A registration-side revocation (a policy change parked the
        # binding) is honored locally too — revoked permissions forbid
        # dispatch and newly authorized effects (A4).
        reg_work = (reg.get("work") or {}).get(work_key)
        if reg_work is not None and reg_work.get("state") != "active":
            return self._park(work_key, "revoked-registration",
                              source=source)

        if work["task_state"] != "bound":
            # Association repair could not publish (associator still
            # down): the row keeps its reserved identity and stays
            # eligible for repair next pass — deferred, never dropped.
            self._recovered_event(work_key, source, "deferred",
                                  reason="task-binding-unpublished",
                                  task_id=work.get("task_id"))
            return {"outcome": "deferred", "work_key": work_key,
                    "reason": "task-binding-unpublished"}

        if work["state"] == "pending":
            # Acknowledged work is retained — the durable row re-enters
            # the queue for the next dispatch pass.
            with self.store.transact() as tx:
                tx.enqueue_work(work_key, tx.next_seq(), state="queued",
                                reason="restart-recovered")
            self._recovered_event(work_key, source, "resumed",
                                  reason="accepted-pending",
                                  task_id=work.get("task_id"))
            return {"outcome": "resumed", "work_key": work_key,
                    "reason": "accepted-pending"}

        # state == "active" — three crash windows:
        ledger = [r for r in self.store.attempt_rows()
                  if r["work_key"] == work_key]
        active = [r for r in ledger if r["state"] == "active"]
        records = self.store.attempt_record_rows(work_key)
        if active:
            beat = self.store.last_heartbeat(work_key)
            if beat is not None and beat >= heartbeat_cutoff:
                # The Hermes-owned worker is still beating through the
                # restarted controller — the attempt genuinely resumes.
                self._recovered_event(
                    work_key, source, "resumed",
                    reason="heartbeat-live",
                    task_id=work.get("task_id"),
                    attempt_id=active[0]["attempt_id"])
                return {"outcome": "resumed", "work_key": work_key,
                        "attempt_id": active[0]["attempt_id"],
                        "reason": "heartbeat-live"}
            # No live handle exists post-restart, so descendant
            # termination is *uncertain*: fence + quarantine — the
            # lane sweep already fences attempts whose heartbeat
            # window lapsed; this covers a lane-less wiring and any
            # attempt the sweep left (A3).
            return self._quarantine_orphaned(work, source,
                                             active[0]["attempt_id"])
        if not records:
            # Active with zero attempts = the accepted→first-claim
            # crash window; the lane re-dispatches it honestly (it
            # provably never ran).
            with self.store.transact() as tx:
                tx.enqueue_work(work_key, tx.next_seq(), state="queued",
                                reason="restart-recovered")
            self._recovered_event(work_key, source, "resumed",
                                  reason="dispatch-not-started",
                                  task_id=work.get("task_id"))
            return {"outcome": "resumed", "work_key": work_key,
                    "reason": "dispatch-not-started"}
        # Attempts exist and all ended mid-pipeline — the interrupted
        # stage's commit boundary was never reached, so no durable
        # resume point exists: park visibly (§6.4 ambiguity parks; the
        # operator retry path owns any replacement generation).
        return self._park(work_key, "recovery-interrupted",
                          source=source,
                          detail="attempts ended without a committed "
                                 "stage boundary")

    # ------------------------------------------------------------------ #
    # periodic reconciliation (A4–A7)
    # ------------------------------------------------------------------ #

    def tick(self):
        """The external cadence's entry point — run a pass when due.

        Scheduling state is durable (the watermark row), so a restart
        cannot lose the schedule and a restarted process resumes the
        same cadence. The staleness check runs on *every* tick, due or
        not — a long server-directed backoff must not defer the
        5-minute stale alert past its threshold (A7).
        """
        now = self._now()
        self._stale_check(now)
        wm = self.store.reconcile_watermark() or {}
        next_due = wm.get("next_due_epoch")
        if next_due is not None and now < next_due:
            return {"outcome": "not-due",
                    "next_due_epoch": next_due,
                    "remote_state": wm.get("remote_state")}
        return self.reconcile_once(source="poll")

    def reconcile_once(self, *, source="poll"):
        """One reconciliation pass over GitHub state.

        A pass may partially observe — the poll can succeed while a
        targeted read fails. ``observed`` is true if *any* remote read
        succeeded (that is what §7.3's "successful GitHub observation"
        means); a wholly-unobserved pass backs off bounded, keeps every
        durable row untouched by guesswork, and never mutates the
        remote (A5).
        """
        now = self._now()
        report = {"source": source, "started_epoch": now,
                  "observed": False, "polled": 0, "accepted": [],
                  "reconciled": 0, "revoked": [], "denied": [],
                  "parked": [], "drifted": [], "intents": [],
                  "errors": [], "remote_state": None}
        observed = False
        retry_after = None            # upstream retry guidance (A5)

        def note_failure(stage, exc, issue=None):
            entry = {"stage": stage, "error": type(exc).__name__}
            if issue is not None:
                entry["issue"] = issue
            report["errors"].append(entry)
            nonlocal retry_after
            guidance = getattr(exc, "retry_after", None)
            if guidance is not None:
                # Honor the largest upstream hint the pass saw.
                retry_after = guidance if retry_after is None else \
                    max(retry_after, guidance)

        # a — the open-issue set: dropped-webhook discovery (A4).
        if self.issue_source is not None and self.intake is not None:
            try:
                issues = self.issue_source.poll_issues()
                observed = True
                report["polled"] = len(issues)
                for ack in self._deliver_observations(
                        issues, source, now):
                    self._classify_ack(ack, report)
            except RemoteError as exc:
                note_failure("poll", exc)
            except IntakeUnavailable as exc:
                # A durable-store failure mid-pass leaves the acked
                # prefix committed and reports honestly — the next pass
                # re-observes the rest.
                report["errors"].append(
                    {"stage": "deliver",
                     "error": type(exc).__name__})

        # b — targeted re-observation of known non-terminal work: a
        #     lost label or a closed issue routes through the same
        #     revocation path a webhook would take (A4).
        for work in self._observable_work():
            try:
                issue = self.issue_source.observe_issue(work["issue"])
                observed = True
            except RemoteError as exc:
                note_failure("observe", exc, issue=work["issue"])
                continue
            if issue is None:
                # The issue itself no longer resolves — the work's
                # authority source is irreconcilable; park visibly (A6).
                report["parked"].append(self._park(
                    work["work_key"], "issue-unobservable",
                    source=source, alert_kind="remote-irreconcilable",
                    severity="high"))
                continue
            try:
                ack = self._deliver_issue(work, issue, source, now)
            except IntakeUnavailable as exc:
                report["errors"].append(
                    {"stage": "deliver", "issue": work["issue"],
                     "error": type(exc).__name__})
                continue
            outcome = ack.get("outcome")
            if outcome == "revoked":
                report["revoked"].append(
                    {"work_key": ack["work_key"],
                     "reason": ack.get("reason")})
            elif outcome == "reconciled":
                report["reconciled"] += 1
            elif outcome in ("denied",):
                report["denied"].append(
                    {"issue": work["issue"],
                     "reason": ack.get("reason")})

        # c — pending publication intents: identity-keyed rediscovery
        #     of remote effects (A2). A remote error aborts the
        #     remaining intents — they stay ``recorded`` for the next
        #     pass; the remote never sees a blind repeat.
        if self.broker is not None:
            had_pending = any(i.get("work_key")
                              for i in self.store.pending_intents())
            try:
                report["intents"] = self.broker.reconcile_pending()
                observed = observed or had_pending
            except RemoteError as exc:
                note_failure("intents", exc)

        # d — linked-PR drift: unexpected terminal state, human
        #     head/base movement, irreconcilable identity (A6).
        if self.remote is not None:
            for work in self._non_terminal():
                drift = self._check_linked_pr(work, source, report)
                if drift is None:
                    continue
                report["drifted"].append(drift)
                if drift["outcome"] == "observed":
                    observed = True
                elif drift["outcome"] == "parked":
                    report["parked"].append(drift)

        # e — the watermark, the stale-remote rule and next-due
        #     scheduling commit durably (A5/A7).
        now = self._now()
        report["observed"] = observed
        if observed:
            next_due = now + self.interval_s
            self._mark_success(now, next_due=next_due)
        else:
            failures, stale_since = self._failure_counters(now)
            delay = self.backoff.delay(failures, retry_after)
            if retry_after is None:
                # Self-imposed backoff stays inside the §5.1 120-second
                # discovery bound; only upstream guidance may exceed it.
                delay = min(delay, DISCOVERY_BOUND_S)
            next_due = now + delay
            self._mark_failure(now, failures, stale_since, next_due)
            report["backoff_s"] = delay
        report["next_due_epoch"] = next_due
        self._stale_check(now)
        wm = self.store.reconcile_watermark() or {}
        report["remote_state"] = wm.get("remote_state")
        report["stale"] = wm.get("remote_state") == "stale"
        report["finished_epoch"] = self._now()
        return report

    # ------------------------------------------------------------------ #
    # pass internals
    # ------------------------------------------------------------------ #

    def _non_terminal(self):
        return [w for w in self.store.work_rows()
                if w["state"] not in TERMINAL_WORK_STATES]

    def _observable_work(self):
        """Non-terminal rows on this source's repository — a multi-repo
        future would key sources per repo; the MVP registration is
        single-repo so a mismatch simply has no observer (A6 honest
        skip, never a guess)."""
        if self.issue_source is None or self.intake is None:
            return []
        repo_id = self.issue_source.repo_id
        return [w for w in self._non_terminal() if w["repo_id"]
                == repo_id]

    def _deliver_observations(self, issues, source, now):
        """Poll rows → the shared intake pipeline. The delivery identity
        is per (pass, issue, revision): a re-observed unchanged issue
        dedups cheaply while a changed one reconciles through the normal
        edit/revoke paths (webhook and poll can never disagree)."""
        run_id = f"{source}-{int(now * 1000)}-{uuid.uuid4().hex[:6]}"
        observations = []
        for issue in issues:
            revision = str(issue.get("updated_at") or
                           issue.get("updatedAt") or "")
            delivery_id = (f"{run_id}-{issue.get('number')}-"
                           f"{revision[:24]}")
            observations.append(issue_observation(
                self.issue_source.repo_id,
                self.issue_source.full_name, issue,
                delivery_id=delivery_id))
        return reconcile_observations(self.intake, observations)

    def _deliver_issue(self, work, issue, source, now):
        """Targeted re-observation of one known issue through the same
        reconciliation channel — a lost label or a closed state lands
        as the durable revocation, identical to the webhook path."""
        revision = str(issue.get("updated_at") or
                       issue.get("updatedAt") or "")
        delivery_id = (f"{source}-{int(now * 1000)}-"
                       f"{uuid.uuid4().hex[:6]}-{issue.get('number')}-"
                       f"{revision[:24]}")
        fields = issue_observation(
            work["repo_id"], self.issue_source.full_name, issue,
            delivery_id=delivery_id)
        return self.intake.deliver(fields)

    @staticmethod
    def _classify_ack(ack, report):
        outcome = ack.get("outcome")
        if outcome == "accepted":
            report["accepted"].append(
                {"work_key": ack.get("work_key"),
                 "task_id": ack.get("task_id")})
        elif outcome == "reconciled":
            report["reconciled"] += 1
        elif outcome == "revoked":
            report["revoked"].append(
                {"work_key": ack.get("work_key"),
                 "reason": ack.get("reason")})
        else:
            report["denied"].append({"reason": ack.get("reason"),
                                     "outcome": outcome})

    def _check_linked_pr(self, work, source, report):
        """Re-read the work's linked remote identity — human movement
        and unexpected terminal states park visibly; nothing is
        re-published, closed, deleted or overwritten (A5/A6)."""
        work_key = work["work_key"]
        pr_number, expected = self._linked_remote(work)
        if pr_number is None:
            return None
        try:
            pr = self.remote.read_pr(pr_number)
        except RemoteError as exc:
            report["errors"].append(
                {"stage": "pr-read", "work_key": work_key,
                 "error": type(exc).__name__})
            return {"work_key": work_key, "outcome": "unknown",
                    "reason": f"remote-unavailable:"
                              f"{type(exc).__name__}"}
        if pr is None:
            # A recorded remote identity that no longer resolves is
            # irreconcilable — park visibly, alert high; nothing is
            # created to replace it (A6).
            self._stale_evidence(work_key, "linked-pr-vanished")
            out = self._park(work_key, "remote-irreconcilable",
                             source=source, severity="high",
                             alert_kind="remote-irreconcilable",
                             detail=f"linked PR {pr_number} no longer "
                                    "resolves")
            return {**out, "work_key": work_key}
        state = str(pr.get("state") or "").upper()
        if state in ("MERGED", "CLOSED"):
            reason = f"remote-terminal:{state.lower()}"
            self._stale_evidence(work_key, reason)
            return {**self._park(work_key, reason, source=source,
                                 severity="high",
                                 alert_kind="remote-terminal",
                                 detail=f"linked PR {pr_number} is "
                                        f"{state}"),
                    "work_key": work_key}
        if expected is not None and pr.get("head") is not None \
                and str(pr["head"]) != str(expected):
            # A human (or unrecorded) revision moved the published
            # head — verified evidence goes stale, the work parks, and
            # no force-push ever restores the recorded SHA (A6).
            self._stale_evidence(work_key, "remote-head-moved")
            return {**self._park(
                work_key,
                f"remote-head-moved:{expected}->{pr['head']}",
                source=source, severity="high",
                alert_kind="remote-drift",
                detail=f"linked PR {pr_number} head moved"),
                "work_key": work_key}
        return {"work_key": work_key, "outcome": "observed",
                "pr": pr_number, "state": state.lower()}

    def _linked_remote(self, work):
        """The work's linked PR number + the expected head revision the
        serialized intent recorded — the identity drift is measured
        against (never against a freshly invented value)."""
        work_key = work["work_key"]
        expected = None
        pr_number = work.get("linked_pr")
        linked = [i for i in self.store.intent_rows(work_key)
                  if i["operation"] == "pr-publish"
                  and i["state"] == "linked" and i["remote_ref"]]
        if pr_number is None and linked:
            pr_number = linked[-1]["remote_ref"]
        if linked:
            matched = [i for i in linked
                       if str(i["remote_ref"]) == str(pr_number)]
            expected = (matched or linked)[-1].get("expected_revision")
        return (str(pr_number) if pr_number is not None else None,
                expected)

    def _stale_evidence(self, work_key, reason):
        """Mark the latest ``verified`` observation stale when the
        verification service is wired — evidence-dependent completion
        stops on observed drift or an unobservable remote (A5/A7)."""
        if self.verification is None:
            return None
        return self.verification.mark_stale(work_key, reason)

    # ------------------------------------------------------------------ #
    # watermark + the 5-minute stale-remote rule (A7)
    # ------------------------------------------------------------------ #

    def _mark_success(self, now, *, next_due=None):
        """One pass that observed the remote: fresh state, cleared
        failure count and episode anchor, next pass due — one durable
        commit so a crash can't split the schedule from the outcome."""
        with self.store.transact() as tx:
            tx.update_reconcile_watermark(
                last_attempt_epoch=now, last_success_epoch=now,
                consecutive_failures=0, remote_state="fresh",
                stale_since_epoch=None, next_due_epoch=next_due)

    def _failure_counters(self, now):
        """Read-only: the consecutive-failure count this pass lands and
        the stale-episode anchor — the last success, or this first
        failure when the remote was never observed."""
        wm = self.store.reconcile_watermark() or {}
        failures = int(wm.get("consecutive_failures") or 0) + 1
        stale_since = wm.get("stale_since_epoch")
        if stale_since is None:
            stale_since = wm.get("last_success_epoch") or now
        return failures, stale_since

    def _mark_failure(self, now, failures, stale_since, next_due):
        """Commit one failed pass — count, (possibly stale) remote
        state, episode anchor and the backoff-scheduled next-due, all
        in one durable transaction so a crash can't split the schedule
        from the outcome."""
        remote_state = ("stale" if self._is_stale_at(stale_since, now)
                        else "unknown")
        with self.store.transact() as tx:
            tx.update_reconcile_watermark(
                last_attempt_epoch=now,
                consecutive_failures=failures,
                remote_state=remote_state,
                stale_since_epoch=stale_since,
                next_due_epoch=next_due)

    @staticmethod
    def _is_stale_at(stale_since, now):
        return stale_since is not None and \
            (now - float(stale_since)) > STALE_REMOTE_S

    def _stale_check(self, now):
        """§7.3 reconciliation-unavailable: >5 minutes with no
        successful GitHub observation ⇒ remote state marked stale, one
        deduplicated medium alert per episode, and every still-
        ``verified`` evidence row staled so evidence-dependent
        completion stops (A7)."""
        wm = self.store.reconcile_watermark() or {}
        stale_since = wm.get("stale_since_epoch")
        if not self._is_stale_at(stale_since, now):
            return
        if wm.get("remote_state") != "stale":
            with self.store.transact() as tx:
                tx.update_reconcile_watermark(remote_state="stale")
        # Per-episode identity: the anchor keeps a repeated signal
        # deduplicated inside the episode while a *new* outage alerts
        # again (severity-transition rule).
        self._notify("reconciliation",
                     f"remote-stale:{int(stale_since)}", "medium",
                     "no successful GitHub observation for >5 minutes "
                     "— remote state is stale; evidence-dependent "
                     "completion is stopped")
        for work in self.store.work_rows():
            latest = self.store.latest_evidence(work["work_key"])
            if latest is not None and latest["status"] == "verified":
                self._stale_evidence(work["work_key"],
                                     "remote-unobserved")

    # ------------------------------------------------------------------ #
    # observability (A6/A7)
    # ------------------------------------------------------------------ #

    def status(self):
        """Offline/readiness/process liveness + last successful
        reconciliation — durable, so a restarted process reports the
        same truth. No always-on SLA is claimed: the fields are honest
        timestamps and counters, not availability promises."""
        wm = self.store.reconcile_watermark() or {}
        work = self.store.work_rows()
        return {
            "process_started_epoch": self._process_started_epoch,
            "now": self._now(),
            "remote_state": wm.get("remote_state", "unknown"),
            "last_attempt_epoch": wm.get("last_attempt_epoch"),
            "last_success_epoch": wm.get("last_success_epoch"),
            "stale_since_epoch": wm.get("stale_since_epoch"),
            "consecutive_failures":
                wm.get("consecutive_failures", 0),
            "next_due_epoch": wm.get("next_due_epoch"),
            "interval_s": self.interval_s,
            "notify_failures": self._notify_failures,
            "pending_intents": len(self.store.pending_intents()),
            "non_terminal_work": [w["work_key"] for w in work
                                  if w["state"] not in
                                  TERMINAL_WORK_STATES],
            "quarantined": [w["work_key"] for w in work
                            if w["state"] == "quarantined"],
            "lane": self.store.lane(),
            "alerts": self.store.alert_rows(),
        }
