#!/usr/bin/env python3
"""§8.2 fault matrix — 19 rows × 3 independent repetitions
(issues #20 + #21, tasks 3.6/3.7).

FAULT01–FAULT12 cover intake/lifecycle, recovery, trust, authorization,
budget, injection/privacy and setup faults (issue #20). FAULT13–FAULT19
cover preview, approval and merge faults (issue #21). Every row runs
``REPS`` times on an independent ``World`` (fresh durable store, fresh
scripted ports — never shared state), records its named assertions plus
bound identities, then the release-gate audits run on the world's own
durable rows: zero duplicate PRs, zero unauthorized merges, zero
accepted fenced results, zero lost acknowledged tasks, and every
``verified`` evidence row audited for independent review + required
checks + a head that still matches authoritative remote state.

The suite is deterministic (fake clock, scripted ports, no network, no
sleeps). It validates the finite §8.2 scenario set — it is not a proof
against every possible security failure.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.faults.harness import REPS, run_row  # noqa: E402
from tests.faults.rows_endpoint import ROWS as ENDPOINT_ROWS  # noqa: E402
from tests.faults.rows_lifecycle import ROWS as LIFECYCLE_ROWS  # noqa: E402

ALL_ROWS = dict(LIFECYCLE_ROWS)
ALL_ROWS.update(ENDPOINT_ROWS)


def _make(row_id, scenario):
    def test(self):
        report = run_row(row_id, scenario, reps=REPS)
        failures = []
        for rep in report["reps"]:
            if rep["pass"]:
                continue
            failures.append(
                {"rep": rep["rep"],
                 "failed_assertions": rep["failed_assertions"],
                 "violations": rep["audit"]["violations"],
                 "error": rep["error"]})
        self.assertEqual(
            report["status"], "pass",
            f"{row_id}: {report['passed_reps']}/"
            f"{report['required_reps']} reps passed; failures: "
            f"{failures}")
    test.__name__ = f"test_{row_id.lower()}_x{REPS}"
    test.__doc__ = (f"§8.2 {row_id} × {REPS} independent repetitions — "
                    f"all assertions pass and all release-gate audits "
                    f"hold.")
    return test


class TestFaultMatrix(unittest.TestCase):
    """One test method per §8.2 row — each is REPS fresh worlds."""


for _rid, _fn in ALL_ROWS.items():
    _test = _make(_rid, _fn)
    setattr(TestFaultMatrix, _test.__name__, _test)


if __name__ == "__main__":
    unittest.main(verbosity=2)
