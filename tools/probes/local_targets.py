#!/usr/bin/env python3
"""factory-kit §5.1 local-target measurement probe — issue #22
(task 3.8, PRD §5.1 targets and §9.1 Q9).

Measures every proposed §5.1 local target on the documented selected
host and emits a per-target pass/fail + uncertainty report that feeds
the Q9 defaults decision. The harness reuses the §8.2 ``World``
fixture stack (``tests/faults/harness.py``): the scripted GitHub,
preview, worker and issue-source ports mean *no GitHub/model/Telegram
network latency can enter the measurements* — local time is measured
with ``time.perf_counter`` and network delay is reported as a separate
zero-by-construction term, exactly the separation §5.1 requires.

Targets measured (issue A1–A7 ↔ PRD §5.1 rows):

- ``intake_p95`` — durable handler accept/reject under sustained
  arrivals, proposed p95 ≤ 2 s, over a store pre-loaded with 100
  pending tasks and 10,000 retained event records (A1+A2). The
  acknowledgement is returned only after the durable transaction
  commits — there is no code path that acknowledges first (F02/A5).
- ``duplicate_burst`` — 100 duplicate deliveries inside a 10-second
  window, including a mid-burst controller restart, converge on one
  logical task and ≤ 1 remote PR (A2).
- ``status_p95`` — 100 persisted local-status assemblies, proposed
  p95 ≤ 1 s; stale remote observations carry the explicit label (A3).
- ``control_persist`` — authenticated pause/resume/cancel commands
  commit their control record (and, for cancel, the generation fence)
  within 5 s of handler receipt, independent of Telegram delivery —
  the transport is not in the measured path (A3).
- ``cancel_window`` — long-running worker/descendant exit confirms or
  quarantines within 30 s of cancellation, with the local notification
  durable inside the same window; replacement stays denied while
  termination is uncertain (A4).
- ``reconcile_discovery`` — nominal reconciliation interval ≤ 60 s;
  a dropped eligible webhook is discovered inside 120 s of GitHub
  connectivity returning, absent upstream-directed backoff (A4).
- ``restart_recovery`` — every known active task recovers or
  explicitly parks within 60 s of controller readiness (A5).
- ``crash_zero_loss`` — defined crash tests lose zero acknowledged
  tasks: read straight out of the recorded §8.2 fault-matrix audit
  counters (A5).
- ``idle_zero_calls`` — a 30-minute idle window with no eligible work
  and no exceptions produces zero model calls (A5).
- ``observability`` — offline/liveness/last-reconciliation state is
  observable, no always-on SLA claim exists, CI wait is excluded from
  the 60 active-worker minutes, the 24-hour wall bound parks, and the
  retry backoff cap is 5 minutes unless upstream directs longer (A6).
- ``q9_defaults`` — the proposed numerical defaults table A7 submits
  to the owner: each default, its source constant and the measured
  evidence behind the accept/adjust recommendation.

Uncertainty is reported honestly: single host, single run, ``n``,
mean/σ, p50/p95/p99/max, and the margin between the proposed bound and
the measured p95. Numbers are measured — never invented.

Exit codes (shared gi-* vocabulary):

    0  targets-passed — every measured target inside its bound
    1  targets-failed — at least one target missed or unmeasured
    2  usage error    — malformed invocation
    4  cannot complete — the benchmark could not be built or read

Usage:

    python3 tools/probes/local_targets.py                 # full run
    python3 tools/probes/local_targets.py --quick         # short run
    python3 tools/probes/local_targets.py --write out.json
    python3 tools/probes/local_targets.py --fixture out.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.config import schema
from factory_kit.diagnostics import views as _views
from factory_kit.events import schema as _events_schema
from factory_kit.execution import limits as _limits
from factory_kit.execution.worker import SessionHandle
from factory_kit.recovery import backoff as _backoff
from factory_kit.recovery.service import RecoveryService
from tests.faults.harness import World

VERSION = "1.0.0"

#: §5.1 proposed bounds (seconds unless noted). Q9-proposed, bounded by
#: schema/recovery constants — measured here, never assumed.
INTAKE_P95_S = 2.0
STATUS_P95_S = 1.0
CONTROL_PERSIST_S = 5.0
CANCEL_WINDOW_S = 30.0
RECONCILE_INTERVAL_S = 60.0
DISCOVERY_BOUND_S = 120.0
RECOVERY_DEADLINE_S = 60.0
IDLE_WINDOW_S = 30 * 60.0
STALE_REMOTE_S = 300.0

#: A1 fixture scale — "one registered repository, one active task, up to
#: 100 pending tasks and 10,000 retained event records" (§5.1 preamble).
SEED_PENDING = 100
SEED_EVENTS = 10_000

#: Full-run sustained intake: 1 event/second for 5 minutes (A2).
FULL_INTAKE_DELIVERIES = 300
FULL_INTAKE_RATE_HZ = 1.0
#: --quick keeps the mechanics, shrinks the soak.
QUICK_INTAKE_DELIVERIES = 30
QUICK_INTAKE_RATE_HZ = 0.0  # unpaced

DUPLICATE_BURST_N = 100
DUPLICATE_BURST_WINDOW_S = 10.0
STATUS_QUERIES = 100

FAULT_MATRIX_JSON = ROOT / "docs" / "evidence" / "fault-matrix-2026-10-05.json"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now():
    return time.perf_counter()


def _stats(samples):
    """Descriptive stats for one latency sample set (seconds)."""
    if not samples:
        return {"n": 0}
    s = sorted(samples)
    n = len(s)

    def pct(p):
        if n == 1:
            return s[0]
        rank = (p / 100.0) * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        return s[lo] + (s[hi] - s[lo]) * frac

    return {
        "n": n,
        "min_s": s[0],
        "mean_s": statistics.fmean(s),
        "stdev_s": statistics.stdev(s) if n > 1 else 0.0,
        "p50_s": pct(50),
        "p95_s": pct(95),
        "p99_s": pct(99),
        "max_s": s[-1],
    }


def _check(checks, name, ok, **measured):
    checks.append({"name": name, "pass": bool(ok), "measured": measured})
    return ok


def _iso_delta_s(later_iso, earlier_iso):
    """seconds between two store ISO timestamps (ms precision)."""
    from datetime import datetime

    a = datetime.fromisoformat(str(later_iso).replace("Z", "+00:00"))
    b = datetime.fromisoformat(str(earlier_iso).replace("Z", "+00:00"))
    return (a - b).total_seconds()


def host_block(world):
    """The documented selected host + fixture identity for the run."""
    eff = world.eff
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor_count": os.cpu_count(),
        "selected_host": (
            "M1 — macOS arm64, Python 3.14 (recorded "
            "in docs/decisions/tested-recipe-selection.md)"
        ),
        "manifest": str(ROOT / "docs" / "examples" / "reference.factory-kit.yml"),
        "manifest_effective_digest": schema.effective_digest(eff),
        "policy_digest": schema.policy_digest(eff),
        "repository": world.full_name,
        "repo_id": world.repo_id,
        "fixture_identity": {
            "remote": "ScriptedRemote — no GitHub network in path",
            "preview": "ScriptedPreview — no provider network in path",
            "worker": "ScriptedWorker — no model network in path",
            "issue_source": "ScriptedIssueSource",
            "telegram": "captured transport — no Telegram network",
            "stores": "IntakeStore SQLite — BEGIN IMMEDIATE + synchronous=FULL",
        },
        "network_latency_note": (
            "All external services are scripted in-process ports, so "
            "GitHub/model/Telegram network latency is excluded from the "
            "measured path by construction (0 ms in-path). External "
            "service latency is a separate operational concern and is "
            "not claimed by these local numbers."
        ),
        "reproduce": "python3 tools/probes/local_targets.py --write "
        "docs/measurements/v1-targets-$(date +%F).json",
    }


def seed_load(world, *, pending=SEED_PENDING, events=SEED_EVENTS):
    """Build the A1 fixture state on ``world``.

    ``pending`` pending work rows are created through the *real* intake
    path (``world.accept`` — the durable accept + bound task + queue
    shape production writes), then the event population is topped up to
    ``events`` rows inside one transaction so the retained-state scale
    is exact. Returns the fixture description recorded in the report.
    """
    work_keys = []
    for issue in range(1, pending + 1):
        work_keys.append(world.accept(issue, bind=False))
    with world.store.transact() as tx:
        for wk in work_keys:
            tx.enqueue_work(wk, tx.next_seq(), state="queued", reason="seeded-backlog")
    have = world.store._q("SELECT COUNT(*) FROM events").fetchone()[0]
    with world.store.transact() as tx:
        for i in range(max(0, events - have)):
            tx.record_event(
                "benchmark_seed",
                work_key=work_keys[i % len(work_keys)],
                detail=f"retained-events fixture row {i}",
            )
    total = world.store._q("SELECT COUNT(*) FROM events").fetchone()[0]
    return {
        "pending_work_keys": work_keys,
        "pending_tasks": len(work_keys),
        "seeded_marker_events": max(0, events - have),
        "real_events": have,
        "total_events": total,
        "db_bytes": os.path.getsize(world.db_path),
    }


# ---------------------------------------------------------------------------
# T1 — durable intake accept/reject p95 (A2; fixture scale per A1)
# ---------------------------------------------------------------------------


def m_intake(world, *, deliveries, rate_hz):
    """Time each ``deliver`` — handler receipt → durable accept/reject.

    ``rate_hz`` paces arrivals (the A2 sustained 1/s for 5 minutes);
    ``0`` runs back-to-back (a strictly harder load for the handler,
    used by --quick and the test suite). Returns the sample set, the
    pacing achieved and per-outcome counts.
    """
    samples, outcomes = [], {}
    issue_base = 1000
    t_start = _now()
    for i in range(deliveries):
        tick = _now()
        env = world.env(issue_base + i, channel="reconciliation")
        t0 = _now()
        ack = world.deliver(env)
        dt = _now() - t0
        samples.append(dt)
        outcomes[ack["outcome"]] = outcomes.get(ack["outcome"], 0) + 1
        if rate_hz > 0:
            period = 1.0 / rate_hz
            elapsed = _now() - tick
            if elapsed < period:
                time.sleep(period - elapsed)
    wall = _now() - t_start
    return {
        "samples_s": samples,
        "stats": _stats(samples),
        "outcomes": outcomes,
        "deliveries": deliveries,
        "rate_hz_requested": rate_hz,
        "wall_s": wall,
        "rate_hz_achieved": (deliveries / wall) if wall else None,
    }


# ---------------------------------------------------------------------------
# T2 — duplicate burst incl. restart (A2)
# ---------------------------------------------------------------------------


def m_dup_burst(world, *, n=DUPLICATE_BURST_N, restart=True):
    """``n`` duplicate deliveries inside the 10-second window: half
    replays of the original delivery id, half distinct redeliveries —
    with a controller restart mid-burst. Converges on one logical task
    and ≤ 1 remote PR."""
    issue = 2001
    ack0 = world.deliver(
        world.env(issue, channel="webhook", delivery="dlv-burst-origin")
    )
    t0 = _now()
    outcomes = {}
    for i in range(n):
        if restart and i == n // 2:
            world.reopen()  # controller restart mid-burst
        if i % 2:
            env = world.env(issue, channel="webhook", delivery="dlv-burst-origin")
        else:
            env = world.env(
                issue, channel="reconciliation", delivery=f"dlv-burst-{i:03d}"
            )
        ack = world.deliver(env)
        outcomes[ack["outcome"]] = outcomes.get(ack["outcome"], 0) + 1
    burst_s = _now() - t0
    rows = [w for w in world.store.work_rows() if w["issue"] == issue]
    remote_prs = sum(
        1
        for p in world.remote.pulls.values()
        if str(p.get("identity", {}).get("work_key") or "").startswith(
            rows[0]["work_key"] if rows else "?"
        )
    )
    return {
        "deliveries": n,
        "burst_wall_s": burst_s,
        "outcomes": outcomes,
        "initial": ack0["outcome"],
        "work_rows": len(rows),
        "remote_prs": remote_prs,
        "restart_mid_burst": restart,
        "window_bound_s": DUPLICATE_BURST_WINDOW_S,
    }


# ---------------------------------------------------------------------------
# T3 — persisted local-status p95 + explicit staleness (A3)
# ---------------------------------------------------------------------------


def m_status(world, work_key, *, queries=STATUS_QUERIES):
    """100 ``status_view`` assemblies over the persisted store."""
    samples = []
    for _ in range(queries):
        t0 = _now()
        view = _views.status_view(world.store, work_key, now=world.clock[0])
        samples.append(_now() - t0)
    return {
        "samples_s": samples,
        "stats": _stats(samples),
        "queries": queries,
        "remote_block_present": "remote" in view,
        "sample": {"work_key": work_key, "remote": view.get("remote")},
    }


# ---------------------------------------------------------------------------
# T4 — control persist within 5 s of authenticated receipt (A3)
# ---------------------------------------------------------------------------


def _control_record(store, command_id):
    for row in store.control_rows():
        if row["command_id"] == command_id:
            return row
    return None


def m_control(world, issues):
    """Pause/resume/cancel over seeded pending works. Measures wall
    handler time AND the durable record's own received→committed delta;
    Telegram transport is structurally outside the measured path (the
    record commits before any ack text is produced)."""
    samples, record_deltas, rows_out = [], [], []
    for i, issue in enumerate(issues):
        action = "pause" if i % 3 == 0 else "resume" if i % 3 == 1 else "cancel"
        if action == "resume":
            # Resume needs a held boundary — pause the same work first.
            world.control.handle(world.msg("pause", issue=issue, generation=1))
        t0 = _now()
        out = world.control.handle(world.msg(action, issue=issue, generation=1))
        samples.append(_now() - t0)
        rows_out.append(out["outcome"])
        rec = _control_record(world.store, out["command_id"])
        if rec is not None:
            record_deltas.append(_iso_delta_s(rec["committed_at"], rec["received_at"]))
    return {
        "samples_s": samples,
        "stats": _stats(samples),
        "commands": len(samples),
        "outcomes": rows_out,
        "record_commit_deltas_s": record_deltas,
        "record_commit_stats": _stats(record_deltas),
    }


# ---------------------------------------------------------------------------
# T5 — cancellation window (A4)
# ---------------------------------------------------------------------------


def _live_attempt(world, issue):
    """An active work row with a live worker handle in the lane — the
    same shape ``lane._run_sessions`` produces for a running stage.
    Returns ``(work_key, attempt_id, generation)``."""
    work_key = world.accept(issue)
    world.registrations.record_work(world.repo_id, work_key)
    work = world.store.get_work(work_key)
    world.store.acquire_lane(work_key, work["task_id"])
    attempt_id = f"{work['task_id']}-a01"
    world.store.begin_execution_attempt(
        work_key,
        attempt_id=attempt_id,
        role="implementation",
        task_id=work["task_id"],
        generation=work["generation"],
        session_id=f"s-{attempt_id}",
        runtime="hermes-kanban",
        model="gpt-6-luna",
        skills="{}",
        config_digest=work["config_digest"],
        policy_digest=work["policy_digest"],
        workspace="/w",
        limits="{}",
        now_epoch=world.clock[0],
    )
    world.lane._handles[attempt_id] = SessionHandle(
        session_id=f"s-{attempt_id}",
        attempt_id=attempt_id,
        role="implementation",
        started_epoch=world.clock[0],
    )
    return work_key, attempt_id, work["generation"]


def m_cancel(world, *, uncertain=False):
    """Cancel a live attempt; measure handler receipt → fence commit →
    termination resolution → local notification, all inside one call."""
    issue = 3002 if uncertain else 3001
    work_key, attempt_id, generation = _live_attempt(world, issue)
    if uncertain:
        world.worker.terminations[attempt_id] = "uncertain"
    t0 = _now()
    out = world.control.handle(world.msg("cancel", issue=issue, generation=generation))
    handler_s = _now() - t0
    fence = world.store.fence_state(work_key)
    alerts = world.store.alert_rows("termination-uncertain")
    notified = bool(alerts) or fence["termination"] == "confirmed"
    rec = _control_record(world.store, out["command_id"])
    if uncertain:
        retry = world.lane.request_retry(world.repo_id, issue, authorized_by="probe")
        retry_reason = retry.get("reason") or retry["outcome"]
    else:
        retry = None
        retry_reason = None
    return {
        "handler_wall_s": handler_s,
        "control_record_commit_s": _iso_delta_s(rec["committed_at"], rec["received_at"])
        if rec
        else None,
        "termination": fence["termination"],
        "fenced": fence["fenced"],
        "work_state": world.store.get_work(work_key)["state"],
        "local_notification_durable": notified,
        "uncertain_alert_rows": len(alerts),
        "outbox_rows": len([r for r in world.store.notification_rows(work_key)]),
        "replacement_eligible": world.store.replacement_eligible(work_key),
        "retry_after_uncertain": retry_reason,
        "window_bound_s": CANCEL_WINDOW_S,
    }


# ---------------------------------------------------------------------------
# T6 — reconciliation cadence + dropped-webhook discovery (A4)
# ---------------------------------------------------------------------------


def m_reconcile(world, *, dropped_issue=2100):
    """A dropped webhook: the eligible issue exists only in the polled
    set. One failed pass (GitHub down) then connectivity returns —
    discovery must land inside 120 s of the restore, absent
    upstream-directed backoff."""
    world.source.issues[dropped_issue] = {
        "title": "dropped-webhook work",
        "body": "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. x\n",
        "labels": [{"name": "factory-kit"}],
        "state": "open",
        "updated_at": "2026-10-05T01:00:00Z",
        "author": {"login": "luongnv89"},
    }
    world.source.faults["poll"] = "down"
    t0 = _now()
    failed = world.recovery.reconcile_once(source="poll")
    failed_wall_s = _now() - t0
    wm = world.store.reconcile_watermark() or {}
    backoff_s = failed.get("backoff_s")
    next_due = wm.get("next_due_epoch")
    now = world._now()
    due_in_s = (next_due - now) if next_due is not None else None
    # Connectivity returns; the next due pass must discover the work.
    del world.source.faults["poll"]
    if due_in_s is not None and due_in_s > 0:
        world.clock[0] += due_in_s
    t1 = _now()
    restored = world.recovery.tick()
    pass_wall_s = _now() - t1
    accepted_keys = [a["work_key"] for a in restored.get("accepted", [])]
    wm2 = world.store.reconcile_watermark() or {}
    clamp = RecoveryService(
        world.store, world.registrations, {world.repo_id: world.eff}, interval_s=600.0
    )
    discovery_latency_s = (due_in_s or 0.0) + pass_wall_s if accepted_keys else None
    return {
        "failed_pass_wall_s": failed_wall_s,
        "observed_on_failed_pass": failed["observed"],
        "self_backoff_s": backoff_s,
        "next_due_in_s": due_in_s,
        "restored_pass_wall_s": pass_wall_s,
        "discovery_latency_s": discovery_latency_s,
        "accepted": accepted_keys,
        "remote_state_after": wm2.get("remote_state"),
        "interval_s": world.recovery.interval_s,
        "clamped_interval_s": clamp.interval_s,
        "nominal_bound_s": RECONCILE_INTERVAL_S,
        "discovery_bound_s": DISCOVERY_BOUND_S,
        "dropped_issue": dropped_issue,
    }


# ---------------------------------------------------------------------------
# T7 — restart recovery within 60 s of readiness (A5)
# ---------------------------------------------------------------------------


def m_recovery(world):
    """A mixed known-set: pending bound, active with live heartbeat,
    active orphaned (no handle — uncertain), pending with an unbound
    task association. ``reopen`` then ``recover`` must settle every
    row inside the deadline."""
    wk_pending = world.accept(4001, bind=False)
    wk_live = world.accept(4002)
    world.registrations.record_work(world.repo_id, wk_live)
    world.store.activate_work(wk_live)
    w = world.store.get_work(wk_live)
    live_attempt = f"{w['task_id']}-a01"
    world.store.begin_execution_attempt(
        wk_live,
        attempt_id=live_attempt,
        role="implementation",
        task_id=w["task_id"],
        generation=w["generation"],
        session_id="s-live",
        runtime="hermes-kanban",
        model="m",
        skills="{}",
        config_digest=w["config_digest"],
        policy_digest=w["policy_digest"],
        workspace="/w",
        limits="{}",
        now_epoch=world.clock[0],
    )
    wk_orphan = world.accept(4003)
    world.registrations.record_work(world.repo_id, wk_orphan)
    world.store.activate_work(wk_orphan)
    w2 = world.store.get_work(wk_orphan)
    world.store.begin_execution_attempt(
        wk_orphan,
        attempt_id=f"{w2['task_id']}-a01",
        role="implementation",
        task_id=w2["task_id"],
        generation=w2["generation"],
        session_id="s-orphan",
        runtime="hermes-kanban",
        model="m",
        skills="{}",
        config_digest=w2["config_digest"],
        policy_digest=w2["policy_digest"],
        workspace="/w",
        limits="{}",
        now_epoch=world.clock[0] - 3600,
    )
    # An accepted-but-unbound row (crash before task publish).
    wk_unbound = world.deliver(world.env(4004))
    world.registrations.record_work(world.repo_id, wk_unbound["work_key"])
    with world.store.transact() as tx:
        tx._q(
            "UPDATE work SET task_state='pending' WHERE work_key=?",
            (wk_unbound["work_key"],),
        )
    expected = {wk_pending, wk_live, wk_orphan, wk_unbound["work_key"]}
    world.clock[0] += 5.0  # restart gap on the clock
    world.reopen()
    t0 = _now()
    report = world.recovery.recover(source="restart")
    wall_s = _now() - t0
    states = {w["work_key"]: w["state"] for w in world.store.work_rows()}
    # A known task is settled when the pass resumed/parked/quarantined/
    # deferred it, the lane sweep fenced it, or it is terminal now.
    covered = set()
    for bucket in ("resumed", "parked", "quarantined", "deferred"):
        covered.update(r["work_key"] for r in report[bucket])
    covered.update(f["work_key"] for f in (report.get("sweep") or {}).get("fenced", []))
    covered.update(k for k, s in states.items() if s in _limits.TERMINAL_WORK_STATES)
    unsettled = sorted(expected - covered)
    settled = len(expected) - len(unsettled)
    return {
        "wall_s": wall_s,
        "reported_duration_s": report["duration_s"],
        "within_deadline": report["within_deadline"],
        "deadline_s": report["deadline_s"],
        "expected": len(expected),
        "settled": settled,
        "unsettled": unsettled,
        "resumed": len(report["resumed"]),
        "parked": len(report["parked"]),
        "quarantined": len(report["quarantined"]),
        "deferred": len(report["deferred"]),
        "sweep_fenced": len((report.get("sweep") or {}).get("fenced", [])),
        "states": states,
        "errors": report["errors"],
    }


# ---------------------------------------------------------------------------
# T8 — crash tests lose zero acknowledged tasks (A5, §8.2 evidence)
# ---------------------------------------------------------------------------


def m_fault_evidence(path=FAULT_MATRIX_JSON):
    """Read the recorded §8.2 fault-matrix run's audit counters — the
    defined crash tests' acknowledged-task loss is a *recorded* number,
    not a fresh estimate."""
    record = json.loads(Path(path).read_text())
    counters = {}
    for row in record.get("rows", {}).values():
        for rep in row.get("reps", []):
            audit = rep.get("audit") or {}
            for k, v in (audit.get("counters") or {}).items():
                counters[k] = counters.get(k, 0) + int(v)
    return {
        "fixture": str(path),
        "verdict": record.get("verdict"),
        "rows": record.get("totals", {}).get("rows"),
        "repetitions": record.get("totals", {}).get("repetitions"),
        "passed_repetitions": record.get("totals", {}).get("passed_repetitions"),
        "audit_counters_summed": counters,
        "lost_acknowledged_tasks": counters.get("lost_acknowledged_tasks"),
    }


# ---------------------------------------------------------------------------
# T9 — idle audit: zero model calls over 30 idle minutes (A5)
# ---------------------------------------------------------------------------


def m_idle(world):
    """Advance the world clock 30 idle minutes with lane ticks and no
    eligible work; the worker port's model-call counter must stay 0
    and the durable idle_audit must agree."""
    ticks = 0
    for _ in range(6):  # 6 × 5 min = 30 min
        world.clock[0] += IDLE_WINDOW_S / 6.0
        world.lane.tick()
        ticks += 1
    audit = world.lane.idle_audit(window_s=IDLE_WINDOW_S)
    return {
        "window_s": IDLE_WINDOW_S,
        "ticks": ticks,
        "model_calls": world.worker.model_calls,
        "audit": audit,
    }


# ---------------------------------------------------------------------------
# T10 — A6 observability + structural bounds
# ---------------------------------------------------------------------------


def m_observability(world):
    """Offline/liveness/last-reconciliation state, no SLA claim, the
    CI-wait exclusion, the 24-hour wall park and the 5-minute backoff
    cap — structural checks measured against the real constants."""
    checks = []
    status = world.recovery.status()
    _check(
        checks,
        "recovery-status-fields",
        all(
            k in status
            for k in (
                "remote_state",
                "last_success_epoch",
                "consecutive_failures",
                "next_due_epoch",
                "lane",
                "process_started_epoch",
            )
        ),
        keys=sorted(status.keys()),
    )
    policy = _backoff.BackoffPolicy()
    _check(
        checks,
        "backoff-nominal-cap-300s",
        policy.delay(9) <= _backoff.BACKOFF_CAP_S,
        delay_s=policy.delay(9),
        cap=_backoff.BACKOFF_CAP_S,
    )
    _check(
        checks,
        "upstream-guidance-may-exceed",
        policy.delay(1, retry_after_s=600.0) == 600.0,
        delay_s=policy.delay(1, retry_after_s=600.0),
    )
    limits = _limits.LaneLimits()
    # CI wait is excluded by construction: only measured active_seconds
    # count — a 25h wall with 30 measured worker-minutes exhausts the
    # wall budget, never the worker budget.
    st = _limits.budget_status(
        measured_active_s=1800.0,
        unknown_usage=False,
        attempts_used=1,
        wall_elapsed_s=25 * 3600.0,
        limits=limits,
    )
    _check(
        checks,
        "ci-wait-excluded-from-worker-minutes",
        "active_worker_seconds" not in st["exceeded"]
        and "wall_seconds" in st["exceeded"],
        exceeded=st["exceeded"],
    )
    _check(
        checks,
        "wall-24h-parks",
        "wall_seconds" in st["exceeded"],
        wall_elapsed_s=st["wall_elapsed_s"],
    )
    _check(
        checks,
        "worker-minutes-budget-60",
        limits.active_worker_seconds == 3600.0,
        active_worker_seconds=limits.active_worker_seconds,
    )
    usage = _events_schema.normalize_usage(
        {"active_seconds": 120, "ci_wait_seconds": 600, "tokens": 100}
    )
    durations = (usage or {}).get("durations") or {}
    _check(
        checks,
        "wait-classes-separate-fields",
        "ci_wait_seconds" in durations and durations["ci_wait_seconds"] == 600.0,
        duration_keys=sorted(durations.keys()),
    )
    view = _views.operations_view(world.store, now=world.clock[0])
    text = _views.render_operations_text(view)
    _check(
        checks,
        "remote-observation-explicit",
        "Remote observation:" in text,
        remote=(view.get("remote") or {}).get("remote_state"),
    )
    lowered = text.lower()
    _check(
        checks,
        "no-always-on-sla-claim",
        "sla" not in lowered and "uptime" not in lowered and "always-on" not in lowered,
        checked="operations render",
    )
    # A3 — a stale remote observation must be *explicit*, never hidden:
    # one wholly-unobserved pass (poll AND targeted observe both down;
    # a partial success is still a "successful observation") anchored
    # >5 minutes past the last success, then read the labels back.
    anchor_work = world.accept(7001)
    world.source.faults["poll"] = "down"
    world.source.faults["observe"] = "down"
    world.clock[0] += _backoff.STALE_REMOTE_S + 1.0
    world.recovery.reconcile_once(source="poll")
    del world.source.faults["poll"]
    del world.source.faults["observe"]
    stale_view = (
        _views.status_view(world.store, anchor_work, now=world.clock[0])
        if anchor_work
        else {}
    )
    remote = stale_view.get("remote") or {}
    wm = world.store.reconcile_watermark() or {}
    _check(
        checks,
        "stale-remote-explicit",
        remote.get("stale") is True
        and remote.get("remote_state") == "stale"
        and wm.get("remote_state") == "stale",
        remote_state=remote.get("remote_state"),
        wm_state=wm.get("remote_state"),
        stale_alerts=len(world.store.alert_rows("reconciliation")),
    )
    return {"checks": checks, "status": status}


# ---------------------------------------------------------------------------
# T11 — the Q9 defaults table (A7)
# ---------------------------------------------------------------------------


def q9_defaults():
    """Every proposed default A7 asks the owner to accept or adjust —
    read from the constants that enforce it, with bounds."""
    return {
        "limits": dict(schema.DEFAULT_LIMITS),
        "limits_bounds": {k: list(v) for k, v in schema.LIMIT_BOUNDS.items()},
        "evidence": dict(schema.DEFAULT_EVIDENCE),
        "endpoint": dict(schema.DEFAULT_ENDPOINT),
        "preview": dict(schema.DEFAULT_PREVIEW),
        "recovery": {
            "nominal_interval_s": _backoff.NOMINAL_INTERVAL_S,
            "discovery_bound_s": _backoff.DISCOVERY_BOUND_S,
            "stale_remote_s": _backoff.STALE_REMOTE_S,
            "backoff_cap_s": _backoff.BACKOFF_CAP_S,
            "recovery_deadline_s": _backoff.RECOVERY_DEADLINE_S,
            "heartbeat_timeout_s": _limits.HEARTBEAT_TIMEOUT_S,
            "idle_audit_s": _limits.IDLE_AUDIT_S,
        },
    }


# ---------------------------------------------------------------------------
# assembly + evaluation
# ---------------------------------------------------------------------------


def evaluate(report):
    """Bind each measured section to its proposed bound → per-target
    pass/fail with margin, then the run verdict."""
    t = {}
    checks = []

    def add(target, ok, **fields):
        checks.append({"name": target, "pass": bool(ok), "measured": fields})
        return ok

    i = report["measurements"]["intake"]
    t["intake_p95"] = add(
        "intake_p95",
        i["stats"].get("p95_s", 9e9) <= INTAKE_P95_S,
        p95_s=i["stats"].get("p95_s"),
        bound_s=INTAKE_P95_S,
        margin_s=(
            INTAKE_P95_S - i["stats"]["p95_s"]
            if i["stats"].get("p95_s") is not None
            else None
        ),
        n=i["stats"].get("n"),
    )

    d = report["measurements"]["duplicate_burst"]
    t["duplicate_burst"] = add(
        "duplicate_burst",
        d["burst_wall_s"] <= DUPLICATE_BURST_WINDOW_S
        and d["work_rows"] == 1
        and d["remote_prs"] <= 1,
        burst_wall_s=d["burst_wall_s"],
        work_rows=d["work_rows"],
        remote_prs=d["remote_prs"],
        outcomes=d["outcomes"],
        window_s=DUPLICATE_BURST_WINDOW_S,
    )

    s = report["measurements"]["status"]
    t["status_p95"] = add(
        "status_p95",
        s["stats"].get("p95_s", 9e9) <= STATUS_P95_S,
        p95_s=s["stats"].get("p95_s"),
        bound_s=STATUS_P95_S,
        margin_s=(
            STATUS_P95_S - s["stats"]["p95_s"]
            if s["stats"].get("p95_s") is not None
            else None
        ),
        n=s["stats"].get("n"),
    )

    c = report["measurements"]["control"]
    commit_p95 = c["record_commit_stats"].get("p95_s")
    t["control_persist"] = add(
        "control_persist",
        c["stats"].get("p95_s", 9e9) <= CONTROL_PERSIST_S
        and (commit_p95 is not None and commit_p95 <= CONTROL_PERSIST_S),
        handler_p95_s=c["stats"].get("p95_s"),
        record_commit_p95_s=commit_p95,
        bound_s=CONTROL_PERSIST_S,
    )

    cx = report["measurements"]["cancel_confirmed"]
    cq = report["measurements"]["cancel_quarantined"]
    t["cancel_window"] = add(
        "cancel_window",
        cx["handler_wall_s"] <= CANCEL_WINDOW_S
        and cx["termination"] == "confirmed"
        and cx["replacement_eligible"]
        and cq["handler_wall_s"] <= CANCEL_WINDOW_S
        and cq["termination"] == "quarantined"
        and cq["local_notification_durable"]
        and not cq["replacement_eligible"]
        and cq["retry_after_uncertain"] == "termination-uncertain",
        confirmed_wall_s=cx["handler_wall_s"],
        quarantined_wall_s=cq["handler_wall_s"],
        bound_s=CANCEL_WINDOW_S,
        termination_confirmed=cx["termination"],
        termination_quarantined=cq["termination"],
    )

    r = report["measurements"]["reconcile"]
    t["reconcile_discovery"] = add(
        "reconcile_discovery",
        r["interval_s"] <= RECONCILE_INTERVAL_S
        and r["clamped_interval_s"] <= RECONCILE_INTERVAL_S
        and (r["self_backoff_s"] or 0) <= DISCOVERY_BOUND_S
        and (r["discovery_latency_s"] or 9e9) <= DISCOVERY_BOUND_S
        and r["accepted"],
        interval_s=r["interval_s"],
        self_backoff_s=r["self_backoff_s"],
        discovery_latency_s=r["discovery_latency_s"],
        restored_pass_wall_s=r["restored_pass_wall_s"],
        accepted=r["accepted"],
    )

    rc = report["measurements"]["recovery"]
    t["restart_recovery"] = add(
        "restart_recovery",
        rc["within_deadline"]
        and rc["reported_duration_s"] <= RECOVERY_DEADLINE_S
        and rc["wall_s"] <= RECOVERY_DEADLINE_S
        and not rc["errors"]
        and not rc["unsettled"],
        wall_s=rc["wall_s"],
        duration_s=rc["reported_duration_s"],
        deadline_s=rc["deadline_s"],
        settled=rc["settled"],
        expected=rc["expected"],
    )

    f = report["measurements"]["fault_evidence"]
    t["crash_zero_loss"] = add(
        "crash_zero_loss",
        f["verdict"] == "fault-matrix-passed"
        and f["lost_acknowledged_tasks"] == 0
        and f["passed_repetitions"] == f["repetitions"],
        lost_acknowledged_tasks=f["lost_acknowledged_tasks"],
        repetitions=f["repetitions"],
    )

    idle = report["measurements"]["idle"]
    t["idle_zero_calls"] = add(
        "idle_zero_calls",
        idle["model_calls"] == 0
        and idle["audit"]["idle"]
        and idle["audit"]["model_calls"] == 0
        and idle["audit"]["attempt_starts"] == 0,
        model_calls=idle["model_calls"],
        window_s=idle["window_s"],
    )

    obs = report["measurements"]["observability"]
    bad = [c2["name"] for c2 in obs["checks"] if not c2["pass"]]
    t["observability"] = add(
        "observability", not bad, failed=bad, checks=len(obs["checks"])
    )

    targets = {"order": list(t), "results": checks}
    verdict = "targets-passed" if all(c2["pass"] for c2 in checks) else "targets-failed"
    report["targets"] = targets
    report["verdict"] = verdict
    failed = [c2["name"] for c2 in checks if not c2["pass"]]
    report["failed_targets"] = failed
    return report


def run(
    *,
    quick=False,
    deliveries=None,
    rate_hz=None,
    pending=SEED_PENDING,
    events=SEED_EVENTS,
):
    """Execute the full §5.1 measurement suite; return the report."""
    if deliveries is None:
        deliveries = QUICK_INTAKE_DELIVERIES if quick else FULL_INTAKE_DELIVERIES
    if rate_hz is None:
        rate_hz = QUICK_INTAKE_RATE_HZ if quick else FULL_INTAKE_RATE_HZ

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = _now()
    measurements = {}

    # -- shared loaded world: A1 fixture (100 pending, 10k events) ------
    with tempfile.TemporaryDirectory(prefix="fk-targets-") as tmp:
        world = World(Path(tmp))
        fixture = seed_load(world, pending=pending, events=events)

        # T2 duplicate burst (before intake so the burst's own volume
        # is also covered by the report).
        measurements["duplicate_burst"] = m_dup_burst(world)

        # T3 status p95 over the loaded store.
        anchor = fixture["pending_work_keys"][0]
        measurements["status"] = m_status(world, anchor)

        # T1 intake p95 under the sustained rate.
        measurements["intake"] = m_intake(world, deliveries=deliveries, rate_hz=rate_hz)

        # T4 control persist on seeded pending works (fresh issues).
        measurements["control"] = m_control(world, issues=list(range(50, 50 + 30)))

        # T5 cancel windows.
        measurements["cancel_confirmed"] = m_cancel(world)
        measurements["cancel_quarantined"] = m_cancel(world, uncertain=True)

        # T9 idle audit on a dedicated world — no queued work, so the
        # 30-minute window exercises the zero-call path honestly.
    with tempfile.TemporaryDirectory(prefix="fk-targets-") as tmp:
        idle_world = World(Path(tmp))
        measurements["idle"] = m_idle(idle_world)

    # -- dedicated world: dropped-webhook reconciliation ---------------
    with tempfile.TemporaryDirectory(prefix="fk-targets-") as tmp:
        rec_world = World(Path(tmp))
        measurements["reconcile"] = m_reconcile(rec_world)
        measurements["observability"] = m_observability(rec_world)

    # -- dedicated world: restart recovery ------------------------------
    with tempfile.TemporaryDirectory(prefix="fk-targets-") as tmp:
        recov_world = World(Path(tmp))
        measurements["recovery"] = m_recovery(recov_world)

    # -- recorded crash evidence (§8.2) ---------------------------------
    measurements["fault_evidence"] = m_fault_evidence()

    report = {
        "schema": "factory-kit/local-targets/v1",
        "version": VERSION,
        "run_id": uuid.uuid4().hex[:12],
        "started": started,
        "quick": bool(quick),
        "issue": 22,
        "measurements": measurements,
        "fixture": {
            "pending_tasks": fixture["pending_tasks"],
            "total_events": fixture["total_events"],
            "seeded_marker_events": fixture["seeded_marker_events"],
            "real_events": fixture["real_events"],
            "db_bytes": fixture["db_bytes"],
        },
        "proposed_bounds": {
            "intake_p95_s": INTAKE_P95_S,
            "duplicate_burst_window_s": DUPLICATE_BURST_WINDOW_S,
            "status_p95_s": STATUS_P95_S,
            "control_persist_s": CONTROL_PERSIST_S,
            "cancel_window_s": CANCEL_WINDOW_S,
            "reconcile_interval_s": RECONCILE_INTERVAL_S,
            "discovery_bound_s": DISCOVERY_BOUND_S,
            "recovery_deadline_s": RECOVERY_DEADLINE_S,
            "idle_window_s": IDLE_WINDOW_S,
        },
        "q9_defaults": q9_defaults(),
    }
    report["elapsed_s"] = _now() - t0
    report["environment"] = host_block(world)
    return evaluate(report)


def reevaluate(record):
    """Re-bind a recorded report's measurements to the bounds — the
    same ``--fixture`` re-check ``fault_matrix.py`` offers."""
    out = dict(record)
    out.pop("targets", None)
    out.pop("verdict", None)
    out.pop("failed_targets", None)
    return evaluate(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--quick", action="store_true", help="short run: 30 unpaced intake deliveries"
    )
    ap.add_argument(
        "--intake-deliveries",
        type=int,
        default=None,
        help="override the sustained delivery count",
    )
    ap.add_argument(
        "--rate-hz", type=float, default=None, help="arrival pacing; 0 = unpaced"
    )
    ap.add_argument("--write", help="write the JSON report to a file")
    ap.add_argument("--fixture", help="re-evaluate a recorded JSON report")
    args = ap.parse_args(argv)

    if args.intake_deliveries is not None and args.intake_deliveries < 1:
        print("--intake-deliveries must be >= 1", file=sys.stderr)
        return 2

    if args.fixture:
        try:
            record = json.loads(Path(args.fixture).read_text())
        except Exception as exc:  # noqa: BLE001 — any unreadable
            # fixture is the CLI's cannot-complete, not a traceback.
            print(f"cannot read fixture: {exc}", file=sys.stderr)
            return 4
        out = reevaluate(record)
        print(json.dumps(out, indent=2))
        return 0 if out["verdict"] == "targets-passed" else 1

    try:
        report = run(
            quick=args.quick, deliveries=args.intake_deliveries, rate_hz=args.rate_hz
        )
    except Exception as exc:  # noqa: BLE001 — a measurement harness
        # maps ANY run failure to cannot-complete, honestly.
        print(f"cannot complete: {exc}", file=sys.stderr)
        return 4

    text = json.dumps(report, indent=2)
    if args.write:
        Path(args.write).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write).write_text(text + "\n")
        print(f"wrote {args.write}")
    else:
        print(text)
    return 0 if report["verdict"] == "targets-passed" else 1


if __name__ == "__main__":
    sys.exit(main())
