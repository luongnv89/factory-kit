#!/usr/bin/env python3
"""One-use revision-bound approval decisions (issue #16 / Task 3.2,
PRD §3.2 F12, §6.4 Approval, §7.1).

:class:`ApprovalService` is the coordinator-owned human-authority
boundary — the only place a Telegram *decision* becomes durable merge
authority. Human decisions are durable, typed, one-use records bound
to an exact revision and evidence set; Telegram conversation state is
never authority (F12).

- **A1** — ``request_approval`` commits the ``approval_requests`` row
  (request ID, ``action=merge``, repository/PR target, exact
  head/base SHA, evidence/preview/config/policy digests, issuance and
  expiry) *before* returning the presentation the transport renders —
  repository/PR link, head/base SHA, preview URL, observed smoke
  result, selected merge method and explicit expiry.
- **A2** — ``decide`` accepts a typed decision only from a current
  allowlisted numeric Telegram actor in the configured conversation
  for the unambiguous request/target. The durable decision row
  carries the decision ID, restricted actor/chat references, the
  request's bound action/target verbatim, receipt/commit times,
  outcome and consumed/revoked state; ``approval_decided`` /
  ``approval_invalidated`` events twin every transition.
- **A3** — the manifest's ``endpoint.merge.approval_expiry_minutes``
  (default 60) bounds every grant. Forged, replayed, expired,
  rejected or revoked decisions grant no merge authority and retain
  explicit auditable reasons; a rejected request leaves the work
  human-``blocked`` — never re-prompted automatically.
- **A4** — repeated or concurrent button/text approvals and
  controller restarts produce exactly one accepted durable decision
  and at most one consumable grant: the live-slot partial index
  serializes issuance and the ``state='awaiting'`` conditional update
  (the spike-proven ``BEGIN IMMEDIATE`` CAS) makes the decide race
  un-losable. ``consume_tx`` spends the grant in the *merge owner's*
  transaction (Task 3.3) — one-use is enforced by the same CAS, not a
  second transition engine.
- **A5** — changed head/base, effective config/policy drift,
  failed/missing current checks, an unhealthy or stale preview, a
  revoked actor, or a canceled/paused task invalidates the affected
  approval — through the same-transaction hooks the verification,
  preview and control services call, through ``decide``'s boundary
  recheck, and through the deterministic ``sweep``. Resume never
  revives authority: reverification produces a fresh evidence set and
  requires a fresh human approval.
- **A6** — decisions never broaden the request: the decision row
  copies ``action``/``target`` from the committed request, and the
  store's update allowlists forbid mutation of the bound revision,
  digests and expiry.
"""

from __future__ import annotations

import hashlib
import json as _json
import re
import socket
import time
import uuid
from datetime import datetime, timezone

from factory_kit.config import schema
from factory_kit.control import commands
from factory_kit.durable.store import _utcnow
from factory_kit.execution.limits import TERMINAL_WORK_STATES

__all__ = ["ApprovalService", "APPROVE_ACTION", "DECISION_VERDICTS"]

#: The one action a request can bind — F12 approves merges only.
APPROVE_ACTION = "merge"

#: Typed decision verdicts the surface accepts.
DECISION_VERDICTS = ("approve", "reject")

#: GitHub's login shape — 1–39 chars, alnum or single interior hyphens.
_GITHUB_LOGIN_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


def _sanitize_host(host):
    """The ``cli:<host>`` chat ref's host token — hostname characters
    only, bounded; anything else collapses to a denyable ref."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", str(host or ""))[:64]
    return cleaned or None


def _telegram_id(ref):
    """``telegram:<id>`` → int, or ``None`` for any other ref — group
    chat ids are negative (``-100…``), so this parses with ``int``,
    never ``isdigit``. A non-Telegram or malformed ref is ``None``:
    the caller denies it as forged rather than crashing."""
    if not isinstance(ref, str) or not ref.startswith("telegram:"):
        return None
    try:
        return int(ref.split(":", 1)[1])
    except ValueError:
        return None


def _digest(node) -> str:
    """SHA-256 over the canonical JSON binding — two observations with
    identical binding content digest identically, so an *unchanged*
    re-verification never voids a standing approval (A5)."""
    return hashlib.sha256(
        _json.dumps(node, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=True).encode()).hexdigest()


def _load_json(raw):
    if raw is None:
        return None
    try:
        return _json.loads(raw)
    except ValueError:
        return raw


def _evidence_digest(ev) -> str:
    """The binding content of one ``verification_evidence`` row —
    status, revision, check results, review identity and the
    verification contract digest. A *new* row with different content
    is a different evidence set (A5)."""
    return _digest({
        "status": ev.get("status"),
        "head_sha": ev.get("head_sha"),
        "base_name": ev.get("base_name"),
        "base_sha": ev.get("base_sha"),
        "checks": _load_json(ev.get("checks")),
        "review_id": ev.get("review_id"),
        "review_session": ev.get("review_session"),
        "contract_digest": ev.get("contract_digest"),
        "pr_number": ev.get("pr_number"),
    })


def _preview_digest(rec) -> str:
    """The binding content of one ``preview_records`` row — the
    durable preview identity plus deployment/artifact correlation,
    revision, URL, observed smoke and expiry. A retired or superseded
    record can never satisfy this digest (A5)."""
    return _digest({
        "preview_id": rec.get("preview_id"),
        "deployment_id": rec.get("deployment_id"),
        "artifact_identity": rec.get("artifact_identity"),
        "head_sha": rec.get("head_sha"),
        "base_name": rec.get("base_name"),
        "base_sha": rec.get("base_sha"),
        "url": rec.get("url"),
        "state": rec.get("state"),
        "smoke_observed": _load_json(rec.get("smoke_observed")),
        "expires_epoch": rec.get("expires_epoch"),
    })


def _iso(epoch) -> str:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc) \
        .isoformat(timespec="seconds")


class ApprovalService:
    """The durable one-use approval boundary (§6.4 Approval, F12).

    - ``store`` — :class:`IntakeStore`; every request, decision and
      invalidation commits durable rows/events before it is reported.
    - ``registrations`` — :class:`RegistrationStore`; the active
      generation and its bound config digests are rechecked at every
      boundary, exactly like the preview permit.
    - ``configs`` — ``{repo_id: effective}`` validated manifests; the
      merge contract (method + approval expiry) and the numeric
      Telegram allowlists are read from the *current* effective config
      at commit time, never cached.
    - ``now`` — injectable epoch clock shared with the store/fixtures
      so the 60-minute expiry tests deterministically.
    - ``alert_sink`` — optional operator-channel sink (list or
      callable); every service-level invalidation emits a durable
      deduplicated alert row first, exactly like the preview surface
      (§7.3) — a lost push never loses the record.
    """

    def __init__(self, store, registrations, configs, *, now=None,
                 alert_sink=None):
        self.store = store
        self.registrations = registrations
        self.configs = dict(configs)
        self._now = now or time.time
        self._alerts = alert_sink

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

    # ------------------------------------------------------------------ #
    # A1 — issue the one-use request (commit before presentation)
    # ------------------------------------------------------------------ #

    def request_approval(self, work_key):
        """Mint the durable one-use request for ``work_key`` when the
        current verified evidence + verified preview qualify.

        Outcomes: ``requested`` (the row committed; ``presentation``
        carries the render surface), ``converged`` (the live request
        still binds the same evidence/config — re-presented, never a
        second grant), ``denied`` (authority/evidence gate — no row)."""
        work = self.store.get_work(work_key)
        if work is None:
            return {"outcome": "denied", "reason": "unknown-work"}
        effective = self.configs.get(work["repo_id"])
        if effective is None:
            return {"outcome": "denied",
                    "reason": "unregistered-authority"}
        merge = (effective.get("endpoint") or {}).get("merge") or {}
        method = merge.get("method")
        if not method:
            return {"outcome": "denied",
                    "reason": "merge-contract-missing"}
        expiry_minutes = float(merge.get(
            "approval_expiry_minutes",
            schema.DEFAULT_ENDPOINT["approval_expiry_minutes"]))
        request_id = f"apr-{uuid.uuid4().hex[:12]}"
        issued_at = _utcnow()
        now = self._now()
        live = None
        denied = None
        with self.store.transact() as tx:
            work = tx.get_work(work_key)
            auth = self._authorize(work, effective)
            if not auth["ok"]:
                denied = auth["reason"]
            else:
                pause = tx.pause_info(work_key)
                if pause["pause_state"] in ("requested", "paused"):
                    denied = "task-paused"
            if denied is None:
                evidence = tx.latest_evidence(work_key)
                bound = self._bound_preview(
                    effective, work, evidence,
                    tx.active_preview(work_key))
                if not self._evidence_ready(evidence):
                    denied = "evidence-not-current"
                elif evidence.get("pr_number") in (None, ""):
                    denied = "no-linked-pr"
                elif not bound["ok"]:
                    denied = bound["reason"]
            if denied is None:
                live = tx.live_approval_request(work_key)
                if live is not None and \
                        live["head_sha"] == evidence["head_sha"] and \
                        live["base_sha"] == evidence["base_sha"] and \
                        live["evidence_digest"] == \
                        _evidence_digest(evidence) and \
                        live["preview_digest"] == bound["digest"] and \
                        live["config_digest"] == \
                        schema.effective_digest(effective) and \
                        live["policy_digest"] == \
                        schema.policy_digest(effective):
                    # The live request already binds exactly this
                    # evidence set — re-present it; a repeated ask can
                    # never mint a second grant (A4).
                    denied = "converged"
                elif live is not None:
                    # The bound evidence moved under a live request:
                    # it dies with its evidence in the same commit
                    # that frees the live slot (A5).
                    tx.invalidate_approval_tx(live, "superseded")
                    live = None
            if denied is None:
                tx.insert_approval_request(
                    request_id, work_key=work_key, seq=tx.next_seq(),
                    task_id=work.get("task_id"),
                    generation=work.get("generation"),
                    authority_key=work.get("authority_key"),
                    repo_id=work.get("repo_id"),
                    action=APPROVE_ACTION,
                    target=self._target(effective, evidence),
                    pr_number=evidence.get("pr_number"),
                    pr_url=evidence.get("pr_url"),
                    head_sha=evidence.get("head_sha"),
                    base_name=evidence.get("base_name"),
                    base_sha=evidence.get("base_sha"),
                    evidence_id=evidence.get("evidence_id"),
                    preview_id=bound["id"],
                    evidence_digest=_evidence_digest(evidence),
                    preview_digest=bound["digest"],
                    config_digest=schema.effective_digest(effective),
                    policy_digest=schema.policy_digest(effective),
                    merge_method=method,
                    state="awaiting", issued_at=issued_at,
                    expires_epoch=now + expiry_minutes * 60.0)
                tx.enqueue_work(work_key, tx.next_seq(),
                                state="awaiting-approval",
                                reason=f"approval:{request_id}")
        if denied == "converged":
            return {"outcome": "converged",
                    "request_id": live["request_id"],
                    "work_key": work_key,
                    "presentation": self._presentation(live, work)}
        if denied is not None:
            return {"outcome": "denied", "reason": denied,
                    "work_key": work_key}
        row = self.store.get_approval_request(request_id)
        return {"outcome": "requested", "request_id": request_id,
                "work_key": work_key,
                "presentation": self._presentation(row, work)}

    # ------------------------------------------------------------------ #
    # A2/A3 — decide: typed, restricted, one-use
    # ------------------------------------------------------------------ #

    def decide(self, *, request_id=None, work_key=None, command_id=None,
               actor_ref=None, chat_ref=None, verdict,
               received_at=None, detail=None):
        """Commit one typed decision for the unambiguous request.

        ``verdict`` is ``approve`` or ``reject``. Every path — the
        accepted grant, the human rejection and every denial — writes
        its durable decision row inside one commit (A2/A3)."""
        with self.store.transact() as tx:
            return self.decide_tx(
                tx, request_id=request_id, work_key=work_key,
                command_id=command_id, actor_ref=actor_ref,
                chat_ref=chat_ref, verdict=verdict,
                received_at=received_at, detail=detail)

    def decide_tx(self, tx, *, request_id=None, work_key=None,
                  command_id=None, actor_ref=None, chat_ref=None,
                  verdict, received_at=None, detail=None):
        """The decision core callable inside a caller's ``transact``
        (the control surface commits the decision row and the command
        record in one write)."""
        received = received_at or _utcnow()
        decided_at = _utcnow()
        now = self._now()
        decision_id = f"dec-{uuid.uuid4().hex[:12]}"
        actor = commands._ref(actor_ref)
        chat = commands._ref(chat_ref)

        if actor is None:
            return self._deny_tx(tx, decision_id, None, work_key,
                                 command_id, "-", chat, verdict,
                                 received, decided_at, "no-actor",
                                 detail=detail)
        if chat is None:
            return self._deny_tx(tx, decision_id, None, work_key,
                                 command_id, actor, "-", verdict,
                                 received, decided_at, "no-chat",
                                 detail=detail)
        if verdict not in DECISION_VERDICTS:
            return self._deny_tx(tx, decision_id, None, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at,
                                 "unknown-verdict", detail=detail)
        if command_id:
            prior = tx.find_decision_by_command(command_id)
            if prior is not None:
                return {"outcome": "duplicate",
                        "recorded_outcome": prior["outcome"],
                        "reason": prior["reason"],
                        "decision_id": prior["decision_id"],
                        "request_id": prior["request_id"]}

        if request_id:
            # An explicit request binds *only* itself — a forged or
            # dead ID can never be silently resolved onto the work's
            # live request (A2: the unambiguous request/target).
            request = tx.get_approval_request(request_id)
        elif work_key:
            request = tx.live_approval_request(work_key)
        else:
            request = None
        if request is not None and work_key is not None and \
                request["work_key"] != work_key:
            return self._deny_tx(
                tx, decision_id, request, work_key, command_id,
                actor, chat, verdict, received, decided_at,
                "target-mismatch", detail=detail)
        if request is None:
            return self._deny_tx(
                tx, decision_id, None, work_key, command_id, actor,
                chat, verdict, received, decided_at,
                "unknown-request", detail=detail,
                presented=request_id)

        work_key = request["work_key"]
        work = tx.get_work(work_key)
        if work is None:
            tx.invalidate_approval_tx(request, "work-missing")
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at, "unknown-work",
                                 detail=detail)
        effective = self.configs.get(work["repo_id"])
        authz = (effective or {}).get("authorization", {})
        user = _telegram_id(actor)
        chat_id = _telegram_id(chat)
        if effective is None or user is None or \
                user not in authz.get("telegram_users", []):
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at, "forged-actor",
                                 detail=detail)
        if chat_id not in authz.get("telegram_chats", []):
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at, "wrong-chat",
                                 detail=detail)
        if not schema.is_execution_authorized(
                effective, telegram_user=user, telegram_chat=chat_id):
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at, "not-authorized",
                                 detail=detail)
        return self._decide_authorized_tx(
            tx, decision_id, request, work, command_id, actor,
            chat, verdict, received, decided_at, now, effective,
            detail=detail)

    def _decide_authorized_tx(self, tx, decision_id, request, work,
                              command_id, actor, chat, verdict,
                              received, decided_at, now, effective, *,
                              detail=None):
        """The post-authorization tail every decision channel shares:
        replay/state, expiry, the A5 lapse recheck, the human-reject
        path, the approve CAS and the durable decision rows. Channel
        callers differ only in *how* actor/chat authority was proved —
        what a proven decision may do is identical."""
        work_key = work["work_key"]
        # The request's own state — exactly one decision may move it
        # (A4). ``approved``/``consumed`` replays name themselves.
        state = request["state"]
        if state in ("approved", "consumed"):
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at, "replayed",
                                 detail=detail)
        if state != "awaiting":
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at,
                                 f"state-{state}", detail=detail)
        if now > float(request["expires_epoch"]):
            tx.invalidate_approval_tx(request, "expired",
                                     state="expired")
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at, "expired",
                                 detail=detail)

        # Boundary recheck (A5): the grant cannot outlive its subject —
        # fence, terminal work, pause, config drift and the bound
        # evidence/preview set are all re-evaluated inside this commit.
        lapse = self._lapse_reason(tx, request, work, effective, now)
        if lapse is not None:
            tx.invalidate_approval_tx(request, lapse)
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at, lapse,
                                 detail=detail)

        if verdict == "reject":
            # A3 — the human declined: the request dies, the work is
            # durably human-blocked, and nothing re-prompts it
            # automatically (``request_approval`` denies blocked work).
            cur = tx._q(
                "UPDATE approval_requests SET state='rejected',"
                " decision_id=?, actor_ref=?, updated_at=?"
                " WHERE request_id=? AND state='awaiting'",
                (decision_id, actor, _utcnow(), request["request_id"]))
            if cur.rowcount != 1:
                return self._deny_tx(
                    tx, decision_id, request, work_key, command_id,
                    actor, chat, verdict, received, decided_at,
                    "replayed", detail=detail)
            self._record_decision_tx(
                tx, decision_id, request, command_id, actor, chat,
                verdict, "rejected", received, decided_at,
                reason="rejected-by-actor", detail=detail)
            tx.set_work_state(work_key, "blocked",
                              reason="approval-rejected")
            tx.enqueue_work(work_key, tx.next_seq(), state="blocked",
                            reason="approval-rejected")
            tx.record_event("dispatch_blocked", work_key=work_key,
                            reason="approval-rejected")
            return {"outcome": "rejected", "reason": "rejected-by-actor",
                    "request_id": request["request_id"],
                    "decision_id": decision_id, "work_key": work_key}

        # approve — the one CAS that consumes the live slot: only the
        # transaction whose conditional update lands wins (the spike-
        # proven primitive, A4).
        cur = tx._q(
            "UPDATE approval_requests SET state='approved',"
            " decision_id=?, actor_ref=?, updated_at=?"
            " WHERE request_id=? AND state='awaiting'",
            (decision_id, actor, _utcnow(), request["request_id"]))
        if cur.rowcount != 1:
            return self._deny_tx(tx, decision_id, request, work_key,
                                 command_id, actor, chat, verdict,
                                 received, decided_at, "replayed",
                                 detail=detail)
        self._record_decision_tx(
            tx, decision_id, request, command_id, actor, chat, verdict,
            "accepted", received, decided_at, detail=detail,
            state="accepted")
        return {"outcome": "approved",
                "request_id": request["request_id"],
                "decision_id": decision_id, "work_key": work_key,
                "action": request["action"], "target": request["target"],
                "head_sha": request["head_sha"],
                "base_sha": request["base_sha"],
                "merge_method": request["merge_method"],
                "expires_epoch": request["expires_epoch"],
                "expires_iso": _iso(request["expires_epoch"])}

    # ------------------------------------------------------------------ #
    # operator-cli channel — a verified GitHub login deciding from the
    # local driver CLI (endpoint.merge.approval_channels entry
    # "operator-cli")
    # ------------------------------------------------------------------ #

    def decide_operator(self, *, request_id, github_login,
                        verified_login, verdict, host=None,
                        received_at=None, detail=None):
        """Commit one typed decision from the operator CLI channel.

        The deciding actor is the *verified* GitHub login —
        ``actor_ref="github:<login>"``, ``chat_ref="cli:<host>"``
        (hostname default :func:`socket.gethostname`, sanitized).
        Only an explicit ``request_id`` resolves — a dead or forged
        ID can never slide onto the work's live request (A2).
        Denials: ``no-actor`` (empty/malformed login),
        ``unknown-request``, ``channel-not-enabled`` (``operator-cli``
        absent from ``approval_channels``), ``forged-actor`` (login
        differs from ``verified_login`` or is off ``github_actors``),
        ``not-authorized`` (the CFG02 permit check), then exactly the
        shared post-authorization tail (:meth:`decide_tx`)."""
        received = received_at or _utcnow()
        decided_at = _utcnow()
        now = self._now()
        decision_id = f"dec-{uuid.uuid4().hex[:12]}"
        host = _sanitize_host(host or socket.gethostname())
        chat = f"cli:{host}" if host else None
        login = str(github_login or "").strip()

        with self.store.transact() as tx:
            if chat is None:
                return self._deny_tx(
                    tx, decision_id, None, None, None, "-", "-",
                    verdict, received, decided_at, "no-chat",
                    detail=detail)
            if not _GITHUB_LOGIN_RE.match(login):
                return self._deny_tx(
                    tx, decision_id, None, None, None, "-", chat,
                    verdict, received, decided_at, "no-actor",
                    detail=detail)
            actor = f"github:{login}"
            if verdict not in DECISION_VERDICTS:
                return self._deny_tx(
                    tx, decision_id, None, None, None, actor, chat,
                    verdict, received, decided_at, "unknown-verdict",
                    detail=detail)
            request = tx.get_approval_request(request_id)
            if request is None:
                return self._deny_tx(
                    tx, decision_id, None, None, None, actor, chat,
                    verdict, received, decided_at, "unknown-request",
                    detail=detail, presented=request_id)
            work_key = request["work_key"]
            work = tx.get_work(work_key)
            if work is None:
                tx.invalidate_approval_tx(request, "work-missing")
                return self._deny_tx(
                    tx, decision_id, request, work_key, None, actor,
                    chat, verdict, received, decided_at,
                    "unknown-work", detail=detail)
            effective = self.configs.get(work["repo_id"])
            channels = (((effective or {}).get("endpoint") or {})
                        .get("merge") or {}).get("approval_channels",
                                                 ["telegram"])
            if "operator-cli" not in channels:
                return self._deny_tx(
                    tx, decision_id, request, work_key, None, actor,
                    chat, verdict, received, decided_at,
                    "channel-not-enabled", detail=detail)
            authz = (effective or {}).get("authorization", {})
            allowlisted = any(
                str(a).lower() == login.lower()
                for a in authz.get("github_actors", []))
            if effective is None or not allowlisted or \
                    login.lower() != str(verified_login or "").lower():
                return self._deny_tx(
                    tx, decision_id, request, work_key, None, actor,
                    chat, verdict, received, decided_at,
                    "forged-actor", detail=detail)
            if not schema.is_execution_authorized(
                    effective, github_actor=login):
                return self._deny_tx(
                    tx, decision_id, request, work_key, None, actor,
                    chat, verdict, received, decided_at,
                    "not-authorized", detail=detail)
            return self._decide_authorized_tx(
                tx, decision_id, request, work, None, actor, chat,
                verdict, received, decided_at, now, effective,
                detail=detail)

    def _record_decision_tx(self, tx, decision_id, request, command_id,
                            actor, chat, verdict, outcome, received,
                            decided_at, *, reason=None, detail=None,
                            state=None):
        """Append the decision row — action/target copied verbatim
        from the committed request so a decision can never broaden
        the grant (A6). ``state`` is ``accepted`` for the winning
        approve (later ``consumed``/``revoked``), ``recorded`` for
        rejections/denials."""
        tx.insert_approval_decision(
            decision_id, request_id=request["request_id"],
            work_key=request["work_key"], seq=tx.next_seq(),
            command_id=command_id, actor_ref=actor, chat_ref=chat,
            action=request["action"], target=request["target"],
            verdict=verdict, outcome=outcome, reason=reason,
            state=state or "recorded", received_at=received,
            decided_at=decided_at, detail=detail)

    def _deny_tx(self, tx, decision_id, request, work_key, command_id,
                 actor, chat, verdict, received, decided_at, reason,
                 *, detail=None, presented=None):
        """Persist one denied decision attempt — the auditable trail
        A3 requires for forged/replayed/expired/rejected authority."""
        tx.insert_approval_decision(
            decision_id,
            request_id=(request["request_id"] if request
                        else (presented or "-")),
            work_key=(request["work_key"] if request else work_key),
            seq=tx.next_seq(), command_id=command_id, actor_ref=actor,
            chat_ref=chat,
            action=(request["action"] if request else "-"),
            target=(request["target"] if request else "-"),
            verdict=verdict if verdict in DECISION_VERDICTS else None,
            outcome="denied", reason=reason, state="recorded",
            received_at=received, decided_at=decided_at,
            detail=detail)
        return {"outcome": "denied", "reason": reason,
                "decision_id": decision_id,
                "request_id": request["request_id"] if request else
                presented}

    # ------------------------------------------------------------------ #
    # A4 — one-use consumption (the Task 3.3 seam)
    # ------------------------------------------------------------------ #

    def consume(self, request_id, *, now_epoch=None):
        """Spend the one-use grant in its own commit — the standalone
        form; the merge owner calls :meth:`consume_tx` inside its own
        intent transaction instead (Task 3.3)."""
        with self.store.transact() as tx:
            return self.consume_tx(tx, request_id, now_epoch=now_epoch)

    def consume_tx(self, tx, request_id, *, now_epoch=None):
        """Atomically spend the grant inside the caller's commit —
        the proven primitive Task 3.3 consumes with its merge intent:
        ``approved`` → ``consumed`` under the same conditional update
        that serialized the decide, so two consumers can never both
        read a live grant (A4). Returns the bound authority fields —
        the caller gets action/target/revision verbatim from the
        request, never from the message."""
        req = tx.get_approval_request(request_id)
        if req is None:
            return {"outcome": "denied", "reason": "unknown-request"}
        now = self._now() if now_epoch is None else now_epoch
        if req["state"] == "consumed":
            return {"outcome": "denied", "reason": "replayed",
                    "consumed_seq": req.get("consumed_seq")}
        if req["state"] != "approved":
            return {"outcome": "denied",
                    "reason": f"state-{req['state']}"}
        # Boundary recheck (A5): consumption is the last commit before
        # the merge owner acts — expiry, fence/terminal/pause, config
        # drift, revoked deciding actor and moved evidence/preview are
        # all re-evaluated inside this commit, not just at decide
        # time or on the sweep's cadence.
        work = tx.get_work(req["work_key"])
        effective = self.configs.get(work["repo_id"]) if work else None
        lapse = self._lapse_reason(tx, req, work, effective, now)
        if lapse is not None:
            tx.invalidate_approval_tx(
                req, lapse,
                state="expired" if lapse == "expired" else
                "invalidated")
            return {"outcome": "denied", "reason": lapse}
        seq = tx.next_seq()
        cur = tx._q(
            "UPDATE approval_requests SET state='consumed',"
            " consumed_seq=?, updated_at=?"
            " WHERE request_id=? AND state='approved'",
            (seq, _utcnow(), request_id))
        if cur.rowcount != 1:
            return {"outcome": "denied", "reason": "replayed"}
        if req.get("decision_id"):
            tx.update_approval_decision(req["decision_id"],
                                        state="consumed")
        return {"outcome": "consumed", "request_id": request_id,
                "decision_id": req.get("decision_id"),
                "consumed_seq": seq,
                "work_key": req["work_key"],
                "action": req["action"], "target": req["target"],
                "pr_number": req["pr_number"], "pr_url": req["pr_url"],
                "head_sha": req["head_sha"],
                "base_name": req["base_name"],
                "base_sha": req["base_sha"],
                "merge_method": req["merge_method"],
                "actor_ref": req["actor_ref"],
                "issued_at": req["issued_at"],
                "expires_epoch": req["expires_epoch"]}

    # ------------------------------------------------------------------ #
    # A5 — invalidation (hooks + sweep)
    # ------------------------------------------------------------------ #

    def invalidate(self, work_key, reason):
        """Retire every live request the work holds — the direct
        invalidation API (cancel/pause callers use the store-level
        ``invalidate_approvals_tx`` inside their own commit)."""
        with self.store.transact() as tx:
            return {"outcome": "invalidated",
                    "requests": tx.invalidate_approvals_tx(
                        work_key, reason)}

    def invalidate_for_evidence(self, work_key):
        """A5 evidence hook — the verification service calls this
        after appending an observation. A *changed* or non-verified
        latest observation voids standing grants; an identical
        re-observation keeps them (unchanged evidence)."""
        with self.store.transact() as tx:
            out = []
            ev = tx.latest_evidence(work_key)
            for req in tx.live_approval_requests(work_key):
                if ev is None or ev["status"] != "verified":
                    reason = "evidence-not-current"
                elif _evidence_digest(ev) != req["evidence_digest"]:
                    reason = "evidence-moved"
                else:
                    continue
                tx.invalidate_approval_tx(req, reason)
                out.append({"request_id": req["request_id"],
                            "reason": reason})
        for hit in out:
            self._alert("approval-invalidated", work_key, "medium",
                        f"{hit['request_id']}: {hit['reason']}")
        return {"outcome": "swept", "invalidated": out}

    def invalidate_for_preview(self, work_key):
        """A5 preview hook — the preview service calls this when a
        record retires. The grant dies whenever its bound preview row
        is no longer the work's verified, unexpired active preview."""
        with self.store.transact() as tx:
            out = []
            work = tx.get_work(work_key)
            bound = self._bound_preview(
                self.configs.get(work["repo_id"]) if work else None,
                work, tx.latest_evidence(work_key),
                tx.active_preview(work_key))
            for req in tx.live_approval_requests(work_key):
                if bound["id"] != req["preview_id"] or \
                        bound["digest"] != req["preview_digest"]:
                    reason = "preview-moved"
                elif not bound["ok"]:
                    reason = bound["reason"]
                else:
                    continue
                tx.invalidate_approval_tx(req, reason)
                out.append({"request_id": req["request_id"],
                            "reason": reason})
        for hit in out:
            self._alert("approval-invalidated", work_key, "medium",
                        f"{hit['request_id']}: {hit['reason']}")
        return {"outcome": "swept", "invalidated": out}

    def sweep(self):
        """The deterministic A5 sweep: every live request re-passes
        the full lapse check — expiry, fence/terminal/pause, config
        drift, revoked deciding actor, moved evidence/preview. Each
        invalidation commits independently so one bad row can never
        wedge the pass."""
        now = self._now()
        report = {"expired": [], "invalidated": [], "kept": [],
                  "errors": []}
        expired_wk = {}
        for req in self.store.live_approval_requests():
            try:
                with self.store.transact() as tx:
                    fresh = tx.get_approval_request(req["request_id"])
                    if fresh is None or fresh["state"] not in \
                            self.store.APPROVAL_LIVE_STATES:
                        continue
                    work = tx.get_work(fresh["work_key"])
                    effective = self.configs.get(
                        work["repo_id"]) if work else None
                    lapse = self._lapse_reason(
                        tx, fresh, work, effective, now)
                    if lapse == "expired":
                        tx.invalidate_approval_tx(
                            fresh, "expired", state="expired")
                        report["expired"].append(fresh["request_id"])
                        expired_wk[fresh["request_id"]] = \
                            fresh["work_key"]
                    elif lapse is not None:
                        tx.invalidate_approval_tx(fresh, lapse)
                        report["invalidated"].append(
                            {"request_id": fresh["request_id"],
                             "work_key": fresh["work_key"],
                             "reason": lapse})
                    else:
                        report["kept"].append(fresh["request_id"])
            except Exception as exc:      # surfaced, never dropped
                report["errors"].append(
                    {"request_id": req["request_id"],
                     "error": type(exc).__name__})
        for hit in report["invalidated"]:
            self._alert("approval-invalidated", hit["work_key"],
                        "medium",
                        f"{hit['request_id']}: {hit['reason']}")
        for request_id in report["expired"]:
            self._alert("approval-expired", expired_wk[request_id],
                        "medium", f"{request_id}: expired")
        return report

    def _lapse_reason(self, tx, req, work, effective, now):
        """The full A5 lapse check inside the caller's transaction —
        ordered so the *authoritative* reason lands first. ``None``
        means the grant still stands on current evidence."""
        if now > float(req["expires_epoch"]):
            return "expired"
        if work is None:
            return "work-missing"
        fence = tx.fence_state(req["work_key"])
        if fence is not None and fence["fenced"]:
            return "fenced"
        if work.get("state") in TERMINAL_WORK_STATES:
            return "work-terminal"
        pause = tx.pause_info(req["work_key"])
        if pause["pause_state"] in ("requested", "paused"):
            return "task-paused"
        record = self.registrations.get(work["repo_id"]) \
            if self.registrations is not None else None
        if record is None or effective is None:
            return "stale-config"
        bound = None
        for gen in record["generations"]:
            if gen["generation"] == work.get("generation"):
                bound = gen
                break
        if bound is None or \
                work.get("generation") != record["active_generation"]:
            return "stale-generation"
        if record.get("pending_policy"):
            return "stale-config"
        if req["state"] == "approved":
            # The decided actor must *still* hold the permit — a
            # revoked allowlist voids the grant it produced (A5).
            # Checked before the generic digest drift so the precise
            # cause, not ``stale-config``, names the lapse.
            decision = tx.get_approval_decision(req["decision_id"]) \
                if req.get("decision_id") else None
            actor = (decision or {}).get("actor_ref") \
                or req.get("actor_ref")
            chat = (decision or {}).get("chat_ref")
            if actor and actor.startswith("github:"):
                # An operator-CLI grant holds only while the deciding
                # GitHub login is still authorized *and* the channel
                # itself is still enabled — either lapsing revokes it.
                login = actor.split(":", 1)[1]
                channels = (((effective or {}).get("endpoint") or {})
                            .get("merge") or {}).get(
                                "approval_channels", ["telegram"])
                if "operator-cli" not in channels or \
                        not schema.is_execution_authorized(
                            effective, github_actor=login):
                    return "actor-revoked"
            else:
                user = _telegram_id(actor)
                chat_id = _telegram_id(chat)
                if user is None or not schema.is_execution_authorized(
                        effective, telegram_user=user,
                        telegram_chat=chat_id):
                    return "actor-revoked"
        if schema.effective_digest(effective) != req["config_digest"] \
                or schema.policy_digest(effective) != \
                req["policy_digest"]:
            return "stale-config"
        ev = tx.latest_evidence(req["work_key"])
        if ev is None or ev["status"] != "verified":
            return "evidence-not-current"
        if _evidence_digest(ev) != req["evidence_digest"]:
            return "evidence-moved"
        bound = self._bound_preview(effective, work, ev,
                                    tx.active_preview(req["work_key"]))
        if bound["id"] != req["preview_id"] or \
                bound["digest"] != req["preview_digest"]:
            return "preview-moved"
        if not bound["ok"]:
            return bound["reason"]
        return None

    # ------------------------------------------------------------------ #
    # status
    # ------------------------------------------------------------------ #

    def status(self, work_key):
        """The work's current approval position — the live request,
        the latest decision, and ``consumable`` (an unexpired
        ``approved`` grant). ``merge_authorized`` stays ``False`` —
        nothing here ever merges; consumption is the merge owner's
        one-use spend (Task 3.3)."""
        now = self._now()
        live = self.store.live_approval_request(work_key)
        rows = self.store.approval_request_rows(work_key)
        decisions = self.store.approval_decision_rows(work_key=work_key)
        last = rows[-1] if rows else None
        consumable = live is not None and live["state"] == "approved" \
            and now <= float(live["expires_epoch"])
        return {
            "work_key": work_key,
            "state": live["state"] if live else
            (last["state"] if last else "none"),
            "request_id": live["request_id"] if live else
            (last["request_id"] if last else None),
            "live_request": live,
            "decisions": decisions,
            "accepted_decisions":
                [d for d in decisions if d["outcome"] == "accepted"],
            "consumable": bool(consumable),
            "merge_authorized": False,
        }

    def awaiting_approvals(self):
        """Durable awaiting requests — the restart-safe surface a
        recovery pass re-presents from (the store is authority; chat
        history is never replayed, A4)."""
        return [r for r in self.store.approval_request_rows()
                if r["state"] == "awaiting"]

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _authorize(self, work, effective):
        """The same commit-time permit recheck the preview boundary
        runs — fence, registration, active generation, bound config
        digests, work liveness and the generation's authorizing
        principal against the *current* manifest (A5)."""
        if work is None:
            return {"ok": False, "reason": "unknown-work"}
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

    def _evidence_ready(self, evidence):
        return evidence is not None and evidence["status"] == "verified"

    def _bound_preview(self, effective, work, evidence, rec):
        """The preview binding the current state supports — the single
        gate every approval path re-derives from the *bound config*.

        Returns ``{"id", "digest", "ok", "reason"}``. Under a declared
        ``endpoint.preview.provider: none`` the binding is the constant
        :data:`schema.NO_PREVIEW_BINDING`, and a preview row appearing
        anyway fails closed (``preview-unexpected``). Otherwise the
        verified, current preview row binds as before; a missing row
        never satisfies it."""
        if not schema.preview_required(effective):
            if rec is not None:
                return {"id": None, "digest": schema.NO_PREVIEW_BINDING,
                        "ok": False, "reason": "preview-unexpected"}
            return {"id": None, "digest": schema.NO_PREVIEW_BINDING,
                    "ok": True, "reason": None}
        if rec is None:
            return {"id": None, "digest": None, "ok": False,
                    "reason": "preview-not-current"}
        ok = self._preview_ready(work, evidence, rec)
        return {"id": rec.get("preview_id"),
                "digest": _preview_digest(rec), "ok": ok,
                "reason": None if ok else "preview-not-current"}

    def _preview_ready(self, work, evidence, rec):
        """``approval_ready`` recomputed now — a verified, unexpired,
        unfenced preview record whose head still equals the verified
        observation's head (mirrors ``PreviewService.status``; old
        evidence can never substitute, A3)."""
        if rec is None or rec["state"] != "verified" or evidence is None:
            return False
        expires = rec.get("expires_epoch")
        if expires is not None and float(expires) < self._now():
            return False
        fence = self.store.fence_state(rec["work_key"])
        if fence is not None and fence["fenced"]:
            return False
        return evidence["status"] == "verified" and \
            evidence.get("head_sha") == rec["head_sha"]

    def _target(self, effective, evidence):
        """The unambiguous merge target — ``owner/name#pr`` (A1/A2)."""
        ident = effective.get("identity", {})
        full = f"{ident.get('owner')}/{ident.get('name')}"
        return f"{full}#{evidence.get('pr_number')}"

    def _presentation(self, req, work=None):
        """The A1 render surface — built only from the *committed*
        row: repository/PR link, head/base SHA, preview URL, observed
        smoke, merge method and explicit expiry, plus the accessible
        approve/reject text alternatives."""
        preview = self.store.get_preview(req.get("preview_id")) \
            if req.get("preview_id") else None
        smoke = _load_json(preview.get("smoke_observed")) \
            if preview else None
        effective = self.configs.get(req["repo_id"]) or {}
        ident = effective.get("identity", {})
        full = f"{ident.get('owner')}/{ident.get('name')}"
        work = work or self.store.get_work(req["work_key"])
        issue = work.get("issue") if work else None
        generation = work.get("generation") if work else None
        expiry_min = round(
            (float(req["expires_epoch"]) - self._now()) / 60.0)
        smoke_txt = _json.dumps(smoke, sort_keys=True) \
            if smoke is not None else "unobserved"
        if preview is None and \
                req.get("preview_digest") == schema.NO_PREVIEW_BINDING:
            preview_line = (
                "Preview: none - the manifest declares"
                " endpoint.preview.provider: none; the evidence is the"
                " required CI checks and the independent review.")
        else:
            preview_line = (
                f"Preview {preview['url'] if preview else 'none'}"
                f" verified; smoke {smoke_txt}.")
        lines = [
            f"APPROVAL REQUIRED - merge authority for {req['target']}.",
            f"Request {req['request_id']} binds action={req['action']}"
            f" head {req['head_sha']} base"
            f" {req.get('base_name') or '-'}@{req.get('base_sha')}.",
            preview_line,
            f"Method: {req['merge_method']}. Expires"
            f" {_iso(req['expires_epoch'])} (~{expiry_min}m).",
            f"Reply 'approve {req['repo_id']} {issue} {generation}' or"
            f" 'reject {req['repo_id']} {issue} {generation}'.",
        ]
        return {
            "request_id": req["request_id"],
            "repository": full,
            "repo_id": req["repo_id"],
            "issue": issue, "generation": generation,
            "action": req["action"], "target": req["target"],
            "pr_number": req["pr_number"], "pr_url": req["pr_url"],
            "head_sha": req["head_sha"],
            "base_name": req["base_name"], "base_sha": req["base_sha"],
            "preview_id": req.get("preview_id"),
            "preview_url": preview["url"] if preview else None,
            "smoke_observed": smoke,
            "merge_method": req["merge_method"],
            "issued_at": req["issued_at"],
            "expires_epoch": req["expires_epoch"],
            "expires_iso": _iso(req["expires_epoch"]),
            "approve_command": f"approve {req['repo_id']} {issue} "
                               f"{generation}",
            "reject_command": f"reject {req['repo_id']} {issue} "
                              f"{generation}",
            "text": "\n".join(lines),
        }
