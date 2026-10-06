#!/usr/bin/env python3
"""Migration/precedence/conflict fixtures (issue #6, A6 / CFG-C01).

``check`` proves the two files' ownership boundary: factory settings live
in .factory-kit.yml, IDD settings stay in .gitissue.yml, and a key claimed
by either side's namespace fails explaining the owner — never a silent
merge, never a factory write into .gitissue.yml.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_config_precedence.py
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from factory_kit.config import precedence, yamlmini  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "config"
MANIFEST = ROOT / "docs" / "examples" / "reference.factory-kit.yml"


class CoexistenceTests(unittest.TestCase):
    """A6 — a valid manifest plus a real .gitissue.yml maps cleanly."""

    def test_valid_pair_maps_without_conflicts(self):
        manifest = yamlmini.load_file(MANIFEST)
        gitissue = yamlmini.load_file(FIXTURES / "valid-gitissue.yml")
        report = precedence.check(manifest, gitissue)
        self.assertIn("identity", report["factory_owned"])
        self.assertIn("resolve", report["idd_owned"])
        self.assertIn("issue", report["idd_owned"])
        self.assertIn("security", report["idd_owned"])

    def test_gitissue_file_is_never_written(self):
        """The check is read-only — .gitissue.yml bytes never change."""
        path = FIXTURES / "valid-gitissue.yml"
        before = path.read_bytes()
        gitissue = yamlmini.load_file(path)
        snapshot = repr(sorted(gitissue.items()))
        precedence.check(yamlmini.load_file(MANIFEST), gitissue)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(repr(sorted(gitissue.items())), snapshot,
                         "the parsed config must not be mutated either")

    def test_mapping_report_documents_shared_concepts(self):
        report = precedence.mapping_report(
            yamlmini.load_file(MANIFEST), {"resolve.auto_test": True})
        self.assertIn("secrets", report["shared_concepts"])
        self.assertIn("factory", report["shared_concepts"]["secrets"])
        self.assertIn(".gitissue.yml", report["rule"])


class ConflictTests(unittest.TestCase):
    """A6 — each conflict class fails and names the owner."""

    def test_factory_key_in_gitissue_fails(self):
        manifest = yamlmini.load_file(MANIFEST)
        gitissue = yamlmini.load_file(FIXTURES / "conflict-gitissue.yml")
        with self.assertRaises(precedence.PrecedenceError) as ctx:
            precedence.check(manifest, gitissue)
        keys = {c["key"] for c in ctx.exception.conflicts}
        self.assertIn("identity.repo_id", keys)
        self.assertIn("endpoint.preview.provider", keys)
        for conflict in ctx.exception.conflicts:
            self.assertEqual(conflict["owner"], "factory")
            self.assertIn(".factory-kit.yml", conflict["message"])

    def test_idd_key_in_manifest_fails(self):
        manifest = yamlmini.load_file(MANIFEST)
        manifest["resolve"] = {"auto_test": False}
        with self.assertRaises(precedence.PrecedenceError) as ctx:
            precedence.check(manifest, {})
        conflict = ctx.exception.conflicts[0]
        self.assertEqual(conflict["key"], "resolve")
        self.assertEqual(conflict["owner"], "idd")
        self.assertIn(".gitissue.yml", conflict["message"])

    def test_security_namespace_cannot_be_claimed_by_manifest(self):
        """security.* is IDD's; a manifest twin would be a policy bypass."""
        manifest = yamlmini.load_file(MANIFEST)
        manifest["security"] = {"allow_pattern": ".*"}
        with self.assertRaises(precedence.PrecedenceError):
            precedence.check(manifest, {})

    def test_conflicting_values_fail_not_merge(self):
        """Two values for one concept is a conflict, never a silent pick."""
        manifest = yamlmini.load_file(MANIFEST)
        gitissue = yamlmini.load_file(FIXTURES / "valid-gitissue.yml")
        gitissue["limits"] = {"active_tasks": 9}
        with self.assertRaises(precedence.PrecedenceError) as ctx:
            precedence.check(manifest, gitissue)
        self.assertIn("limits.active_tasks",
                      {c["key"] for c in ctx.exception.conflicts})

    def test_every_conflict_explains_ownership(self):
        manifest = yamlmini.load_file(MANIFEST)
        gitissue = yamlmini.load_file(FIXTURES / "conflict-gitissue.yml")
        with self.assertRaises(precedence.PrecedenceError) as ctx:
            precedence.check(manifest, gitissue)
        for conflict in ctx.exception.conflicts:
            self.assertIn(conflict["owner"], ("factory", "idd"))
            self.assertIn("owned", conflict["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
