#!/usr/bin/env python3
"""Clock-controlled privacy retention — PRD §5.2 (issue #18 /
Task 3.4, F07 A5).

The proposed retention defaults — 7 days for worker logs, 30 days
after completion for detailed audit metadata — are *config* (the
bounded ``evidence.worker_log_retention_days`` / ``audit_retention_days``
keys; the floors are the contract: a config can lengthen retention,
never shrink it). :func:`cleanup` applies them against an injected
``now_epoch`` so every window boundary is testable.

Two prune classes, two different guarantees:

- **Worker-log class** — ``attempt_liveness`` heartbeat noise is the
  worker-liveness log: deleted outright past the log window.
- **Audit-detail class** — for work in a *terminal* state whose last
  transition predates the audit window: the *detail* payloads are
  pruned (event ``detail``, attempt ``usage``, evidence
  ``checks``/``artifacts``, preview ``detail``/``smoke_observed``,
  control ``detail``, notification ``body``/``last_error``) while the
  rows' identities — seq, kind, ids, verdicts, digests — stay.

Nothing deletes the **identity tombstones** a replay needs to bounce
against: ``work`` logical identity, ``deliveries`` webhook dedup,
``control_records``/``publication_intents``/``merge_intents`` remote
identities, ``approval_*`` grant bindings, ``execution_fence`` and the
notification outbox rows all persist — for *active* work nothing is
touched at all. These minimal identities remain until the explicit,
reviewed registration removal
(:mod:`factory_kit.setup.remove` → ``RegistrationStore.remove``), which
is the only path that may drop them; :func:`describe_retention` renders
that distinction verbatim for the export/delete surfaces (A5).
"""

from __future__ import annotations

import time

from factory_kit.diagnostics.report import _iso_to_epoch

__all__ = [
    "WORKER_LOG_FLOOR_DAYS",
    "AUDIT_FLOOR_DAYS",
    "DEFAULT_WORKER_LOG_DAYS",
    "DEFAULT_AUDIT_DAYS",
    "TERMINAL_WORK_STATES",
    "DETAIL_COLUMNS",
    "retention_policy",
    "cleanup",
    "describe_retention",
]

#: The contract floors (§5.2): retention may lengthen, never shrink.
WORKER_LOG_FLOOR_DAYS = 7
AUDIT_FLOOR_DAYS = 30
DEFAULT_WORKER_LOG_DAYS = 7
DEFAULT_AUDIT_DAYS = 30

#: States where audit detail ages out — ``parked`` is *waiting*, never
#: terminal, so parked work keeps its full context for resumption.
TERMINAL_WORK_STATES = ("completed", "canceled", "blocked", "quarantined")

#: table -> detail columns pruned at the audit window for terminal
#: work. Row identities (keys, seqs, digests, verdicts) always stay —
#: only the free-form/detail payload goes.
DETAIL_COLUMNS = {
    "events": ("detail",),
    "attempt_usage": ("usage",),
    "verification_evidence": ("checks", "artifacts"),
    "preview_records": ("detail", "smoke_observed"),
    "control_records": ("detail",),
    "notification_outbox": ("body", "last_error"),
}


def retention_policy(effective=None):
    """Resolve the retention windows (seconds) from an effective config.

    ``effective`` is the repo's effective manifest (``evidence`` group);
    absent keys take the §5.2 defaults, and a value under the floor is
    clamped *up* — defense in depth on top of schema validation, so a
    hand-built config cannot silently shrink the windows either (A5).
    """
    evidence = (effective or {}).get("evidence") or {}

    def _days(key, default, floor):
        raw = evidence.get(key, default)
        try:
            days = float(raw)
        except (TypeError, ValueError):
            days = default
        return max(days, floor)

    return {
        "worker_log_days": _days("worker_log_retention_days",
                                 DEFAULT_WORKER_LOG_DAYS,
                                 WORKER_LOG_FLOOR_DAYS),
        "audit_days": _days("audit_retention_days",
                            DEFAULT_AUDIT_DAYS, AUDIT_FLOOR_DAYS),
        "worker_log_seconds": _days("worker_log_retention_days",
                                    DEFAULT_WORKER_LOG_DAYS,
                                    WORKER_LOG_FLOOR_DAYS) * 86400.0,
        "audit_seconds": _days("audit_retention_days",
                               DEFAULT_AUDIT_DAYS,
                               AUDIT_FLOOR_DAYS) * 86400.0,
    }


def _terminal_work_keys(store, audit_cutoff_epoch):
    """Work rows in a terminal state whose last durable transition
    (``updated_at``) predates the audit cutoff — the prune set. Active
    and parked work is never in it."""
    out = set()
    for w in store.work_rows():
        if w["state"] not in TERMINAL_WORK_STATES:
            continue
        if _iso_to_epoch(w.get("updated_at")) < audit_cutoff_epoch:
            out.add(w["work_key"])
    return out


def cleanup(store, effective=None, *, now_epoch=None):
    """One atomic clock-controlled retention pass (A5).

    Prunes the worker-log class past ``worker_log_seconds`` and the
    audit-detail class for terminal work past ``audit_seconds``, inside
    a single ``BEGIN IMMEDIATE`` transaction — a crash leaves the whole
    pass applied or none of it. Identity rows are never deleted: the
    retained work/delivery/identity records are what make an old
    webhook delivery collide with dedup instead of recreating work.

    Returns ``{"outcome": "pruned", "deleted": {…}, "pruned": {…},
    "retained_identities": …, "explanation": […]}`` — the counts are
    real SQL rowcounts, the explanation is :func:`describe_retention`'s
    export/delete distinction text.
    """
    now = float(time.time() if now_epoch is None else now_epoch)
    policy = retention_policy(effective)
    log_cutoff = now - policy["worker_log_seconds"]
    audit_cutoff = now - policy["audit_seconds"]

    deleted = {}
    pruned = {}
    with store.transact() as tx:
        # Worker-log class: heartbeat noise older than the log window —
        # but only for *terminal* work. A live attempt's liveness row is
        # active state, never log noise (A5 retains active records).
        terminal_all = {w["work_key"] for w in store.work_rows()
                        if w["state"] in TERMINAL_WORK_STATES}
        if terminal_all:
            marks = ",".join("?" for _ in terminal_all)
            cur = tx._q(
                "DELETE FROM attempt_liveness WHERE last_beat_epoch < ?"
                f" AND work_key IN ({marks})",
                [log_cutoff] + sorted(terminal_all))
            deleted["attempt_liveness"] = cur.rowcount
        else:
            deleted["attempt_liveness"] = 0

        terminal = _terminal_work_keys(store, audit_cutoff)
        if terminal:
            marks = ",".join("?" for _ in terminal)
            keys = sorted(terminal)
            for table, cols in DETAIL_COLUMNS.items():
                if table == "notification_outbox":
                    continue  # handled by state, below
                sets = ",".join(f"{c}=NULL" for c in cols)
                cur = tx._q(
                    f"UPDATE {table} SET {sets} WHERE work_key IN"
                    f" ({marks})", keys)
                pruned[table] = cur.rowcount
            # Outbox: the same window measured on the row's last update
            # for *settled* notifications — a pending retry is active
            # state, never detail.
            cur = tx._q(
                "UPDATE notification_outbox SET body=NULL,"
                " last_error=NULL WHERE work_key IN (" + marks + ")"
                " AND state IN ('delivered','failed')", keys)
            pruned["notification_outbox"] = cur.rowcount
            # Delivery-bound events (denied deliveries carry no
            # work_key): prune detail when the delivery's own ts is
            # past the window.
            delivery_ids = [r["delivery_id"] for r in
                            tx._rows("deliveries")
                            if r["work_key"] in terminal]
            if delivery_ids:
                dmarks = ",".join("?" for _ in delivery_ids)
                cur = tx._q(
                    "UPDATE events SET detail=NULL WHERE"
                    f" delivery_id IN ({dmarks})", sorted(delivery_ids))
                pruned["events"] = pruned.get("events", 0) + cur.rowcount
        # Orphan events (no work binding — pass-scoped recovery rows)
        # still age out of their detail at the audit window; their
        # seq/kind/timestamp identity is retained.
        cur = tx._q(
            "UPDATE events SET detail=NULL WHERE detail IS NOT NULL"
            " AND work_key IS NULL AND ts < ?",
            (_iso(audit_cutoff),))
        pruned["events"] = pruned.get("events", 0) + cur.rowcount

    retained = len(store.work_rows()) + len(store.delivery_rows())
    return {
        "outcome": "pruned",
        "now_epoch": now,
        "policy": policy,
        "cutoffs": {"worker_log_before_epoch": log_cutoff,
                    "audit_before_epoch": audit_cutoff},
        "deleted": deleted,
        "pruned": pruned,
        "retained_identities": retained,
        "explanation": describe_retention(effective)["explanation"],
    }


def describe_retention(effective=None):
    """The retention/tombstone distinction text (A5) — the same
    explanation the export and delete paths quote so an operator always
    sees *what* survives pruning and why replay protection holds."""
    policy = retention_policy(effective)
    return {
        "policy": policy,
        "explanation": [
            (f"worker-log detail (attempt liveness heartbeats) is "
             f"deleted after {policy['worker_log_days']:.0f} days"),
            (f"detailed audit metadata (event detail, usage payloads, "
             f"check artifacts, preview/control detail) is pruned "
             f"{policy['audit_days']:.0f} days after work reaches a "
             f"terminal state — active and parked work is never pruned"),
            ("identity tombstones persist until explicit registration "
             "removal: work logical identity, delivery dedup records, "
             "control/command identities, publication/merge intent "
             "identities and the notification outbox stay, so an old "
             "webhook delivery still collides with dedup instead of "
             "recreating work"),
            ("export copies the retained history; delete removes the "
             "repo-scoped detail rows — neither drops tombstones while "
             "the registration stands; only the reviewed registration "
             "removal does"),
        ],
    }


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(
        float(epoch), tz=timezone.utc).isoformat(timespec="milliseconds")
