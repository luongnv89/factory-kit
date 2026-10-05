#!/usr/bin/env python3
"""Control service - typed, authorized, durable Telegram control
(issue #12 / Task 2.7, PRD §6.4 Control, §7.1, CFG02).

Every inbound message — slash command, structured field set or a
natural-language proposal — passes the same typed validator
(:mod:`factory_kit.control.commands`), is allowlist-checked against the
manifest's numeric ``telegram_users``/``telegram_chats``, resolves its
explicit repository/task/generation target, commits a durable
``control_records`` row (command ID, restricted actor/chat references,
action, target, receipt + commit times, accepted/rejected outcome)
*before* the acknowledgement text leaves, and only then produces the
response. A transport failure after the commit can lose the ack but
never the committed control state (A1/A6).

Actions:

- **status** — a read: explicit state words, stable identifiers,
  blocker, SHA/time, limits, committed-effect links, last
  heartbeat/reconciliation (A2, A6).
- **pause** — the durable pause boundary: ``requested`` while a stage
  runs (the next stage cannot start until resume), ``paused`` directly
  when no stage is in flight (A2).
- **resume** — clears the boundary: ``resumed`` is durable so a
  restart keeps the recorded stage (A2).
- **cancel** — commits the command record and the generation fence in
  ONE transaction, within the same handler receipt (A3); termination
  then runs against the lane's live handles - fence acknowledgement
  and confirmed/quarantined exit are separate persisted outcomes
  (A4). Cancel is terminal for the generation; effects committed
  before the fence stay linked, never silently reversed.
- **retry** — mints a freshly authorized, audited generation via
  ``authorize_generation`` with the control actor as
  ``authorized_by``; there is no silent retry path (A4).
- **approve**/**reject** — the Task-3.2 human decision on a durable
  one-use approval request. The decision row (restricted actor/chat,
  bound action/target, outcome) commits in the *same* transaction as
  the command record; pause and cancel invalidate standing
  approvals in theirs (F12 A5 — a held or canceled task can never
  leave live merge authority behind).
"""

from __future__ import annotations

import json
import time
import uuid

from factory_kit.config import schema
from factory_kit.durable.store import _utcnow, work_key_for
from factory_kit.control import commands
from factory_kit.diagnostics import views as _views
from factory_kit.execution.lane import _iso_to_epoch

__all__ = ["ControlService"]

#: The text-command footer every rendered response carries — each
#: interactive affordance documents its accessible text alternative,
#: and no result depends on colour or emoji (A6).
_COMMAND_FOOTER = (
    "Commands: 'status <repo> <issue>' | 'pause <repo> <issue> <gen>' | "
    "'resume <repo> <issue> <gen>' | 'cancel <repo> <issue> <gen>' | "
    "'retry <repo> <issue>' | 'approve <repo> <issue> <gen>' | "
    "'reject <repo> <issue> <gen>'")

_STATE_WORDS = {
    "pending": "QUEUED", "active": "ACTIVE", "completed": "COMPLETED",
    "parked": "PARKED", "quarantined": "QUARANTINED",
    "blocked": "BLOCKED", "canceled": "CANCELED",
}
_PAUSE_WORDS = {
    "none": "not paused", "requested": "PAUSE REQUESTED",
    "paused": "PAUSED", "resumed": "RESUMED",
}


class ControlService:
    """Authorized durable control over the execution lane."""

    def __init__(self, store, lane, registrations, configs, *,
                 preview=None, approval=None, alert_sink=None, now=None):
        self.store = store
        self.lane = lane
        self.registrations = registrations
        self.configs = dict(configs)
        self.preview = preview
        self.approval = approval
        self._alerts = alert_sink if alert_sink is not None else []
        self._now = now or time.time

    # ------------------------------------------------------------------ #
    # entry point
    # ------------------------------------------------------------------ #

    def handle(self, message):
        """Validate → authorize → commit → acknowledge one command.

        Returns the response dict (``text`` is the accessible
        acknowledgement). Every path — accepted or rejected — persists
        its control record before this returns, so the durable outcome
        never depends on the transport delivering the ack."""
        received_at = _utcnow()

        # Dedup first when a command ID is present: a repeated delivery
        # replays the recorded outcome — it never re-runs the action
        # and never produces a second effect (A5).
        if isinstance(message, dict) and message.get("command_id"):
            prior = self.store.find_control(str(message["command_id"]))
            if prior is not None:
                # A replay only replays for the *same* restricted actor
                # and chat — a forged actor presenting a known command
                # ID is rejected, never answered with the recorded
                # outcome (A5).
                actor = commands._ref(
                    message.get("actor_ref", message.get("actor")))
                chat = commands._ref(
                    message.get("chat_ref", message.get("chat")))
                if actor != prior["actor_ref"] or \
                        chat != prior["chat_ref"]:
                    cmd_id = str(message["command_id"])
                    fake = {"command_id":
                            f"{cmd_id}-replay-{uuid.uuid4().hex[:8]}",
                            "actor_ref": actor or "-",
                            "chat_ref": chat or "-",
                            "action": "-",
                            "repo_id": None, "issue": None,
                            "generation": None, "work_key": None}
                    with self.store.transact() as tx:
                        self._record(tx, fake, received_at, "rejected",
                                     reason="forged-replay")
                    return {"ok": False, "outcome": "rejected",
                            "reason": "forged-replay",
                            "command_id": cmd_id,
                            "text": self._reject_text(
                                "forged-actor", None, None)}
                return {
                    "ok": prior["outcome"] == "accepted"
                    or prior["outcome"] == "answered",
                    "outcome": "duplicate",
                    "recorded_outcome": prior["outcome"],
                    "reason": prior["reason"],
                    "command_id": str(message["command_id"]),
                    "work_key": prior["work_key"],
                    "text": self._reject_text(
                        "duplicate", prior["outcome"],
                        prior.get("work_key"))}

        check = commands.validate(message)
        if not check["ok"]:
            return self._reject(message, check["reason"], received_at,
                                missing=check.get("missing"))
        cmd = check["value"]

        # Allowlist + authority (CFG02): the *numeric* actor/chat
        # references must each be on their own manifest list under the
        # repo's effective config.
        effective = self.configs.get(cmd["repo_id"])
        if effective is None:
            return self._reject_cmd(cmd, "unregistered-authority",
                                    received_at)
        authz = effective.get("authorization", {})
        user = int(cmd["actor_ref"].split(":", 1)[1])
        chat = int(cmd["chat_ref"].split(":", 1)[1])
        if user not in authz.get("telegram_users", []):
            return self._reject_cmd(cmd, "forged-actor", received_at)
        if chat not in authz.get("telegram_chats", []):
            return self._reject_cmd(cmd, "wrong-chat", received_at)
        if not schema.is_execution_authorized(
                effective, telegram_user=user, telegram_chat=chat):
            return self._reject_cmd(cmd, "not-authorized", received_at)

        # Resolve the explicit durable target.
        authority = schema.authority_key({"repo_id": cmd["repo_id"]})
        work = self._resolve(authority, cmd)
        if work is None:
            return self._reject_cmd(cmd, "target-required", received_at)
        cmd["work_key"] = work["work_key"]
        if cmd["action"] in commands.MUTATING_ACTIONS:
            # A mutating command against anything but the registration's
            # live generation is a stale-generation rejection — effects
            # can only ever move the current authority (A5).
            if work["generation"] != cmd["generation"]:
                return self._reject_cmd(cmd, "stale-generation",
                                        received_at)
            reg = self.registrations.get(cmd["repo_id"])
            if reg is not None and \
                    cmd["generation"] != reg["active_generation"]:
                return self._reject_cmd(cmd, "stale-generation",
                                        received_at)

        handler = getattr(self, f"_do_{cmd['action']}")
        return handler(cmd, work, effective, received_at)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _resolve(self, authority, cmd):
        """Explicit (repo, issue, generation) → work row. Without a
        generation (status/retry), the *latest* generation row for the
        issue is the concrete target - never a guess across repos."""
        if cmd["generation"] is not None:
            return self.store.find_work(authority, cmd["issue"],
                                        cmd["generation"])
        rows = [w for w in self.store.work_rows()
                if w["authority_key"] == authority
                and w["issue"] == cmd["issue"]]
        if not rows:
            return None
        rows.sort(key=lambda w: w["generation"])
        return rows[-1]

    def _record(self, tx, cmd, received_at, outcome, *,
                reason=None, detail=None):
        work_row = self.store.get_work(cmd["work_key"]) \
            if cmd.get("work_key") else None
        tx.record_control(
            cmd["command_id"], received_at=received_at,
            committed_at=_utcnow(), actor_ref=cmd["actor_ref"],
            chat_ref=cmd["chat_ref"], action=cmd["action"],
            repo_id=cmd["repo_id"],
            authority_key=work_row["authority_key"] if work_row
            else None,
            issue=cmd["issue"], generation=cmd["generation"],
            work_key=cmd.get("work_key"), outcome=outcome,
            reason=reason, detail=detail, seq=tx.next_seq())

    def _reject(self, message, reason, received_at, missing=None):
        """Persist a rejected record for a message that never reached a
        typed command (best-effort fields; a missing command ID gets a
        generated one so the evidence still lands)."""
        msg = message if isinstance(message, dict) else {}
        command_id = str(msg.get("command_id") or "").strip() \
            or f"untrack-{_utcnow()}"
        action = str(msg.get("action") or "").strip() or "-"
        actor = msg.get("actor_ref") or msg.get("actor") or "-"
        chat = msg.get("chat_ref") or msg.get("chat") or "-"
        actor = actor if isinstance(actor, str) else \
            f"telegram:{actor.get('user_id', '?')}"
        chat = chat if isinstance(chat, str) else \
            f"telegram:{chat.get('chat_id', '?')}"
        with self.store.transact() as tx:
            try:
                tx.record_control(
                    command_id, received_at=received_at,
                    committed_at=_utcnow(),
                    actor_ref=actor, chat_ref=chat, action=action,
                    outcome="rejected", reason=reason,
                    detail=",".join(missing or []),
                    seq=tx.next_seq())
            except Exception:
                pass            # evidence best-effort; answer anyway
        return {"ok": False, "outcome": "rejected", "reason": reason,
                "command_id": command_id,
                "text": self._reject_text(reason, None, None,
                                          missing=missing)}

    def _reject_cmd(self, cmd, reason, received_at):
        """Persist + acknowledge a rejection of a *validly typed*
        command - the actor/chat/action/target columns are all real."""
        with self.store.transact() as tx:
            self._record(tx, cmd, received_at, "rejected",
                         reason=reason)
        return {"ok": False, "outcome": "rejected", "reason": reason,
                "command_id": cmd["command_id"],
                "work_key": cmd.get("work_key"),
                "text": self._reject_text(reason, None,
                                          cmd.get("work_key"))}

    # ------------------------------------------------------------------ #
    # actions — each commits its control record before acknowledging
    # ------------------------------------------------------------------ #

    def _do_status(self, cmd, work, effective, received_at):
        with self.store.transact() as tx:
            self._record(tx, cmd, received_at, "answered")
        return {"ok": True, "outcome": "answered",
                "command_id": cmd["command_id"],
                "work_key": work["work_key"],
                "text": self._status_text(work)}

    def _do_pause(self, cmd, work, effective, received_at):
        work_key = work["work_key"]
        pause = self.store.pause_info(work_key)
        if pause["pause_state"] in ("requested", "paused"):
            return self._reject_cmd(cmd, "already-paused", received_at)
        if work["state"] in ("completed", "parked", "quarantined",
                             "blocked", "canceled"):
            return self._reject_cmd(cmd, "work-terminal", received_at)
        active = [r for r in self.store.attempt_rows()
                  if r["work_key"] == work_key and r["state"] == "active"]
        with self.store.transact() as tx:
            if active:
                # A stage is in flight: the boundary is requested; the
                # lane commits ``paused`` when that stage completes and
                # the next cannot start until resume (A2).
                tx.set_pause(work_key, "requested",
                             detail=f"requested by {cmd['actor_ref']}")
                detail = "pause-requested"
            else:
                # No stage running — the boundary is already here.
                tx.set_pause(work_key, "paused",
                             stage=pause.get("paused_stage")
                             or "implementation",
                             detail=f"paused by {cmd['actor_ref']}")
                tx.enqueue_work(work_key, tx.next_seq(),
                                state="paused", reason="pause-boundary")
                detail = "paused"
            tx.record_event("work_paused", work_key=work_key,
                            reason=detail)
            # F12 A5 — a held task leaves no live merge authority: any
            # standing approval dies inside the same commit as the
            # pause boundary (resume never revives it — fresh
            # reverification + approval are required).
            tx.invalidate_approvals_tx(work_key, "task-paused")
            self._record(tx, cmd, received_at, "accepted",
                         detail=detail)
        word = "PAUSE REQUESTED" if active else "PAUSED"
        return {"ok": True, "outcome": "accepted",
                "command_id": cmd["command_id"], "work_key": work_key,
                "detail": detail,
                "text": self._ack_text(
                    cmd, work,
                    f"{word} - work {work_key}. "
                    + ("The running stage finishes first; the next stage"
                       " will not start until resume."
                       if active else
                       "Held at the stage boundary; no stage is "
                       "running.")
                    + f" Resume: 'resume {cmd['repo_id']} "
                      f"{cmd['issue']} {cmd['generation']}'.")}

    def _do_resume(self, cmd, work, effective, received_at):
        work_key = work["work_key"]
        pause = self.store.pause_info(work_key)
        if pause["pause_state"] not in ("requested", "paused"):
            return self._reject_cmd(cmd, "not-paused", received_at)
        if work["state"] in ("completed", "parked", "quarantined",
                             "blocked", "canceled"):
            return self._reject_cmd(cmd, "work-terminal", received_at)
        with self.store.transact() as tx:
            # ``resumed`` keeps ``paused_stage`` (the upsert preserves
            # it) — the lane restarts exactly at the held boundary.
            tx.set_pause(work_key, "resumed",
                         detail=f"resumed by {cmd['actor_ref']}")
            tx.enqueue_work(work_key, tx.next_seq(), state="queued",
                            reason="resumed")
            tx.record_event("work_resumed", work_key=work_key,
                            reason="operator-resume")
            self._record(tx, cmd, received_at, "accepted",
                         detail="resumed")
        return {"ok": True, "outcome": "accepted",
                "command_id": cmd["command_id"], "work_key": work_key,
                "text": self._ack_text(
                    cmd, work,
                    f"RESUMED - work {work_key} re-enters the lane at "
                    f"stage {pause['paused_stage'] or 'implementation'}."
                    )}

    def _do_cancel(self, cmd, work, effective, received_at):
        work_key = work["work_key"]
        if work["state"] in ("completed", "canceled", "parked",
                             "quarantined", "blocked"):
            return self._reject_cmd(cmd, "work-terminal", received_at)
        fence = self.store.fence_state(work_key)
        if fence is not None and fence["fenced"]:
            return self._reject_cmd(cmd, "already-canceled",
                                    received_at)
        # ONE durable commit: the command record AND the generation
        # fence — the fence is durable before termination starts and
        # inside the authenticated handler's receipt window (A3).
        with self.store.transact() as tx:
            tx.fence_work_tx(work_key, "cancel", work["generation"])
            # F12 A5 — cancellation voids standing approvals inside
            # the same commit as the fence: the generation can never
            # carry live merge authority past its own end.
            tx.invalidate_approvals_tx(work_key, "canceled")
            self._record(tx, cmd, received_at, "accepted",
                         detail="fenced")
        # Termination is a separate persisted outcome (A4): the fence
        # ack above vs resolve_fence's confirmed/quarantined below.
        term = self.lane.terminate_work(
            work_key, reason="cancel", deadline_s=30.0)
        # Confirmed task termination starts the preview cleanup window
        # (F11 A5): owned deployments are removed under the configured
        # deadline; a provider outage leaves a visible backlog entry —
        # never a removal claim.
        preview_cleanup = None
        if self.preview is not None:
            try:
                preview_cleanup = self.preview.terminate(
                    work_key, reason="cancel")
            except Exception as exc:
                preview_cleanup = {"outcome": "error",
                                   "reason": type(exc).__name__}
        state_word = ("CANCELED" if term["termination"] == "confirmed"
                      else "QUARANTINED")
        committed = self._committed_links(work_key)
        links = (f" Effects already committed: {committed}."
                 if committed else "")
        notify = (" Exit unconfirmed inside the deadline - work "
                  "quarantined and the operator was notified."
                  if term["termination"] != "confirmed" else
                  " Worker exit confirmed.")
        return {"ok": True, "outcome": "accepted",
                "command_id": cmd["command_id"], "work_key": work_key,
                "termination": term["termination"],
                "preview_cleanup": preview_cleanup,
                "text": self._ack_text(
                    cmd, work,
                    f"{state_word} - work {work_key}, generation "
                    f"{cmd['generation']} fenced and terminated."
                    f"{notify}{links} New effects and late results are"
                    " denied; cancel is final for this generation - "
                    f"retry needs fresh authorization: 'retry "
                    f"{cmd['repo_id']} {cmd['issue']}'.")}

    def _do_retry(self, cmd, work, effective, received_at):
        # Retry = fresh authorized generation (authorize_generation);
        # the control actor is the attributable principal (A4).
        result = self.lane.request_retry(
            cmd["repo_id"], cmd["issue"], authorized_by=cmd["actor_ref"])
        accepted = result["outcome"] == "retry-authorized"
        with self.store.transact() as tx:
            self._record(
                tx, cmd, received_at,
                "accepted" if accepted else "rejected",
                reason=result.get("reason"),
                detail=f"new work {result.get('work_key')}"
                       if accepted else None)
        text = self._ack_text(
            cmd, work,
            f"RETRY AUTHORIZED - new generation "
            f"{result['generation']}, work {result['work_key']} queued."
            if accepted else
            f"RETRY DENIED - {result.get('reason')}.")
        return {"ok": accepted,
                "outcome": "accepted" if accepted else "rejected",
                "reason": result.get("reason"),
                "command_id": cmd["command_id"],
                "work_key": result.get("work_key") or work["work_key"],
                "generation": result.get("generation"),
                "text": text}

    def _do_approve(self, cmd, work, effective, received_at):
        return self._do_decision(cmd, work, effective, received_at,
                                 "approve")

    def _do_reject(self, cmd, work, effective, received_at):
        return self._do_decision(cmd, work, effective, received_at,
                                 "reject")

    def _do_decision(self, cmd, work, effective, received_at, verdict):
        """The F12 decision path (Task 3.2): the typed verdict is
        resolved against the durable request — the explicit
        ``request_id`` when the button carried one, else the work's
        one live request — and the decision row + command record
        commit in ONE transaction. A transport that lost the ack can
        never lose the committed decision."""
        work_key = work["work_key"]
        if self.approval is None:
            return self._reject_cmd(cmd, "approval-unsupported",
                                    received_at)
        with self.store.transact() as tx:
            res = self.approval.decide_tx(
                tx, request_id=cmd.get("request_id"),
                work_key=work_key, command_id=cmd["command_id"],
                actor_ref=cmd["actor_ref"], chat_ref=cmd["chat_ref"],
                verdict=verdict, received_at=received_at)
            ok = res["outcome"] in ("approved", "rejected", "duplicate")
            self._record(
                tx, cmd, received_at,
                "accepted" if ok else "rejected",
                reason=res.get("reason") or res["outcome"],
                detail=f"decision {res.get('decision_id')} on "
                       f"{res.get('request_id')}")
        if res["outcome"] == "approved":
            headline = (
                f"APPROVED - request {res['request_id']} grants "
                f"{res['action']} on {res['target']} (head "
                f"{res.get('head_sha')}, method {res.get('merge_method')}"
                f") until {res.get('expires_iso')}. The grant "
                "is one-use: the merge owner spends it once.")
        elif res["outcome"] == "rejected":
            headline = (
                f"REJECTION RECORDED - request {res['request_id']} "
                f"declined; work {work_key} is blocked for a human "
                "decision. No further approval is requested "
                "automatically.")
        elif res["outcome"] == "duplicate":
            headline = (
                f"DUPLICATE - decision already recorded "
                f"({res.get('recorded_outcome')}).")
        else:
            headline = (f"APPROVAL DENIED - {res.get('reason')}. "
                        "No merge authority was granted.")
        return {"ok": ok,
                "outcome": "accepted" if ok else "rejected",
                "decision_outcome": res["outcome"],
                "reason": res.get("reason"),
                "request_id": res.get("request_id"),
                "decision_id": res.get("decision_id"),
                "command_id": cmd["command_id"],
                "work_key": work_key,
                "text": self._ack_text(cmd, work, headline)}

    # ------------------------------------------------------------------ #
    # accessible rendering (A2/A6) — explicit state words, stable ids,
    # authorized links, documented text alternatives
    # ------------------------------------------------------------------ #

    def _committed_links(self, work_key):
        refs = [i["remote_ref"] for i in
                self.store.intent_rows(work_key)
                if i["state"] == "linked" and i["remote_ref"]]
        work = self.store.get_work(work_key)
        if work and work.get("linked_pr") and \
                f"#{work['linked_pr']}" not in refs:
            refs.append(f"PR #{work['linked_pr']}")
        return ", ".join(refs) or None

    def _status_text(self, work):
        work_key = work["work_key"]
        pause = self.store.pause_info(work_key)
        fence = self.store.fence_state(work_key)
        state = _STATE_WORDS.get(work["state"],
                                 str(work["state"]).upper())
        pause_word = _PAUSE_WORDS.get(pause["pause_state"],
                                      pause["pause_state"])
        attempts = self.store.attempt_record_rows(work_key)
        last = attempts[-1] if attempts else None
        heartbeat = self.store.last_heartbeat(work_key)
        beat = (f"{max(0, int(self._now() - heartbeat))}s ago"
                if heartbeat else "none recorded")
        links = self._committed_links(work_key) or "none"
        blocker = work.get("parked_reason") or (
            pause["paused_stage"] and
            f"held at {pause['paused_stage']}") or "none"
        fence_txt = "none"
        if fence and fence["fenced"]:
            fence_txt = (f"fenced ({fence['fence_reason']}), termination"
                         f" {fence['termination']}")
        intents = self.store.intent_rows(work_key)
        revisions = [i["expected_revision"] for i in intents
                     if i.get("expected_revision")]
        revision = revisions[-1] if revisions else "none recorded"
        # Task-3.4 A2 additions — the same extras the local view layer
        # computes, so the Telegram surface and the local report can
        # never disagree (queue age, remote-observation freshness,
        # notification failures).
        extras = _views.status_extras(self.store, work_key,
                                      now=self._now())
        remote = extras.get("remote") or {}
        remote_txt = ("unknown - no reconciliation has completed"
                      if not remote else
                      (remote.get("remote_state") or "unknown")
                      + (" STALE" if remote.get("stale") else ""))
        notes = extras.get("notifications") or {}
        notes_txt = (f"{notes.get('pending', 0)} pending, "
                     f"{notes.get('failed', 0)} failed, "
                     f"{notes.get('delivered', 0)} delivered")
        if notes.get("last_failure_reason"):
            notes_txt += f"; last failure {notes['last_failure_reason']}"
        lines = [
            f"STATE {state} - {pause_word}.",
            f"Work {work_key}; task {work.get('task_id')}; "
            f"issue #{work['issue']}; generation {work['generation']}.",
            f"Attempts: {self.store.count_attempts(work_key)}"
            + (f"; last attempt {last['attempt_id']} "
               f"{last['verdict'] or last['outcome'] or 'in-flight'}"
               f" at {last['started']}." if last
               else "; none yet."),
            f"Revision: {revision}. Last update: "
            f"{work.get('updated_at')}.",
            self._limits_text(work, attempts),
            f"Blocker: {blocker}.",
            f"Committed effects: {links}.",
            f"Last heartbeat: {beat}. Fence: {fence_txt}.",
            (f"Queue: {extras['queue_state']} for "
             f"{_views._fmt_age(extras['queue_age_seconds'])}."
             if extras.get("queue_state") else None),
            f"Remote observation: {remote_txt}.",
            f"Notifications: {notes_txt}.",
            _COMMAND_FOOTER,
        ]
        return "\n".join(l for l in lines if l is not None)

    def _limits_text(self, work, attempts):
        """A2 — the limits element of the status surface: the bound
        execution budget and the work's consumption against it, from
        the latest attempt's durable limits snapshot (the dispatch-time
        bound budget) or the current effective config before any
        attempt ran. Unknown usage is named, never shown as zero."""
        work_key = work["work_key"]
        lim = None
        for rec in reversed(attempts):
            if rec.get("limits"):
                try:
                    lim = json.loads(rec["limits"])
                except ValueError:
                    lim = None
                if lim:
                    break
        if lim is None:
            raw = (self.configs.get(work["repo_id"]) or {}).get(
                "limits") or {}
            lim = {
                "implementation_attempts": int(
                    raw.get("implementation_attempts", 2)),
                "active_worker_seconds": float(
                    raw.get("active_worker_minutes", 60)) * 60.0,
                "wall_seconds": float(raw.get("wall_hours", 24)) * 3600.0,
            }
        measured, unknown = self.store.work_active_seconds(work_key)
        impl_used = self.store.count_attempts(
            work_key, role="implementation")
        starts = []
        for rec in attempts:
            epoch = _iso_to_epoch(rec.get("started"))
            if epoch:
                starts.append(epoch)
        wall_used = max(0.0, self._now() - min(starts)) \
            if starts else 0.0
        unknown_txt = (f", {unknown} unmeasured" if unknown else "")
        return (
            f"Limits: implementation attempts {impl_used}/"
            f"{int(lim.get('implementation_attempts', 0))}; "
            f"active worker {measured:.0f}s/"
            f"{float(lim.get('active_worker_seconds', 0)):.0f}s"
            f"{unknown_txt}; wall {wall_used:.0f}s/"
            f"{float(lim.get('wall_seconds', 0)):.0f}s.")

    def _ack_text(self, cmd, work, headline):
        return "\n".join([
            headline,
            f"Command {cmd['command_id']} by {cmd['actor_ref']} in "
            f"{cmd['chat_ref']} - committed.",
            _COMMAND_FOOTER])

    def _reject_text(self, reason, outcome, work_key, missing=None):
        ask = ""
        if reason == "target-required":
            ask = (" Name the concrete target: 'status <repo> <issue>',"
                   " 'pause <repo> <issue> <generation>'.")
        if reason == "duplicate":
            return (f"DUPLICATE - command already processed "
                    f"(recorded outcome: {outcome}).\n"
                    f"{_COMMAND_FOOTER}")
        return (f"REJECTED - {reason}.{ask}\n{_COMMAND_FOOTER}")
