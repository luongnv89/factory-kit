#!/usr/bin/env python3
"""Backup/restore rehearsal on the selected persistence mechanism —
issue #23 / Task 3.9 (A3, A6).

The supported persistence pair is file-level: the kit-owned SQLite
intake store (``in.db`` — rollback-journal, ``synchronous=FULL``, so a
closed file is self-contained) plus the registration JSON. The
rehearsal performs a real backup (close → byte copy into a backup
directory) and a real restore (fresh directory, reopen the copies,
rebuild every service, run the recovery pass), then proves:

- task/attempt/fence identities survive byte-for-byte;
- delivery, notification, approval and publication-intent records
  survive, and redelivery deduplicates rather than minting work;
- a pending remote intent is rechecked against the remote before
  dispatch — the existing PR is linked, never duplicated;
- consumed and expired approval authority is never revived;
- corrupt and incomplete restores report concrete blockers (the store
  refuses to open, or every row parks) and nothing dispatches.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/recovery/test_backup_restore.py
"""

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.approval import ApprovalService  # noqa: E402
from factory_kit.config import registration, schema  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.execution import (  # noqa: E402
    ExecutionLane, ScriptedWorker)
from factory_kit.intake import service  # noqa: E402
from factory_kit.publication import (  # noqa: E402
    OPERATION_PR_PUBLISH, PublicationBroker, ScriptedRemote)
from factory_kit.recovery import RecoveryService  # noqa: E402

MANIFEST = ROOT / ".factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"
ACTOR = "luongnv89"
USER_REF = "telegram:123456789"
CHAT_REF = "telegram:-1001234567890"

READINGS = {
    "tools": {"git": "/usr/bin/git", "gh": "/x/gh", "hermes": "/x/hermes"},
    "models": {"openai-codex": {"authenticated": True,
                                "available": ["gpt-6-luna"]}},
    "skills": {"issue-resolver": "0.19.0", "issue-pr-review": "0.19.0"},
}

SHA = "a1" * 20


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


class BackupRestoreFixture(unittest.TestCase):
    """Live deployment at ``primary/``; the rehearsal copies the two
    durable artifacts into ``backup/`` and restores them into
    ``restored/`` — exactly the file-level rehearsal the recipe
    publishes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.primary = Path(self.tmp.name) / "primary"
        self.backup = Path(self.tmp.name) / "backup"
        self.restored = Path(self.tmp.name) / "restored"
        for d in (self.primary, self.backup, self.restored):
            d.mkdir()
        self.eff = effective()
        self.repo_id = self.eff["identity"]["repo_id"]
        self.full_name = (f"{self.eff['identity']['owner']}/"
                          f"{self.eff['identity']['name']}")
        self.clock = [time.time()]
        self.alerts = []
        # The remote survives the controller host — like GitHub.
        self.remote = ScriptedRemote(self.repo_id, self.full_name)
        self._open(self.primary)

    def _now(self):
        return self.clock[0]

    def _open(self, directory):
        """(Re)build every service on the durable pair in
        ``directory`` — used for the live deployment, then again on
        the restored copies so the reopened files are the only
        authority."""
        self.store_path = directory / "in.db"
        self.reg_path = directory / "registration.json"
        self.registrations = registration.RegistrationStore(
            self.reg_path)
        self.store = durable.IntakeStore(self.store_path)
        self.intake = service.IntakeService(
            self.store, self.registrations, {self.repo_id: self.eff},
            signing_secret="s", opt_in_label="factory-kit")
        self.lane = ExecutionLane(
            self.store, self.registrations, {self.repo_id: self.eff},
            worker=ScriptedWorker(),
            workspace_root=directory / "ws",
            readings=READINGS, now=self._now)
        self.broker = PublicationBroker(
            self.store, self.remote, self.registrations, self.eff,
            clock=self._now, alert_sink=self.alerts)
        self.approval = ApprovalService(
            self.store, self.registrations, {self.repo_id: self.eff},
            now=self._now)
        self.recovery = RecoveryService(
            self.store, self.registrations, {self.repo_id: self.eff},
            intake=self.intake, lane=self.lane, broker=self.broker,
            remote=self.remote, now=self._now,
            alert_sink=self.alerts)

    # -- the rehearsal verbs ------------------------------------------------

    def _register(self):
        self.registrations.register(self.eff, readiness=READY,
                                    supported_versions=VERSIONS)

    def _backup(self):
        """The documented backup: quiesce the writer, then byte-copy
        the durable pair. ``synchronous=FULL`` + rollback journal means
        a cleanly closed ``in.db`` is the complete committed state."""
        self.store.close()
        shutil.copy2(self.store_path, self.backup / "in.db")
        shutil.copy2(self.reg_path, self.backup / "registration.json")

    def _restore(self, *, db_source=None, reg_source=None):
        """The documented restore: copy the artifacts into a fresh
        deployment directory and reopen — no state outside the pair."""
        db_source = db_source or self.backup / "in.db"
        shutil.copy2(db_source, self.restored / "in.db")
        if reg_source is not False:
            shutil.copy2(reg_source or self.backup / "registration.json",
                         self.restored / "registration.json")
        self._open(self.restored)

    # -- durable surfaces the rehearsal exercises ---------------------------

    def _accept(self, issue=42, delivery=None):
        env = {"delivery_id": delivery or f"d-{issue}",
               "channel": "reconciliation", "repo_id": self.repo_id,
               "repository": self.full_name, "issue": issue,
               "action": "reconcile", "sender": ACTOR,
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.intake.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        self.registrations.record_work(self.repo_id, work_key)
        return work_key

    def _live_attempt(self, work_key):
        work = self.store.get_work(work_key)
        self.store.activate_work(work_key)
        self.store.acquire_lane(work_key, work["task_id"])
        attempt_id = f"{work['task_id']}-a01"
        self.store.begin_execution_attempt(
            work_key, attempt_id=attempt_id, role="implementation",
            task_id=work["task_id"], generation=work["generation"],
            session_id="s1", runtime="hermes-kanban", model="m",
            skills="{}", config_digest=work["config_digest"],
            policy_digest=work["policy_digest"], workspace="/w",
            limits="{}", now_epoch=self._now())
        return attempt_id

    def _insert_request(self, request_id, work_key, *, state,
                        expires_epoch):
        work = self.store.get_work(work_key)
        with self.store.transact() as tx:
            tx.insert_approval_request(
                request_id, work_key=work_key,
                seq=self.store.next_seq(),
                task_id=work["task_id"],
                generation=work["generation"],
                authority_key=work["authority_key"],
                repo_id=self.repo_id, action="merge",
                target="pr:77", pr_number="77",
                pr_url="https://x/pr/77", head_sha=SHA,
                base_name="main", base_sha=SHA,
                merge_method="squash", state=state,
                actor_ref=USER_REF,
                issued_at="2026-10-05T00:00:00Z",
                expires_epoch=expires_epoch)


# ---------------------------------------------------------------------------
# A3 — restore preserves every identity and rechecks the remote
# ---------------------------------------------------------------------------

class TestA3RestoreRehearsal(BackupRestoreFixture):

    def test_restore_preserves_all_identities_and_records(self):
        self._register()
        work_key = self._accept(42)
        work = self.store.get_work(work_key)
        task_id = work["task_id"]
        attempt_id = self._live_attempt(work_key)

        # Pre-backup durable records: a fence sweep input (the live
        # attempt), a pending notification, a consumed approval and a
        # pending publication intent whose remote PR already exists.
        self.clock[0] += 61  # heartbeat window lapsed → the restore's
                             # sweep must fence this attempt
        self.store.enqueue_notification(
            "ntf-1", kind="approval-requested", identity=work_key,
            severity="info", destination_ref=CHAT_REF,
            body="approval requested", work_key=work_key,
            seq=self.store.next_seq())
        self._insert_request("apr-consumed", work_key,
                             state="consumed",
                             expires_epoch=self._now() + 600)
        self.store.insert_intent(
            "pub-1", work_key=work_key, task_id=task_id, generation=1,
            authority_key=work["authority_key"], repo_id=self.repo_id,
            operation=OPERATION_PR_PUBLISH, expected_revision=SHA,
            target="factory-kit/impl-42", actor_ref=ACTOR,
            state="recorded")
        self.remote.open_pr("factory-kit/impl-42", "main", "t",
                            {"work_key": work_key,
                             "authority_key": work["authority_key"],
                             "generation": 1})
        publish_calls = [c["op"] for c in self.remote.calls
                         if c["op"] == "pr-publish"]

        self._backup()
        self._restore()
        report = self.recovery.recover(source="restore")

        # Task identity survived — same work_key, same reserved/bound
        # task_id, same authority key.
        restored_work = self.store.get_work(work_key)
        self.assertIsNotNone(restored_work)
        self.assertEqual(restored_work["task_id"], task_id)
        self.assertEqual(restored_work["authority_key"],
                         work["authority_key"])

        # Attempt identity survived: the sweep fenced the restored
        # attempt row, did not mint a new one.
        self.assertEqual(
            [f["work_key"] for f in report["sweep"]["fenced"]],
            [work_key])
        fence = self.store.fence_state(work_key)
        self.assertTrue(fence["fenced"])
        attempts = [a for a in self.store.attempt_rows()
                    if a["work_key"] == work_key]
        self.assertEqual([a["attempt_id"] for a in attempts],
                         [attempt_id])

        # Delivery record survived — redelivery deduplicates rather
        # than minting a second logical task.
        ack = self.intake.deliver(
            {"delivery_id": "d-42", "channel": "reconciliation",
             "repo_id": self.repo_id, "repository": self.full_name,
             "issue": 42, "action": "reconcile", "sender": ACTOR,
             "labels": ["factory-kit"], "issue_title": "t",
             "issue_body": BODY, "issue_revision": "2026-10-05"})
        self.assertEqual(ack["outcome"], "deduplicated")
        self.assertEqual(self.store.counts()["work"], 1)
        self.assertEqual(self.store.counts()["deliveries"], 1)

        # The pending notification row survived byte-for-byte.
        ntf = self.store.get_notification("ntf-1")
        self.assertEqual(ntf["state"], "pending")
        self.assertEqual(ntf["body"], "approval requested")
        self.assertEqual(ntf["destination_ref"], CHAT_REF)

        # The consumed approval row survived — see the dedicated
        # authority test for the no-revival proof.
        req = self.store.get_approval_request("apr-consumed")
        self.assertEqual(req["state"], "consumed")

        # The remote intent was rechecked, not re-issued: the existing
        # PR was discovered by identity and linked; zero new publishes.
        self.assertEqual(len(self.remote.pulls), 1)
        self.assertEqual(
            [c["op"] for c in self.remote.calls].count("pr-publish"),
            len(publish_calls))
        intent = self.store.intent_rows(work_key)[0]
        self.assertEqual(intent["state"], "linked")
        self.assertEqual(intent["remote_ref"],
                         str(next(iter(self.remote.pulls))))
        self.assertTrue(report["within_deadline"])

    def test_pending_intent_rechecks_remote_before_dispatch(self):
        """Restored state alone never proves the remote effect — the
        recovery pass reads the remote back and links exactly one
        matching identity; dispatch decisions follow the read-back."""
        self._register()
        work_key = self._accept(42)
        work = self.store.get_work(work_key)
        self.store.insert_intent(
            "pub-2", work_key=work_key,
            task_id=work["task_id"], generation=1,
            authority_key=work["authority_key"], repo_id=self.repo_id,
            operation=OPERATION_PR_PUBLISH, expected_revision=SHA,
            target="factory-kit/impl-42", actor_ref=ACTOR,
            state="recorded")
        self.remote.open_pr("factory-kit/impl-42", "main", "t",
                            {"work_key": work_key,
                             "authority_key": work["authority_key"],
                             "generation": 1})
        self._backup()
        self._restore()
        report = self.recovery.recover(source="restore")
        self.assertEqual(len(self.remote.pulls), 1)
        self.assertEqual(
            self.store.get_work(work_key)["linked_pr"],
            str(next(iter(self.remote.pulls))))
        # The reconcile outcome reached the report — the operator can
        # audit that a remote read-back happened.
        self.assertTrue(report["intents"])

    def test_ambiguous_remote_on_restore_parks_without_duplicate(self):
        self._register()
        work_key = self._accept(42)
        work = self.store.get_work(work_key)
        self.store.insert_intent(
            "pub-3", work_key=work_key,
            task_id=work["task_id"], generation=1,
            authority_key=work["authority_key"], repo_id=self.repo_id,
            operation=OPERATION_PR_PUBLISH, expected_revision=SHA,
            target="factory-kit/impl-42", actor_ref=ACTOR,
            state="recorded")
        # Two remote PRs match the restored identity — the honest
        # outcome is a park + alert, never a guess or a third PR.
        for _ in range(2):
            self.remote.open_pr(
                "factory-kit/impl-42", "main", "t",
                {"work_key": work_key,
                 "authority_key": work["authority_key"],
                 "generation": 1})
        self._backup()
        self._restore()
        self.recovery.recover(source="restore")
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertEqual(work["parked_reason"], "duplicate-remote-pr")
        self.assertEqual(len(self.remote.pulls), 2)
        self.assertTrue(
            self.store.alert_rows("duplicate-remote-pr"))

    # -- A3: consumed/expired authority never revives -------------------------

    def test_consumed_authority_denies_after_restore(self):
        self._register()
        work_key = self._accept(42)
        self._insert_request("apr-done", work_key, state="consumed",
                             expires_epoch=self._now() + 600)
        self._backup()
        self._restore()
        # The consumed row is durable — a replayed consume is denied
        # with the auditable 'replayed' reason, never re-granted.
        result = self.approval.consume("apr-done")
        self.assertEqual(result["outcome"], "denied")
        self.assertEqual(result["reason"], "replayed")
        self.assertEqual(
            self.store.get_approval_request("apr-done")["state"],
            "consumed")

    def test_expired_authority_denies_after_restore(self):
        self._register()
        work_key = self._accept(42)
        # Approved before the backup, expired while the restore ran —
        # consumption must re-evaluate expiry, not trust the row.
        self._insert_request("apr-old", work_key, state="approved",
                             expires_epoch=self._now() + 30)
        self._backup()
        self._restore()
        self.clock[0] += 60
        result = self.approval.consume("apr-old")
        self.assertEqual(result["outcome"], "denied")
        self.assertEqual(result["reason"], "expired")
        self.assertEqual(
            self.store.get_approval_request("apr-old")["state"],
            "expired")


# ---------------------------------------------------------------------------
# A6 — corrupt and incomplete restores report blockers, never dispatch
# ---------------------------------------------------------------------------

class TestA6RestoreBlockers(BackupRestoreFixture):

    def test_corrupt_db_reports_blocker(self):
        self._register()
        self._accept(42)
        self._backup()
        # The backup artifact was damaged in transit/at rest: the
        # restore refuses at open with a concrete blocker naming the
        # file — no partial state is ever acted on.
        damaged = self.backup / "in.db"
        damaged.write_bytes(damaged.read_bytes()[:200])
        with self.assertRaises(durable.IntakeStoreError) as ctx:
            self._restore()
        self.assertIn("cannot open intake store", str(ctx.exception))

    def test_non_db_bytes_report_blocker(self):
        self._register()
        self._accept(42)
        self._backup()
        garbage = Path(self.tmp.name) / "garbage.db"
        garbage.write_bytes(b"not a sqlite database" * 64)
        with self.assertRaises(durable.IntakeStoreError):
            self._restore(db_source=garbage)

    def test_incomplete_restore_parks_everything(self):
        self._register()
        work_key = self._accept(42)
        self._backup()
        # The intake store restored but the registration file did not —
        # every durable row parks explicitly; none is dispatched under
        # a missing authority boundary.
        self._restore(reg_source=False)
        self.assertIsNone(self.registrations.get(self.repo_id))
        report = self.recovery.recover(source="restore")
        self.assertEqual([p["work_key"] for p in report["parked"]],
                         [work_key])
        work = self.store.get_work(work_key)
        self.assertEqual(work["state"], "parked")
        self.assertEqual(work["parked_reason"],
                         "generation-superseded")
        self.assertTrue(self.store.alert_rows("recovery-parked"))
        # Nothing was queued or dispatched — the parked row is inert.
        self.assertEqual(self.lane.eligible_work(), [])
        self.assertEqual(len(self.remote.pulls), 0)


if __name__ == "__main__":
    unittest.main()
