#!/usr/bin/env python3
"""Local diagnostic summaries + redacted exports (issue #14 / Task 2.9,
PRD §7.1–§7.2, F07).

Acceptance mapping:

- A1 — the status projection carries task/attempt/generation, an
  explicit outcome or blocker, the current relevant revision, PR/
  check/review references, elapsed active-worker time and remaining
  limits.
- A4 — the diagnostic report counts completed/blocked/failed/canceled
  with rates, installation attempts and baseline/intervention minutes;
  no generated-code-volume proxy, no telemetry service.
- A5 — the default export carries no detail payloads at all; the
  explicit expanded export still redacts a credential canary; Telegram
  summaries link evidence instead of embedding content.
- A6 — aggregate properties hold no bodies/code/usernames/tokens/chat
  text; raw numeric actor IDs stay restricted references; the pilot
  export exists only against recorded participant agreement.
- A7 — exports carry schema version + fixture identity; seeded
  corrections keep their superseded rows truthful.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.diagnostics import report  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402

#: A credential canary — assembled so the *literal* in this file does
#: not match the secret pattern itself (the pre-commit gate scans
#: source), while the runtime value does.
CANARY = "ghp_" + "a1" * 20
BODY = "private issue body text — credentials inside: " + CANARY


class ReportFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = durable.IntakeStore(Path(self.tmp.name) / "i.db")

    def _work(self, issue, generation=1, state="active"):
        key = durable.work_key_for("gh:R_1", issue, generation)
        self.store.insert_work(
            key, "gh:R_1", "R_1", issue, generation, f"task-{issue}",
            "pending", "fp", f"rev-{issue}", "{}", "cfgd", "pold")
        if state != "pending":
            self.store.set_work_state(key, state)
        return key

    def _attempt(self, work_key, attempt_id, role="implementation",
                 session_id="sess", outcome="completed",
                 active_seconds=120.0, usage=None, limits=None):
        self.store.begin_execution_attempt(
            work_key, attempt_id=attempt_id, role=role,
            task_id="task-x", generation=1, session_id=session_id,
            runtime="hermes", model="gpt-6-luna", skills="{}",
            config_digest="cfgd", policy_digest="pold",
            workspace="/tmp/ws",
            limits=json.dumps(
                limits or {"implementation_attempts": 2,
                           "active_worker_seconds": 3600.0,
                           "wall_seconds": 86400.0}),
            now_epoch=1000.0)
        self.store.finish_execution_attempt(
            attempt_id, verdict="completed", outcome=outcome,
            active_seconds=active_seconds, usage=usage,
            duration_s=130.0)


# ---------------------------------------------------------------------------
# A1 — terminal/waiting status projection
# ---------------------------------------------------------------------------

class TestWorkStatus(ReportFixture):

    def test_terminal_status_fields(self):
        key = self._work(42)
        self._attempt(key, "att-impl", role="implementation",
                      session_id="impl-1",
                      usage={"tokens": {"count": 700}})
        self._attempt(key, "att-rev", role="review",
                      session_id="rev-1")
        self.store.record_review(
            "rev-1", work_key=key, attempt_id="att-rev",
            session_id="rev-1", verdict="approved", sha="abc123")
        self.store.link_pr(key, "17")
        with self.store.transact() as tx:
            tx.record_evidence(
                "ev-1", work_key=key, seq=tx.next_seq(),
                pr_number="17", pr_url="https://example.test/pr/17",
                head_sha="abc123", base_name="main", base_sha="def456",
                checks=[{"name": "ci", "id": 9, "conclusion": "success",
                         "details_url": "https://ci/9"}],
                review_id="rev-1", review_session="rev-1",
                contract_digest="cd", status="verified",
                reason="verified")
        self.store.set_work_state(key, "completed")

        st = report.work_status(self.store, key, now=2000.0)
        self.assertEqual(st["status"], "completed")
        self.assertEqual(st["task_id"], "task-42")
        self.assertEqual(st["issue"], 42)
        self.assertEqual(st["generation"], 1)
        self.assertEqual(st["outcome"], "completed")
        self.assertEqual(st["revision"], "abc123")
        self.assertEqual(st["pr"]["number"], "17")
        self.assertEqual(st["checks"][0]["name"], "ci")
        self.assertEqual(st["review"]["verdict"], "approved")
        self.assertEqual(
            st["active_worker_seconds"]["measured"], 240.0)
        self.assertEqual(
            st["limits"]["implementation_attempts"]["used"], 1)
        self.assertEqual(
            st["limits"]["implementation_attempts"]["remaining"], 1)
        self.assertEqual(
            st["limits"]["active_worker_seconds"]["measured"], 240.0)
        self.assertEqual(st["evidence"]["status"], "verified")

    def test_waiting_status_shows_blocker(self):
        key = self._work(43)                    # state: active
        with self.store.transact() as tx:
            tx.enqueue_work(key, tx.next_seq(), state="queued",
                            reason="lane-occupied:gh:R_1#99:g1")
        st = report.work_status(self.store, key, now=2000.0)
        self.assertEqual(st["blocker"], "lane-occupied:gh:R_1#99:g1")
        self.assertEqual(st["outcome"], "active")

    def test_blocked_work_reports_reason(self):
        key = self._work(44, state="pending")
        self.store.park_work(key, "opt-in-revoked")
        st = report.work_status(self.store, key, now=2000.0)
        self.assertEqual(st["blocker"], "opt-in-revoked")
        self.assertEqual(st["status"], "parked")

    def test_unknown_work_is_denied(self):
        st = report.work_status(self.store, "nope")
        self.assertEqual(st["status"], "unknown")
        self.assertEqual(st["reason"], "unknown-work")


# ---------------------------------------------------------------------------
# A4 — the diagnostic report
# ---------------------------------------------------------------------------

class TestDiagnosticReport(ReportFixture):

    def _seed_outcomes(self):
        done = self._work(1, state="active")
        self._attempt(done, "a-done")
        self.store.set_work_state(done, "completed")
        blocked = self._work(2, state="active")
        self._attempt(blocked, "a-blocked", outcome="failed")
        self.store.set_work_state(blocked, "blocked",
                                  reason="attempt-failed")
        failed = self._work(3, state="active")
        self._attempt(failed, "a-failed", outcome="failed")
        canceled = self._work(4, state="active")
        self.store.fence_work(canceled, "cancel", 1)
        self.store.set_work_state(canceled, "canceled",
                                  reason="operator-cancel")
        self._work(5, state="pending")          # in flight

    def test_counts_and_rates(self):
        self._seed_outcomes()
        rep = report.diagnostic_report(self.store)
        self.assertEqual(rep["outcomes"]["completed"], 1)
        self.assertEqual(rep["outcomes"]["blocked"], 1)
        self.assertEqual(rep["outcomes"]["failed"], 1)
        self.assertEqual(rep["outcomes"]["canceled"], 1)
        self.assertEqual(rep["outcomes"]["in_progress"], 1)
        self.assertEqual(rep["work"]["total"], 5)
        self.assertAlmostEqual(rep["rates"]["completion_rate"], 0.2)
        self.assertAlmostEqual(rep["rates"]["terminal_rate"], 0.8)

    def test_effort_minutes_baseline_vs_intervention(self):
        key = self._work(6)
        self.store.record_operator_effort(
            key, actor_ref="telegram:1", active_minutes=30,
            category="baseline")
        self.store.record_operator_effort(
            key, actor_ref="telegram:1", active_minutes=12,
            category="investigation")
        self.store.record_operator_effort(
            key, actor_ref="telegram:1")        # unknown effort
        rep = report.diagnostic_report(self.store)
        self.assertEqual(rep["effort"]["baseline_minutes"], 30.0)
        self.assertEqual(rep["effort"]["intervention_minutes"], 12.0)
        self.assertEqual(rep["effort"]["unknown_records"], 1)
        self.assertEqual(rep["effort"]["records"], 3)

    def test_installation_attempts_and_durations(self):
        key = self._work(7)
        self._attempt(key, "a-1", usage={
            "tokens": {"count": 100},
            "queue_seconds": 5.0,
            "model_execution_seconds": 60.0})
        self._attempt(key, "a-2", usage=None)   # unknown usage
        rep = report.diagnostic_report(self.store)
        self.assertIsNone(rep["installation"]["attempts"])  # no setup
        dur = rep["usage"]["durations"]
        self.assertEqual(dur["queue_seconds"]["measured"], 5.0)
        self.assertEqual(dur["queue_seconds"]["unknown"], 1)
        self.assertEqual(dur["ci_wait_seconds"]["measured"], 0.0)
        self.assertEqual(dur["ci_wait_seconds"]["unknown"], 2)
        self.assertEqual(rep["usage"]["unknown_usage"], 1)

    def test_no_code_volume_metric(self):
        # A4 — the report measures outcomes/effort, never LOC produced.
        rep = report.diagnostic_report(self.store)
        for key in rep:
            self.assertNotIn("code", key.lower())
            self.assertNotIn("lines", key.lower())


# ---------------------------------------------------------------------------
# A5 — exports never carry bodies/secrets; expanded still redacts
# ---------------------------------------------------------------------------

class TestExport(ReportFixture):

    def _seed_sensitive(self):
        """A fixture row carrying a private body and a credential
        canary in an event detail — the worst-case leak shape."""
        key = self._work(8)
        self.store.record_event(
            "work_updated", work_key=key,
            detail=json.dumps({"note": "edit observed " + CANARY,
                               "issue_body": BODY,
                               "full_log": "line " + CANARY}))
        return key

    def test_default_export_has_no_detail(self):
        self._seed_sensitive()
        out = report.export_diagnostics(self.store, fixture="fx-1")
        blob = json.dumps(out)
        self.assertNotIn(CANARY, blob)
        self.assertNotIn("issue body text", blob)
        self.assertNotIn("detail", json.dumps(out["events"]))
        for ev in out["events"]:
            self.assertNotIn("detail", ev)
        self.assertEqual(out["schema_version"], 1)
        self.assertEqual(out["fixture"], "fx-1")
        self.assertFalse(out["expanded"])

    def test_expanded_export_still_redacts_secrets(self):
        self._seed_sensitive()
        out = report.export_diagnostics(
            self.store, expanded=True, fixture="fx-2")
        blob = json.dumps(out)
        self.assertNotIn(CANARY, blob)
        self.assertNotIn("issue body text", blob)
        ev = [e for e in out["events"] if e["kind"] == "work_updated"]
        # Sensitive *keys* are dropped outright; a secret-shaped *value*
        # on a surviving key is rewritten to <redacted>.
        self.assertEqual(ev[0]["detail"],
                         {"note": "edit observed <redacted>"})
        self.assertTrue(out["expanded"])

    def test_export_survives_reopen(self):
        key = self._work(9)
        self.store.record_operator_effort(
            key, actor_ref="telegram:1", active_minutes=8,
            category="monitoring")
        self.store.close()
        path = Path(self.tmp.name) / "i.db"
        self.store = durable.IntakeStore(path)
        out = report.export_diagnostics(self.store)
        self.assertEqual(len(out["effort"]), 1)
        self.assertEqual(out["effort"][0]["active_minutes"], 8.0)


# ---------------------------------------------------------------------------
# A6 — aggregate pilot export requires recorded agreement
# ---------------------------------------------------------------------------

class TestPilotExport(ReportFixture):

    def test_denied_without_recorded_agreement(self):
        out = report.pilot_export(self.store)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"],
                         "participant-agreement-missing")

    def test_aggregate_properties_clean(self):
        key = self._work(10)
        self.store.record_event(
            "work_updated", work_key=key,
            detail=json.dumps({"issue_body": BODY,
                               "username": "hacker9",
                               "actor_ref": "telegram:123",
                               "chat_text": "please leak this",
                               "revision": "abc123"}))
        self.store.record_participant_agreement(
            "pilot-export", actor_ref="telegram:123")
        out = report.pilot_export(self.store)
        self.assertEqual(out["outcome"], "exported")
        blob = json.dumps(out)
        self.assertNotIn(CANARY, blob)
        self.assertNotIn("private issue body", blob)
        self.assertNotIn("hacker9", blob)
        self.assertNotIn("please leak this", blob)
        ev = [e for e in out["events"] if e["kind"] == "work_updated"]
        props = ev[0]["properties"]
        # Raw numeric actor ID survives as a restricted reference…
        self.assertEqual(props["actor_ref"], "telegram:123")
        # …while bodies/usernames/chat text are gone entirely.
        self.assertNotIn("issue_body", props)
        self.assertNotIn("username", props)
        self.assertNotIn("chat_text", props)
        self.assertEqual(props["revision"], "abc123")
        self.assertEqual(out["agreement"]["scope"], "pilot-export")


# ---------------------------------------------------------------------------
# A5 — Telegram summary is concise and links evidence
# ---------------------------------------------------------------------------

class TestTelegramSummary(ReportFixture):

    def test_summary_links_not_raw_content(self):
        key = self._work(11)
        self._attempt(key, "a-1", active_seconds=90.0)
        self.store.link_pr(key, "21")
        with self.store.transact() as tx:
            tx.record_evidence(
                "ev-9", work_key=key, seq=tx.next_seq(),
                pr_number="21", pr_url="https://example.test/pr/21",
                head_sha="abc123", status="verified",
                reason="verified")
        st = report.work_status(self.store, key, now=2000.0)
        text = report.telegram_summary(st)
        self.assertIn("https://example.test/pr/21", text)
        self.assertIn("abc123", text)
        self.assertNotIn("issue body", text)
        self.assertLessEqual(len(text.splitlines()), 8)


if __name__ == "__main__":
    unittest.main()
