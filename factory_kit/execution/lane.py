#!/usr/bin/env python3
"""The one bounded sequential IDD execution lane — F03 (issue #9 /
Task 2.4).

:class:`ExecutionLane` is Hermes-owned work made real inside the kit:
it consumes the durable intake rows Task 2.3 wrote (``work`` in
``pending`` state with a bound task association), binds each to the
registration's active generation via ``record_work`` (left for this
lane by Task 2.2), and runs the *implementation → review* session pair
sequentially on a single bounded lane — with a bounded fix pass — under
the configured limits.

Boundaries (what this module deliberately never does):

- **No second scheduler / backlog loop.** ``tick()`` is one explicit
  pass; there is no thread, no poll loop, no IDD-side backlog scanner.
  Eligibility is read from durable rows; the kanban dispatcher remains
  the only scheduler (boundary map).
- **No implicit auto-merge.** The lane ends at implementation+review;
  publication intents (Task 2.5), verification (F04) and merge (F12)
  are later owners. The worker port exposes only supported
  claims/transitions — ``merge`` is not a verb it can name.
- **No provider/model fallback.** The dispatch context carries the
  configured model for the role; the pre-dispatch gate blocks an
  unavailable one rather than substituting another (A3).
- **Not a security boundary.** The workspace directory isolates files;
  the trust boundary is the credential/fence contract from the spike,
  not the directory (implementation note in the issue body).

All durable transitions go through :class:`IntakeStore` — one
``BEGIN IMMEDIATE`` commit per claim/fence/finish, so the
acknowledgement ordering the spike proved holds here too.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

from ..config import schema as _schema
from ..durable.store import work_key_for
from . import limits as _limits
from .preflight import dispatch_gate
from .worker import DispatchContext, WorkerError, new_session_id

__all__ = ["LaneError", "ExecutionLane"]


class LaneError(Exception):
    """The lane could not evaluate or commit a transition."""


def _iso_to_epoch(ts) -> float:
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _workspace_name(work_key) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", work_key)


class ExecutionLane:
    """One bounded sequential lane over the durable intake rows.

    - ``store`` — :class:`factory_kit.durable.store.IntakeStore`
      (work/attempt/fence/event/alert tables).
    - ``registrations`` — :class:`RegistrationStore`; dispatch binds to
      its ``active_generation`` and digests.
    - ``configs`` — ``{repo_id: effective}`` validated manifests.
    - ``worker`` — a :class:`WorkerPort` (the Hermes kanban adapter in
      production; ``ScriptedWorker`` in tests).
    - ``workspace_root`` — directory the isolated per-work workspaces
      are created under.
    - ``readings`` — host facts for the pre-dispatch gate
      (:func:`dispatch_gate`); absent ⇒ the gate fails closed.
    - ``now`` — epoch-seconds clock, injectable so heartbeat/wall
      boundaries test deterministically.
    """

    def __init__(self, store, registrations, configs, *, worker,
                 workspace_root, readings=None, lane_id="main",
                 now=None):
        self.store = store
        self.registrations = registrations
        self.configs = dict(configs)
        self.worker = worker
        self.workspace_root = str(workspace_root)
        self.readings = readings
        self.lane_id = lane_id
        self._now = now or time.time
        self._handles = {}            # attempt_id -> live SessionHandle
        self._first_dispatch = {}     # work_key -> epoch of first attempt

    # ------------------------------------------------------------------
    # Eligibility + queue visibility
    # ------------------------------------------------------------------

    def _reg(self, repo_id):
        return self.registrations.get(repo_id)

    def eligible_work(self):
        """Ready work in FIFO order: intake-accepted, task association
        bound, bound to the registration's *active* generation, not
        fenced, and not under a pause boundary (``requested`` or
        ``paused`` — Task 2.7). Parked/quarantined/canceled/completed
        rows are never eligible.

        A row is eligible while ``pending``, or while ``active`` with
        zero attempt records — that is exactly the crash window between
        activation and the first durable claim, so re-dispatching it
        can never resurrect a worker that ran."""
        out = []
        for work in self.store.work_rows():
            if work["task_state"] != "bound":
                continue
            if work["state"] == "active" and \
                    self.store.count_attempts(work["work_key"]) == 0:
                pass                    # crashed pre-claim — safe to retry
            elif work["state"] != "pending":
                continue
            reg = self._reg(work["repo_id"])
            if reg is None or \
                    work["generation"] != reg["active_generation"]:
                continue
            fence = self.store.fence_state(work["work_key"])
            if fence and fence["fenced"]:
                continue
            if self.store.pause_info(work["work_key"])["pause_state"] \
                    in ("requested", "paused"):
                continue                # held at a stage boundary (2.7)
            out.append(work)
        return out

    def _mark_queued(self, work_key, state, reason=None, detail=None):
        with self.store.transact() as tx:
            tx.enqueue_work(work_key, tx.next_seq(), state=state,
                            reason=reason, detail=detail)

    # ------------------------------------------------------------------
    # One scheduling pass
    # ------------------------------------------------------------------

    def tick(self):
        """One sequential pass: dispatch eligible work while the single
        lane is free; leave the rest durably queued with the visible
        ``lane-occupied`` reason (A1). Returns the pass report."""
        report = {"dispatched": [], "queued": [], "blocked": [],
                  "completed": [], "parked": [], "paused": []}
        while True:
            eligible = self.eligible_work()
            lane = self.store.lane(self.lane_id)
            occupied = lane["occupied_work"] if lane else None
            if occupied:
                for work in eligible:
                    reason = f"lane-occupied:{occupied}"
                    self._mark_queued(work["work_key"], "queued",
                                      reason=reason)
                    report["queued"].append(
                        {"work_key": work["work_key"],
                         "reason": reason})
                report["outcome"] = "occupied"
                return report
            if not eligible:
                report["outcome"] = "idle"
                return report
            work = eligible[0]
            result = self._dispatch(work)
            report["dispatched"].append(work["work_key"])
            report[result.get("final", "completed")].append(
                {"work_key": work["work_key"],
                 "outcome": result["outcome"],
                 "reason": result.get("reason")})
            # Sequential: the lane is free again (or the task
            # terminally blocked) → keep draining the queue.

    # ------------------------------------------------------------------
    # Dispatch one work row through the bounded session pair
    # ------------------------------------------------------------------

    def _dispatch(self, work):
        work_key = work["work_key"]
        repo_id = work["repo_id"]
        effective = self.configs.get(repo_id)
        reg = self._reg(repo_id)
        if effective is None or reg is None:
            return self._block(work, "config-unavailable",
                               "no effective config/registration")

        # Bind to the registration's *current* active generation; a
        # superseded or drifted row never dispatches (A5). Superseded
        # work parks — it is rebindable via an authorized retry, not
        # a broken row.
        active = None
        for gen in reg.get("generations") or []:
            if gen["generation"] == reg["active_generation"]:
                active = gen
        if active is None or work["generation"] != reg["active_generation"]:
            return self._block(work, "generation-superseded",
                               "work bound to a superseded generation",
                               state="parked")
        if work["config_digest"] != active["config_digest"]:
            return self._block(work, "config-drift",
                               "bound config digest != active "
                               "generation digest")

        # A3 — the pre-dispatch gate: missing tool, unavailable model,
        # unsupported capability or unsatisfied skill pin blocks
        # *before* any session exists.
        gate = dispatch_gate(effective, readings=self.readings)
        if not gate["dispatchable"]:
            codes = ",".join(b["code"] for b in gate["blockers"])
            self.store.emit_alert("dispatch-blocked", work_key, "high",
                                  detail=codes)
            return self._block(work, f"dispatch-blocked:{codes}",
                               "pre-dispatch gate",
                               blockers=gate["blockers"])

        # Registration binding (Task 2.2's record_work) + activation.
        self.registrations.record_work(repo_id, work_key)
        self.store.activate_work(work_key)
        acq = self.store.acquire_lane(work_key, work["task_id"],
                                      lane=self.lane_id)
        if acq["outcome"] != "acquired":
            # Lost the race — revert the activation so the row is
            # eligible (and visibly queued) rather than stranded
            # 'active' outside the lane.
            self.store.set_work_state(work_key, "pending")
            self._mark_queued(work_key, "queued",
                              reason=f"lane-occupied:{acq['occupied_by']}")
            return {"outcome": "queued",
                    "reason": f"lane-occupied:{acq['occupied_by']}",
                    "final": "queued"}
        self._mark_queued(work_key, "dispatched")

        limits = _limits.LaneLimits.from_effective(effective)
        workspace = os.path.join(self.workspace_root,
                                 _workspace_name(work_key))
        os.makedirs(workspace, exist_ok=True)
        acceptance_ref = (
            f"{work['authority_key']}#{work['issue']}"
            f"/criteria@{(work['context_fingerprint'] or '')[:12]}")
        dispatch_base = {
            "work_key": work_key,
            "task_id": work["task_id"],
            "issue": work["issue"],
            "generation": work["generation"],
            "runtime": gate["selected"]["runtime"],
            "skills": gate["selected"]["skills"],
            "config_digest": work["config_digest"],
            "policy_digest": work["policy_digest"],
            "workspace": workspace,
            "limits": limits.as_dict(),
            "acceptance_ref": acceptance_ref,
            "tools_allow": tuple(gate["selected"]["tools"]),
        }
        try:
            outcome = self._run_sessions(work, dispatch_base, limits)
        except Exception as exc:
            # A failed/ambiguous durable write must raise (store
            # contract) — but the work is parked and the failure is
            # visible first, so the exception never strands it inside
            # the lane.
            self.store.emit_alert("lane-error", work_key, "high",
                                  detail=type(exc).__name__)
            self.store.set_work_state(work_key, "parked",
                                      reason=f"lane-error:"
                                             f"{type(exc).__name__}")
            self._mark_queued(work_key, "parked",
                              reason="lane-error")
            raise
        finally:
            self.store.release_lane(self.lane_id,
                                    expected_work=work_key)
        return outcome

    # -- the sequential session pair --------------------------------------

    def _budget(self, work_key, limits):
        measured, unknown = self.store.work_active_seconds(work_key)
        return _limits.budget_status(
            measured_active_s=measured,
            unknown_usage=unknown,
            attempts_used=self.store.count_attempts(
                work_key, role="implementation"),
            wall_elapsed_s=self._now() - self._wall_start(work_key),
            limits=limits)

    def _wall_start(self, work_key):
        """The wall clock anchored to the *earliest durable attempt
        start* — a restart mid-run can never gift the task fresh wall
        hours (A4's 24h bound is per task, not per process)."""
        starts = [_iso_to_epoch(r["started"]) for r in
                  self.store.attempt_record_rows(work_key)]
        if starts:
            return min(starts)
        return self._first_dispatch.get(work_key, self._now())

    def _warn_budget(self, work_key, status):
        # Warn once per (work, budget) identity — dedup makes the
        # single-warning contract structural, and per-budget identity
        # keeps each crossing's detail intact.
        for name in status["warnings"]:
            self.store.emit_alert("budget", f"{work_key}:{name}",
                                  "medium", detail=f"{name}>=80%")
        if status["usage_unknown"]:
            self.store.emit_alert("budget", work_key, "low",
                                  detail="usage unknown — measured "
                                         "totals are a floor, not "
                                         "the whole spend")

    def _run_sessions(self, work, base, limits):
        """implementation → review, sequentially, with at most
        ``limits.implementation_attempts`` implementation attempts —
        the default 2 is *initial + one fix* (A2/A4). Every attempt is a
        fresh session_id on the shared runtime; the worker's verdict is
        a request the lane commits or denies."""
        work_key = work["work_key"]
        self._first_dispatch.setdefault(work_key, self._now())
        # Resume lands on the durably recorded boundary stage — a
        # restart can never resume *earlier* than the stage the pause
        # committed at (Task 2.7 A2).
        pause = self.store.pause_info(work_key)
        stage = (pause["paused_stage"] or "implementation") \
            if pause["pause_state"] == "resumed" else "implementation"
        final = None
        fix_permitted = True
        while stage is not None:
            # The pause gate: a requested pause commits ``paused`` at
            # this stage boundary — the *current* stage already
            # finished, the *next* stage does not start until an
            # authorized resume writes ``resumed`` (A2). The work
            # returns to ``pending`` so resume re-dispatches it; the
            # boundary stage is durable, so a crash mid-pause resumes
            # at the same stage.
            pause = self.store.pause_info(work_key)
            # ``paused`` is gated too, not only ``requested``: a pause
            # committed on the no-active-attempt path can land between
            # the eligibility read and this gate — the stage must not
            # start just because the boundary already says ``paused``.
            if pause["pause_state"] in ("requested", "paused"):
                with self.store.transact() as tx:
                    # A fence may have committed between the gate read
                    # and this write — recheck inside the transaction
                    # so a canceled/fenced work is never written back
                    # to ``pending``.
                    fence = tx.fence_state(work_key)
                    if fence is not None and fence["fenced"]:
                        return {"outcome": "fenced",
                                "reason": fence["fence_reason"],
                                "final": "parked"}
                    tx.set_pause(work_key, "paused", stage=stage,
                                 detail="pause took effect at stage "
                                        "boundary")
                    tx.set_work_state(work_key, "pending",
                                      reason="paused")
                    tx.enqueue_work(work_key, tx.next_seq(),
                                    state="paused",
                                    reason="pause-boundary")
                    tx.record_event("work_paused", work_key=work_key,
                                    reason="pause-boundary",
                                    detail=f"stage={stage}")
                return {"outcome": "paused", "reason": "pause-boundary",
                        "final": "paused", "stage": stage}
            status = self._budget(work_key, limits)
            # Emit every crossing warning — even when the same
            # checkpoint is already exhausted, the 80% crossing still
            # happened and must be recorded once (dedup keeps it a
            # single alert per budget identity).
            if status["warnings"] or status["usage_unknown"]:
                self._warn_budget(work_key, status)
            if stage == "implementation" and \
                    status["attempts_used"] >= \
                    limits.implementation_attempts:
                self.store.emit_alert("budget", work_key, "high",
                                      detail="attempts-exhausted")
                return self._finish(
                    work_key, "parked", "attempts-exhausted",
                    final="parked")
            if status["status"] == "exhausted":
                reason = "budget-exhausted:" + ",".join(
                    status["exceeded"])
                self.store.emit_alert("budget", work_key, "high",
                                      detail=reason)
                return self._finish(work_key, "parked", reason,
                                    final="parked")

            result = self._run_attempt(work_key, base, stage, limits)
            if result["outcome"] == "blocked":
                return self._finish(work_key, "blocked",
                                    result["reason"], final="blocked")
            if result["outcome"] == "fenced":
                # Heartbeat sweep or cancel fenced mid-attempt — the
                # fence resolver already owns the terminal state. A
                # cancel-fence with confirmed termination lands the
                # work on ``canceled`` (terminal for the generation,
                # Task 2.7 A4); uncertainty quarantines.
                fence = self.store.fence_state(work_key)
                term = (fence or {}).get("termination")
                reason = (fence or {}).get("fence_reason") or "fenced"
                if str(reason).startswith("cancel"):
                    state = ("canceled" if term == "confirmed"
                             else "quarantined")
                else:
                    state = "quarantined" if term in (None, "pending",
                                                      "quarantined") \
                        else "parked"
                return self._finish(work_key, state, reason,
                                    final="parked")
            verdict = result["verdict"]

            if stage == "implementation":
                if verdict in ("failed", "blocked"):
                    return self._finish(work_key, "parked",
                                        f"implementation-{verdict}",
                                        final="parked")
                stage = "review"
                continue
            # review stage
            if verdict == "changes-requested" and fix_permitted and \
                    self.store.count_attempts(
                        work_key, role="implementation") < \
                    limits.implementation_attempts:
                stage = "implementation"
                fix_permitted = False   # one fix attempt only (A4)
                continue
            if verdict in ("completed", "approved",
                           "changes-requested"):
                reason = ("reviewed" if verdict != "changes-requested"
                          else "attempts-exhausted")
                state = ("completed" if verdict != "changes-requested"
                         else "parked")
                return self._finish(work_key, state, reason,
                                    final=state)
            return self._finish(work_key, "parked",
                                f"review-{verdict}", final="parked")

    def _run_attempt(self, work_key, base, role, limits):
        """One role session: durable claim → worker start → collect →
        durable finish. The worker cannot commit — its verdict is a
        candidate recorded through ``accept_result`` before the attempt
        record closes."""
        n = self.store.count_attempts(work_key) + 1
        attempt_id = f"{base['task_id']}-a{n:02d}"
        model = self._role_model(base["work_key"], role)
        ctx = DispatchContext(
            attempt_id=attempt_id, role=role,
            session_id=new_session_id(), model=model, **base)
        started_epoch = self._now()
        begun = self.store.begin_execution_attempt(
            work_key, attempt_id=attempt_id, role=role,
            task_id=ctx.task_id, generation=ctx.generation,
            session_id=ctx.session_id, runtime=ctx.runtime,
            model=ctx.model,
            skills=json.dumps(ctx.skills, sort_keys=True),
            config_digest=ctx.config_digest,
            policy_digest=ctx.policy_digest, workspace=ctx.workspace,
            limits=json.dumps(ctx.limits, sort_keys=True),
            now_epoch=started_epoch)
        if begun["outcome"] != "active":
            return {"outcome": "blocked",
                    "reason": f"claim-{begun['reason']}"}
        try:
            handle = self.worker.start(ctx)
        except WorkerError as exc:
            self.store.finish_execution_attempt(
                attempt_id, verdict="failed", outcome="failed",
                active_seconds=None, usage=None,
                duration_s=self._now() - started_epoch)
            return {"outcome": "blocked",
                    "reason": f"worker-start:{exc}"}
        self._handles[attempt_id] = handle
        try:
            result = self.worker.collect(handle)
        except WorkerError as exc:
            result = None
            collect_error = str(exc)[:200]
        else:
            collect_error = None
        self._handles.pop(attempt_id, None)

        # The candidate result crosses the durable intake — fenced or
        # superseded attempts are rejected here even when collect
        # returned normally (A6 late output).
        if result is not None:
            accepted = self.store.accept_result(
                f"{attempt_id}-result", attempt_id,
                detail=result.verdict)
            if not accepted["accepted"]:
                self.store.emit_alert(
                    "fenced-result", work_key, "high",
                    detail=f"{attempt_id}: {accepted['reason']}")
                return {"outcome": "fenced",
                        "reason": accepted["reason"],
                        "verdict": result.verdict}
        duration = self._now() - started_epoch
        if result is None:
            self.store.finish_execution_attempt(
                attempt_id, verdict="failed", outcome="failed",
                active_seconds=None, usage=None, duration_s=duration)
            return {"outcome": "blocked",
                    "reason": f"worker-collect:{collect_error}"}
        self.store.finish_execution_attempt(
            attempt_id, verdict=result.verdict, outcome="completed",
            active_seconds=result.active_seconds, usage=result.usage,
            duration_s=duration)
        return {"outcome": "completed", "verdict": result.verdict,
                "detail": result.detail}

    def _role_model(self, work_key, role):
        work = self.store.get_work(work_key)
        effective = self.configs.get(work["repo_id"]) if work else None
        return ((effective or {}).get("runtime", {})
                .get("roles", {}).get(role, {}) or {}).get("model")

    # -- terminal bookkeeping -------------------------------------------------

    def _finish(self, work_key, state, reason, *, final):
        self.store.set_work_state(work_key, state, reason=reason)
        self._mark_queued(work_key,
                          "done" if state == "completed" else "parked",
                          reason=reason)
        self.store.record_event("lane_finished", work_key=work_key,
                                reason=reason,
                                detail=f"state={state}")
        return {"outcome": state, "reason": reason, "final": final}

    def _block(self, work, reason, detail, blockers=None,
               state="blocked"):
        work_key = work["work_key"]
        self.store.set_work_state(work_key, state, reason=reason)
        self._mark_queued(
            work_key, "blocked" if state == "blocked" else "parked",
            reason=reason)
        self.store.record_event("dispatch_blocked", work_key=work_key,
                                reason=reason,
                                detail=json.dumps(blockers or detail))
        return {"outcome": "blocked", "reason": reason,
                "final": "blocked", "blockers": blockers or []}

    # ------------------------------------------------------------------
    # Heartbeat + fencing (A6)
    # ------------------------------------------------------------------

    def record_heartbeat(self, attempt_id):
        """Record one worker heartbeat durably — the store is the
        liveness truth. A beat on a fenced/dead attempt is denied so a
        late worker cannot resurrect itself; the runtime forward is
        best-effort because a lost forward must never erase a real beat
        (that would fence a healthy worker)."""
        beat = self.store.heartbeat(attempt_id, self._now())
        if beat["outcome"] != "beat":
            return beat
        handle = self._handles.get(attempt_id)
        if handle is not None:
            try:
                self.worker.heartbeat(handle)
            except WorkerError:
                pass
        return beat

    def sweep(self, *, timeout_s=None):
        """Fence every active attempt that missed the heartbeat window
        (60s default — §7.3). The fence commits *before* termination is
        initiated; uncertain termination quarantines the work and keeps
        replacement ineligible (A6)."""
        timeout_s = (self._limits_now().heartbeat_timeout_s
                     if timeout_s is None else timeout_s)
        fenced = []
        for stale in self.store.stale_active_attempts(
                timeout_s, self._now()):
            work_key = stale["work_key"]
            work = self.store.get_work(work_key)
            if work is None:
                continue
            self.store.fence_work(work_key, "heartbeat-lost",
                                  work["generation"])
            handle = self._handles.pop(stale["attempt_id"], None)
            termination = "uncertain"
            if handle is not None:
                termination = self.worker.terminate(handle)
            if termination == "confirmed":
                self.store.resolve_fence(work_key, "confirmed",
                                         "termination confirmed")
                self.store.set_work_state(work_key, "parked",
                                          reason="heartbeat-lost")
                self._mark_queued(work_key, "parked",
                                  reason="heartbeat-lost")
            else:
                self.store.resolve_fence(
                    work_key, "quarantined",
                    "termination unconfirmed — process/descendant "
                    "state uncertain")
                self.store.set_work_state(
                    work_key, "quarantined",
                    reason="termination-uncertain")
                self._mark_queued(work_key, "parked",
                                  reason="termination-uncertain")
            self.store.release_lane(self.lane_id,
                                    expected_work=work_key)
            self.store.emit_alert("heartbeat", work_key, "high",
                                  detail=f"attempt "
                                         f"{stale['attempt_id']} missed "
                                         f">{timeout_s:.0f}s")
            fenced.append({"work_key": work_key,
                           "attempt_id": stale["attempt_id"],
                           "termination": termination})
        return {"fenced": fenced,
                "lane_recovered": self._recover_lane(timeout_s)}

    def terminate_work(self, work_key, *, reason="cancel",
                       deadline_s=30.0):
        """Terminate every live worker/descendant of ``work_key`` —
        called *after* the generation fence already committed (the
        control service writes its command record and the fence in one
        transaction, so the fence is durable before this runs: A3).

        The fence acknowledgement and the process-exit outcome are
        separate persisted rows: the fence row's ``fenced`` flag is the
        ack; ``termination`` becomes ``confirmed`` only when every live
        handle reports dead inside the deadline — anything else is
        ``quarantined`` with a high-severity local/operator alert
        inside the same window (A3/A4). Work lands ``canceled``
        (terminal for the generation) or ``quarantined``; replacement
        stays denied while termination is uncertain.
        """
        deadline = self._now() + float(deadline_s)
        work = self.store.get_work(work_key)
        if work is None:
            return {"outcome": "denied", "reason": "unknown-work"}
        fence = self.store.fence_state(work_key)
        if fence is None or not fence["fenced"]:
            self.store.fence_work(work_key, reason,
                                  work["generation"])
        # Terminate every live handle owned by this work's attempts —
        # the fence already ended their ledger rows ``fenced``, so
        # liveness truth comes from the handle map + the durable
        # ``ended`` marker, not attempt state. An attempt with no live
        # handle AND no durable end was live when fenced (its process
        # may still run on the runtime side) — that is uncertain, never
        # silently confirmed.
        records = {r["attempt_id"]: r for r in
                   self.store.attempt_record_rows(work_key)}
        outcomes = []
        for attempt_id in sorted(records):
            handle = self._handles.pop(attempt_id, None)
            if handle is None:
                ended = records[attempt_id].get("ended")
                outcomes.append({
                    "attempt_id": attempt_id,
                    "termination": "confirmed" if ended else
                    "uncertain"})
                continue
            if self._now() > deadline:
                outcomes.append({"attempt_id": attempt_id,
                                 "termination": "uncertain"})
                continue
            try:
                outcome = self.worker.terminate(handle)
            except WorkerError:
                outcome = "uncertain"
            outcomes.append({"attempt_id": attempt_id,
                             "termination": outcome})
        uncertain = [o for o in outcomes
                     if o["termination"] != "confirmed"]
        if uncertain:
            self.store.resolve_fence(
                work_key, "quarantined",
                "termination unconfirmed inside deadline — "
                "process/descendant state uncertain")
            self.store.set_work_state(work_key, "quarantined",
                                      reason="termination-uncertain")
            self._mark_queued(work_key, "parked",
                              reason="termination-uncertain")
            self.store.emit_alert(
                "termination-uncertain", work_key, "high",
                detail=f"{len(uncertain)} attempt(s) did not confirm "
                       f"exit inside {deadline_s:.0f}s")
        else:
            self.store.resolve_fence(work_key, "confirmed",
                                     "worker+descendants exited")
            self.store.set_work_state(work_key, "canceled",
                                      reason=reason)
            self._mark_queued(work_key, "done",
                              reason=reason)
        self.store.release_lane(self.lane_id, expected_work=work_key)
        self.store.record_event(
            "work_terminated", work_key=work_key, reason=reason,
            detail=json.dumps(outcomes, sort_keys=True))
        return {"outcome": "terminated",
                "termination": "quarantined" if uncertain
                else "confirmed",
                "attempts": outcomes}

    def _recover_lane(self, timeout_s):
        """Free an occupant that can never resume: terminal work state,
        or a crashed dispatch (occupied past the heartbeat window with
        no live attempt). A zero-attempt occupant reverts to ``pending``
        — it provably never ran, so requeueing is honest."""
        lane = self.store.lane(self.lane_id)
        if not lane or not lane["occupied_work"]:
            return None
        work_key = lane["occupied_work"]
        work = self.store.get_work(work_key)
        if work is None:
            self.store.release_lane(self.lane_id,
                                    expected_work=work_key)
            return work_key
        active = [r for r in self.store.attempt_rows()
                  if r["work_key"] == work_key and r["state"] == "active"]
        if active:
            return None                 # liveness sweep owns it
        occupied_since = _iso_to_epoch(lane["occupied_since"])
        if self._now() - occupied_since < timeout_s:
            return None                 # inside the grace window
        if work["state"] == "active":
            if self.store.count_attempts(work_key) == 0:
                self.store.set_work_state(work_key, "pending")
                self._mark_queued(work_key, "queued",
                                  reason="lane-recovered")
            else:
                self.store.set_work_state(
                    work_key, "parked",
                    reason="lane-recovered-incomplete")
                self._mark_queued(work_key, "parked",
                                  reason="lane-recovered-incomplete")
        self.store.release_lane(self.lane_id, expected_work=work_key)
        self.store.record_event("lane_recovered", work_key=work_key,
                                reason="occupant-stale")
        return work_key

    def _limits_now(self):
        """Limits for the heartbeat sweep — read from the first config
        (the bound lane contract); config-less sweep falls back to the
        60s constant."""
        for eff in self.configs.values():
            return _limits.LaneLimits.from_effective(eff)
        return _limits.LaneLimits()

    # ------------------------------------------------------------------
    # Late results (A6) — the worker request that can never commit
    # ------------------------------------------------------------------

    def accept_result(self, result_id, attempt_id, detail=None):
        """Durable candidate-result intake. Accepted only for a live
        (active, unfenced) attempt; every rejection is durable evidence
        and fires the deduplicated high-severity fenced-result alert."""
        out = self.store.accept_result(result_id, attempt_id,
                                       detail=detail)
        if not out["accepted"]:
            work_key = None
            for rec in self.store.attempt_record_rows():
                if rec["attempt_id"] == attempt_id:
                    work_key = rec["work_key"]
                    break
            self.store.emit_alert("fenced-result",
                                  work_key or attempt_id, "high",
                                  detail=out["reason"])
        return out

    # ------------------------------------------------------------------
    # Operator retry — fresh authorization → new generation (A5)
    # ------------------------------------------------------------------

    def request_retry(self, repo_id, issue, *, authorized_by,
                      new_effective=None):
        """An explicit, attributable operator retry.

        Requires the prior generation fenced with termination
        ``confirmed`` or quarantine ``resolved`` *before* new authority
        issues — otherwise another task could receive active authority
        while the old worker's state is uncertain. The new work row
        binds the fresh generation (new ``work_key``); every prior
        attempt, usage and event row under the old key stays visible.
        """
        reg = self._reg(repo_id)
        if reg is None:
            raise LaneError(f"no registration for {repo_id!r}")
        authority = reg["authority_key"]
        cur_gen = reg["active_generation"]
        # The retry attaches to the *latest* durable work row for this
        # issue — any generation. No prior row means no intake-authorized
        # work exists to retry: minting one would bypass the intake
        # gates entirely, so it is denied outright.
        priors = [w for w in self.store.work_rows()
                  if w["authority_key"] == authority and
                  w["issue"] == int(issue)]
        priors.sort(key=lambda w: w["generation"])
        if not priors:
            return {"outcome": "denied", "reason": "unknown-work"}
        old = priors[-1]
        old_key = old["work_key"]
        fence = self.store.fence_state(old_key)
        if old["state"] == "active" and \
                not (fence and fence["fenced"]):
            return {"outcome": "denied",
                    "reason": "prior-generation-active",
                    "work_key": old_key}
        if fence is not None and \
                not self.store.replacement_eligible(old_key):
            return {"outcome": "denied",
                    "reason": "termination-uncertain",
                    "work_key": old_key}
        if old["state"] == "pending" and \
                old["generation"] == cur_gen:
            return {"outcome": "already-queued", "work_key": old_key}
        effective = new_effective or self.configs.get(repo_id)
        if effective is None:
            raise LaneError(f"no effective config for {repo_id!r}")
        advanced = self.registrations.authorize_generation(
            repo_id, effective, authorized_by=authorized_by)
        new_gen = advanced["generation"]
        new_key = work_key_for(authority, issue, new_gen)
        with self.store.transact() as tx:
            if tx.get_work(new_key) is None:
                seq = tx.next_seq()
                task_id = f"fk-task-{seq:06d}"
                prior = self.store.get_work(old_key) or {}
                tx.insert_work(
                    new_key, authority, repo_id, issue, new_gen,
                    task_id, "pending",
                    prior.get("context_fingerprint"),
                    prior.get("issue_revision"),
                    json.dumps({"actor": authorized_by,
                                "via": "operator-retry",
                                "policy_digest":
                                    _schema.policy_digest(effective),
                                "supersedes": cur_gen},
                               sort_keys=True),
                    _schema.effective_digest(effective),
                    _schema.policy_digest(effective))
                tx.bind_task(new_key, task_id)
                tx.enqueue_work(new_key, seq, state="queued",
                                reason="retry-authorized")
                tx.record_event(
                    "generation_retried", work_key=new_key,
                    config_digest=_schema.effective_digest(effective),
                    detail=f"authorized_by={authorized_by} "
                           f"supersedes=g{cur_gen}")
        self.registrations.record_work(repo_id, new_key)
        return {"outcome": "retry-authorized",
                "work_key": new_key, "generation": new_gen,
                "authorized_by": authorized_by}

    # ------------------------------------------------------------------
    # Idle audit + status (A7)
    # ------------------------------------------------------------------

    def idle_audit(self, *, window_s=_limits.IDLE_AUDIT_S,
                   now_epoch=None):
        """The §5.1 idle proof: attempts started inside the window ==
        model calls issued inside the window (each session start is the
        model boundary). With no eligible work and no starts the audit
        records zero — never an estimated or assumed count."""
        now = self._now() if now_epoch is None else now_epoch
        starts = 0
        for ev in self.store.event_rows("attempt_started"):
            if now - _iso_to_epoch(ev["ts"]) <= window_s:
                starts += 1
        eligible = [w["work_key"] for w in self.eligible_work()]
        calls = self.worker.model_calls
        report = {
            "window_s": window_s,
            "idle": not eligible and starts == 0,
            "attempt_starts": starts,
            "model_calls": calls if calls is not None else starts,
            "eligible_work": eligible,
        }
        self.store.record_event(
            "idle_audit",
            detail=json.dumps(report, sort_keys=True))
        return report

    def status(self):
        """The locally-visible lane view: occupancy, the durable queue
        with reasons, per-work budget consumption, fence state and the
        deduplicated alert trail (A1/A7)."""
        work = {}
        for row in self.store.work_rows():
            measured, unknown = self.store.work_active_seconds(
                row["work_key"])
            work[row["work_key"]] = {
                "state": row["state"], "task_id": row["task_id"],
                "generation": row["generation"],
                "issue": row["issue"],
                "repo_id": row["repo_id"],
                "linked_pr": row["linked_pr"],
                "parked_reason": row["parked_reason"],
                "attempts": self.store.count_attempts(row["work_key"]),
                "active_seconds": measured,
                "unknown_usage": unknown,
                "fence": self.store.fence_state(row["work_key"]),
                "pause": self.store.pause_info(row["work_key"]),
                "last_heartbeat_epoch":
                    self.store.last_heartbeat(row["work_key"]),
                "queue": self.store.queue_entry(row["work_key"]),
            }
        return {"lane": self.store.lane(self.lane_id),
                "queue": self.store.queue_rows(),
                "work": work,
                "alerts": self.store.alert_rows()}
