#!/usr/bin/env python3
"""Internal v1.0 evidence gate — executable audit check (issue #24 /
Task 3.10).

``docs/evidence/v1.0-gate.md`` is the assembled requirement-to-evidence
audit; this module makes its mechanical claims reproducible instead of
trusting the prose:

- every recorded evidence archive exists, parses, carries a passing
  verdict and the selected fixture identity, and re-checks clean through
  its own ``--fixture`` mode on this tree;
- the §8.2 archive asserts the gate numbers the audit quotes (19 rows,
  57 repetitions, zero release-gate audit violations);
- the gate ledger records the honest verdicts — seven gates open and
  GATE-M02 closed — and names every carried blocker rather than claiming
  it satisfied;
- the owner acceptance record keeps the release decision pending (Q9
  unaccepted, GATE-M02 closed ⇒ no-go per the issue's own rule) and the
  README links both artifacts.

Run:  python3 -m unittest tests.evidence.test_v1_gate -v
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE_DOC = ROOT / "docs" / "evidence" / "v1.0-gate.md"
ACCEPTANCE_DOC = ROOT / "docs" / "decisions" / "v1.0-acceptance.md"
README = ROOT / "README.md"

ARCHIVES = {
    "fault_matrix": (
        ROOT / "docs/evidence/fault-matrix-2026-10-05.json",
        "fault-matrix-passed",
        "tools/probes/fault_matrix.py"),
    "v1_targets": (
        ROOT / "docs/measurements/v1-targets-2026-10-05.json",
        "targets-passed",
        "tools/probes/local_targets.py"),
    "endpoint": (
        ROOT / "docs/spike/evidence/endpoint-2026-10-05.json",
        "endpoint-demonstrated",
        "tools/probes/endpoint_walkthrough.py"),
    "spike_faults": (
        ROOT / "docs/spike/evidence/faults-2026-10-05.json",
        "fault-suite-passed",
        "tools/probes/spike_faults.py"),
}

# The carried live-endpoint blockers the audit must name rather than
# claim satisfied (owners on file in the decision records).
LIVE_BLOCKERS = (
    "base-unprotected",
    "telegram-adapter-live",
    "vercel-linkage",
    "kanban-live-write",
)


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _recheck(probe, archive):
    """Re-evaluate one recorded run through its own --fixture mode —
    the same command the gate doc publishes under Reproduce."""
    return subprocess.run(
        [sys.executable, str(ROOT / probe), "--fixture", str(archive)],
        capture_output=True, text=True, cwd=str(ROOT))


class TestRecordedRuns(unittest.TestCase):
    """Every archive the audit cites exists, parses and passes."""

    def test_archives_exist_and_carry_passing_verdicts(self):
        for name, (path, verdict, _probe) in ARCHIVES.items():
            with self.subTest(archive=name):
                self.assertTrue(path.is_file(), f"missing archive {path}")
                report = _load(path)
                self.assertEqual(report.get("verdict"), verdict)

    def test_fault_matrix_release_numbers(self):
        report = _load(ARCHIVES["fault_matrix"][0])
        totals = report["totals"]
        self.assertEqual(totals["rows"], 19)
        self.assertEqual(totals["repetitions"], 57)
        self.assertEqual(totals["passed_repetitions"], 57)
        self.assertEqual(report["failed_rows"], [])
        # Every row ran exactly the required three independent reps, and
        # every repetition's release-gate audit counters are zero —
        # the GATE-M07 evidence the audit quotes.
        zero_keys = ("duplicate_prs", "unauthorized_merges",
                     "accepted_fenced_results", "lost_acknowledged_tasks",
                     "stale_counted_passing")
        self.assertEqual(len(report["rows"]), 19)
        for row_id in [f"FAULT{i:02d}" for i in range(1, 20)]:
            row = report["rows"][row_id]
            self.assertEqual(row["status"], "pass", row_id)
            self.assertEqual(row["passed_reps"], 3, row_id)
            self.assertEqual(len(row["reps"]), 3, row_id)
            for rep in row["reps"]:
                self.assertTrue(rep["pass"], f"{row_id} rep {rep['rep']}")
                self.assertEqual(rep["audit"]["violations"], [])
                for key in zero_keys:
                    self.assertEqual(
                        rep["audit"]["counters"][key], 0,
                        f"{row_id} rep {rep['rep']} {key}")

    def test_fixture_identity_is_the_selected_recipe(self):
        """The fault matrix and v1-targets archives must carry the same
        selected repository + manifest/policy digests — the exact
        fixture identity A1 demands of every evidence row."""
        fm = _load(ARCHIVES["fault_matrix"][0])["environment"]
        vt = _load(ARCHIVES["v1_targets"][0])["environment"]
        self.assertEqual(
            fm["fixture_identity"]["repo_id"], "R_kgDOQncOdA")
        self.assertEqual(vt["repo_id"], "R_kgDOQncOdA")
        self.assertEqual(vt["repository"], "luongnv89/money-mind")
        self.assertEqual(
            fm["manifest_digest"], vt["manifest_effective_digest"])
        self.assertEqual(fm["policy_digest"], vt["policy_digest"])
        self.assertIn("hermes/0.21.5", fm["supported_versions"])

    def test_fixture_rechecks_pass_on_this_tree(self):
        for name, (path, _verdict, probe) in ARCHIVES.items():
            with self.subTest(archive=name):
                proc = _recheck(probe, path)
                self.assertEqual(
                    proc.returncode, 0,
                    f"{probe} --fixture failed: {proc.stderr[-400:]}")


class TestGateDocument(unittest.TestCase):
    """The audit document records the honest gate state."""

    @classmethod
    def setUpClass(cls):
        cls.text = GATE_DOC.read_text(encoding="utf-8")

    def test_all_eight_gates_have_verdicts(self):
        for n in range(1, 9):
            self.assertIn(f"GATE-M0{n}", self.text)
        # The audit's own ledger line: seven open, one closed.
        self.assertIn("7 of 8 gates open", self.text)

    def test_gate_m02_recorded_closed_with_blockers(self):
        row = re.search(r"\| *GATE-M02 *\|.*\|", self.text)
        self.assertIsNotNone(row, "GATE-M02 ledger row missing")
        self.assertIn("closed", row.group(0).lower())
        for blocker in LIVE_BLOCKERS:
            self.assertIn(blocker, self.text)

    def test_unresolved_decisions_are_named_not_claimed(self):
        for needle in ("Q9", "pending", "NO-GO", "Q8",
                       "day2-go-no-go.md", "owner"):
            self.assertIn(needle, self.text)

    def test_doc_cites_reproduce_commands(self):
        for probe in ("fault_matrix.py", "local_targets.py",
                      "endpoint_walkthrough.py", "spike_faults.py"):
            self.assertIn(probe, self.text)


class TestAcceptanceRecord(unittest.TestCase):
    """The owner decision record cannot self-grant acceptance."""

    @classmethod
    def setUpClass(cls):
        cls.text = ACCEPTANCE_DOC.read_text(encoding="utf-8")

    def test_owner_decision_pending_and_no_go(self):
        self.assertIn("Luong", self.text)
        self.assertIn("NO-GO", self.text)
        self.assertRegex(self.text, r"[Pp]ending")
        # Acceptance must remain the owner's unchecked act.
        self.assertIn("- [ ] **Accepted — internal v1.0**", self.text)

    def test_a6_a7_guards_recorded(self):
        # Q9 stays proposed, not accepted; A7 non-authorizations listed.
        self.assertIn("q9-acceptance.md", self.text)
        for needle in ("autonomous_merge", "production_deploy",
                       "external", "compliance"):
            self.assertIn(needle, self.text)
        for blocker in LIVE_BLOCKERS:
            self.assertIn(blocker, self.text)


class TestReadmeIndex(unittest.TestCase):

    def test_readme_links_gate_and_acceptance(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("docs/evidence/v1.0-gate.md", text)
        self.assertIn("docs/decisions/v1.0-acceptance.md", text)


if __name__ == "__main__":
    unittest.main()
