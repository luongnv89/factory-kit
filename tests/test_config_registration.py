#!/usr/bin/env python3
"""Registration contract fixtures (issue #6, A7 / REC01 + CFG-C02).

A registration persists repository identity, configuration digest, owner,
supported version set, authorization policy and readiness outcome; a policy
change parks affected active work or opens an explicitly authorized fresh
generation — and never broadens a generation already recorded.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_config_registration.py
"""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from factory_kit.config import registration, schema  # noqa: E402

MANIFEST = ROOT / ".factory-kit.yml"
READINESS = {"verdict": "ready", "blockers": [],
             "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]


def effective():
    return schema.load_manifest_file(MANIFEST)


def policy_changed(eff):
    changed = copy.deepcopy(eff)
    changed["authorization"]["github_actors"] = ["luongnv89", "second-op"]
    changed["limits"]["active_tasks"] = 2
    return changed


class RegistrationPersistenceTests(unittest.TestCase):
    """REC01 — the record and its required contents."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registration.json"
        self.store = registration.RegistrationStore(self.path)

    def test_register_persists_required_fields(self):
        eff = effective()
        self.store.register(eff, readiness=READINESS,
                            supported_versions=VERSIONS)
        record = self.store.get(eff["identity"]["repo_id"])
        self.assertEqual(record["authority_key"],
                         schema.authority_key(eff["identity"]))
        self.assertEqual(record["owner"], "luongnv89")
        self.assertEqual(record["supported_versions"], VERSIONS)
        self.assertEqual(record["readiness"], READINESS)
        gen = record["generations"][0]
        self.assertEqual(gen["config_digest"],
                         schema.effective_digest(eff))
        self.assertEqual(gen["policy_digest"], schema.policy_digest(eff))

    def test_store_survives_restart(self):
        eff = effective()
        self.store.register(eff, readiness=READINESS,
                            supported_versions=VERSIONS)
        reopened = registration.RegistrationStore(self.path)
        record = reopened.get(eff["identity"]["repo_id"])
        self.assertIsNotNone(record, "registration must be durable")
        self.assertEqual(record["generations"][0]["config_digest"],
                         schema.effective_digest(eff))

    def test_register_is_idempotent_no_duplicate(self):
        eff = effective()
        first = self.store.register(eff, readiness=READINESS,
                                    supported_versions=VERSIONS)
        second = self.store.register(eff, readiness=READINESS,
                                     supported_versions=VERSIONS)
        self.assertEqual(second["outcome"], "existing")
        self.assertEqual(len(self.store._data["registrations"]), 1)
        self.assertEqual(first["registration"]["created_at"],
                         second["registration"]["created_at"])

    def test_display_rename_returns_same_registration(self):
        eff = effective()
        self.store.register(eff, readiness=READINESS,
                            supported_versions=VERSIONS)
        renamed = copy.deepcopy(eff)
        renamed["identity"]["name"] = "new-display-name"
        result = self.store.register(renamed, readiness=READINESS,
                                     supported_versions=VERSIONS)
        self.assertEqual(result["outcome"], "existing")
        record = result["registration"]
        self.assertEqual(record["display"]["name"], "new-display-name")
        self.assertEqual(record["authority_key"],
                         schema.authority_key(eff["identity"]),
                         "renaming the display name cannot change "
                         "repository authority")

    def test_reregister_with_policy_drift_is_not_silent(self):
        """A re-run setup carrying a changed policy is a policy change —
        it parks affected work, never reports a quiet 'existing'."""
        eff = effective()
        self.store.register(eff, readiness=READINESS,
                            supported_versions=VERSIONS)
        repo_id = eff["identity"]["repo_id"]
        self.store.record_work(repo_id, "issue-9")
        drifted = copy.deepcopy(eff)
        drifted["authorization"]["github_actors"] = ["luongnv89", "other"]
        result = self.store.register(drifted, readiness=READINESS,
                                     supported_versions=VERSIONS)
        self.assertEqual(result["outcome"], "parked")
        self.assertIn("issue-9", result["affected"])
        record = self.store.get(repo_id)
        self.assertEqual(record["generations"][0]["policy_digest"],
                         schema.policy_digest(eff),
                         "the drifted policy must not overwrite the "
                         "recorded generation")


class PolicyChangeTests(unittest.TestCase):
    """A7 / CFG-C02 — park or fresh-generation, never silent expansion."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registration.json"
        self.store = registration.RegistrationStore(self.path)
        self.eff = effective()
        self.repo_id = self.eff["identity"]["repo_id"]
        self.store.register(self.eff, readiness=READINESS,
                            supported_versions=VERSIONS)
        self.store.record_work(self.repo_id, "issue-42")

    def test_unauthorized_change_parks_active_work(self):
        result = self.store.apply_policy_change(
            self.repo_id, policy_changed(self.eff))
        self.assertEqual(result["outcome"], "parked")
        self.assertIn("issue-42", result["affected"])
        work = self.store.get(self.repo_id)["work"]["issue-42"]
        self.assertEqual(work["state"], "parked")
        self.assertEqual(self.store.get(self.repo_id)["active_generation"],
                         1, "no new generation without authorization")

    def test_parked_work_cannot_attach_digest(self):
        self.store.apply_policy_change(
            self.repo_id, policy_changed(self.eff))
        with self.assertRaises(registration.RegistrationError):
            self.store.attempt_context(self.repo_id, "issue-42",
                                       "implementation")

    def test_authorized_change_opens_new_generation(self):
        changed = policy_changed(self.eff)
        result = self.store.apply_policy_change(
            self.repo_id, changed, authorized_by="luongnv89")
        self.assertEqual(result["outcome"], "new-generation")
        self.assertEqual(result["generation"], 2)
        record = self.store.get(self.repo_id)
        self.assertEqual(record["active_generation"], 2)
        self.assertEqual(record["generations"][1]["policy_digest"],
                         schema.policy_digest(changed))
        self.assertEqual(record["generations"][1]["authorized_by"],
                         "luongnv89")

    def test_new_generation_never_broadens_existing(self):
        """The gen-1 row must stay byte-identical after gen-2 exists."""
        changed = policy_changed(self.eff)
        self.store.apply_policy_change(
            self.repo_id, changed, authorized_by="luongnv89")
        raw = json.loads(self.path.read_text())
        gens = raw["registrations"][self.repo_id]["generations"]
        self.assertEqual(len(gens), 2)
        self.assertEqual(gens[0]["policy_digest"],
                         schema.policy_digest(self.eff),
                         "generation 1's policy must never be rewritten")
        self.assertEqual(gens[0]["config_digest"],
                         schema.effective_digest(self.eff))
        self.assertNotEqual(gens[0]["policy_digest"],
                            gens[1]["policy_digest"])
        self.assertNotIn("second-op",
                         json.dumps(gens[0]),
                         "an authorized later generation never broadens "
                         "the earlier one's authorization policy")

    def test_display_only_change_is_not_a_policy_change(self):
        renamed = copy.deepcopy(self.eff)
        renamed["identity"]["name"] = "display-rename"
        result = self.store.apply_policy_change(self.repo_id, renamed)
        self.assertEqual(result["outcome"], "unchanged")
        work = self.store.get(self.repo_id)["work"]["issue-42"]
        self.assertEqual(work["state"], "active",
                         "a display rename must never park work")


class AttemptDigestTests(unittest.TestCase):
    """CFG-C02 — every attempt carries the immutable effective digest."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "registration.json"
        self.store = registration.RegistrationStore(self.path)
        self.eff = effective()
        self.repo_id = self.eff["identity"]["repo_id"]
        self.store.register(self.eff, readiness=READINESS,
                            supported_versions=VERSIONS)

    def test_attempt_context_attaches_config_digest(self):
        self.store.record_work(self.repo_id, "issue-7")
        ctx = self.store.attempt_context(self.repo_id, "issue-7",
                                         "implementation")
        self.assertEqual(ctx["config_digest"],
                         schema.effective_digest(self.eff))
        self.assertEqual(ctx["policy_digest"],
                         schema.policy_digest(self.eff))
        self.assertEqual(ctx["generation"], 1)
        self.assertEqual(ctx["repo_id"], self.repo_id)

    def test_rebound_work_attaches_new_generation_digest(self):
        """Parked work re-binds only explicitly — under the new digest."""
        self.store.record_work(self.repo_id, "issue-8")
        ctx_before = self.store.attempt_context(self.repo_id, "issue-8",
                                                "review")
        changed = policy_changed(self.eff)
        self.store.apply_policy_change(
            self.repo_id, changed, authorized_by="luongnv89")
        with self.assertRaises(registration.RegistrationError):
            self.store.attempt_context(self.repo_id, "issue-8", "review")
        self.store.record_work(self.repo_id, "issue-8")  # explicit rebind
        ctx_after = self.store.attempt_context(self.repo_id, "issue-8",
                                               "review")
        self.assertEqual(ctx_before["config_digest"],
                         schema.effective_digest(self.eff))
        self.assertEqual(ctx_after["generation"], 2)
        self.assertEqual(ctx_after["config_digest"],
                         schema.effective_digest(changed),
                         "restart happens under the authorized fresh "
                         "generation — never silently under the old one")

    def test_unregistered_repo_rejects_attempt(self):
        with self.assertRaises(registration.RegistrationError):
            self.store.attempt_context("R_kgDOnone", "issue-1",
                                       "implementation")

    def test_unrecorded_work_rejects_attempt(self):
        with self.assertRaises(registration.RegistrationError):
            self.store.attempt_context(self.repo_id, "issue-999",
                                       "review")


if __name__ == "__main__":
    unittest.main(verbosity=2)
