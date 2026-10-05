#!/usr/bin/env python3
"""Operator-facing status/operations views — PRD §7.2 (issue #18 /
Task 3.4, F07 A2/A3/A6).

Two read-only projections over the durable store, plus their text
renderers:

- :func:`status_view` — the per-work view: everything
  :func:`factory_kit.diagnostics.report.work_status` already answers
  (task/attempt/generation, explicit state/blocker, revision, PR/
  check/review/preview references, elapsed worker time, remaining
  limits) *plus* the Task-3.4 additions — durable queue age, the last
  heartbeat, the reconciliation watermark (last attempt/success, remote
  freshness, consecutive failures) and the notification outbox tallies
  for the work.
- :func:`operations_view` — the installation-level view:
  :func:`~factory_kit.diagnostics.report.diagnostic_report` (attempts,
  completion rate, intervention minutes, usage durations) plus the same
  remote-observation block and fleet-wide notification tallies.

Freshness is *labelled, never hidden*: a ``remote.stale`` flag and the
``remote_state`` word come straight from the durable reconciliation
watermark — a stale observation is shown *as* stale rather than
silently omitted — and every unmeasured field stays ``None``
(``unknown``), never zero or an estimate (A2). The renderers emit plain
text only: stable identifiers, explicit state words and links, no color
or emoji anywhere — every line survives a screen reader verbatim (A3).
"""

from __future__ import annotations

import time

from factory_kit.diagnostics import report as _report

__all__ = [
    "status_extras",
    "status_view",
    "operations_view",
    "render_status_text",
    "render_operations_text",
]


def _iso_to_epoch(ts):
    return _report._iso_to_epoch(ts)


# --------------------------------------------------------------------- #
# Shared extras — one computation both the view layer and the Telegram
# status handler consume, so the two surfaces can never disagree (A2).
# --------------------------------------------------------------------- #

def status_extras(store, work_key, *, now=None):
    """The Task-3.4 fields A2 adds to the per-work status surface.

    ``queue_age_seconds`` — time since the durable queue entry was
    stamped (``work_queue.enqueued_at``); ``None`` when the work is not
    queued/parked. ``remote`` — the reconciliation watermark:
    ``remote_state`` is the durable freshness word (``fresh`` /
    ``stale`` / ``unknown``) and ``stale`` is the explicit label an
    observation carries while the remote is past the stale threshold.
    ``notifications`` — outbox tallies for this work plus the last
    terminal failure reason, so a delivery failure is visible wherever
    the work is inspected (A1/A6).
    """
    now = time.time() if now is None else float(now)
    queue = store.queue_entry(work_key)
    queue_age = None
    if queue is not None and queue.get("enqueued_at"):
        queue_age = max(0.0, now - _iso_to_epoch(queue["enqueued_at"]))
    watermark = store.reconcile_watermark()
    remote = None
    if watermark is not None:
        remote = {
            "remote_state": watermark["remote_state"],
            "stale": watermark["remote_state"] == "stale",
            "stale_since_epoch": watermark["stale_since_epoch"],
            "last_attempt_epoch": watermark["last_attempt_epoch"],
            "last_success_epoch": watermark["last_success_epoch"],
            "consecutive_failures": watermark["consecutive_failures"],
        }
    notifications = store.notification_counts(work_key)
    failed = store.notification_rows(work_key, state="failed")
    notifications["last_failure_reason"] = \
        failed[-1].get("last_error") if failed else None
    return {"queue_age_seconds": queue_age,
            "queue_state": queue["state"] if queue else None,
            "queue_reason": queue["reason"] if queue else None,
            "remote": remote,
            "notifications": notifications}


def status_view(store, work_key, *, now=None):
    """The complete A2 per-work view: the work_status projection merged
    with :func:`status_extras`. Unknown stays ``None`` — labelled, never
    invented."""
    status = _report.work_status(store, work_key, now=now)
    status.update(status_extras(store, work_key, now=now))
    return status


def operations_view(store, *, now=None, setup_store=None):
    """The A2 installation-level view: the §7.2 report (installation
    attempts, completion rate, intervention minutes, separately fielded
    durations, alert/notification tallies) plus remote-observation
    freshness and pending notification count — the operator's "is the
    loop healthy" read."""
    report = _report.diagnostic_report(store, setup_store=setup_store)
    watermark = store.reconcile_watermark()
    report["remote"] = None if watermark is None else {
        "remote_state": watermark["remote_state"],
        "stale": watermark["remote_state"] == "stale",
        "stale_since_epoch": watermark["stale_since_epoch"],
        "last_attempt_epoch": watermark["last_attempt_epoch"],
        "last_success_epoch": watermark["last_success_epoch"],
        "consecutive_failures": watermark["consecutive_failures"],
    }
    counts = store.notification_counts()
    counts["total"] = sum(counts.values())
    report["notification_outbox"] = counts
    return report


# --------------------------------------------------------------------- #
# Text renderers — plain text, explicit words, links only (A3)
# --------------------------------------------------------------------- #

def _fmt_age(seconds):
    """Age in whole seconds/minutes — plain words, no symbols."""
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _fmt_epoch(epoch):
    """Epoch → ISO seconds; ``None`` renders as the explicit word
    ``unknown`` — a never-observed remote is labelled, not hidden."""
    if epoch is None:
        return "unknown"
    from datetime import datetime, timezone
    return datetime.fromtimestamp(
        float(epoch), tz=timezone.utc).isoformat(timespec="seconds")


def render_status_text(view):
    """Render :func:`status_view` as the Telegram-accessible status text:
    the existing concise summary plus queue age, remote-observation
    freshness and notification failures — all explicit words (A2/A3)."""
    lines = [_report.telegram_summary(view)]
    if view.get("queue_state"):
        lines.append(
            f"Queue: {view['queue_state']} for "
            f"{_fmt_age(view.get('queue_age_seconds'))}"
            + (f" ({view.get('queue_reason')})"
               if view.get("queue_reason") else "") + ".")
    remote = view.get("remote") or {}
    if remote:
        state = remote.get("remote_state") or "unknown"
        stale_note = " STALE" if remote.get("stale") else ""
        lines.append(
            f"Remote observation: {state}{stale_note}; last success "
            f"{_fmt_epoch(remote.get('last_success_epoch'))}; "
            f"consecutive failures "
            f"{remote.get('consecutive_failures', 0)}.")
    else:
        lines.append("Remote observation: unknown - no reconciliation "
                     "has completed.")
    notes = view.get("notifications") or {}
    line = (f"Notifications: {notes.get('pending', 0)} pending, "
            f"{notes.get('failed', 0)} failed, "
            f"{notes.get('delivered', 0)} delivered.")
    if notes.get("last_failure_reason"):
        line += f" Last failure: {notes['last_failure_reason']}."
    lines.append(line)
    return "\n".join(lines)


def render_operations_text(view):
    """Render :func:`operations_view` as the accessible operations
    summary — installation counts, rates, minutes and the remote/
    notification health lines (A2/A3)."""
    work = view.get("work") or {}
    outcomes = view.get("outcomes") or {}
    rates = view.get("rates") or {}
    install = view.get("installation") or {}
    effort = view.get("effort") or {}
    usage = view.get("usage") or {}
    notes = view.get("notification_outbox") or {}
    remote = view.get("remote") or {}

    def _rate(v):
        return f"{v * 100:.0f}%" if isinstance(v, (int, float)) \
            else "unknown"

    lines = [
        "OPERATIONS SUMMARY.",
        f"Work: {work.get('total', 0)} total "
        f"({', '.join(f'{k} {v}' for k, v in sorted(
            work.get('by_state', {}).items())) or 'none'}); "
        f"outcomes: completed {outcomes.get('completed', 0)}, "
        f"blocked {outcomes.get('blocked', 0)}, "
        f"failed {outcomes.get('failed', 0)}, "
        f"canceled {outcomes.get('canceled', 0)}, "
        f"in progress {outcomes.get('in_progress', 0)}.",
        f"Completion rate {_rate(rates.get('completion_rate'))}; "
        f"terminal rate {_rate(rates.get('terminal_rate'))}.",
        f"Installation: attempts "
        f"{install.get('attempts')
            if install.get('attempts') is not None else 'unknown'}, "
        f"applied "
        f"{install.get('applied')
            if install.get('applied') is not None else 'unknown'}.",
        f"Effort: baseline "
        f"{effort.get('baseline_minutes')}m, intervention "
        f"{effort.get('intervention_minutes')}m "
        f"({effort.get('unknown_records', 0)} records unknown).",
        f"Usage: {usage.get('attempts', 0)} attempts, "
        f"{usage.get('unknown_usage', 0)} unknown usage.",
        f"Notifications: {notes.get('pending', 0)} pending, "
        f"{notes.get('failed', 0)} failed, "
        f"{notes.get('delivered', 0)} delivered.",
    ]
    if remote:
        lines.append(
            f"Remote observation: "
            f"{remote.get('remote_state') or 'unknown'}"
            + (" STALE" if remote.get("stale") else "")
            + "; last success "
            + f"{_fmt_epoch(remote.get('last_success_epoch'))}.")
    else:
        lines.append("Remote observation: unknown - no reconciliation "
                     "has completed.")
    return "\n".join(lines)
