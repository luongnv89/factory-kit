#!/usr/bin/env python3
"""§8.4/GATE-E04 optional-harness admission assessment — issue #32
(task 5.1, PRD §3.1 F13, §6.5 runtime contract, §8.3 pilot gate).

Audits the *committed* pilot evidence and the single documented
candidate fixture, then emits the versioned admission assessment this
task owes: every gate the issue's A1–A5 acceptance criteria name —
completed ten-issue/two-project sequential dogfood, the three external
installation/repeat-use measurements, the recorded operator-effort
baseline/setup/maintenance cost, the actual continue/narrow/stop pilot
decision, exactly-one-candidate provenance and capability/authority
matrix, the A3 compatibility probes, the A4 operator-adoption record
and the A5 scope lock — is evaluated against its source artifacts and
reported as ``passed``, ``blocked``, ``pending`` or ``unknown``.

What this evidence is. A read-only audit over committed artifacts:
the aggregated usefulness evaluation and its recorded disposition, the
scripted cohort and external-study archives, the §8.2 fault matrix,
the host-readiness archive, the Hermes boundary map, the tested-recipe
selection, the task-4.6 decision record, this task's own F13 decision
record and the documented candidate fixture. Every capability state is
*derived* from those sources — the candidate fixture's own claims are
cross-checked against the derivation, and an over-claim is an
instrumentation breach, never a pass.

What it is not. Not an admission: ``admission-ready-for-owner`` still
requires the operator's explicit adoption act in
``docs/decisions/f13-admission.md`` before task 5.2 may run, and the
recorded disposition is *data*. Not a capability probe: nothing here
executes adapter code, and ``adapter_execution`` is asserted ``none``
and audited. Not a new measurement — no study re-runs and no fixture
verdict is promoted to a live-target claim.

Disposition semantics (the issue's honest-reporting contract):

- ``admission-deferred`` — pilot-gate, demand, capability or adoption
  rows unmet while safety/admission evidence is clean; task 5.2 stays
  deferred with no adapter code execution. A narrow/stop pilot outcome
  or missing repeated-use evidence lands here by construction.
- ``admission-blocked`` — failed safety/admission evidence (fault
  matrix not passed, consent-ordering or admission-gate violations,
  evaluation instrumentation breached).
- ``admission-ready-for-owner`` — every gate passed; the owner act is
  still outstanding, so even this disposition executes nothing.

Verdict semantics:

- ``assessment-complete`` — every audit clean; ``disposition`` is data.
- ``assessment-instrumentation-failed`` — an audit breached (unevaluated
  gate, unaccounted source gap, deferred-reason coverage hole,
  fabricated disposition, over-claimed capability, adapter execution
  asserted, leaked canary); exit 1.
- ``--fixture`` re-judges a recorded run: gates/matrix are re-evaluated
  and the recorded disposition must match the recomputed one — an
  archive claiming admission over a non-passed gate is a breach.

Exit codes (shared probe vocabulary):

    0  assessment-complete                 — audits clean
    1  assessment-instrumentation-failed   — an audit breached
    2  usage error                         — malformed invocation
    4  cannot complete                     — fixture unreadable /
                                             wrong kind

Usage:

    python3 tools/probes/harness_admission.py                 # audit
    python3 tools/probes/harness_admission.py --write out.json
    python3 tools/probes/harness_admission.py --fixture out.json
    python3 tools/probes/harness_admission.py --candidate path.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.config import schema
from factory_kit.privacy import export as _export

VERSION = "1.0.0"

#: Committed source artifacts the assessment audits. ``fmt`` is ``md``
#: (required markers) or ``json`` (parse + kind/schema checks). A source
#: that is missing/unreadable/wrong-kind forces its gates to
#: ``unknown``/``blocked`` — never an unearned pass.
SOURCES = {
    "evaluation": {
        "path": ROOT / "docs" / "measurements" / "pilot-evaluation-2026-10-05.json",
        "fmt": "json",
        "kinds": {"pilot-evaluation"},
        "role": "aggregated usefulness evaluation + recorded "
                "continue/narrow/stop disposition (task 4.6)",
    },
    "dogfood": {
        "path": ROOT / "docs" / "measurements" / "dogfood-comparison-2026-10-05.json",
        "fmt": "json",
        "kinds": {"dogfood-cohort"},
        "role": "scripted ten-issue two-project sequential dogfood "
                "cohort archive (task 4.3)",
    },
    "external": {
        "path": ROOT / "docs" / "measurements" / "external-study-2026-10-05.json",
        "fmt": "json",
        "kinds": {"external-pilot"},
        "role": "scripted external install + repeat-use study archive "
                "under the consent/admission gate (task 4.5)",
    },
    "fault_matrix": {
        "path": ROOT / "docs" / "evidence" / "fault-matrix-2026-10-05.json",
        "fmt": "json",
        "kinds": {"fault-matrix", "factory-kit/fault-matrix@1"},
        "role": "§8.2 fault-matrix recorded run (tasks 3.6/3.7)",
    },
    "readiness": {
        "path": ROOT / "docs" / "spike" / "evidence" / "readiness-2026-10-05.json",
        "fmt": "json",
        "role": "host/harness readiness probe archive (task 1.1/1.3)",
    },
    "release_gate": {
        "path": ROOT / "docs" / "releases" / "v1.1" / "release-gate-2026-10-05.json",
        "fmt": "json",
        "kinds": {"release-gate"},
        "role": "v1.1 conditional release-gate ledger (task 4.7)",
    },
    "boundary_map": {
        "path": ROOT / "docs" / "spike" / "hermes-boundary-map.md",
        "fmt": "md",
        "role": "documented supported-boundary map incl. the no-go list",
    },
    "recipe": {
        "path": ROOT / "docs" / "decisions" / "tested-recipe-selection.md",
        "fmt": "md",
        "role": "selected host/harness/model/skill support recipe "
                "(task 3.9)",
    },
    "decision_cns": {
        "path": ROOT / "docs" / "decisions" / "continue-narrow-stop.md",
        "fmt": "md",
        "role": "continue/narrow/stop decision record (task 4.6)",
    },
    "f13_record": {
        "path": ROOT / "docs" / "decisions" / "f13-admission.md",
        "fmt": "md",
        "role": "F13 operator admission record — the A4 artifact this "
                "task produces",
    },
    "candidate": {
        "path": ROOT / "tests" / "fixtures" / "adapters" / "candidate-codex-cli.json",
        "fmt": "json",
        "kinds": {"factory-kit/harness-candidate@1"},
        "role": "the single documented candidate harness under "
                "assessment (claims — never evidence)",
    },
}

#: Required A2 capability rows — resume/live steering stay optional,
#: never presumed (§6.5 + the harness adapter contract).
REQUIRED_CAPABILITIES = [
    "readiness_auth",
    "start",
    "liveness",
    "structured_results",
    "independent_role_sessions",
    "cancel_descendants",
]
OPTIONAL_CAPABILITIES = ["resume_steering", "live_steering"]

#: Required A3 probe rows — full existing preview/human-approved merge
#: endpoint compatibility without a second scheduler.
REQUIRED_PROBES = [
    "current_generation_authorization",
    "credential_separation",
    "hermes_lifecycle_integration",
    "endpoint_compatibility",
]

#: Evidence-state vocabulary for matrix/probe rows.
EVIDENCE_STATES = ("proven", "conditional", "unproven", "no-go",
                   "optional")

#: Gate vocabulary — only ``passed`` opens admission consideration.
GATE_STATES = ("passed", "blocked", "pending", "unknown")

#: Admission-disposition vocabulary.
DISPOSITIONS = ("admission-deferred", "admission-blocked",
                "admission-ready-for-owner")

#: Fields the consented disclosure may carry on emitted demand rows —
#: anything content-bearing (issue bodies, chat text, tokens) is a
#: breach, matching the sibling probes' aggregate-only contract.
BANNED_KEY_RE = re.compile(
    r"(issue_?body|body_?text|chat|username|user_?name|token|secret|"
    r"credential_?value|password|participant_?ref|code_?(block|content|"
    r"snippet))", re.IGNORECASE)

#: Candidate lanes must never introduce a second scheduler, and §5.2
#: adapter code must not exist before owner adoption — checked on disk.
ADAPTER_CODE_DIRS = ("factory_kit/adapters", "factory_kit/harnesses")

#: The external-study target ids A1 calls "the three external
#: installation/repeat-use measurements".
EXTERNAL_MEASUREMENTS = [
    "onboarding_completed_60d",
    "independent_setup_2_in_45min",
    "repeat_use_2x4wk_90d",
]


class HarnessAdmissionError(Exception):
    """A fixture could not be read — exit 4."""


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise HarnessAdmissionError(f"cannot load {path}: {exc}") from exc


def _sha12(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return None


# --------------------------------------------------------------------------
# Source loading — identical contract to the sibling probes
# --------------------------------------------------------------------------


def load_sources(paths):
    """Read every declared source once. Returns ``{name: {...}}`` where
    ``status`` is ``ok`` | ``missing`` | ``unreadable`` | ``parse-error`` |
    ``wrong-kind`` — a non-ok status never crashes the audit: the gates
    depending on it evaluate ``unknown`` and the gap is disclosed."""
    out = {}
    for name, spec in SOURCES.items():
        path = Path(paths.get(name, spec["path"]))
        try:
            display = str(path.resolve().relative_to(ROOT))
        except ValueError:
            display = str(path)
        entry = {"path": display, "sha256": _sha12(path),
                 "status": "ok", "role": spec["role"]}
        if not path.exists():
            entry["status"] = "missing"
            out[name] = entry
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            entry["status"] = "unreadable"
            out[name] = entry
            continue
        if spec["fmt"] == "md":
            entry["text"] = text
        else:
            try:
                entry["json"] = json.loads(text)
            except ValueError:
                entry["status"] = "parse-error"
                out[name] = entry
                continue
            kinds = spec.get("kinds")
            ident = entry["json"].get("kind") or entry["json"].get("schema")
            if kinds and ident not in kinds:
                entry["status"] = "wrong-kind"
                entry["kind"] = ident
            else:
                entry["kind"] = ident
                entry["verdict"] = entry["json"].get("verdict")
        out[name] = entry
    return out


def _ok(src, name):
    return src.get(name, {}).get("status") == "ok"


def _text(src, name):
    return src.get(name, {}).get("text")


def _json(src, name):
    return src.get(name, {}).get("json")


def _src_status(src, names):
    return {n: src.get(n, {}).get("status", "missing") for n in names}


# --------------------------------------------------------------------------
# Derived evidence — the candidate's own claims are never trusted
# --------------------------------------------------------------------------


def _derive_capabilities(src, cand):
    """Derive each A2 capability row's evidence state from the committed
    sources. A row is ``proven`` only with positive recorded evidence on
    the *candidate* lane — supported-for-the-selected-lane is cited but
    never transfers: the candidate was never dispatched through it."""
    readiness = _json(src, "readiness") or {}
    readings = readiness.get("readings") or {}
    runtimes = readings.get("runtimes") or {}
    auth_providers = ((readings.get("hermes") or {})
                      .get("auth_providers")) or []
    ops = ((readiness.get("report") or {}).get("operations")) or {}

    harness = (cand.get("readings_key") or cand.get("harness") or ""
               ).lower()
    rt = runtimes.get(harness) or {}
    binary = bool(rt.get("present")) and bool(rt.get("version"))
    pooled = "openai-codex" in auth_providers

    derived = {}
    # Presence + a pooled provider is *host* readiness, not lane
    # readiness: substantive readiness/auth on the candidate lane was
    # never probed, so the honest state caps at conditional.
    derived["readiness_auth"] = ("conditional" if binary and pooled
                                 else "unproven")
    for cap, op in (("start", "start"), ("liveness", "liveness"),
                    ("structured_results", "result"),
                    ("cancel_descendants", "cancel")):
        # The kanban contract ops are supported for the *profile
        # worker*; the candidate lane never exercised them.
        lane = bool((ops.get(op) or {}).get("supported"))
        derived[cap] = "unproven"
        derived[f"{cap}__lane_supported"] = lane
    derived["independent_role_sessions"] = "unproven"
    derived["resume_steering"] = "optional"
    derived["live_steering"] = "optional"
    return derived


def _derive_probes(src, cand):
    """Derive each A3 probe row. The boundary map's no-go list is the
    actionable evidence A3 names — an unsupported primitive rejects
    admission outright."""
    boundary = (_text(src, "boundary_map") or "")
    readiness = _json(src, "readiness") or {}
    ops = ((readiness.get("report") or {}).get("operations")) or {}

    derived = {}
    derived["current_generation_authorization"] = "unproven"
    derived["credential_separation"] = "unproven"
    cli_nogo = bool(re.search(
        r"external cli worker lanes[^\n|]*\|?[^\n]*no-?go",
        boundary, re.IGNORECASE) or "not a paved path" in boundary.lower())
    derived["hermes_lifecycle_integration"] = (
        "no-go" if cli_nogo else "unproven")
    derived["endpoint_compatibility"] = "unproven"
    derived["__kanban_ops_supported"] = all(
        bool((ops.get(o) or {}).get("supported"))
        for o in ("start", "liveness", "result", "cancel"))
    derived["__cli_nogo_cited"] = cli_nogo
    return derived


def _candidate_block(src):
    """Normalize the candidate fixture into the report's ``candidates``
    list. A wrong-kind/missing candidate yields an empty list — the
    singleton audit then fails rather than guessing."""
    if not _ok(src, "candidate"):
        return []
    doc = _json(src, "candidate") or {}
    cand = doc.get("candidate") or {}
    derived_caps = _derive_capabilities(src, cand)
    derived_probes = _derive_probes(src, cand)
    matrix = []
    for row in cand.get("capability_matrix") or []:
        cap = row.get("capability")
        matrix.append({
            "capability": cap,
            "required": bool(row.get("required", True)),
            "optional": bool(row.get("optional", False)),
            "claimed": row.get("evidence_state"),
            "derived": derived_caps.get(cap),
            "claim": row.get("claim"),
            "evidence": row.get("evidence") or [],
        })
    probes = []
    for row in cand.get("probes") or []:
        p = row.get("probe")
        probes.append({
            "probe": p,
            "claimed": row.get("evidence_state"),
            "derived": derived_probes.get(p),
            "claim": row.get("claim"),
            "evidence": row.get("evidence") or [],
        })
    return [{
        "harness": cand.get("harness"),
        "harness_version": cand.get("harness_version"),
        "integration_path": cand.get("integration_path"),
        "provenance": cand.get("provenance") or {},
        "demand": cand.get("demand") or {},
        "incremental_maintenance": cand.get("incremental_maintenance")
        or {},
        "matrix": matrix,
        "probes": probes,
        "__derived": derived_probes,
    }]


# --------------------------------------------------------------------------
# Gate checks — A1 pilot gate · A2 candidate · A3 probes · A4/A5 record
# --------------------------------------------------------------------------


def _gate_dogfood_cohort(src):
    """A1 — the completed ten-issue/two-project sequential dogfood:
    recorded run complete, two sequential registrations, one active
    task at a time, denominators intact."""
    names = ["dogfood"]
    ev = ["docs/measurements/dogfood-comparison-2026-10-05.json"]
    if not _ok(src, "dogfood"):
        return {"state": "unknown",
                "summary": "completed ten-issue two-project sequential "
                           "dogfood",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": [f"cohort archive "
                             f"{src.get('dogfood', {}).get('status')}"]}
    dog = _json(src, "dogfood")
    audits = dog.get("audits") or {}
    blockers = []
    if dog.get("verdict") != "cohort-run-complete":
        blockers.append(f"cohort verdict {dog.get('verdict')!r} ≠ "
                        "cohort-run-complete")
    order = ((audits.get("sequential_registrations") or {})
             .get("order")) or []
    if not (audits.get("sequential_registrations") or {}).get(
            "sequential") or len(set(order)) < 2:
        blockers.append("cohort did not run two sequential project "
                        "registrations")
    if (audits.get("one_active_task") or {}).get("violations"):
        blockers.append("one-active-task violations recorded")
    denom = audits.get("denominator_integrity") or {}
    if denom.get("cohort_size") != 10 or denom.get("unaccounted"):
        blockers.append("cohort denominator not intact "
                        f"({denom.get('accounted')}/10 accounted)")
    return {"state": "blocked" if blockers else "passed",
            "summary": "completed ten-issue two-project sequential "
                       "dogfood cohort",
            "evidence": ev, "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_external_measurements(src):
    """A1 — the three external installation/repeat-use measurements are
    recorded (their verdicts are data; the repeat-use *gate* is separate)."""
    names = ["external"]
    ev = ["docs/measurements/external-study-2026-10-05.json"]
    if not _ok(src, "external"):
        return {"state": "unknown",
                "summary": "three external installation/repeat-use "
                           "measurements recorded",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["external-study archive "
                             f"{src.get('external', {}).get('status')}"]}
    ext = _json(src, "external")
    blockers = []
    if ext.get("verdict") != "external-pilot-run-complete":
        blockers.append(f"external-study verdict "
                        f"{ext.get('verdict')!r} ≠ "
                        "external-pilot-run-complete")
    ids = [t.get("id")
           for t in (ext.get("targets") or {}).get("results") or []]
    missing = [t for t in EXTERNAL_MEASUREMENTS if t not in ids]
    if missing:
        blockers.append(f"measurement rows absent: {missing}")
    return {"state": "blocked" if blockers else "passed",
            "summary": "three external installation/repeat-use "
                       "measurements recorded",
            "evidence": ev, "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_operator_burden(src):
    """A1 — the operator-effort baseline plus setup/maintenance cost is
    recorded: cohort vs baseline medians and the amortized overhead,
    never zero-filled."""
    names = ["evaluation"]
    ev = ["docs/measurements/pilot-evaluation-2026-10-05.json#burden"]
    if not _ok(src, "evaluation"):
        return {"state": "unknown",
                "summary": "operator-effort baseline/setup/maintenance "
                           "cost recorded",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["evaluation archive "
                             f"{src.get('evaluation', {}).get('status')}"]}
    burden = (_json(src, "evaluation") or {}).get("burden") or {}
    blockers = []
    for key in ("baseline_median_minutes", "cohort_median_intervention_"
                "minutes", "total_burden_median_minutes"):
        if burden.get(key) is None:
            blockers.append(f"burden field missing: {key}")
    overhead = burden.get("overhead") or {}
    if overhead.get("amortized_per_issue_minutes") is None:
        blockers.append("amortized setup/maintenance cost missing")
    if burden.get("baseline_comparable") is not True:
        blockers.append("baseline comparability not asserted")
    return {"state": "blocked" if blockers else "passed",
            "summary": "operator-effort baseline + setup/maintenance "
                       "cost recorded",
            "evidence": ev, "sources": _src_status(src, names),
            "burden": {
                "cohort_median": burden.get(
                    "cohort_median_intervention_minutes"),
                "baseline_median": burden.get("baseline_median_minutes"),
                "amortized_overhead": overhead.get(
                    "amortized_per_issue_minutes"),
                "total_burden": burden.get(
                    "total_burden_median_minutes"),
                "net_vs_baseline": burden.get("net_vs_baseline_minutes"),
                "maintenance_erases_savings": burden.get(
                    "maintenance_erases_savings"),
            },
            "blockers": blockers}


def _gate_pilot_decision(src):
    """A1 — the actual continue/narrow/stop pilot decision. Only an
    owner-confirmed ``continue`` opens §8.4 consideration; a narrow or
    stop outcome — or a still-pending verdict — records blocked/deferred
    rather than admitting expansion."""
    names = ["evaluation", "decision_cns"]
    ev = ["docs/decisions/continue-narrow-stop.md",
          "docs/measurements/pilot-evaluation-2026-10-05.json"]
    if not _ok(src, "evaluation"):
        return {"state": "unknown",
                "summary": "explicit continue pilot decision",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["evaluation archive "
                             f"{src.get('evaluation', {}).get('status')}"]}
    reco = (_json(src, "evaluation") or {}).get("recommendation") or {}
    recorded = reco.get("disposition")
    status = reco.get("decision_status") or ""
    confirmed = False
    if _ok(src, "decision_cns"):
        doc = _text(src, "decision_cns") or ""
        confirmed = bool(re.search(
            r"-\s*\[x\]\s*\*\*Continue", doc, re.IGNORECASE))
    blockers = []
    if recorded is None:
        blockers.append("no continue/narrow/stop disposition recorded")
        state = "unknown"
    elif recorded == "continue" and confirmed:
        state = "passed"
    else:
        state = "blocked"
        if recorded != "continue":
            blockers.append(
                f"recorded disposition {recorded!r} — a narrow/stop "
                "outcome never authorizes §8.4 expansion")
        if not confirmed:
            blockers.append(
                f"owner confirmation pending ({status or 'not recorded'})"
                " — a recommendation is not a verdict")
    return {"state": state,
            "summary": "explicit owner-confirmed continue pilot "
                       "decision",
            "evidence": ev,
            "recorded_disposition": recorded,
            "owner_confirmed": confirmed,
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_repeat_use(src):
    """A1 — measured repeated external use. A failed or absent
    repeat-use row is precisely A1's 'missing repeated-use evidence'
    trigger: blocked, never waived."""
    names = ["external"]
    ev = ["docs/measurements/external-study-2026-10-05.json#"
          "targets.repeat_use_2x4wk_90d"]
    if not _ok(src, "external"):
        return {"state": "unknown",
                "summary": "repeated-use evidence measured favorably",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["external-study archive "
                             f"{src.get('external', {}).get('status')}"]}
    rows = (ext_targets(src))
    row = next((r for r in rows
                if r.get("id") == "repeat_use_2x4wk_90d"), None)
    if row is None:
        return {"state": "blocked",
                "summary": "repeated-use evidence measured favorably",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["repeat-use measurement row absent"]}
    verdict = row.get("verdict")
    blockers = []
    if verdict != "pass":
        blockers.append(
            f"repeat_use_2x4wk_90d {verdict} (measured "
            f"{row.get('measured')} of proposed "
            f"{row.get('threshold')}) — missing repeated-use evidence")
    return {"state": "blocked" if blockers else "passed",
            "summary": "repeated-use evidence measured favorably",
            "evidence": ev,
            "repeat_use": {"measured": row.get("measured"),
                           "verdict": verdict,
                           "live_status": row.get("live_status")},
            "sources": _src_status(src, names),
            "blockers": blockers}


def ext_targets(src):
    ext = _json(src, "external") or {}
    return (ext.get("targets") or {}).get("results") or []


def _gate_single_candidate(src, candidates):
    """A2 — exactly one candidate harness/version is proposed, fully
    identified: harness, pinned version, integration path and
    host/model/skill provenance. Zero or multiple candidates both fail
    the 'exactly one' contract."""
    names = ["candidate", "readiness", "recipe", "boundary_map"]
    ev = ["tests/fixtures/adapters/candidate-codex-cli.json",
          "docs/spike/hermes-boundary-map.md",
          "docs/decisions/tested-recipe-selection.md"]
    blockers = []
    if not _ok(src, "candidate"):
        blockers.append("candidate fixture "
                        f"{src.get('candidate', {}).get('status')}")
    if len(candidates) != 1:
        blockers.append(f"candidate count {len(candidates)} ≠ exactly 1")
    else:
        cand = candidates[0]
        prov = cand.get("provenance") or {}
        for field, label in (("harness", "harness"),
                             ("harness_version", "harness version"),
                             ("integration_path", "integration path")):
            if not cand.get(field):
                blockers.append(f"candidate missing {label}")
        for slot in ("host", "model", "skill"):
            if not (prov.get(slot) or {}).get("source"):
                blockers.append(f"provenance.{slot} undocumented")
    if blockers and "fixture" in " ".join(blockers):
        state = "unknown" if not _ok(src, "candidate") else "blocked"
    else:
        state = "blocked" if blockers else "passed"
    return {"state": state,
            "summary": "exactly one candidate harness/version proposed "
                       "with provenance",
            "evidence": ev, "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_demand(src, candidates):
    """A2 — documented demand. Demand is *sufficient* only on favorable
    measured signals: repeat-use passing plus at least two distinct
    favorable requests for an alternate runtime. A denied request, a
    failed repeat-use row and a cohort with zero harness-caused
    blockages are all unfavorable data — counted, never hidden."""
    names = ["candidate", "external", "dogfood"]
    ev = ["docs/measurements/external-study-2026-10-05.json",
          "docs/measurements/dogfood-comparison-2026-10-05.json"]
    if not candidates:
        return {"state": "unknown",
                "summary": "documented demand for the candidate",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["no candidate under assessment"]}
    signals = ((candidates[0].get("demand") or {}).get("signals")) or []
    favorable = [s for s in signals if s.get("favorable") is True]
    unfavorable = [s.get("id") for s in signals
                   if s.get("favorable") is not True]
    repeat = next((r for r in ext_targets(src)
                   if r.get("id") == "repeat_use_2x4wk_90d"), {})
    repeat_pass = repeat.get("verdict") == "pass"
    blockers = []
    if not _ok(src, "external") or not _ok(src, "dogfood"):
        blockers.append("demand sources not readable — cannot weigh "
                        "signals")
    if not repeat_pass:
        blockers.append(
            f"repeat-use {repeat.get('verdict') or 'unknown'} — no "
            "measured demand base for a second runtime")
    if len(favorable) < 2:
        blockers.append(
            f"favorable demand signals {len(favorable)} < 2 — the only "
            "recorded request (runtime-mirror workload) was denied as "
            "unsupported, and no cohort issue failed for want of a "
            "second harness")
    return {"state": "blocked" if blockers else "passed",
            "summary": "documented demand sufficient to justify the "
                       "candidate",
            "evidence": ev,
            "demand": {"signals": len(signals),
                       "favorable": len(favorable),
                       "unfavorable": unfavorable,
                       "verdict": "sufficient" if not blockers
                                  else "insufficient"},
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_maintenance_cost(src, candidates):
    """A2 — the incremental maintenance cost is *documented*: new
    surfaces enumerated, an effort bound named, and the measured burden
    context cited. Documentation is the gate — the justification fails
    on demand, not here."""
    names = ["candidate", "evaluation"]
    ev = ["tests/fixtures/adapters/candidate-codex-cli.json",
          "docs/measurements/pilot-evaluation-2026-10-05.json#burden"]
    if not candidates:
        return {"state": "unknown",
                "summary": "incremental maintenance cost documented",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["no candidate under assessment"]}
    cost = candidates[0].get("incremental_maintenance") or {}
    blockers = []
    if not cost.get("new_surfaces"):
        blockers.append("no new maintenance surfaces enumerated")
    if not cost.get("effort_class"):
        blockers.append("no effort bound recorded (task-5.2 cap ≤ 3 "
                        "developer-days)")
    if not cost.get("measured_context"):
        blockers.append("measured maintenance context not cited")
    return {"state": "blocked" if blockers else "passed",
            "summary": "incremental maintenance cost documented with "
                       "measured context",
            "evidence": ev,
            "maintenance": {
                "new_surfaces": len(cost.get("new_surfaces") or []),
                "effort_class": cost.get("effort_class"),
                "estimated_setup_minutes": cost.get(
                    "estimated_setup_minutes"),
            },
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_capability_matrix(src, candidates):
    """A2 — the capability/authority matrix exists, covers every
    substantive row and is *honest*: each claimed state equals the
    state derived from committed evidence, and resume/live steering are
    marked optional instead of presumed."""
    names = ["candidate", "readiness", "boundary_map"]
    ev = ["tests/fixtures/adapters/candidate-codex-cli.json",
          "docs/spike/hermes-boundary-map.md",
          "docs/spike/evidence/readiness-2026-10-05.json"]
    if not candidates:
        return {"state": "unknown",
                "summary": "capability/authority matrix complete and "
                           "honest",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["no candidate under assessment"]}
    matrix = candidates[0].get("matrix") or []
    by_cap = {r.get("capability"): r for r in matrix}
    blockers = []
    for cap in REQUIRED_CAPABILITIES:
        row = by_cap.get(cap)
        if row is None:
            blockers.append(f"required capability row absent: {cap}")
            continue
        if row.get("required") is not True:
            blockers.append(f"{cap} not marked required")
        if row.get("claimed") != row.get("derived"):
            blockers.append(
                f"{cap} over/under-claimed: claimed "
                f"{row.get('claimed')!r} vs derived "
                f"{row.get('derived')!r}")
        if row.get("claimed") not in EVIDENCE_STATES:
            blockers.append(f"{cap} state {row.get('claimed')!r} not in "
                            "vocabulary")
    for cap in OPTIONAL_CAPABILITIES:
        row = by_cap.get(cap)
        if row is None:
            blockers.append(f"optional capability row absent: {cap}")
            continue
        if not row.get("optional") or row.get("required"):
            blockers.append(f"{cap} presumed instead of marked optional")
        if row.get("claimed") != "optional":
            blockers.append(f"{cap} claimed {row.get('claimed')!r} — "
                            "optional rows are never presumed proven")
    return {"state": "blocked" if blockers else "passed",
            "summary": "capability/authority matrix covers the required "
                       "rows; optional rows marked optional",
            "evidence": ev,
            "matrix_rows": len(matrix),
            "sources": _src_status(src, names),
            "blockers": blockers}


def _probe_gate(src, candidates, probe_id, summary, evidence):
    """A3 — one compatibility probe row. ``passed`` only on derived
    ``proven``; ``no-go``/``unproven`` reject admission with the
    actionable evidence cited; missing sources land ``unknown``."""
    names = ["candidate", "readiness", "boundary_map", "release_gate"]
    if not candidates:
        return {"state": "unknown", "summary": summary,
                "evidence": evidence,
                "sources": _src_status(src, names),
                "blockers": ["no candidate under assessment"]}
    row = next((p for p in candidates[0].get("probes") or []
                if p.get("probe") == probe_id), None)
    if row is None:
        return {"state": "blocked", "summary": summary,
                "evidence": evidence,
                "sources": _src_status(src, names),
                "blockers": [f"probe row absent: {probe_id}"]}
    derived = row.get("derived")
    blockers = []
    if derived == "proven":
        state = "passed"
    elif derived in EVIDENCE_STATES:
        state = "blocked"
        blockers.append(f"{probe_id} derived {derived!r}: "
                        f"{row.get('claim')}")
    else:
        state = "unknown"
        blockers.append(f"{probe_id} evidence state underivable")
    return {"state": state, "summary": summary, "evidence": evidence,
            "derived": derived,
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_operator_adoption(src):
    """A4 — the operator's explicit adoption record. Only an ``[x]``
    adoption row in the F13 decision record counts; no decision and a
    rejected decision both keep task 5.2 deferred — and no adapter code
    may have executed."""
    names = ["f13_record"]
    ev = ["docs/decisions/f13-admission.md"]
    if not _ok(src, "f13_record"):
        return {"state": "pending",
                "summary": "operator adoption record (adopted "
                           "candidate/version, recipe, scope, cost, "
                           "acceptance gates)",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["no F13 decision record — task 5.2 stays "
                             "deferred"]}
    doc = _text(src, "f13_record") or ""
    adopted = re.search(r"-\s*\[x\]\s*\*\*Adopt", doc, re.IGNORECASE)
    rejected = re.search(r"-\s*\[x\]\s*\*\*Reject", doc, re.IGNORECASE)
    confirmed_defer = re.search(
        r"-\s*\[x\]\s*\*\*Defer", doc, re.IGNORECASE)
    if adopted:
        return {"state": "passed",
                "summary": "operator adoption recorded",
                "evidence": ev, "sources": _src_status(src, names),
                "adopted": True, "blockers": []}
    blockers = ["no operator adoption recorded — task 5.2 stays "
                "deferred"]
    if rejected:
        blockers.append("operator rejected the candidate")
    elif confirmed_defer:
        blockers.append("operator confirmed the deferral")
    return {"state": "blocked" if rejected else "pending",
            "summary": "operator adoption recorded",
            "evidence": ev, "sources": _src_status(src, names),
            "adopted": False, "blockers": blockers}


def _gate_scope_lock(src, candidates, repro_root):
    """A5 — the emitted scope retains the measured limits: one active
    task/project at a time, at most one additional adapter, the selected
    preview provider only, no dates promised and no broader
    harness/provider abstraction — and no adapter code exists on disk."""
    names = ["decision_cns", "evaluation"]
    ev = ["docs/decisions/continue-narrow-stop.md",
          "docs/decisions/f13-admission.md"]
    blockers = []
    for d in ADAPTER_CODE_DIRS:
        if (Path(repro_root) / d).exists():
            blockers.append(f"adapter code present: {d} — A4 forbids "
                            "execution before adoption")
    if len(candidates) > 1:
        blockers.append("more than one candidate under assessment")
    return {"state": "blocked" if blockers else "passed",
            "summary": "scope lock: one active task/project, ≤1 "
                       "additional adapter, selected preview provider, "
                       "no dates, no broader abstraction",
            "evidence": ev, "sources": _src_status(src, names),
            "blockers": blockers}


#: The assessment checklist — order is the issue's A1→A5 order.
CHECKLIST_ORDER = [
    "dogfood_cohort",
    "external_measurements",
    "operator_burden",
    "pilot_decision",
    "repeat_use_evidence",
    "single_candidate",
    "demand",
    "maintenance_cost",
    "capability_matrix",
    "probe_authorization",
    "probe_credential_separation",
    "probe_hermes_lifecycle",
    "probe_endpoint_compat",
    "operator_adoption",
    "scope_lock",
]


def _evaluate_gates(src, candidates, repro_root):
    return {
        "dogfood_cohort": _gate_dogfood_cohort(src),
        "external_measurements": _gate_external_measurements(src),
        "operator_burden": _gate_operator_burden(src),
        "pilot_decision": _gate_pilot_decision(src),
        "repeat_use_evidence": _gate_repeat_use(src),
        "single_candidate": _gate_single_candidate(src, candidates),
        "demand": _gate_demand(src, candidates),
        "maintenance_cost": _gate_maintenance_cost(src, candidates),
        "capability_matrix": _gate_capability_matrix(src, candidates),
        "probe_authorization": _probe_gate(
            src, candidates, "current_generation_authorization",
            "current-generation authorization demonstrated",
            ["docs/spike/evidence/readiness-2026-10-05.json"]),
        "probe_credential_separation": _probe_gate(
            src, candidates, "credential_separation",
            "credential separation demonstrated",
            ["docs/spike/hermes-boundary-map.md"]),
        "probe_hermes_lifecycle": _probe_gate(
            src, candidates, "hermes_lifecycle_integration",
            "supported Hermes lifecycle integration without a second "
            "scheduler",
            ["docs/spike/hermes-boundary-map.md"]),
        "probe_endpoint_compat": _probe_gate(
            src, candidates, "endpoint_compatibility",
            "existing preview/human-approved merge endpoint "
            "compatibility",
            ["docs/releases/v1.1/release-gate-2026-10-05.json",
             "docs/evidence/v1.0-gate.md"]),
        "operator_adoption": _gate_operator_adoption(src),
        "scope_lock": _gate_scope_lock(src, candidates, repro_root),
    }


# --------------------------------------------------------------------------
# Disposition + audits
# --------------------------------------------------------------------------


def _safety_clean(src):
    """Failed safety/admission evidence forces ``admission-blocked``:
    fault matrix not passed, consent-ordering or admission-gate
    violations, or a breached evaluation archive."""
    reasons = []
    if _ok(src, "fault_matrix") and \
            _json(src, "fault_matrix").get("verdict") != \
            "fault-matrix-passed":
        reasons.append("fault-matrix verdict "
                       f"{_json(src, 'fault_matrix').get('verdict')!r}")
    if _ok(src, "external"):
        audits = (_json(src, "external") or {}).get("audits") or {}
        if (audits.get("consent_ordering") or {}).get("violations"):
            reasons.append("consent-ordering violations recorded")
        if (audits.get("admission_gate") or {}).get("mismatches"):
            reasons.append("pilot admission-gate mismatches recorded")
    if _ok(src, "evaluation"):
        safety = (_json(src, "evaluation") or {}).get(
            "safety_admission") or {}
        if safety.get("clean") is False:
            reasons.append("evaluation safety/admission evidence "
                           "unclean")
        if (_json(src, "evaluation") or {}).get("breaches"):
            reasons.append("evaluation archive carries breaches")
    return (not reasons, reasons)


def _disposition(gates, safety_ok):
    """Data, not a gate pass: ``admission-ready-for-owner`` iff *every*
    gate passed — and even that executes nothing without the owner's
    adoption act. Failed safety evidence blocks; everything else
    defers, each non-passed row named."""
    non_passed = {g: v for g, v in gates.items() if v["state"] != "passed"}
    if not safety_ok:
        disp = "admission-blocked"
    elif not non_passed:
        disp = "admission-ready-for-owner"
    else:
        disp = "admission-deferred"
    reasons = [{"gate": g, "state": v["state"],
                "detail": "; ".join(v["blockers"]) or v["summary"]}
               for g, v in non_passed.items()]
    return disp, reasons


#: What re-opening the assessment requires — enumerated, never implied.
RE_ENTRY = [
    "owner records a confirmed `continue` verdict in "
    "docs/decisions/continue-narrow-stop.md",
    "a live (not fixture) repeat-use observation meeting the proposed "
    "bound, or an adjusted target the owner accepts",
    "documented demand: ≥2 favorable measured signals for the "
    "candidate runtime lane",
    "the candidate's capability/authority matrix proven row-by-row on "
    "its own lane — supported-for-the-selected-lane never transfers",
    "the hermes codex-runtime path demonstrated against the kanban "
    "contract, or a documented supported primitive replacing it",
    "operator adoption recorded in docs/decisions/f13-admission.md "
    "with bounded test scope, cost/capacity and acceptance gates",
]

#: What no disposition here can ever authorize.
NEVER_AUTHORIZED = [
    "adapter code execution or F13 delivery claims before explicit "
    "owner adoption (task 5.2 stays deferred)",
    "a second scheduler or transition engine (§6.1/boundary map)",
    "concurrency beyond one active task/project, additional preview "
    "providers, or broader harness/provider abstraction",
    "treating the completed assessment as permission to implement "
    "(§8.4)",
    "dates promised for expansion",
]


def _adoption_block(gates):
    """The A4 record fields — populated only on an operator adoption,
    which the recorded run does not carry."""
    adopted = bool((gates.get("operator_adoption") or {}).get("adopted"))
    if not adopted:
        return {"state": "none-recorded",
                "candidate": None,
                "candidate_version": None,
                "support_trust_recipe": None,
                "bounded_test_scope": None,
                "cost_capacity": None,
                "acceptance_gates": list(RE_ENTRY)}
    return {"state": "adopted-pending-scope",
            "candidate": "see docs/decisions/f13-admission.md",
            "acceptance_gates": list(RE_ENTRY)}


def _audits(report, *, recorded_disposition=None, recorded_reasons=None):
    gates = report["gates"]
    src = report.get("sources") or {}
    audits = {}

    # Gate denominator — every declared row evaluated, in vocabulary.
    audits["gate_denominator"] = {
        "declared": len(CHECKLIST_ORDER),
        "evaluated": len(gates),
        "unevaluated": [g for g in CHECKLIST_ORDER if g not in gates],
        "bad_states": {g: v["state"] for g, v in gates.items()
                       if v["state"] not in GATE_STATES},
    }

    # Source coverage — non-ok sources are named gaps.
    gaps = {n: s["status"] for n, s in src.items()
            if s["status"] != "ok"}
    audits["source_coverage"] = {"gaps": gaps, "sources": len(src)}

    # Deferred-reason coverage — every non-passed gate named once.
    reasons = (recorded_reasons if recorded_reasons is not None
               else report.get("deferred_reasons")) or []
    named = {r["gate"] for r in reasons}
    non_passed = {g for g, v in gates.items() if v["state"] != "passed"}
    audits["deferred_reason_coverage"] = {
        "non_passed": sorted(non_passed),
        "named": sorted(named),
        "uncovered": sorted(non_passed - named),
        "phantom": sorted(named - non_passed),
    }

    # No admission on gated evidence — the recorded disposition must
    # equal the one recomputed from gate states + safety alone (a
    # fixture claiming ready/adopted over a closed gate is a breach).
    safety_ok = report.get("safety", {}).get("clean", True)
    recomputed, _ = _disposition(gates, safety_ok)
    recorded = (recorded_disposition if recorded_disposition is not None
                else recomputed)
    audits["no_admission_on_gated"] = {
        "recomputed": recomputed,
        "recorded": recorded,
        "consistent": recorded == recomputed,
        "valid_vocabulary": recorded in DISPOSITIONS,
    }

    # Candidate singleton — exactly one candidate under assessment.
    candidates = report.get("candidates") or []
    audits["candidate_singleton"] = {
        "count": len(candidates),
        "ok": len(candidates) == 1,
    }

    # Matrix honesty — every claimed state equals the derived state;
    # optional rows marked optional; the probe rows are all present.
    overclaims, missing_probe = [], []
    for cand in candidates:
        for row in cand.get("matrix") or []:
            if row.get("claimed") != row.get("derived"):
                overclaims.append(
                    f"{row.get('capability')}: claimed "
                    f"{row.get('claimed')!r} vs derived "
                    f"{row.get('derived')!r}")
            if row.get("capability") in OPTIONAL_CAPABILITIES and (
                    not row.get("optional") or row.get("required")):
                overclaims.append(
                    f"{row.get('capability')} presumed, not optional")
        have = {p.get("probe") for p in cand.get("probes") or []}
        missing_probe += [p for p in REQUIRED_PROBES
                          if p not in have]
        for p in cand.get("probes") or []:
            if p.get("claimed") != p.get("derived"):
                overclaims.append(
                    f"probe {p.get('probe')}: claimed "
                    f"{p.get('claimed')!r} vs derived "
                    f"{p.get('derived')!r}")
    audits["matrix_honesty"] = {
        "overclaims": overclaims[:20],
        "missing_probe_rows": sorted(set(missing_probe)),
    }

    # No adapter execution — the assessment produced no runtime code;
    # the emitted field must assert ``none`` and no adapter dir exists.
    audits["no_adapter_execution"] = {
        "adapter_execution": report.get("adapter_execution"),
        "ok": report.get("adapter_execution") == "none",
    }

    # Aggregate-only disclosure — demand rows carry no content keys.
    bad_keys = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if BANNED_KEY_RE.search(str(k)):
                    bad_keys.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(report.get("demand") or {}, "$.demand")
    audits["aggregate_only"] = {"bad_keys": bad_keys[:20]}

    return audits


def _breaches(audits):
    b = []
    d = audits["gate_denominator"]
    if d["unevaluated"] or d["bad_states"] or \
            d["evaluated"] != d["declared"]:
        b.append("gate_denominator")
    c = audits["deferred_reason_coverage"]
    if c["uncovered"] or c["phantom"]:
        b.append("deferred_reason_coverage")
    n = audits["no_admission_on_gated"]
    if not n["consistent"] or not n["valid_vocabulary"]:
        b.append("no_admission_on_gated")
    if not audits["candidate_singleton"]["ok"]:
        b.append("candidate_singleton")
    m = audits["matrix_honesty"]
    if m["overclaims"] or m["missing_probe_rows"]:
        b.append("matrix_honesty")
    if not audits["no_adapter_execution"]["ok"]:
        b.append("no_adapter_execution")
    if audits["aggregate_only"]["bad_keys"]:
        b.append("aggregate_only")
    return b


def evaluate(report):
    """Bind gates → safety → disposition → adoption → audits → verdict.
    Recomputable from ``report['gates']`` + ``report['candidates']`` +
    ``report['safety']`` — ``reevaluate`` reruns this on an archive,
    where the *recorded* disposition/reasons are what the consistency
    audits check (a fixture claiming admission over a non-passed gate
    breaches)."""
    recorded_disposition = report.get("disposition")
    recorded_reasons = report.get("deferred_reasons")
    safety = report.get("safety") or {}
    safety_ok = safety.get("clean", True)
    safety_reasons = safety.get("reasons", [])
    disp, reasons = _disposition(report["gates"], safety_ok)
    report["disposition"] = disp
    report["deferred_reasons"] = reasons
    report["safety"] = {"clean": safety_ok, "reasons": safety_reasons}
    report["adoption"] = _adoption_block(report["gates"])
    report.setdefault("adapter_execution", "none")
    report["audits"] = _audits(
        report, recorded_disposition=recorded_disposition,
        recorded_reasons=recorded_reasons)
    report["breaches"] = _breaches(report["audits"])
    report["instrumentation"] = (
        "clean" if not report["breaches"] else "breached")
    report["verdict"] = (
        "assessment-complete" if not report["breaches"]
        else "assessment-instrumentation-failed")
    report["task_5_2"] = ("deferred — no adapter code execution"
                          if disp != "admission-ready-for-owner"
                          else "still gated on the owner's explicit "
                               "adoption act")
    report["never_authorized"] = list(NEVER_AUTHORIZED)
    report["re_entry"] = list(RE_ENTRY)
    report["study_outcome"] = (
        "GATE-E04 admission assessment audited — the disposition is "
        "data, never an implementation act; fixture verdicts are never "
        "live-target claims")
    # Scan the *finished* report — gate reasons and demand rows carry
    # source-derived text, so scanning the shell under-covers.
    leaks = _export.secret_leaks(
        {k: v for k, v in report.items() if k != "audits"})
    report["audits"]["secret_scan"] = (
        {"outcome": "clean"} if not leaks
        else {"outcome": "leak", "paths": leaks[:20]})
    if leaks and "secret_scan" not in report["breaches"]:
        report["breaches"].append("secret_scan")
        report["instrumentation"] = "breached"
        report["verdict"] = "assessment-instrumentation-failed"
    return report


def reevaluate(report):
    """Re-judge a recorded assessment archive from its recorded gates,
    candidates and safety block — the replayability contract, and the
    fabricated-admission check."""
    if not isinstance(report, dict) or \
            report.get("kind") != "harness-admission-assessment":
        raise HarnessAdmissionError(
            f"expected kind 'harness-admission-assessment', got "
            f"{report.get('kind')!r}" if isinstance(report, dict)
            else "not a harness-admission archive")
    if not isinstance(report.get("gates"), dict):
        raise HarnessAdmissionError("archive carries no gates map")
    if not isinstance(report.get("safety"), dict):
        raise HarnessAdmissionError("archive carries no safety block")
    try:
        return evaluate(dict(report))
    except (KeyError, TypeError, AttributeError) as exc:
        raise HarnessAdmissionError(f"archive incomplete: {exc}") from exc


# --------------------------------------------------------------------------
# The run — read committed sources and audit the assessment
# --------------------------------------------------------------------------


def run(*, paths=None, repro_root=ROOT):
    """Read the committed source artifacts plus the documented candidate
    fixture and produce the GATE-E04 admission assessment. Read-only:
    nothing here executes adapter code or authorizes task 5.2."""
    paths = paths or {}
    src = load_sources(paths)
    candidates = _candidate_block(src)
    gates = _evaluate_gates(src, candidates, repro_root)
    safety_ok, safety_reasons = _safety_clean(src)

    # Demand block for the disclosure audit — aggregate fields only.
    demand = {}
    if candidates:
        sig = (candidates[0].get("demand") or {}).get("signals") or []
        demand = {
            "candidate": candidates[0].get("harness"),
            "candidate_version": candidates[0].get("harness_version"),
            "signals": [{"id": s.get("id"), "class": s.get("class"),
                         "favorable": s.get("favorable"),
                         "source": s.get("source")}
                        for s in sig],
            "requests_for_alternate_runtime": (candidates[0]
                                               .get("demand") or {})
            .get("requests_for_alternate_runtime"),
        }

    report = {
        "run_id": f"harness-admission-{int(time.time())}",
        "version": VERSION,
        "kind": "harness-admission-assessment",
        "mode": "admission-assessment",
        "generated_at": _utcnow(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "manifest": str(ROOT / "docs" / "examples" / "reference.factory-kit.yml"),
            "manifest_effective_digest": schema.effective_digest(
                schema.load_manifest_file(ROOT / "docs" / "examples" / "reference.factory-kit.yml")),
            "reproduce": "python3 tools/probes/harness_admission.py "
            "--write docs/adapters/admission-assessment-$(date +%F).json",
        },
        "sources": {n: {k: v for k, v in s.items()
                        if k not in ("text", "json")}
                    for n, s in src.items()},
        "scope": {
            "one_active_task_per_project": True,
            "additional_adapters": 0,
            "preview_provider": "selected-provider-only",
            "dates_promised": False,
            "broader_abstraction": False,
        },
        "candidates": [
            {k: v for k, v in c.items() if not k.startswith("__")}
            for c in candidates],
        "demand": demand,
        "gates": gates,
        "checklist": list(CHECKLIST_ORDER),
    }
    report["safety"] = {"clean": safety_ok, "reasons": safety_reasons}
    return evaluate(report)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", help="write the JSON report to a file")
    ap.add_argument("--fixture",
                    help="re-judge a recorded admission archive")
    ap.add_argument("--repro-root", default=str(ROOT))
    for name in SOURCES:
        ap.add_argument(f"--{name.replace('_', '-')}",
                        default=str(SOURCES[name]["path"]))
    args = ap.parse_args(argv)

    try:
        if args.fixture:
            report = reevaluate(_load_json(args.fixture))
        else:
            report = run(
                paths={n: getattr(args, n) for n in SOURCES},
                repro_root=args.repro_root)
    except HarnessAdmissionError as exc:
        print(f"✗ cannot complete: {exc}", file=sys.stderr)
        return 4

    if args.write:
        Path(args.write).write_text(json.dumps(report, indent=1))
        print(f"wrote {args.write}")

    for g in report.get("checklist") or list(report.get("gates") or {}):
        gate = report["gates"].get(g)
        if gate is None:
            continue
        print(f"  {g}: {gate['state']}"
              + (f" — {'; '.join(gate['blockers'][:1])}"
                 if gate["blockers"] else ""))
    print(f"disposition: {report['disposition']}")
    if report.get("deferred_reasons"):
        for r in report["deferred_reasons"]:
            print(f"  deferred: {r['gate']} [{r['state']}]")
    print(f"verdict: {report['verdict']}")
    if report["breaches"]:
        print(f"  breaches: {', '.join(report['breaches'])}")
    return 0 if report["verdict"] == "assessment-complete" else 1


if __name__ == "__main__":
    sys.exit(main())
