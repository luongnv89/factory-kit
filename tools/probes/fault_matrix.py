#!/usr/bin/env python3
"""factory-kit §8.2 fault-matrix probe — the executable evidence runner
for issues #20 (task 3.6) and #21 (task 3.7).

Runs all 19 §8.2 fault rows three times each — 57 independent
repetitions, every repetition on a fresh ``World`` (fresh durable
store, fresh scripted GitHub/preview/worker/issue-source ports, fake
clock, no network) — and archives:

- per-repetition named assertions with their measured values,
- the bound identities (work/attempt/intent/request/PR/preview/
  deployment/notification ids, merge SHAs),
- the release-gate audits: zero duplicate PRs, zero unauthorized
  merges, zero accepted fenced results, zero lost acknowledged tasks,
  and the verified-outcome audit (independent review + required checks
  + head matching authoritative remote state on every ``verified``
  row),
- the exact environment: python/platform, harness version, manifest +
  policy digests, supported versions, fixture identity, and the
  reproduce command.

A row only counts verified when ALL THREE repetitions pass every
assertion and audit. The gate fails closed: a crashed scenario, a
failed assertion, or an audit violation in ANY repetition fails the
row and the run.

The suite exercises the merged services through the packaged scripted
ports — deterministic local fixture evidence plus scripted-remote
read-back where the row calls for authoritative reconciliation. It is
not a proof against every possible security failure; it validates the
finite §8.2 scenario set the MVP commits to.

Exit codes (shared gi-* vocabulary):

    0  fault-matrix-passed — all rows × all repetitions clean
    1  fault-matrix-failed — named row failures (a verdict)
    2  usage error          — malformed invocation
    4  cannot complete      — the matrix could not be built or read

Usage:

    python3 tools/probes/fault_matrix.py [--row FAULTNN]
                                        [--reps N]
                                        [--write report.json]
                                        [--fixture report.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.faults.harness import (  # noqa: E402
    REPS, reevaluate, run_matrix)
from tests.faults.rows_endpoint import ROWS as ENDPOINT_ROWS  # noqa: E402
from tests.faults.rows_lifecycle import ROWS as LIFECYCLE_ROWS  # noqa: E402

ALL_ROWS = dict(LIFECYCLE_ROWS)
ALL_ROWS.update(ENDPOINT_ROWS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--row", choices=sorted(ALL_ROWS),
                    help="run one §8.2 row instead of the full matrix")
    ap.add_argument("--reps", type=int, default=REPS,
                    help=f"repetitions per row (default {REPS})")
    ap.add_argument("--write", help="write the JSON report to a file")
    ap.add_argument("--fixture",
                    help="re-evaluate a recorded JSON report")
    args = ap.parse_args(argv)

    if args.reps < 1:
        print("--reps must be >= 1", file=sys.stderr)
        return 2

    if args.fixture:
        try:
            record = json.loads(Path(args.fixture).read_text())
        except Exception as exc:
            print(f"cannot read fixture: {exc}", file=sys.stderr)
            return 4
        out = reevaluate(record)
        print(json.dumps(out, indent=2))
        return 0 if out["verdict"] == "fault-matrix-passed" else 1

    rows = ({args.row: ALL_ROWS[args.row]} if args.row else ALL_ROWS)
    try:
        report = run_matrix(rows, reps=args.reps)
    except Exception as exc:  # the matrix itself could not run
        print(f"cannot complete: {exc}", file=sys.stderr)
        return 4

    text = json.dumps(report, indent=2)
    if args.write:
        Path(args.write).parent.mkdir(parents=True, exist_ok=True)
        Path(args.write).write_text(text + "\n")
        print(f"wrote {args.write}")
    else:
        print(text)
    return 0 if report["verdict"] == "fault-matrix-passed" else 1


if __name__ == "__main__":
    sys.exit(main())
