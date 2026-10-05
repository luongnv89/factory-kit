#!/usr/bin/env python3
"""factory-kit §1.4/§8.3 usefulness evaluation — issue #30 (task 4.6,
PRD §1.4 MET04/MET05/MET06/MET07, §8.3 GATE-P02, §9.1 Q9, §9.3).

Aggregates the *recorded* internal and external measurement archives
into the single evidence table the continue / narrow / stop decision
rests on, then records the evidence-supported disposition with
measured reasons — the owner's act is confirmation, not a fabricated
acceptance.

What this evidence is. A read-only cross-check over committed
recorded runs: the §5.1 local-target run
(``docs/measurements/v1-targets-2026-10-05.json``), the scripted
ten-issue sequential dogfood cohort
(``docs/measurements/dogfood-comparison-2026-10-05.json``), the
scripted external-pilot observation study
(``docs/measurements/external-study-2026-10-05.json``) and the §8.2
fault matrix (``docs/evidence/fault-matrix-2026-10-05.json``). Every
cell in the emitted table is re-derived from those archives and
cross-checked against the verdicts they recorded — a cell that cannot
be re-derived is a breach, not a silent drop.

What it is not. Not a new measurement: nothing here re-runs the
studies, and no fixture verdict is promoted to a live-target claim.
Not the owner's decision: ``continue`` requires an explicit owner
verdict against this evidence, so the recorded disposition is a
*recommendation with measured reasons* — narrow/consolidate or stop
when the AC triggers hold — never an expansion authorization.

Verdict semantics (the issue's honest-reporting contract):

- ``evaluation-complete`` — every aggregation audit clean (cell
  cross-check, denominator integrity, missing-data coverage, takeover
  retention, Q9 recorded verbatim, secret-free report). The
  disposition is data, not a gate pass.
- ``evaluation-instrumentation-failed`` — an aggregation audit
  breached; exit 1.
- Dispositions: ``stop`` (failed safety/admission evidence),
  ``narrow-consolidate`` (failed targets or takeover /
  maintenance-erasure triggers), ``insufficient-evidence``
  (incomplete data with no failures), ``continue-eligible-pending-owner``
  (all proposed targets pass *and* nothing unresolved — still never a
  continue verdict itself).

Exit codes (shared gi-* vocabulary):

    0  evaluation-complete                — audits clean
    1  evaluation-instrumentation-failed  — an aggregation audit breached
    2  usage error                        — malformed invocation
    4  cannot complete                    — a source archive unreadable

Usage:

    python3 tools/probes/pilot_evaluation.py                # aggregate
    python3 tools/probes/pilot_evaluation.py --write out.json
    python3 tools/probes/pilot_evaluation.py --fixture out.json
    python3 tools/probes/pilot_evaluation.py --dogfood path.json ...
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.config import schema
from factory_kit.privacy import export as _export

VERSION = "1.0.0"

#: Recorded source archives (the inputs the evaluation is bound to).
SOURCES = {
    "v1_targets": ROOT / "docs" / "measurements" / "v1-targets-2026-10-05.json",
    "dogfood": ROOT / "docs" / "measurements" / "dogfood-comparison-2026-10-05.json",
    "external": ROOT / "docs" / "measurements" / "external-study-2026-10-05.json",
    "fault_matrix": ROOT / "docs" / "evidence" / "fault-matrix-2026-10-05.json",
}

#: Accepted identity per source — the older archives identify via
#: ``schema``; the study archives via ``kind``. Either must match.
EXPECTED_KINDS = {
    "v1_targets": {"v1-targets", "factory-kit/local-targets/v1"},
    "dogfood": {"dogfood-cohort"},
    "external": {"external-pilot"},
    "fault_matrix": {"fault-matrix", "factory-kit/fault-matrix@1"},
}

#: The proposed §5.1 bounds, verbatim — the archive carries bound
#: values; this table names each target the way issue ACs did.
LOCAL_TARGET_LABELS = {
    "intake_p95": "durable intake accept/reject p95 ≤ 2 s at 1 ev/s × 5 min",
    "duplicate_burst": "100 duplicate deliveries ≤ 10 s incl. restart → 1 task, ≤ 1 PR",
    "status_p95": "local status p95 ≤ 1 s over persisted rows",
    "control_persist": "authorized pause/cancel + fence persist ≤ 5 s, Telegram-independent",
    "cancel_window": "worker/descendant exit or quarantine + local notify ≤ 30 s",
    "reconcile_discovery": "reconcile interval ≤ 60 s; dropped work found ≤ 120 s after GitHub returns",
    "restart_recovery": "recover/park all known active tasks ≤ 60 s of readiness",
    "crash_zero_loss": "0 lost acknowledged tasks in defined crash tests",
    "idle_zero_calls": "0 model calls in 30 idle minutes without work/exceptions",
    "observability": "A6 structural bounds — observable, no SLA claim, CI excluded, 24 h park, 5 min cap, stale remote explicit",
}

#: Outcomes that count an issue toward the cohort's successful set —
#: mirrors dogfood_cohort.compute_metrics.
_SUCCESSFUL_OUTCOMES = ("merged", "merge_ready")

#: Scoped follow-up a narrow/consolidate disposition records (A3) —
#: the consolidation is into the already-packaged Hermes/IDD recipes,
#: never into new platform capability.
NARROW_FOLLOWUP = [
    (
        "consolidate the proven mechanics into the supported Hermes/IDD "
        "recipes — reviewed additive setup/readiness/removal, upgrade/"
        "repair/rollback, the revision-bound verification contract, "
        "one-use approval + guarded merge (docs/recipes/ is the package)"
    ),
    (
        "keep the one-active-task support limit — no concurrency or "
        "additional runtime/provider capability is admitted"
    ),
    (
        "task 5.1's optional-harness admission stays blocked — a "
        "narrow/consolidate outcome never authorizes §8.4 expansion work"
    ),
    (
        "task 4.7 ships the recipe-scoped v1.1 deliverable under its A4 "
        "release-withheld path — evidence and support recipes publish, "
        "expansion claims do not"
    ),
]

#: What any disposition here can never authorize (A3/A4) — surfaced
#: verbatim so the record cannot be read as an expansion claim.
NEVER_AUTHORIZED = [
    (
        "expansion beyond the v1.1 recipe scope (§8.4 adapters, extra "
        "runtimes/preview providers, autonomous merge)"
    ),
    (
        "external data collection or participant recruitment beyond the "
        "admitted pilot scope"
    ),
    "publication or distribution claims — Q8 rows stay unresolved",
    "treating fixture verdicts as live-target attainment",
]


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise EvaluationError(f"cannot load {path}: {exc}") from exc


class EvaluationError(Exception):
    """A source archive could not be read — exit 4."""


def _median(values):
    vals = sorted(v for v in values if v is not None)
    return statistics.median(vals) if vals else None


# --------------------------------------------------------------------------
# Extraction — pull every cell the evaluation needs out of the archives
# --------------------------------------------------------------------------


def _extract_local(v1):
    results = (v1.get("targets") or {}).get("results") or []
    rows = []
    for r in results:
        rows.append(
            {
                "name": r.get("name"),
                "pass": bool(r.get("pass")),
                "measured": r.get("measured") or {},
            }
        )
    return {
        "verdict": v1.get("verdict"),
        "results": rows,
        "failed": list(v1.get("failed_targets") or []),
    }


def _extract_cohort(dog):
    metrics = dog.get("metrics") or {}
    im = metrics.get("intervention_minutes") or {}
    overhead = metrics.get("overhead") or {}
    return {
        "targets": ((dog.get("targets") or {}).get("results")) or [],
        "metrics": {
            "cohort_size": metrics.get("cohort_size"),
            "intervention_minutes": im,
            "baseline": metrics.get("baseline") or {},
            "intervention_reduction_pct": metrics.get("intervention_reduction_pct"),
            "overhead": overhead,
            "waits": metrics.get("waits") or {},
            "usage": metrics.get("usage") or {},
            "intervention_categories_observed": metrics.get(
                "intervention_categories_observed"
            )
            or [],
        },
        "baseline_comparability": dog.get("baseline_comparability") or {},
        "audits": dog.get("audits") or {},
        "issues": [
            {
                "key": i.get("key"),
                "outcome": i.get("outcome"),
                "intervention_minutes": i.get("intervention_minutes"),
                "measurement": i.get("measurement"),
                "human_decision": i.get("human_decision"),
            }
            for i in dog.get("issues") or []
        ],
        "q9_target_status": dog.get("q9_target_status"),
        "live_prerequisites": list(dog.get("live_prerequisites") or []),
    }


def _extract_external(ext):
    participants = []
    for p in ext.get("participants") or []:
        inst = p.get("install") or {}
        participants.append(
            {
                "key": p.get("key"),
                "install_outcome": inst.get("outcome"),
                "elapsed_minutes": inst.get("elapsed_minutes"),
                "author_takeover": bool(inst.get("author_takeover")),
                "author_assistance": inst.get("author_assistance"),
                "install_blocker": inst.get("blocker"),
                "first_run": (p.get("first_run") or {}).get("outcome"),
                "repeat_use": p.get("repeat_use") or {},
                "weekly_counts": (p.get("weekly_classification") or {}).get("counts")
                or {},
                "dropout": p.get("dropout"),
            }
        )
    return {
        "targets": ((ext.get("targets") or {}).get("results")) or [],
        "participants": participants,
        "blocked": list(ext.get("blocked") or []),
        "weekly_totals": ext.get("weekly_totals") or {},
        "study_effort": ext.get("study_effort") or {},
        "audits": ext.get("audits") or {},
        "admissions": ext.get("admissions") or {},
        "q9_target_status": ext.get("q9_target_status"),
        "live_prerequisites": list(ext.get("live_prerequisites") or []),
    }


def _extract_fault(fault):
    return {
        "verdict": fault.get("verdict"),
        "totals": fault.get("totals") or {},
        "failed_rows": list(fault.get("failed_rows") or []),
    }


# --------------------------------------------------------------------------
# Unified evidence rows (A1) — every proposed numerical target, one row
# --------------------------------------------------------------------------


def _local_denominator(name, measured):
    """Window + denominator text re-derived from the measured cell."""
    window = denom = None
    if name == "intake_p95":
        window = "5 min sustained at 1 ev/s (paced 302.0 s wall)"
        denom = f"n={measured.get('n')} deliveries"
    elif name == "duplicate_burst":
        window = f"{measured.get('window_s')} s burst incl. store rebuild"
        denom = "100 duplicate deliveries"
    elif name == "status_p95":
        denom = f"n={measured.get('n')} persisted-row queries"
    elif name == "control_persist":
        denom = "n=30 control actions (pause/resume/cancel)"
    elif name == "cancel_window":
        window = "30 s bound on confirm-or-quarantine + notify"
    elif name == "reconcile_discovery":
        window = "interval ≤ 60 s / discovery ≤ 120 s bounds"
    elif name == "restart_recovery":
        denom = (
            f"{measured.get('settled')}/{measured.get('expected')} known tasks settled"
        )
    elif name == "crash_zero_loss":
        denom = f"{measured.get('repetitions')} fault-matrix repetitions"
    elif name == "idle_zero_calls":
        window = f"{int((measured.get('window_s') or 0) / 60)} idle minutes"
    elif name == "observability":
        denom = f"{measured.get('checks')} mechanism checks"
    return window, denom


def _local_rows(local):
    rows = []
    for r in local["results"]:
        measured = r["measured"]
        window, denom = _local_denominator(r["name"], measured)
        rows.append(
            {
                "id": f"local.{r['name']}",
                "scope": "internal-local",
                "proposed": LOCAL_TARGET_LABELS.get(r["name"], r["name"]),
                "measured": measured,
                "verdict": "pass" if r["pass"] else "fail",
                "basis": "selected-host measured run",
                "live_status": "measured-local — scripted ports, single host",
                "window": window,
                "denominator": denom,
                "missing": [],
                "source": "docs/measurements/v1-targets-2026-10-05.json",
            }
        )
    return rows


def _cohort_rows(cohort):
    """The three §1.4 internal targets — missing data folded in from
    the recorded metrics, never from memory."""
    rows = []
    missing_effort = [
        k
        for k, v in (
            cohort["metrics"]["intervention_minutes"].get("per_issue") or {}
        ).items()
        if v is None
    ]
    for r in cohort["targets"]:
        row = {
            "id": f"cohort.{r['id']}",
            "scope": "internal-cohort",
            "proposed": r.get("proposed"),
            "measured": r.get("measured"),
            "verdict": r.get("verdict"),
            "basis": r.get("basis"),
            "live_status": r.get("live_status"),
            "window": (
                f"{r.get('window_days')} days"
                if r.get("window_days")
                else "30-day post-MVP window"
            ),
            "denominator": (
                f"{r.get('denominator')} cohort issues"
                if r.get("denominator")
                else "10-issue cohort / 10-issue baseline"
            ),
            "missing": [],
            "source": "docs/measurements/dogfood-comparison-2026-10-05.json",
        }
        if r["id"] == "intervention_reduction":
            row["missing"] = [
                {
                    "key": k,
                    "what": "operator effort record",
                    "effect": "median over n=9 known — reduction verdict "
                    "stays inconclusive, never zero-filled",
                }
                for k in missing_effort
            ]
        rows.append(row)
    return rows


def _pilot_rows(external):
    """The three §1.4 external targets — takeover exclusions, blocked
    admissions and missing weeks kept visible per row."""
    rows = []
    participants = external["participants"]
    install_blocked = [
        p["key"] for p in participants if p["install_outcome"] != "completed"
    ]
    takeovers = [p["key"] for p in participants if p["author_takeover"]]
    weekly_missing = {
        p["key"]: (p["weekly_counts"].get("missing") or 0)
        for p in participants
        if (p["weekly_counts"].get("missing") or 0) > 0
    }
    blocked_keys = [b["key"] for b in external["blocked"]]

    for r in external["targets"]:
        row = {
            "id": f"pilot.{r['id']}",
            "scope": "external-pilot",
            "proposed": r.get("proposed"),
            "measured": r.get("measured"),
            "verdict": r.get("verdict"),
            "basis": r.get("basis"),
            "live_status": r.get("live_status"),
            "window": f"{r.get('window_days')} days" if r.get("window_days") else None,
            "denominator": (
                f"{r.get('denominator')} admitted participants"
                if r.get("denominator")
                else None
            ),
            "missing": [],
            "excluded": [],
            "source": "docs/measurements/external-study-2026-10-05.json",
        }
        if r["id"] == "onboarding_completed_60d":
            row["missing"] = [
                {
                    "key": k,
                    "what": "install blocked — "
                    + next(
                        (p["install_blocker"] for p in participants if p["key"] == k),
                        "unknown",
                    ),
                    "effect": "retained in denominator, excluded from numerator",
                }
                for k in install_blocked
            ]
            row["excluded"] = [
                {
                    "key": k,
                    "why": next(
                        (b.get("reason") for b in external["blocked"] if b["key"] == k),
                        "not admitted",
                    ),
                }
                for k in blocked_keys
            ]
        elif r["id"] == "independent_setup_2_in_45min":
            row["excluded"] = [
                {
                    "key": k,
                    "why": "author takeover — counts toward onboarding, "
                    "never toward independence",
                }
                for k in takeovers
            ]
            row["missing"] = [
                {
                    "key": k,
                    "what": "setup never completed",
                    "effect": "retained in denominator, excluded from numerator",
                }
                for k in install_blocked
            ]
        elif r["id"] == "repeat_use_2x4wk_90d":
            row["missing"] = [
                {
                    "key": k,
                    "what": f"{n} weekly report(s) missing"
                    + (
                        " — dropout recorded"
                        if next(
                            (p["dropout"] for p in participants if p["key"] == k),
                            None,
                        )
                        else ""
                    ),
                    "effect": "labeled absence — streaks never inferred",
                }
                for k, n in weekly_missing.items()
            ]
            row["failed_runs_retained"] = r.get("failed_runs_retained")
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# Burden comparison (A2) and intervention signals (A3)
# --------------------------------------------------------------------------


def _burden(cohort, external):
    im = cohort["metrics"]["intervention_minutes"]
    baseline = cohort["metrics"]["baseline"]
    overhead = cohort["metrics"]["overhead"]
    base_median = baseline.get("median")
    cohort_median = im.get("median")
    total_burden = overhead.get("median_total_burden_minutes")
    net = (
        round(total_burden - base_median, 3)
        if total_burden is not None and base_median is not None
        else None
    )
    return {
        "cohort_median_intervention_minutes": cohort_median,
        "cohort_known_n": im.get("known_n"),
        "cohort_missing": list(im.get("missing_keys") or []),
        "baseline_median_minutes": base_median,
        "baseline_n": baseline.get("n"),
        "baseline_comparable": (cohort["baseline_comparability"] or {}).get(
            "comparable"
        ),
        "reduction_pct": cohort["metrics"]["intervention_reduction_pct"],
        "reduction_verdict": next(
            (
                r.get("verdict")
                for r in cohort["targets"]
                if r.get("id") == "intervention_reduction"
            ),
            None,
        ),
        "successful_median": im.get("median_successful"),
        "unsuccessful_median": im.get("median_unsuccessful"),
        "overhead": overhead,
        "total_burden_median_minutes": total_burden,
        "net_vs_baseline_minutes": net,
        "maintenance_erases_savings": bool(net is not None and net >= 0),
        "external_builder_active_minutes": (external["study_effort"] or {}).get(
            "builder_active_minutes"
        ),
        "external_waits": (external["study_effort"] or {}).get("waits") or {},
        "usage": cohort["metrics"].get("usage") or {},
        "waits": cohort["metrics"].get("waits") or {},
        "uncertainty": [
            (
                "scripted-harness medians over n=9 known cohort issues "
                "vs n=10 recorded baseline — one missing effort record"
            ),
            (
                "scripted ports: no network/CI jitter in the measured "
                "path; live operators may differ"
            ),
            (
                "takeover and assistance minutes are fixture-recorded "
                "observations, not independently timed"
            ),
        ],
    }


def _intervention_signals(cohort, external):
    takeovers = [p["key"] for p in external["participants"] if p["author_takeover"]]
    assists = [
        p["key"]
        for p in external["participants"]
        if p["author_assistance"] not in (None, "none")
    ]
    admitted = len(external["participants"])
    im = cohort["metrics"]["intervention_minutes"]
    return {
        "author_takeovers": takeovers,
        "author_takeover_n": len(takeovers),
        "admitted_participants": admitted,
        "assistance_events": assists,
        "cohort_intervention_categories": cohort["metrics"][
            "intervention_categories_observed"
        ],
        "cohort_unsuccessful_median_minutes": im.get("median_unsuccessful"),
        "cohort_successful_median_minutes": im.get("median_successful"),
        "repeated_author_intervention": bool(takeovers),
        "note": "a takeover is excluded from the independence numerator "
        "but never hidden — it counts as an author-intervention signal",
    }


# --------------------------------------------------------------------------
# Safety/admission evidence + unresolved items (A4)
# --------------------------------------------------------------------------


def _safety_admission(cohort, external, fault):
    pa = external["audits"]
    ca = cohort["audits"]
    reasons = []
    if (pa.get("consent_ordering") or {}).get("violations"):
        reasons.append("pilot consent_ordering violations present")
    if (pa.get("admission_gate") or {}).get("mismatches"):
        reasons.append("pilot admission_gate mismatches present")
    if (pa.get("observation_minimization") or {}).get("violations"):
        reasons.append("pilot observation_minimization violations present")
    if (pa.get("denominator_integrity") or {}).get("unaccounted"):
        reasons.append("pilot denominator unaccounted participants")
    if (pa.get("secret_scan") or {}).get("outcome") != "clean":
        reasons.append("pilot secret_scan not clean")
    if (ca.get("one_active_task") or {}).get("violations"):
        reasons.append("cohort one_active_task violations")
    if not (ca.get("sequential_registrations") or {}).get("sequential"):
        reasons.append("cohort registrations not sequential")
    if (ca.get("revision_evidence") or {}).get("violations"):
        reasons.append("cohort revision_evidence violations")
    if (ca.get("denominator_integrity") or {}).get("unaccounted"):
        reasons.append("cohort denominator unaccounted issues")
    if (ca.get("secret_scan") or {}).get("outcome") != "clean":
        reasons.append("cohort secret_scan not clean")
    if fault["verdict"] != "fault-matrix-passed":
        reasons.append(f"fault matrix verdict {fault['verdict']!r} ≠ passed")
    return {
        "clean": not reasons,
        "reasons": reasons,
        "fault_matrix_verdict": fault["verdict"],
        "fault_matrix_totals": fault["totals"],
        "pilot_audits": {
            "admission_gate": pa.get("admission_gate"),
            "consent_ordering_violations": len(
                (pa.get("consent_ordering") or {}).get("violations") or []
            ),
            "minimization_violations": len(
                (pa.get("observation_minimization") or {}).get("violations") or []
            ),
            "denominator_integrity": pa.get("denominator_integrity"),
            "secret_scan": (pa.get("secret_scan") or {}).get("outcome"),
        },
        "cohort_audits_clean": {
            "one_active_task": not (ca.get("one_active_task") or {}).get("violations"),
            "sequential": bool(
                (ca.get("sequential_registrations") or {}).get("sequential")
            ),
            "revision_evidence": not (ca.get("revision_evidence") or {}).get(
                "violations"
            ),
            "denominator": not (ca.get("denominator_integrity") or {}).get(
                "unaccounted"
            ),
            "secret_scan": (ca.get("secret_scan") or {}).get("outcome") == "clean",
        },
    }


def _unresolved(cohort, external, rows):
    """Open decisions and required next observations — deduplicated by
    id, with every row that carries the item named in ``rows``."""
    merged = {}

    def add(item):
        slot = merged.setdefault(item["id"], dict(item, rows=[]))
        for src in item.get("rows") or []:
            if src not in slot["rows"]:
                slot["rows"].append(src)

    q9 = cohort.get("q9_target_status") or external.get("q9_target_status")
    if q9 and "pending" in q9:
        add(
            {
                "id": "q9-adoption",
                "kind": "owner-decision",
                "state": q9,
                "required": "owner accept/adjust/reject verdict in "
                "docs/decisions/q9-acceptance.md",
            }
        )
    for scope, prereqs in (
        ("cohort", cohort.get("live_prerequisites") or []),
        ("pilot", external.get("live_prerequisites") or []),
    ):
        for prereq in prereqs:
            if prereq["id"] == "q9-adoption":
                continue  # already carried once above
            doc = (
                "docs/measurements/dogfood-comparison.md"
                if scope == "cohort"
                else "docs/measurements/external-study.md"
            )
            add(
                {
                    "id": f"{scope}.{prereq['id']}",
                    "kind": "next-observation",
                    "state": prereq.get("blocker"),
                    "required": f"live {scope} observation — see {doc}",
                }
            )
    for row in rows:
        for m in row.get("missing") or []:
            add(
                {
                    "id": f"missing.{m['key']}",
                    "kind": "missing-data",
                    "state": m["what"],
                    "required": m.get("effect"),
                    "rows": [row["id"]],
                }
            )
    return list(merged.values())


# --------------------------------------------------------------------------
# Aggregation audits — the evaluation's own honesty contract
# --------------------------------------------------------------------------


def _audit_crosscheck(evidence, rows):
    """Every emitted cell re-derives from the recorded archive cells —
    a disagreement is a breach, never a silent correction."""
    mismatches = []
    cohort = evidence["cohort"]
    im = cohort["metrics"]["intervention_minutes"]
    known = [v for v in (im.get("per_issue") or {}).values() if v is not None]
    if _median(known) != im.get("median"):
        mismatches.append("cohort intervention median")
    base = [
        v
        for v in (cohort["metrics"]["baseline"].get("per_issue") or {}).values()
        if v is not None
    ]
    if _median(base) != cohort["metrics"]["baseline"].get("median"):
        mismatches.append("baseline median")
    cm, bm = im.get("median"), cohort["metrics"]["baseline"].get("median")
    if cm is not None and bm:
        pct = round((bm - cm) / bm * 100.0, 2)
        if pct != cohort["metrics"]["intervention_reduction_pct"]:
            mismatches.append("intervention reduction pct")
    overhead = cohort["metrics"]["overhead"]
    if (
        cm is not None
        and overhead.get("amortized_per_issue_minutes") is not None
        and overhead.get("median_total_burden_minutes") is not None
        and round(cm + overhead["amortized_per_issue_minutes"], 3)
        != overhead["median_total_burden_minutes"]
    ):
        mismatches.append("median total burden")

    external = evidence["external"]
    by_key = {p["key"]: p for p in external["participants"]}
    for r in external["targets"]:
        if r["id"] == "repeat_use_2x4wk_90d":
            qualified = sum(
                1
                for p in external["participants"]
                if (p["repeat_use"] or {}).get("qualified")
            )
            if qualified != r.get("measured"):
                mismatches.append("repeat-use qualified count")
        if r["id"] == "independent_setup_2_in_45min":
            declared = set(r.get("takeover_excluded") or [])
            actual = {k for k, p in by_key.items() if p["author_takeover"]}
            if declared != actual:
                mismatches.append("takeover exclusion set")

    local = evidence["local"]
    all_pass = all(r["pass"] for r in local["results"])
    if (local["verdict"] == "targets-passed") != all_pass:
        mismatches.append("v1-targets verdict vs rows")

    emitted = {r["id"]: r for r in rows}
    if len(emitted) != len(rows):
        mismatches.append("duplicate evidence row ids")
    return {"mismatches": mismatches}


def _audit_denominators(evidence, rows):
    """Every source denominator arrives intact in the table."""
    problems = []
    cohort = evidence["cohort"]
    den = cohort["audits"].get("denominator_integrity") or {}
    if den.get("unaccounted"):
        problems.append(f"cohort unaccounted: {den['unaccounted']}")
    pden = evidence["external"]["audits"].get("denominator_integrity") or {}
    if pden.get("unaccounted"):
        problems.append(f"pilot unaccounted: {pden['unaccounted']}")
    expected_rows = (
        len(evidence["local"]["results"])
        + len(cohort["targets"])
        + len(evidence["external"]["targets"])
    )
    if len(rows) != expected_rows:
        problems.append(f"evidence rows {len(rows)} != source targets {expected_rows}")
    for r in rows:
        if r["scope"] == "internal-cohort" and r.get("measured") is None:
            problems.append(f"{r['id']}: measured missing")
    return {"expected_rows": expected_rows, "problems": problems}


def _audit_missing_coverage(evidence, rows):
    """Every known missing/unfavorable datum is *named* in some row —
    the table may not quietly absorb gaps."""
    expected = set()
    for k, v in (
        evidence["cohort"]["metrics"]["intervention_minutes"].get("per_issue") or {}
    ).items():
        if v is None:
            expected.add(k)
    for p in evidence["external"]["participants"]:
        if p["install_outcome"] != "completed":
            expected.add(p["key"])
        if (p["weekly_counts"].get("missing") or 0) > 0:
            expected.add(p["key"])
        if p["author_takeover"]:
            expected.add(p["key"])
    for b in evidence["external"]["blocked"]:
        expected.add(b["key"])

    named = set()
    for r in rows:
        for m in r.get("missing") or []:
            named.add(m["key"])
        for e in r.get("excluded") or []:
            named.add(e["key"])
    return {
        "expected_named": sorted(expected),
        "named": sorted(named),
        "uncovered": sorted(expected - named),
    }


def _audit_takeover(evidence, signals):
    takeovers = set(signals["author_takeovers"])
    row = next(
        (
            r
            for r in evidence["external"]["targets"]
            if r["id"] == "independent_setup_2_in_45min"
        ),
        {},
    )
    declared = set(row.get("takeover_excluded") or [])
    return {
        "takeovers": sorted(takeovers),
        "independence_excluded": sorted(declared),
        "hidden": sorted(takeovers - declared),
    }


def _recommendation(rows, burden, signals, safety, unresolved):
    """A3/A4: the evidence-supported disposition. `continue` is never
    computed — at best the evidence is *eligible* for an owner verdict."""
    failed = [r["id"] for r in rows if r["verdict"] == "fail"]
    inconclusive = [r["id"] for r in rows if r["verdict"] == "inconclusive"]
    missing_rows = [r["id"] for r in rows if r.get("missing")]

    reasons = []
    if not safety["clean"]:
        reasons.extend(safety["reasons"])
        disposition = "stop"
    elif (
        failed
        or signals["repeated_author_intervention"]
        or burden["maintenance_erases_savings"]
    ):
        disposition = "narrow-consolidate"
        for r in rows:
            if r["verdict"] == "fail":
                reasons.append(
                    f"{r['id']}: measured {r['measured']} — proposed {r['proposed']}"
                )
        if burden["maintenance_erases_savings"]:
            reasons.append(
                f"integration overhead erases the per-issue saving: "
                f"cohort median {burden['cohort_median_intervention_minutes']} "
                f"min + amortized setup/maintenance "
                f"{burden['overhead'].get('amortized_per_issue_minutes')} "
                f"min = {burden['total_burden_median_minutes']} min/issue "
                f"vs baseline median {burden['baseline_median_minutes']} "
                f"min (net {burden['net_vs_baseline_minutes']:+} min)"
            )
        if signals["repeated_author_intervention"]:
            reasons.append(
                f"author intervention required: takeover "
                f"{signals['author_takeovers']} "
                f"({signals['author_takeover_n']} of "
                f"{signals['admitted_participants']} admitted); "
                f"recorded assistance {signals['assistance_events']}"
            )
    elif inconclusive or missing_rows or unresolved:
        disposition = "insufficient-evidence"
        reasons.append(
            "incomplete data blocks a positive claim: inconclusive "
            f"{inconclusive}, missing in {missing_rows}, "
            f"unresolved {len(unresolved)}"
        )
    else:
        disposition = "continue-eligible-pending-owner"
        reasons.append(
            "all proposed targets pass on the recorded evidence and no "
            "unresolved items remain — the continue verdict itself "
            "stays the owner's act"
        )

    return {
        "disposition": disposition,
        "decision_status": "recorded — pending owner confirmation",
        "continue_requires": "explicit owner decision against the "
        "assembled evidence (issue A3)",
        "reasons": reasons,
        "failed_targets": failed,
        "inconclusive_targets": inconclusive,
        "scoped_followup": (
            NARROW_FOLLOWUP if disposition == "narrow-consolidate" else []
        ),
        "never_authorized": NEVER_AUTHORIZED,
        "expansion": "not authorized",
    }


def _audit_no_positive_claim(reco, rows, unresolved, safety):
    """A4: with any failure, gap or unresolved item the disposition may
    not read as a positive/expansion claim."""
    problems = []
    blocked = bool(
        unresolved
        or not safety["clean"]
        or any(r["verdict"] in ("fail", "inconclusive", "missing") for r in rows)
    )
    if blocked and reco["disposition"] == "continue-eligible-pending-owner":
        problems.append("continue-eligible emitted over incomplete evidence")
    if reco.get("expansion") != "not authorized":
        problems.append("expansion field drifted")
    if reco.get("decision_status") != "recorded — pending owner confirmation":
        problems.append("owner-pending marker dropped")
    return {"problems": problems, "positive_claim_blocked": blocked}


def _audit_q9(evidence, q9):
    """Q9 adoption/adjustment is recorded verbatim — the evaluation
    never retroactively writes a baseline difference away."""
    problems = []
    if not q9.get("status"):
        problems.append("q9 status absent")
    c = evidence["cohort"].get("q9_target_status")
    e = evidence["external"].get("q9_target_status")
    if c and e and c != e:
        problems.append("cohort/pilot q9 status disagree")
    if c and q9.get("status") != c:
        problems.append("q9 status not carried verbatim")
    return {"problems": problems}


# --------------------------------------------------------------------------
# evaluate / reevaluate — recomputable from the recorded evidence block
# --------------------------------------------------------------------------


def evaluate(report):
    """Bind extracted evidence to the unified table, audits and the
    recorded disposition. Recomputable from ``report['evidence']`` —
    ``reevaluate`` reruns this on an archive."""
    evidence = report["evidence"]
    rows = (
        _local_rows(evidence["local"])
        + _cohort_rows(evidence["cohort"])
        + _pilot_rows(evidence["external"])
    )
    burden = _burden(evidence["cohort"], evidence["external"])
    signals = _intervention_signals(evidence["cohort"], evidence["external"])
    safety = _safety_admission(
        evidence["cohort"], evidence["external"], evidence["fault_matrix"]
    )
    unresolved = _unresolved(evidence["cohort"], evidence["external"], rows)

    c9 = evidence["cohort"].get("q9_target_status") or evidence["external"].get(
        "q9_target_status"
    )
    q9 = {
        "status": c9,
        "record": "docs/decisions/q9-acceptance.md",
        "adjustments": [],
        "note": "proposed targets/defaults stay *proposed* until the "
        "owner records a verdict — no retroactive baseline edits",
    }

    reco = _recommendation(rows, burden, signals, safety, unresolved)

    audits = {
        "cell_crosscheck": _audit_crosscheck(evidence, rows),
        "denominator_integrity": _audit_denominators(evidence, rows),
        "missing_data_coverage": _audit_missing_coverage(evidence, rows),
        "takeover_retained": _audit_takeover(evidence, signals),
        "no_positive_claim_on_incomplete": _audit_no_positive_claim(
            reco, rows, unresolved, safety
        ),
        "q9_recorded": _audit_q9(evidence, q9),
    }

    breaches = []
    if audits["cell_crosscheck"]["mismatches"]:
        breaches.append("cell_crosscheck")
    if audits["denominator_integrity"]["problems"]:
        breaches.append("denominator_integrity")
    if audits["missing_data_coverage"]["uncovered"]:
        breaches.append("missing_data_coverage")
    if audits["takeover_retained"]["hidden"]:
        breaches.append("takeover_retained")
    if audits["no_positive_claim_on_incomplete"]["problems"]:
        breaches.append("no_positive_claim_on_incomplete")
    if audits["q9_recorded"]["problems"]:
        breaches.append("q9_recorded")

    report["targets"] = {"order": [r["id"] for r in rows], "results": rows}
    report["q9"] = q9
    report["burden"] = burden
    report["intervention_signals"] = signals
    report["safety_admission"] = safety
    report["unresolved"] = unresolved
    report["recommendation"] = reco
    report["audits"] = audits
    report["breaches"] = breaches
    report["instrumentation"] = "clean" if not breaches else "breached"
    report["verdict"] = (
        "evaluation-complete" if not breaches else "evaluation-instrumentation-failed"
    )
    report["study_outcome"] = (
        "recorded evidence aggregated — fixture verdicts are never "
        "live-target claims; the disposition is a measured "
        "recommendation pending owner confirmation"
    )
    return report


def reevaluate(report):
    """Re-check an archived evaluation from its recorded evidence — the
    replayability contract."""
    return evaluate(dict(report))


# --------------------------------------------------------------------------
# The run — load the recorded archives and aggregate
# --------------------------------------------------------------------------


def run(*, sources=None):
    """Read the four recorded archives and produce the evaluation
    report. Read-only: nothing here re-executes a study."""
    sources = dict(SOURCES if sources is None else sources)
    archives = {}
    for name, path in sources.items():
        payload = _load_json(path)
        identity = payload.get("kind") or payload.get("schema")
        if identity not in EXPECTED_KINDS[name]:
            raise EvaluationError(
                f"{path}: expected one of {sorted(EXPECTED_KINDS[name])}, "
                f"got {identity!r}"
            )
        archives[name] = payload

    evidence = {
        "local": _extract_local(archives["v1_targets"]),
        "cohort": _extract_cohort(archives["dogfood"]),
        "external": _extract_external(archives["external"]),
        "fault_matrix": _extract_fault(archives["fault_matrix"]),
    }

    report = {
        "run_id": f"pilot-evaluation-{int(time.time())}",
        "version": VERSION,
        "kind": "pilot-evaluation",
        "mode": "archive-aggregation",
        "generated_at": _utcnow(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "manifest": str(ROOT / ".factory-kit.yml"),
            "manifest_effective_digest": schema.effective_digest(
                schema.load_manifest_file(ROOT / ".factory-kit.yml")
            ),
            "source_identity": {
                name: {
                    "path": str(sources[name]),
                    "run_id": archives[name].get("run_id"),
                    "verdict": archives[name].get("verdict"),
                }
                for name in SOURCES
            },
            "reproduce": "python3 tools/probes/pilot_evaluation.py "
            "--write docs/measurements/"
            "pilot-evaluation-$(date +%F).json",
        },
        "evidence": evidence,
    }

    report = evaluate(report)
    # Scan the *finished* report — derived rows and reason strings carry
    # archive-sourced text, so scanning the shell would under-cover.
    leaks = _export.secret_leaks(report)
    report["audits"]["secret_scan"] = (
        {"outcome": "clean"} if not leaks else {"outcome": "leak", "paths": leaks[:20]}
    )
    if leaks and "secret_scan" not in report["breaches"]:
        report["breaches"].append("secret_scan")
        report["instrumentation"] = "breached"
        report["verdict"] = "evaluation-instrumentation-failed"
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", help="write the JSON report to a file")
    ap.add_argument("--fixture", help="re-evaluate a recorded JSON report")
    ap.add_argument("--v1-targets", default=str(SOURCES["v1_targets"]))
    ap.add_argument("--dogfood", default=str(SOURCES["dogfood"]))
    ap.add_argument("--external", default=str(SOURCES["external"]))
    ap.add_argument("--fault-matrix", default=str(SOURCES["fault_matrix"]))
    args = ap.parse_args(argv)

    try:
        if args.fixture:
            report = reevaluate(_load_json(args.fixture))
        else:
            report = run(
                sources={
                    "v1_targets": args.v1_targets,
                    "dogfood": args.dogfood,
                    "external": args.external,
                    "fault_matrix": args.fault_matrix,
                }
            )
    except EvaluationError as exc:
        print(f"✗ cannot complete: {exc}", file=sys.stderr)
        return 4

    if args.write:
        Path(args.write).write_text(json.dumps(report, indent=1))
        print(f"wrote {args.write}")

    results = report["targets"]["results"]
    for r in results:
        print(f"  {r['id']}: {r['verdict']} (measured {r['measured']})")
    print(f"disposition: {report['recommendation']['disposition']}")
    print(f"verdict: {report['verdict']}")
    if report["breaches"]:
        print(f"  breaches: {', '.join(report['breaches'])}")
    return 0 if report["verdict"] == "evaluation-complete" else 1


if __name__ == "__main__":
    sys.exit(main())
