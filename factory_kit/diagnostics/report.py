#!/usr/bin/env python3
"""Local diagnostic/status summaries — PRD §7.2 (issue #14 / Task 2.9,
F07 A1/A4/A5/A6).

Read-only views over the durable intake store. Three surfaces:

- :func:`work_status` — the A1 terminal/waiting status projection:
  task/attempt/generation identity, the explicit outcome or blocker,
  the current relevant revision, PR/check/review references, elapsed
  active-worker time and remaining limits.
- :func:`diagnostic_report` — the A4 product/technical summary:
  completed/blocked/failed/canceled counts with rates, installation
  attempts, baseline/intervention minutes, separately fielded usage
  durations and alert/notification tallies. No generated-code-volume
  proxy; no centralized telemetry — this is a local read.
- :func:`export_diagnostics` / :func:`pilot_export` — the export paths
  (A5/A6): the default export carries no ``detail`` payload at all;
  the explicitly selected *expanded* export includes detail run
  through :func:`factory_kit.events.schema.redact_properties`, so an
  export can never carry a credential canary or a raw body. Aggregate
  ``pilot_export`` exists only against a *recorded* participant
  agreement — external collection cannot be silently enabled.

Every field the store cannot answer stays ``None`` (unknown), never a
zero or an estimate.
"""

from __future__ import annotations

import json as _json
import time
from datetime import datetime

from factory_kit.durable.store import _utcnow
from factory_kit.events import schema as _schema

__all__ = [
    "work_status",
    "diagnostic_report",
    "export_diagnostics",
    "pilot_export",
    "telegram_summary",
]

_PILOT_EXPORT_SCOPE = "pilot-export"

#: Work states that are terminal-but-not-successful — the A4 "blocked"
#: bucket. ``parked`` is *waiting*, not blocked: a revoked opt-in or a
#: recovery park parks work that a re-authorization can still move.
_BLOCKED_STATES = ("blocked", "quarantined")
_TERMINAL_STATES = ("completed", "canceled", "blocked", "quarantined")
_FAILED_OUTCOMES = ("failed", "fenced")


def _iso_to_epoch(ts):
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _json_or_none(text):
    if not text:
        return None
    try:
        return _json.loads(text)
    except ValueError:
        return None


# --------------------------------------------------------------------- #
# A1 — per-work terminal/waiting status
# --------------------------------------------------------------------- #

def _latest_attempt(store, work_key):
    rows = store.attempt_record_rows(work_key)
    return rows[-1] if rows else None


def _limits_remaining(store, work, attempts, now):
    """Remaining execution limits from the latest attempt's durable
    limits snapshot — the dispatch-time bound budget — or ``None`` when
    no attempt ever recorded one (unknown, not zero)."""
    snapshot = None
    for rec in reversed(attempts):
        snap = _json_or_none(rec.get("limits"))
        if snap:
            snapshot = snap
            break
    if snapshot is None:
        return None
    measured, unknown = store.work_active_seconds(work["work_key"])
    impl_used = store.count_attempts(work["work_key"],
                                     role="implementation")
    starts = [_iso_to_epoch(r.get("started")) for r in attempts]
    wall_used = max(0.0, now - min(s for s in starts if s)) \
        if any(starts) else 0.0
    lim_att = snapshot.get("implementation_attempts")
    lim_active = snapshot.get("active_worker_seconds")
    lim_wall = snapshot.get("wall_seconds")
    return {
        "implementation_attempts": {
            "used": impl_used, "limit": lim_att,
            "remaining": (lim_att - impl_used)
            if lim_att is not None else None},
        "active_worker_seconds": {
            "measured": measured, "unknown_attempts": unknown,
            "limit": lim_active,
            "remaining": (lim_active - measured)
            if lim_active is not None else None},
        "wall_seconds": {
            "used": wall_used, "limit": lim_wall,
            "remaining": (lim_wall - wall_used)
            if lim_wall is not None else None},
    }


def work_status(store, work_key, *, now=None):
    """The A1 status projection for one work row.

    Terminal *and* waiting states answer the same questions: task/
    attempt/generation identity, the explicit outcome or the blocker
    it's waiting on, the current relevant revision, PR/check/review
    references, elapsed active-worker time and remaining limits. A
    field the trail cannot answer is ``None`` — unknown, never
    invented.
    """
    now = time.time() if now is None else now
    work = store.get_work(work_key)
    if work is None:
        return {"status": "unknown", "reason": "unknown-work",
                "work_key": work_key}
    pause = store.pause_info(work_key)
    fence = store.fence_state(work_key)
    queue = store.queue_entry(work_key)
    attempts = store.attempt_record_rows(work_key)
    last = attempts[-1] if attempts else None
    evidence = store.latest_evidence(work_key)
    review = store.latest_review(work_key)

    # Current relevant revision: the verified/observed head wins, then
    # the newest publication intent's expected revision, then the
    # issue revision recorded at intake.
    revision = None
    if evidence and evidence.get("head_sha"):
        revision = evidence["head_sha"]
    else:
        intents = [i for i in store.intent_rows(work_key)
                   if i.get("expected_revision")]
        if intents:
            revision = intents[-1]["expected_revision"]
        elif work.get("issue_revision"):
            revision = work["issue_revision"]

    # Explicit blocker — the *reason* a waiting/terminal work is where
    # it is, in precedence order.
    blocker = None
    if work.get("parked_reason"):
        blocker = work["parked_reason"]
    elif fence and fence.get("fenced"):
        blocker = f"fenced:{fence.get('fence_reason') or 'unknown'}"
    elif queue and queue.get("reason"):
        blocker = queue["reason"]
    elif pause.get("pause_state") in ("requested", "paused"):
        blocker = (f"pause-{pause['pause_state']}"
                   + (f":{pause['paused_stage']}"
                      if pause.get("paused_stage") else ""))

    checks = None
    if evidence and evidence.get("checks"):
        raw = _json_or_none(evidence["checks"]) or []
        checks = [{"name": c.get("name"), "id": c.get("id"),
                   "conclusion": c.get("conclusion")}
                  for c in raw if isinstance(c, dict)] or None

    measured, unknown = store.work_active_seconds(work_key)
    heartbeat = store.last_heartbeat(work_key)

    # F11 preview projection (A3): the durable record plus
    # ``approval_ready``, computed *now* — a verified record counts only
    # while unexpired, unfenced and backed by a ``verified`` observation
    # of the same head; anything else is a visible blocker, never a
    # substitute.
    preview_row = store.latest_preview(work_key)
    preview = None
    if preview_row is not None:
        expired = preview_row["expires_epoch"] is not None and \
            float(preview_row["expires_epoch"]) < now
        preview = {
            "preview_id": preview_row["preview_id"],
            "state": preview_row["state"],
            "reason": preview_row["reason"],
            "provider": preview_row["provider"],
            "deployment_id": preview_row["deployment_id"],
            "artifact_identity": preview_row["artifact_identity"],
            "url": preview_row["url"],
            "visibility": preview_row["visibility"],
            "head_sha": preview_row["head_sha"],
            "base_sha": preview_row["base_sha"],
            "observed_at": preview_row["observed_at"],
            "expires_epoch": preview_row["expires_epoch"],
            "cleanup_deadline_epoch":
                preview_row["cleanup_deadline_epoch"],
            "cleanup_owner": preview_row["cleanup_owner"],
            "removed_at": preview_row["removed_at"],
            "expired": bool(expired),
            "approval_ready": bool(
                preview_row["state"] == "verified" and not expired
                and not (fence and fence["fenced"])
                and evidence is not None
                and evidence["status"] == "verified"
                and evidence.get("head_sha") ==
                preview_row["head_sha"]),
        }

    return {
        "status": work["state"],
        "work_key": work_key,
        "task_id": work.get("task_id"),
        "issue": work.get("issue"),
        "generation": work.get("generation"),
        "outcome": (last["outcome"] if last and last.get("outcome")
                    else work["state"]),
        "verdict": last["verdict"] if last else None,
        "blocker": blocker,
        "pause_state": pause["pause_state"],
        "revision": revision,
        "pr": {"number": work.get("linked_pr"),
               "url": evidence.get("pr_url") if evidence else None,
               "evidence_pr": evidence.get("pr_number")
               if evidence else None},
        "checks": checks,
        "review": {"review_id": review["review_id"],
                   "session_id": review["session_id"],
                   "verdict": review["verdict"],
                   "sha": review["sha"]} if review else None,
        "evidence": {"status": evidence["status"],
                     "reason": evidence["reason"],
                     "observed_at": evidence["observed_at"]}
        if evidence else None,
        "preview": preview,
        "attempts": {"count": len(attempts),
                     "latest": (last["attempt_id"] if last else None)},
        "active_worker_seconds": {"measured": measured,
                                  "unknown_attempts": unknown},
        "limits": _limits_remaining(store, work, attempts, now),
        "fence": {"fenced": bool(fence["fenced"]),
                  "reason": fence["fence_reason"],
                  "termination": fence["termination"]}
        if fence else None,
        "last_heartbeat_epoch": heartbeat,
        "updated_at": work.get("updated_at"),
    }


# --------------------------------------------------------------------- #
# A4 — the local diagnostic report
# --------------------------------------------------------------------- #

def _outcome_bucket(store, work_row):
    """One exclusive outcome bucket per work row (A4): terminal states
    win; a non-terminal row whose latest finished attempt failed counts
    as failed; everything else is still in flight."""
    state = work_row["state"]
    if state == "completed":
        return "completed"
    if state == "canceled":
        return "canceled"
    if state in _BLOCKED_STATES:
        return "blocked"
    last = _latest_attempt(store, work_row["work_key"])
    if last is not None and last.get("outcome") in _FAILED_OUTCOMES:
        return "failed"
    return "in_progress"


def _duration_totals(store):
    """A3's four wait classes, summed over measured usage rows — each
    class separately reported, each gap counted as unknown."""
    totals = {f: {"measured": 0.0, "unknown": 0}
              for f in _schema.USAGE_DURATION_FIELDS}
    for row in store._rows("attempt_usage"):
        usage = _json_or_none(row.get("usage"))
        if usage is None:
            for field in totals:
                totals[field]["unknown"] += 1
            continue
        norm = _schema.normalize_usage(usage)
        for field in totals:
            val = norm["durations"].get(field)
            if val is None:
                totals[field]["unknown"] += 1
            else:
                totals[field]["measured"] += val
    return totals


def diagnostic_report(store, *, setup_store=None):
    """The §7.2 product + technical view (A4).

    Counts and rates come straight from durable rows — no
    generated-code-volume proxy anywhere. ``installation.attempts`` is
    the ``setup_checked`` event count across the intake store and an
    optional setup store; with no setup data source at all it is
    ``None`` — unknown, not zero.
    """
    works = store.work_rows()
    states = {}
    outcomes = {"completed": 0, "blocked": 0, "failed": 0,
                "canceled": 0, "in_progress": 0}
    for w in works:
        states[w["state"]] = states.get(w["state"], 0) + 1
        outcomes[_outcome_bucket(store, w)] += 1
    total = len(works)
    terminal = sum(outcomes[k] for k in
                   ("completed", "blocked", "failed", "canceled"))

    measured_s, _unused = 0.0, 0
    for w in works:
        m, u = store.work_active_seconds(w["work_key"])
        measured_s += m
        _unused += u
    # Unknown usage = an attempt with no measured usage payload *or*
    # no measured active time — counted as unknown, never treated as
    # zero consumption (A3).
    usage_rows = store._rows("attempt_usage")
    unknown_usage = sum(1 for r in usage_rows
                        if r["usage"] is None
                        or r["active_seconds"] is None)
    effort = store._rows("operator_effort")
    current_effort = [r for r in effort
                      if not any(e["supersedes"] == r["effort_id"]
                                 for e in effort)]
    baseline_min = sum(r["active_minutes"] for r in current_effort
                       if r["category"] == "baseline"
                       and r["active_minutes"] is not None)
    intervention_min = sum(r["active_minutes"] for r in current_effort
                           if r["category"] != "baseline"
                           and r["active_minutes"] is not None)

    setup_events = store.event_rows("setup_checked")
    applied_events = store.event_rows("setup_applied")
    setup_attempts = len(setup_events) + (
        len(setup_store.events("setup_checked"))
        if setup_store is not None else 0)
    setup_applied = len(applied_events) + (
        len(setup_store.events("setup_applied"))
        if setup_store is not None else 0)

    alerts = store._rows("alerts")
    by_severity = {}
    for a in alerts:
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1

    return {
        "schema_version": _schema.EVENT_SCHEMA_VERSION,
        "generated_at": _utcnow(),
        "work": {"total": total, "by_state": states},
        "outcomes": {k: v for k, v in outcomes.items()},
        "rates": {
            "completion_rate":
                outcomes["completed"] / total if total else None,
            "terminal_rate": terminal / total if total else None,
        },
        "installation": {
            "attempts": setup_attempts
            if (setup_store is not None or setup_events) else None,
            "applied": setup_applied
            if (setup_store is not None or applied_events) else None,
        },
        "effort": {
            "records": len(effort),
            "current_records": len(current_effort),
            "baseline_minutes": baseline_min,
            "intervention_minutes": intervention_min,
            "unknown_records": sum(
                1 for r in current_effort
                if r["active_minutes"] is None),
        },
        "usage": {
            "attempts": len(store._rows("attempt_usage")),
            "active_seconds_measured": measured_s,
            "unknown_usage": unknown_usage,
            "durations": _duration_totals(store),
        },
        "alerts": {"total": len(alerts), "by_severity": by_severity},
        "notifications": {
            "delivered":
                len(store.event_rows("notification_delivered")),
            "failed": len(store.event_rows("notification_failed")),
        },
    }


# --------------------------------------------------------------------- #
# A5/A6 — exports
# --------------------------------------------------------------------- #

def _event_projection(row, *, expanded):
    """One event row for export. The default projection carries no
    ``detail`` at all — the strongest possible "no raw bodies/logs"
    guarantee (A5). Expanded export parses detail and runs it through
    the schema redactor, so secrets still cannot leave."""
    out = {"seq": row["seq"], "ts": row["ts"], "kind": row["kind"],
           "work_key": row["work_key"],
           "delivery_id": row["delivery_id"], "reason": row["reason"],
           "config_digest": row["config_digest"],
           "supersedes": row.get("supersedes")}
    if expanded and row.get("detail"):
        out["detail"] = _schema.redact_properties(
            _json_or_none(row["detail"]) if
            row["detail"].lstrip().startswith(("{", "["))
            else row["detail"])
    return out


def export_diagnostics(store, *, expanded=False, fixture=None,
                       setup_store=None):
    """The local diagnostic export (A5).

    Default: summaries plus per-event metadata only — no detail
    payload, so no issue body, code, log or chat text can appear.
    ``expanded=True`` is the explicit operator-selected path and still
    redacts secrets; ``fixture`` names a seeded fixture identity (A7)
    so a measurement stays reproducible.
    """
    return {
        "schema_version": _schema.EVENT_SCHEMA_VERSION,
        "fixture": fixture,
        "generated_at": _utcnow(),
        "expanded": bool(expanded),
        "report": diagnostic_report(store, setup_store=setup_store),
        "events": [_event_projection(r, expanded=expanded)
                   for r in store.event_rows()],
        "effort": [
            {"effort_id": r["effort_id"], "work_key": r["work_key"],
             "actor_ref": r["actor_ref"],
             "active_minutes": r["active_minutes"],
             "category": r["category"], "supersedes": r["supersedes"],
             "recorded_at": r["recorded_at"]}
            for r in store._rows("operator_effort")],
        "control": [
            {"command_id": r["command_id"],
             "actor_ref": r["actor_ref"], "chat_ref": r["chat_ref"],
             "action": r["action"], "outcome": r["outcome"],
             "reason": r["reason"], "committed_at": r["committed_at"]}
            for r in store._rows("control_records")],
    }


def pilot_export(store, *, scope=_PILOT_EXPORT_SCOPE):
    """Aggregate pilot export (A6 + Task 4.4/A2/A5) — gated on recorded
    participant agreement; it cannot silently enable external
    collection.

    Returns ``{"outcome": "denied", "reason":
    "participant-agreement-missing"}`` unless a durable agreement row
    exists for ``scope``, and
    ``{"outcome": "denied", "reason":
    "participant-agreement-revoked"}`` once that agreement is withdrawn
    — revoked consent blocks the aggregate export, not only new
    collection (A2). On success, events bound to participants whose
    consent is revoked or whose admission was denied are excluded via
    their ``authority_key`` — the durable consent state, not an
    in-memory flag, decides. Every event's properties then pass
    through :func:`aggregate_properties` — no bodies, code, usernames,
    tokens or chat text; raw numeric actor IDs remain restricted
    references, and participant identities never appear (the admission
    trail carries hashes only, A5).
    """
    # Deferred: privacy.retention imports this module, so the consent
    # gate is resolved at call time to keep the import acyclic.
    from factory_kit.privacy import consent as _consent
    gate = _consent.export_gate(store, scope)
    if gate["outcome"] == "denied":
        return gate
    agreement = gate["agreement"]
    excluded = set(gate["excluded_authorities"])
    # work_key embeds the authority it was minted under —
    # ``{authority}#{issue}:g{n}`` — so an excluded participant's
    # collected events never reach the aggregate.
    events = []
    dropped = 0
    for row in store.event_rows():
        work_key = row["work_key"]
        if work_key and work_key.split("#", 1)[0] in excluded:
            dropped += 1
            continue
        props = {}
        if row.get("detail"):
            parsed = _json_or_none(row["detail"])
            # Only mapping details contribute properties — a stored
            # scalar/array detail is not a property bag, and feeding
            # it to dict.update would crash the export.
            if isinstance(parsed, dict):
                props = parsed
        merged = {"work_key": work_key, "reason": row["reason"],
                  "config_digest": row["config_digest"]}
        merged.update(props)
        events.append({"seq": row["seq"], "ts": row["ts"],
                       "kind": row["kind"],
                       "properties": _schema.aggregate_properties(
                           merged)})
    return {
        "outcome": "exported",
        "scope": scope,
        "schema_version": _schema.EVENT_SCHEMA_VERSION,
        "generated_at": _utcnow(),
        "agreement": {"scope": agreement["scope"],
                      "actor_ref": agreement["actor_ref"],
                      "recorded_at": agreement["recorded_at"]},
        "consent": {"excluded_authorities": sorted(excluded),
                    "events_excluded": dropped},
        "report": diagnostic_report(store),
        "events": events,
    }


# --------------------------------------------------------------------- #
# A5 — concise Telegram summary: links, never raw content
# --------------------------------------------------------------------- #

def telegram_summary(status):
    """Render :func:`work_status` output as a concise operator summary
    — stable identifiers, an explicit state word, the blocker and
    *links to authorized evidence* (PR URL, evidence reference), never
    raw bodies, code, logs or chat text (A5)."""
    if status.get("status") == "unknown":
        return (f"STATUS UNKNOWN - {status.get('work_key')}: "
                f"{status.get('reason')}.")
    state = str(status.get("status") or "unknown").upper()
    lines = [
        f"{state} - work {status.get('work_key')}; task "
        f"{status.get('task_id')}; generation {status.get('generation')}.",
        f"Outcome: {status.get('outcome') or 'in-flight'}; "
        f"blocker: {status.get('blocker') or 'none'}.",
        f"Revision: {status.get('revision') or 'unknown'}; "
        f"attempts: {status.get('attempts', {}).get('count', 0)}.",
    ]
    active = status.get("active_worker_seconds") or {}
    measured = active.get("measured")
    unknown = active.get("unknown_attempts") or 0
    lines.append(
        "Active worker: "
        + (f"{measured:.0f}s" if measured is not None else "unknown")
        + (f" (+{unknown} unmeasured)" if unknown else "") + ".")
    pr = status.get("pr") or {}
    link = pr.get("url") or (f"#{pr['number']}" if pr.get("number")
                             else None)
    evidence = status.get("evidence") or {}
    refs = []
    if link:
        refs.append(f"PR {link}")
    if evidence.get("observed_at"):
        refs.append(f"evidence {evidence.get('status')} @ "
                    f"{evidence['observed_at']}")
    lines.append("Evidence: " + ("; ".join(refs) if refs else "none") + ".")
    limits = status.get("limits") or {}
    impl = limits.get("implementation_attempts") or {}
    if impl:
        lines.append(
            f"Limits: attempts {impl.get('used')}/"
            f"{impl.get('limit')} (remaining {impl.get('remaining')}).")
    return "\n".join(lines)
