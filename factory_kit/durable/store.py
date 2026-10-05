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
  ``work_updated`` / ``work_parked`` / task-binding transitions). Rows
  carry identity/delivery IDs, reason codes and digests — never issue
  bodies, signatures or secrets.
- ``attempt_ledger`` — at-most-one-active-attempt per work row, enforced
  by a partial unique index so the cap cannot be raced. The execution
  lane (Task 2.4) claims through it; intake only guarantees the
  structure.

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

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

__all__ = ["IntakeStoreError", "IntakeStore", "work_key_for"]


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
    detail TEXT);

CREATE TABLE IF NOT EXISTS attempt_ledger(
    attempt_id TEXT PRIMARY KEY,
    work_key TEXT NOT NULL,
    role TEXT NOT NULL,
    state TEXT NOT NULL,
    started TEXT NOT NULL,
    ended TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt_per_work
    ON attempt_ledger(work_key) WHERE state='active';
"""


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
        except sqlite3.Error as exc:
            raise IntakeStoreError(
                f"cannot open intake store {path}: {exc}") from exc

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
        try:
            return self.db.execute(sql, params)
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

    # -- events (§7.1) ---------------------------------------------------------

    def record_event(self, kind, *, delivery_id=None, work_key=None,
                     reason=None, config_digest=None, detail=None):
        """Append one intake event. Carries identities, reason codes and
        digests only — bodies, signatures and secrets never reach this
        table by construction (callers pass reason codes, not content)."""
        self._q(
            "INSERT INTO events(ts,kind,delivery_id,work_key,reason,"
            "config_digest,detail) VALUES(?,?,?,?,?,?,?)",
            (_utcnow(), kind, delivery_id, work_key, reason,
             config_digest, detail))

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
