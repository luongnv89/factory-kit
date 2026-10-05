#!/usr/bin/env python3
"""v1.1 conditional release gate — issue #31 (task 4.7, PRD §3.2
F09/F10, §8.3 GATE-P03/GATE-P06, §9.1 Q8).

Audits the *committed* source evidence behind the conditional v1.1
release package and emits the release checklist verdict the package
records: every gate the issue's A2 checklist names — passed v1.0
gate, consent/trust admission, recorded pilot observations, explicit
continue/narrow/stop decision and resolved Q8 distribution decisions —
plus the GATE-P03 F09/F10 acceptance-evidence and GATE-P06 package
gates is evaluated against its source artifacts and reported as
``passed``, ``blocked``, ``pending`` or ``unknown``.

What this evidence is. A read-only audit over committed artifacts:
the v1.0 acceptance/gate-audit records, the pilot consent/admission
record, the recorded external-study archive, the continue/narrow/stop
decision record, the Q8 distribution record, the §8.2 fault-matrix
archive and the packaged recipes. A gate that cannot be positively
verified is never waved through: ``blocked``/``pending``/``unknown``
all keep the release withheld — only ``passed`` opens it, and the
recorded disposition is *data*, never a publication act.

What it is not. Not a release: ``release-ready`` means the checklist
permits publication, not that anything was published — public
release remains the owner's act under the session's authorization.
Not a new measurement: nothing re-runs a study and no fixture
verdict is promoted to a live-target claim. Not an expansion
authorization of any kind — the withheld path ships the
recipe-scoped deliverable (issue A4), and no concurrency, runtime,
provider or adapter capability is admitted by it.

Verdict semantics (the issue's honest-reporting contract):

- ``release-check-complete`` — every audit clean; ``disposition`` is
  then ``release-ready`` (all gates passed) or ``release-withheld``
  with ``withheld_reasons`` naming each non-passed gate.
- ``release-check-instrumentation-failed`` — an audit breached
  (unevaluated gate, undisclosed source gap, withheld reason
  coverage hole, fabricated ``release-ready``, leaked canary); exit 1.
- ``--fixture`` re-judges a recorded run: the recorded gates are
  re-aggregated and the recorded verdict must match — a fixture
  claiming ``release-ready`` over a non-passed gate is a breach.

Exit codes (shared probe vocabulary):

    0  release-check-complete                — audits clean
    1  release-check-instrumentation-failed  — an audit breached
    2  usage error                           — malformed invocation
    4  cannot complete                       — fixture unreadable /
                                               wrong kind

Usage:

    python3 tools/probes/release_gate.py                 # audit
    python3 tools/probes/release_gate.py --write out.json
    python3 tools/probes/release_gate.py --fixture out.json
    python3 tools/probes/release_gate.py --q8 path.md ...  # overrides
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

#: Committed source artifacts the checklist audits. ``fmt`` is ``md``
#: (required markers) or ``json`` (parse + kind/verdict checks).
SOURCES = {
    "v1_acceptance": {
        "path": ROOT / "docs" / "decisions" / "v1.0-acceptance.md",
        "fmt": "md",
        "role": "v1.0 owner acceptance record (task 3.10 A6/A7)",
    },
    "v1_audit": {
        "path": ROOT / "docs" / "evidence" / "v1.0-gate.md",
        "fmt": "md",
        "role": "v1.0 requirement-to-evidence gate audit (GATE-M01–M08)",
    },
    "consent": {
        "path": ROOT / "docs" / "pilot" / "consent-admission.md",
        "fmt": "md",
        "role": "pilot consent + trust-admission record (task 4.4)",
    },
    "observations": {
        "path": ROOT / "docs" / "pilot" / "observations.md",
        "fmt": "md",
        "role": "per-participant minimized observation log (task 4.5)",
    },
    "external": {
        "path": ROOT / "docs" / "measurements" / "external-study-2026-10-05.json",
        "fmt": "json",
        "kinds": {"external-pilot"},
        "role": "recorded external-pilot study archive (task 4.5)",
    },
    "evaluation": {
        "path": ROOT / "docs" / "measurements" / "pilot-evaluation-2026-10-05.json",
        "fmt": "json",
        "kinds": {"pilot-evaluation"},
        "role": "aggregated usefulness evaluation (task 4.6)",
    },
    "decision": {
        "path": ROOT / "docs" / "decisions" / "continue-narrow-stop.md",
        "fmt": "md",
        "role": "continue/narrow/stop decision record (task 4.6)",
    },
    "q8": {
        "path": ROOT / "docs" / "decisions" / "q8-distribution.md",
        "fmt": "md",
        "role": "Q8 license/distribution/support/notice record (task 3.9)",
    },
    "fault_matrix": {
        "path": ROOT / "docs" / "evidence" / "fault-matrix-2026-10-05.json",
        "fmt": "json",
        "kinds": {"fault-matrix", "factory-kit/fault-matrix@1"},
        "role": "§8.2 fault-matrix recorded run (tasks 3.6/3.7)",
    },
    "support_matrix": {
        "path": ROOT / "docs" / "recipes" / "support-matrix.md",
        "fmt": "md",
        "role": "pinned supported versions/skills/dependencies (task 3.9)",
    },
}

#: The reviewable package this audit covers (A1/A4). ``package_docs``
#: resolves under ``--package-dir``.
PACKAGE_DOCS = ("evidence.md", "onboarding.md", "release-checklist.md")
DEFAULT_PACKAGE_DIR = ROOT / "docs" / "releases" / "v1.1"

#: Reproducible F09/F10 acceptance evidence (GATE-P03): the checkpoint/
#: budget steering suite (F09) and the interrupted-migration / conflict /
#: rollback suite (F10) plus the modules they exercise — the files the
#: package cites, verified present and non-trivial, never merely named.
F09_F10_EVIDENCE = [
    "factory_kit/control/steering.py",
    "tests/control/test_steering.py",
    "factory_kit/setup/upgrade.py",
    "factory_kit/setup/repair.py",
    "tests/test_setup_upgrade.py",
    "tests/recipes/test_upgrade_recipe.py",
]

#: The supported onboarding/recipe surface (GATE-P06): install,
#: upgrade/repair/rollback, operations, backup/restore, support matrix —
#: each locked by its own recipe/recovery test module.
RECIPE_DOCS = [
    "docs/recipes/installation.md",
    "docs/recipes/upgrade-repair.md",
    "docs/recipes/operations.md",
    "docs/recipes/backup-restore.md",
    "docs/recipes/support-matrix.md",
]

#: Fields the consented aggregate disclosure may carry (A3) — proposed
#: vs actual, denominators and observation limits only. Anything else
#: (code, issue bodies, usernames, tokens, chat text) is a breach.
AGGREGATE_KEYS = {
    "id", "scope", "proposed", "measured", "verdict", "basis",
    "live_status", "window", "denominator", "missing", "excluded",
    "source",
}
BANNED_KEY_RE = re.compile(
    r"(issue_?body|body_?text|chat|username|user_?name|token|secret|"
    r"credential|password|participant_?ref|code_?(block|content|snippet))",
    re.IGNORECASE)

#: Gate vocabulary — only ``passed`` opens the release.
GATE_STATES = ("passed", "blocked", "pending", "unknown")


class ReleaseGateError(Exception):
    """A fixture could not be read — exit 4."""


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise ReleaseGateError(f"cannot load {path}: {exc}") from exc


def _sha12(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return None


# --------------------------------------------------------------------------
# Source loading — every source lands in exactly one status bucket
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
    """Disclose which of a gate's sources were actually consulted."""
    return {n: src.get(n, {}).get("status", "missing") for n in names}


# --------------------------------------------------------------------------
# Gate checks — the A2 checklist plus the GATE-P03/P06 package gates.
# Each returns {"state", "summary", "evidence", "sources", "blockers"}.
# --------------------------------------------------------------------------


def _gate_f09_f10(src, repro_root):
    """GATE-P03 — reproducible F09 checkpoint/budget and F10 interrupted
    migration/conflict/rollback evidence exists and is runnable."""
    names = ["fault_matrix"]
    missing, thin = [], []
    for rel in F09_F10_EVIDENCE:
        p = Path(repro_root) / rel
        if not p.exists() or p.stat().st_size == 0:
            missing.append(rel)
        elif rel.endswith(".py") and "test" in Path(rel).name and \
                "def test" not in p.read_text(encoding="utf-8",
                                              errors="replace"):
            thin.append(rel)
    blockers = ([f"evidence file absent: {m}" for m in missing] +
                [f"evidence test file carries no test cases: {t}"
                 for t in thin])
    fault_verdict = None
    if _ok(src, "fault_matrix"):
        fault_verdict = _json(src, "fault_matrix").get("verdict")
        if fault_verdict != "fault-matrix-passed":
            blockers.append(f"fault-matrix verdict {fault_verdict!r} "
                            f"≠ fault-matrix-passed")
    elif not _ok(src, "fault_matrix"):
        blockers.append(
            f"fault-matrix archive {src['fault_matrix']['status']}")
    if missing or thin:
        state = "blocked"
    elif src.get("fault_matrix", {}).get("status") == "ok" and \
            fault_verdict == "fault-matrix-passed":
        state = "passed"
    elif src.get("fault_matrix", {}).get("status") == "ok":
        state = "blocked"
    else:
        state = "unknown"
    return {
        "state": state,
        "summary": "reproducible F09/F10 acceptance evidence",
        "evidence": F09_F10_EVIDENCE + [
            "docs/evidence/fault-matrix-2026-10-05.json"],
        "fault_matrix_verdict": fault_verdict,
        "sources": _src_status(src, names),
        "blockers": blockers,
    }


def _gate_support_recipes(src, package_dir):
    """GATE-P06 inputs — pinned support matrix and independent
    install/upgrade/repair/removal recipes are packaged."""
    missing = [rel for rel in RECIPE_DOCS
               if not (ROOT / rel).exists()]
    blockers = [f"recipe doc absent: {m}" for m in missing]
    if not _ok(src, "support_matrix"):
        blockers.append(
            f"support matrix {src['support_matrix']['status']}")
    return {
        "state": "blocked" if blockers else "passed",
        "summary": "pinned supported versions + install/upgrade/"
                   "removal recipes",
        "evidence": RECIPE_DOCS +
                    [f"docs/releases/v1.1/{n}" for n in PACKAGE_DOCS],
        "sources": _src_status(src, ["support_matrix"]),
        "blockers": blockers,
    }


def _gate_v1_0(src):
    """A2 — the internal v1.0 gate passed: an *accepted* owner verdict
    with no closed gate on the audit ledger. NO-GO or a closed ledger
    row is a failed gate, never a pending one."""
    names = ["v1_acceptance", "v1_audit"]
    if not all(_ok(src, n) for n in names):
        return {
            "state": "unknown",
            "summary": "passed v1.0 gate",
            "evidence": ["docs/decisions/v1.0-acceptance.md",
                         "docs/evidence/v1.0-gate.md"],
            "sources": _src_status(src, names),
            "blockers": ["acceptance/audit record "
                         f"{_src_status(src, names)}"],
        }
    acc, audit = _text(src, "v1_acceptance"), _text(src, "v1_audit")
    closed = sorted(set(re.findall(
        r"GATE-M\d\d[^\n]*?\*\*closed\*\*", audit)))
    closed = [c.split("|")[0].strip() for c in closed]
    accepted = bool(re.search(r"-\s*\[x\]\s*\*\*Accepted", acc, re.I))
    nogo = "NO-GO" in acc
    owner_pending = bool(
        re.search(r"Owner (?:release )?decision:[^\n]*pending",
                  acc, re.I)) or "pending owner" in acc.lower()
    blockers = []
    if closed:
        blockers += [f"{g} closed on the v1.0 audit ledger"
                     for g in closed]
    if owner_pending:
        blockers.append("owner v1.0 acceptance decision pending")
    if not accepted:
        blockers.append("no accepted internal-v1.0 verdict recorded")
    if closed or nogo:
        state = "blocked"
    elif accepted:
        state = "passed"
    elif owner_pending:
        state = "pending"
    else:
        state = "unknown"
        blockers.append("acceptance record status unparsable")
    return {
        "state": state,
        "summary": "passed v1.0 gate (owner acceptance + open ledger)",
        "evidence": ["docs/decisions/v1.0-acceptance.md",
                     "docs/evidence/v1.0-gate.md"],
        "closed_gates": closed,
        "owner_accepted": accepted,
        "sources": _src_status(src, names),
        "blockers": blockers,
    }


def _gate_consent(src):
    """A2 — consent/trust admission: the recorded admission gate held
    clean on the study archive (ordering 0 violations, admission 0
    mismatches, denominators fully accounted)."""
    names = ["consent", "external"]
    if not all(_ok(src, n) for n in names):
        return {
            "state": "unknown",
            "summary": "consent/trust admission recorded and clean",
            "evidence": ["docs/pilot/consent-admission.md"],
            "sources": _src_status(src, names),
            "blockers": ["consent record or study archive "
                         f"{_src_status(src, names)}"],
        }
    audits = _json(src, "external").get("audits") or {}
    ordering = (audits.get("consent_ordering") or {}).get("violations")
    mismatches = (audits.get("admission_gate") or {}).get("mismatches")
    unaccounted = (audits.get("denominator_integrity") or {}).get(
        "unaccounted")
    blockers = []
    if "admit_participant" not in (_text(src, "consent") or ""):
        blockers.append("consent record carries no admission procedure")
    if ordering:
        blockers.append(f"consent-ordering violations: {ordering}")
    if mismatches:
        blockers.append(f"admission-gate mismatches: {mismatches}")
    if unaccounted:
        blockers.append(f"unaccounted participants: {unaccounted}")
    if ordering is None or mismatches is None or unaccounted is None:
        blockers.append("study archive missing admission audits")
        state = "unknown"
    else:
        state = "blocked" if blockers else "passed"
    return {
        "state": state,
        "summary": "consent/trust admission recorded and clean",
        "evidence": ["docs/pilot/consent-admission.md",
                     "docs/measurements/external-study-2026-10-05.json"],
        "sources": _src_status(src, names),
        "blockers": blockers,
    }


def _gate_pilot_observations(src):
    """A2 — recorded pilot observations: the per-participant log exists
    and the recorded study run completed with denominators intact."""
    names = ["observations", "external"]
    if not all(_ok(src, n) for n in names):
        return {
            "state": "unknown",
            "summary": "pilot observations recorded",
            "evidence": ["docs/pilot/observations.md"],
            "sources": _src_status(src, names),
            "blockers": ["observation log or study archive "
                         f"{_src_status(src, names)}"],
        }
    verdict = _json(src, "external").get("verdict")
    blockers = []
    if verdict != "external-pilot-run-complete":
        blockers.append(
            f"external-study verdict {verdict!r} ≠ "
            "external-pilot-run-complete")
    return {
        "state": "blocked" if blockers else "passed",
        "summary": "pilot observations recorded (fixture study — "
                   "live prerequisites disclosed separately)",
        "evidence": ["docs/pilot/observations.md",
                     "docs/measurements/external-study-2026-10-05.json"],
        "study_verdict": verdict,
        "sources": _src_status(src, names),
        "blockers": blockers,
    }


def _gate_owner_decision(src):
    """A2 — an explicit continue/narrow/stop decision: a *confirmed*
    owner verdict, not a recorded recommendation. The narrow/
    consolidate disposition is carried as data either way."""
    names = ["decision", "evaluation"]
    if not _ok(src, "decision"):
        return {
            "state": "unknown",
            "summary": "explicit continue/narrow/stop decision",
            "evidence": ["docs/decisions/continue-narrow-stop.md"],
            "sources": _src_status(src, names),
            "blockers": ["decision record "
                         f"{src.get('decision', {}).get('status')}"],
        }
    doc = _text(src, "decision")
    confirmed = re.search(
        r"-\s*\[x\]\s*\*\*(Narrow/consolidate confirmed|Continue|Stop)",
        doc, re.I)
    recorded = None
    if _ok(src, "evaluation"):
        reco = _json(src, "evaluation").get("recommendation") or {}
        recorded = reco.get("disposition")
    if recorded is None:
        m = re.search(r"\*\*(narrow/consolidate|narrow-consolidate|"
                      r"continue|stop)\*\*", doc, re.I)
        recorded = m.group(1) if m else None
    pending = bool(re.search(r"pending owner", doc, re.I))
    blockers = []
    if confirmed:
        state = "passed"
    elif recorded and pending:
        state = "pending"
        blockers.append(
            "owner confirmation pending — recorded disposition "
            f"{recorded!r} is a recommendation, not a verdict")
    elif recorded:
        state = "pending"
        blockers.append("recorded disposition lacks a confirmed "
                        "owner verdict")
    else:
        state = "unknown"
        blockers.append("no disposition recorded in the decision "
                        "record")
    return {
        "state": state,
        "summary": "explicit continue/narrow/stop decision "
                   "(confirmed owner verdict)",
        "evidence": ["docs/decisions/continue-narrow-stop.md",
                     "docs/measurements/"
                     "pilot-evaluation-2026-10-05.json"],
        "recorded_disposition": recorded,
        "owner_confirmed": bool(confirmed),
        "sources": _src_status(src, names),
        "blockers": blockers,
    }


def _gate_q8(src):
    """A2 — Q8 license/distribution/support/notice resolved. Every
    ``Unresolved`` row in the Q8 table is a named blocker."""
    names = ["q8"]
    if not _ok(src, "q8"):
        return {
            "state": "unknown",
            "summary": "Q8 distribution decisions resolved",
            "evidence": ["docs/decisions/q8-distribution.md"],
            "sources": _src_status(src, names),
            "blockers": ["Q8 record "
                         f"{src.get('q8', {}).get('status')}"],
        }
    doc = _text(src, "q8")
    section = doc.split("## Q8", 1)[-1]
    unresolved = [m.group(1).strip() for m in re.finditer(
        r"\|\s*([^|]+?)\s*\|\s*\*\*Unresolved[^*]*\*\*", section)]
    blockers = [f"Q8 row unresolved: {u}" for u in unresolved]
    return {
        "state": "blocked" if unresolved else "passed",
        "summary": "Q8 license/distribution/support/notice resolved",
        "evidence": ["docs/decisions/q8-distribution.md"],
        "unresolved_rows": unresolved,
        "sources": _src_status(src, names),
        "blockers": blockers,
    }


def _gate_package_docs(src, package_dir):
    """A1/A4 — the reviewable package itself: evidence, onboarding and
    release-checklist docs exist and are non-trivial."""
    pdir = Path(package_dir)
    missing, thin = [], []
    for name in PACKAGE_DOCS:
        p = pdir / name
        if not p.exists():
            missing.append(name)
        elif p.stat().st_size < 400:
            thin.append(name)
    blockers = ([f"package doc absent: docs/releases/v1.1/{m}"
                 for m in missing] +
                [f"package doc too thin to review: {t}" for t in thin])
    return {
        "state": "blocked" if blockers else "passed",
        "summary": "reviewable v1.1 package docs present",
        "evidence": [f"docs/releases/v1.1/{n}" for n in PACKAGE_DOCS],
        "sources": {},
        "blockers": blockers,
    }


#: The release checklist — A2's five confirmation gates plus the two
#: package gates (GATE-P03 evidence, GATE-P06 recipes/docs). Order is
#: the checklist order; ``blocking`` gates hold publication closed,
#: ``package`` gates describe the deliverable itself. A non-passed
#: state on any row withholds the release — no class silently re-opens.
def _evaluate_gates(src, package_dir, repro_root):
    return {
        "f09_f10_evidence": _gate_f09_f10(src, repro_root),
        "support_recipes": _gate_support_recipes(src, package_dir),
        "package_docs": _gate_package_docs(src, package_dir),
        "v1_0_gate": _gate_v1_0(src),
        "consent_trust_admission": _gate_consent(src),
        "pilot_observations": _gate_pilot_observations(src),
        "owner_decision": _gate_owner_decision(src),
        "q8_resolved": _gate_q8(src),
    }


CHECKLIST_ORDER = [
    "f09_f10_evidence",
    "support_recipes",
    "package_docs",
    "v1_0_gate",
    "consent_trust_admission",
    "pilot_observations",
    "owner_decision",
    "q8_resolved",
]

#: What publication requires once withheld — enumerated, never implied.
PUBLISH_REQUIRES = [
    "owner records a verdict in docs/decisions/continue-narrow-stop.md",
    "owner records internal-v1.0 acceptance in "
    "docs/decisions/v1.0-acceptance.md with every audit-ledger gate open",
    "all four Q8 rows resolved in docs/decisions/q8-distribution.md",
    "live-observation prerequisites stay named — fixture verdicts are "
    "never promoted to live-target claims",
]

#: What no disposition here can ever authorize (A4) — verbatim, so the
#: package can never be read as an expansion claim.
NEVER_AUTHORIZED = [
    "new concurrency, runtime, provider or adapter support",
    "marking missing/pending evidence as achieved",
    "publication or distribution claims while Q8 stays unresolved",
    "expansion beyond the recipe-scoped deliverable (§8.4)",
]


# --------------------------------------------------------------------------
# Aggregate disclosure (A3) — proposed vs actual, aggregate fields only
# --------------------------------------------------------------------------


def _disclosure(src):
    """Consented aggregate results verbatim from the evaluation archive:
    proposed target status, actual pass/fail/inconclusive, denominators,
    missing data and observation limits. Only AGGREGATE_KEYS fields
    survive — the audit below proves it."""
    if not _ok(src, "evaluation"):
        return {"targets": [], "q9_status": None,
                "observation_limits": [],
                "source_gap": src.get("evaluation", {}).get("status")}
    ev = _json(src, "evaluation")
    rows = []
    for r in (ev.get("targets") or {}).get("results") or []:
        rows.append({k: r[k] for k in AGGREGATE_KEYS if k in r})
    limits = []
    for u in ev.get("unresolved") or []:
        limits.append({"id": u.get("id"), "kind": u.get("kind"),
                       "state": u.get("state"),
                       "required": u.get("required")})
    return {
        "q9_status": (ev.get("q9") or {}).get("status"),
        "targets": rows,
        "observation_limits": limits,
        "limits_note": "fixture/study verdicts are never live-target "
                       "attainment; live prerequisites stay named",
    }


def _aggregate_only(report):
    """A3 audit — the disclosure carries only aggregate fields and no
    banned content-bearing key survives anywhere in the report."""
    bad_keys = []
    allowed = AGGREGATE_KEYS | {"id", "kind", "state", "required"}

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if BANNED_KEY_RE.search(str(k)):
                    bad_keys.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(report.get("disclosure") or {}, "$.disclosure")
    rows = (report.get("disclosure") or {}).get("targets") or []
    nonconforming = [
        r.get("id") for r in rows
        if isinstance(r, dict) and not set(r) <= allowed]
    return {"bad_keys": bad_keys[:20],
            "nonconforming_rows": nonconforming[:20]}


# --------------------------------------------------------------------------
# Disposition + audits
# --------------------------------------------------------------------------


def _disposition(gates):
    """Data, not a gate pass: release-ready iff *every* gate passed.
    The withheld path's deliverable scope follows A4 — narrow/
    consolidate (recorded or pending) ships the recipe-scoped package;
    stop or an unreadable record yields the reviewable draft."""
    non_passed = {g: v for g, v in gates.items() if v["state"] != "passed"}
    withheld_reasons = [
        {"gate": g, "state": v["state"],
         "detail": "; ".join(v["blockers"]) or v["summary"]}
        for g, v in non_passed.items()]
    if not non_passed:
        return {"disposition": "release-ready",
                "deliverable_scope": "full-package",
                "withheld_reasons": [],
                "publish_requires": []}
    recorded = (gates.get("owner_decision") or {}).get(
        "recorded_disposition")
    scope = ("recipe-scoped" if recorded in
             ("narrow-consolidate", "narrow/consolidate")
             else "reviewable-draft")
    return {"disposition": "release-withheld",
            "deliverable_scope": scope,
            "withheld_reasons": withheld_reasons,
            "publish_requires": list(PUBLISH_REQUIRES)}


def _audits(report, *, recorded_disposition=None, recorded_reasons=None):
    gates = report["gates"]
    src = report.get("sources") or {}
    audits = {}

    # Denominator integrity — every declared gate produced a verdict in
    # the vocabulary; none was silently dropped.
    audits["gate_denominator"] = {
        "declared": len(CHECKLIST_ORDER),
        "evaluated": len(gates),
        "unevaluated": [g for g in CHECKLIST_ORDER if g not in gates],
        "bad_states": {g: v["state"] for g, v in gates.items()
                       if v["state"] not in GATE_STATES},
    }

    # Source coverage — every gate discloses which sources it consulted;
    # non-ok sources are named gaps (they force unknown/blocked, never
    # an unearned pass).
    gaps = {n: s["status"] for n, s in src.items()
            if s["status"] != "ok"}
    audits["source_coverage"] = {"gaps": gaps,
                                 "sources": len(src)}

    # Withheld-reason coverage — every non-passed gate is named exactly
    # once in withheld_reasons (denominator-preserving honesty). On a
    # fixture re-check the *recorded* reasons are compared, so an
    # archive that dropped a blocker is caught.
    reasons = (recorded_reasons if recorded_reasons is not None
               else report.get("withheld_reasons")) or []
    named = {r["gate"] for r in reasons}
    non_passed = {g for g, v in gates.items() if v["state"] != "passed"}
    audits["withheld_reason_coverage"] = {
        "non_passed": sorted(non_passed),
        "named": sorted(named),
        "uncovered": sorted(non_passed - named),
        "phantom": sorted(named - non_passed),
    }

    # No publish on unpassed — the emitted disposition must equal the
    # disposition recomputed from gate states alone (a recorded
    # ``release-ready`` over a blocked gate is a fabrication breach).
    recomputed = "release-ready" if not non_passed else "release-withheld"
    recorded = (recorded_disposition if recorded_disposition is not None
                else recomputed)
    audits["no_publish_on_unpassed"] = {
        "recomputed": recomputed,
        "recorded": recorded,
        "consistent": recorded == recomputed,
    }

    # Aggregate-only disclosure (A3).
    audits["aggregate_only"] = _aggregate_only(report)

    # Observation limits carried (A3) — the disclosure must retain the
    # observation limits whenever a study contributed rows.
    disc = report.get("disclosure") or {}
    has_rows = bool(disc.get("targets"))
    audits["observation_limits_carried"] = {
        "targets": len(disc.get("targets") or []),
        "limits": len(disc.get("observation_limits") or []),
        "ok": (not has_rows) or bool(disc.get("observation_limits")),
    }

    return audits


def _breaches(audits):
    b = []
    d = audits["gate_denominator"]
    if d["unevaluated"] or d["bad_states"] or d["evaluated"] != d["declared"]:
        b.append("gate_denominator")
    c = audits["withheld_reason_coverage"]
    if c["uncovered"] or c["phantom"]:
        b.append("withheld_reason_coverage")
    if not audits["no_publish_on_unpassed"]["consistent"]:
        b.append("no_publish_on_unpassed")
    a = audits["aggregate_only"]
    if a["bad_keys"] or a["nonconforming_rows"]:
        b.append("aggregate_only")
    if not audits["observation_limits_carried"]["ok"]:
        b.append("observation_limits_carried")
    return b


def evaluate(report):
    """Bind gates → disposition → audits → verdict. Recomputable from
    ``report['gates']`` + ``report['disclosure']`` — ``reevaluate``
    reruns this on an archive, where the *recorded* disposition and
    withheld reasons are what the consistency audits check (a fixture
    claiming ``release-ready`` over a blocked gate breaches)."""
    recorded_disposition = report.get("disposition")
    recorded_reasons = report.get("withheld_reasons")
    disp = _disposition(report["gates"])
    report.update(disp)
    report["audits"] = _audits(
        report, recorded_disposition=recorded_disposition,
        recorded_reasons=recorded_reasons)
    report["breaches"] = _breaches(report["audits"])
    report["instrumentation"] = (
        "clean" if not report["breaches"] else "breached")
    report["verdict"] = (
        "release-check-complete" if not report["breaches"]
        else "release-check-instrumentation-failed")
    report["never_authorized"] = list(NEVER_AUTHORIZED)
    report["study_outcome"] = (
        "conditional v1.1 release checklist audited — withheld states "
        "are data, never a publication act; fixture verdicts are never "
        "live-target claims")
    # Scan the *finished* report — gate reasons and disclosure rows
    # carry source-derived text, so scanning the shell under-covers.
    leaks = _export.secret_leaks(
        {k: v for k, v in report.items() if k != "audits"})
    report["audits"]["secret_scan"] = (
        {"outcome": "clean"} if not leaks
        else {"outcome": "leak", "paths": leaks[:20]})
    if leaks and "secret_scan" not in report["breaches"]:
        report["breaches"].append("secret_scan")
        report["instrumentation"] = "breached"
        report["verdict"] = "release-check-instrumentation-failed"
    return report


def reevaluate(report):
    """Re-judge a recorded release-gate archive from its recorded gates
    — the replayability contract, and the failed-gate-fixture check."""
    if not isinstance(report, dict) or report.get("kind") != "release-gate":
        raise ReleaseGateError(
            f"expected kind 'release-gate', got {report.get('kind')!r}"
            if isinstance(report, dict) else "not a release-gate archive")
    if not isinstance(report.get("gates"), dict):
        raise ReleaseGateError("archive carries no gates map")
    try:
        return evaluate(dict(report))
    except (KeyError, TypeError, AttributeError) as exc:
        raise ReleaseGateError(f"archive incomplete: {exc}") from exc


# --------------------------------------------------------------------------
# The run — read committed sources and audit the checklist
# --------------------------------------------------------------------------


def run(*, paths=None, package_dir=DEFAULT_PACKAGE_DIR,
        repro_root=ROOT):
    """Read the committed source artifacts and produce the release-gate
    report. Read-only: nothing here publishes anything."""
    paths = paths or {}
    src = load_sources(paths)
    gates = _evaluate_gates(src, package_dir, repro_root)

    report = {
        "run_id": f"release-gate-{int(time.time())}",
        "version": VERSION,
        "kind": "release-gate",
        "mode": "release-check",
        "generated_at": _utcnow(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "manifest": str(ROOT / ".factory-kit.yml"),
            "manifest_effective_digest": schema.effective_digest(
                schema.load_manifest_file(ROOT / ".factory-kit.yml")),
            "package_dir": str(package_dir),
            "reproduce": "python3 tools/probes/release_gate.py "
            "--write docs/releases/v1.1/release-gate-$(date +%F).json",
        },
        "sources": {n: {k: v for k, v in s.items()
                        if k not in ("text", "json")}
                    for n, s in src.items()},
        "gates": gates,
        "checklist": list(CHECKLIST_ORDER),
        "disclosure": _disclosure(src),
    }
    return evaluate(report)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", help="write the JSON report to a file")
    ap.add_argument("--fixture",
                    help="re-judge a recorded release-gate archive")
    ap.add_argument("--package-dir", default=str(DEFAULT_PACKAGE_DIR))
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
                package_dir=args.package_dir,
                repro_root=args.repro_root)
    except ReleaseGateError as exc:
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
    print(f"disposition: {report['disposition']}"
          f" ({report.get('deliverable_scope', '-')})")
    if report.get("withheld_reasons"):
        for r in report["withheld_reasons"]:
            print(f"  withheld: {r['gate']} [{r['state']}]")
    print(f"verdict: {report['verdict']}")
    if report["breaches"]:
        print(f"  breaches: {', '.join(report['breaches'])}")
    return 0 if report["verdict"] == "release-check-complete" else 1


if __name__ == "__main__":
    sys.exit(main())
