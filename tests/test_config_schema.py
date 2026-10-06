#!/usr/bin/env python3
"""Executable schema fixtures for .factory-kit.yml (issue #6, A1–A5).

Covers PRD §6.3 CFG01–CFG09: immutable identity + deny-by-default
authorization (A1), runtime/role/skill pins + actionable failures (A2),
nonempty verification contract + bounded defaults (A3), endpoint policy +
disabled capabilities (A4), retention/tombstones + secret references (A5).

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_config_schema.py
"""

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from factory_kit.config import schema, yamlmini  # noqa: E402

MANIFEST = ROOT / ".factory-kit.yml"


def load_valid():
    return yamlmini.load_file(MANIFEST)


def problems_of(mutator):
    """Apply ``mutator`` to a fresh copy of the valid manifest, then return
    the flattened problem paths validation raises."""
    raw = load_valid()
    mutator(raw)
    with unittest.TestCase().assertRaises(schema.ConfigError) as ctx:
        schema.validate(raw)
    return ctx.exception.problems


def paths(problems):
    return {p["path"] for p in problems}


class ShippedManifestTests(unittest.TestCase):
    """The repo-root .factory-kit.yml is the canonical schema fixture."""

    def test_shipped_manifest_validates(self):
        effective = schema.load_manifest_file(MANIFEST)
        self.assertEqual(effective["factory_kit"], 1)

    def test_digest_is_stable_and_sensitive(self):
        effective = schema.load_manifest_file(MANIFEST)
        again = schema.load_manifest_file(MANIFEST)
        self.assertEqual(schema.effective_digest(effective),
                         schema.effective_digest(again))
        mutated = copy.deepcopy(effective)
        mutated["limits"]["active_tasks"] = 2
        self.assertNotEqual(schema.effective_digest(effective),
                            schema.effective_digest(mutated))

    def test_shipped_manifest_digests_match_main(self):
        """New optional keys must not shift existing digests — the
        shipped manifest predates ``approval_channels`` and its
        effective/policy digests are pinned to the values main
        computed for the identical file."""
        effective = schema.load_manifest_file(MANIFEST)
        self.assertEqual(
            schema.effective_digest(effective),
            "471800e23c5595c818afde9d59d627dc6800fba44ca6444430d072303"
            "433a372")
        self.assertEqual(
            schema.policy_digest(effective),
            "a3808f62b97d652152c437a250e8ef5f71159c3de1d4ee4244e23922"
            "395ba410")


class IdentityAuthorizationTests(unittest.TestCase):
    """A1 — CFG01/CFG02."""

    def test_repo_id_required(self):
        def mutate(raw):
            raw["identity"]["repo_id"] = ""
        self.assertIn("identity.repo_id", paths(problems_of(mutate)))

    def test_display_rename_keeps_authority(self):
        effective = schema.load_manifest_file(MANIFEST)
        renamed = copy.deepcopy(effective)
        renamed["identity"]["owner"] = "someone-else"
        renamed["identity"]["name"] = "renamed-repo"
        self.assertEqual(schema.authority_key(effective["identity"]),
                         schema.authority_key(renamed["identity"]))
        self.assertEqual(schema.policy_digest(effective),
                         schema.policy_digest(renamed),
                         "a display rename must not be a policy change")

    def test_different_repo_id_changes_authority(self):
        effective = schema.load_manifest_file(MANIFEST)
        other = copy.deepcopy(effective)
        other["identity"]["repo_id"] = "R_kgDOdifferent"
        self.assertNotEqual(schema.authority_key(effective["identity"]),
                            schema.authority_key(other["identity"]))

    def test_one_registration_owner_required(self):
        def mutate(raw):
            del raw["registration"]["owner"]
        self.assertIn("registration.owner", paths(problems_of(mutate)))

    def test_deny_by_default_when_opt_in_absent(self):
        raw = load_valid()
        del raw["authorization"]["execution_opt_in"]
        effective = schema.validate(raw)
        self.assertFalse(
            schema.is_execution_authorized(
                effective, github_actor="luongnv89"),
            "absent opt-in must deny even an allowlisted actor")

    def test_deny_by_default_for_unlisted_principals(self):
        effective = schema.load_manifest_file(MANIFEST)
        self.assertFalse(schema.is_execution_authorized(
            effective, github_actor="unlisted-user"))
        self.assertFalse(schema.is_execution_authorized(
            effective, telegram_user=999))
        self.assertFalse(schema.is_execution_authorized(
            effective, telegram_chat=-42))

    def test_opted_in_allowlisted_principal_authorizes(self):
        effective = schema.load_manifest_file(MANIFEST)
        self.assertTrue(schema.is_execution_authorized(
            effective, github_actor="luongnv89"))
        self.assertTrue(schema.is_execution_authorized(
            effective, telegram_user=123456789))
        self.assertTrue(schema.is_execution_authorized(
            effective, telegram_chat=-1001234567890))

    def test_opt_in_without_principal_denies(self):
        effective = schema.load_manifest_file(MANIFEST)
        self.assertFalse(schema.is_execution_authorized(effective),
                         "the opt-in flag alone authorizes no one")

    def test_actor_and_role_categories_never_cross(self):
        effective = schema.load_manifest_file(MANIFEST)
        self.assertFalse(schema.is_execution_authorized(
            effective, github_actor="maintainer"),
            "an actor named like a role must not inherit role authority")
        self.assertTrue(schema.is_execution_authorized(
            effective, github_role="maintainer"))
        self.assertFalse(schema.is_execution_authorized(
            effective, github_role="luongnv89"),
            "a role named like an actor must not inherit actor authority")

    def test_telegram_allowlists_are_numeric_only(self):
        def mutate(raw):
            raw["authorization"]["telegram_users"] = ["@alice"]
        problems = problems_of(mutate)
        self.assertIn("authorization.telegram_users[0]", paths(problems))

    def test_telegram_bool_is_not_numeric_id(self):
        def mutate(raw):
            raw["authorization"]["telegram_chats"] = [True]
        self.assertIn("authorization.telegram_chats[0]",
                      paths(problems_of(mutate)))


class RuntimeRoleSkillTests(unittest.TestCase):
    """A2 — CFG03/CFG04."""

    def test_unsupported_runtime_fails(self):
        def mutate(raw):
            raw["runtime"]["name"] = "claude-cli"
        problems = problems_of(mutate)
        self.assertIn("runtime.name", paths(problems))
        self.assertTrue(any("hermes-kanban" in p["message"]
                            for p in problems),
                        "error must name the supported runtime")

    def test_missing_review_role_fails(self):
        def mutate(raw):
            del raw["runtime"]["roles"]["review"]
        self.assertIn("runtime.roles.review", paths(problems_of(mutate)))

    def test_missing_model_fails(self):
        def mutate(raw):
            raw["runtime"]["roles"]["implementation"]["model"] = ""
        self.assertIn("runtime.roles.implementation.model",
                      paths(problems_of(mutate)))

    def test_missing_tool_policy_fails(self):
        def mutate(raw):
            del raw["runtime"]["tools"]
        self.assertIn("runtime.tools", paths(problems_of(mutate)))

    def test_unpinned_skill_revision_fails(self):
        for bad in ("latest", "*", "", ">=1.0", "main"):
            def mutate(raw, value=bad):
                raw["skills"]["approved"]["issue-resolver"] = value
            self.assertIn("skills.approved.issue-resolver",
                          paths(problems_of(mutate)), bad)

    def test_pinned_revisions_accepted(self):
        raw = load_valid()
        raw["skills"]["approved"]["custom"] = "a" * 40
        raw["skills"]["approved"]["other"] = "v1.2.3-rc.1"
        self.assertIsInstance(schema.validate(raw), dict)

    def test_newly_discovered_skill_execution_rejected(self):
        def mutate(raw):
            raw["skills"]["auto_discover"] = True
        self.assertIn("skills.auto_discover", paths(problems_of(mutate)))

    def test_unknown_field_fails_with_actionable_error(self):
        def mutate(raw):
            raw["nonsense"] = True
        problems = problems_of(mutate)
        self.assertIn("nonsense", paths(problems))
        self.assertTrue(any("remove it" in p["message"] for p in problems))

    def test_unknown_nested_field_fails(self):
        def mutate(raw):
            raw["limits"]["surprise"] = 3
        self.assertIn("limits.surprise", paths(problems_of(mutate)))

    def test_incompatible_schema_version_fails(self):
        def mutate(raw):
            raw["factory_kit"] = 99
        problems = problems_of(mutate)
        self.assertIn("factory_kit", paths(problems))
        self.assertTrue(any("incompatible" in p["message"]
                            for p in problems))

    def test_missing_schema_version_fails(self):
        def mutate(raw):
            del raw["factory_kit"]
        self.assertIn("factory_kit", paths(problems_of(mutate)))


class VerificationLimitTests(unittest.TestCase):
    """A3 — CFG05/CFG06."""

    def test_empty_acceptance_commands_rejected(self):
        def mutate(raw):
            raw["verification"]["acceptance_commands"] = []
        self.assertIn("verification.acceptance_commands",
                      paths(problems_of(mutate)))

    def test_missing_required_checks_rejected(self):
        """The empty contract fails even with no protected checks."""
        def mutate(raw):
            del raw["verification"]["required_checks"]
        problems = problems_of(mutate)
        self.assertIn("verification.required_checks", paths(problems))
        self.assertTrue(any("empty verification contract" in p["message"]
                            for p in problems))

    def test_empty_check_contexts_rejected(self):
        def mutate(raw):
            raw["verification"]["required_checks"]["contexts"] = []
        self.assertIn("verification.required_checks.contexts",
                      paths(problems_of(mutate)))

    def test_limit_defaults_applied(self):
        raw = load_valid()
        del raw["limits"]
        effective = schema.validate(raw)
        self.assertEqual(effective["limits"], {
            "active_tasks": 1,
            "implementation_attempts": 2,
            "active_worker_minutes": 60,
            "wall_hours": 24,
        })

    def test_limit_out_of_bounds_fails(self):
        def mutate(raw):
            raw["limits"]["active_worker_minutes"] = 99999
        problems = problems_of(mutate)
        self.assertIn("limits.active_worker_minutes", paths(problems))
        self.assertTrue(any("between" in p["message"] for p in problems))

    def test_zero_active_tasks_fails(self):
        def mutate(raw):
            raw["limits"]["active_tasks"] = 0
        self.assertIn("limits.active_tasks", paths(problems_of(mutate)))


class EndpointPolicyTests(unittest.TestCase):
    """A4 — CFG09."""

    def test_one_supported_preview_provider(self):
        def mutate(raw):
            raw["endpoint"]["preview"]["provider"] = "netlify"
        self.assertIn("endpoint.preview.provider",
                      paths(problems_of(mutate)))

    def test_one_supported_merge_method(self):
        def mutate(raw):
            raw["endpoint"]["merge"]["method"] = "rebase"
        self.assertIn("endpoint.merge.method",
                      paths(problems_of(mutate)))

    def test_approval_expiry_default_and_bound(self):
        raw = load_valid()
        del raw["endpoint"]["merge"]["approval_expiry_minutes"]
        effective = schema.validate(raw)
        self.assertEqual(
            effective["endpoint"]["merge"]["approval_expiry_minutes"], 60)

        def mutate(r):
            r["endpoint"]["merge"]["approval_expiry_minutes"] = 5
        self.assertIn("endpoint.merge.approval_expiry_minutes",
                      paths(problems_of(mutate)))

    def test_smoke_age_default_and_bound(self):
        effective = schema.load_manifest_file(MANIFEST)
        self.assertEqual(
            effective["endpoint"]["merge"]["max_smoke_age_minutes"], 10)

        def mutate(r):
            r["endpoint"]["merge"]["max_smoke_age_minutes"] = 45
        self.assertIn("endpoint.merge.max_smoke_age_minutes",
                      paths(problems_of(mutate)))

    def test_required_capabilities_stay_disabled(self):
        effective = schema.load_manifest_file(MANIFEST)
        for cap in schema.REQUIRED_DISABLED:
            self.assertIn(cap, effective["endpoint"]["disabled"])

    def test_dropping_a_disabled_capability_fails(self):
        def mutate(raw):
            raw["endpoint"]["disabled"] = ["production_deploy"]
        problems = problems_of(mutate)
        self.assertIn("endpoint.disabled", paths(problems))
        self.assertTrue(any("cannot be" in p["message"] or
                            "must keep" in p["message"]
                            for p in problems))

    def test_worker_cannot_enable_production(self):
        """Worker-input escape hatches are unknown fields — hard fail."""
        for key in ("production_deploy", "allow_production",
                    "autonomous_merge", "enable_autonomous_merge"):
            def mutate(raw, k=key):
                raw["endpoint"][k] = True
            self.assertIn(f"endpoint.{key}",
                          paths(problems_of(mutate)), key)

    def test_production_preview_environment_rejected(self):
        def mutate(raw):
            raw["endpoint"]["preview"]["environment"] = "production"
        self.assertIn("endpoint.preview.environment",
                      paths(problems_of(mutate)))

    def test_approval_channels_absent_unless_manifest_sets_it(self):
        """The key is carried only when the manifest names it — every
        reader defaults to ["telegram"], and an unset key keeps the
        effective/policy digests of pre-channel manifests stable."""
        effective = schema.load_manifest_file(MANIFEST)
        self.assertNotIn("approval_channels",
                         effective["endpoint"]["merge"])

    def test_approval_channels_operator_cli(self):
        raw = load_valid()
        raw["endpoint"]["merge"]["approval_channels"] = [
            "operator-cli"]
        effective = schema.validate(raw)
        self.assertEqual(
            effective["endpoint"]["merge"]["approval_channels"],
            ["operator-cli"])

    def test_approval_channels_unknown_member_rejected(self):
        def mutate(raw):
            raw["endpoint"]["merge"]["approval_channels"] = [
                "telegram", "sms"]
        self.assertIn("endpoint.merge.approval_channels[1]",
                      paths(problems_of(mutate)))

    def test_approval_channels_empty_rejected(self):
        def mutate(raw):
            raw["endpoint"]["merge"]["approval_channels"] = []
        problems = problems_of(mutate)
        self.assertTrue(any(
            p["path"].startswith("endpoint.merge.approval_channels")
            for p in problems))


class RetentionSecretTests(unittest.TestCase):
    """A5 — CFG07/CFG08."""

    def test_retention_defaults(self):
        raw = load_valid()
        del raw["evidence"]["worker_log_retention_days"]
        del raw["evidence"]["audit_retention_days"]
        effective = schema.validate(raw)
        self.assertEqual(
            effective["evidence"]["worker_log_retention_days"], 7)
        self.assertEqual(effective["evidence"]["audit_retention_days"], 30)

    def test_retention_floors_cannot_weaken(self):
        """7-day worker logs / 30-day audit are floors, not suggestions."""
        def mutate(raw):
            raw["evidence"]["worker_log_retention_days"] = 3
            raw["evidence"]["audit_retention_days"] = 14
        problems = problems_of(mutate)
        self.assertIn("evidence.worker_log_retention_days",
                      paths(problems))
        self.assertIn("evidence.audit_retention_days", paths(problems))

    def test_tombstones_must_survive_until_removal(self):
        def mutate(raw):
            raw["evidence"]["tombstones"][
                "retain_until_registration_removal"] = False
        self.assertIn(
            "evidence.tombstones.retain_until_registration_removal",
            paths(problems_of(mutate)))

    def test_required_redactions_cannot_shrink(self):
        def mutate(raw):
            raw["evidence"]["export"]["redact"] = ["tokens"]
        self.assertIn("evidence.export.redact",
                      paths(problems_of(mutate)))

    def test_approved_secret_references_accepted(self):
        raw = load_valid()
        for ref in ("env:SOME_VAR", "hermes-secrets:kit/token",
                    "bw:item-id-1", "op:vault/item/field",
                    "vault:secret/data/key"):
            raw["secrets"]["k_" + ref.split(":")[0]] = ref
        self.assertIsInstance(schema.validate(raw), dict)

    def test_literal_secret_canaries_fail(self):
        """Built programmatically — no real-looking token is committed."""
        canaries = [
            "ghp_" + "a" * 36,
            "sk-" + "b" * 24,
            "AKIA" + "C" * 16,
            "-----BEGIN" + " RSA PRIVATE KEY-----",
            "f" * 64,                       # raw hex blob
            "QUJD" * 16 + "==",             # long base64 blob
        ]
        for canary in canaries:
            def mutate(raw, value=canary):
                raw["secrets"]["leaked"] = value
            problems = problems_of(mutate)
            self.assertIn("secrets.leaked", paths(problems), canary[:12])
            self.assertTrue(
                any("literal secret" in p["message"] for p in problems),
                f"canary {canary[:16]}… must be named a literal secret")

    def test_non_reference_secret_fails(self):
        def mutate(raw):
            raw["secrets"]["token"] = "not-a-secret-ref"
        problems = problems_of(mutate)
        self.assertIn("secrets.token", paths(problems))
        self.assertTrue(any("approved secret-storage" in p["message"]
                            for p in problems))


class ParserContractTests(unittest.TestCase):
    """The restricted-YAML subset itself is part of the contract."""

    def test_unsupported_syntax_raises_not_misparses(self):
        for bad in ("a: &anchor 1\nb: *anchor\n",
                    "a: |\n  multiline\n  scalar\n",
                    "a: {b: 1}\n",
                    "- key: value\n"):
            with self.assertRaises(yamlmini.ParseError, msg=bad):
                yamlmini.load(bad)

    def test_comments_and_inline_lists(self):
        data = yamlmini.load(
            "# comment\na: 1 # trailing\nb: [x, y]\nc: 'it''s'\n")
        self.assertEqual(data, {"a": 1, "b": ["x", "y"], "c": "it's"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
