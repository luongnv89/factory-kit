#!/usr/bin/env python3
"""factory-kit §1.4/§8.3 sequential dogfood cohort probe — issue #27
(task 4.3, PRD §1.4 internal-usefulness / operator-time targets, §7.1
measurement contract, §8.3 v1.1 gate).

Runs the proposed ten-issue internal usefulness cohort across **two
sequential project registrations** on the packaged scripted-port stack
(``tests/faults/harness.py`` ``World``) and compares operator burden
against a recorded ten-issue direct-IDD baseline. Every cohort issue is
driven through the *real* merged services — durable intake accept,
attempt ledger + normalized usage, independent review, remote PR +
checks, the verification gate, preview deploy, the approval decision
path and the guarded merge — while the fixture supplies the scripted
inputs (task mix, scripted outcome path, operator-recorded intervention
minutes, attempt usage). One runtime task is active at a time; the
second registration starts only after the first project's cohort is
settled.

What this evidence is. A scripted-harness execution of the cohort
protocol: real durable rows, real events, real export — with cohort
data taken from ``tests/fixtures/cohort/``. It validates the
instrumentation end-to-end (medians, denominators, wait-class
separation, missing/unknown handling, secret-free reproducible export)
and reports each proposed §1.4 target's verdict *over the recorded
fixture*.

What it is not. Not a live 30-day dogfood: no real second project, no
live Hermes runtime, no maintainer judgments on real diffs. Those are
named live-run prerequisites in the report's ``live_prerequisites``
block — a fixture ``pass`` is never a claim the live target was met.

Verdict semantics (A5's honest-reporting contract):

- ``cohort-run-complete`` — every instrumentation audit clean
  (one-active-task, sequential registrations, revision evidence,
  denominator integrity, secret-free export). Study-target verdicts
  are data, not gate results: a ``fail`` on a proposed target is a
  legitimate cohort outcome and still exits 0.
- ``instrumentation-failed`` — an audit breached; exit 1.
- Per-target verdicts: ``pass`` / ``fail`` / ``missing`` /
  ``inconclusive`` — missing records and a noncomparable baseline are
  reported, never excluded or replaced.

Exit codes (shared gi-* vocabulary):

    0  cohort-run-complete      — audits clean (study verdicts inside)
    1  instrumentation-failed   — a measurement-contract audit breached
    2  usage error              — malformed invocation
    4  cannot complete          — fixture/report could not be loaded

Usage:

    python3 tools/probes/dogfood_cohort.py                 # full run
    python3 tools/probes/dogfood_cohort.py --write out.json
    python3 tools/probes/dogfood_cohort.py --fixture out.json
    python3 tools/probes/dogfood_cohort.py --fixtures DIR  # alt fixtures
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.config import schema
from factory_kit.diagnostics import report as _report
from factory_kit.events import schema as _schema
from factory_kit.execution.worker import SessionHandle
from factory_kit.privacy import export as _export
from tests.faults.harness import CHAT, USER, World

VERSION = "1.0.0"
FIXTURES = ROOT / "tests" / "fixtures" / "cohort"

#: Scripted issue paths the cohort knows how to drive.
PATHS = (
    "merged",
    "merge_ready",
    "declined",
    "failed_attempt",
    "canceled",
    "verification_blocked",
)

#: Endpoint stages in order — the fixed issue→preview→approved→merge
#: endpoint A1 pins before selection.
ENDPOINT_STAGES = (
    "accepted",
    "preview_verified",
    "merge_ready",
    "human_approved",
    "merged",
)

#: Proposed §1.4 targets — reported exactly, per the issue's A3.
TARGET_PREVIEW_MERGE_READY = 8
TARGET_HUMAN_MERGED = 3
TARGET_INTERVENTION_REDUCTION_PCT = 25.0
COHORT_SIZE = 10
BASELINE_SIZE = 10
WINDOW_DAYS = 30

#: Proposed-target status at run time — Q9 remains unadopted.
Q9_TARGET_STATUS = (
    "proposed — pending owner acceptance (docs/decisions/q9-acceptance.md)"
)

#: The live-run prerequisites a scripted harness cannot satisfy — A5's
#: named blockers, surfaced rather than hidden.
LIVE_PREREQUISITES = [
    {
        "id": "q9-adoption",
        "blocker": "Q9 numerical targets are still proposed — owner "
        "acceptance pending (docs/decisions/q9-acceptance.md)",
    },
    {
        "id": "second-project-registration",
        "blocker": "no live second project registration with a live "
        "Hermes runtime exists — both cohort legs ran on "
        "scripted ports",
    },
    {
        "id": "thirty-day-window",
        "blocker": "the 30-day post-MVP-release observation window has "
        "not elapsed — merged/declined counts are "
        "fixture-recorded, not live-observed",
    },
    {
        "id": "human-decisions",
        "blocker": "approval/decline decisions were scripted fixture "
        "inputs through the real decision path, not maintainer "
        "judgments on real diffs",
    },
    {
        "id": "baseline-collection",
        "blocker": "baseline minutes come from the recorded fixture "
        "table — a live baseline needs ten comparable "
        "direct-IDD issues logged in the same window",
    },
]

#: A single green check missing the "Security Scan" context — the
#: verification_blocked path's fixture.
PARTIAL_CHECKS = [
    {
        "id": "1",
        "name": "Code Quality & Build",
        "status": "completed",
        "conclusion": "success",
        "app": "github-actions",
        "details_url": "https://ci.test/1",
    },
]

#: Intervention categories that count as operator intervention effort —
#: ``baseline`` is the unassisted comparison class and never appears on
#: a cohort issue.
_INTERVENTION_CATEGORIES = tuple(
    c for c in _schema.INTERVENTION_CATEGORIES if c != "baseline"
)


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_to_epoch(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise CohortError(f"cannot load {path}: {exc}") from exc


class CohortError(Exception):
    """Fixture/report could not be read — exit 4."""


# --------------------------------------------------------------------------
# Attempt / effort recording — the World fixture pattern, usage scripted
# --------------------------------------------------------------------------


def _attempt(
    world,
    work_key,
    role,
    *,
    verdict="completed",
    outcome="completed",
    usage=None,
    active_seconds=10.0,
    duration_s=10.0,
):
    """One durable finished attempt with scripted usage — the
    ``world.attempt`` fixture pattern with caller-supplied usage so the
    four §7.1 wait classes and tokens/billed/quota fields exercise the
    real normalization path (``usage=None`` records honest unknown)."""
    n = world.store.count_attempts(work_key) + 1
    task_id = world.store.get_work(work_key)["task_id"]
    aid = f"{task_id}-a{n:02d}"
    session = f"sess-{aid}"
    gen = world.store.get_work(work_key)["generation"]
    out = world.store.begin_execution_attempt(
        work_key,
        attempt_id=aid,
        role=role,
        task_id=task_id,
        generation=gen,
        session_id=session,
        runtime="hermes-kanban",
        model="gpt-6-luna",
        skills="{}",
        config_digest="c",
        policy_digest="p",
        workspace=str(world.root / "ws"),
        limits="{}",
        now_epoch=world.clock[0],
    )
    assert out["outcome"] == "active", out
    world.store.finish_execution_attempt(
        aid,
        verdict=verdict,
        outcome=outcome,
        active_seconds=active_seconds,
        usage=usage,
        duration_s=duration_s,
    )
    return aid, session


def _live_attempt(world, work_key):
    """An in-flight implementation attempt with a live lane handle —
    the ``_live_attempt`` fixture shape the cancel path terminates."""
    work = world.store.get_work(work_key)
    world.store.acquire_lane(work_key, work["task_id"])
    aid = f"{work['task_id']}-a01"
    world.store.begin_execution_attempt(
        work_key,
        attempt_id=aid,
        role="implementation",
        task_id=work["task_id"],
        generation=work["generation"],
        session_id=f"s-{aid}",
        runtime="hermes-kanban",
        model="gpt-6-luna",
        skills="{}",
        config_digest=work["config_digest"],
        policy_digest=work["policy_digest"],
        workspace=str(world.root / "ws"),
        limits="{}",
        now_epoch=world.clock[0],
    )
    world.lane._handles[aid] = SessionHandle(
        session_id=f"s-{aid}",
        attempt_id=aid,
        role="implementation",
        started_epoch=world.clock[0],
    )
    return aid, work["generation"]


def _record_interventions(world, work_key, spec):
    """Persist the issue's scripted operator-effort records through the
    real ``operator_effort`` trail — the §7.1 vocabulary keeps the
    report content-free by construction."""
    written = []
    for item in spec.get("interventions") or []:
        cat = item["category"]
        if cat not in _INTERVENTION_CATEGORIES:
            raise CohortError(f"{spec['key']}: {cat!r} is not an intervention category")
        out = world.store.record_operator_effort(
            work_key,
            actor_ref=f"telegram:{USER}",
            active_minutes=item.get("active_minutes"),
            category=cat,
            source="cohort-fixture",
        )
        written.append(out["effort_id"])
    return written


# --------------------------------------------------------------------------
# Per-issue scripted lifecycle drivers
# --------------------------------------------------------------------------


def _endpoint_runup(world, spec, work_key, sha, branch):
    """accept → impl+review attempts → review → PR → verify → preview →
    approval request: the shared F11/F12 run-up, real services only.
    Returns (evidence_status, preview_outcome, request_id)."""
    checks = spec.get("checks")
    impl_usage = spec.get("usage")
    review_usage = spec.get("review_usage", impl_usage)
    pair = {
        "impl": _attempt(
            world,
            work_key,
            "implementation",
            usage=impl_usage,
            active_seconds=spec.get("active_seconds", 600.0),
        ),
        "review": _attempt(
            world,
            work_key,
            "review",
            usage=review_usage,
            active_seconds=spec.get("review_seconds", 240.0),
        ),
    }
    world.review(work_key, pair, sha=sha)
    world.publish(work_key, sha, branch=branch, checks=checks)
    world.verify(work_key, sha)
    rec = world.store.latest_evidence(work_key)
    ev_status = rec["status"] if rec else None
    if ev_status != "verified":
        return ev_status, None, None
    dep = world.deploy(work_key, sha)
    if dep["outcome"] not in ("verified", "converged"):
        return ev_status, dep["outcome"], None
    req = world.approval.request_approval(work_key)
    if req["outcome"] != "requested":
        return ev_status, dep["outcome"], None
    return ev_status, dep["outcome"], req["request_id"]


def _run_issue(world, spec, audit):
    """Drive one cohort issue through its scripted path on the real
    services; return the per-issue measurement record."""
    issue = spec["issue"]
    sha = hashlib.sha1(f"{world.repo_id}:{issue}:{spec['key']}".encode()).hexdigest()
    branch = f"cohort/{spec['key']}"
    rec = {
        "key": spec["key"],
        "project": spec["project"],
        "issue": issue,
        "task_class": spec["task_class"],
        "path": spec["path"],
        "title": spec.get("title"),
        "stages": {
            "accepted": True,
            "preview_verified": False,
            "merge_ready": False,
            "human_approved": False,
            "merged": False,
        },
        "human_decision": None,
        "merged_at": None,
        "outcome": None,
        "store_bucket": None,
    }

    work_key = world.accept(issue, title=spec.get("title", "t"))
    rec["work_key"] = work_key
    audit["active_samples"].append(_active_count(world))

    path = spec["path"]
    if path in ("merged", "merge_ready", "declined"):
        ev_status, _dep, request_id = _endpoint_runup(
            world, spec, work_key, sha, branch
        )
        assert ev_status == "verified", (spec["key"], ev_status)
        rec["stages"]["preview_verified"] = True
        status = _report.work_status(world.store, work_key, now=world.clock[0])
        rec["stages"]["merge_ready"] = bool(
            (status.get("preview") or {}).get("approval_ready")
        )
        if path == "merge_ready":
            # The approval grant sits awaiting — the lane slot frees
            # while the human decides; revision evidence is retained.
            world.store.set_work_state(
                work_key, "parked", reason="awaiting-approval-decision"
            )
            rec["outcome"] = "merge_ready"
        else:
            dec = world.approval.decide(
                request_id=request_id,
                actor_ref=f"telegram:{USER}",
                chat_ref=f"telegram:{CHAT}",
                verdict="approve" if path == "merged" else "reject",
            )
            if path == "declined":
                assert dec["outcome"] == "rejected", dec
                rec["human_decision"] = "declined"
                rec["outcome"] = "declined"  # blocked by the real path
            else:
                assert dec["outcome"] == "approved", dec
                rec["stages"]["human_approved"] = True
                out = world.merge.merge(work_key)
                assert out["outcome"] == "merged", out
                rec["stages"]["merged"] = True
                rec["merge_sha"] = out["merge_sha"]
                obs = world.store.event_rows("merge_observed")
                mine = [e for e in obs if e["work_key"] == work_key]
                rec["merged_at"] = mine[-1]["ts"] if mine else None
                # The lane's bookkeeping role for the terminal
                # transition (fixture pattern — no dispatch tick runs).
                world.store.set_work_state(work_key, "completed")
                rec["outcome"] = "merged"
    elif path == "failed_attempt":
        _attempt(
            world,
            work_key,
            "implementation",
            verdict="failed",
            outcome="failed",
            usage=spec.get("usage"),
            active_seconds=spec.get("active_seconds", 300.0),
        )
        world.store.set_work_state(
            work_key, "blocked", reason="implementation-attempt-failed"
        )
        rec["outcome"] = "failed"
    elif path == "canceled":
        aid, gen = _live_attempt(world, work_key)
        world.control.handle(world.msg("cancel", issue=issue, generation=gen))
        # The terminated attempt's usage lands via the record close —
        # the steered-attempt pattern, honest when absent.
        world.store.end_attempt_record(
            aid,
            outcome="canceled",
            verdict="canceled",
            usage=spec.get("usage"),
            active_seconds=spec.get("active_seconds"),
            duration_s=spec.get("duration_s"),
        )
        state = world.store.get_work(work_key)["state"]
        assert state == "canceled", state
        rec["outcome"] = "canceled"
    elif path == "verification_blocked":
        spec = dict(spec, checks=PARTIAL_CHECKS)
        ev_status, _dep, _req = _endpoint_runup(world, spec, work_key, sha, branch)
        assert ev_status != "verified", (spec["key"], ev_status)
        world.store.set_work_state(
            work_key, "blocked", reason=f"verification-{ev_status}"
        )
        rec["outcome"] = "blocked"
        rec["evidence_status"] = ev_status
    else:
        raise CohortError(f"{spec['key']}: unknown path {path!r}")

    # Operator effort — every scripted intervention lands on the real
    # durable trail; an explicit "missing" marker records none (A5).
    if spec.get("effort_record") == "missing":
        rec["effort_ids"] = []
        rec["measurement"] = "missing-effort-record"
    else:
        rec["effort_ids"] = _record_interventions(world, work_key, spec)
        rec["measurement"] = "recorded"

    audit["active_samples"].append(_active_count(world))
    _fill_measurements(world, rec)
    return rec


def _active_count(world):
    return len([w for w in world.store.work_rows() if w["state"] == "active"])


def _fill_measurements(world, rec):
    """Read back the durable trail for this issue: intervention
    minutes (current, non-superseded rows), per-class waits, usage
    known/unknown, the diagnostics outcome bucket and the revision-
    evidence check."""
    store = world.store
    wk = rec["work_key"]
    effort = store.effort_rows(wk, current_only=True)
    known = [r["active_minutes"] for r in effort if r["active_minutes"] is not None]
    rec["effort_rows"] = len(effort)
    rec["intervention_minutes"] = sum(known) if effort else None
    rec["intervention_categories"] = sorted(
        {r["category"] for r in effort if r["category"]}
    )

    waits = {f: {"measured": 0.0, "unknown": 0} for f in _schema.USAGE_DURATION_FIELDS}
    usage_rows = [r for r in store._rows("attempt_usage") if r["work_key"] == wk]
    known_tokens = known_billed = known_quota = 0
    unknown_usage = 0
    for row in usage_rows:
        raw = row.get("usage")
        if raw is None:
            unknown_usage += 1
            for w in waits.values():
                w["unknown"] += 1
            continue
        norm = _schema.normalize_usage(json.loads(raw))
        if norm["tokens"]:
            known_tokens += 1
        if norm["billed"] and norm["billed"].get("amount") is not None:
            known_billed += 1
        if norm["quota"]:
            known_quota += 1
        for f, w in waits.items():
            val = norm["durations"].get(f)
            if val is None:
                w["unknown"] += 1
            else:
                w["measured"] += val
    rec["usage"] = {
        "attempts": len(usage_rows),
        "unknown": unknown_usage,
        "known_tokens": known_tokens,
        "known_billed": known_billed,
        "known_quota": known_quota,
    }
    rec["waits"] = {
        f: {"measured_s": round(w["measured"], 3), "unknown": w["unknown"]}
        for f, w in waits.items()
    }

    work = store.get_work(wk)
    rec["store_bucket"] = _report._outcome_bucket(store, work)
    ev = store.latest_evidence(wk)
    rec["evidence"] = {
        "present": ev is not None,
        "status": ev["status"] if ev else None,
        "head_sha": ev["head_sha"] if ev else None,
        "remote_head": world.remote.branches.get(f"cohort/{rec['key']}"),
    }
    rec["work_state"] = work["state"]


# --------------------------------------------------------------------------
# Audits (A2/A5) — the measurement contract itself
# --------------------------------------------------------------------------


def _audit_revision_evidence(issues):
    """A2: every issue must retain matching revision evidence (verified
    evidence whose head matches the remote head) or an explicit
    terminal/blocked state — nothing may end unaccountable."""
    matched, explicit, violations = [], [], []
    for rec in issues:
        ev = rec["evidence"]
        verified_match = (
            ev["present"]
            and ev["status"] == "verified"
            and ev["head_sha"]
            and ev["head_sha"] == ev["remote_head"]
        )
        if verified_match:
            matched.append(rec["key"])
        elif rec["work_state"] in (
            "blocked",
            "canceled",
            "quarantined",
            "completed",
            "parked",
        ):
            explicit.append(rec["key"])
        else:
            violations.append(
                {
                    "key": rec["key"],
                    "state": rec["work_state"],
                    "evidence": ev["status"],
                }
            )
    return {
        "verified_revision": matched,
        "explicit_terminal": explicit,
        "violations": violations,
    }


def _audit_denominator(issues, cohort_size):
    """A2/A5: every cohort issue stays in the denominator — blocked,
    failed, canceled and declined outcomes are reported, never dropped;
    human declines are counted separately."""
    outcomes = {}
    declines = []
    for rec in issues:
        outcomes[rec["outcome"]] = outcomes.get(rec["outcome"], 0) + 1
        if rec["human_decision"] == "declined":
            declines.append(rec["key"])
    return {
        "cohort_size": cohort_size,
        "accounted": len(issues),
        "unaccounted": [r["key"] for r in issues if not r["outcome"]],
        "outcomes": outcomes,
        "human_declines": declines,
    }


def _comparability(baseline):
    """A5's noncomparable-baseline check — each criterion stated and
    judged; a failure flips the comparison target to inconclusive."""
    comp = baseline.get("comparability") or {}
    issues = baseline.get("issues") or []
    checks = {
        "baseline_size_10": len(issues) == BASELINE_SIZE,
        "same_maintainer": bool(comp.get("same_maintainer")),
        "endpoint_standard_recorded": bool(comp.get("endpoint_standard")),
        "collection_window_days": comp.get("collection_window_days") == WINDOW_DAYS,
        "all_minutes_recorded": all(
            isinstance(i.get("intervention_minutes"), (int, float))
            and not isinstance(i.get("intervention_minutes"), bool)
            for i in issues
        ),
    }
    return {"comparable": all(checks.values()), "checks": checks}


# --------------------------------------------------------------------------
# Metrics — medians, denominators, wait classes, burden (A3/A4)
# --------------------------------------------------------------------------


def _median(values):
    vals = sorted(v for v in values if v is not None)
    return statistics.median(vals) if vals else None


def compute_metrics(issues, baseline, overhead):
    """Fold per-issue records into the §1.4 comparison numbers.

    Intervention minutes are per-issue sums of non-baseline operator
    effort; a missing effort record stays ``None`` and is *counted* as
    missing, never zero-filled — so the median is over the known set
    with the gap reported beside it."""
    per_issue = {}
    missing = []
    for rec in issues:
        per_issue[rec["key"]] = rec["intervention_minutes"]
        if rec["intervention_minutes"] is None:
            missing.append(rec["key"])
    known = [v for v in per_issue.values() if v is not None]

    successful = [
        r["intervention_minutes"]
        for r in issues
        if r["outcome"] in ("merged", "merge_ready")
    ]
    unsuccessful = [
        r["intervention_minutes"]
        for r in issues
        if r["outcome"] not in ("merged", "merge_ready")
    ]

    waits = {
        f: {"measured_s": 0.0, "unknown": 0} for f in _schema.USAGE_DURATION_FIELDS
    }
    usage_totals = {
        "attempts": 0,
        "unknown": 0,
        "known_tokens": 0,
        "known_billed": 0,
        "known_quota": 0,
    }
    for rec in issues:
        for f, w in waits.items():
            w["measured_s"] += rec["waits"][f]["measured_s"]
            w["unknown"] += rec["waits"][f]["unknown"]
        for k in usage_totals:
            usage_totals[k] += rec["usage"][k]
    for w in waits.values():
        w["measured_s"] = round(w["measured_s"], 3)

    base_minutes = [i.get("intervention_minutes") for i in baseline.get("issues", [])]
    setup = overhead.get("setup_minutes") or {}
    setup_total = sum(v for v in setup.values() if isinstance(v, (int, float)))
    maintenance = overhead.get("maintenance_minutes") or 0.0
    total_overhead = setup_total + maintenance
    amortized = (total_overhead / len(issues)) if issues else None

    cohort_median = _median(known)
    base_median = _median(base_minutes)
    reduction = None
    if cohort_median is not None and base_median:
        reduction = round((base_median - cohort_median) / base_median * 100.0, 2)

    categories = sorted(
        {cat for rec in issues for cat in rec["intervention_categories"]}
    )
    return {
        "cohort_size": len(issues),
        "intervention_minutes": {
            "per_issue": per_issue,
            "known_n": len(known),
            "missing_n": len(missing),
            "missing_keys": missing,
            "median": cohort_median,
            "median_successful": _median([v for v in successful if v is not None]),
            "median_unsuccessful": _median([v for v in unsuccessful if v is not None]),
            "successful_n": len(successful),
            "unsuccessful_n": len(unsuccessful),
        },
        "baseline": {
            "n": len(base_minutes),
            "median": base_median,
            "per_issue": {
                i["key"]: i.get("intervention_minutes")
                for i in baseline.get("issues", [])
            },
            "outcomes": {
                o: sum(1 for i in baseline.get("issues", []) if i.get("outcome") == o)
                for o in {i.get("outcome") for i in baseline.get("issues", [])}
            },
        },
        "intervention_reduction_pct": reduction,
        "overhead": {
            "setup_minutes": setup,
            "maintenance_minutes": maintenance,
            "total_overhead_minutes": total_overhead,
            "amortized_per_issue_minutes": amortized,
            "median_total_burden_minutes": (
                round(cohort_median + amortized, 3)
                if cohort_median is not None and amortized is not None
                else None
            ),
        },
        "waits": waits,
        "usage": usage_totals,
        "intervention_categories_observed": categories,
    }


# --------------------------------------------------------------------------
# Target + audit evaluation — verdict semantics per the module docstring
# --------------------------------------------------------------------------


def evaluate(report):
    """Bind measured metrics to the exact proposed §1.4 targets and
    check the instrumentation audits. Recomputable from the report's
    recorded inputs — ``reevaluate`` reruns this on an archive."""
    metrics = report["metrics"]
    issues = report["issues"]
    comparability = report["baseline_comparability"]

    merged_in_window = sum(
        1
        for r in issues
        if r["stages"]["merged"]
        and r.get("merged_at")
        and _iso_to_epoch(r["merged_at"])
        <= _iso_to_epoch(report["protocol"]["observation_window"]["declared_start"])
        + WINDOW_DAYS * 86400
    )
    merge_ready = sum(
        1
        for r in issues
        if r["stages"]["preview_verified"] and r["stages"]["merge_ready"]
    )

    results = []

    def add(tid, proposed, measured, verdict, **extra):
        row = {
            "id": tid,
            "proposed": proposed,
            "measured": measured,
            "verdict": verdict,
            "basis": "scripted-cohort-fixture",
            "live_status": "pending-live-cohort",
        }
        row.update(extra)
        results.append(row)

    if metrics["cohort_size"] < report["protocol"]["cohort_size"]:
        verdict = "missing"
    else:
        verdict = "pass" if merge_ready >= TARGET_PREVIEW_MERGE_READY else "fail"
    add(
        "preview_merge_ready",
        "at least 8 of 10 issues preview-verified and merge-ready",
        merge_ready,
        verdict,
        threshold=TARGET_PREVIEW_MERGE_READY,
        denominator=metrics["cohort_size"],
    )

    if metrics["cohort_size"] < report["protocol"]["cohort_size"]:
        verdict = "missing"
    else:
        verdict = "pass" if merged_in_window >= TARGET_HUMAN_MERGED else "fail"
    add(
        "human_approved_merged_30d",
        "at least 3 human-approved and merged within 30 days of MVP "
        "release across two sequential registrations",
        merged_in_window,
        verdict,
        threshold=TARGET_HUMAN_MERGED,
        window_days=WINDOW_DAYS,
    )

    if not comparability["comparable"]:
        verdict = "inconclusive"
    elif metrics["intervention_minutes"]["missing_n"]:
        verdict = "inconclusive"  # gap reported, denominator retained
    elif metrics["intervention_reduction_pct"] is None:
        verdict = "missing"
    else:
        verdict = (
            "pass"
            if metrics["intervention_reduction_pct"]
            >= TARGET_INTERVENTION_REDUCTION_PCT
            else "fail"
        )
    add(
        "intervention_reduction",
        "at least 25% lower median intervention minutes per issue "
        "than the ten-issue comparable baseline within the 30-day "
        "window",
        metrics["intervention_reduction_pct"],
        verdict,
        threshold_pct=TARGET_INTERVENTION_REDUCTION_PCT,
        cohort_median=metrics["intervention_minutes"]["median"],
        baseline_median=metrics["baseline"]["median"],
        missing_n=metrics["intervention_minutes"]["missing_n"],
        baseline_comparable=comparability["comparable"],
    )

    audits = dict(report.get("audits") or {})
    audits["denominator_integrity"] = _audit_denominator(
        issues, report["protocol"]["cohort_size"]
    )
    breaches = []
    if audits.get("one_active_task", {}).get("violations"):
        breaches.append("one_active_task")
    if audits.get("revision_evidence", {}).get("violations"):
        breaches.append("revision_evidence")
    if audits["denominator_integrity"]["unaccounted"]:
        breaches.append("denominator_integrity")
    if not audits.get("sequential_registrations", {}).get("sequential"):
        breaches.append("sequential_registrations")
    if (audits.get("secret_scan") or {}).get("outcome") != "clean":
        breaches.append("secret_scan")

    report["targets"] = {"order": [r["id"] for r in results], "results": results}
    report["audits"] = audits
    report["instrumentation"] = "clean" if not breaches else "breached"
    report["breaches"] = breaches
    report["verdict"] = (
        "cohort-run-complete" if not breaches else "instrumentation-failed"
    )
    report["study_outcome"] = (
        "scripted rehearsal recorded — the live §1.4 cohort stays "
        "open on the named live_prerequisites; fixture verdicts are "
        "not live-target claims"
    )
    return report


def reevaluate(report):
    """Re-check an archived report's verdicts from its own recorded
    per-issue inputs — the replayability contract (A5)."""
    return evaluate(dict(report))


# --------------------------------------------------------------------------
# The run — two sequential registrations, one active task
# --------------------------------------------------------------------------


def run(*, fixture_dir=FIXTURES):
    """Execute the scripted cohort across two sequential project
    registrations and return the measurement report."""
    fixture_dir = Path(fixture_dir)
    cohort = _load_json(fixture_dir / "cohort.json")
    baseline = _load_json(fixture_dir / "baseline.json")
    issues = cohort.get("issues") or []
    if len(issues) != cohort.get("protocol", {}).get("cohort_size", COHORT_SIZE):
        raise CohortError(
            f"cohort fixture carries {len(issues)} issues, expected "
            f"{cohort.get('protocol', {}).get('cohort_size')}"
        )
    bad = [i["key"] for i in issues if i.get("path") not in PATHS]
    if bad:
        raise CohortError(f"unknown scripted paths: {bad}")

    records = []
    audit = {"active_samples": []}
    project_reports = []
    exports = {}
    completed_slots = []

    with tempfile.TemporaryDirectory(prefix="fk-cohort-") as tmp:
        for project in cohort["projects"]:
            slot = project["slot"]
            overrides = (
                {"identity": project["identity_override"]}
                if project.get("identity_override")
                else None
            )
            wroot = Path(tmp) / slot
            wroot.mkdir(parents=True, exist_ok=True)
            world = World(wroot, eff_overrides=overrides)
            assert world.repo_id == project["repo_id"], (slot, world.repo_id)
            reg = world.registrations.get(world.repo_id)
            for spec in [i for i in issues if i["project"] == slot]:
                rec = _run_issue(world, spec, audit)
                rec["repo_id"] = world.repo_id
                records.append(rec)
            # The project leg is settled before the next registration:
            # nothing left in ``active`` — awaiting decisions are
            # parked, everything else terminal.
            lingering = [
                w["work_key"] for w in world.store.work_rows() if w["state"] == "active"
            ]
            audit["active_samples"].append(_active_count(world))
            diagnostics = _report.diagnostic_report(world.store)
            exports[slot] = _export.history_export(world.store, f"gh:{world.repo_id}")
            project_reports.append(
                {
                    "slot": slot,
                    "name": project["name"],
                    "repo_id": world.repo_id,
                    "registered": bool(reg),
                    "issues": [r["key"] for r in records if r["project"] == slot],
                    "lingering_active": lingering,
                    "diagnostics": diagnostics,
                }
            )
            completed_slots.append(slot)
            world.store.close()

    issues_records = records
    metrics = compute_metrics(issues_records, baseline, cohort.get("overhead") or {})
    report = {
        "run_id": f"dogfood-cohort-{int(time.time())}",
        "version": VERSION,
        "kind": "dogfood-cohort",
        "mode": "scripted-replay",
        "generated_at": _utcnow(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "manifest": str(ROOT / "docs" / "examples" / "reference.factory-kit.yml"),
            "manifest_effective_digest": schema.effective_digest(
                schema.load_manifest_file(ROOT / "docs" / "examples" / "reference.factory-kit.yml")
            ),
            "fixture_identity": {
                "cohort": cohort.get("fixture_id"),
                "baseline": baseline.get("fixture_id"),
                "remote": "ScriptedRemote — no GitHub network in path",
                "preview": "ScriptedPreview — no provider network",
                "worker": "ScriptedWorker — no model network",
                "stores": "IntakeStore SQLite — BEGIN IMMEDIATE",
            },
            "reproduce": "python3 tools/probes/dogfood_cohort.py "
            "--write docs/measurements/"
            "dogfood-comparison-$(date +%F).json",
        },
        "protocol": cohort.get("protocol") or {},
        "q9_target_status": Q9_TARGET_STATUS,
        "projects": project_reports,
        "issues": issues_records,
        "baseline_comparability": _comparability(baseline),
        "metrics": metrics,
        "audits": {
            "one_active_task": {
                "violations": [s for s in audit["active_samples"] if s > 1],
                "samples": len(audit["active_samples"]),
            },
            "sequential_registrations": {
                "order": completed_slots,
                "sequential": len(completed_slots) == 2
                and all(not p["lingering_active"] for p in project_reports),
            },
            "revision_evidence": _audit_revision_evidence(issues_records),
        },
        "export": exports,
        "live_prerequisites": LIVE_PREREQUISITES,
    }
    leaks = _export.secret_leaks(report)
    report["audits"]["secret_scan"] = (
        {"outcome": "clean"} if not leaks else {"outcome": "leak", "paths": leaks[:20]}
    )
    return evaluate(report)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", help="write the JSON report to a file")
    ap.add_argument("--fixture", help="re-evaluate a recorded JSON report")
    ap.add_argument(
        "--fixtures",
        default=str(FIXTURES),
        help="directory holding cohort.json + baseline.json",
    )
    args = ap.parse_args(argv)

    try:
        if args.fixture:
            report = reevaluate(_load_json(args.fixture))
        else:
            report = run(fixture_dir=args.fixtures)
    except CohortError as exc:
        print(f"✗ cannot complete: {exc}", file=sys.stderr)
        return 4

    if args.write:
        Path(args.write).write_text(json.dumps(report, indent=1))
        print(f"wrote {args.write}")

    results = report["targets"]["results"]
    for r in results:
        print(
            f"  {r['id']}: {r['verdict']} (measured {r['measured']}, "
            f"proposed {r['proposed'][:50]}…)"
        )
    print(f"verdict: {report['verdict']}")
    if report["breaches"]:
        print(f"  breaches: {', '.join(report['breaches'])}")
    return 0 if report["verdict"] == "cohort-run-complete" else 1


if __name__ == "__main__":
    sys.exit(main())
