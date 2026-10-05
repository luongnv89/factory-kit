#!/usr/bin/env python3
"""factory-kit §1.4/§8.3 external-pilot observation harness — issue #29
(task 4.5, GATE-P05 / MET06 / MET07: three independent external
installations and repeat use).

Runs the proposed independent-onboarding and repeat-use study on the
published support recipe under the Task 4.4 consent/admission gate:

- **Admission first (A1/A4).** The pilot obligations row and each
  fixture participant's admission go through the real
  :mod:`factory_kit.privacy.consent` gate on a durable ``IntakeStore``.
  Collection for a participant opens only while
  :func:`consent.open_collection` returns ``open`` — a denied
  admission or absent consent blocks the leg outright, and nothing is
  recorded under that participant.
- **The pinned walkthrough, for real (A1).** Each admitted participant
  gets a fresh disposable repository and runs the documented
  ``python3 -m factory_kit.setup`` legs — ``plan`` → ``apply`` →
  ``readiness`` (against the participant's recorded environment
  readings) → ``status`` → ``uninstall`` → ``remove --history retain``
  — as real subprocesses, on three simulated distinct host
  environments (Darwin arm64, Linux x86_64, Darwin x86_64).
- **Minimized observation trail (A1/A5).** Every recorded study beat —
  install, first-run, removal, weekly-report — lands as a durable
  ``pilot_observation_recorded`` event keyed by ``participant_hash``,
  carrying only the agreed minimized fields. The raw participant
  reference never enters the event trail.
- **Honest classification (A3/A4).** Weekly reports classify as
  ``completed`` / ``failed`` / ``missing`` — failed runs are retained
  as attempts, missing weeks are labeled, download/star vanity counts
  are never collected or counted. Denied, blocked, install-failed and
  dropped-out participants stay in the denominators.

What this evidence is. A scripted-harness execution of the external
pilot protocol on simulated distinct environments: real gate, real
setup CLI, real durable observation events, real consent-gated export —
with participants, environments, elapsed setup minutes, assistance
records and weekly reports taken from ``tests/fixtures/pilot/``.

What it is not. Not a live external study: no real external
maintainers were recruited, no 60/90-day window elapsed, setup timing
is fixture-recorded rather than independently observed wall-clock, and
installs ran against disposable repositories with scripted readings —
never participant production hosts. Those are named live-observation
prerequisites in ``live_prerequisites`` — a fixture ``pass`` is never
a claim the live target was met.

Verdict semantics (A4/A5's honest-reporting contract):

- ``external-pilot-run-complete`` — every instrumentation audit clean
  (consent ordering, admission expectations, denominator integrity,
  takeover exclusion, weekly classification, minimized export).
  Study-target verdicts are data, not gate results: a ``fail`` on a
  proposed target is a legitimate study outcome and still exits 0.
- ``instrumentation-failed`` — an audit breached; exit 1.
- Per-target verdicts: ``pass`` / ``fail`` / ``missing`` /
  ``inconclusive`` — missing records and non-observable rows are
  reported, never excluded or replaced.

Exit codes (shared gi-* vocabulary):

    0  external-pilot-run-complete — audits clean (study verdicts inside)
    1  instrumentation-failed      — an observation-contract audit breached
    2  usage error                 — malformed invocation
    4  cannot complete             — fixture/report could not be loaded

Usage:

    python3 tools/probes/external_pilot.py                 # full run
    python3 tools/probes/external_pilot.py --write out.json
    python3 tools/probes/external_pilot.py --fixture out.json
    python3 tools/probes/external_pilot.py --fixtures DIR  # alt fixtures
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.diagnostics import report as _report
from factory_kit.durable import store as _durable
from factory_kit.events import schema as _schema
from factory_kit.privacy import consent as _consent
from factory_kit.privacy import export as _export
from tests.fixtures import setup_repo as _fx

VERSION = "1.0.0"
FIXTURES = ROOT / "tests" / "fixtures" / "pilot"

#: The restricted actor that records the study's own beats — the
#: observing operator, never the participant (the participant's side of
#: the line is their own environment; the study store holds only the
#: minimized observation fields the gate admitted).
OBSERVER = "pilot-study-observer"
SCOPE = _consent.PILOT_COLLECTION_SCOPE
EXPORT_SCOPE = "pilot-export"

#: Observation phases carried by ``pilot_observation_recorded``.
PHASES = ("install", "first-run", "removal", "weekly-report")

#: Weekly-report outcome classes — A3's completed-vs-attempt
#: distinction. ``failed`` is a retained attempt, ``missing`` is a
#: labeled absence; neither ever counts as a completed run.
RUN_OUTCOMES = ("completed", "failed", "missing")

#: Author-assistance classes (protocol.assistance_classes). Only
#: ``takeover`` excludes a participant from the independence
#: threshold (A2); lesser assistance is recorded, not disqualifying.
ASSISTANCE_CLASSES = ("none", "clarification", "takeover")

#: Proposed-target status at run time — Q9 remains unadopted.
Q9_TARGET_STATUS = (
    "proposed — pending owner acceptance (docs/decisions/q9-acceptance.md)"
)

#: The live-observation prerequisites a scripted harness cannot
#: satisfy — the issue's named blockers, surfaced rather than hidden.
LIVE_PREREQUISITES = [
    {
        "id": "q9-adoption",
        "blocker": "Q9 numerical targets are still proposed — owner "
        "acceptance pending (docs/decisions/q9-acceptance.md)",
    },
    {
        "id": "external-participants",
        "blocker": "no real external maintainers were recruited — the "
        "cohort is scripted fixtures on simulated environments, so "
        "independence is fixture-recorded, not observed",
    },
    {
        "id": "pilot-windows",
        "blocker": "the 60-day onboarding and 90-day repeat-use "
        "windows have not elapsed — days are fixture-declared, not "
        "calendar-observed",
    },
    {
        "id": "independent-timing",
        "blocker": "elapsed setup minutes are fixture-recorded "
        "observations, not independently measured wall-clock on a "
        "participant host",
    },
    {
        "id": "simulated-environments",
        "blocker": "installations ran on disposable repositories with "
        "recorded readiness readings — distinct simulated hosts, "
        "never participant production environments with live Hermes",
    },
    {
        "id": "live-repeat-use",
        "blocker": "weekly run reports are scripted fixture inputs — "
        "four consecutive live reporting weeks remain unobserved",
    },
]


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise PilotError(f"cannot load {path}: {exc}") from exc


class PilotError(Exception):
    """Fixture/report could not be read — exit 4."""


# --------------------------------------------------------------------------
# Observation recording — the minimized durable trail (A1/A4/A5)
# --------------------------------------------------------------------------


def _observe(
    store,
    spec,
    phase,
    *,
    outcome=None,
    blocker=None,
    elapsed_minutes=None,
    week_index=None,
    assistance_class=None,
    environment_ref=None,
):
    """Record one minimized study beat on the real event trail.

    Only ever called after :func:`consent.open_collection` returned
    ``open`` for this participant — the durable seq therefore orders
    every observation after the participant's consent event, which is
    exactly what the collection-ordering audit replays (A4).
    """
    if phase not in PHASES:
        raise PilotError(f"unknown observation phase {phase!r}")
    if assistance_class is not None and assistance_class not in ASSISTANCE_CLASSES:
        raise PilotError(f"unknown assistance class {assistance_class!r}")
    out = store.record_typed_event(
        "pilot_observation_recorded",
        properties={
            "participant_hash": _durable._participant_hash(spec["participant_ref"]),
            "scope": SCOPE,
            "phase": phase,
            "actor_ref": OBSERVER,
            "recorded_at": _utcnow(),
            "outcome": outcome,
            "blocker": blocker,
            "elapsed_minutes": elapsed_minutes,
            "week_index": week_index,
            "assistance_class": assistance_class,
            "environment_ref": environment_ref,
        },
    )
    return out["seq"]


# --------------------------------------------------------------------------
# The pinned walkthrough — real ``python3 -m factory_kit.setup`` legs
# --------------------------------------------------------------------------


def _cli(*args):
    """Run the published setup command exactly as the recipe writes it —
    the participant's real entry point, not a library shortcut."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "factory_kit.setup", *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        check=False,
    )
    return proc


def _emit_json(proc):
    """Parse a setup command's JSON payload when it emitted one."""
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return None


def _run_walkthrough(spec, env_root, fixture_dir):
    """Drive the pinned install/first-run/removal walkthrough on real
    setup CLI legs inside one simulated distinct environment.

    Returns the per-participant observation record: every leg's exit
    code against its expected verdict, the named blocker when the
    environment refuses (readiness is substantive — a failing reading
    exits 1 and names ``blocker_codes``), and the fixture-recorded
    observation fields (elapsed setup minutes, assistance, first-run
    and weekly outcomes).
    """
    env = spec["environment"]
    repo = _fx.build_repo(env_root / "repo")
    manifest = env_root / "candidate.factory-kit.yml"
    manifest.write_text(
        _fx.manifest_text(
            repo_id=env["repo_id"], owner=env["repo_owner"], name=env["repo_name"]
        ),
        encoding="utf-8",
    )
    state = env_root / "setup-state.json"
    registrations = env_root / "registrations.json"
    plan_path = env_root / "plan.json"
    removal_path = env_root / "removal.json"
    readings = fixture_dir / "readings" / env["readings"]

    expected_blocker = (spec.get("install") or {}).get("expected_blocker")

    legs = []

    def leg(name, argv, expect):
        proc = _cli(*[str(a) for a in argv])
        payload = _emit_json(proc)
        legs.append(
            {
                "leg": name,
                "command": "python3 -m factory_kit.setup " + " ".join(argv[:1]),
                "exit": proc.returncode,
                "expected_exit": expect,
                "ok": proc.returncode == expect,
                "verdict": (payload or {}).get("verdict"),
                "blocker_codes": (payload or {}).get("blocker_codes"),
            }
        )
        return proc, payload

    rec = {
        "key": spec["key"],
        "environment_ref": env["readings"].rsplit(".", 1)[0],
        "host_label": env.get("host_label"),
        "legs": legs,
        "install": {
            "outcome": None,
            "elapsed_minutes": (spec.get("install") or {}).get("elapsed_minutes"),
            "completed_day": (spec.get("install") or {}).get("completed_day"),
            "author_assistance": (spec.get("install") or {}).get("author_assistance"),
            "author_takeover": bool((spec.get("install") or {}).get("author_takeover")),
            "builder_active_minutes": (spec.get("install") or {}).get(
                "builder_active_minutes"
            ),
            "blocker": None,
        },
        "first_run": dict(spec.get("first_run") or {}),
        "weekly_reports": [dict(r) for r in spec.get("weekly_reports") or []],
        "dropout": spec.get("dropout"),
        "removal": dict(spec.get("removal") or {}),
        "study_waits": dict(spec.get("study_waits") or {}),
    }

    # 1 — inspect (read-only; exit 0 = appliable).
    leg("plan", ["plan", "--repo", repo, "--manifest", manifest, "--out", plan_path], 0)
    if not legs[-1]["ok"]:
        rec["install"]["outcome"] = "blocked"
        rec["install"]["blocker"] = "plan-refused"
        return rec

    # 2 — review, then apply (digest-bound; exit 0 = applied).
    leg(
        "apply",
        [
            "apply",
            "--repo",
            repo,
            "--plan",
            plan_path,
            "--accepted-by",
            spec["participant_ref"],
            "--state",
            state,
            "--registrations",
            registrations,
        ],
        0,
    )
    if not legs[-1]["ok"]:
        rec["install"]["outcome"] = "blocked"
        rec["install"]["blocker"] = "apply-refused"
        return rec

    # 3 — readiness (substantive; exit 0 = ready/dispatch allowed).
    expect = 1 if expected_blocker else 0
    _proc, payload = leg(
        "readiness",
        [
            "readiness",
            "--repo",
            repo,
            "--readings",
            readings,
            "--state",
            state,
            "--registrations",
            registrations,
        ],
        expect,
    )
    if not legs[-1]["ok"]:
        rec["install"]["outcome"] = "inconclusive"
        rec["install"]["blocker"] = "readiness-unexpected"
        return rec
    if (payload or {}).get("verdict") != "ready":
        # The environment itself refused — a real installation
        # failure, labeled and retained in the denominators (A4). An
        # unparseable readiness payload is never read as ready.
        codes = (payload or {}).get("blocker_codes") or []
        rec["install"]["outcome"] = "blocked"
        rec["install"]["blocker"] = codes[0] if codes else "readiness-not-ready"
        rec["install"]["blocker_codes"] = codes
        return rec

    # 4 — status (operator ledger read-back; exit 0).
    leg("status", ["status", "--repo", repo, "--state", state], 0)
    rec["install"]["outcome"] = "completed" if legs[-1]["ok"] else "inconclusive"
    if not legs[-1]["ok"]:
        rec["install"]["blocker"] = "status-failed"
        return rec

    # 5 — removal (reviewable plan, then applied; --history has no
    # default — retain keeps the consent tombstone). A participant who
    # never attempted removal — or whose install failed upstream —
    # runs no removal legs at all.
    if (spec.get("removal") or {}).get("outcome") in (
        "not-attempted",
        "not-applicable",
    ):
        return rec
    leg(
        "uninstall",
        [
            "uninstall",
            "--repo",
            repo,
            "--out",
            removal_path,
            "--state",
            state,
            "--registrations",
            registrations,
        ],
        0,
    )
    if not legs[-1]["ok"]:
        rec["removal"]["outcome"] = "failed"
        return rec
    leg(
        "remove",
        [
            "remove",
            "--repo",
            repo,
            "--plan",
            removal_path,
            "--history",
            "retain",
            "--accepted-by",
            spec["participant_ref"],
            "--state",
            state,
            "--registrations",
            registrations,
        ],
        0,
    )
    rec["removal"]["outcome"] = "completed" if legs[-1]["ok"] else "failed"
    return rec


# --------------------------------------------------------------------------
# Classification + audits (A3/A4/A5)
# --------------------------------------------------------------------------


def _classify_weeks(reports):
    """Fold one participant's weekly reports into the run counts and
    the longest completed-run streak. ``failed`` reports are retained
    attempts; ``missing`` weeks are absences — neither counts toward
    the consecutive-completed requirement (A3). A *gap* in the week
    index — a week no report exists for at all — breaks the streak the
    same way an explicit ``missing`` outcome does."""
    counts = {o: 0 for o in RUN_OUTCOMES}
    streak = best = 0
    prev_week = None
    for r in sorted(reports, key=lambda x: x.get("week") or 0):
        outcome = r.get("outcome")
        if outcome not in counts:
            outcome = "missing"
        counts[outcome] += 1
        week = r.get("week")
        gap = prev_week is not None and week is not None and week > prev_week + 1
        if outcome == "completed" and not gap:
            streak += 1
            best = max(best, streak)
        else:
            streak = 1 if outcome == "completed" else 0
            best = max(best, streak)
        prev_week = week
    return {
        "counts": counts,
        "longest_completed_streak": best,
        "weeks_reported": len(reports),
    }


def _audit_consent_ordering(store, blocked):
    """A4: every observation event follows its participant's consent
    event in durable seq order, and no observation event exists under
    a denied or never-presented participant hash."""
    consent_seq = {}
    for ev in store.event_rows("pilot_consent_recorded"):
        h = (json.loads(ev["detail"] or "{}")).get("participant_hash")
        if h:
            consent_seq[h] = ev["seq"]
    obs_by_hash = {}
    violations = []
    for ev in store.event_rows("pilot_observation_recorded"):
        detail = json.loads(ev["detail"] or "{}")
        h = detail.get("participant_hash")
        obs_by_hash.setdefault(h, []).append(ev)
        cseq = consent_seq.get(h)
        if cseq is None or ev["seq"] <= cseq:
            violations.append(
                {
                    "participant_hash": h,
                    "event_seq": ev["seq"],
                    "consent_seq": cseq,
                    "reason": "observation-outside-consent",
                }
            )
    blocked_hashes = {b["participant_hash"] for b in blocked}
    stray = [h for h in blocked_hashes if obs_by_hash.get(h)]
    for h in stray:
        violations.append(
            {"participant_hash": h, "reason": "observation-under-blocked-participant"}
        )
    return {
        "violations": violations,
        "consent_seqs": consent_seq,
        "observation_participants": sorted(obs_by_hash),
        "blocked_hashes_checked": sorted(blocked_hashes),
    }


def _audit_minimization(store):
    """A1/A5: every recorded observation event's properties are
    aggregate-safe — no identifying participant reference, no free
    text, no content-bearing fields ever entered the trail."""
    violations = []
    for ev in store.event_rows("pilot_observation_recorded"):
        detail = json.loads(ev["detail"] or "{}")
        if not _schema.is_aggregate_safe(detail):
            violations.append({"seq": ev["seq"], "detail": detail})
        if "participant_ref" in detail:
            violations.append({"seq": ev["seq"], "reason": "raw-participant-ref"})
    return {
        "events": len(store.event_rows("pilot_observation_recorded")),
        "violations": violations,
    }


def _audit_denominator(fixture_participants, records, blocked):
    """A4: every recruited participant stays in the denominator —
    admitted rows classify completed/blocked; non-admitted rows
    classify denied/consent-missing. None are dropped."""
    accounted = [r["key"] for r in records] + [b["key"] for b in blocked]
    expected = [p["key"] for p in fixture_participants]
    unaccounted = [k for k in expected if k not in accounted]
    classes = {}
    for r in records:
        cls = (
            "install-blocked"
            if r["install"]["outcome"] == "blocked"
            else "install-completed"
        )
        classes[r["key"]] = cls
    for b in blocked:
        classes[b["key"]] = f"blocked:{b['reason']}"
    return {
        "recruited": len(expected),
        "accounted": len(accounted),
        "unaccounted": unaccounted,
        "classes": classes,
    }


# --------------------------------------------------------------------------
# Target evaluation — verdict semantics per the module docstring
# --------------------------------------------------------------------------


def evaluate(report):
    """Bind measured observations to the exact proposed targets and
    re-check the instrumentation audits from the report's recorded
    inputs — recomputable on an archive (``reevaluate``)."""
    records = report["participants"]
    protocol = report["protocol"]
    targets = protocol.get("proposed_targets") or {}
    onboarding = targets.get("onboarding_completed") or {}
    independent = targets.get("independent_setup") or {}
    repeat = targets.get("repeat_use") or {}
    window_onboard = onboarding.get("within_days", 60)
    window_repeat = repeat.get("within_days", 90)

    onboard_completed = [
        r["key"]
        for r in records
        if r["install"]["outcome"] == "completed"
        and r["first_run"].get("outcome") == "completed"
        and (
            r["install"].get("completed_day") is not None
            and r["install"]["completed_day"] <= window_onboard
        )
    ]
    independent_completions = [
        r["key"]
        for r in records
        if r["install"]["outcome"] == "completed"
        and not r["install"]["author_takeover"]
        and r["install"]["elapsed_minutes"] is not None
        and r["install"]["elapsed_minutes"] <= independent.get("within_minutes", 45)
    ]
    takeover_excluded = [
        r["key"]
        for r in records
        if r["install"]["author_takeover"] and r["install"]["outcome"] == "completed"
    ]

    repeat_qualified = []
    for r in records:
        weeks = [
            w
            for w in r["weekly_reports"]
            if w.get("day") is not None and w["day"] <= window_repeat
        ]
        streak = _classify_weeks(weeks)["longest_completed_streak"]
        r["repeat_use"] = {
            "weeks_in_window": len(weeks),
            "longest_completed_streak": streak,
            "qualified": streak >= repeat.get("consecutive_weeks", 4),
        }
        if r["repeat_use"]["qualified"]:
            repeat_qualified.append(r["key"])

    results = []

    def add(tid, proposed, measured, verdict, **extra):
        row = {
            "id": tid,
            "proposed": proposed,
            "measured": measured,
            "verdict": verdict,
            "basis": "scripted-pilot-fixture",
            "live_status": "pending-live-study",
        }
        row.update(extra)
        results.append(row)

    add(
        "onboarding_completed_60d",
        f"at least {onboarding.get('count', 3)} external maintainers "
        f"complete installation and a first run within "
        f"{window_onboard} days of pilot opening",
        len(onboard_completed),
        "pass" if len(onboard_completed) >= onboarding.get("count", 3) else "fail",
        threshold=onboarding.get("count", 3),
        denominator=len(records),
        qualified=onboard_completed,
        window_days=window_onboard,
    )
    add(
        "independent_setup_2_in_45min",
        f"at least {independent.get('count', 2)} external maintainers "
        f"complete setup in {independent.get('within_minutes', 45)} "
        "minutes without author takeover",
        len(independent_completions),
        "pass"
        if len(independent_completions) >= independent.get("count", 2)
        else "fail",
        threshold=independent.get("count", 2),
        denominator=len(records),
        qualified=independent_completions,
        takeover_excluded=takeover_excluded,
        within_minutes=independent.get("within_minutes", 45),
    )
    add(
        "repeat_use_2x4wk_90d",
        f"at least {repeat.get('participants', 2)} external "
        f"maintainers complete one run per week for "
        f"{repeat.get('consecutive_weeks', 4)} consecutive weeks "
        f"within {window_repeat} days of pilot opening",
        len(repeat_qualified),
        "pass" if len(repeat_qualified) >= repeat.get("participants", 2) else "fail",
        threshold=repeat.get("participants", 2),
        denominator=len(records),
        qualified=repeat_qualified,
        window_days=window_repeat,
        failed_runs_retained=sum(
            r["weekly_classification"]["counts"]["failed"] for r in records
        ),
    )

    audits = dict(report.get("audits") or {})
    audits["denominator_integrity"] = _audit_denominator(
        report["fixture_participants"], records, report["blocked"]
    )
    breaches = []
    if audits.get("consent_ordering", {}).get("violations"):
        breaches.append("consent_ordering")
    if audits.get("observation_minimization", {}).get("violations"):
        breaches.append("observation_minimization")
    if audits["denominator_integrity"]["unaccounted"]:
        breaches.append("denominator_integrity")
    if audits.get("admission_gate", {}).get("mismatches"):
        breaches.append("admission_gate")
    if (audits.get("secret_scan") or {}).get("outcome") != "clean":
        breaches.append("secret_scan")

    report["targets"] = {"order": [r["id"] for r in results], "results": results}
    report["audits"] = audits
    report["instrumentation"] = "clean" if not breaches else "breached"
    report["breaches"] = breaches
    report["verdict"] = (
        "external-pilot-run-complete" if not breaches else "instrumentation-failed"
    )
    report["study_outcome"] = (
        "scripted rehearsal recorded — the live external study stays "
        "open on the named live_prerequisites; fixture verdicts are "
        "not live-target claims"
    )
    return report


def reevaluate(report):
    """Re-check an archived report's verdicts from its own recorded
    per-participant inputs — the replayability contract (A5)."""
    return evaluate(dict(report))


# --------------------------------------------------------------------------
# The run — admission gate, then observation only where consent opens
# --------------------------------------------------------------------------


def run(*, fixture_dir=FIXTURES):
    """Execute the scripted external-pilot study and return the
    observation report."""
    fixture_dir = Path(fixture_dir)
    fixture = _load_json(fixture_dir / "participants.json")
    terms = fixture.get("terms") or {}
    protocol = fixture.get("protocol") or {}
    participants = fixture.get("participants") or []
    if not participants:
        raise PilotError("fixture carries no participants")

    records, blocked, admissions = [], [], {}
    mismatches = []

    with tempfile.TemporaryDirectory(prefix="fk-pilot-") as tmp:
        store = _durable.IntakeStore(Path(tmp) / "pilot-study.db")

        # Phase 0 — the Task 4.4 admission context exists before any
        # participant data could (obligations + the export-scope
        # participant agreement the aggregate export gates on).
        obl = _consent.record_obligations(store, SCOPE, actor_ref=OBSERVER, **terms)
        store.record_participant_agreement(EXPORT_SCOPE, actor_ref=OBSERVER)

        # Phase 1 — admissions through the real gate. A
        # ``never-presented`` fixture participant simulates consent
        # never being offered: no admission row is created at all.
        for spec in participants:
            expected = spec.get("admission_expected")
            if expected == "never-presented":
                admissions[spec["key"]] = {"outcome": "never-presented"}
                continue
            admit = _consent.admit_participant(
                store,
                spec["participant_ref"],
                scope=SCOPE,
                actor_ref=OBSERVER,
                authority_key=spec.get("authority_key"),
                workload_class=spec.get("workload_class"),
                trust_class=spec.get("trust_class"),
            )
            # The raw participant_ref stays in the durable
            # pilot_participants row; the report carries the hash only
            # — the same minimization contract the event trail uses.
            admit = dict(admit)
            admit.pop("participant_ref", None)
            admit["participant_hash"] = _durable._participant_hash(
                spec["participant_ref"]
            )
            admissions[spec["key"]] = admit
            if expected and admit["outcome"] != expected:
                mismatches.append(
                    {
                        "key": spec["key"],
                        "expected": expected,
                        "observed": admit["outcome"],
                        "blocker": admit.get("blocker"),
                    }
                )
            if (
                expected == "denied"
                and spec.get("expected_blocker")
                and admit.get("blocker") != spec["expected_blocker"]
            ):
                mismatches.append(
                    {
                        "key": spec["key"],
                        "expected_blocker": spec["expected_blocker"],
                        "observed_blocker": admit.get("blocker"),
                    }
                )

        # Phase 2 — observation, gated per participant. The gate is
        # re-checked at collection time; a denied or absent consent
        # records a blocked leg and no observation data (A4).
        for spec in participants:
            key = spec["key"]
            gate = _consent.open_collection(store, spec["participant_ref"])
            if gate["outcome"] != "open":
                blocked.append(
                    {
                        "key": key,
                        "participant_hash": _durable._participant_hash(
                            spec["participant_ref"]
                        ),
                        "attempted_phase": (spec.get("collection_attempt") or {}).get(
                            "phase"
                        ),
                        "reason": gate.get("reason"),
                    }
                )
                continue
            env_root = Path(tmp) / key
            env_root.mkdir(parents=True, exist_ok=True)
            rec = _run_walkthrough(spec, env_root, fixture_dir)
            # Durable minimized beats — install, first-run, weekly
            # reports, removal — each under the opened consent.
            _observe(
                store,
                spec,
                "install",
                outcome=rec["install"]["outcome"],
                blocker=rec["install"]["blocker"],
                elapsed_minutes=rec["install"]["elapsed_minutes"],
                assistance_class=rec["install"]["author_assistance"],
                environment_ref=rec["environment_ref"],
            )
            _observe(store, spec, "first-run", outcome=rec["first_run"].get("outcome"))
            for w in rec["weekly_reports"]:
                _observe(
                    store,
                    spec,
                    "weekly-report",
                    outcome=w.get("outcome"),
                    week_index=w.get("week"),
                )
            _observe(store, spec, "removal", outcome=rec["removal"].get("outcome"))
            rec["weekly_classification"] = _classify_weeks(rec["weekly_reports"])
            records.append(rec)

        # Phase 3 — the consent-gated aggregate export: exists only
        # because the scope agreement stands; denied/revoked authority
        # keys are excluded inside it (A2/A5). A gate failure here is an
        # instrumentation defect — it raises, never degrades silently.
        export = _report.pilot_export(store, scope=EXPORT_SCOPE)

        report = {
            "run_id": f"external-pilot-{int(time.time())}",
            "version": VERSION,
            "kind": "external-pilot",
            "mode": "scripted-replay",
            "generated_at": _utcnow(),
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "walkthrough": "python3 -m factory_kit.setup — "
                "docs/recipes/installation.md pinned legs",
                "fixture_identity": {
                    "participants": fixture.get("fixture_id"),
                    "store": "IntakeStore SQLite — real consent gate",
                    "installs": "disposable repos + recorded "
                    "readings — simulated distinct hosts",
                },
                "reproduce": "python3 tools/probes/external_pilot.py "
                "--write docs/measurements/"
                "external-study-$(date +%F).json",
            },
            "protocol": protocol,
            "q9_target_status": Q9_TARGET_STATUS,
            "obligations": {
                "scope": SCOPE,
                "outcome": obl["outcome"],
                "terms_digest": obl["terms_digest"],
            },
            "fixture_participants": [
                {"key": p["key"], "admission_expected": p.get("admission_expected")}
                for p in participants
            ],
            "admissions": admissions,
            "participants": records,
            "blocked": blocked,
            "weekly_totals": {
                o: sum(r["weekly_classification"]["counts"][o] for r in records)
                for o in RUN_OUTCOMES
            },
            "study_effort": {
                "builder_active_minutes": sum(
                    r["install"]["builder_active_minutes"] or 0.0 for r in records
                ),
                "waits": {
                    "recruitment_wait_days": {
                        r["key"]: r["study_waits"].get("recruitment_wait_days")
                        for r in records
                    },
                    "participant_wait_days": {
                        r["key"]: r["study_waits"].get("participant_wait_days")
                        for r in records
                    },
                },
                "note": "active builder minutes are separated from "
                "calendar observation/recruitment and participant "
                "waits — waits never count as builder effort (A5)",
            },
            "export": {
                "outcome": (export or {}).get("outcome"),
                "scope": EXPORT_SCOPE,
                "consent": (export or {}).get("consent"),
                "events": len((export or {}).get("events") or []),
            },
            "audits": {
                "admission_gate": {"mismatches": mismatches},
                "consent_ordering": _audit_consent_ordering(store, blocked),
                "observation_minimization": _audit_minimization(store),
            },
            "live_prerequisites": LIVE_PREREQUISITES,
        }
        store.close()

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
        help="directory holding participants.json + readings/",
    )
    args = ap.parse_args(argv)

    try:
        if args.fixture:
            report = reevaluate(_load_json(args.fixture))
        else:
            report = run(fixture_dir=args.fixtures)
    except PilotError as exc:
        print(f"✗ cannot complete: {exc}", file=sys.stderr)
        return 4

    if args.write:
        Path(args.write).write_text(json.dumps(report, indent=1))
        print(f"wrote {args.write}")

    results = report["targets"]["results"]
    for r in results:
        print(
            f"  {r['id']}: {r['verdict']} (measured {r['measured']}, "
            f"proposed {r['proposed'][:60]}…)"
        )
    print(f"verdict: {report['verdict']}")
    if report["breaches"]:
        print(f"  breaches: {', '.join(report['breaches'])}")
    return 0 if report["verdict"] == "external-pilot-run-complete" else 1


if __name__ == "__main__":
    sys.exit(main())
