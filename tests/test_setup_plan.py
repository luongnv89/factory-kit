#!/usr/bin/env python3
"""Unit tests for factory_kit.setup.plan — reviewable inspection (A1).

Maps to issue #7 acceptance criteria:

- A1: on the dirty fixture repo (CI + .gitissue.yml + dirty/untracked
  developer files), inspection produces a reviewable inventory/diff of
  factory-owned additions and explicit integration edits, with zero local
  or remote mutation before operator acceptance.
- A4 (plan half): competing automation fixtures are named findings and
  the CI/IDD conventions are explicitly mapped — and nothing in the entry
  vocabulary can install a scheduler or auto-merge loop.
- A5: every proposed byte is inline in the plan and canary-scanned.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_setup_plan.py
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "setup_repo.py"

sys.path.insert(0, str(ROOT))
from factory_kit.config import schema  # noqa: E402
from factory_kit.setup import plan as plan_mod  # noqa: E402

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)


class InspectFixtureTests(unittest.TestCase):
    """A1 — reviewable inventory/diff, zero mutation before acceptance."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = fx.build_repo(Path(self.tmp.name) / "repo")
        self.manifest = Path(self.tmp.name) / "candidate.factory-kit.yml"
        self.manifest.write_text(fx.manifest_text(), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_has_reviewable_entries(self):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        self.assertTrue(plan["appliable"], plan["conflicts"])
        kinds = {e["kind"] for e in plan["entries"]}
        self.assertEqual(
            kinds,
            {"configuration", "webhook", "registration", "dependency"},
            "one entry per A2 category")
        config = next(e for e in plan["entries"]
                      if e["kind"] == "configuration")
        self.assertEqual(config["action"], "create")
        self.assertEqual(config["path"], ".factory-kit.yml")
        self.assertIn("factory_kit: 1", config["payload"],
                      "the proposed bytes are reviewable inline")
        self.assertEqual(len(config["sha256"]), 64)

    def test_inspection_mutates_nothing(self):
        before = fx.snapshot(self.repo)
        status_before = __import__("subprocess").run(
            ["git", "-C", str(self.repo), "status", "--porcelain=v1"],
            capture_output=True, text=True).stdout
        plan_mod.inspect(self.repo, manifest_source=self.manifest)
        self.assertEqual(before, fx.snapshot(self.repo),
                         "inspection must not change a single byte")
        status_after = __import__("subprocess").run(
            ["git", "-C", str(self.repo), "status", "--porcelain=v1"],
            capture_output=True, text=True).stdout
        self.assertEqual(status_before, status_after,
                         "no stash/reset/clean side effects on the tree")

    def test_preserved_inventory_covers_developer_work(self):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        preserved = {p["path"] for p in plan["preserved"]}
        self.assertIn("app.py", preserved)
        self.assertIn("scratch-notes.txt", preserved)
        self.assertIn(".gitissue.yml", preserved)
        self.assertIn(".github/workflows/ci.yml", preserved)
        work = plan["inventory"]["developer_work"]
        self.assertTrue(any(d["path"] == "app.py"
                            for d in work["dirty"]))
        self.assertTrue(any(u["path"] == "scratch-notes.txt"
                            for u in work["untracked"]))

    def test_idd_conventions_mapped_never_written(self):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        mapping = plan["inventory"]["integration_mapping"]
        self.assertIn("resolve", mapping.get("idd_owned", {}),
                      "existing .gitissue.yml groups map to IDD ownership")
        edits = [e for e in plan["entries"]
                 if e["kind"] == "integration-edit"]
        self.assertEqual(edits, [],
                         "the recipe proposes no edits to existing "
                         "files — CI stays authoritative, .gitissue.yml "
                         "is never written")
        self.assertIn(".github/workflows/ci.yml",
                      plan["inventory"]["ci_workflows"])

    def test_no_scheduler_or_automerge_entries(self):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        self.assertEqual(plan["scheduler_entries"], [])
        for entry in plan["entries"]:
            self.assertNotIn(entry["kind"],
                             ("scheduler", "auto-merge", "cron"),
                             "A4 — no independent backlog scheduler or "
                             "implicit auto-merge loop can be installed")

    def test_competing_automation_is_named_finding(self):
        fx.build_repo(Path(self.tmp.name) / "repo2",
                      automation=(".mergify.yml",))
        plan = plan_mod.inspect(Path(self.tmp.name) / "repo2",
                                manifest_source=self.manifest)
        self.assertIn(".mergify.yml",
                      plan["inventory"]["automation_files"])
        sources = {f["source"] for f in plan["findings"]}
        self.assertIn("competing-automation", sources)

    def test_invalid_manifest_makes_plan_not_appliable(self):
        bad = Path(self.tmp.name) / "bad.yml"
        bad.write_text("factory_kit: 99\n", encoding="utf-8")
        plan = plan_mod.inspect(self.repo, manifest_source=bad)
        self.assertFalse(plan["appliable"])
        self.assertTrue(any(c["source"] == "manifest-validation"
                            for c in plan["conflicts"]))
        with self.assertRaises(plan_mod.PlanError):
            plan_mod.accept(plan, "operator")

    def test_secret_canary_blocks_acceptance(self):
        canary_manifest = Path(self.tmp.name) / "canary.yml"
        # A literal secret inside an otherwise-valid manifest — schema
        # validation itself rejects it, and the plan records the canary
        # class of failure either way (A5).
        text = fx.manifest_text().replace(
            'telegram_users: [12345]',
            'telegram_users: [12345]\nsecrets:\n  k: "ghp_' + "A" * 40 + '"')
        canary_manifest.write_text(text, encoding="utf-8")
        try:
            schema.load_manifest(text)
            schema_rejected = False
        except schema.ConfigError:
            schema_rejected = True
        plan = plan_mod.inspect(self.repo,
                                manifest_source=canary_manifest)
        self.assertFalse(plan["appliable"])
        self.assertTrue(schema_rejected or
                        not plan["secret_hygiene"]["clean"])

    def test_precedence_conflict_blocks_plan(self):
        """A .gitissue.yml carrying factory-owned keys is an explicit
        conflict — review sees it, apply never runs on it."""
        self.repo.joinpath(".gitissue.yml").write_text(
            "platform: github\nidentity:\n  repo_id: \"X\"\n",
            encoding="utf-8")
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        self.assertFalse(plan["appliable"])
        self.assertTrue(any(c["source"] == "precedence"
                            for c in plan["conflicts"]))

    def test_accept_binds_plan_digest(self):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        accepted = plan_mod.accept(plan, "operator-1")
        self.assertEqual(accepted["acceptance"]["plan_digest"],
                         plan["plan_digest"])
        self.assertEqual(accepted["acceptance"]["accepted_by"],
                         "operator-1")
        # Mutating the plan invalidates the binding — apply checks it.
        accepted["entries"][0]["payload"] = "tampered: true\n"
        self.assertNotEqual(plan_mod.plan_digest(accepted),
                            accepted["acceptance"]["plan_digest"])

    def test_plan_json_roundtrips_for_review_file(self):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        blob = json.dumps(plan)
        self.assertIn('"plan_digest"', blob,
                      "the plan file the operator reviews is JSON")


class ManifestFromRepoTests(unittest.TestCase):
    """Plan picks up a repo's own .factory-kit.yml as the candidate."""

    def test_existing_manifest_defaults_to_source(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = fx.build_repo(Path(tmp.name) / "repo")
        (repo / ".factory-kit.yml").write_text(fx.manifest_text(),
                                               encoding="utf-8")
        plan = plan_mod.inspect(repo)
        config = next(e for e in plan["entries"]
                      if e["kind"] == "configuration")
        self.assertEqual(config["action"], "keep",
                         "identical bytes already in place")

    def test_drifted_manifest_is_reviewable_replace_with_diff(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = fx.build_repo(Path(tmp.name) / "repo")
        (repo / ".factory-kit.yml").write_text(
            "factory_kit: 1\n# old content\n", encoding="utf-8")
        candidate = Path(tmp.name) / "candidate.yml"
        candidate.write_text(fx.manifest_text(), encoding="utf-8")
        plan = plan_mod.inspect(repo, manifest_source=candidate)
        config = next(e for e in plan["entries"]
                      if e["kind"] == "configuration")
        self.assertEqual(config["action"], "replace")
        self.assertIn("diff", config)
        self.assertIn("-# old content", config["diff"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
