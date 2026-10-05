#!/usr/bin/env python3
"""§1.4/§8.3 external-pilot observation harness — issue #29 (task 4.5).

CI-side mechanics tests for ``tools/probes/external_pilot.py``: the
scripted external-pilot replay under the real Task 4.4
consent/admission gate, the pinned ``python3 -m factory_kit.setup``
walkthrough legs on simulated distinct environments, the minimized
``pilot_observation_recorded`` durable trail, weekly-report
classification (completed vs retained failed attempts vs labeled
missing), the denominator/consent-ordering audits and the
consent-gated aggregate export — all on the packaged
``tests/fixtures/pilot/`` fixtures.

Run:  python3 -m unittest tests.benchmarks.test_external_pilot -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PROBES = ROOT / "tools" / "probes"
if str(PROBES) not in sys.path:
    sys.path.insert(0, str(PROBES))

import external_pilot as ep

from factory_kit.durable import store as _durable
from factory_kit.privacy import export as _export

#: Canary convention — assembled, never a literal secret in source.
CANARY = "ghp_" + "b2" * 20

_RUN = None


def _run_once():
    """The scripted study is fast (subprocess legs, no network) — run
    it once per test process and reuse the report."""
    global _RUN
    if _RUN is None:
        _RUN = ep.run()
    return _RUN


def _participant(report, key):
    return next(p for p in report["participants"] if p["key"] == key)


class TestPilotRun(unittest.TestCase):
    """The scripted run over the packaged fixtures."""

    def test_run_completes_clean(self):
        report = _run_once()
        self.assertEqual(report["verdict"], "external-pilot-run-complete")
        self.assertEqual(report["breaches"], [])
        self.assertEqual(report["instrumentation"], "clean")
        self.assertEqual(report["mode"], "scripted-replay")

    def test_admission_gate_verdicts(self):
        report = _run_once()
        adm = report["admissions"]
        for key in ("obs-alpha", "obs-beta", "obs-gamma", "obs-delta"):
            self.assertEqual(adm[key]["outcome"], "admitted", key)
        # The unsupported workload is denied with the named blocker —
        # a durable audit row, never a silent drop (A4).
        self.assertEqual(adm["denied-wl"]["outcome"], "denied")
        self.assertEqual(
            adm["denied-wl"]["blocker"], "unsupported-workload:runtime-mirror"
        )
        # Consent never presented → no admission row at all.
        self.assertEqual(adm["nonconsent"]["outcome"], "never-presented")
        self.assertEqual(report["audits"]["admission_gate"]["mismatches"], [])

    def test_denominator_integrity(self):
        """A4 — every recruited participant stays accounted; denied,
        blocked, install-failed and dropout rows are labeled."""
        den = _run_once()["audits"]["denominator_integrity"]
        self.assertEqual(den["recruited"], 6)
        self.assertEqual(den["accounted"], 6)
        self.assertEqual(den["unaccounted"], [])
        self.assertEqual(
            den["classes"],
            {
                "obs-alpha": "install-completed",
                "obs-beta": "install-completed",
                "obs-gamma": "install-completed",
                "obs-delta": "install-blocked",
                "denied-wl": "blocked:admission-denied:unsupported-workload:"
                "runtime-mirror",
                "nonconsent": "blocked:consent-missing",
            },
        )


class TestWalkthroughLegs(unittest.TestCase):
    """A1 — the pinned recipe's legs ran as real CLI exits per
    admitted participant, in three distinct environments."""

    def test_three_distinct_environments_completed(self):
        report = _run_once()
        envs = set()
        for key in ("obs-alpha", "obs-beta", "obs-gamma"):
            p = _participant(report, key)
            self.assertEqual(p["install"]["outcome"], "completed", key)
            envs.add(p["environment_ref"])
            self.assertTrue(p["host_label"], key)
        # Three genuinely distinct simulated hosts — not one env
        # replayed three times.
        self.assertEqual(envs, {"env-alpha", "env-beta", "env-gamma"})

    def test_full_walkthrough_leg_set(self):
        report = _run_once()
        p = _participant(report, "obs-alpha")
        legs = [(leg["leg"], leg["exit"], leg["ok"]) for leg in p["legs"]]
        self.assertEqual(
            legs,
            [
                ("plan", 0, True),
                ("apply", 0, True),
                ("readiness", 0, True),
                ("status", 0, True),
                ("uninstall", 0, True),
                ("remove", 0, True),
            ],
        )

    def test_install_failure_named_and_retained(self):
        """A4 — the readiness-blocked environment is a real install
        failure: exit 1, the named blocker, no first run, removal not
        applicable — and the row stays in the denominators."""
        p = _participant(_run_once(), "obs-delta")
        self.assertEqual(p["install"]["outcome"], "blocked")
        self.assertEqual(p["install"]["blocker"], "unsupported-version")
        legs = {leg["leg"]: leg for leg in p["legs"]}
        self.assertEqual(legs["readiness"]["exit"], 1)
        self.assertIn("unsupported-version", legs["readiness"]["blocker_codes"])
        # Nothing after the blocker — the failed install does not
        # silently walk on to status/removal.
        self.assertNotIn("status", legs)
        self.assertEqual(p["first_run"]["outcome"], "not-attempted")
        self.assertEqual(p["removal"]["outcome"], "not-applicable")

    def test_removal_walkthrough_ran(self):
        report = _run_once()
        for key in ("obs-alpha", "obs-beta"):
            p = _participant(report, key)
            self.assertEqual(p["removal"]["outcome"], "completed", key)
            names = [leg["leg"] for leg in p["legs"]]
            self.assertIn("uninstall", names)
            self.assertIn("remove", names)


class TestConsentOrdering(unittest.TestCase):
    """A4 — collection never bypasses the Task 4.4 admission gate."""

    def test_no_observation_outside_consent(self):
        audit = _run_once()["audits"]["consent_ordering"]
        self.assertEqual(audit["violations"], [])
        # Observation events exist only under admitted hashes.
        self.assertEqual(
            sorted(audit["observation_participants"]),
            sorted(
                _durable._participant_hash(ref)
                for ref in (
                    "gh-ext/obs-alpha",
                    "gh-ext/obs-beta",
                    "gh-ext/obs-gamma",
                    "gh-ext/obs-delta",
                )
            ),
        )

    def test_blocked_participants_collected_nothing(self):
        report = _run_once()
        blocked = {b["key"]: b for b in report["blocked"]}
        self.assertEqual(
            blocked["denied-wl"]["reason"],
            "admission-denied:unsupported-workload:runtime-mirror",
        )
        self.assertEqual(blocked["nonconsent"]["reason"], "consent-missing")
        # The minimization audit already proved zero stray events;
        # assert the blocked legs carry no observation payload either.
        for b in report["blocked"]:
            self.assertNotIn("legs", b)
            self.assertNotIn("weekly_reports", b)

    def test_observation_events_minimized(self):
        audit = _run_once()["audits"]["observation_minimization"]
        self.assertEqual(audit["violations"], [])
        # 4 admitted participants × (install + first-run + 4 weekly +
        # removal) = 28 minimized beats.
        self.assertEqual(audit["events"], 28)


class TestTargetVerdicts(unittest.TestCase):
    """A2/A3 — the proposed targets reported exactly, with
    denominators and windows retained."""

    def _results(self):
        return {r["id"]: r for r in _run_once()["targets"]["results"]}

    def test_onboarding_target(self):
        r = self._results()["onboarding_completed_60d"]
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["measured"], 3)
        self.assertEqual(r["denominator"], 4)
        self.assertEqual(r["window_days"], 60)
        self.assertEqual(sorted(r["qualified"]), ["obs-alpha", "obs-beta", "obs-gamma"])

    def test_independence_threshold_excludes_takeover(self):
        """A2 — gamma's takeover install is explicitly recorded and
        cannot count toward the independence leg."""
        r = self._results()["independent_setup_2_in_45min"]
        self.assertEqual(r["verdict"], "pass")
        self.assertEqual(r["measured"], 2)
        self.assertEqual(sorted(r["qualified"]), ["obs-alpha", "obs-beta"])
        self.assertEqual(r["takeover_excluded"], ["obs-gamma"])
        p = _participant(_run_once(), "obs-gamma")
        self.assertTrue(p["install"]["author_takeover"])
        self.assertEqual(p["install"]["author_assistance"], "takeover")

    def test_repeat_use_target_fails_honestly(self):
        """A3 — only obs-alpha reaches four consecutive completed
        weeks; the fixture verdict is a legitimate ``fail``, still
        exit 0."""
        r = self._results()["repeat_use_2x4wk_90d"]
        self.assertEqual(r["verdict"], "fail")
        self.assertEqual(r["measured"], 1)
        self.assertEqual(r["threshold"], 2)
        self.assertEqual(r["qualified"], ["obs-alpha"])
        self.assertEqual(r["window_days"], 90)

    def test_failed_and_missing_weeks_classified(self):
        """A3/A4 — beta's failed week is a retained attempt, gamma's
        dropout weeks are labeled missing; neither counts as
        completed."""
        report = _run_once()
        beta = _participant(report, "obs-beta")
        self.assertEqual(
            beta["weekly_classification"]["counts"],
            {"completed": 3, "failed": 1, "missing": 0},
        )
        self.assertEqual(beta["repeat_use"]["longest_completed_streak"], 2)
        gamma = _participant(report, "obs-gamma")
        self.assertEqual(
            gamma["weekly_classification"]["counts"],
            {"completed": 2, "failed": 0, "missing": 2},
        )
        self.assertEqual(gamma["dropout"]["after_week"], 2)
        self.assertEqual(
            report["weekly_totals"], {"completed": 9, "failed": 1, "missing": 6}
        )
        # The retained failed run is counted in the target's audit
        # field — attempts are visible, never counted as runs.
        res = {r["id"]: r for r in report["targets"]["results"]}
        self.assertEqual(res["repeat_use_2x4wk_90d"]["failed_runs_retained"], 1)

    def test_week_gap_breaks_completed_streak(self):
        """A3 — an unreported week between reports breaks a completed
        streak exactly like an explicit missing outcome does."""
        cls = ep._classify_weeks(
            [
                {"week": 1, "outcome": "completed"},
                {"week": 2, "outcome": "completed"},
                {"week": 4, "outcome": "completed"},
            ]
        )
        self.assertEqual(cls["longest_completed_streak"], 2)
        cls = ep._classify_weeks(
            [
                {"week": 1, "outcome": "completed"},
                {"week": 2, "outcome": "completed"},
                {"week": 3, "outcome": "completed"},
                {"week": 4, "outcome": "completed"},
            ]
        )
        self.assertEqual(cls["longest_completed_streak"], 4)


class TestEffortSeparationAndExport(unittest.TestCase):
    """A5 — builder work separated from calendar waits; the export is
    consent-gated and carries no identifying content."""

    def test_effort_wait_separation(self):
        eff = _run_once()["study_effort"]
        self.assertEqual(eff["builder_active_minutes"], 51.0)
        waits = eff["waits"]
        self.assertEqual(
            waits["recruitment_wait_days"],
            {"obs-alpha": 8.0, "obs-beta": 11.0, "obs-gamma": 14.0, "obs-delta": 9.0},
        )
        self.assertIn("participant_wait_days", waits)

    def test_consent_gated_export(self):
        report = _run_once()
        self.assertEqual(report["export"]["outcome"], "exported")
        self.assertEqual(
            report["export"]["consent"]["excluded_authorities"], ["ext-denied-wl"]
        )

    def test_report_secret_free(self):
        report = _run_once()
        self.assertEqual(report["audits"]["secret_scan"], {"outcome": "clean"})
        self.assertEqual(_export.secret_leaks(report), [])
        # The raw participant reference never leaves the study store:
        # the whole report hashes identities.
        blob = json.dumps(report)
        for ref in (
            "gh-ext/obs-alpha",
            "gh-ext/obs-beta",
            "gh-ext/obs-gamma",
            "gh-ext/obs-delta",
            "gh-ext/denied-wl",
            "gh-ext/nonconsent",
        ):
            self.assertNotIn(ref, blob)


class TestReplayability(unittest.TestCase):
    """A5 — the archived report re-evaluates to identical verdicts."""

    def test_reevaluate_stable(self):
        report = _run_once()
        again = ep.reevaluate(json.loads(json.dumps(report)))
        self.assertEqual(again["verdict"], report["verdict"])
        self.assertEqual(
            [r["verdict"] for r in again["targets"]["results"]],
            [r["verdict"] for r in report["targets"]["results"]],
        )
        self.assertEqual(again["breaches"], report["breaches"])

    def test_fixture_reload_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            out.write_text(json.dumps(_run_once()))
            self.assertEqual(ep.main(["--fixture", str(out)]), 0)


class TestLivePrerequisites(unittest.TestCase):
    """The named live-observation blockers stay visible — a fixture
    verdict is never a live-target claim."""

    def test_prerequisites_named(self):
        ids = {p["id"] for p in _run_once()["live_prerequisites"]}
        self.assertEqual(
            ids,
            {
                "q9-adoption",
                "external-participants",
                "pilot-windows",
                "independent-timing",
                "simulated-environments",
                "live-repeat-use",
            },
        )

    def test_targets_live_status_pending(self):
        for r in _run_once()["targets"]["results"]:
            self.assertEqual(r["live_status"], "pending-live-study")
            self.assertEqual(r["basis"], "scripted-pilot-fixture")


if __name__ == "__main__":
    unittest.main()
