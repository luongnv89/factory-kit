#!/usr/bin/env python3
"""Task 5.2 admission-precondition check — issue #33 (PRD §3.1 F13,
§8.4 GATE-E04 expansion contract).

Fail-closed gate on the *only* act that may start task 5.2: a recorded
operator adoption, in ``docs/decisions/f13-admission.md``, of exactly
the single candidate the GATE-E04 assessment admitted — plus the
evidence preconditions the issue's A1 names (supported hooks, grounded
≤3-day effort bound) and the plan's confirmed-continue requirement
(tasks.md: "5.2 runs only after explicit adoption of exactly one
candidate and a positive continue decision").

What this evidence is. A read-only audit over committed artifacts: the
recorded GATE-E04 admission assessment archive, the F13 operator
decision record, the continue/narrow/stop decision record, the
pilot-evaluation archive and the documented candidate fixture. Every
precondition row is *derived* from those sources — an adoption claim
is parsed out of the decision record's own fields, and a checked box
without the named candidate/version, recipe, scope, cost/capacity and
acceptance-gate fields is not an adoption.

What it is not. Not an adapter — nothing here executes harness code,
``adapter_execution`` is asserted ``none`` and audited. Not an
admission instrument — the owner's act is recorded in
``docs/decisions/f13-admission.md``, never here; this check only
verifies whether that act exists and satisfies task 5.2's own
preconditions. Not a weakening of any gate — ``precondition: unmet``
keeps adapter implementation unauthorized with every unmet row named.

Precondition semantics (the issue's honest-reporting contract):

- ``precondition: met`` — every checklist gate ``passed``: the GATE-E04
  assessment archive is complete and clean, the owner confirmed
  ``continue``, an adoption is recorded naming the single assessed
  candidate/version with all five adoption fields, the candidate's
  capability/authority rows and compatibility probes are proven on its
  own lane, a grounded ≤3-developer-day bound exists, and no adapter
  code sits on disk ahead of authorization. Only then does
  ``authorization`` read ``authorized-for-implementation``.
- ``precondition: unmet`` — any gate ``blocked``/``pending``/
  ``unknown``; ``authorization: blocked`` and every non-passed row is
  named in ``unmet_reasons`` (denominator-preserving). Missing,
  rejected or revoked admission, unproven hooks and an ungrounded or
  out-of-bound estimate all land here — per A1 that is *blocked pending
  scoped replan*, never a silent broadening of the task.

Verdict semantics (shared probe vocabulary):

- ``precondition-check-complete`` — every audit clean; ``precondition``
  is data.
- ``precondition-check-instrumentation-failed`` — an audit breached
  (unevaluated gate, unaccounted source gap, uncovered unmet reason,
  fabricated authorization over a non-passed gate, adoption-gate pass
  without a named candidate, asserted adapter execution, unauthorized
  adapter code on disk, leaked canary); exit 1.
- ``--fixture`` re-judges a recorded run: gates are re-evaluated and
  the recorded ``precondition``/``authorization``/``unmet_reasons``
  must match the recomputed ones — an archive claiming authorization
  over a non-passed gate is a breach.

Exit codes (shared probe vocabulary):

    0  precondition-check-complete                 — audits clean
    1  precondition-check-instrumentation-failed   — an audit breached
    2  usage error                                 — malformed invocation
    4  cannot complete                             — fixture unreadable /
                                                     wrong kind

Usage:

    python3 tools/probes/adapter_precondition.py                 # audit
    python3 tools/probes/adapter_precondition.py --write out.json
    python3 tools/probes/adapter_precondition.py --fixture out.json
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

#: Committed source artifacts the check audits. ``fmt`` is ``md``
#: (required markers) or ``json`` (parse + kind/schema checks). A source
#: that is missing/unreadable/wrong-kind forces its gates to
#: ``unknown``/``pending`` — never an unearned pass.
SOURCES = {
    "assessment": {
        "path": ROOT / "docs" / "adapters"
                / "admission-assessment-2026-10-05.json",
        "fmt": "json",
        "kinds": {"harness-admission-assessment"},
        "role": "recorded GATE-E04 admission assessment (task 5.1) — "
                "the singleton candidate, derived capability/probe "
                "states and the effort bound are read from it",
    },
    "f13_record": {
        "path": ROOT / "docs" / "decisions" / "f13-admission.md",
        "fmt": "md",
        "role": "F13 operator admission record — the adoption act this "
                "check verifies; never written by this probe",
    },
    "decision_cns": {
        "path": ROOT / "docs" / "decisions" / "continue-narrow-stop.md",
        "fmt": "md",
        "role": "continue/narrow/stop decision record (task 4.6) — "
                "owner-confirmed continue is part of the standing gate",
    },
    "evaluation": {
        "path": ROOT / "docs" / "measurements"
                / "pilot-evaluation-2026-10-05.json",
        "fmt": "json",
        "kinds": {"pilot-evaluation"},
        "role": "aggregated usefulness evaluation — the recorded "
                "continue/narrow/stop disposition",
    },
    "candidate": {
        "path": ROOT / "tests" / "fixtures" / "adapters"
                / "candidate-codex-cli.json",
        "fmt": "json",
        "kinds": {"factory-kit/harness-candidate@1"},
        "role": "the single documented candidate under assessment — "
                "identity cross-check, never evidence of support",
    },
}

#: Adapter-code directories that must not exist while the precondition
#: is unmet (§5.2 adapter code may not precede owner adoption).
ADAPTER_CODE_DIRS = ("factory_kit/adapters", "factory_kit/harnesses")

#: Gate vocabulary — only ``passed`` opens the precondition.
GATE_STATES = ("passed", "blocked", "pending", "unknown")

#: Precondition/authorization vocabulary.
PRECONDITIONS = ("met", "unmet")
AUTHORIZATIONS = ("authorized-for-implementation", "blocked")

#: The five adoption fields task 5.1 A4 requires the operator record to
#: carry inline — label regex → report key. A field line must carry a
#: real value; ``none``/``pending``/placeholder text is *not* a value.
ADOPTION_FIELDS = [
    ("adopted_candidate",
     re.compile(r"adopted\W+candidate\W*version", re.IGNORECASE)),
    ("support_trust_recipe",
     re.compile(r"support\W*trust\W*recipe|tested\s+recipe",
                re.IGNORECASE)),
    ("bounded_test_scope",
     re.compile(r"bounded\W+test\W+scope|test\W+scope",
                re.IGNORECASE)),
    ("cost_capacity",
     re.compile(r"cost\W*capacity|cost\W+decision", re.IGNORECASE)),
    ("acceptance_gates",
     re.compile(r"acceptance\W+gates?", re.IGNORECASE)),
]

#: Values that look recorded but assert nothing — an adoption field
#: whose value is a placeholder is an absent field.
PLACEHOLDER_RE = re.compile(
    r"^[\s_>*-]*(none|pending|tbd|n/?a|not\W+recorded|none\W+recorded|"
    r"deferred|_+|\.\.\.)\b", re.IGNORECASE)

#: A candidate identity mention: ``harness x.y.z`` — used to parse the
#: adopted candidate out of the operator-decision/adoption lines.
CANDIDATE_RE = re.compile(r"([a-z][a-z0-9_-]*)\s+(\d+\.\d+\.\d+)",
                          re.IGNORECASE)

#: The capability rows and compatibility probes whose derived state must
#: be ``proven`` on the candidate lane before hooks count as supported
#: (the issue's A1 "missing supported hooks" trigger).
HOOK_GATES = ("capability_matrix", "probe_authorization",
              "probe_credential_separation", "probe_hermes_lifecycle",
              "probe_endpoint_compat")

#: Fields the emitted adoption block may carry — anything content-
#: bearing (issue bodies, chat text, tokens) is a breach, matching the
#: sibling probes' aggregate-only contract.
BANNED_KEY_RE = re.compile(
    r"(issue_?body|body_?text|chat|username|user_?name|token|secret|"
    r"credential_?value|password|participant_?ref|code_?(block|content|"
    r"snippet))", re.IGNORECASE)


class AdapterPreconditionError(Exception):
    """A fixture could not be read — exit 4."""


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise AdapterPreconditionError(
            f"cannot load {path}: {exc}") from exc


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
    depending on it evaluate ``unknown``/``pending`` and the gap is
    disclosed."""
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
            ident = entry["json"].get("kind") or entry["json"].get(
                "schema")
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
# Adoption parsing — the decision record's own fields, never its prose
# --------------------------------------------------------------------------


def _field_lines(doc):
    """Yield ``(key, value)`` for every ``- **label:** value``-shaped
    line. Checkbox prose ("adopts … with support/trust recipe, …") is
    not a field: only a colon-bearing label line counts."""
    for line in doc.splitlines():
        m = re.match(r"^\s*[-*]?\s*\*{0,2}([^:*\n]{3,60}?)\*{0,2}"
                     r"\s*:\s*(.+?)\s*$", line)
        if m:
            yield m.group(1).strip(), m.group(2).strip()


def _field_value(fields, label_re):
    """The first non-placeholder value under a matching label, else
    ``None``. ``**Adopted candidate/version:** none`` records nothing."""
    for label, value in fields:
        if label_re.search(label):
            if not PLACEHOLDER_RE.match(value):
                return value
    return None


def _parse_adoption(doc):
    """Parse the operator's adoption act out of the F13 decision record.

    Returns ``{checked, disposition, candidate, candidate_version,
    fields, operator_decision}`` where ``checked`` is true only on an
    ``[x] **Adopt**`` row, ``disposition`` is ``adopted`` | ``rejected``
    | ``defer-confirmed`` | ``pending``, and ``candidate`` /
    ``candidate_version`` come from the adoption field or the operator-
    decision line — a checked box that never names a versioned
    candidate is parsed, not trusted.

    Field lines are only read *after* the checked Adopt row: text
    earlier in the record describes the deferred disposition (the same
    labels appear under "Recorded disposition"), and counting it would
    let an unchecked assessment impersonate an adoption."""
    lines = doc.splitlines()
    adopt_idx = reject_idx = defer_idx = None
    for i, line in enumerate(lines):
        if adopt_idx is None and re.search(
                r"-\s*\[x\]\s*\*\*Adopt", line, re.IGNORECASE):
            adopt_idx = i
        if reject_idx is None and re.search(
                r"-\s*\[x\]\s*\*\*Reject", line, re.IGNORECASE):
            reject_idx = i
        if defer_idx is None and re.search(
                r"-\s*\[x\]\s*\*\*Defer", line, re.IGNORECASE):
            defer_idx = i
    adopted = adopt_idx is not None
    rejected = reject_idx is not None
    defer = defer_idx is not None
    decision = None
    m = re.search(r"\*\*Operator decision:\*\*\s*(.+)", doc)
    if m:
        decision = m.group(1).strip()

    if adopted:
        fields = list(_field_lines("\n".join(lines[adopt_idx:])))
    else:
        fields = []
    parsed = {key: _field_value(fields, label_re)
              for key, label_re in ADOPTION_FIELDS}

    candidate = version = None
    # Prefer the explicit adopted-candidate field, then the operator
    # decision line — both are adoption surfaces, not checkbox prose.
    for text in (parsed.get("adopted_candidate"),
                 decision if adopted else None):
        if not text:
            continue
        m = CANDIDATE_RE.search(text)
        if m:
            candidate, version = m.group(1).lower(), m.group(2)
            break
    if candidate is None and adopted and \
            parsed.get("adopted_candidate"):
        # A value with no versioned identity is still recorded text —
        # expose it raw so the match gate fails closed.
        candidate = parsed["adopted_candidate"]

    if adopted:
        disposition = "adopted"
    elif rejected:
        disposition = "rejected"
    elif defer:
        disposition = "defer-confirmed"
    else:
        disposition = "pending"
    return {"checked": adopted, "disposition": disposition,
            "candidate": candidate, "candidate_version": version,
            "fields": parsed, "operator_decision": decision}


def _assessed_candidate(src):
    """The single candidate the GATE-E04 assessment recorded — the only
    identity an adoption may name. Returns ``None`` when the archive
    cannot supply exactly one candidate."""
    doc = _json(src, "assessment") or {}
    candidates = doc.get("candidates") or []
    if len(candidates) != 1:
        return None
    c = candidates[0]
    if not c.get("harness") or not c.get("harness_version"):
        return None
    return {"harness": str(c.get("harness")).lower(),
            "harness_version": str(c.get("harness_version")),
            "integration_path": c.get("integration_path")}


# --------------------------------------------------------------------------
# Precondition gates — the checklist the issue's A1 names
# --------------------------------------------------------------------------


def _gate_assessment_recorded(src):
    """The GATE-E04 assessment exists as a clean, complete, singleton
    record — the precondition's evidence base. A missing or breached
    archive can never ground an authorization."""
    names = ["assessment"]
    ev = ["docs/adapters/admission-assessment-2026-10-05.json"]
    if not _ok(src, "assessment"):
        return {"state": "unknown",
                "summary": "GATE-E04 admission assessment recorded "
                           "complete and clean",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["assessment archive "
                             f"{src.get('assessment', {}).get('status')}"]}
    doc = _json(src, "assessment")
    blockers = []
    if doc.get("verdict") != "assessment-complete":
        blockers.append(f"assessment verdict "
                        f"{doc.get('verdict')!r} ≠ "
                        "assessment-complete")
    if doc.get("instrumentation") != "clean":
        blockers.append("assessment instrumentation not clean")
    if doc.get("disposition") not in ("admission-deferred",
                                      "admission-blocked",
                                      "admission-ready-for-owner"):
        blockers.append(f"assessment disposition "
                        f"{doc.get('disposition')!r} out of vocabulary")
    if len(doc.get("candidates") or []) != 1:
        blockers.append("assessment does not record exactly one "
                        "candidate")
    return {"state": "blocked" if blockers else "passed",
            "summary": "GATE-E04 admission assessment recorded "
                       "complete and clean",
            "evidence": ev,
            "recorded_disposition": doc.get("disposition"),
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_pilot_continue(src):
    """The plan's standing requirement: a positive continue decision.
    Only an owner-confirmed ``[x] **Continue**`` opens 5.2; the
    recorded narrow disposition and a pending confirmation both fail
    closed — completing the pilot task with a narrow/stop outcome never
    authorizes adapter execution (tasks.md)."""
    names = ["decision_cns", "evaluation"]
    ev = ["docs/decisions/continue-narrow-stop.md",
          "docs/measurements/pilot-evaluation-2026-10-05.json"]
    if not (_ok(src, "decision_cns") and _ok(src, "evaluation")):
        return {"state": "unknown",
                "summary": "owner-confirmed continue pilot decision",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["decision/evaluation source not readable"]}
    recorded = (((_json(src, "evaluation") or {})
                 .get("recommendation") or {}).get("disposition"))
    doc = _text(src, "decision_cns") or ""
    confirmed = bool(re.search(r"-\s*\[x\]\s*\*\*Continue", doc,
                               re.IGNORECASE))
    blockers = []
    if recorded == "continue" and confirmed:
        state = "passed"
    elif recorded is None:
        state = "unknown"
        blockers.append("no continue/narrow/stop disposition recorded")
    else:
        state = "blocked"
        if recorded != "continue":
            blockers.append(
                f"recorded disposition {recorded!r} — a narrow/stop "
                "outcome never authorizes §8.4 expansion")
        if not confirmed:
            blockers.append("owner confirmation pending — a "
                            "recommendation is not a verdict")
    return {"state": state,
            "summary": "owner-confirmed continue pilot decision",
            "evidence": ev,
            "recorded_disposition": recorded,
            "owner_confirmed": confirmed,
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_operator_adoption(src, adoption):
    """The operator's explicit adoption act — the single precondition
    5.1 A4 calls "before 5.2 may run". Only ``[x] **Adopt**`` counts; a
    rejected candidate is revoked admission and a confirmed deferral or
    empty checkbox leaves the task blocked/pending — never waived."""
    names = ["f13_record"]
    ev = ["docs/decisions/f13-admission.md"]
    if not _ok(src, "f13_record"):
        return {"state": "pending",
                "summary": "operator adoption recorded (adopted "
                           "candidate/version, recipe, scope, cost, "
                           "acceptance gates)",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["no F13 decision record — task 5.2 stays "
                             "deferred"]}
    disp = adoption["disposition"]
    if disp == "adopted":
        return {"state": "passed",
                "summary": "operator adoption recorded",
                "evidence": ev, "sources": _src_status(src, names),
                "adopted": True, "blockers": []}
    blockers = ["no operator adoption recorded — task 5.2 stays "
                "deferred"]
    state = "pending"
    if disp == "rejected":
        state = "blocked"
        blockers.append("operator rejected the candidate — revoked "
                        "admission blocks execution (A1)")
    elif disp == "defer-confirmed":
        blockers.append("operator confirmed the deferral")
    return {"state": state,
            "summary": "operator adoption recorded",
            "evidence": ev, "sources": _src_status(src, names),
            "adopted": False, "blockers": blockers}


def _gate_adoption_scope(src, adoption):
    """A4's field contract: the recorded adoption carries adopted
    candidate/version, support/trust recipe, bounded test scope,
    cost/capacity and acceptance gates — each with a real value, not a
    placeholder. A checked box missing a field is an incomplete
    adoption, still unmet."""
    names = ["f13_record"]
    ev = ["docs/decisions/f13-admission.md"]
    if not _ok(src, "f13_record"):
        return {"state": "unknown",
                "summary": "adoption record carries candidate/version, "
                           "recipe, bounded scope, cost/capacity and "
                           "acceptance gates",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["no F13 decision record to parse"]}
    if adoption["disposition"] != "adopted":
        return {"state": "pending",
                "summary": "adoption record carries candidate/version, "
                           "recipe, bounded scope, cost/capacity and "
                           "acceptance gates",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["no recorded adoption to evaluate"]}
    fields = dict(adoption["fields"])
    # The candidate field is satisfied by a versioned identity parsed
    # anywhere on the adoption surface (field or operator-decision
    # line) — the other four have no such fallback.
    if adoption.get("candidate_version"):
        fields["adopted_candidate"] = fields.get(
            "adopted_candidate") or (
                f"{adoption['candidate']} "
                f"{adoption['candidate_version']}")
    missing = [key for key, _ in ADOPTION_FIELDS
               if not fields.get(key)]
    if not adoption.get("candidate_version") and \
            "adopted_candidate" not in missing:
        missing.append("adopted_candidate (no versioned identity)")
    blockers = [f"adoption field missing/placeholder: {k}"
                for k in missing]
    return {"state": "blocked" if blockers else "passed",
            "summary": "adoption record carries candidate/version, "
                       "recipe, bounded scope, cost/capacity and "
                       "acceptance gates",
            "evidence": ev,
            "recorded_fields": {k: bool(v) for k, v
                                in fields.items()},
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_candidate_match(src, adoption, assessed):
    """A1's "explicitly admitted candidate/version is the only new
    adapter": the adoption must name exactly the assessed singleton —
    harness *and* pinned version. Adopting anything else (or nothing
    identifiable) is out of scope: blocked pending scoped replan,
    never a silent broadening."""
    names = ["f13_record", "assessment", "candidate"]
    ev = ["docs/decisions/f13-admission.md",
          "docs/adapters/admission-assessment-2026-10-05.json",
          "tests/fixtures/adapters/candidate-codex-cli.json"]
    if assessed is None:
        return {"state": "unknown",
                "summary": "adopted candidate/version is the assessed "
                           "singleton",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["assessed singleton not identifiable "
                             "from the archive"]}
    if adoption["disposition"] != "adopted":
        return {"state": "pending",
                "summary": "adopted candidate/version is the assessed "
                           "singleton",
                "evidence": ev, "sources": _src_status(src, names),
                "assessed": assessed,
                "blockers": ["no recorded adoption to compare"]}
    blockers = []
    if not adoption.get("candidate") or \
            not adoption.get("candidate_version"):
        blockers.append("adoption names no versioned candidate — "
                        "missing admission for the assessed singleton")
    else:
        if adoption["candidate"] != assessed["harness"]:
            blockers.append(
                f"adopted harness {adoption['candidate']!r} ≠ assessed "
                f"{assessed['harness']!r} — out of scope, scoped "
                "replan required")
        if adoption["candidate_version"] != \
                assessed["harness_version"]:
            blockers.append(
                f"adopted version "
                f"{adoption['candidate_version']!r} ≠ assessed "
                f"{assessed['harness_version']!r}")
    return {"state": "blocked" if blockers else "passed",
            "summary": "adopted candidate/version is the assessed "
                       "singleton",
            "evidence": ev,
            "assessed": assessed,
            "adopted": {"harness": adoption.get("candidate"),
                        "harness_version":
                            adoption.get("candidate_version")},
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_supported_hooks(src):
    """A1's "missing supported hooks blocks execution": the candidate's
    required capability rows and A3 compatibility probes must be
    *proven on the candidate lane* — the recorded assessment's derived
    states are the evidence, and supported-for-the-selected-lane never
    transfers."""
    names = ["assessment"]
    ev = ["docs/adapters/admission-assessment-2026-10-05.json"]
    if not _ok(src, "assessment"):
        return {"state": "unknown",
                "summary": "supported hooks proven on the candidate "
                           "lane (capability matrix + compatibility "
                           "probes)",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["assessment archive "
                             f"{src.get('assessment', {}).get('status')}"]}
    doc = _json(src, "assessment")
    gates = doc.get("gates") or {}
    blockers = []
    for gid in HOOK_GATES:
        gate = gates.get(gid) or {}
        if gate.get("state") != "passed":
            blockers.append(f"assessment gate {gid} "
                            f"{gate.get('state') or 'absent'}")
    unproven, no_go = [], []
    candidates = doc.get("candidates") or []
    if candidates:
        cand = candidates[0]
        for row in cand.get("matrix") or []:
            derived = row.get("derived")
            cap = row.get("capability")
            if row.get("required") and derived != "proven":
                (no_go if derived == "no-go" else unproven).append(cap)
        for row in cand.get("probes") or []:
            if row.get("derived") == "no-go":
                no_go.append(f"probe:{row.get('probe')}")
            elif row.get("derived") != "proven":
                unproven.append(f"probe:{row.get('probe')}")
    if unproven:
        blockers.append("unproven on the candidate lane: "
                        + ", ".join(sorted(str(u) for u in unproven)))
    if no_go:
        blockers.append("recorded no-go: "
                        + ", ".join(sorted(str(u) for u in no_go)))
    return {"state": "blocked" if blockers else "passed",
            "summary": "supported hooks proven on the candidate lane "
                       "(capability matrix + compatibility probes)",
            "evidence": ev,
            "unproven_rows": sorted(str(u) for u in unproven),
            "no_go_rows": sorted(str(u) for u in no_go),
            "sources": _src_status(src, names),
            "blockers": blockers}


_EFFORT_BOUND_RE = re.compile(
    r"(?:≤|<=|at most|up to|max(?:imum)?)?\s*(\d+(?:\.\d+)?)\s*"
    r"(?:developer-?|dev-?)?days?", re.IGNORECASE)
_UNGROUNDED_RE = re.compile(
    r"unproven|cannot be grounded|not grounded|ungrounded|"
    r"unverifiable", re.IGNORECASE)


def _gate_effort_bound(src):
    """A1's "an effort estimate beyond three days blocks execution":
    a *grounded* ≤3-developer-day bound must be on record. The recorded
    bound marked ungrounded (hooks unproven) is exactly the replan
    trigger A1 names — blocked, never silently broadened."""
    names = ["assessment", "candidate"]
    ev = ["docs/adapters/admission-assessment-2026-10-05.json",
          "tests/fixtures/adapters/candidate-codex-cli.json"]
    effort_class = None
    if _ok(src, "assessment"):
        doc = _json(src, "assessment") or {}
        effort_class = (((doc.get("gates") or {})
                         .get("maintenance_cost") or {})
                        .get("maintenance") or {}).get("effort_class")
    if effort_class is None and _ok(src, "candidate"):
        cand = (_json(src, "candidate") or {}).get("candidate") or {}
        effort_class = (cand.get("incremental_maintenance") or {}
                        ).get("effort_class")
    if effort_class is None:
        return {"state": "unknown",
                "summary": "grounded effort bound ≤ 3 developer-days "
                           "recorded",
                "evidence": ev, "sources": _src_status(src, names),
                "blockers": ["no effort bound on record"]}
    blockers = []
    m = _EFFORT_BOUND_RE.search(str(effort_class))
    days = float(m.group(1)) if m else None
    if days is None:
        blockers.append("effort bound unparseable — no day estimate "
                        "on record")
    elif days > 3:
        blockers.append(f"effort estimate {days:g} developer-days > 3 "
                        "— beyond the task bound")
    if _UNGROUNDED_RE.search(str(effort_class)):
        blockers.append("recorded estimate flagged ungrounded (hooks "
                        "unproven) — the estimate cannot be grounded "
                        "today")
    return {"state": "blocked" if blockers else "passed",
            "summary": "grounded effort bound ≤ 3 developer-days "
                       "recorded",
            "evidence": ev,
            "effort_class": effort_class,
            "estimated_days": days,
            "sources": _src_status(src, names),
            "blockers": blockers}


def _gate_adapter_code_fence(adapter_dirs, other_gates_met):
    """The execution fence: adapter code on disk is lawful only inside
    an authorized implementation. Present while unmet is an
    unauthorized-execution signal — blocked and counted by the
    ``unauthorized_execution`` audit."""
    ev = [f"{d}/" for d in ADAPTER_CODE_DIRS]
    if adapter_dirs and not other_gates_met:
        return {"state": "blocked",
                "summary": "no adapter code on disk ahead of "
                           "authorization",
                "evidence": ev, "sources": {},
                "blockers": ["adapter code present while the "
                             "precondition is unmet: "
                             + ", ".join(adapter_dirs)]}
    return {"state": "passed",
            "summary": "no adapter code on disk ahead of "
                       "authorization",
            "evidence": ev, "sources": {},
            "blockers": []}


#: The checklist — evaluation order. ``adapter_code_fence`` is the only
#: order-sensitive row: it reads the other gates' states.
CHECKLIST_ORDER = [
    "assessment_recorded",
    "pilot_continue_confirmed",
    "operator_adoption",
    "adoption_scope",
    "adopted_candidate_match",
    "supported_hooks",
    "effort_bound",
    "adapter_code_fence",
]


def _evaluate_gates(src, adoption, assessed):
    """Evaluate the source-derived rows. The ``adapter_code_fence`` row
    is set by ``evaluate()`` — it derives from these gates plus the
    adapter-dir ledger and is recomputed identically on replay."""
    return {
        "assessment_recorded": _gate_assessment_recorded(src),
        "pilot_continue_confirmed": _gate_pilot_continue(src),
        "operator_adoption": _gate_operator_adoption(src, adoption),
        "adoption_scope": _gate_adoption_scope(src, adoption),
        "adopted_candidate_match": _gate_candidate_match(
            src, adoption, assessed),
        "supported_hooks": _gate_supported_hooks(src),
        "effort_bound": _gate_effort_bound(src),
    }


# --------------------------------------------------------------------------
# Precondition + audits
# --------------------------------------------------------------------------


def _precondition(gates):
    """Fail closed: ``met`` iff every checklist gate passed. Any other
    state — including ``pending`` adoption — is ``unmet``, and every
    non-passed row is named (denominator-preserving)."""
    non_passed = {g: v for g, v in gates.items()
                  if v["state"] != "passed"}
    pre = "met" if not non_passed else "unmet"
    auth = ("authorized-for-implementation" if pre == "met"
            else "blocked")
    reasons = [{"gate": g, "state": v["state"],
                "detail": "; ".join(v["blockers"]) or v["summary"]}
               for g, v in non_passed.items()]
    return pre, auth, reasons


#: What re-opening requires — enumerated, never implied. Mirrors the
#: F13 record's re-entry list plus task 5.2's own A1 bound.
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
    "naming the assessed candidate/version with support/trust recipe, "
    "bounded test scope, cost/capacity and acceptance gates",
    "a grounded effort estimate within the ≤3-developer-day bound — "
    "an estimate beyond three days or flagged ungrounded re-plans "
    "before execution (A1)",
]


#: What no outcome here can ever authorize.
NEVER_AUTHORIZED = [
    "adapter code execution or F13 delivery claims before the "
    "precondition reads met — an unmet check blocks execution pending "
    "scoped replan (A1)",
    "a second scheduler or transition engine (§6.1/boundary map)",
    "concurrency beyond one active task/project, additional preview "
    "providers, or broader harness/provider abstraction",
    "treating a recorded check — met or unmet — as the operator's "
    "adoption act itself (§8.4)",
    "dates promised for expansion",
]


def _audits(report, *, recorded_precondition=None,
            recorded_authorization=None, recorded_reasons=None):
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

    # Unmet-reason coverage — every non-passed gate named once.
    reasons = (recorded_reasons if recorded_reasons is not None
               else report.get("unmet_reasons")) or []
    named = {r["gate"] for r in reasons}
    non_passed = {g for g, v in gates.items() if v["state"] != "passed"}
    audits["unmet_reason_coverage"] = {
        "non_passed": sorted(non_passed),
        "named": sorted(named),
        "uncovered": sorted(non_passed - named),
        "phantom": sorted(named - non_passed),
    }

    # No authorization on unmet gates — the recorded precondition and
    # authorization must equal the recomputed pair (a fixture claiming
    # authorization over a non-passed gate is a breach).
    recomputed_pre, recomputed_auth, _ = _precondition(gates)
    recorded_pre = (recorded_precondition
                    if recorded_precondition is not None
                    else recomputed_pre)
    recorded_auth = (recorded_authorization
                     if recorded_authorization is not None
                     else recomputed_auth)
    audits["no_authorization_on_unmet"] = {
        "recomputed_precondition": recomputed_pre,
        "recorded_precondition": recorded_pre,
        "precondition_consistent": recorded_pre == recomputed_pre,
        "recomputed_authorization": recomputed_auth,
        "recorded_authorization": recorded_auth,
        "authorization_consistent": recorded_auth == recomputed_auth,
        "valid_vocabulary": (recorded_pre in PRECONDITIONS
                             and recorded_auth in AUTHORIZATIONS),
    }

    # Adoption consistency — a passed adoption gate requires a named
    # versioned candidate in the emitted adoption block.
    adoption = report.get("adoption") or {}
    adoption_gate = (gates.get("operator_adoption") or {}).get("state")
    audits["adoption_consistency"] = {
        "gate_state": adoption_gate,
        "candidate": adoption.get("candidate"),
        "candidate_version": adoption.get("candidate_version"),
        "ok": adoption_gate != "passed" or (
            bool(adoption.get("candidate"))
            and bool(adoption.get("candidate_version"))),
    }

    # Singleton reference — the assessed archive carries exactly one
    # candidate; the emitted assessed_candidate must match it.
    doc_candidates = ((report.get("assessment") or {})
                      .get("candidates_count"))
    assessed = report.get("assessed_candidate")
    audits["singleton_reference"] = {
        "candidates_count": doc_candidates,
        "assessed": assessed,
        "ok": assessed is not None and doc_candidates == 1,
    }

    # No adapter execution — the check produced no runtime code; the
    # emitted field must assert ``none``.
    audits["no_adapter_execution"] = {
        "adapter_execution": report.get("adapter_execution"),
        "ok": report.get("adapter_execution") == "none",
    }

    # Unauthorized execution — adapter code on disk is lawful only
    # under an authorized precondition.
    dirs = report.get("adapter_dirs_present") or []
    audits["unauthorized_execution"] = {
        "adapter_dirs_present": dirs,
        "authorization": recorded_auth,
        "ok": not dirs or recorded_auth ==
        "authorized-for-implementation",
    }

    # Aggregate-only disclosure — the adoption block carries no
    # content-bearing keys.
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

    walk(report.get("adoption") or {}, "$.adoption")
    audits["aggregate_only"] = {"bad_keys": bad_keys[:20]}

    return audits


def _breaches(audits):
    b = []
    d = audits["gate_denominator"]
    if d["unevaluated"] or d["bad_states"] or \
            d["evaluated"] != d["declared"]:
        b.append("gate_denominator")
    c = audits["unmet_reason_coverage"]
    if c["uncovered"] or c["phantom"]:
        b.append("unmet_reason_coverage")
    n = audits["no_authorization_on_unmet"]
    if not (n["precondition_consistent"]
            and n["authorization_consistent"]
            and n["valid_vocabulary"]):
        b.append("no_authorization_on_unmet")
    if not audits["adoption_consistency"]["ok"]:
        b.append("adoption_consistency")
    if not audits["singleton_reference"]["ok"]:
        b.append("singleton_reference")
    if not audits["no_adapter_execution"]["ok"]:
        b.append("no_adapter_execution")
    if not audits["unauthorized_execution"]["ok"]:
        b.append("unauthorized_execution")
    if audits["aggregate_only"]["bad_keys"]:
        b.append("aggregate_only")
    return b


def evaluate(report):
    """Bind gates → precondition → authorization → audits → verdict.
    Recomputable from ``report['gates']`` + ``report['adoption']`` +
    ``report['adapter_dirs_present']`` — ``reevaluate`` reruns this on
    an archive, where the *recorded* precondition/authorization/reasons
    are what the consistency audits check."""
    recorded_pre = report.get("precondition")
    recorded_auth = report.get("authorization")
    recorded_reasons = report.get("unmet_reasons")
    gates = report["gates"]
    # The adapter-code fence is a *derived* row — recomputed from the
    # recorded dirs and the other gates on both the live and replay
    # paths, so an archive cannot pass it by assertion alone.
    dirs = report.get("adapter_dirs_present") or []
    others_met = all(v["state"] == "passed" for g, v in gates.items()
                     if g != "adapter_code_fence")
    gates["adapter_code_fence"] = _gate_adapter_code_fence(
        dirs, others_met)
    pre, auth, reasons = _precondition(gates)
    report["precondition"] = pre
    report["authorization"] = auth
    report["unmet_reasons"] = reasons
    report["task_5_2"] = (
        "blocked — adapter implementation not authorized"
        if pre != "met" else
        "authorized — adapter implementation may proceed within the "
        "adopted bounded scope")
    report.setdefault("adapter_execution", "none")
    report["audits"] = _audits(
        report, recorded_precondition=recorded_pre,
        recorded_authorization=recorded_auth,
        recorded_reasons=recorded_reasons)
    report["breaches"] = _breaches(report["audits"])
    report["instrumentation"] = (
        "clean" if not report["breaches"] else "breached")
    report["verdict"] = (
        "precondition-check-complete" if not report["breaches"]
        else "precondition-check-instrumentation-failed")
    report["never_authorized"] = list(NEVER_AUTHORIZED)
    report["re_entry"] = list(RE_ENTRY)
    report["study_outcome"] = (
        "task-5.2 admission precondition audited — the gate is "
        "fail-closed: only a recorded operator adoption of the "
        "assessed singleton plus proven hooks and a grounded bound "
        "authorizes implementation; this check executes nothing")
    # Scan the *finished* report — gate reasons and the parsed adoption
    # carry source-derived text, so scanning the shell under-covers.
    leaks = _export.secret_leaks(
        {k: v for k, v in report.items() if k != "audits"})
    report["audits"]["secret_scan"] = (
        {"outcome": "clean"} if not leaks
        else {"outcome": "leak", "paths": leaks[:20]})
    if leaks and "secret_scan" not in report["breaches"]:
        report["breaches"].append("secret_scan")
        report["instrumentation"] = "breached"
        report["verdict"] = "precondition-check-instrumentation-failed"
    return report


def reevaluate(report):
    """Re-judge a recorded precondition archive from its recorded
    gates, adoption block and adapter-dir ledger — the replayability
    contract, and the fabricated-authorization check."""
    if not isinstance(report, dict) or \
            report.get("kind") != "adapter-precondition":
        raise AdapterPreconditionError(
            f"expected kind 'adapter-precondition', got "
            f"{report.get('kind')!r}" if isinstance(report, dict)
            else "not an adapter-precondition archive")
    if not isinstance(report.get("gates"), dict):
        raise AdapterPreconditionError(
            "archive carries no gates map")
    try:
        return evaluate(dict(report))
    except (KeyError, TypeError, AttributeError) as exc:
        raise AdapterPreconditionError(
            f"archive incomplete: {exc}") from exc


# --------------------------------------------------------------------------
# The run — read committed sources and audit the precondition
# --------------------------------------------------------------------------


def run(*, paths=None, repro_root=ROOT):
    """Read the committed source artifacts and evaluate whether task
    5.2's admission precondition holds. Read-only: nothing here
    executes adapter code, and ``unmet`` authorizes nothing."""
    paths = paths or {}
    src = load_sources(paths)
    adoption = _parse_adoption(_text(src, "f13_record") or "")
    assessed = _assessed_candidate(src)
    adapter_dirs = [d for d in ADAPTER_CODE_DIRS
                    if (Path(repro_root) / d).exists()]
    gates = _evaluate_gates(src, adoption, assessed)

    doc = _json(src, "assessment") or {}
    report = {
        "run_id": f"adapter-precondition-{int(time.time())}",
        "version": VERSION,
        "kind": "adapter-precondition",
        "mode": "precondition-check",
        "issue": 33,
        "task": "5.2",
        "generated_at": _utcnow(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "manifest": str(ROOT / ".factory-kit.yml"),
            "manifest_effective_digest": schema.effective_digest(
                schema.load_manifest_file(ROOT / ".factory-kit.yml")),
            "reproduce": "python3 tools/probes/adapter_precondition.py "
            "--write docs/adapters/"
            "adapter-precondition-$(date +%F).json",
        },
        "sources": {n: {k: v for k, v in s.items()
                        if k not in ("text", "json")}
                    for n, s in src.items()},
        "assessment": {
            "archive": src.get("assessment", {}).get("path"),
            "verdict": doc.get("verdict"),
            "disposition": doc.get("disposition"),
            "candidates_count": len(doc.get("candidates") or []),
        },
        "assessed_candidate": assessed,
        "adoption": adoption,
        "adapter_dirs_present": adapter_dirs,
        "scope": {
            "one_active_task_per_project": True,
            "additional_adapters": 0,
            "preview_provider": "selected-provider-only",
            "dates_promised": False,
            "broader_abstraction": False,
        },
        "gates": gates,
        "checklist": list(CHECKLIST_ORDER),
    }
    return evaluate(report)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", help="write the JSON report to a file")
    ap.add_argument("--fixture",
                    help="re-judge a recorded precondition archive")
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
    except AdapterPreconditionError as exc:
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
    print(f"precondition: {report['precondition']}")
    print(f"authorization: {report['authorization']}")
    print(f"task_5_2: {report['task_5_2']}")
    if report.get("unmet_reasons"):
        for r in report["unmet_reasons"]:
            print(f"  unmet: {r['gate']} [{r['state']}]")
    print(f"verdict: {report['verdict']}")
    if report["breaches"]:
        print(f"  breaches: {', '.join(report['breaches'])}")
    return 0 if report["verdict"] == "precondition-check-complete" else 1


if __name__ == "__main__":
    sys.exit(main())
