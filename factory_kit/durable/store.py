#!/usr/bin/env python3
"""Durable intake store — PRD §6.4 REC02, §5.1 persistence (issue #8).

The kit-owned half of the F02 convergence contract. Spike-proven shape
(``tools/probes/spike_faults.py::FaultStore``, ``endpoint_walkthrough.py
::EndpointStore``): one SQLite database, one serialized writer, every
acknowledgement preceded by a durable commit.

Tables:

- ``meta`` — the monotonic sequence every identity mints from.
- ``work`` — one row per *logical* work identity: repository authority key
  + issue + explicit execution generation. ``work_key`` is the primary key
  and ``(authority_key, issue, generation)`` carries a UNIQUE index, so a
  duplicate logical task cannot exist even under racing writers. The row
  also carries the delivery-proof fields §6.4 requires: source context
  fingerprint, authorization evidence, configuration/policy digests, the
  reserved Hermes task association and the linked PR (set once).
- ``deliveries`` — delivery dedup, **distinct** from work identity: the
  same ``delivery_id`` never writes twice, and a *distinct* delivery for
  an existing ``work_key`` converges onto it instead of minting a second
  task. Denied deliveries write no row here — they persist only a
  redacted ``work_rejected`` event.
- ``events`` — the §7.1 intake event trail (``work_accepted`` /
  ``work_rejected`` / ``delivery_deduplicated`` / ``work_reconciled`` /
  ``work_updated`` / ``work_parked`` / task-binding transitions /
  ``attempt_started`` / ``attempt_finished``). Rows carry
  identity/delivery IDs, reason codes and digests — never issue bodies,
  signatures or secrets.
- ``attempt_ledger`` — at-most-one-active-attempt per work row, enforced
  by a partial unique index so the cap cannot be raced. The execution
  lane (Task 2.4) claims through it; intake only guarantees the
  structure.
- ``lane_state`` — the single bounded execution lane's current occupant.
  One row per lane ID (``main``); ``acquire_lane`` is atomic inside
  ``transact`` so two dispatches can never overlap.
- ``work_queue`` — durable queue position and the *visible* reason a
  ready task is waiting (``lane-occupied:<work_key>``).
- ``attempt_records`` — the §6.4 Attempt record: task/attempt/
  generation, role, session, runtime/model, pinned skills, workspace,
  config/policy digests, limits snapshot, heartbeat, verdict/usage
  outcome.
- ``attempt_liveness`` — heartbeat bookkeeping per attempt (last beat +
  epoch), the input the 60-second fence sweep reads.
- ``attempt_usage`` — per-attempt measured usage; ``active_seconds``
  NULL means *unknown* — never zero-filled (§7.1 measured-or-unknown).
- ``results`` — worker candidate-result intake, deduplicated by
  ``result_id``; only a live (active, unfenced) attempt may have a
  result accepted — late output is rejected durably.
- ``execution_fence`` — per-work fence/termination state: fenced flag,
  reason, and termination certainty (``pending`` → ``confirmed`` /
  ``quarantined`` → operator ``resolved``). A replacement generation is
  eligible only after fencing plus confirmed/resolved termination.
- ``alerts`` — operator-visible alert rows deduplicated per
  (kind, identity, severity) — heartbeat and budget alerts fire once per
  identity at each severity transition (§7.3).
- ``publication_intents`` — the §6.4 Publication intent record (Task
  2.5): stable operation identity, task/generation, repository, expected
  revision, permitted operation, target branch/PR and the remote
  outcome/ambiguity. One durable row is committed *before* the effect is
  sent, so a crash between remote create and response persistence is
  reconciled by identity on recovery instead of republished blindly.
- ``control_records`` — the §6.4 Control record / §7.1
  ``control_recorded`` event (Task 2.7): command identity, restricted
  actor/chat reference, action, target, receipt/commit times and the
  accepted/rejected outcome — persisted before any acknowledgement.
- ``work_control`` — per-work control state (Task 2.7): pause
  requested/paused/resumed plus the stage the next dispatch must resume
  at, so a pause is durable across restarts and a resume never replays
  a completed stage.
- ``review_records`` — the §6.4 independent-review record (Task 2.6 /
  F04 A1): the separate reviewer session's own identity (attempt +
  session + verdict + findings) bound to the exact revision it
  inspected. Implementation self-reports can never mint one — the
  record is accepted only against a real finished ``review``-role
  attempt on a session no implementation attempt used.
- ``verification_evidence`` — the §6.4 Evidence record / §7.1
  ``evidence_checked`` (Task 2.6 / F04 A2–A6): one timestamped
  observation row per evaluation — PR identity, head/base SHA,
  check-run identities/conclusions, review identity, artifact/log
  references and the passing/stale/unknown reason. ``verified`` is a
  point-in-time observation bound to a SHA, never merge authority;
  later observations append new rows rather than rewriting history.
- ``reconcile_watermark`` — the F06 reconciliation bookkeeping row
  (Task 2.8): last GitHub observation attempt/success, consecutive
  failures, the remote-state word, the stale-episode anchor and the
  next scheduled pass. Durable so "last successful reconciliation"
  and the 5-minute stale alert survive a controller restart.
- ``operator_effort`` — the §7.1 ``operator_effort_recorded`` rows
  (Task 2.9 / F07 A2): task identity, the restricted actor reference,
  operator-supplied ``active_minutes`` and the intervention
  ``category`` from a fixed vocabulary. ``NULL`` minutes/category
  mean *unknown* — never zero-filled. A correction appends a new row
  carrying ``supersedes=<prior effort_id>``; the superseded row and
  its event stay in the trail unrewritten (A1/A7).
- ``participant_agreements`` — recorded participant agreement per
  export scope (Task 2.9 / A6). An aggregate pilot export exists only
  against a durable row here; it can never silently enable external
  collection.
- ``merge_intents`` — the §6.4 Merge outcome record (Task 3.3 / F12):
  the single merge owner's serialized intent. One row commits inside
  the same transaction that consumes the one-use approval — request/
  decision identities, expected head/base, method, granting actor —
  *before* the conditional merge is sent; the outcome side carries the
  remote's authoritative merged flag, merge commit SHA, actual merge
  actor and read-back time. One intent per request is index-enforced
  and one *live* intent per work (recorded/parked) serializes
  concurrent merges; unknown outcomes park until reconciliation
  resolves them.

The ``events`` table also carries ``supersedes`` (Task 2.9 / A1): a
correction appends a new event pointing at the earlier row's seq —
history is never rewritten.

Durability rules honoured here:

- ``isolation_level=None`` + explicit ``BEGIN IMMEDIATE`` +
  ``PRAGMA synchronous=FULL`` + ``busy_timeout``: every multi-row
  transition is atomic and survives a host crash at the commit boundary.
- An ambiguous or failed durable write raises — callers must never
  translate a torn write into a "safely queued" acknowledgement (A5).
- Denied events persist a *redacted reason* only; nothing in this module
  accepts or stores issue body text, signatures or key material.
"""

from __future__ import annotations

import json as _json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from factory_kit.events import schema as _events_schema

__all__ = ["IntakeStore", "IntakeStoreError", "work_key_for"]


class IntakeStoreError(Exception):
    """A durable write failed or was ambiguous — never acknowledge it."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def work_key_for(authority_key, issue, generation) -> str:
    """The logical work identity: repository authority + issue + the
    explicit execution generation bound at registration (§6.4 REC02).

    Delivery IDs are deliberately absent — dedup lives in ``deliveries``
    so webhook, polling and restart converge on this one key.
    """
    return f"{authority_key}#{int(issue)}:g{int(generation)}"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v INTEGER);
INSERT OR IGNORE INTO meta VALUES('seq', 0);

CREATE TABLE IF NOT EXISTS work(
    work_key TEXT PRIMARY KEY,
    authority_key TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    issue INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    task_id TEXT,
    task_state TEXT NOT NULL DEFAULT 'pending',
    state TEXT NOT NULL,
    context_fingerprint TEXT,
    issue_revision TEXT,
    authorization_evidence TEXT,
    config_digest TEXT,
    policy_digest TEXT,
    linked_pr TEXT,
    reevaluation_required INTEGER NOT NULL DEFAULT 0,
    parked_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS work_logical
    ON work(authority_key, issue, generation);

CREATE TABLE IF NOT EXISTS deliveries(
    delivery_id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    work_key TEXT,
    outcome TEXT NOT NULL,
    reason TEXT,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    delivery_id TEXT,
    work_key TEXT,
    reason TEXT,
    config_digest TEXT,
    detail TEXT,
    supersedes INTEGER);

CREATE TABLE IF NOT EXISTS attempt_ledger(
    attempt_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    role TEXT NOT NULL,
    state TEXT NOT NULL,
    started TEXT NOT NULL,
    ended TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt_per_work
    ON attempt_ledger(work_key) WHERE state='active';

CREATE TABLE IF NOT EXISTS lane_state(
    lane TEXT PRIMARY KEY,
    occupied_work TEXT,
    occupied_task TEXT,
    occupied_since TEXT);

CREATE TABLE IF NOT EXISTS work_queue(
    work_key TEXT PRIMARY KEY,
    enqueued_seq INTEGER NOT NULL,
    state TEXT NOT NULL,
    reason TEXT,
    detail TEXT,
    enqueued_at TEXT);

CREATE TABLE IF NOT EXISTS attempt_records(
    attempt_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    task_id TEXT,
    generation INTEGER NOT NULL,
    role TEXT NOT NULL,
    session_id TEXT,
    runtime TEXT,
    model TEXT,
    skills TEXT,
    config_digest TEXT,
    policy_digest TEXT,
    workspace TEXT,
    limits TEXT,
    heartbeat_at TEXT,
    heartbeat_epoch REAL,
    beats INTEGER NOT NULL DEFAULT 0,
    verdict TEXT,
    outcome TEXT,
    started TEXT NOT NULL,
    ended TEXT,
    duration_s REAL,
    usage_state TEXT);

CREATE TABLE IF NOT EXISTS attempt_liveness(
    attempt_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    last_beat TEXT NOT NULL,
    last_beat_epoch REAL NOT NULL,
    beats INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS attempt_usage(
    attempt_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    role TEXT NOT NULL,
    active_seconds REAL,
    usage TEXT,
    recorded_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS results(
    result_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    work_key TEXT,
    accepted INTEGER NOT NULL,
    reason TEXT,
    ts TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS execution_fence(
    work_key TEXT PRIMARY KEY,
    generation INTEGER NOT NULL,
    fenced INTEGER NOT NULL DEFAULT 0,
    fence_reason TEXT,
    termination TEXT NOT NULL DEFAULT 'none',
    termination_detail TEXT,
    updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS alerts(
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    identity TEXT NOT NULL,
    severity TEXT NOT NULL,
    detail TEXT,
    UNIQUE(kind, identity, severity));

CREATE TABLE IF NOT EXISTS publication_intents(
    intent_id TEXT PRIMARY KEY,
    work_key TEXT,
    seq INTEGER,
    task_id TEXT,
    generation INTEGER,
    authority_key TEXT,
    repo_id TEXT,
    operation TEXT NOT NULL,
    expected_revision TEXT,
    target TEXT,
    actor_ref TEXT,
    claim_expires_epoch REAL,
    state TEXT NOT NULL,
    reason TEXT,
    remote_ref TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
-- One serialized operation per (work, operation, target): concurrent
-- publication attempts converge on the single committed row, so a racing
-- second request can never produce a second remote effect (§6.4, A3).
CREATE UNIQUE INDEX IF NOT EXISTS one_intent_per_op_target
    ON publication_intents(work_key, operation, target)
    WHERE work_key IS NOT NULL AND target IS NOT NULL;

CREATE TABLE IF NOT EXISTS control_records(
    command_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    chat_ref TEXT NOT NULL,
    action TEXT NOT NULL,
    repo_id TEXT,
    authority_key TEXT,
    issue INTEGER,
    generation INTEGER,
    work_key TEXT,
    outcome TEXT NOT NULL,
    reason TEXT,
    detail TEXT,
    seq INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS work_control(
    work_key TEXT PRIMARY KEY,
    pause_state TEXT NOT NULL DEFAULT 'none',
    paused_stage TEXT,
    detail TEXT,
    updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS review_records(
    review_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT,
    verdict TEXT NOT NULL,
    findings TEXT,
    sha TEXT NOT NULL,
    artifacts TEXT,
    recorded_at TEXT NOT NULL);
-- One review record per review attempt — a session's verdict is
-- recorded exactly once; re-reviews are new attempts, not rewrites.
CREATE UNIQUE INDEX IF NOT EXISTS one_review_per_attempt
    ON review_records(attempt_id);
CREATE INDEX IF NOT EXISTS review_by_work_sha
    ON review_records(work_key, sha);

CREATE TABLE IF NOT EXISTS verification_evidence(
    evidence_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    seq INTEGER,
    pr_number TEXT,
    pr_url TEXT,
    head_sha TEXT,
    base_name TEXT,
    base_sha TEXT,
    checks TEXT,
    review_id TEXT,
    review_session TEXT,
    artifacts TEXT,
    contract_digest TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    observed_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evidence_by_work
    ON verification_evidence(work_key, observed_at);

-- The reconciliation watermark (Task 2.8 / F06): one durable singleton
-- row carrying last GitHub observation attempt/success, consecutive
-- failure count, the remote-state word (fresh/unknown/stale), the
-- stale episode anchor and the next scheduled pass. Persisted state —
-- not process memory — so "last successful reconciliation" stays
-- observable across a restart (§5.1) and the 5-minute stale rule (§7.3)
-- cannot reset just because the controller died.
CREATE TABLE IF NOT EXISTS reconcile_watermark(
    id INTEGER PRIMARY KEY CHECK(id=1),
    last_attempt_epoch REAL,
    last_success_epoch REAL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    remote_state TEXT NOT NULL DEFAULT 'unknown',
    stale_since_epoch REAL,
    next_due_epoch REAL,
    updated_at TEXT NOT NULL);

-- Operator effort (Task 2.9 / §7.1 operator_effort_recorded, F07 A2):
-- one append-only row per operator-supplied effort report. A NULL
-- active_minutes/category is the honest *unknown* (A3); a correction
-- is a new row whose ``supersedes`` names the earlier ``effort_id`` —
-- the superseded row and its event are never rewritten (A1/A7).
CREATE TABLE IF NOT EXISTS operator_effort(
    effort_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    actor_ref TEXT NOT NULL,
    active_minutes REAL,
    category TEXT,
    source TEXT,
    supersedes TEXT,
    seq INTEGER NOT NULL,
    event_seq INTEGER NOT NULL,
    recorded_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS effort_by_work
    ON operator_effort(work_key, recorded_at);

-- Recorded participant agreement (Task 2.9 / A6): an aggregate pilot
-- export exists only against a durable row here — external collection
-- can never be silently enabled by a missing flag.
CREATE TABLE IF NOT EXISTS participant_agreements(
    scope TEXT PRIMARY KEY,
    actor_ref TEXT NOT NULL,
    detail TEXT,
    seq INTEGER NOT NULL,
    recorded_at TEXT NOT NULL);

-- Preview evidence (Task 3.1 / F11, §6.4): one durable row per preview
-- deployment bound to the exact head/base revision it covers — provider,
-- deployment/artifact identity, URL, visibility, the configured smoke
-- contract and its observed result, expiry and cleanup ownership. The
-- row commits *before* the provider effect so a crash between deploy and
-- response persistence is reconciled by identity, and the partial unique
-- index enforces one *active* preview per work (deploying/deployed/
-- verified) — a superseding revision retires the old row before the new
-- one mints (A5). ``removed_at`` is set only on provider-confirmed
-- removal; an owned deployment whose removal could not be confirmed sits
-- visibly in ``cleanup-pending`` — never a false removal claim.
CREATE TABLE IF NOT EXISTS preview_records(
    preview_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    seq INTEGER,
    task_id TEXT,
    generation INTEGER,
    provider TEXT,
    deployment_id TEXT,
    artifact_identity TEXT,
    head_sha TEXT,
    base_name TEXT,
    base_sha TEXT,
    url TEXT,
    visibility TEXT,
    environment TEXT,
    smoke_command TEXT,
    smoke_expected TEXT,
    smoke_observed TEXT,
    state TEXT NOT NULL,
    reason TEXT,
    cleanup_owner TEXT,
    expires_epoch REAL,
    cleanup_deadline_epoch REAL,
    observed_at TEXT,
    removed_at TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_preview_per_work
    ON preview_records(work_key)
    WHERE state IN ('deploying','deployed','verified');
CREATE INDEX IF NOT EXISTS preview_by_work
    ON preview_records(work_key, created_at);
CREATE INDEX IF NOT EXISTS preview_by_deployment
    ON preview_records(deployment_id);

-- One-use approval authority (Task 3.2 / F12, §6.4 Approval): one
-- durable row per issued human-approval request, binding the action,
-- the repository/PR target, the exact head/base revision, the
-- evidence/preview/config/policy digests it grants on, issuance and
-- expiry, and the decision identity that resolved it. States:
-- ``awaiting`` (issued, undecided), ``approved`` (accepted decision,
-- consumable exactly once), ``rejected`` (the human declined — the
-- work is human-blocked), ``expired``, ``invalidated`` (revision/
-- evidence/config/actor/task moved under it) and ``consumed`` (the
-- one merge authority was spent atomically by the merge owner).
CREATE TABLE IF NOT EXISTS approval_requests(
    request_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    seq INTEGER,
    task_id TEXT,
    generation INTEGER,
    authority_key TEXT,
    repo_id TEXT,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    pr_number TEXT,
    pr_url TEXT,
    head_sha TEXT,
    base_name TEXT,
    base_sha TEXT,
    evidence_id TEXT,
    preview_id TEXT,
    evidence_digest TEXT,
    preview_digest TEXT,
    config_digest TEXT,
    policy_digest TEXT,
    merge_method TEXT,
    state TEXT NOT NULL,
    reason TEXT,
    decision_id TEXT,
    actor_ref TEXT,
    issued_at TEXT NOT NULL,
    expires_epoch REAL NOT NULL,
    consumed_seq INTEGER,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
-- One *live* (awaiting/approved) request per work — concurrent
-- issuance and decision races converge on the single committed row,
-- the same way the active-preview slot is un-raceable (A4).
CREATE UNIQUE INDEX IF NOT EXISTS one_live_approval_per_work
    ON approval_requests(work_key)
    WHERE state IN ('awaiting','approved');
CREATE INDEX IF NOT EXISTS approval_by_work
    ON approval_requests(work_key, created_at);

-- Approval decisions (F12 A2/A3): append-only human-decision history.
-- Every typed decision reaching the service — accepted, rejected or
-- denied (forged actor, wrong chat, replayed, expired, stale
-- evidence) — persists a row carrying the restricted actor/chat
-- references, the request's bound action/target (never broader than
-- the request), receipt + commit times, the outcome and the auditable
-- reason. ``state`` is the consumed/revoked lifecycle of the winning
-- accepted row; denied/rejected rows stay ``recorded``.
CREATE TABLE IF NOT EXISTS approval_decisions(
    decision_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    work_key TEXT,
    seq INTEGER,
    command_id TEXT,
    actor_ref TEXT NOT NULL,
    chat_ref TEXT,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    verdict TEXT,
    outcome TEXT NOT NULL,
    reason TEXT,
    state TEXT NOT NULL,
    received_at TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    detail TEXT);
CREATE INDEX IF NOT EXISTS decisions_by_request
    ON approval_decisions(request_id, decided_at);
CREATE INDEX IF NOT EXISTS decisions_by_command
    ON approval_decisions(command_id);

-- Guarded merge intents (Task 3.3 / F12, §6.4 Merge outcome): one durable
-- row per consumed approval — the atomic approval→intent spend commits in a
-- single ``transact`` before any remote merge call. The row binds the
-- request/decision identities, the exact expected head/base revision the
-- conditional merge carries, the selected method, the granting actor and
-- the authoritative outcome: ``merged`` flag, actual merge commit SHA, the
-- remote-recorded merge actor and the read-back time. States: ``recorded``
-- (committed; the remote effect may or may not have been sent — the crash
-- window recovery reconciles), ``merged`` (read-back confirmed the merge),
-- ``not-merged`` (the remote refused or the preconditions lapsed before
-- send), ``parked`` (the outcome is unknown — a lost response with an
-- unreadable remote; reconciled, never retried blindly) and
-- ``invalidated`` (fence/cancel/expiry won before the effect).
CREATE TABLE IF NOT EXISTS merge_intents(
    intent_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    seq INTEGER,
    task_id TEXT,
    generation INTEGER,
    authority_key TEXT,
    repo_id TEXT,
    request_id TEXT NOT NULL,
    decision_id TEXT,
    pr_number TEXT,
    expected_head TEXT,
    expected_base_name TEXT,
    expected_base_sha TEXT,
    merge_method TEXT,
    actor_ref TEXT,
    state TEXT NOT NULL,
    reason TEXT,
    merged INTEGER,
    merge_sha TEXT,
    merged_by TEXT,
    merge_actor_kind TEXT,
    expires_epoch REAL,
    sent_at TEXT,
    read_back_at TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
-- One merge intent per consumed approval — the one-use grant can never
-- mint two effects (A3).
CREATE UNIQUE INDEX IF NOT EXISTS one_merge_intent_per_request
    ON merge_intents(request_id);
-- One *live* (recorded/parked) merge intent per work — concurrent merge
-- invocations converge on the single committed row, and a parked unknown
-- holds the slot until reconciliation resolves it (A3/A6).
CREATE UNIQUE INDEX IF NOT EXISTS one_live_merge_intent_per_work
    ON merge_intents(work_key)
    WHERE state IN ('recorded','parked');
CREATE INDEX IF NOT EXISTS merge_intents_by_work
    ON merge_intents(work_key, created_at);

-- Durable notification outbox (Task 3.4 / §7.1 notification_*,
-- §7.3 delivery-failure row, A1): one row per (kind, identity,
-- severity) dedup triple — the same alert dedup discipline — carrying
-- the authorized destination reference, the redacted body, the
-- bounded-retry bookkeeping (attempts, first/last/next attempt epochs,
-- last error) and the terminal outcome (delivered/failed). A
-- severity *transition* mints a new row; a repeated same-severity
-- signal dedups onto the pending/delivered/failed record, so a
-- transport outage can never multiply sends and a restart can never
-- lose the pending record.
CREATE TABLE IF NOT EXISTS notification_outbox(
    notification_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    identity TEXT NOT NULL,
    severity TEXT NOT NULL,
    destination_ref TEXT NOT NULL,
    body TEXT,
    work_key TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    first_attempt_epoch REAL,
    last_attempt_epoch REAL,
    next_attempt_epoch REAL,
    last_error TEXT,
    terminal_outcome TEXT,
    seq INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_notification_per_signal
    ON notification_outbox(kind, identity, severity);
CREATE INDEX IF NOT EXISTS notifications_by_work
    ON notification_outbox(work_key, state);
CREATE INDEX IF NOT EXISTS notifications_due
    ON notification_outbox(state, next_attempt_epoch);
"""


class _EagerCursor:
    """A ``sqlite3.Cursor`` stand-in whose rows are already materialized.

    :meth:`IntakeStore._q` executes *and* fetches under the store's
    ``RLock``, so the single shared connection is never stepped by two
    threads mid-read — a bare ``execute`` returning a lazy cursor would
    let a second thread's query interleave between the first cursor's
    steps and corrupt both ("bad parameter or other API misuse").
    """

    def __init__(self, rows, *, rowcount=-1, lastrowid=None):
        self._rows = list(rows)
        self._pos = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        rows = self._rows[self._pos:]
        self._pos = len(self._rows)
        return list(rows)

    def __iter__(self):
        while self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            yield row


class IntakeStore:
    """SQLite-backed durable intake records.

    ``path`` may be a filesystem path or ``":memory:"`` for tests. The
    single connection is shared across threads the way the spike proved
    (``check_same_thread=False`` + a busy timeout), and ``transact``
    holds an ``RLock`` across ``BEGIN IMMEDIATE``…``COMMIT`` so
    overlapping in-process writers serialize instead of tripping the
    shared connection's nested-transaction error. A second process (or
    connection) serializes on the database write lock.
    """

    def __init__(self, path):
        self._lock = threading.RLock()
        try:
            self.db = sqlite3.connect(str(path), isolation_level=None,
                                      check_same_thread=False)
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.executescript(_SCHEMA)
            self._migrate()
        except sqlite3.Error as exc:
            raise IntakeStoreError(
                f"cannot open intake store {path}: {exc}") from exc

    def _migrate(self):
        """In-place additive upgrades for databases created before a
        column existed (``CREATE TABLE IF NOT EXISTS`` alone cannot add
        one). Currently: ``events.supersedes`` (Task 2.9) and
        ``work_queue.enqueued_at`` (Task 3.4 — the durable queue-age
        source the A2 views read)."""
        cols = {r[1] for r in
                self.db.execute("PRAGMA table_info(events)")}
        if "supersedes" not in cols:
            self.db.execute(
                "ALTER TABLE events ADD COLUMN supersedes INTEGER")
        cols = {r[1] for r in
                self.db.execute("PRAGMA table_info(work_queue)")}
        if "enqueued_at" not in cols:
            self.db.execute(
                "ALTER TABLE work_queue ADD COLUMN enqueued_at TEXT")

    def close(self):
        db = getattr(self, "db", None)
        if db is None:
            return
        try:
            db.close()
        except sqlite3.Error:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- serialized write boundary ------------------------------------------

    @contextmanager
    def transact(self):
        """One ``BEGIN IMMEDIATE`` write transaction.

        Everything intake commits — dedup check, decision writes, the event
        row — happens inside this single boundary, so a crash mid-write
        leaves either the whole record or nothing. Any failure rolls back
        and re-raises as :class:`IntakeStoreError`; a caller that catches it
        must not acknowledge the delivery.

        The store's ``RLock`` is held for the whole transaction so racing
        in-process writers serialize on the boundary rather than failing
        on the shared connection's single active-transaction state.
        """
        with self._lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise IntakeStoreError(
                    f"cannot begin durable write: {exc}") from exc
            try:
                yield self
            except Exception as exc:
                try:
                    self.db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                if isinstance(exc, IntakeStoreError):
                    raise
                raise IntakeStoreError(
                    f"durable intake write failed: {exc}") from exc
            try:
                self.db.execute("COMMIT")
            except sqlite3.Error as exc:
                raise IntakeStoreError(
                    f"durable intake commit failed: {exc}") from exc

    def _q(self, sql, params=()):
        """Execute + materialize under the store lock — the shared
        connection never carries a lazy cursor across threads."""
        try:
            with self._lock:
                cur = self.db.execute(sql, params)
                return _EagerCursor(cur.fetchall(),
                                    rowcount=cur.rowcount,
                                    lastrowid=cur.lastrowid)
        except sqlite3.Error as exc:
            raise IntakeStoreError(f"intake store query failed: {exc}") \
                from exc

    def next_seq(self) -> int:
        """Mint the next durable sequence number (inside ``transact``)."""
        self._q("UPDATE meta SET v = v + 1 WHERE k = 'seq'")
        return self._q("SELECT v FROM meta WHERE k='seq'").fetchone()[0]

    # -- deliveries ----------------------------------------------------------

    def find_delivery(self, delivery_id):
        """Return the durable delivery row or ``None`` (dedup lookup)."""
        if not delivery_id:
            return None
        row = self._q(
            "SELECT work_key, outcome, reason, channel, seq FROM deliveries"
            " WHERE delivery_id=?", (delivery_id,)).fetchone()
        if row is None:
            return None
        return {"work_key": row[0], "outcome": row[1], "reason": row[2],
                "channel": row[3], "seq": row[4]}

    def record_delivery(self, delivery_id, channel, work_key, outcome,
                        reason, seq):
        """Persist the delivery→work binding (accepted/reconciled/revoked
        paths only — denials never create delivery rows)."""
        self._q(
            "INSERT INTO deliveries"
            "(delivery_id,channel,work_key,outcome,reason,seq,ts)"
            " VALUES(?,?,?,?,?,?,?)",
            (delivery_id, channel, work_key, outcome, reason, seq,
             _utcnow()))

    # -- work ----------------------------------------------------------------

    def get_work(self, work_key):
        row = self._q(
            "SELECT work_key,authority_key,repo_id,issue,generation,"
            "task_id,task_state,state,context_fingerprint,issue_revision,"
            "authorization_evidence,config_digest,policy_digest,linked_pr,"
            "reevaluation_required,parked_reason,created_at,updated_at"
            " FROM work"
            " WHERE work_key=?", (work_key,)).fetchone()
        return self._work_row(row)

    def find_work(self, authority_key, issue, generation):
        """The logical-identity lookup: at most one row can exist per
        (authority_key, issue, generation)."""
        row = self._q(
            "SELECT work_key FROM work WHERE authority_key=? AND issue=?"
            " AND generation=? ORDER BY rowid LIMIT 1",
            (authority_key, int(issue), int(generation))).fetchone()
        return self.get_work(row[0]) if row else None

    @staticmethod
    def _work_row(row):
        if row is None:
            return None
        cols = ("work_key", "authority_key", "repo_id", "issue",
                "generation", "task_id", "task_state", "state",
                "context_fingerprint", "issue_revision",
                "authorization_evidence", "config_digest", "policy_digest",
                "linked_pr", "reevaluation_required", "parked_reason",
                "created_at", "updated_at")
        return dict(zip(cols, row))

    def insert_work(self, work_key, authority_key, repo_id, issue,
                    generation, task_id, state, context_fingerprint,
                    issue_revision, authorization_evidence, config_digest,
                    policy_digest):
        """Create the durable logical work row + its reserved Hermes task
        association (``task_id`` bound at accept; ``task_state`` starts
        ``pending`` until the publish leg or a repair pass binds it)."""
        now = _utcnow()
        self._q(
            "INSERT INTO work VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (work_key, authority_key, repo_id, int(issue), int(generation),
             task_id, "pending", state, context_fingerprint,
             issue_revision, authorization_evidence, config_digest,
             policy_digest, None, 0, None, now, now))

    def update_work_context(self, work_key, context_fingerprint,
                            issue_revision):
        """Pending-issue edit: refresh the source context fingerprint and
        observed revision in place (A4 — pending work is updated, never
        duplicated)."""
        self._q(
            "UPDATE work SET context_fingerprint=?, issue_revision=?,"
            " updated_at=? WHERE work_key=?",
            (context_fingerprint, issue_revision, _utcnow(), work_key))

    def set_reevaluation(self, work_key):
        """Active-issue edit: flag the work for re-evaluation. A new
        generation — never this row — is what parallel work would take,
        so an edit here can never spawn it (A4)."""
        self._q(
            "UPDATE work SET reevaluation_required=1, updated_at=?"
            " WHERE work_key=?", (_utcnow(), work_key))

    def park_work(self, work_key, reason):
        """Fence a pending/active work row — a revoked opt-in reconciled
        against current state. Parked rows stay parked for their
        generation; re-authorization is a new explicit generation."""
        self._q(
            "UPDATE work SET state='parked', parked_reason=?,"
            " updated_at=? WHERE work_key=?",
            (reason, _utcnow(), work_key))

    def activate_work(self, work_key):
        """accepted→active marker for the execution lane."""
        self._q(
            "UPDATE work SET state='active', updated_at=?"
            " WHERE work_key=? AND state='pending'",
            (_utcnow(), work_key))

    # -- Hermes task association ----------------------------------------------

    def bind_task(self, work_key, task_id):
        """Mark the reserved association published. Idempotent: a binding
        is bound at most once and always to the same ``task_id`` — repair
        after a crash can never double-dispatch (A5)."""
        row = self.get_work(work_key)
        if row is None:
            raise IntakeStoreError(f"no work row {work_key!r}")
        if row["task_state"] == "bound" and row["task_id"] == task_id:
            return {"outcome": "already-bound", "task_id": task_id}
        if row["task_state"] == "bound":
            raise IntakeStoreError(
                f"work {work_key!r} bound to {row['task_id']!r}, "
                f"not {task_id!r}")
        self._q(
            "UPDATE work SET task_state='bound', updated_at=?"
            " WHERE work_key=? AND task_id=?",
            (_utcnow(), work_key, task_id))
        return {"outcome": "bound", "task_id": task_id}

    def pending_task_bindings(self):
        """Work rows whose reserved task association was never confirmed —
        the crash window ``repair_task_associations`` closes (A5)."""
        rows = self._q(
            "SELECT work_key, task_id FROM work WHERE task_state='pending'"
            " ORDER BY rowid").fetchall()
        return [{"work_key": r[0], "task_id": r[1]} for r in rows]

    # -- attempts + linked PR --------------------------------------------------

    def begin_attempt(self, work_key, role, attempt_id):
        """At most one active attempt per work row — enforced by the
        partial unique index, so concurrent claims cannot race it (A2)."""
        with self.transact():
            open_row = self._q(
                "SELECT attempt_id FROM attempt_ledger WHERE work_key=?"
                " AND state='active'", (work_key,)).fetchone()
            if open_row:
                return {"outcome": "denied", "reason": "attempt-active",
                        "active": open_row[0]}
            self._q(
                "INSERT INTO attempt_ledger"
                "(attempt_id,work_key,role,state,started,ended)"
                " VALUES(?,?,?,?,?,NULL)",
                (attempt_id, work_key, role, "active", _utcnow()))
            return {"outcome": "active", "attempt_id": attempt_id}

    def end_attempt(self, attempt_id, state="completed"):
        with self.transact():
            self._q(
                "UPDATE attempt_ledger SET state=?, ended=?"
                " WHERE attempt_id=?", (state, _utcnow(), attempt_id))

    # -- execution lane (Task 2.4 / F03) ------------------------------------
    #
    # The lane claims through ``attempt_ledger`` (the one-active-attempt
    # cap intake guaranteed) and records the full §6.4 Attempt shape in
    # ``attempt_records``. Every multi-row transition below runs inside
    # one ``transact`` so claim, liveness and the ``attempt_started`` /
    # ``attempt_finished`` events commit together.

    def lane(self, lane="main"):
        """Current occupant of the named execution lane, or ``None``."""
        row = self._q(
            "SELECT lane,occupied_work,occupied_task,occupied_since"
            " FROM lane_state WHERE lane=?", (lane,)).fetchone()
        if row is None:
            return None
        return {"lane": row[0], "occupied_work": row[1],
                "occupied_task": row[2], "occupied_since": row[3]}

    def acquire_lane(self, work_key, task_id, lane="main"):
        """Atomically occupy the lane for ``work_key``.

        ``occupied`` is a verdict, never an exception — the caller marks
        the would-be dispatch durably queued with the returned
        ``occupied_by`` as its visible reason (A1).
        """
        with self.transact():
            current = self.lane(lane)
            if current is not None and current["occupied_work"]:
                return {"outcome": "occupied", "lane": lane,
                        "occupied_by": current["occupied_work"]}
            self._q(
                "INSERT OR REPLACE INTO lane_state VALUES(?,?,?,?)",
                (lane, work_key, task_id, _utcnow()))
            return {"outcome": "acquired", "lane": lane,
                    "occupied_work": work_key}

    def release_lane(self, lane="main", expected_work=None):
        """Free the lane; returns the released occupant.

        ``expected_work`` guards against a late release clearing another
        task's occupancy — a mismatch is denied, never silently clears.
        """
        with self.transact():
            current = self.lane(lane)
            if current is None or not current["occupied_work"]:
                return {"outcome": "idle", "lane": lane}
            if expected_work is not None and \
                    current["occupied_work"] != expected_work:
                return {"outcome": "denied", "reason": "not-occupant",
                        "occupied_by": current["occupied_work"]}
            self._q(
                "UPDATE lane_state SET occupied_work=NULL,"
                " occupied_task=NULL, occupied_since=NULL WHERE lane=?",
                (lane,))
            return {"outcome": "released", "lane": lane,
                    "released": current["occupied_work"]}

    # -- durable queue -------------------------------------------------------

    def enqueue_work(self, work_key, seq, state="queued", reason=None,
                     detail=None, enqueued_at=None):
        """Upsert a durable queue row — the *visible* reason a ready task
        waits (``lane-occupied:<occupant>``) survives restarts (A1).
        ``enqueued_at`` stamps the *entry* time (Task 3.4 A2 queue age):
        a re-upsert keeps the original stamp so an operator-visible wait
        cannot be reset by a reconcile rewrite."""
        stamp = enqueued_at or _utcnow()
        self._q(
            "INSERT INTO work_queue"
            "(work_key,enqueued_seq,state,reason,detail,enqueued_at)"
            " VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(work_key) DO UPDATE SET"
            " state=excluded.state, reason=excluded.reason,"
            " detail=excluded.detail,"
            " enqueued_at=COALESCE(work_queue.enqueued_at,"
            " excluded.enqueued_at)",
            (work_key, int(seq), state, reason, detail, stamp))

    def queue_entry(self, work_key):
        row = self._q(
            "SELECT work_key,enqueued_seq,state,reason,detail,"
            "enqueued_at FROM work_queue WHERE work_key=?",
            (work_key,)).fetchone()
        if row is None:
            return None
        return {"work_key": row[0], "enqueued_seq": row[1],
                "state": row[2], "reason": row[3], "detail": row[4],
                "enqueued_at": row[5]}

    def queue_rows(self, state=None):
        rows = [dict(zip(("work_key", "enqueued_seq", "state", "reason",
                          "detail", "enqueued_at"), r))
                for r in self._q(
                    "SELECT work_key,enqueued_seq,state,reason,detail,"
                    "enqueued_at FROM work_queue ORDER BY enqueued_seq")]
        if state is None:
            return rows
        return [r for r in rows if r["state"] == state]

    def set_work_state(self, work_key, state, reason=None):
        """Terminal/passive work states the lane drives: ``active``,
        ``completed``, ``parked``, ``quarantined``, ``blocked``,
        ``canceled`` (terminal for the generation — Task 2.7).

        ``reason`` is recorded into ``parked_reason`` for parked,
        quarantined, blocked and canceled rows so the block is always
        attributable.
        """
        if state not in ("pending", "active", "completed", "parked",
                         "quarantined", "blocked", "canceled"):
            raise IntakeStoreError(f"unknown work state {state!r}")
        if state in ("parked", "quarantined", "blocked", "canceled"):
            self._q(
                "UPDATE work SET state=?, parked_reason=?, updated_at=?"
                " WHERE work_key=?", (state, reason, _utcnow(), work_key))
        else:
            self._q(
                "UPDATE work SET state=?, updated_at=? WHERE work_key=?",
                (state, _utcnow(), work_key))

    # -- execution attempts -----------------------------------------------------

    def begin_execution_attempt(
            self, work_key, *, attempt_id, role, task_id, generation,
            session_id, runtime, model, skills, config_digest,
            policy_digest, workspace, limits, now_epoch):
        """Claim + record one role attempt atomically.

        One commit carries: the one-active-attempt claim
        (``attempt_ledger``), the §6.4 attempt record, the first
        heartbeat liveness row and the ``attempt_started`` event — the
        event can never describe an attempt that was not durably claimed
        (A2/§7.1).
        """
        with self.transact() as tx:
            work = tx.get_work(work_key)
            if work is None:
                return {"outcome": "denied", "reason": "unknown-work"}
            if work["state"] != "active":
                return {"outcome": "denied",
                        "reason": f"work-{work['state']}"}
            fence = tx.fence_state(work_key)
            if fence and fence["fenced"]:
                return {"outcome": "denied", "reason": "generation-fenced"}
            open_row = tx._q(
                "SELECT attempt_id FROM attempt_ledger WHERE work_key=?"
                " AND state='active'", (work_key,)).fetchone()
            if open_row:
                return {"outcome": "denied", "reason": "attempt-active",
                        "active": open_row[0]}
            now = _utcnow()
            tx._q(
                "INSERT INTO attempt_ledger"
                "(attempt_id,work_key,role,state,started,ended)"
                " VALUES(?,?,?,?,?,NULL)",
                (attempt_id, work_key, role, "active", now))
            tx._q(
                "INSERT INTO attempt_records VALUES"
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (attempt_id, work_key, task_id, int(generation), role,
                 session_id, runtime, model, skills, config_digest,
                 policy_digest, workspace, limits, now, now_epoch, 1,
                 None, None, now, None, None, None))
            tx._q(
                "INSERT INTO attempt_liveness VALUES(?,?,?,?,?)",
                (attempt_id, work_key, now, now_epoch, 1))
            tx.record_event(
                "attempt_started", work_key=work_key,
                config_digest=config_digest,
                detail=_json.dumps({
                    "task_id": task_id, "attempt_id": attempt_id,
                    "generation": int(generation), "role": role,
                    "runtime": runtime, "model": model,
                    "session_id": session_id, "workspace": workspace},
                    sort_keys=True))
            return {"outcome": "active", "attempt_id": attempt_id}

    def heartbeat(self, attempt_id, now_epoch):
        """Record one liveness beat. Only a live (active, unfenced)
        attempt may beat — a fenced attempt's heartbeat is denied so a
        late worker can never appear alive (A6)."""
        with self.transact() as tx:
            row = tx._q(
                "SELECT l.work_key, a.state FROM attempt_liveness l"
                " JOIN attempt_ledger a ON a.attempt_id=l.attempt_id"
                " WHERE l.attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                return {"outcome": "denied", "reason": "unknown-attempt"}
            if row[1] != "active":
                return {"outcome": "denied",
                        "reason": f"attempt-{row[1]}"}
            fence = tx.fence_state(row[0])
            if fence and fence["fenced"]:
                return {"outcome": "denied",
                        "reason": "generation-fenced"}
            now = _utcnow()
            tx._q(
                "UPDATE attempt_liveness SET last_beat=?,"
                " last_beat_epoch=?, beats=beats+1 WHERE attempt_id=?",
                (now, now_epoch, attempt_id))
            tx._q(
                "UPDATE attempt_records SET heartbeat_at=?,"
                " heartbeat_epoch=?, beats=beats+1 WHERE attempt_id=?",
                (now, now_epoch, attempt_id))
            return {"outcome": "beat", "attempt_id": attempt_id}

    def finish_execution_attempt(
            self, attempt_id, *, verdict, outcome, active_seconds, usage,
            duration_s):
        """Close one attempt: ledger end + record outcome + usage row +
        ``attempt_finished`` event, one commit (§7.1).

        ``active_seconds=None`` and ``usage=None`` record *unknown* —
        honest gaps, never zero-filled (A4/A7). A present ``usage``
        mapping is normalized through
        :func:`factory_kit.events.schema.normalize_usage` first so
        token/provider, billed-amount/currency, quota and the four
        wait-class durations persist as separate fields (Task 2.9 A3).
        """
        usage_state = "measured" if usage is not None else "unknown"
        usage = _events_schema.normalize_usage(usage) \
            if usage is not None else None
        with self.transact() as tx:
            row = tx._q(
                "SELECT work_key, role, state FROM attempt_ledger"
                " WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None:
                return {"outcome": "denied", "reason": "unknown-attempt"}
            work_key, role, state = row
            if state != "active":
                return {"outcome": "denied",
                        "reason": f"attempt-{state}"}
            now = _utcnow()
            tx._q(
                "UPDATE attempt_ledger SET state=?, ended=?"
                " WHERE attempt_id=?", (outcome, now, attempt_id))
            tx._q(
                "UPDATE attempt_records SET verdict=?, outcome=?,"
                " ended=?, duration_s=?, usage_state=?"
                " WHERE attempt_id=?",
                (verdict, outcome, now, duration_s, usage_state,
                 attempt_id))
            rec = tx._q(
                "SELECT task_id, generation FROM attempt_records"
                " WHERE attempt_id=?", (attempt_id,)).fetchone()
            tx._q(
                "INSERT OR REPLACE INTO attempt_usage"
                "(attempt_id,work_key,role,active_seconds,usage,"
                "recorded_at) VALUES(?,?,?,?,?,?)",
                (attempt_id, work_key, role, active_seconds,
                 _json.dumps(usage, sort_keys=True)
                 if usage is not None else None, now))
            tx.record_event(
                "attempt_finished", work_key=work_key,
                detail=_json.dumps({
                    "task_id": rec[0] if rec else None,
                    "attempt_id": attempt_id,
                    "generation": rec[1] if rec else None,
                    "role": role, "duration_s": duration_s,
                    "verdict": verdict, "outcome": outcome,
                    "usage": usage if usage is not None else "unknown"},
                    sort_keys=True))
            return {"outcome": outcome, "attempt_id": attempt_id,
                    "work_key": work_key, "role": role}

    # -- result intake (worker candidates commit nothing) ----------------------

    def accept_result(self, result_id, attempt_id, detail=None):
        """Record a worker's candidate result — deduplicated by
        ``result_id``, accepted only for a live attempt on an unfenced
        work row. Late output from a fenced/expired generation is
        rejected with the durable reason (A6); the worker *requests*,
        only the lane commits (A3)."""
        with self.transact() as tx:
            prior = tx._q(
                "SELECT accepted, reason FROM results WHERE result_id=?",
                (result_id,)).fetchone()
            if prior:
                return {"result_id": result_id, "outcome": "deduplicated",
                        "accepted": bool(prior[0]), "reason": prior[1]}
            row = tx._q(
                "SELECT work_key, state FROM attempt_ledger"
                " WHERE attempt_id=?", (attempt_id,)).fetchone()
            accepted, reason, work_key = 0, "unknown-attempt", None
            if row is not None:
                work_key = row[0]
                fence = tx.fence_state(work_key)
                if fence and fence["fenced"]:
                    reason = "generation-fenced"
                elif row[1] != "active":
                    reason = f"attempt-{row[1]}"
                else:
                    accepted, reason = 1, None
            tx._q(
                "INSERT INTO results VALUES(?,?,?,?,?,?)",
                (result_id, attempt_id, work_key, accepted, reason,
                 _utcnow()))
            if not accepted:
                tx.record_event(
                    "result_rejected", work_key=work_key, reason=reason,
                    detail=f"attempt={attempt_id}")
            return {"result_id": result_id, "accepted": accepted,
                    "outcome": "accepted" if accepted else "denied",
                    "reason": reason}

    # -- fencing (A5/A6) ---------------------------------------------------------

    def fence_state(self, work_key):
        row = self._q(
            "SELECT work_key,generation,fenced,fence_reason,termination,"
            "termination_detail,updated_at FROM execution_fence"
            " WHERE work_key=?", (work_key,)).fetchone()
        if row is None:
            return None
        return {"work_key": row[0], "generation": row[1],
                "fenced": bool(row[2]), "fence_reason": row[3],
                "termination": row[4], "termination_detail": row[5],
                "updated_at": row[6]}

    def fence_work(self, work_key, reason, generation):
        """Fence the work's generation: flag the fence row
        ``termination=pending`` and end any active attempt ``fenced`` —
        the commit *precedes* any termination attempt, so an effect or
        result racing the fence loses (A5/A6 ordering)."""
        with self.transact() as tx:
            return tx.fence_work_tx(work_key, reason, generation)

    def fence_work_tx(self, work_key, reason, generation):
        """The fence-write core, callable inside an enclosing
        ``transact`` — the typed cancel path (Task 2.7) commits its
        control record and the generation fence in ONE durable
        transaction, so the fence can never be lost behind an ack."""
        now = _utcnow()
        self._q(
            "INSERT INTO execution_fence"
            "(work_key,generation,fenced,fence_reason,termination,"
            "updated_at) VALUES(?,?,?,?,'pending',?)"
            " ON CONFLICT(work_key) DO UPDATE SET"
            " fenced=1, fence_reason=excluded.fence_reason,"
            " termination='pending', updated_at=excluded.updated_at",
            (work_key, int(generation), 1, reason, now))
        self._q(
            "UPDATE attempt_ledger SET state='fenced', ended=?"
            " WHERE work_key=? AND state='active'", (now, work_key))
        self.record_event("work_fenced", work_key=work_key,
                          reason=reason)
        return {"outcome": "fenced", "work_key": work_key,
                "termination": "pending"}

    def resolve_fence(self, work_key, termination, detail=None):
        """Record the termination outcome: ``confirmed`` (worker +
        descendants observed dead), ``quarantined`` (uncertain — blocks
        replacement eligibility), or ``resolved`` (operator cleared the
        quarantine)."""
        with self.transact() as tx:
            return tx.resolve_fence_tx(work_key, termination,
                                       detail=detail)

    def resolve_fence_tx(self, work_key, termination, detail=None):
        """The resolve-write core, callable inside an enclosing
        ``transact`` — restart recovery commits the orphan fence, the
        uncertain-termination resolution, the quarantined work state
        and the queue marker in ONE durable transaction, so a crash
        mid-settle can never leave a fenced attempt looking resumable."""
        if termination not in ("confirmed", "quarantined", "resolved"):
            raise IntakeStoreError(
                f"unknown termination state {termination!r}")
        fence = self.fence_state(work_key)
        if fence is None or not fence["fenced"]:
            return {"outcome": "denied", "reason": "not-fenced"}
        self._q(
            "UPDATE execution_fence SET termination=?,"
            " termination_detail=?, updated_at=? WHERE work_key=?",
            (termination, detail, _utcnow(), work_key))
        self.record_event("fence_resolved", work_key=work_key,
                          reason=termination, detail=detail)
        return {"outcome": termination, "work_key": work_key}

    def replacement_eligible(self, work_key):
        """True only when the generation's fence committed *and*
        termination is ``confirmed`` or quarantine ``resolved`` —
        uncertain termination can never permit replacement authority
        (A5/A6)."""
        fence = self.fence_state(work_key)
        if fence is None:
            return False
        return bool(fence["fenced"]) and \
            fence["termination"] in ("confirmed", "resolved")

    # -- usage + alerts ------------------------------------------------------------

    def attempt_record_rows(self, work_key=None):
        rows = self._rows("attempt_records")
        if work_key is None:
            return rows
        return [r for r in rows if r["work_key"] == work_key]

    def count_attempts(self, work_key, role=None):
        rows = self.attempt_record_rows(work_key)
        if role is None:
            return len(rows)
        return len([r for r in rows if r["role"] == role])

    def work_active_seconds(self, work_key):
        """(measured_seconds, unknown_count) across every role attempt —
        unknown usage is counted *as unknown*, never as zero (A4)."""
        rows = [r for r in self._rows("attempt_usage")
                if r["work_key"] == work_key]
        measured = sum(r["active_seconds"] for r in rows
                       if r["active_seconds"] is not None)
        unknown = sum(1 for r in rows if r["active_seconds"] is None)
        return measured, unknown

    def stale_active_attempts(self, max_age_s, now_epoch):
        """Active attempts whose last heartbeat epoch is older than
        ``max_age_s`` — the 60-second missed-liveness sweep input (A6)."""
        rows = self._q(
            "SELECT l.attempt_id, l.work_key, l.last_beat_epoch"
            " FROM attempt_liveness l JOIN attempt_ledger a"
            " ON a.attempt_id=l.attempt_id"
            " WHERE a.state='active' AND l.last_beat_epoch < ?",
            (now_epoch - max_age_s,)).fetchall()
        return [{"attempt_id": r[0], "work_key": r[1],
                 "last_beat_epoch": r[2]} for r in rows]

    def last_heartbeat(self, work_key):
        """Latest heartbeat epoch across the work's attempts (or None)
        — the status surface's "last heartbeat" field (Task 2.7 A2)."""
        row = self._q(
            "SELECT MAX(last_beat_epoch) FROM attempt_liveness l"
            " JOIN attempt_ledger a ON a.attempt_id=l.attempt_id"
            " WHERE l.work_key=?", (work_key,)).fetchone()
        return row[0] if row and row[0] is not None else None

    def emit_alert(self, kind, identity, severity, detail=None):
        """Append one operator-visible alert, deduplicated per
        (kind, identity, severity). A repeated signal reports
        ``deduplicated``; a *severity transition* (medium→high) is a new
        row so the escalation stays visible (§7.3)."""
        with self.transact() as tx:
            cur = tx._q(
                "INSERT OR IGNORE INTO alerts(ts,kind,identity,severity,"
                "detail) VALUES(?,?,?,?,?)",
                (_utcnow(), kind, identity, severity, detail))
            if cur.rowcount:
                return {"outcome": "emitted", "kind": kind,
                        "identity": identity, "severity": severity,
                        "deduplicated": False}
            return {"outcome": "deduplicated", "kind": kind,
                    "identity": identity, "severity": severity,
                    "deduplicated": True}

    def alert_rows(self, kind=None):
        rows = self._rows("alerts")
        if kind is None:
            return rows
        return [r for r in rows if r["kind"] == kind]

    # -- notification outbox (Task 3.4 / §7.1, §7.3, A1) ---------------------
    #
    # One durable row per (kind, identity, severity) signal. The service
    # layer decides *when* to send; the store owns the dedup identity, the
    # retry bookkeeping and the terminal outcome so a process restart can
    # never lose a pending notification and a transport outage can never
    # roll back the committed task/control state it reports on.

    def enqueue_notification(self, notification_id, *, kind, identity,
                             severity, destination_ref, body=None,
                             work_key=None, seq):
        """Persist a pending notification (inside ``transact``).
        Dedup is the ``(kind, identity, severity)`` triple: a repeat of
        the same signal returns ``deduplicated`` with the existing row;
        a severity transition is a distinct row by construction (§7.3).
        """
        cur = self._q(
            "INSERT OR IGNORE INTO notification_outbox"
            "(notification_id,kind,identity,severity,destination_ref,"
            "body,work_key,state,attempts,seq,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,'pending',0,?,?,?)",
            (notification_id, kind, identity, severity,
             destination_ref, body, work_key, int(seq),
             _utcnow(), _utcnow()))
        if cur.rowcount:
            return {"outcome": "queued",
                    "notification_id": notification_id,
                    "deduplicated": False}
        return {"outcome": "deduplicated",
                "notification_id": notification_id,
                "deduplicated": True,
                "row": self.get_notification(notification_id)
                or self.find_notification(kind, identity, severity)}

    def get_notification(self, notification_id):
        row = self._q(
            "SELECT * FROM notification_outbox"
            " WHERE notification_id=?", (notification_id,)).fetchone()
        return self._notification_row(row)

    def find_notification(self, kind, identity, severity):
        row = self._q(
            "SELECT * FROM notification_outbox WHERE kind=? AND"
            " identity=? AND severity=?", (kind, identity, severity)
        ).fetchone()
        return self._notification_row(row)

    def due_notifications(self, now_epoch):
        """Pending rows whose next attempt is due — the restart-safe
        drain set (A1)."""
        return [self._notification_row(r) for r in self._q(
            "SELECT * FROM notification_outbox WHERE state='pending'"
            " AND (next_attempt_epoch IS NULL OR"
            " next_attempt_epoch <= ?) ORDER BY seq",
            (float(now_epoch),)).fetchall()]

    def notification_attempt(self, notification_id, *, now_epoch,
                             ok, error=None, next_attempt_epoch=None,
                             terminal=None):
        """Record one delivery attempt's outcome (inside ``transact``).
        ``ok`` settles the row ``delivered``; ``terminal`` settles it
        ``failed``; otherwise it stays ``pending`` with the next retry
        scheduled. The pending row is *retained* either way — the
        terminal outcome is a column update, never a delete (A1)."""
        row = self.get_notification(notification_id)
        if row is None:
            raise IntakeStoreError(
                f"no notification row {notification_id!r}")
        now_iso = _utcnow()
        first = row["first_attempt_epoch"] \
            if row["first_attempt_epoch"] is not None else now_epoch
        if ok:
            state, term, nxt = "delivered", "delivered", None
        elif terminal:
            state, term, nxt = "failed", terminal, None
        else:
            state, term, nxt = "pending", None, next_attempt_epoch
        self._q(
            "UPDATE notification_outbox SET state=?, attempts=?,"
            " first_attempt_epoch=?, last_attempt_epoch=?,"
            " next_attempt_epoch=?, last_error=?, terminal_outcome=?,"
            " updated_at=? WHERE notification_id=?",
            (state, row["attempts"] + 1, first, now_epoch, nxt,
             error, term, now_iso, notification_id))
        return {"outcome": "attempt-recorded",
                "notification_id": notification_id,
                "state": state, "attempts": row["attempts"] + 1}

    def notification_rows(self, work_key=None, state=None):
        rows = [self._notification_row(r) for r in self._q(
            "SELECT * FROM notification_outbox ORDER BY seq").fetchall()]
        if work_key is not None:
            rows = [r for r in rows if r["work_key"] == work_key]
        if state is not None:
            rows = [r for r in rows if r["state"] == state]
        return rows

    def notification_counts(self, work_key=None):
        """{pending, delivered, failed} tallies — the A2 view field."""
        counts = {"pending": 0, "delivered": 0, "failed": 0}
        for row in self.notification_rows(work_key):
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        return counts

    @staticmethod
    def _notification_row(row):
        if row is None:
            return None
        keys = ("notification_id", "kind", "identity", "severity",
                "destination_ref", "body", "work_key", "state",
                "attempts", "first_attempt_epoch", "last_attempt_epoch",
                "next_attempt_epoch", "last_error", "terminal_outcome",
                "seq", "created_at", "updated_at")
        if isinstance(row, sqlite3.Row):
            row = tuple(row)
        return {k: row[i] for i, k in enumerate(keys)}

    def result_rows(self, attempt_id=None):
        rows = self._rows("results")
        if attempt_id is None:
            return rows
        return [r for r in rows if r["attempt_id"] == attempt_id]

    def link_pr(self, work_key, pr):
        """Set-once PR link — the "at most one linked PR" half of the
        convergence contract (A2). A second distinct link is denied."""
        with self.transact():
            row = self.get_work(work_key)
            if row is None:
                raise IntakeStoreError(f"no work row {work_key!r}")
            if row["linked_pr"] is None:
                self._q(
                    "UPDATE work SET linked_pr=?, updated_at=?"
                    " WHERE work_key=?", (str(pr), _utcnow(), work_key))
                return {"outcome": "linked", "linked_pr": str(pr)}
            if row["linked_pr"] == str(pr):
                return {"outcome": "already-linked", "linked_pr": str(pr)}
            return {"outcome": "denied", "reason": "pr-linked",
                    "linked_pr": row["linked_pr"]}

    # -- publication intents (Task 2.5 / §6.4) ---------------------------------
    #
    # One durable intent row per serialized operation identity, committed
    # *before* the remote effect is sent — the row is the stable identity
    # the post-crash recovery reconciles by (never a blind republish).
    # ``work_key``/``seq`` are NULL on denials that could not resolve a
    # work row (retained as audit evidence, A6).

    def insert_intent(self, intent_id, *, work_key=None, seq=None,
                      task_id=None, generation=None, authority_key=None,
                      repo_id=None, operation, expected_revision=None,
                      target=None, actor_ref=None,
                      claim_expires_epoch=None, state, reason=None,
                      remote_ref=None, detail=None):
        """Write one intent row (inside ``transact``)."""
        now = _utcnow()
        self._q(
            "INSERT INTO publication_intents"
            "(intent_id,work_key,seq,task_id,generation,authority_key,"
            "repo_id,operation,expected_revision,target,actor_ref,"
            "claim_expires_epoch,state,reason,remote_ref,detail,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "?,?,?)",
            (intent_id, work_key, seq, task_id, generation,
             authority_key, repo_id, operation, expected_revision,
             target, actor_ref, claim_expires_epoch, state, reason,
             remote_ref, detail, now, now))

    def get_intent(self, intent_id):
        row = self._q(
            "SELECT * FROM publication_intents WHERE intent_id=?",
            (intent_id,)).fetchone()
        return self._intent_row(row)

    def find_intent(self, work_key, operation, target):
        """The serialized-identity lookup: at most one live intent per
        (work, operation, target) — the index makes racing writers
        converge instead of doubling the remote effect."""
        row = self._q(
            "SELECT * FROM publication_intents WHERE work_key=?"
            " AND operation=? AND target=?"
            " ORDER BY created_at LIMIT 1",
            (work_key, operation, target)).fetchone()
        return self._intent_row(row)

    def update_intent(self, intent_id, *, state=None, reason=None,
                      remote_ref=None, detail=None):
        """Advance an intent's recorded outcome (inside ``transact``)."""
        sets, params = ["updated_at=?"], [_utcnow()]
        for col, val in (("state", state), ("reason", reason),
                         ("remote_ref", remote_ref), ("detail", detail)):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(val)
        params.append(intent_id)
        self._q(
            f"UPDATE publication_intents SET {', '.join(sets)}"
            " WHERE intent_id=?", params)

    def reopen_intent(self, intent_id, *, task_id=None, generation=None,
                      authority_key=None, repo_id=None,
                      expected_revision=None, actor_ref=None,
                      claim_expires_epoch=None):
        """Re-open a terminally ``failed``/``denied`` intent for a fresh
        authorized attempt (inside ``transact``).

        The partial unique index covers *every* state, so a retry cannot
        insert a second row for the same serialized (work, operation,
        target) identity — it re-opens the same one instead: the
        terminal outcome is cleared back to ``recorded`` and the
        attempt-bound fields are refreshed, keeping exactly one remote
        operation per identity while a transient failure never
        permanently tombstones republication (A3)."""
        self._q(
            "UPDATE publication_intents SET state='recorded',"
            " reason=NULL, remote_ref=NULL, detail=NULL,"
            " task_id=?, generation=?, authority_key=?, repo_id=?,"
            " expected_revision=?, actor_ref=?,"
            " claim_expires_epoch=?, updated_at=?"
            " WHERE intent_id=?",
            (task_id, generation, authority_key, repo_id,
             expected_revision, actor_ref, claim_expires_epoch,
             _utcnow(), intent_id))

    def intent_rows(self, work_key=None):
        rows = self._rows("publication_intents")
        if work_key is None:
            return rows
        return [r for r in rows if r["work_key"] == work_key]

    def pending_intents(self):
        """Intents whose remote effect was sent-or-uncertain: committed
        ``recorded`` (crash before the effect returned) or ``applied``
        (crash before read-back linked it) — the recovery set."""
        rows = self._q(
            "SELECT * FROM publication_intents"
            " WHERE state IN ('recorded','applied')"
            " ORDER BY created_at").fetchall()
        return [self._intent_row(r) for r in rows]

    @staticmethod
    def _intent_row(row):
        if row is None:
            return None
        cols = ("intent_id", "work_key", "seq", "task_id", "generation",
                "authority_key", "repo_id", "operation",
                "expected_revision", "target", "actor_ref",
                "claim_expires_epoch", "state", "reason", "remote_ref",
                "detail", "created_at", "updated_at")
        return dict(zip(cols, row))

    # -- control records (Task 2.7 / §6.4 Control, §7.1 control_recorded) ----

    def record_control(self, command_id, *, received_at, committed_at,
                       actor_ref, chat_ref, action, repo_id=None,
                       authority_key=None, issue=None, generation=None,
                       work_key=None, outcome, reason=None, detail=None,
                       seq):
        """Persist one control command + its outcome (inside
        ``transact``). The row commits before the acknowledgement leaves
        — an ack that outlived its record could never exist (A1)."""
        self._q(
            "INSERT INTO control_records"
            "(command_id,received_at,committed_at,actor_ref,chat_ref,"
            "action,repo_id,authority_key,issue,generation,work_key,"
            "outcome,reason,detail,seq) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "?,?)",
            (command_id, received_at, committed_at, actor_ref, chat_ref,
             action, repo_id, authority_key,
             int(issue) if issue is not None else None,
             int(generation) if generation is not None else None,
             work_key, outcome, reason, detail, int(seq)))
        # §7.1 ``control_recorded`` — the typed event twin of the row
        # (Task 2.9 A2): restricted actor/chat references only, never
        # the message text.
        self.record_event(
            "control_recorded", work_key=work_key, reason=reason,
            detail=_json.dumps(
                {"command_id": command_id, "actor_ref": actor_ref,
                 "chat_ref": chat_ref, "action": action,
                 "outcome": outcome,
                 "recorded_at": committed_at}, sort_keys=True))

    def find_control(self, command_id):
        """Dedup lookup — a repeated command ID returns the recorded
        outcome instead of re-running the action (A5)."""
        row = self._q(
            "SELECT outcome, reason, work_key, committed_at, actor_ref,"
            " chat_ref FROM control_records WHERE command_id=?",
            (command_id,)).fetchone()
        if row is None:
            return None
        return {"outcome": row[0], "reason": row[1], "work_key": row[2],
                "committed_at": row[3], "actor_ref": row[4],
                "chat_ref": row[5]}

    def control_rows(self, work_key=None):
        rows = self._rows("control_records")
        if work_key is None:
            return rows
        return [r for r in rows if r["work_key"] == work_key]

    # -- per-work control state (pause boundary) --------------------------------

    def pause_info(self, work_key):
        """``{"pause_state": "none"|"requested"|"paused"|"resumed",
        "paused_stage": …}`` — absent row reads as ``none``."""
        row = self._q(
            "SELECT pause_state, paused_stage, detail, updated_at"
            " FROM work_control WHERE work_key=?", (work_key,)).fetchone()
        if row is None:
            return {"pause_state": "none", "paused_stage": None,
                    "detail": None, "updated_at": None}
        return {"pause_state": row[0], "paused_stage": row[1],
                "detail": row[2], "updated_at": row[3]}

    def set_pause(self, work_key, state, *, stage=None, detail=None):
        """Upsert the pause boundary (inside ``transact`` when grouped).
        ``stage`` records the lane stage the next dispatch resumes at —
        ``None`` keeps any previously recorded stage."""
        if state not in ("none", "requested", "paused", "resumed"):
            raise IntakeStoreError(f"unknown pause state {state!r}")
        self._q(
            "INSERT INTO work_control"
            "(work_key,pause_state,paused_stage,detail,updated_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(work_key) DO UPDATE SET"
            " pause_state=excluded.pause_state,"
            " paused_stage=COALESCE(excluded.paused_stage,"
            "  work_control.paused_stage),"
            " detail=excluded.detail, updated_at=excluded.updated_at",
            (work_key, state, stage, detail, _utcnow()))

    def work_control_rows(self, state=None):
        rows = self._rows("work_control")
        if state is None:
            return rows
        return [r for r in rows if r["pause_state"] == state]

    # -- independent review records (Task 2.6 / F04 A1) -------------------------
    #
    # A review record is candidate evidence from the *separate* reviewer
    # session: it is accepted only when the claimed attempt is a real,
    # finished ``review``-role attempt on this work, the claimed session
    # matches the attempt's own, and that session was never used by an
    # implementation attempt — the concrete check that makes a
    # self-reported implementation review unable to satisfy the
    # independent-review requirement.

    def record_review(self, review_id, *, work_key, attempt_id,
                      session_id, model=None, verdict, findings=None,
                      sha, artifacts=None):
        """Persist the reviewer session's own record — the validation,
        the row and its event commit as ONE durable transaction.
        Returns the acceptance verdict; denials are also durable — a
        rejected record writes a ``review_rejected`` event with the
        reason so a substituted self-report stays visible."""
        with self.transact() as tx:
            return tx._record_review_tx(
                review_id, work_key=work_key, attempt_id=attempt_id,
                session_id=session_id, model=model, verdict=verdict,
                findings=findings, sha=sha, artifacts=artifacts)

    def _record_review_tx(self, review_id, *, work_key, attempt_id,
                          session_id, model=None, verdict,
                          findings=None, sha, artifacts=None):
        attempt = self._q(
            "SELECT work_key, role, session_id, verdict, ended FROM"
            " attempt_records WHERE attempt_id=?",
            (attempt_id,)).fetchone()
        reason = None
        if self.get_work(work_key) is None:
            reason = "unknown-work"
        elif attempt is None or attempt[0] != work_key:
            reason = "unknown-attempt"
        elif attempt[1] != "review":
            reason = "not-review-attempt"
        elif attempt[4] is None:
            reason = "attempt-unfinished"
        elif attempt[2] != session_id:
            reason = "session-mismatch"
        else:
            impl_sessions = {
                r[0] for r in
                self._q("SELECT session_id FROM attempt_records"
                        " WHERE work_key=? AND role='implementation'",
                        (work_key,)).fetchall()}
            if session_id in impl_sessions:
                reason = "not-independent"
            elif self._q(
                    "SELECT review_id FROM review_records"
                    " WHERE attempt_id=?", (attempt_id,)).fetchone():
                reason = "already-recorded"
        if reason is not None:
            self.record_event("review_rejected", work_key=work_key,
                              reason=reason,
                              detail=f"attempt={attempt_id}")
            return {"outcome": "denied", "reason": reason,
                    "review_id": None}
        self._q(
            "INSERT INTO review_records"
            "(review_id,work_key,attempt_id,session_id,role,model,"
            "verdict,findings,sha,artifacts,recorded_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (review_id, work_key, attempt_id, session_id, "review",
             model, verdict,
             _json.dumps(findings, sort_keys=True)
             if findings is not None else None,
             str(sha),
             _json.dumps(artifacts, sort_keys=True)
             if artifacts is not None else None,
             _utcnow()))
        self.record_event(
            "review_recorded", work_key=work_key,
            detail=_json.dumps({"review_id": review_id,
                                "attempt_id": attempt_id,
                                "session_id": session_id,
                                "verdict": verdict, "sha": str(sha)},
                               sort_keys=True))
        return {"outcome": "recorded", "review_id": review_id}

    def review_rows(self, work_key=None, sha=None):
        rows = self._rows("review_records")
        if work_key is not None:
            rows = [r for r in rows if r["work_key"] == work_key]
        if sha is not None:
            rows = [r for r in rows if r["sha"] == str(sha)]
        return rows

    def latest_review(self, work_key, sha=None):
        """The newest review record for the work (optionally bound to one
        revision) — revision-bound evidence must always come from the
        review session that inspected *that* SHA (A5). Ordered by
        ``rowid`` — commit order, never wall-clock ties."""
        if sha is None:
            row = self._q(
                "SELECT * FROM review_records WHERE work_key=?"
                " ORDER BY rowid DESC LIMIT 1", (work_key,)).fetchone()
        else:
            row = self._q(
                "SELECT * FROM review_records WHERE work_key=?"
                " AND sha=? ORDER BY rowid DESC LIMIT 1",
                (work_key, str(sha))).fetchone()
        if row is None:
            return None
        cols = [c[1] for c in self._q("PRAGMA table_info(review_records)")]
        return dict(zip(cols, row))

    # -- verification evidence (Task 2.6 / F04 A2–A6, §6.4 Evidence) -----------
    #
    # One row per observation — never updated after the commit. ``verified``
    # is a timestamped intermediate observation; a later head/base/contract
    # change is recorded by *appending* a new observation, so the trail
    # always shows which SHA was verified when and why the current answer
    # is stale/failed/unknown.

    def record_evidence(self, evidence_id, *, work_key, seq=None,
                        pr_number=None, pr_url=None, head_sha=None,
                        base_name=None, base_sha=None, checks=None,
                        review_id=None, review_session=None,
                        artifacts=None, contract_digest=None,
                        status, reason=None):
        """Append one ``evidence_checked`` observation (inside
        ``transact``). The event row carries the §7.1 fields — PR
        identity, SHA, check/review identities, observation time and
        the passing/stale/unknown reason."""
        now = _utcnow()
        self._q(
            "INSERT INTO verification_evidence"
            "(evidence_id,work_key,seq,pr_number,pr_url,head_sha,"
            "base_name,base_sha,checks,review_id,review_session,"
            "artifacts,contract_digest,status,reason,observed_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (evidence_id, work_key, seq,
             str(pr_number) if pr_number is not None else None, pr_url,
             head_sha, base_name, base_sha,
             _json.dumps(checks, sort_keys=True)
             if checks is not None else None,
             review_id, review_session,
             _json.dumps(artifacts, sort_keys=True)
             if artifacts is not None else None,
             contract_digest, status, reason, now))
        self.record_event(
            "evidence_checked", work_key=work_key, reason=reason,
            detail=_json.dumps(
                {"evidence_id": evidence_id,
                 "pr": str(pr_number) if pr_number is not None else None,
                 "head_sha": head_sha, "base_sha": base_sha,
                 "checks": checks, "review_id": review_id,
                 "review_session": review_session,
                 "status": status, "observed_at": now},
                sort_keys=True))
        return {"outcome": "recorded", "evidence_id": evidence_id,
                "observed_at": now}

    def evidence_rows(self, work_key=None):
        rows = self._rows("verification_evidence")
        if work_key is None:
            return rows
        return [r for r in rows if r["work_key"] == work_key]

    def latest_evidence(self, work_key):
        """The current observation — the *only* row a status surface may
        quote. Ordered by ``rowid`` (commit order): wall-clock ties can
        never reorder two observations (A6)."""
        row = self._q(
            "SELECT * FROM verification_evidence WHERE work_key=?"
            " ORDER BY rowid DESC LIMIT 1", (work_key,)).fetchone()
        if row is None:
            return None
        cols = [c[1] for c in
                self._q("PRAGMA table_info(verification_evidence)")]
        return dict(zip(cols, row))

    def latest_verified_evidence(self, work_key):
        """The newest ``verified`` observation — the anchor drift
        detection compares a fresh read against (A6)."""
        row = self._q(
            "SELECT * FROM verification_evidence WHERE work_key=?"
            " AND status='verified' ORDER BY rowid DESC LIMIT 1",
            (work_key,)).fetchone()
        if row is None:
            return None
        cols = [c[1] for c in
                self._q("PRAGMA table_info(verification_evidence)")]
        return dict(zip(cols, row))

    # -- reconciliation watermark (Task 2.8 / F06, §5.1, §7.3) ------------------

    def reconcile_watermark(self):
        """The singleton reconciliation bookkeeping row, or ``None``
        before the first pass runs."""
        row = self._q(
            "SELECT last_attempt_epoch,last_success_epoch,"
            "consecutive_failures,remote_state,stale_since_epoch,"
            "next_due_epoch,updated_at FROM reconcile_watermark"
            " WHERE id=1").fetchone()
        if row is None:
            return None
        return {"last_attempt_epoch": row[0],
                "last_success_epoch": row[1],
                "consecutive_failures": row[2],
                "remote_state": row[3],
                "stale_since_epoch": row[4],
                "next_due_epoch": row[5],
                "updated_at": row[6]}

    def update_reconcile_watermark(self, **fields):
        """Upsert the singleton watermark row (inside ``transact``).

        Only the named columns may be written — this is controller
        bookkeeping, not a caller-influenced field bag."""
        allowed = {"last_attempt_epoch", "last_success_epoch",
                   "consecutive_failures", "remote_state",
                   "stale_since_epoch", "next_due_epoch"}
        unknown = set(fields) - allowed
        if unknown:
            raise IntakeStoreError(
                f"unknown watermark fields {sorted(unknown)}")
        self._q(
            "INSERT OR IGNORE INTO reconcile_watermark"
            "(id,updated_at) VALUES(1,?)", (_utcnow(),))
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in sorted(fields))
        self._q(
            f"UPDATE reconcile_watermark SET {sets}, updated_at=?"
            " WHERE id=1",
            [fields[k] for k in sorted(fields)] + [_utcnow()])

    # -- events (§7.1) ---------------------------------------------------------

    def record_event(self, kind, *, delivery_id=None, work_key=None,
                     reason=None, config_digest=None, detail=None,
                     supersedes=None):
        """Append one intake event; returns the row's ``seq``.

        Carries identities, reason codes and digests only — bodies,
        signatures and secrets never reach this table by construction
        (callers pass reason codes, not content). ``supersedes`` points
        a correction at the earlier row's ``seq`` — the superseded row
        is kept verbatim, so history is never rewritten (Task 2.9 A1).
        """
        cur = self._q(
            "INSERT INTO events(ts,kind,delivery_id,work_key,reason,"
            "config_digest,detail,supersedes) VALUES(?,?,?,?,?,?,?,?)",
            (_utcnow(), kind, delivery_id, work_key, reason,
             config_digest, detail, supersedes))
        return cur.lastrowid

    def record_typed_event(self, kind, *, properties=None, work_key=None,
                           delivery_id=None, reason=None,
                           config_digest=None, supersedes=None):
        """Append one event validated against the §7.1 contract.

        ``properties`` are merged over the column identities and checked
        against :data:`factory_kit.events.schema.EVENT_SCHEMAS` — an
        unregistered kind or a missing required property raises
        :class:`IntakeStoreError` (a producer defect, not a verdict).
        Returns ``{"outcome": "recorded", "seq": …}``.
        """
        props = dict(properties or {})
        merged = {"work_key": work_key, "delivery_id": delivery_id,
                  "reason": reason, "config_digest": config_digest}
        merged.update(props)
        check = _events_schema.validate_event(kind, merged)
        if not check["ok"]:
            raise IntakeStoreError(
                f"event {kind!r} rejected: {check['reason']}"
                + (f" missing={check['missing']}"
                   if check.get("missing") else ""))
        seq = self.record_event(
            kind, delivery_id=delivery_id, work_key=work_key,
            reason=reason, config_digest=config_digest,
            detail=_json.dumps(props, sort_keys=True)
            if props else None,
            supersedes=supersedes)
        return {"outcome": "recorded", "seq": seq}

    def event_superseded(self, seq):
        """True when a later event's ``supersedes`` names ``seq`` —
        the correction exists alongside the original, which stays in
        the trail unrewritten (A1)."""
        row = self._q(
            "SELECT 1 FROM events WHERE supersedes=? LIMIT 1",
            (seq,)).fetchone()
        return row is not None

    def current_events(self, kind=None):
        """Events minus rows a later correction superseded — the view a
        status/diagnostic surface must quote (A1)."""
        rows = self.event_rows(kind)
        superseded = {r["supersedes"] for r in self.event_rows()
                      if r.get("supersedes") is not None}
        return [r for r in rows if r["seq"] not in superseded]

    # -- operator effort (Task 2.9 / §7.1 operator_effort_recorded, F07 A2) --

    def record_operator_effort(self, work_key, *, actor_ref,
                               active_minutes=None, category=None,
                               source="operator", supersedes=None,
                               effort_id=None):
        """Persist one operator-effort record + its event, one commit.

        ``active_minutes`` is the *operator-supplied* active time —
        ``None`` records honest *unknown*, never zero (A2/A3).
        ``category`` is the fixed intervention vocabulary
        (:data:`factory_kit.events.schema.INTERVENTION_CATEGORIES`) so
        no free text — and with it no private content — can enter the
        trail. ``supersedes`` names the earlier ``effort_id`` this
        record corrects: the new row *and* a new
        ``operator_effort_recorded`` event pointing at the prior
        event's seq are appended; history is never rewritten (A1).
        """
        if not work_key:
            raise IntakeStoreError(
                "operator effort requires a work_key")
        if not actor_ref:
            raise IntakeStoreError(
                "operator effort requires a restricted actor_ref")
        if active_minutes is not None:
            try:
                active_minutes = float(active_minutes)
            except (TypeError, ValueError) as exc:
                raise IntakeStoreError(
                    f"active_minutes must be a number or None: {exc}"
                ) from exc
            if active_minutes < 0:
                raise IntakeStoreError(
                    "active_minutes cannot be negative")
        if category is not None and category not in \
                _events_schema.INTERVENTION_CATEGORIES:
            raise IntakeStoreError(
                f"unknown intervention category {category!r}")
        effort_id = effort_id or f"eff-{uuid.uuid4().hex[:12]}"
        now = _utcnow()
        with self.transact() as tx:
            prior_seq = None
            if supersedes is not None:
                row = tx._q(
                    "SELECT event_seq FROM operator_effort"
                    " WHERE effort_id=?", (supersedes,)).fetchone()
                if row is None:
                    raise IntakeStoreError(
                        f"no operator effort row {supersedes!r}")
                prior_seq = row[0]
            seq = tx.next_seq()
            event_seq = tx.record_event(
                "operator_effort_recorded", work_key=work_key,
                detail=_json.dumps(
                    {"effort_id": effort_id, "work_key": work_key,
                     "actor_ref": actor_ref,
                     "active_minutes": active_minutes,
                     "category": category, "source": source,
                     "supersedes": supersedes, "recorded_at": now},
                    sort_keys=True),
                supersedes=prior_seq)
            tx._q(
                "INSERT INTO operator_effort"
                "(effort_id,work_key,actor_ref,active_minutes,category,"
                "source,supersedes,seq,event_seq,recorded_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (effort_id, work_key, actor_ref, active_minutes,
                 category, source, supersedes, seq, event_seq, now))
            return {"outcome": "recorded", "effort_id": effort_id,
                    "event_seq": event_seq, "recorded_at": now}

    def effort_rows(self, work_key=None, *, current_only=False):
        rows = self._rows("operator_effort")
        if work_key is not None:
            rows = [r for r in rows if r["work_key"] == work_key]
        if current_only:
            superseded = {r["supersedes"] for r in rows
                          if r.get("supersedes") is not None}
            rows = [r for r in rows
                    if r["effort_id"] not in superseded]
        return rows

    def work_effort_minutes(self, work_key):
        """(measured_minutes, unknown_count) over the *current* effort
        records — superseded rows stay in the table but do not count,
        and unknown effort is counted as unknown, never zero (A2/A4)."""
        rows = self.effort_rows(work_key, current_only=True)
        measured = sum(r["active_minutes"] for r in rows
                       if r["active_minutes"] is not None)
        unknown = sum(1 for r in rows if r["active_minutes"] is None)
        return measured, unknown

    # -- participant agreement (Task 2.9 / A6) -------------------------------

    def record_participant_agreement(self, scope, *, actor_ref,
                                     detail=None):
        """Record participant agreement for an export ``scope`` (e.g.
        ``"pilot-export"``). The row is the durable proof an aggregate
        export checks — it can never be enabled silently (A6)."""
        if not scope:
            raise IntakeStoreError(
                "participant agreement requires a scope")
        if not actor_ref:
            raise IntakeStoreError(
                "participant agreement requires a restricted actor_ref")
        now = _utcnow()
        with self.transact() as tx:
            seq = tx.next_seq()
            tx._q(
                "INSERT INTO participant_agreements"
                "(scope,actor_ref,detail,seq,recorded_at)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(scope) DO UPDATE SET"
                " actor_ref=excluded.actor_ref,"
                " detail=excluded.detail, seq=excluded.seq,"
                " recorded_at=excluded.recorded_at",
                (scope, actor_ref, detail, seq, now))
            tx.record_event(
                "participant_agreement_recorded",
                detail=_json.dumps(
                    {"scope": scope, "actor_ref": actor_ref,
                     "recorded_at": now}, sort_keys=True))
            return {"outcome": "recorded", "scope": scope,
                    "recorded_at": now}

    def participant_agreement(self, scope="pilot-export"):
        """The recorded agreement row for ``scope``, or ``None`` — the
        gate an aggregate pilot export must pass (A6)."""
        row = self._q(
            "SELECT scope,actor_ref,detail,seq,recorded_at"
            " FROM participant_agreements WHERE scope=?",
            (scope,)).fetchone()
        if row is None:
            return None
        return {"scope": row[0], "actor_ref": row[1], "detail": row[2],
                "seq": row[3], "recorded_at": row[4]}

    # -- preview records (Task 3.1 / F11, §6.4 Preview evidence) -------------
    #
    # One durable row per preview deployment, committed *before* the
    # provider effect — the crash window between "deployment created"
    # and "response recorded" reconciles by identity on recovery, never
    # by blind redeploy. ``state`` vocabulary:
    #
    # - ``deploying`` — committed intent; provider call in flight or
    #   its outcome lost (recovery reconciles by identity).
    # - ``deployed`` — provider returned a deployment; authoritative
    #   read-back + smoke not yet observed.
    # - ``verified`` — read-back corroborated the bound revision and
    #   the configured smoke contract passed; the approval-request
    #   evidence (a point-in-time record — never merge authority).
    # - ``failed`` — a denial condition was observed (smoke failed,
    #   provider outage, identity mismatch, lost deploy outcome).
    # - ``invalidated`` — superseded by revision/base movement,
    #   cancellation/fencing, supersession or expiry.
    # - ``cleanup-pending`` — the owned deployment's removal is required
    #   but unconfirmed (provider unreachable or removal unacknowledged):
    #   the visible backlog, never claimed removed.
    # - ``removed`` — the provider confirmed removal (``removed_at``
    #   set); or ``denied`` — rejected before any provider effect
    #   (audit row only, no deployment identity).

    _PREVIEW_COLS = (
        "preview_id", "work_key", "seq", "task_id", "generation",
        "provider", "deployment_id", "artifact_identity", "head_sha",
        "base_name", "base_sha", "url", "visibility", "environment",
        "smoke_command", "smoke_expected", "smoke_observed", "state",
        "reason", "cleanup_owner", "expires_epoch",
        "cleanup_deadline_epoch", "observed_at", "removed_at", "detail",
        "created_at", "updated_at")

    #: Preview states that still hold the task's one active slot.
    PREVIEW_ACTIVE_STATES = ("deploying", "deployed", "verified")

    #: Preview states whose owned deployment may still exist remotely —
    #: the cleanup sweep's retry set (``removed``/``denied`` are done).
    PREVIEW_CLEANUP_STATES = ("cleanup-pending",)

    def insert_preview(self, preview_id, *, work_key, seq=None,
                       task_id=None, generation=None, provider=None,
                       deployment_id=None, artifact_identity=None,
                       head_sha=None, base_name=None, base_sha=None,
                       url=None, visibility=None, environment=None,
                       smoke_command=None, smoke_expected=None,
                       smoke_observed=None, state, reason=None,
                       cleanup_owner=None, expires_epoch=None,
                       cleanup_deadline_epoch=None, observed_at=None,
                       removed_at=None, detail=None):
        """Write one preview record (inside ``transact``)."""
        now = _utcnow()
        self._q(
            "INSERT INTO preview_records"
            "(preview_id,work_key,seq,task_id,generation,provider,"
            "deployment_id,artifact_identity,head_sha,base_name,"
            "base_sha,url,visibility,environment,smoke_command,"
            "smoke_expected,smoke_observed,state,reason,cleanup_owner,"
            "expires_epoch,cleanup_deadline_epoch,observed_at,"
            "removed_at,detail,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (preview_id, work_key, seq, task_id, generation, provider,
             deployment_id, artifact_identity, head_sha, base_name,
             base_sha, url, visibility, environment, smoke_command,
             smoke_expected, smoke_observed, state, reason,
             cleanup_owner, expires_epoch, cleanup_deadline_epoch,
             observed_at, removed_at, detail, now, now))

    def update_preview(self, preview_id, **fields):
        """Advance a preview record's outcome (inside ``transact``).

        Only named columns may be written — this is the controller's
        evidence surface, not a caller-influenced field bag."""
        allowed = {"seq", "task_id", "provider", "deployment_id",
                   "artifact_identity", "head_sha", "base_name",
                   "base_sha", "url", "visibility", "environment",
                   "smoke_command", "smoke_expected", "smoke_observed",
                   "state", "reason", "cleanup_owner", "expires_epoch",
                   "cleanup_deadline_epoch", "observed_at", "removed_at",
                   "detail"}
        unknown = set(fields) - allowed
        if unknown:
            raise IntakeStoreError(
                f"unknown preview fields {sorted(unknown)}")
        sets, params = ["updated_at=?"], [_utcnow()]
        for col in sorted(fields):
            sets.append(f"{col}=?")
            params.append(fields[col])
        params.append(preview_id)
        self._q(
            f"UPDATE preview_records SET {', '.join(sets)}"
            " WHERE preview_id=?", params)

    def get_preview(self, preview_id):
        row = self._q(
            "SELECT * FROM preview_records WHERE preview_id=?",
            (preview_id,)).fetchone()
        return self._preview_row(row)

    def find_preview_by_deployment(self, deployment_id):
        """The durable record for a provider deployment id — the lookup
        a deployment callback binds against (A4). ``None`` means the id
        is not one this factory recorded: an unknown artifact identity
        is ignored, never adopted."""
        if not deployment_id:
            return None
        row = self._q(
            "SELECT * FROM preview_records WHERE deployment_id=?"
            " ORDER BY rowid DESC LIMIT 1", (deployment_id,)).fetchone()
        return self._preview_row(row)

    def latest_preview(self, work_key):
        """The newest preview record for the work — ordered by
        ``rowid`` (commit order), never wall-clock ties."""
        row = self._q(
            "SELECT * FROM preview_records WHERE work_key=?"
            " ORDER BY rowid DESC LIMIT 1", (work_key,)).fetchone()
        return self._preview_row(row)

    def active_preview(self, work_key):
        """The row currently holding the task's one active preview
        slot, or ``None`` — enforced by the partial unique index so a
        racing second deploy can never mint a second live preview."""
        row = self._q(
            "SELECT * FROM preview_records WHERE work_key=?"
            " AND state IN ('deploying','deployed','verified')"
            " ORDER BY rowid DESC LIMIT 1", (work_key,)).fetchone()
        return self._preview_row(row)

    def preview_rows(self, work_key=None, state=None):
        rows = self._rows("preview_records")
        if work_key is not None:
            rows = [r for r in rows if r["work_key"] == work_key]
        if state is not None:
            rows = [r for r in rows if r["state"] == state]
        return rows

    def previews_expired(self, now_epoch):
        """Active previews whose recorded TTL elapsed — the expiry
        sweep's input (A5)."""
        rows = self._q(
            "SELECT * FROM preview_records"
            " WHERE state IN ('deploying','deployed','verified')"
            " AND expires_epoch IS NOT NULL AND expires_epoch < ?",
            (now_epoch,)).fetchall()
        return [self._preview_row(r) for r in rows]

    def preview_cleanup_backlog(self):
        """Owned deployments whose removal is required but unconfirmed
        — the visible backlog a provider outage leaves (A5)."""
        rows = self._q(
            "SELECT * FROM preview_records WHERE state='cleanup-pending'"
            " ORDER BY created_at").fetchall()
        return [self._preview_row(r) for r in rows]

    def previews_deploying(self):
        """Records still between committed intent and confirmed
        provider outcome — the crash-window set recovery reconciles
        by identity (A5)."""
        rows = self._q(
            "SELECT * FROM preview_records WHERE state='deploying'"
            " ORDER BY created_at").fetchall()
        return [self._preview_row(r) for r in rows]

    @classmethod
    def _preview_row(cls, row):
        if row is None:
            return None
        return dict(zip(cls._PREVIEW_COLS, row))

    # -- one-use approval authority (Task 3.2 / F12, §6.4 Approval) -----
    #
    # ``approval_requests`` holds the issued one-use grant; the partial
    # unique index makes one live (awaiting/approved) request per work
    # un-raceable. ``approval_decisions`` is the append-only decision
    # history — accepted, rejected and every denied attempt — carrying
    # restricted actor/chat references and the request's bound
    # action/target verbatim so a decision can never broaden the
    # request's scope (A6).

    _APPROVAL_REQUEST_COLS = (
        "request_id", "work_key", "seq", "task_id", "generation",
        "authority_key", "repo_id", "action", "target", "pr_number",
        "pr_url", "head_sha", "base_name", "base_sha", "evidence_id",
        "preview_id", "evidence_digest", "preview_digest",
        "config_digest", "policy_digest", "merge_method", "state",
        "reason", "decision_id", "actor_ref", "issued_at",
        "expires_epoch", "consumed_seq", "detail", "created_at",
        "updated_at")

    _APPROVAL_DECISION_COLS = (
        "decision_id", "request_id", "work_key", "seq", "command_id",
        "actor_ref", "chat_ref", "action", "target", "verdict",
        "outcome", "reason", "state", "received_at", "decided_at",
        "detail")

    #: Request states that still hold the work's one live slot.
    APPROVAL_LIVE_STATES = ("awaiting", "approved")

    def insert_approval_request(self, request_id, *, work_key, seq=None,
                                task_id=None, generation=None,
                                authority_key=None, repo_id=None,
                                action, target, pr_number=None,
                                pr_url=None, head_sha=None,
                                base_name=None, base_sha=None,
                                evidence_id=None, preview_id=None,
                                evidence_digest=None,
                                preview_digest=None, config_digest=None,
                                policy_digest=None, merge_method=None,
                                state, reason=None, decision_id=None,
                                actor_ref=None, issued_at,
                                expires_epoch, consumed_seq=None,
                                detail=None):
        """Write one approval request row (inside ``transact``). The
        row must commit *before* the request is presented anywhere —
        a prompt that outlived its durable record could never exist
        (A1)."""
        now = _utcnow()
        self._q(
            "INSERT INTO approval_requests"
            "(request_id,work_key,seq,task_id,generation,authority_key,"
            "repo_id,action,target,pr_number,pr_url,head_sha,base_name,"
            "base_sha,evidence_id,preview_id,evidence_digest,"
            "preview_digest,config_digest,policy_digest,merge_method,"
            "state,reason,decision_id,actor_ref,issued_at,"
            "expires_epoch,consumed_seq,detail,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "?,?,?,?,?)",
            (request_id, work_key, seq, task_id, generation,
             authority_key, repo_id, action, target,
             str(pr_number) if pr_number is not None else None, pr_url,
             head_sha, base_name, base_sha, evidence_id, preview_id,
             evidence_digest, preview_digest, config_digest,
             policy_digest, merge_method, state, reason, decision_id,
             actor_ref, issued_at, expires_epoch, consumed_seq, detail,
             now, now))

    def update_approval_request(self, request_id, **fields):
        """Advance a request's outcome (inside ``transact``). Only
        named columns may be written — the bound evidence surface is
        fixed at issuance so a decision can never broaden action,
        scope, revision or expiry (A6)."""
        allowed = {"state", "reason", "decision_id", "actor_ref",
                   "consumed_seq", "detail"}
        unknown = set(fields) - allowed
        if unknown:
            raise IntakeStoreError(
                f"unknown approval-request fields {sorted(unknown)}")
        sets, params = ["updated_at=?"], [_utcnow()]
        for col in sorted(fields):
            sets.append(f"{col}=?")
            params.append(fields[col])
        params.append(request_id)
        self._q(
            f"UPDATE approval_requests SET {', '.join(sets)}"
            " WHERE request_id=?", params)

    def get_approval_request(self, request_id):
        row = self._q(
            "SELECT * FROM approval_requests WHERE request_id=?",
            (request_id,)).fetchone()
        return self._approval_request_row(row)

    def live_approval_request(self, work_key):
        """The request currently holding the work's one live
        (awaiting/approved) slot, or ``None`` — enforced by the
        partial unique index so a racing second request can never
        mint a second live grant."""
        row = self._q(
            "SELECT * FROM approval_requests WHERE work_key=?"
            " AND state IN ('awaiting','approved')"
            " ORDER BY rowid DESC LIMIT 1", (work_key,)).fetchone()
        return self._approval_request_row(row)

    def live_approval_requests(self, work_key=None):
        """Every live request (the sweep's input); ``work_key``
        narrows to one work."""
        if work_key is None:
            rows = self._q(
                "SELECT * FROM approval_requests"
                " WHERE state IN ('awaiting','approved')"
                " ORDER BY created_at").fetchall()
        else:
            rows = self._q(
                "SELECT * FROM approval_requests WHERE work_key=?"
                " AND state IN ('awaiting','approved')"
                " ORDER BY created_at", (work_key,)).fetchall()
        return [self._approval_request_row(r) for r in rows]

    def approval_request_rows(self, work_key=None, state=None):
        rows = self._rows("approval_requests")
        if work_key is not None:
            rows = [r for r in rows if r["work_key"] == work_key]
        if state is not None:
            rows = [r for r in rows if r["state"] == state]
        return rows

    def insert_approval_decision(self, decision_id, *, request_id,
                                 work_key=None, seq=None, command_id=None,
                                 actor_ref, chat_ref=None, action,
                                 target, verdict=None, outcome,
                                 reason=None, state, received_at,
                                 decided_at=None, detail=None):
        """Append one decision row + its ``approval_decided`` typed
        event twin (inside ``transact``) — the §7.1 record of the
        durable decision ID, restricted actor, action, target and
        receipt/outcome (A2/A3)."""
        self._q(
            "INSERT INTO approval_decisions"
            "(decision_id,request_id,work_key,seq,command_id,actor_ref,"
            "chat_ref,action,target,verdict,outcome,reason,state,"
            "received_at,decided_at,detail) VALUES(?,?,?,?,?,?,?,?,?,?,"
            "?,?,?,?,?,?)",
            (decision_id, request_id, work_key, seq, command_id,
             actor_ref, chat_ref, action, target, verdict, outcome,
             reason, state, received_at, decided_at or _utcnow(),
             detail))
        self.record_typed_event(
            "approval_decided", work_key=work_key, reason=reason,
            properties={"request_id": request_id,
                        "decision_id": decision_id,
                        "actor_ref": actor_ref,
                        "chat_ref": chat_ref,
                        "action": action, "target": target,
                        "verdict": verdict, "outcome": outcome,
                        "decided_at": decided_at or _utcnow()})

    def update_approval_decision(self, decision_id, **fields):
        """Advance the winning decision's consumed/revoked lifecycle
        (inside ``transact``). ``outcome``/``verdict``/``action``/
        ``target`` are immutable — history is append-only (A6)."""
        allowed = {"state", "reason", "detail"}
        unknown = set(fields) - allowed
        if unknown:
            raise IntakeStoreError(
                f"unknown approval-decision fields {sorted(unknown)}")
        sets, params = [], []
        for col in sorted(fields):
            sets.append(f"{col}=?")
            params.append(fields[col])
        params.append(decision_id)
        self._q(
            f"UPDATE approval_decisions SET {', '.join(sets)}"
            " WHERE decision_id=?", params)

    def get_approval_decision(self, decision_id):
        row = self._q(
            "SELECT * FROM approval_decisions WHERE decision_id=?",
            (decision_id,)).fetchone()
        return self._approval_decision_row(row)

    def find_decision_by_command(self, command_id):
        """Dedup lookup — a repeated typed command returns the
        recorded decision instead of re-deciding (A4)."""
        if not command_id:
            return None
        row = self._q(
            "SELECT * FROM approval_decisions WHERE command_id=?"
            " ORDER BY rowid DESC LIMIT 1", (command_id,)).fetchone()
        return self._approval_decision_row(row)

    def approval_decision_rows(self, request_id=None, work_key=None):
        rows = self._rows("approval_decisions")
        if request_id is not None:
            rows = [r for r in rows if r["request_id"] == request_id]
        if work_key is not None:
            rows = [r for r in rows if r["work_key"] == work_key]
        return rows

    def invalidate_approvals(self, work_key, reason):
        """Own-transact wrapper: invalidate every live request the
        work holds (control/preview/verification callers that carry
        no enclosing write boundary)."""
        with self.transact() as tx:
            return tx.invalidate_approvals_tx(work_key, reason)

    def invalidate_approvals_tx(self, work_key, reason):
        """Retire every live request for ``work_key`` — the A5 move:
        cancel/pause/fence callers run this inside their own commit so
        the task transition and the authority loss are atomic (a task
        that moved can never leave a live grant behind)."""
        out = []
        for req in self.live_approval_requests(work_key):
            out.append(self.invalidate_approval_tx(req, reason))
        return out

    def invalidate_approval_tx(self, req, reason,
                              state="invalidated"):
        """One terminal transition on a live request: the row's state
        + reason, the winning decision's ``revoked`` mark and the
        ``approval_invalidated`` event — all inside the caller's
        commit (inside ``transact``)."""
        self.update_approval_request(req["request_id"], state=state,
                                     reason=reason)
        if req.get("decision_id"):
            self.update_approval_decision(req["decision_id"],
                                          state="revoked")
        self.record_typed_event(
            "approval_invalidated", work_key=req["work_key"],
            reason=reason,
            properties={"request_id": req["request_id"],
                        "decision_id": req.get("decision_id"),
                        "prior_state": req["state"],
                        "action": req["action"],
                        "target": req["target"],
                        "head_sha": req.get("head_sha"),
                        "base_sha": req.get("base_sha"),
                        "invalidated_at": _utcnow()})
        return req["request_id"]

    @classmethod
    def _approval_request_row(cls, row):
        if row is None:
            return None
        return dict(zip(cls._APPROVAL_REQUEST_COLS, row))

    @classmethod
    def _approval_decision_row(cls, row):
        if row is None:
            return None
        return dict(zip(cls._APPROVAL_DECISION_COLS, row))

    # -- guarded merge intents (Task 3.3 / F12, §6.4 Merge outcome) --------
    #
    # ``merge_intents`` is the single merge owner's serialized identity: the
    # durable row commits inside the same ``transact`` that consumes the
    # one-use approval, *before* any remote merge call. The partial unique
    # index on live states makes concurrent invocations un-raceable — a
    # second merge call converges on the committed row instead of minting a
    # second intent (A3). Terminal outcome rows carry the authoritative
    # merged flag, actual merge commit SHA, the remote-recorded merge actor
    # and the read-back time (A6).

    _MERGE_INTENT_COLS = (
        "intent_id", "work_key", "seq", "task_id", "generation",
        "authority_key", "repo_id", "request_id", "decision_id",
        "pr_number", "expected_head", "expected_base_name",
        "expected_base_sha", "merge_method", "actor_ref", "state",
        "reason", "merged", "merge_sha", "merged_by",
        "merge_actor_kind", "expires_epoch", "sent_at", "read_back_at",
        "detail", "created_at", "updated_at")

    #: Intent states that still hold the work's one live merge slot —
    #: ``recorded`` (pre-effect or crash window) and ``parked`` (unknown
    #: outcome awaiting reconciliation). Terminal states never block a
    #: fresh approval's intent.
    MERGE_LIVE_STATES = ("recorded", "parked")

    def insert_merge_intent(self, intent_id, *, work_key, seq=None,
                            task_id=None, generation=None,
                            authority_key=None, repo_id=None,
                            request_id, decision_id=None, pr_number=None,
                            expected_head=None, expected_base_name=None,
                            expected_base_sha=None, merge_method=None,
                            actor_ref=None, state, reason=None,
                            merged=None, merge_sha=None, merged_by=None,
                            merge_actor_kind=None, expires_epoch=None,
                            sent_at=None, read_back_at=None, detail=None):
        """Write one merge intent row (inside ``transact``). The row must
        commit in the same transaction that consumes the approval — an
        intent without its spent grant, or a spent grant without its
        intent, is the torn state the atomic spend exists to prevent
        (A3)."""
        now = _utcnow()
        self._q(
            "INSERT INTO merge_intents"
            "(intent_id,work_key,seq,task_id,generation,authority_key,"
            "repo_id,request_id,decision_id,pr_number,expected_head,"
            "expected_base_name,expected_base_sha,merge_method,actor_ref,"
            "state,reason,merged,merge_sha,merged_by,merge_actor_kind,"
            "expires_epoch,sent_at,read_back_at,detail,created_at,"
            "updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            "?)",
            (intent_id, work_key, seq, task_id, generation,
             authority_key, repo_id, request_id, decision_id,
             str(pr_number) if pr_number is not None else None,
             expected_head, expected_base_name, expected_base_sha,
             merge_method, actor_ref, state, reason, merged, merge_sha,
             merged_by, merge_actor_kind, expires_epoch, sent_at,
             read_back_at, detail, now, now))

    def update_merge_intent(self, intent_id, **fields):
        """Advance a merge intent's outcome (inside ``transact``). Only
        named columns may be written — the bound identity (request,
        decision, expected head/base, method) is fixed at creation so an
        outcome can never rebind the intent to a different grant or
        revision (A6)."""
        allowed = {"seq", "state", "reason", "merged", "merge_sha",
                   "merged_by", "merge_actor_kind", "sent_at",
                   "read_back_at", "detail"}
        unknown = set(fields) - allowed
        if unknown:
            raise IntakeStoreError(
                f"unknown merge-intent fields {sorted(unknown)}")
        sets, params = ["updated_at=?"], [_utcnow()]
        for col in sorted(fields):
            sets.append(f"{col}=?")
            params.append(fields[col])
        params.append(intent_id)
        self._q(
            f"UPDATE merge_intents SET {', '.join(sets)}"
            " WHERE intent_id=?", params)

    def get_merge_intent(self, intent_id):
        row = self._q(
            "SELECT * FROM merge_intents WHERE intent_id=?",
            (intent_id,)).fetchone()
        return self._merge_intent_row(row)

    def merge_intent_for_request(self, request_id):
        """The intent a consumed approval minted, or ``None`` — the
        one-use grant's single spend (A3)."""
        if not request_id:
            return None
        row = self._q(
            "SELECT * FROM merge_intents WHERE request_id=?",
            (request_id,)).fetchone()
        return self._merge_intent_row(row)

    def live_merge_intent(self, work_key):
        """The intent currently holding the work's one live merge slot
        (``recorded``/``parked``), or ``None`` — enforced by the partial
        unique index so a racing second merge can never mint a second
        live intent."""
        row = self._q(
            "SELECT * FROM merge_intents WHERE work_key=?"
            " AND state IN ('recorded','parked')"
            " ORDER BY rowid DESC LIMIT 1", (work_key,)).fetchone()
        return self._merge_intent_row(row)

    def merge_intent_rows(self, work_key=None):
        rows = self._rows("merge_intents")
        if work_key is not None:
            rows = [r for r in rows if r["work_key"] == work_key]
        return rows

    def pending_merge_intents(self):
        """Intents between committed spend and authoritative outcome —
        ``recorded`` (the crash window) and ``parked`` (unknown
        outcome): the set recovery/reconciliation resolves by remote
        read-back, never by blind resend (A6/A7)."""
        rows = self._q(
            "SELECT * FROM merge_intents"
            " WHERE state IN ('recorded','parked')"
            " ORDER BY created_at").fetchall()
        return [self._merge_intent_row(r) for r in rows]

    @classmethod
    def _merge_intent_row(cls, row):
        if row is None:
            return None
        return dict(zip(cls._MERGE_INTENT_COLS, row))

    # -- read accessors (inspectability + tests) --------------------------------

    def _rows(self, table):
        cols = [c[1] for c in self._q(f"PRAGMA table_info({table})")]
        return [dict(zip(cols, r))
                for r in self._q(f"SELECT * FROM {table}")]

    def work_rows(self):
        return self._rows("work")

    def delivery_rows(self):
        return self._rows("deliveries")

    def event_rows(self, kind=None):
        rows = self._rows("events")
        if kind is None:
            return rows
        return [r for r in rows if r["kind"] == kind]

    def attempt_rows(self):
        return self._rows("attempt_ledger")

    def counts(self):
        """Convergence counters for the burst/restart assertions."""
        return {
            "work": self._q("SELECT COUNT(*) FROM work").fetchone()[0],
            "deliveries":
                self._q("SELECT COUNT(*) FROM deliveries").fetchone()[0],
            "events": self._q("SELECT COUNT(*) FROM events").fetchone()[0],
            "active_attempts": self._q(
                "SELECT COUNT(*) FROM attempt_ledger WHERE state='active'"
            ).fetchone()[0],
        }

    # -- repo-scoped history lifecycle (Task 3.5 / F08 A4) -------------------
    #
    # The store may hold several repo identities; removal of one
    # registration touches only rows bound to *its* authority key.
    # ``work_key`` embeds the authority key (``{authority}#{issue}:g{n}``)
    # so every dependent table scopes by its ``work_key`` column; the
    # exceptions below carry their own identity column
    # (``control_records.authority_key``, ``alerts.identity`` = work_key,
    # ``publication_intents.authority_key``).
    #
    # Deliberately out of scope — shared/global rows a single
    # registration never owned: ``meta`` (the sequence), ``lane_state``
    # rows for lanes never occupied by this repo, ``reconcile_watermark``
    # (store-global), ``participant_agreements`` (consent outlives any
    # registration), and denied-delivery ``events`` whose delivery_id was
    # never bound to a work row (they carry no repo binding).

    def work_rows_for_authority(self, authority_key):
        """Every work row minted under one repo identity."""
        return [r for r in self.work_rows()
                if r["authority_key"] == authority_key]

    def _history_scope(self, authority_key):
        """(work_keys, attempt_ids, delivery_ids) for one identity —
        the shared scoping export and delete agree on."""
        work_keys = {r["work_key"] for r in
                     self.work_rows_for_authority(authority_key)}
        attempt_ids = {r["attempt_id"]
                       for r in self._rows("attempt_ledger")
                       if r["work_key"] in work_keys}
        attempt_ids |= {r["attempt_id"]
                        for r in self._rows("attempt_records")
                        if r["work_key"] in work_keys}
        attempt_ids |= {r["attempt_id"]
                        for r in self._rows("attempt_liveness")
                        if r["work_key"] in work_keys}
        delivery_ids = {r["delivery_id"]
                        for r in self._rows("deliveries")
                        if r["work_key"] in work_keys}
        return work_keys, attempt_ids, delivery_ids

    #: table -> scoping column for the repo-scoped history tables.
    #: ``"work_key"`` covers the common case; the remaining columns are
    #: named per table.
    _HISTORY_SCOPED_TABLES = (
        ("deliveries", "work_key"),
        ("attempt_ledger", "work_key"),
        ("work_queue", "work_key"),
        ("attempt_records", "work_key"),
        ("attempt_liveness", "work_key"),
        ("attempt_usage", "work_key"),
        ("execution_fence", "work_key"),
        ("work_control", "work_key"),
        ("review_records", "work_key"),
        ("verification_evidence", "work_key"),
        ("operator_effort", "work_key"),
        ("preview_records", "work_key"),
        ("approval_requests", "work_key"),
        ("approval_decisions", "work_key"),
        ("publication_intents", "work_key"),
        ("control_records", "work_key"),
        ("alerts", "identity"),
        ("notification_outbox", "work_key"),
    )

    def export_history(self, authority_key):
        """Every durable row scoped to one repo identity — the
        retain/export/delete choice's *export* payload (F08 A4).

        Returns ``{"authority_key", "exported_at", "tables": {name:
        [rows]}}`` — redaction rules are unchanged: the store never
        held bodies, signatures or secrets, so the export is exactly
        the durable audit surface.
        """
        work_keys, attempt_ids, delivery_ids = \
            self._history_scope(authority_key)
        tables = {
            "work": [r for r in self.work_rows()
                     if r["authority_key"] == authority_key],
        }
        for table, col in self._HISTORY_SCOPED_TABLES:
            tables[table] = [r for r in self._rows(table)
                             if r.get(col) in work_keys]
        # Extra scoping the work_key column cannot express:
        # publication intents/control rows that resolved no work key
        # but still carry this authority, results keyed by attempt,
        # and events joined via work_key or this repo's deliveries.
        tables["publication_intents"] += [
            r for r in self._rows("publication_intents")
            if r.get("work_key") is None
            and r.get("authority_key") == authority_key]
        tables["control_records"] += [
            r for r in self._rows("control_records")
            if r.get("work_key") is None
            and r.get("authority_key") == authority_key]
        tables["results"] = [
            r for r in self._rows("results")
            if r.get("work_key") in work_keys
            or r.get("attempt_id") in attempt_ids]
        tables["events"] = [
            r for r in self._rows("events")
            if r.get("work_key") in work_keys
            or r.get("delivery_id") in delivery_ids]
        return {"authority_key": authority_key,
                "exported_at": _utcnow(),
                "work_keys": sorted(work_keys),
                "tables": tables}

    def delete_repo_history(self, authority_key):
        """Delete every durable row scoped to one repo identity —
        the *delete* history choice (F08 A4), inside ONE transaction
        so a crash cannot leave a half-deleted history.

        Returns the per-table deletion counts. The global rows
        (``meta``, ``reconcile_watermark``, ``participant_agreements``)
        and other identities' rows are untouched by construction.
        ``publication_intents``/``control_records`` carry their own
        ``authority_key`` so rows that never bound a work key are
        still scoped out correctly.
        """
        work_keys, attempt_ids, delivery_ids = \
            self._history_scope(authority_key)
        counts = {}
        with self.transact():
            if work_keys:
                marks = ",".join("?" for _ in work_keys)
                params = sorted(work_keys)
                for table, col in self._HISTORY_SCOPED_TABLES:
                    cur = self._q(
                        f"DELETE FROM {table} WHERE {col} IN ({marks})",
                        params)
                    counts[table] = cur.rowcount
                if attempt_ids:
                    amarks = ",".join("?" for _ in attempt_ids)
                    cur = self._q(
                        "DELETE FROM results WHERE attempt_id IN "
                        f"({amarks})", sorted(attempt_ids))
                    counts["results"] = cur.rowcount
                cur = self._q(
                    "DELETE FROM results WHERE work_key IN "
                    f"({marks})", params)
                counts["results"] = counts.get("results", 0) \
                    + cur.rowcount
                cur = self._q(
                    "DELETE FROM lane_state WHERE occupied_work IN "
                    f"({marks})", params)
                counts["lane_state"] = cur.rowcount
                # Events: work-scoped rows plus this identity's recorded
                # deliveries (denied-delivery events are unresolvable
                # and retained — they carry no repo binding).
                cur = self._q(
                    f"DELETE FROM events WHERE work_key IN ({marks})",
                    params)
                counts["events"] = cur.rowcount
                if delivery_ids:
                    dmarks = ",".join("?" for _ in delivery_ids)
                    cur = self._q(
                        "DELETE FROM events WHERE delivery_id IN "
                        f"({dmarks})", sorted(delivery_ids))
                    counts["events"] += cur.rowcount
                cur = self._q(
                    "DELETE FROM work WHERE authority_key=?",
                    (authority_key,))
                counts["work"] = cur.rowcount
            # Identity-keyed tables — bound to a work_key or not, every
            # row naming this authority goes (the work-scoped halves
            # were already covered when work_keys was non-empty).
            for table in ("publication_intents", "control_records"):
                cur = self._q(
                    f"DELETE FROM {table} WHERE authority_key=?",
                    (authority_key,))
                counts[table] = counts.get(table, 0) + cur.rowcount
        return {"authority_key": authority_key, "deleted": counts}
