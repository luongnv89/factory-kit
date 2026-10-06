#!/usr/bin/env python3
"""The live driver — one bounded pass over the whole factory loop.

The kit's services (intake → lane → publication broker → verification →
preview → approval → merge → recovery) were composed only inside tests;
this module wires them over the real ports and drives the post-lane
stages the lane deliberately does not own. The lane hands off at the
``reviewed`` queue boundary with the work still ``active``; from there
every stage is idempotent and driven by durable rows, so a crashed
driver resumes exactly where the durable state says it stopped:

  (a) read the bound workspace + head SHA,
  (b) record the independent review against the exact head,
  (c) publish branch + PR through the serialized broker,
  (d) verify the linked PR head + checks (pending checks wait,
      completed failures park),
  (e) deploy + smoke the immutable preview,
  (f) request the one-use human approval,
  (g) merge only through the guarded merge owner; a smoke-age denial
      re-smokes the deployment and requires a *fresh* approval (F12).

Ordering rule: post-lane stages run before any new dispatch —
``lane.tick(max_dispatch=1)`` runs only when nothing is in post-lane
flight, so a second opted-in issue can never interleave with an
in-flight one.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from factory_kit.approval import ApprovalService
from factory_kit.config import schema
from factory_kit.durable.store import _iso_to_epoch
from factory_kit.execution import ExecutionLane
from factory_kit.intake import service as intake_service
from factory_kit.merge import MergeService
from factory_kit.notifications.outbox import NotificationService
from factory_kit.preview import PreviewService
from factory_kit.publication import (
    OPERATION_BRANCH_PUBLISH, OPERATION_PR_PUBLISH, PublicationBroker)
from factory_kit.publication.remote import RemoteError
from factory_kit.recovery import RecoveryService
from factory_kit.verification import VerificationService
from factory_kit.verification.contract import contract_from_effective

#: Queue states the lane hands to the driver — ``reviewed`` (the
#: stage-pair boundary) and ``awaiting-approval`` (the human gate).
_POST_LANE_STATES = ("reviewed", "awaiting-approval")

#: Merge-gate denials whose cause is stale preview evidence — the
#: documented F12 recovery is re-observe the smoke, invalidate the
#: bound grant, and mint a fresh request.
_RESMOKE_REASONS = ("smoke-stale", "smoke-failed", "smoke-unobserved")

#: Verification statuses that mean "retry next tick" — a pending CI
#: run or an unreachable remote is never a park (the remote answered
#: fine, or could not answer at all: both retry).
_WAIT_STATUSES = ("blocked", "unknown")


def _null_transport(dest, text):
    """The no-op notification transport — the operator CLI is the
    channel here; durable outbox rows still commit (§7.1) but nothing
    is pushed anywhere."""
    return None


def _err_tail(exc):
    """A diagnosable one-line cause for operator logs — the message's
    first 200 characters with every secret canary from the manifest
    schema's table rewritten to ``[redacted]`` (logs never carry
    secrets; redact before truncating so a boundary can never split a
    canary and leak its prefix)."""
    text = str(exc)
    for pattern in schema.SECRET_CANARY_RES:
        text = pattern.sub("[redacted]", text)
    return text[:200]


def _watermark_since_iso(wm):
    """Best-effort ISO timestamp for ``remote <state> since <iso>`` —
    the stale-episode anchor when one exists, else the last attempt,
    else the row's own update time."""
    epoch = wm.get("stale_since_epoch") or \
        wm.get("last_attempt_epoch")
    if epoch is not None:
        return datetime.fromtimestamp(
            float(epoch), timezone.utc).isoformat(timespec="seconds")
    return str(wm.get("updated_at") or "unknown")


def compose(*, store, registrations, effective, worker, remote,
            preview_port, issue_source, workspace_root, readings=None,
            signing_secret=None, opt_in_label="factory-kit",
            transport=None, alert_sink=None, now=None):
    """The ``World._build_services`` composition over the supplied
    ports — the single place services wire so tests and the CLI build
    identical objects. ``factory_actors`` is empty: operator and kit
    share the one GitHub login in this deployment."""
    now = now or time.time
    cfgs = {effective["identity"]["repo_id"]: effective}
    intake = intake_service.IntakeService(
        store, registrations, cfgs, signing_secret=signing_secret,
        opt_in_label=opt_in_label, factory_actors=())
    approval = ApprovalService(store, registrations, cfgs, now=now,
                               alert_sink=alert_sink)
    verifier = VerificationService(store, now=now, approval=approval)
    preview = PreviewService(store, preview_port, registrations, cfgs,
                             now=now, approval=approval)
    lane = ExecutionLane(store, registrations, cfgs, worker=worker,
                         workspace_root=workspace_root,
                         readings=readings, now=now)
    merge = MergeService(store, remote, approval, registrations, cfgs,
                         now=now, alert_sink=alert_sink)
    notifier = NotificationService(
        store, transport or _null_transport, configs=cfgs, now=now)
    broker = PublicationBroker(store, remote, registrations, effective,
                               clock=now, alert_sink=alert_sink)
    recovery = RecoveryService(
        store, registrations, cfgs, intake=intake, lane=lane,
        broker=broker, verification=verifier, preview=preview,
        merge=merge, issue_source=issue_source, remote=remote, now=now,
        alert_sink=alert_sink, notifier=notifier)
    return SimpleNamespace(
        intake=intake, approval=approval, verifier=verifier,
        preview=preview, lane=lane, merge=merge, notifier=notifier,
        broker=broker, recovery=recovery)


class Driver:
    """The stage orchestrator over a composed service set.

    - ``store``/``registrations``/``configs`` — the same durable rows
      every service rechecks.
    - ``services`` — the :func:`compose` namespace.
    - ``worker`` — the worker port (``result_for`` re-reads finished
      attempts; ``cleanup_workspace`` runs only after a merged
      read-back).
    - ``remote`` — the remote port for verification reads and the
      publish actor identity.
    - ``git_bin``/``runner`` — the read-only worktree probes.
    """

    def __init__(self, repo, *, store, registrations, effective,
                 services, worker, remote, base_branch="main",
                 git_bin="git", check_grace_s=900.0, now=None,
                 log=None):
        self.repo = os.path.abspath(repo)
        self.store = store
        self.registrations = registrations
        self.effective = effective
        self.services = services
        self.worker = worker
        self.remote = remote
        self.base_branch = base_branch
        self.git_bin = git_bin
        self.check_grace_s = check_grace_s
        self._now = now or time.time
        self._log = log or (lambda msg: None)
        # Lane progress rides the driver's logger when the worker port
        # exposes the ``log`` seam (HermesKanbanWorker does; fixture
        # ports may not).
        if getattr(self.worker, "log", self._log) is None:
            try:
                self.worker.log = self._log
            except AttributeError:
                pass

    # ------------------------------------------------------------------ #
    # one pass
    # ------------------------------------------------------------------ #

    def tick(self):
        """One bounded pass: sweep durable fences/expiry, run the
        recovery cadence, drive every in-flight post-lane work, then —
        only when nothing is in flight — dispatch at most one lane
        item and immediately drive the work it reviewed."""
        report = {"recovery": None, "lane": None,
                  "stages": {}, "errors": []}
        self.services.lane.sweep()
        self.services.approval.sweep()
        self.services.preview.sweep()
        try:
            report["recovery"] = self.services.recovery.tick()
        except Exception as exc:
            report["errors"].append(
                {"stage": "recovery", "error": type(exc).__name__,
                 "detail": _err_tail(exc)})
            self._log(f"recovery tick failed: "
                      f"{type(exc).__name__}: {_err_tail(exc)}")

        # The reconciliation watermark gates every post-lane stage and
        # the lane dispatch: while the remote is not provably fresh a
        # stage can still succeed on a reachable API — but the next
        # reconcile marks the evidence it minted stale and invalidates
        # any request bound to it (the verify → request → invalidate
        # flap seen live). Pause until a pass observes the remote.
        watermark = self.store.reconcile_watermark() or {}
        remote_state = watermark.get("remote_state")
        if remote_state != "fresh":
            self._log(f"remote {remote_state or 'unknown'} since "
                      f"{_watermark_since_iso(watermark)} — evidence "
                      f"stages paused until reconciliation succeeds")
            return report

        in_flight = self._post_lane_rows()
        for row in in_flight:
            self._drive(row["work_key"], report)

        if not in_flight:
            try:
                report["lane"] = self.services.lane.tick(
                    max_dispatch=1)
            except Exception as exc:
                report["errors"].append(
                    {"stage": "lane", "error": type(exc).__name__,
                     "detail": _err_tail(exc)})
                report["lane"] = None
            for work_key in (report["lane"] or {}).get("dispatched",
                                                      []):
                queued = self.store.queue_entry(work_key)
                if queued is not None and \
                        queued["state"] in _POST_LANE_STATES:
                    self._drive(work_key, report)
        return report

    def _post_lane_rows(self):
        """Active work parked at a post-lane durable boundary."""
        out = []
        for row in self.store.queue_rows():
            if row["state"] not in _POST_LANE_STATES:
                continue
            work = self.store.get_work(row["work_key"])
            if work is not None and work["state"] == "active":
                out.append(row)
        return out

    # ------------------------------------------------------------------ #
    # the post-lane stages — idempotent, durable-row driven
    # ------------------------------------------------------------------ #

    def _drive(self, work_key, report):
        work = self.store.get_work(work_key)
        if work is None or work["state"] != "active":
            return
        stages = report["stages"].setdefault(work_key, [])
        try:
            self._stages(work, stages)
        except Exception as exc:
            # A stage that cannot commit/report surfaces in the tick
            # report — the durable rows decide the next pass.
            tail = _err_tail(exc)
            stages.append({"stage": "error",
                           "error": type(exc).__name__,
                           "detail": tail})
            report["errors"].append(
                {"work_key": work_key, "error": type(exc).__name__,
                 "detail": tail})
            self._log(f"{work_key}: stage error "
                      f"{type(exc).__name__}: {tail}")

    def _stages(self, work, stages):
        work_key = work["work_key"]
        issue = work.get("issue")

        # (a) — the bound workspace + its exact head.
        records = self.store.attempt_record_rows(work_key)
        workspace = records[-1].get("workspace") if records else None
        head = self._git(workspace, "rev-parse", "HEAD")
        branch = self._git(workspace, "rev-parse", "--abbrev-ref",
                         "HEAD")
        if not head:
            self._park(work_key, "head-unreadable",
                       "no readable head in the bound workspace")
            stages.append({"stage": "head", "outcome": "parked",
                           "reason": "head-unreadable"})
            return
        stages.append({"stage": "head", "head_sha": head,
                       "branch": branch})

        # (b) — the independent review record bound to this head.
        if self.store.latest_review(work_key, head) is None:
            review_rows = [r for r in records
                           if r["role"] == "review" and
                           r.get("ended")]
            if not review_rows:
                self._park(work_key, "review-attempt-missing",
                           "queue says reviewed but no finished "
                           "review attempt exists")
                stages.append({"stage": "review",
                               "outcome": "parked"})
                return
            row = review_rows[-1]
            info = self.worker.result_for(row["attempt_id"])
            if info is None:
                # The runtime could not resolve the task — transient
                # unavailability waits for the next tick; durable
                # state is untouched.
                stages.append({"stage": "review",
                               "outcome": "waiting",
                               "reason": "result-unresolved"})
                return
            metadata = info.get("metadata") or {}
            reviewed_sha = metadata.get("reviewed_sha") or \
                info.get("observed_head")
            if reviewed_sha and str(reviewed_sha) != head:
                self._park(work_key, "review-head-mismatch",
                           f"reviewed {reviewed_sha} but workspace "
                           f"head is {head}")
                stages.append({"stage": "review",
                               "outcome": "parked",
                               "reason": "review-head-mismatch"})
                return
            result = info.get("result")
            verdict = metadata.get("verdict") or \
                getattr(result, "verdict", None)
            if verdict not in ("approved", "changes-requested",
                               "rejected"):
                # A review result without a verdict is not evidence —
                # park rather than guess approval.
                self._park(work_key, "review-verdict-missing",
                           "the review attempt ended without a "
                           "metadata verdict")
                stages.append({"stage": "review",
                               "outcome": "parked",
                               "reason": "review-verdict-missing"})
                return
            findings = [str(f)[:300]
                        for f in (metadata.get("findings") or [])][:20]
            artifacts = []
            if info.get("kanban_task_id"):
                artifacts.append({"kind": "kanban-task",
                                  "ref": info["kanban_task_id"]})
            out = self.services.verifier.record_review(
                work_key, attempt_id=row["attempt_id"],
                session_id=row["session_id"], sha=head,
                verdict=verdict, model=row.get("model"),
                findings=findings, artifacts=artifacts or None)
            if out.get("outcome") != "recorded":
                self._park(work_key,
                           f"review-record:{out.get('reason')}",
                           "independent review could not be recorded")
                stages.append({"stage": "review",
                               "outcome": "parked",
                               "reason": out.get("reason")})
                return
            stages.append({"stage": "review", "outcome": "recorded",
                           "review_id": out.get("review_id")})
            self._log(f"review recorded for {work_key} "
                      f"(verdict {verdict})")
        else:
            stages.append({"stage": "review", "outcome": "current"})

        # (c) — publish branch + PR through the serialized broker.
        work = self.store.get_work(work_key)
        if not work.get("linked_pr"):
            published = self._publish(work, head, branch, stages)
            if not published:
                return
        else:
            stages.append({"stage": "publish", "outcome": "linked",
                           "pr": work["linked_pr"]})

        # (d) — verify the linked PR's exact head + checks.
        #
        # Race guard: GitHub registers check-runs asynchronously after
        # the push — a ``required-check-missing`` seconds after publish
        # is CI that has not started yet, not a failure. Verify only
        # once every required context has a completed run, any required
        # context completed with a disallowed conclusion, or the
        # ``check_grace_s`` window since the pr-publish intent elapsed;
        # otherwise wait without writing an evidence row (a premature
        # ``failed`` row would park the work on a lie).
        if not self._checks_ready(work_key, head, stages):
            return
        ev = self.services.verifier.verify(
            work_key, head, remote=self.remote,
            effective=self.effective)
        status = ev.get("status")
        stages.append({"stage": "verify", "status": status,
                       "reason": ev.get("reason")})
        if status == "verified":
            pass
        elif status in _WAIT_STATUSES:
            reason = str(ev.get("reason") or "")
            if status == "blocked" and \
                    reason.startswith("pr-not-open"):
                # A closed/merged PR is terminal — park, never wait.
                self._park(work_key,
                           f"verification-failed:{reason}",
                           "the linked PR left the open state")
                return
            # Pending checks / unreachable remote — wait, never park.
            self._log(f"{work_key}: verification {status} "
                      f"({ev.get('reason')}) — waiting")
            return
        else:
            self._park(work_key,
                       f"verification-failed:{ev.get('reason')}",
                       "the verification gate failed closed")
            return

        # (e) — the immutable preview behind the verified head.
        preview_status = self.services.preview.status(work_key)
        if not (preview_status.get("approval_ready") and
                preview_status.get("head_sha") == head):
            dep = self.services.preview.deploy_preview(
                work_key, head_sha=head)
            stages.append({"stage": "preview",
                           "outcome": dep.get("outcome"),
                           "reason": dep.get("reason"),
                           "url": dep.get("url")})
            if dep.get("outcome") not in ("verified", "converged"):
                return
            if dep.get("url"):
                self._log(f"{work_key}: preview {dep['url']}")
        else:
            stages.append({"stage": "preview", "outcome": "verified",
                           "url": preview_status.get("url")})

        # (f) — the one-use human approval request.
        approval_status = self.services.approval.status(work_key)
        live = approval_status.get("live_request")
        if live is None or live["state"] not in ("awaiting",
                                                 "approved"):
            req = self.services.approval.request_approval(work_key)
            stages.append({"stage": "approval",
                           "outcome": req.get("outcome"),
                           "request_id": req.get("request_id")})
            if req.get("outcome") not in ("requested", "converged"):
                return
            self._present_approval(work, req)
            live = self.store.get_approval_request(
                req["request_id"])
            if live is None:
                return
        if live["state"] == "awaiting":
            stages.append({"stage": "approval", "outcome": "awaiting",
                           "request_id": live["request_id"]})
            smoke_deadline = self._smoke_deadline(work_key)
            if smoke_deadline is not None and \
                    self._now() >= smoke_deadline:
                # The smoke evidence has already aged past
                # max_smoke_age — this request can no longer merge, so
                # re-keying it loses nothing, and a verified re-smoke
                # pushes the deadline a full window out (at most once
                # per smoke window). Only an ``awaiting`` request may
                # be re-keyed: an ``approved`` grant is the operator's
                # and is never touched here. Re-observe the deployment:
                # the fresh smoke rewrites the preview digest, this
                # request dies preview-moved, and a fresh one is minted
                # + presented.
                re = self.services.preview.resmoke(work_key)
                stages.append({"stage": "resmoke",
                               "outcome": re.get("outcome"),
                               "reason": re.get("reason")})
                if re.get("outcome") == "verified":
                    req = self.services.approval.request_approval(
                        work_key)
                    stages.append({"stage": "approval",
                                   "outcome": req.get("outcome"),
                                   "request_id":
                                       req.get("request_id"),
                                   "fresh": True})
                    if req.get("outcome") in ("requested",
                                              "converged"):
                        self._log(f"{work_key}: smoke evidence "
                                  f"expiring — re-smoked the preview "
                                  f"and requested a fresh approval")
                        self._present_approval(work, req)
            return

        # (g) — the guarded merge owner.
        out = self.services.merge.merge(work_key,
                                        request_id=live["request_id"])
        stages.append({"stage": "merge", "outcome": out.get("outcome"),
                       "reason": out.get("reason"),
                       "merge_sha": out.get("merge_sha")})
        if out.get("outcome") == "merged":
            self.services.preview.terminate(work_key, reason="merged")
            if workspace:
                try:
                    self.worker.cleanup_workspace(workspace)
                except Exception:
                    pass          # cleanup best-effort post-merge
            self._log(f"{work_key}: merged {out.get('merge_sha')}")
            return
        if out.get("outcome") == "denied" and \
                out.get("reason") in _RESMOKE_REASONS:
            # F12: the smoke evidence aged out — re-observe the live
            # deployment (rewrites the preview evidence + voids the
            # bound grant), then mint a fresh request. The merge is
            # never retried under the dead evidence.
            re = self.services.preview.resmoke(work_key)
            stages.append({"stage": "resmoke",
                           "outcome": re.get("outcome"),
                           "reason": re.get("reason")})
            if re.get("outcome") == "verified":
                req = self.services.approval.request_approval(work_key)
                stages.append({"stage": "approval",
                               "outcome": req.get("outcome"),
                               "request_id": req.get("request_id"),
                               "fresh": True})
                if req.get("outcome") in ("requested", "converged"):
                    self._present_approval(work, req)
        elif out.get("outcome") == "denied":
            self._log(f"{work_key}: merge denied "
                      f"{out.get('reason')}")
        elif out.get("outcome") == "parked":
            self._log(f"{work_key}: merge outcome parked — "
                      f"{out.get('reason')}")


    def _checks_ready(self, work_key, head, stages):
        """Whether verify() may run now. Required contexts are the
        contract's; the grace clock starts at the pr-publish intent's
        commit timestamp, never at a poll — restart-safe."""
        contract = contract_from_effective(self.effective)
        contexts = list(contract.contexts) if contract else []
        if not contexts:
            return True               # verify() reports its own gate
        try:
            runs = self.remote.check_runs(head)
        except RemoteError as exc:
            stages.append({"stage": "verify", "status": "waiting",
                           "reason": "remote-unavailable"})
            self._log(f"{work_key}: waiting for checks "
                      f"(remote unavailable: {_err_tail(exc)})")
            return False
        by_name = {}
        for run in runs or []:
            if run.get("name"):
                by_name[run["name"]] = run
        registered = sum(1 for c in contexts if c in by_name)
        completed = sum(1 for c in contexts
                        if by_name.get(c, {}).get("status")
                        == "completed")
        failed = any(
            by_name[c].get("status") == "completed" and
            by_name[c].get("conclusion") not in contract.conclusions
            for c in contexts if c in by_name)
        if completed == len(contexts) or failed:
            return True
        committed = None
        for intent in self.store.intent_rows(work_key):
            if intent.get("operation") == OPERATION_PR_PUBLISH:
                ts = _iso_to_epoch(intent.get("created_at"))
                if committed is None or ts < committed:
                    committed = ts
        if committed is not None and \
                self._now() - committed > self.check_grace_s:
            return True               # grace elapsed — verify records it
        stages.append({"stage": "verify", "status": "waiting",
                       "reason": "checks-pending",
                       "registered": registered,
                       "completed": completed})
        self._log(f"{work_key}: waiting for checks "
                  f"({registered}/{len(contexts)} registered, "
                  f"{completed} completed)")
        return False


    def _publish(self, work, head, branch, stages):
        """branch-publish then pr-publish through the broker — the
        only remote-effect path. Anything but applied/converged leaves
        the durable outcome the broker committed."""
        work_key = work["work_key"]
        repo_id = work["repo_id"]
        ident = self.effective.get("identity") or {}
        full_name = f"{ident.get('owner')}/{ident.get('name')}"
        try:
            actor = (self.remote.actor_identity() or {}).get("login")
        except Exception as exc:
            actor = None
            self._log(f"{work_key}: actor identity unreadable "
                      f"({type(exc).__name__}: {_err_tail(exc)})")
        if not actor:
            stages.append({"stage": "publish", "outcome": "waiting",
                           "reason": "actor-unresolved"})
            return False
        # The broker's permit check is the manifest's bare-login
        # allowlist — ``github:<login>`` is the *decision* ref shape,
        # not the publication actor ref.
        actor_ref = str(actor)
        base = self.base_branch
        try:
            issue_info = self._issue_info(work.get("issue"))
        except Exception:
            issue_info = None
        title = (f"{(issue_info or {}).get('title') or work_key}"
                 f" (#{work.get('issue')})")
        repository = {"repo_id": repo_id, "full_name": full_name}
        out = self.services.broker.publish(work_key, {
            "operation": OPERATION_BRANCH_PUBLISH,
            "target": branch, "base": base,
            "title": title,
            "repository": repository,
            "expected_revision": head,
            "actor_ref": actor_ref,
            "claim_expires_epoch": self._now() + 600})
        stages.append({"stage": "publish-branch",
                       "outcome": out.get("outcome"),
                       "reason": out.get("reason")})
        if out.get("outcome") not in ("applied", "converged"):
            return False
        out = self.services.broker.publish(work_key, {
            "operation": OPERATION_PR_PUBLISH,
            "target": branch, "base": base,
            "title": title,
            "repository": repository,
            "expected_revision": head,
            "actor_ref": actor_ref,
            "claim_expires_epoch": self._now() + 600})
        stages.append({"stage": "publish-pr",
                       "outcome": out.get("outcome"),
                       "reason": out.get("reason")})
        if out.get("outcome") in ("applied", "converged"):
            pr = (self.store.get_work(work_key) or {}).get("linked_pr")
            if pr:
                self._log(f"{work_key}: PR #{pr} published+linked")
            return True
        return False

    def _issue_info(self, issue):
        """The issue's current title for the PR subject — an
        authoritative re-observation, never the stale intake body."""
        source = getattr(self.services.recovery, "issue_source", None)
        if source is None:
            return None
        return source.observe_issue(issue)

    def _smoke_deadline(self, work_key):
        """Epoch when the preview's smoke observation ages out under
        ``endpoint.merge.max_smoke_age_minutes`` — ``None`` while no
        observation exists, so the caller has no deadline to race."""
        observed = (self.services.preview.status(work_key) or {}) \
            .get("observed_at")
        if not observed:
            return None
        merge_cfg = ((self.effective.get("endpoint") or {})
                     .get("merge") or {})
        max_age_s = float(merge_cfg.get(
            "max_smoke_age_minutes",
            schema.DEFAULT_ENDPOINT["max_smoke_age_minutes"])) * 60.0
        return _iso_to_epoch(observed) + max_age_s

    def _present_approval(self, work, req):
        """Render the committed request — the full presentation plus
        the exact approve/reject commands the operator runs. The
        approve-before line names the earlier of the request expiry
        and the smoke-evidence deadline so an operator never approves
        into already-dead evidence."""
        presentation = req.get("presentation") or {}
        self._log(presentation.get("text") or
                  f"approval requested: {req['request_id']}")
        expires = presentation.get("expires_epoch")
        if expires is None:
            row = self.store.get_approval_request(
                req.get("request_id")) if req.get("request_id") \
                else None
            expires = (row or {}).get("expires_epoch")
        smoke_deadline = self._smoke_deadline(work["work_key"])
        before = min([t for t in (expires, smoke_deadline)
                      if t is not None], default=None)
        if before is not None:
            self._log(f"  approve before "
                      f"{time.strftime('%H:%M:%S UTC', time.gmtime(before))}")
        self._log(f"  approve: python3 -m factory_kit.run approve "
                  f"--repo {self.repo} --request {req['request_id']}")
        self._log(f"  reject:  python3 -m factory_kit.run reject "
                  f"--repo {self.repo} --request {req['request_id']}")

    def _park(self, work_key, reason, detail):
        with self.store.transact() as tx:
            tx.set_work_state(work_key, "parked", reason=reason)
            tx.enqueue_work(work_key, tx.next_seq(), state="parked",
                            reason=reason)
            tx.record_event("work_parked", work_key=work_key,
                            reason=reason, detail=detail)
        self._log(f"{work_key}: parked — {reason}")

    def _git(self, cwd, *argv):
        """Read-only worktree probe; ``None`` when it cannot answer —
        callers classify, never guess a head."""
        if not cwd:
            return None
        try:
            proc = subprocess.run(
                [self.git_bin, "-C", str(cwd), *argv],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    # ------------------------------------------------------------------ #
    # status surface
    # ------------------------------------------------------------------ #

    def status(self):
        """One block per known work — durable state only."""
        blocks = []
        for work in self.store.work_rows():
            work_key = work["work_key"]
            queued = self.store.queue_entry(work_key)
            records = self.store.attempt_record_rows(work_key)
            impl = [r for r in records if r["role"] == "implementation"]
            rev = [r for r in records if r["role"] == "review"]
            workspace = records[-1].get("workspace") if records else None
            head = self._git(workspace, "rev-parse", "HEAD")
            evidence = self.store.latest_evidence(work_key)
            preview = self.services.preview.status(work_key)
            approval = self.services.approval.status(work_key)
            merge_rows = self.store.merge_intent_rows(work_key)
            last_merge = merge_rows[-1] if merge_rows else None
            smoke_age = preview.get("smoke_age_s")
            smoke_age = (round(smoke_age)
                         if smoke_age is not None else None)
            live = approval.get("live_request") or {}
            blocks.append({
                "issue": work.get("issue"),
                "work_key": work_key,
                "state": work.get("state"),
                "state_reason": work.get("parked_reason"),
                "queue_state": (queued or {}).get("state"),
                "queue_reason": (queued or {}).get("reason"),
                "attempts": {"implementation": len(impl),
                             "review": len(rev)},
                "head_sha": head,
                "linked_pr": work.get("linked_pr"),
                "verification": {
                    "status": (evidence or {}).get("status"),
                    "reason": (evidence or {}).get("reason"),
                },
                "preview": {
                    "state": preview.get("status"),
                    "url": preview.get("url"),
                    "smoke_age_s": smoke_age,
                },
                "approval": {
                    "request_id": live.get("request_id"),
                    "state": live.get("state") or
                    approval.get("state"),
                    "expires_epoch": live.get("expires_epoch"),
                },
                "merge": {
                    "state": (last_merge or {}).get("state"),
                    "merge_sha": (last_merge or {}).get("merge_sha"),
                },
            })
        return {"repo": self.repo, "work": blocks}
