#!/usr/bin/env python3
"""Privacy retention cleanup + guarded export (issue #18 / Task 3.4).

Acceptance mapping:

- A5 — clock-controlled cleanup honors the 7-day worker-log and 30-day
  detailed-audit retention defaults: liveness/log records for terminal
  work expire at the worker-log cutoff, detail columns are pruned at
  the audit cutoff, active task/control records are never touched, and
  enough identity (``deliveries``, ``work``, minimal rows) survives to
  refuse webhook replay. Tombstones persist until explicit
  registration removal; the export/delete choice explains the
  distinction.
- A4 — default exports carry no detail columns; expanded exports are an
  explicit operator choice and still run every value through the
  credential scanner, so known secrets never leave the boundary.
- A6 — an old ``delivery_id`` replayed after detail pruning is still
  deduplicated — identity is retained, not the detail.
"""

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.durable.store import IntakeStore, work_key_for  # noqa: E402
from factory_kit.privacy import export, retention  # noqa: E402

CANARY = "ghp_" + "b2" * 20
DAY = 86400.0
NOW = 1_800_000_000.0  # fixed clock — cleanup is clock-controlled


def _iso(epoch):
    return (datetime.fromtimestamp(epoch, timezone.utc)
            .isoformat(timespec="milliseconds"))


class PrivacyFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = IntakeStore(Path(self.tmp.name) / "i.db")

    def _work(self, issue, state, updated_epoch, gen=1):
        wk = work_key_for("gh:R_1", issue, gen)
        self.store.insert_work(
            wk, "gh:R_1", "R_1", issue, gen, f"task-{issue}",
            "pending", "fp", f"rev-{issue}", "{}", "cfgd", "pold")
        self.store.set_work_state(wk, state)
        self.store._q("UPDATE work SET updated_at=? WHERE work_key=?",
                      (_iso(updated_epoch), wk))
        return wk

    def _liveness(self, attempt_id, work_key, beat_epoch):
        self.store._q("INSERT INTO attempt_liveness VALUES(?,?,?,?,?)",
                      (attempt_id, work_key, _iso(beat_epoch),
                       beat_epoch, 1))

    def _usage(self, attempt_id, work_key, recorded_epoch, usage="{}"):
        self.store._q(
            "INSERT INTO attempt_usage VALUES(?,?,?,?,?,?)",
            (attempt_id, work_key, "implementation", 5.0, usage,
             _iso(recorded_epoch)))

    def _event(self, work_key, recorded_epoch, detail):
        self.store.record_event("work_parked", work_key=work_key,
                                detail=detail)
        seq = self.store.event_rows()[-1]["seq"]
        self.store._q("UPDATE events SET ts=? WHERE seq=?",
                      (_iso(recorded_epoch), seq))
        return seq


# ---------------------------------------------------------------------------
# A5 — retention boundaries


class TestRetentionBoundaries(PrivacyFixture):

    def test_worker_log_cutoff_at_seven_days(self):
        """A terminal work's liveness rows are pruned strictly past the
        7-day worker-log cutoff; a beat at the boundary survives."""
        old = self._work(1, "completed", NOW - 40 * DAY)
        edge = self._work(2, "completed", NOW - 40 * DAY)
        self._liveness("a-old", old, NOW - 8 * DAY)
        self._liveness("a-edge", edge, NOW - 7 * DAY)
        out = retention.cleanup(self.store, now_epoch=NOW)
        self.assertEqual(out["deleted"].get("attempt_liveness"), 1)
        beats = {r["attempt_id"] for r in
                 self.store._rows("attempt_liveness")}
        self.assertEqual(beats, {"a-edge"})

    def test_audit_detail_cutoff_at_thirty_days(self):
        """Detail for terminal work is nulled strictly past the 30-day
        audit cutoff (measured from the work's terminal transition);
        work whose transition sits at the boundary survives."""
        old = self._work(3, "completed", NOW - 31 * DAY)
        edge = self._work(4, "canceled", NOW - 30 * DAY)
        self._event(old, NOW - 5 * DAY, '{"k":"old"}')
        self._event(edge, NOW - 5 * DAY, '{"k":"edge"}')
        retention.cleanup(self.store, now_epoch=NOW)
        rows = {r["work_key"]: r for r in self.store.event_rows()
                if r["work_key"]}
        self.assertIsNone(rows[old]["detail"])
        self.assertEqual(rows[edge]["detail"], '{"k":"edge"}')
        # Identity survives — the event row itself is kept.
        self.assertEqual(rows[old]["kind"], "work_parked")

    def test_active_work_is_never_pruned(self):
        """Active task/control records — and their detail — survive any
        age: cleanup only prunes terminal work."""
        wk = self._work(5, "active", NOW - 90 * DAY)
        self._event(wk, NOW - 60 * DAY, '{"k":"keep"}')
        self._liveness("a-live", wk, NOW - 60 * DAY)
        self._usage("a-live", wk, NOW - 60 * DAY, '{"tok":9}')
        retention.cleanup(self.store, now_epoch=NOW)
        ev = [r for r in self.store.event_rows() if r["work_key"] == wk]
        self.assertEqual(ev[0]["detail"], '{"k":"keep"}')
        self.assertEqual(len(self.store._rows("attempt_liveness")), 1)
        usage = self.store._rows("attempt_usage")
        self.assertEqual(usage[0]["usage"], '{"tok":9}')

    def test_detail_prune_keeps_identity_rows(self):
        """A5's replay clause: detail pruning keeps the identity row —
        the work row, delivery row and event row all survive."""
        wk = self._work(6, "completed", NOW - 40 * DAY)
        self.store.record_delivery("del-6", "webhook", wk,
                                   "accepted", None, 1)
        self._event(wk, NOW - 40 * DAY, '{"secret":"x"}')
        self._usage("a-6", wk, NOW - 40 * DAY, '{"secret":"x"}')
        out = retention.cleanup(self.store, now_epoch=NOW)
        self.assertGreater(out["retained_identities"], 0)
        self.assertIsNotNone(self.store.find_delivery("del-6"))
        self.assertIsNotNone(self.store.get_work(wk))
        # Usage row retained but its detail column pruned.
        usage = self.store._rows("attempt_usage")
        self.assertEqual(len(usage), 1)
        self.assertIsNone(usage[0]["usage"])

    def test_replay_after_detail_pruning_is_deduplicated(self):
        """A webhook replayed after detail pruning still hits the
        retained ``deliveries`` identity and dedups — never
        re-executes."""
        wk = self._work(7, "completed", NOW - 40 * DAY)
        self.store.record_delivery("del-7", "webhook", wk,
                                   "accepted", None, 1)
        self._event(wk, NOW - 40 * DAY, "detail")
        retention.cleanup(self.store, now_epoch=NOW)
        # The replay path is the same lookup intake performs.
        replayed = self.store.find_delivery("del-7")
        self.assertIsNotNone(replayed)
        self.assertEqual(replayed["work_key"], wk)
        self.assertEqual(replayed["outcome"], "accepted")

    def test_recent_terminal_work_untouched(self):
        """Terminal work inside both retention windows keeps
        everything."""
        wk = self._work(8, "completed", NOW - 3 * DAY)
        self._liveness("a-8", wk, NOW - 3 * DAY)
        self._event(wk, NOW - 3 * DAY, '{"k":"recent"}')
        out = retention.cleanup(self.store, now_epoch=NOW)
        self.assertEqual(out["deleted"].get("attempt_liveness"), 0)
        ev = [r for r in self.store.event_rows() if r["work_key"] == wk]
        self.assertEqual(ev[0]["detail"], '{"k":"recent"}')

    def test_cleanup_records_its_result(self):
        out = retention.cleanup(self.store, now_epoch=NOW)
        for key in ("deleted", "pruned", "retained_identities",
                    "cutoffs", "policy", "explanation"):
            self.assertIn(key, out)
        self.assertIn("worker_log_before_epoch", out["cutoffs"])
        self.assertIn("audit_before_epoch", out["cutoffs"])


# ---------------------------------------------------------------------------
# A4/A5 — export boundary + the export/delete distinction


class TestExportBoundary(PrivacyFixture):

    def _seed_detail(self):
        wk = self._work(9, "completed", NOW - 1 * DAY)
        self.store.record_event("work_parked", work_key=wk,
                                detail=f'{{"note":"{CANARY}"}}')
        self.store.record_delivery("del-9", "webhook", wk,
                                   "accepted", None, 2)
        self.store.emit_alert("parked", wk, "high",
                              f"token {CANARY}")
        return wk

    def test_default_export_drops_detail_columns(self):
        wk = self._seed_detail()
        out = export.history_export(self.store, "gh:R_1")
        for table, rows in out["tables"].items():
            for row in rows:
                self.assertNotIn("detail", row, table)
                self.assertNotIn("usage", row, table)
                self.assertNotIn("body", row, table)
        # Identity is present — replay-resistant rows are exported.
        self.assertEqual(
            out["tables"]["deliveries"][0]["delivery_id"], "del-9")
        self.assertEqual(out["secret_scan"], "clean")

    def test_expanded_export_redacts_credentials(self):
        self._seed_detail()
        out = export.history_export(self.store, "gh:R_1",
                                    expanded=True)
        blob = repr(out["tables"])
        self.assertNotIn(CANARY, blob)
        # Detail fields are present but redacted.
        ev = out["tables"]["events"][0]
        self.assertIn("detail", ev)
        self.assertEqual(ev["detail"], {"note": "<redacted>"})
        self.assertEqual(out["secret_scan"], "clean")

    def test_sensitive_keys_never_exported(self):
        wk = self._work(10, "completed", NOW - DAY)
        # A row carrying sensitive-key fields is stripped at the
        # boundary regardless of the expanded flag.
        self.store._q(
            "INSERT INTO events(kind,ts,detail,work_key) "
            "VALUES(?,?,?,?)",
            ("x", _iso(NOW), '{"password":"pw","issue":5}', wk))
        out = export.history_export(self.store, "gh:R_1",
                                    expanded=True)
        found = out["tables"]["events"]
        row = [r for r in found if r["kind"] == "x"][0]
        self.assertNotIn("password", row["detail"])
        self.assertIn("issue", row["detail"])

    def test_secret_leaks_flags_a_planted_canary(self):
        self._seed_detail()
        good = export.history_export(self.store, "gh:R_1",
                                     expanded=True)
        self.assertEqual(export.secret_leaks(good), [])
        bad = {"tables": {"events": [{"detail": CANARY}]}}
        leaks = export.secret_leaks(bad)
        self.assertTrue(leaks)


if __name__ == "__main__":
    unittest.main()
