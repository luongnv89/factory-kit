#!/usr/bin/env python3
"""Hostile boundary probes for external-workload admission (issue #28 /
Task 4.4, A3–A4).

These checks are *independent of the admission code's own claims*: the
fixtures below actually attempt the hostile actions (filesystem read
outside the workspace, outbound network exfiltration, privileged
writes) inside the isolation mechanism under test, and assert the
platform denies them. A scripted runner simulates both denial and
breach so the verdict logic is exercised deterministically; when the
host provides a real mechanism (``sandbox-exec`` on macOS, ``bwrap`` on
Linux) the suite executes the fixtures for real.

- A3 — process/filesystem/network boundaries plus absence of
  controller, production and unrestricted credentials are
  *demonstrated*, not asserted.
- A4 — anything missing or failed yields a named blocker; a worktree,
  a prompt rule and branch protection can never be isolation evidence,
  even when a document claims ``verified``.
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.privacy import boundary  # noqa: E402


class _Proc:
    """Scripted subprocess result — the injected runner returns these
    so a test can simulate a platform denial or a breach without the
    mechanism."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _denying_runner(argv, env=None, cwd=None, timeout=None):
    return _Proc(stdout="PROBE:DENIED:PermissionError\n")


def _failing_runner(argv, env=None, cwd=None, timeout=None):
    """Everything the workload tries succeeds — a real breach."""
    return _Proc(stdout="PROBE:ALLOWED\n")


def _exploding_runner(argv, env=None, cwd=None, timeout=None):
    raise AssertionError(
        "hostile fixture executed without an isolation mechanism")


def _garbage_runner(argv, env=None, cwd=None, timeout=None):
    return _Proc(stdout="", stderr="sandbox-exec: compile failed",
                 returncode=71)


# ---------------------------------------------------------------------------
# A4 — mechanisms that are never isolation evidence
# ---------------------------------------------------------------------------

class TestNonIsolation(unittest.TestCase):

    def test_named_non_isolation_claims_never_verify(self):
        for fake in boundary.NON_ISOLATION:
            ev = {"mechanism": fake, "outcome": "verified",
                  "probes": {n: {"result": "denied"}
                             for n in boundary.PROBE_NAMES},
                  "credentials": {"unrestricted_absent": True}}
            verdict = boundary.verify_boundary(ev)
            self.assertFalse(verdict["ok"], fake)
            self.assertEqual(verdict["reason"], f"non-isolation:{fake}")

    def test_non_isolation_run_executes_no_fixtures(self):
        """A worktree/prompt-rule/branch-protection claim never even
        spawns the hostile fixtures — there is nothing to probe."""
        for fake in boundary.NON_ISOLATION:
            ev = boundary.run_probes(fake, runner=_exploding_runner)
            self.assertEqual(ev["outcome"], "unverified")
            self.assertEqual(ev["blocker"], f"non-isolation:{fake}")

    def test_missing_mechanism_names_the_blocker(self):
        ev = boundary.run_probes("none", runner=_exploding_runner)
        self.assertEqual(ev["outcome"], "unverified")
        self.assertEqual(ev["blocker"], "isolation-mechanism-missing")
        self.assertFalse(boundary.verify_boundary(ev)["ok"])

    def test_malformed_evidence_rejected(self):
        for bad in (None, "seatbelt", [], {"mechanism": None},
                    {"mechanism": "seatbelt"}):
            verdict = boundary.verify_boundary(bad)
            self.assertFalse(verdict["ok"], bad)


# ---------------------------------------------------------------------------
# A3 — the probe suite under a simulated mechanism
# ---------------------------------------------------------------------------

def _host_which(name):
    """Pretend the mechanism binary exists — the scripted runner stands
    in for it, so these verdict tests run the same on any CI host
    (without it, Linux has no ``sandbox-exec`` and every probe is
    honestly ``inconclusive``)."""
    return f"/usr/bin/{name}"


class TestProbeSuite(unittest.TestCase):

    def test_all_denied_is_verified(self):
        ev = boundary.run_probes("seatbelt", runner=_denying_runner,
                                 which=_host_which)
        self.assertEqual(ev["outcome"], "verified")
        self.assertIsNone(ev["blocker"])
        for name in boundary.PROBE_NAMES:
            self.assertEqual(ev["probes"][name]["result"], "denied")
        self.assertTrue(ev["evidence_ref"].startswith(
            "seatbelt:sha256:"))
        self.assertTrue(boundary.verify_boundary(ev)["ok"])

    def test_one_allowed_probe_is_a_breach(self):
        """Any successful hostile action fails the workload and names
        the breached boundary (A4)."""
        calls = {"n": 0}

        def allowing_one(argv, env=None, cwd=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 2:  # net-egress
                return _Proc(stdout="PROBE:ALLOWED\n")
            return _Proc(stdout="PROBE:DENIED:EPERM\n")

        ev = boundary.run_probes("seatbelt", runner=allowing_one,
                                 which=_host_which)
        self.assertEqual(ev["outcome"], "failed")
        self.assertEqual(ev["blocker"], "boundary-breach:net-egress")
        self.assertFalse(boundary.verify_boundary(ev)["ok"])

    def test_inconclusive_probe_is_not_verified(self):
        """A probe whose result cannot be determined is not proof —
        the blocker names the inconclusive fixture (A4)."""
        ev = boundary.run_probes("seatbelt", runner=_garbage_runner,
                                 which=_host_which)
        self.assertEqual(ev["outcome"], "unverified")
        self.assertTrue(ev["blocker"].startswith(
            "boundary-probe-inconclusive:"))

    def test_forged_verified_outcome_does_not_pass(self):
        """verify_boundary re-derives the verdict from the per-probe
        results — an evidence doc claiming ``verified`` with an allowed
        probe is rejected."""
        ev = {"mechanism": "seatbelt", "outcome": "verified",
              "probes": {n: {"result": "denied"}
                         for n in boundary.PROBE_NAMES},
              "credentials": {"unrestricted_absent": True}}
        ev["probes"]["privileged-write"] = {"result": "allowed"}
        verdict = boundary.verify_boundary(ev)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["reason"],
                         "probe-not-denied:privileged-write")

    def test_restricted_credentials_in_workload_env_breach(self):
        """A workload environment carrying controller/provider tokens
        is a credential-scope breach — absence is demonstrated (A3)."""
        ev = boundary.run_probes(
            "seatbelt", runner=_denying_runner, which=_host_which,
            env={"PATH": "/usr/bin", "GH_TOKEN": "x",
                 "AWS_SECRET_ACCESS_KEY": "y"})
        self.assertEqual(ev["outcome"], "failed")
        self.assertEqual(ev["blocker"],
                         "boundary-breach:credential-scope")
        self.assertEqual(ev["credentials"]["rejected"],
                         ["AWS_SECRET_ACCESS_KEY", "GH_TOKEN"])
        # Credential *names* are reported; their values never are.
        self.assertNotIn("x", str(ev["credentials"]["rejected"]))
        self.assertNotIn("y", str(ev["credentials"]["rejected"]))
        verdict = boundary.verify_boundary(ev)
        self.assertFalse(verdict["ok"])

    def test_env_values_never_appear_in_evidence(self):
        creds = boundary.credential_scope_ok(
            {"GH_TOKEN": "tok-value", "PATH": "/p"})
        self.assertEqual(creds["unrestricted_absent"], False)
        self.assertNotIn("tok-value", str(creds))


# ---------------------------------------------------------------------------
# A3 — the real host mechanism executes the hostile fixtures
# ---------------------------------------------------------------------------

class TestHostMechanism(unittest.TestCase):

    def test_detected_mechanism_denies_the_fixtures(self):
        """When the host provides seatbelt or bubblewrap, run the real
        fixtures: fs-read outside, outbound exfiltration and a
        privileged write must actually be denied by the platform. On a
        host without either the honest verdict is unverified."""
        mechanism = boundary.detect_mechanism()
        if mechanism is None:
            self.skipTest("no isolation mechanism on this host")
        with tempfile.TemporaryDirectory() as ws:
            ev = boundary.run_probes(mechanism, workspace=ws)
        self.assertEqual(ev["outcome"], "verified", ev["probes"])
        for name in ("fs-read-outside", "net-egress",
                     "privileged-write", "credential-scope"):
            self.assertEqual(ev["probes"][name]["result"], "denied",
                             f"{name}: {ev['probes'][name]}")
        self.assertTrue(boundary.verify_boundary(ev)["ok"])

    def test_hostile_read_outside_workspace_actually_fails(self):
        """A3 fixture: reading /etc/passwd under the real mechanism is
        denied — not skipped, not simulated."""
        mechanism = boundary.detect_mechanism()
        if mechanism is None:
            self.skipTest("no isolation mechanism on this host")
        with tempfile.TemporaryDirectory() as ws:
            ev = boundary.run_probes(mechanism, workspace=ws)
        probe = ev["probes"]["fs-read-outside"]
        self.assertEqual(probe["result"], "denied")
        # The denial comes from the sandbox (PermissionError) or the
        # isolated view (FileNotFoundError) — never a silent pass.
        self.assertIn(probe["detail"],
                      ("PermissionError", "FileNotFoundError", ""))


if __name__ == "__main__":
    unittest.main()
