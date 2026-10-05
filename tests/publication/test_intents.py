#!/usr/bin/env python3
"""Serialized publication intents + authorization rechecks — issue #10
(Task 2.5, PRD §5.2, §6.4).

Each test class maps to an acceptance criterion:

- A1 — the durable intent (task/generation, repository, expected
  revision, operation, target) commits before the remote effect, and
  read-back binds the remote result to the same recorded identity.
- A2 — workers/descendants carry no remote authority; every mutation
  attempt crosses the scoped broker and is denied for wrong
  repository/action, stale generation, changed config, expired claim
  or revoked actor.
- A3 — concurrent attempts converge on one intent (one remote
  operation); a crash after remote PR creation is reconciled by
  recorded identity on recovery, never republished blindly.
- A4 — a fence committed before effect authorization denies the
  effect; an effect authorized before the fence is reconciled and
  accurately linked, not silently reversed.
- A5 — duplicate remote PRs, uncertain repository identity and
  ambiguous API outcomes park the work; nothing closes, deletes or
  overwrites user work.
- A6 — unauthorized mutation attempts are retained as durable
  evidence, quarantine the affected work and emit a deduplicated
  high-severity local/operator alert; >1 matching remote PR emits the
  identity alert.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.config import registration, schema  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.intake import service  # noqa: E402
from factory_kit.publication import (  # noqa: E402
    OPERATION_PR_PUBLISH, PublicationBroker, ScriptedRemote)

MANIFEST = ROOT / ".factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"

ACTOR = "luongnv89"           # allowlisted github_actor in the manifest


def effective(**overrides):
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


class PublicationFixture(unittest.TestCase):
    """Registered repo + accepted work row + scripted remote + broker."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.eff = effective()
        self.repo_id = self.eff["identity"]["repo_id"]
        self.full_name = (f"{self.eff['identity']['owner']}/"
                          f"{self.eff['identity']['name']}")
        self.clock = [time.time()]
        self.registrations = registration.RegistrationStore(
            Path(self.tmp.name) / "registration.json")
        self.registrations.register(self.eff, readiness=READY,
                                    supported_versions=VERSIONS)
        self.store = durable.IntakeStore(Path(self.tmp.name) / "in.db")
        self.service = service.IntakeService(
            self.store, self.registrations, {self.repo_id: self.eff},
            signing_secret="s", opt_in_label="factory-kit")
        self.remote = ScriptedRemote(self.repo_id, self.full_name)
        self.alert_sink = []

    def _now(self):
        return self.clock[0]

    def _broker(self, eff=None, remote=None):
        return PublicationBroker(
            self.store, remote or self.remote, self.registrations,
            eff or self.eff, clock=self._now,
            alert_sink=self.alert_sink)

    def _accept(self, issue, delivery=None, sender="luongnv89"):
        env = {"delivery_id": delivery or f"d-{issue}",
               "channel": "reconciliation", "repo_id": self.repo_id,
               "repository": self.full_name, "issue": issue,
               "action": "reconcile", "sender": sender,
               "labels": ["factory-kit"], "issue_title": "t",
               "issue_body": BODY, "issue_revision": "2026-10-05"}
        ack = self.service.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        # Realistic binding: intake-accepted work is recorded against
        # the active generation before publication may target it.
        self.registrations.record_work(self.repo_id, work_key)
        return work_key

    def _request(self, work_key=None, **overrides):
        req = {"work_key": work_key,
               "operation": OPERATION_PR_PUBLISH,
               "target": "factory-kit/impl-42",
               "base": "main",
               "title": "implement issue",
               "repository": {"repo_id": self.repo_id,
                              "full_name": self.full_name},
               "expected_revision":
                   "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
               "actor_ref": ACTOR,
               "claim_expires_epoch": self.clock[0] + 600}
        req.update(overrides)
        return req

    def _intents(self, work_key=None):
        return self.store.intent_rows(work_key)


class GuardRemote(ScriptedRemote):
    """Asserts the durable intent already exists at the moment the
    remote effect is sent — the commit-before-effect ordering (A1)."""

    def __init__(self, *a, store=None, **kw):
        super().__init__(*a, **kw)
        self._store = store
        self.states_at_effect = []

    def _call(self, op, apply, **kw):
        identity = kw.get("identity") or {}
        intent = self._store.get_intent(identity.get("intent_id")) \
            if identity.get("intent_id") else None
        self.states_at_effect.append(
            (op, intent["state"] if intent else None))
        return super()._call(op, apply, **kw)


# ---------------------------------------------------------------------------
# A1 — durable intent before the effect; read-back binds identity
# ---------------------------------------------------------------------------

class TestA1IntentBeforeEffect(PublicationFixture):

    def test_intent_commits_before_remote_effect(self):
        work_key = self._accept(42)
        remote = GuardRemote(self.repo_id, self.full_name,
                             store=self.store)
        out = self._broker(remote=remote).publish(
            work_key, self._request())
        self.assertEqual(out["outcome"], "applied")
        # The remote call observed a committed intent — effect never
        # precedes the durable record.
        self.assertEqual(remote.states_at_effect,
                         [("pr-publish", "applied")])

    def test_intent_carries_full_bound_identity(self):
        work_key = self._accept(43)
        out = self._broker().publish(work_key, self._request())
        intent = self.store.get_intent(out["intent_id"])
        work = self.store.get_work(work_key)
        self.assertEqual(intent["task_id"], work["task_id"])
        self.assertEqual(intent["generation"], 1)
        self.assertEqual(intent["repo_id"], self.repo_id)
        self.assertEqual(intent["authority_key"], work["authority_key"])
        self.assertEqual(intent["operation"], "pr-publish")
        self.assertEqual(intent["target"], "factory-kit/impl-42")
        self.assertEqual(intent["expected_revision"],
                         "a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1")
        self.assertEqual(intent["state"], "linked")
        # Read-back bound the remote result to the same identity — the
        # PR record carries this intent's id + work + generation.
        pr = self.remote.pulls[out["remote_ref"]]
        self.assertEqual(pr["identity"]["intent_id"],
                         intent["intent_id"])
        self.assertEqual(pr["identity"]["work_key"], work_key)
        self.assertEqual(pr["identity"]["generation"], 1)
        # …and the work row links the remote PR once.
        self.assertEqual(self.store.get_work(work_key)["linked_pr"],
                         out["remote_ref"])
        events = self.store.event_rows("publication_intent_recorded")
        self.assertEqual(json.loads(events[-1]["detail"])
                         ["intent_id"], intent["intent_id"])

    def test_branch_publish_readback_binds_revision(self):
        work_key = self._accept(44)
        req = self._request(operation="branch-publish",
                            target="factory-kit/impl-44")
        out = self._broker().publish(work_key, req)
        self.assertEqual(out["outcome"], "applied")
        self.assertEqual(out["remote_ref"],
                         "refs/heads/factory-kit/impl-44")
        self.assertEqual(self.remote.branches["factory-kit/impl-44"],
                         req["expected_revision"])


# ---------------------------------------------------------------------------
# A2 — scoped broker; every denial class; no authority in workers
# ---------------------------------------------------------------------------

class TestA2ScopedBroker(PublicationFixture):

    def test_workers_carry_no_remote_authority(self):
        """The worker port exposes only candidate-result verbs — no
        store handle, no remote port, no publish/mutation surface."""
        from factory_kit.execution.worker import ScriptedWorker
        worker = ScriptedWorker()
        for forbidden in ("publish", "request_mutation", "remote",
                          "transact", "set_work_state", "github_token"):
            self.assertFalse(hasattr(worker, forbidden), forbidden)

    def test_wrong_repository_denied(self):
        work_key = self._accept(45)
        req = self._request(work_key=work_key,
                            repository={"repo_id": "R_other_repo",
                                        "full_name": "elsewhere/x"})
        out = self._broker().request_mutation(req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "unregistered-authority")
        self.assertEqual(self.remote.calls, [])      # no effect sent

    def test_wrong_action_denied(self):
        work_key = self._accept(46)
        # A verb the paved surface never granted.
        out = self._broker().request_mutation(
            self._request(work_key=work_key, operation="delete-branch"))
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "unknown-operation")
        # …and a paved op explicitly disabled is denied at authority.
        eff = effective()
        eff["endpoint"]["disabled"] = sorted(
            set(eff["endpoint"]["disabled"]) | {"pr-publish"})
        out = self._broker(eff=eff).publish(
            work_key, self._request())
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "operation-not-granted")
        self.assertEqual(self.remote.calls, [])

    def test_stale_generation_denied(self):
        work_key = self._accept(47)
        # Fresh authorization mints generation 2 — generation-1 work
        # can no longer publish.
        self.registrations.authorize_generation(
            self.repo_id, self.eff, authorized_by="operator",
            reason="rotate")
        out = self._broker().publish(work_key, self._request())
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "stale-generation")
        self.assertEqual(self.remote.calls, [])

    def test_changed_config_denied(self):
        work_key = self._accept(48)
        drifted = effective()
        drifted["limits"]["wall_hours"] = 12    # rekeys both digests
        out = self._broker(eff=drifted).publish(
            work_key, self._request())
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "stale-config")
        self.assertEqual(self.remote.calls, [])

    def test_expired_claim_denied(self):
        work_key = self._accept(49)
        req = self._request(work_key=work_key,
                            claim_expires_epoch=self.clock[0] - 1)
        out = self._broker().request_mutation(req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "expired-claim")
        self.assertEqual(self.remote.calls, [])

    def test_revoked_actor_denied(self):
        work_key = self._accept(50)
        out = self._broker().request_mutation(
            self._request(work_key=work_key, actor_ref="mallory"))
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "actor-revoked")
        self.assertEqual(self.remote.calls, [])

    def test_malformed_repository_shape_denied_not_raised(self):
        """A non-dict ``repository`` field is malformed input — denied
        cleanly, never an AttributeError escape from the worker-facing
        surface."""
        work_key = self._accept(68)
        req = self._request(work_key=work_key)
        req["repository"] = "not-a-dict"
        out = self._broker().request_mutation(req)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "bad-repository")
        self.assertEqual(self.remote.calls, [])

    def test_expired_claim_at_effect_time_denies(self):
        """The claim window is re-evaluated in the transaction that
        marks the intent applied — a claim lapsing between the intent
        commit and the effect still denies (violation: quarantine +
        alert), and no remote call leaves."""
        work_key = self._accept(69)
        ticks = [self.clock[0]]

        def advancing():
            ticks[0] += 1000.0
            return ticks[0]

        broker = PublicationBroker(
            self.store, self.remote, self.registrations, self.eff,
            clock=advancing, alert_sink=self.alert_sink)
        req = self._request(
            work_key=work_key,
            claim_expires_epoch=self.clock[0] + 1500)
        out = broker.publish(work_key, req)
        # Clock reads: intent commit at t+1000 (< t+1500, live); the
        # pre-effect gate reads t+2000 — the claim already expired.
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "expired-claim")
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "quarantined")
        self.assertTrue(self.store.alert_rows("unauthorized-mutation"))


# ---------------------------------------------------------------------------
# A3 — one accepted operation/PR; crash-window recovery by identity
# ---------------------------------------------------------------------------

class TestA3SerializationAndRecovery(PublicationFixture):

    def test_concurrent_attempts_converge_one_pr(self):
        work_key = self._accept(51)
        broker = self._broker()
        first = broker.publish(work_key, self._request())
        second = broker.publish(work_key, self._request())
        self.assertEqual(first["outcome"], "applied")
        self.assertEqual(second["outcome"], "converged")
        self.assertEqual(second["intent_id"], first["intent_id"])
        pr_calls = [c for c in self.remote.calls
                    if c["op"] == "pr-publish"]
        self.assertEqual(len(pr_calls), 1)          # one remote effect
        self.assertEqual(len(self.remote.pulls), 1)

    def test_crash_after_create_recovers_by_identity(self):
        """Remote PR created, response lost: reconciliation finds the
        existing PR under the recorded identity — no republish."""
        work_key = self._accept(52)
        self.remote.faults["pr-publish"] = "crash-after-create"
        out = self._broker().publish(work_key, self._request())
        self.assertEqual(out["outcome"], "applied")   # reconciled
        self.assertEqual(len(self.remote.pulls), 1)   # never doubled
        intent = self.store.get_intent(out["intent_id"])
        self.assertEqual(intent["state"], "linked")
        pr = self.remote.pulls[out["remote_ref"]]
        self.assertEqual(pr["identity"]["work_key"], work_key)

    def test_transient_remote_failure_retries_same_identity(self):
        """A clean remote refusal leaves a terminal ``failed`` intent —
        a healthy retry re-opens the same serialized identity (one row,
        one remote effect) instead of tombstoning the operation."""
        work_key = self._accept(63)
        self.remote.faults["pr-publish"] = "raise"
        first = self._broker().publish(work_key, self._request())
        self.assertEqual(first["reason"], "remote-error")
        self.assertEqual(self.store.get_intent(first["intent_id"])
                         ["state"], "failed")
        self.remote.faults.pop("pr-publish")
        second = self._broker().publish(work_key, self._request())
        self.assertEqual(second["outcome"], "applied")
        self.assertEqual(second["intent_id"], first["intent_id"])
        self.assertEqual(len(self.remote.pulls), 1)
        self.assertEqual(len(self.store.intent_rows(work_key)), 1)
        events = self.store.event_rows("publication_intent_reopened")
        self.assertEqual(json.loads(events[-1]["detail"])
                         ["intent_id"], first["intent_id"])

    def test_denied_intent_reopens_after_config_restored(self):
        """A non-violation denial (stale config) leaves terminal
        evidence; once the config matches the bound digests again the
        retry re-opens the same identity — no silent second row."""
        work_key = self._accept(64)
        drifted = effective()
        drifted["limits"]["wall_hours"] = 12
        first = self._broker(eff=drifted).publish(
            work_key, self._request())
        self.assertEqual(first["reason"], "stale-config")
        second = self._broker().publish(work_key, self._request())
        self.assertEqual(second["outcome"], "applied")
        self.assertEqual(second["intent_id"], first["intent_id"])
        self.assertEqual(len(self.store.intent_rows(work_key)), 1)

    def test_expired_claim_replay_on_consumed_identity_converges(self):
        """A replay with an expired claim over an already-linked
        identity converges on the committed intent — never a crash on
        the serialized identity's unique index."""
        work_key = self._accept(65)
        out = self._broker().publish(work_key, self._request())
        self.assertEqual(out["outcome"], "applied")
        replay = self._broker().publish(
            work_key,
            self._request(claim_expires_epoch=self.clock[0] - 1))
        self.assertEqual(replay["outcome"], "converged")
        self.assertEqual(replay["intent_id"], out["intent_id"])

    def test_pending_intent_recovery_links_without_republish(self):
        """A broker restart finds intents stuck pre-effect and links
        whatever the recorded identity already created remotely."""
        work_key = self._accept(53)
        self.store.insert_intent(
            "pub-deadbeef", work_key=work_key,
            task_id="fk-task-000001", generation=1,
            authority_key=self.store.get_work(work_key)["authority_key"],
            repo_id=self.repo_id, operation=OPERATION_PR_PUBLISH,
            target="factory-kit/impl-53", state="applied")
        # The remote already has the PR the lost response described.
        self.remote.pulls["777"] = {
            "number": "777", "head": "factory-kit/impl-53",
            "base": "main", "state": "open",
            "url": "https://example.test/pull/777",
            "identity": {"work_key": work_key,
                         "authority_key":
                             self.store.get_work(work_key)
                             ["authority_key"],
                         "generation": 1}}
        outcomes = self._broker().reconcile_pending()
        self.assertEqual(outcomes[0]["outcome"], "applied")
        self.assertEqual(outcomes[0]["remote_ref"], "777")
        self.assertEqual(len(self.remote.pulls), 1)   # no republish
        self.assertFalse(any(c["op"] == "pr-publish"
                             for c in self.remote.calls))
        self.assertEqual(self.store.get_intent("pub-deadbeef")
                         ["state"], "linked")


# ---------------------------------------------------------------------------
# A4 — fence-before-effect denies; in-flight effects reconcile honestly
# ---------------------------------------------------------------------------

class TestA4FenceOrdering(PublicationFixture):

    def test_fence_before_effect_denies(self):
        work_key = self._accept(54)
        self.store.fence_work(work_key, "cancel", 1)
        out = self._broker().publish(work_key, self._request())
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "fenced")
        self.assertEqual(self.remote.calls, [])       # no effect
        self.assertEqual(self.store.get_intent(out["intent_id"])
                         ["state"], "denied")

    def test_inflight_effect_authorized_before_fence_links(self):
        """The fence lands between the intent commit and the read-back:
        the already-authorized effect is reconciled and accurately
        linked — never silently rolled back or reported absent."""
        work_key = self._accept(55)
        store = self.store

        class FencingRemote(ScriptedRemote):
            def open_pr(self, head, base, title, identity):
                result = super().open_pr(head, base, title, identity)
                store.fence_work(work_key, "cancel", 1)  # mid-flight
                return result

        remote = FencingRemote(self.repo_id, self.full_name)
        out = self._broker(remote=remote).publish(
            work_key, self._request())
        self.assertEqual(out["outcome"], "applied")
        self.assertEqual(self.store.get_intent(out["intent_id"])
                         ["state"], "linked")
        self.assertTrue(self.store.fence_state(work_key)["fenced"])
        # The remote PR still exists — linked, not absent.
        self.assertEqual(len(remote.pulls), 1)


# ---------------------------------------------------------------------------
# A5 — uncertainty parks; user work is never closed/deleted/overwritten
# ---------------------------------------------------------------------------

class TestA5UncertaintyParks(PublicationFixture):

    def test_two_matching_remote_prs_park_and_alert(self):
        work_key = self._accept(56)
        for number in ("900", "901"):
            self.remote.pulls[number] = {
                "number": number, "head": "factory-kit/impl-56",
                "base": "main", "state": "open",
                "url": f"https://example.test/pull/{number}",
                "identity": {"work_key": work_key}}
        out = self._broker().publish(work_key, self._request(
            target="factory-kit/impl-56"))
        self.assertEqual(out["outcome"], "parked")
        self.assertEqual(out["reason"], "duplicate-remote-pr")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")
        self.assertEqual(self.store.get_work(work_key)
                         ["parked_reason"], "duplicate-remote-pr")
        alerts = self.store.alert_rows("duplicate-remote-pr")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "high")
        # No new PR, nothing closed or deleted — both remain open.
        self.assertEqual(len(self.remote.pulls), 2)
        self.assertTrue(all(p["state"] == "open"
                            for p in self.remote.pulls.values()))
        self.assertFalse(any(c["op"] == "pr-publish"
                             for c in self.remote.calls))

    def test_uncertain_repository_identity_parks(self):
        work_key = self._accept(57)
        self.remote.identity_override = {
            "repo_id": "R_somewhere_else",
            "full_name": "other/repo"}
        out = self._broker().publish(work_key, self._request())
        self.assertEqual(out["outcome"], "parked")
        self.assertEqual(out["reason"], "uncertain-repo-identity")
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "parked")
        self.assertTrue(self.store.alert_rows(
            "uncertain-repo-identity"))

    def test_ambiguous_outcome_parks_without_republish(self):
        work_key = self._accept(58)
        self.remote.faults["pr-publish"] = "crash-before-create"
        out = self._broker().publish(work_key, self._request())
        self.assertEqual(out["outcome"], "parked")
        self.assertEqual(out["reason"], "ambiguous-outcome")
        self.assertEqual(len(self.remote.pulls), 0)
        # And the remote port itself offers no destructive verbs —
        # parking is the only answer to uncertainty.
        for destructive in ("close_pr", "delete_branch", "force_push"):
            self.assertFalse(hasattr(self.remote, destructive),
                             destructive)


# ---------------------------------------------------------------------------
# A6 — unauthorized mutation: evidence + quarantine + deduped alert
# ---------------------------------------------------------------------------

class TestA6ViolationQuarantineAlert(PublicationFixture):

    def test_unauthorized_attempt_evidence_quarantine_alert(self):
        work_key = self._accept(59)
        out = self._broker().request_mutation(
            self._request(work_key=work_key, actor_ref="mallory"))
        self.assertEqual(out["outcome"], "denied")
        # Durable evidence: the denied intent row with the actor.
        intent = self.store.get_intent(out["intent_id"])
        self.assertEqual(intent["state"], "denied")
        self.assertEqual(intent["reason"], "actor-revoked")
        self.assertEqual(intent["actor_ref"], "mallory")
        # Affected work quarantined.
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "quarantined")
        # High-severity alert: durable row + operator sink emission.
        alerts = self.store.alert_rows("unauthorized-mutation")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["severity"], "high")
        self.assertEqual(self.alert_sink[-1]["kind"],
                         "unauthorized-mutation")
        self.assertEqual(self.alert_sink[-1]["severity"], "high")

    def test_repeated_violation_never_repages(self):
        work_key = self._accept(60)
        broker = self._broker()
        broker.request_mutation(
            self._request(work_key=work_key, actor_ref="mallory",
                          target="b/mallory-1"))
        # A second unauthorized attempt on a *different* target still
        # produces durable denied-intent evidence — but the operator
        # alert for this work was already emitted and never re-pages.
        second = broker.request_mutation(
            self._request(work_key=work_key, actor_ref="mallory",
                          target="b/mallory-2"))
        self.assertEqual(second["outcome"], "denied")
        denied = [i for i in self.store.intent_rows(work_key)
                  if i["state"] == "denied"]
        self.assertEqual(len(denied), 2)
        self.assertEqual(
            len(self.store.alert_rows("unauthorized-mutation")), 1)
        emitted = [a for a in self.alert_sink
                   if a["outcome"] == "emitted"]
        self.assertEqual(len(emitted), 1)

    def test_repeated_violation_same_identity_converges(self):
        """A repeated unauthorized attempt on the SAME (work,
        operation, target) converges on the committed denied intent —
        the serialized identity is enforced by the unique index, so the
        second denial must not crash on it; the evidence, quarantine
        and deduplicated alert from the first attempt still stand."""
        work_key = self._accept(62)
        broker = self._broker()
        req = self._request(work_key=work_key, actor_ref="mallory")
        first = broker.request_mutation(dict(req))
        second = broker.request_mutation(dict(req))
        self.assertEqual(first["outcome"], "denied")
        self.assertEqual(second["outcome"], "denied")
        self.assertEqual(second["intent_id"], first["intent_id"])
        denied = [i for i in self.store.intent_rows(work_key)
                  if i["state"] == "denied"]
        self.assertEqual(len(denied), 1)          # one identity, one row
        self.assertEqual(self.store.get_work(work_key)["state"],
                         "quarantined")
        self.assertEqual(
            len(self.store.alert_rows("unauthorized-mutation")), 1)

    def test_alert_store_dedupes_same_identity(self):
        """The durable dedup contract the operator channel relies on:
        one (kind, identity, severity) — one row, one emission."""
        work_key = self._accept(61)
        first = self.store.emit_alert(
            "unauthorized-mutation", work_key, "high")
        second = self.store.emit_alert(
            "unauthorized-mutation", work_key, "high")
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])

    def test_fenced_attempt_retained_without_false_quarantine(self):
        """A denial that is only a pre-existing fence is retained as
        evidence but does not quarantine — the fence is already the
        stronger record."""
        work_key = self._accept(61)
        self.store.fence_work(work_key, "cancel", 1)
        out = self._broker().request_mutation(
            self._request(work_key=work_key))
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "fenced")
        self.assertEqual(self.store.get_intent(out["intent_id"])
                         ["state"], "denied")
        self.assertNotEqual(self.store.get_work(work_key)["state"],
                            "quarantined")


if __name__ == "__main__":
    unittest.main()
