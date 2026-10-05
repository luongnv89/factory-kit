#!/usr/bin/env python3
"""Unit tests for factory_kit.setup.apply — idempotent apply (A2).

Maps to issue #7 acceptance criteria:

- A2: applying an explicitly accepted plan preserves all unrelated bytes
  and developer edits without automatic stash/reset; ownership/checksums
  and one registration/webhook/configuration/dependency entry are
  recorded; a second run returns an empty effective diff and no
  duplicates.
- A1 (gate half): apply refuses any plan that was not explicitly
  accepted — the review gate is structural, not a flag.

Run:  python3 -m unittest discover -s tests -v
  or: python3 tests/test_setup_apply.py
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_MOD_PATH = ROOT / "tests" / "fixtures" / "setup_repo.py"

sys.path.insert(0, str(ROOT))
from factory_kit.config.registration import RegistrationStore  # noqa: E402
from factory_kit.setup import apply as apply_mod  # noqa: E402
from factory_kit.setup import plan as plan_mod  # noqa: E402
from factory_kit.setup.ownership import SetupStore  # noqa: E402

spec_fx = importlib.util.spec_from_file_location(
    "setup_repo", FIXTURE_MOD_PATH)
fx = importlib.util.module_from_spec(spec_fx)
spec_fx.loader.exec_module(fx)


class ApplyFixtureTests(unittest.TestCase):
    """A2 — apply once: preserved bytes, one entry of each kind."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = fx.build_repo(Path(self.tmp.name) / "repo")
        self.manifest = Path(self.tmp.name) / "candidate.yml"
        self.manifest.write_text(fx.manifest_text(), encoding="utf-8")
        self.store_path = Path(self.tmp.name) / "setup-state.json"
        self.reg_path = Path(self.tmp.name) / "registrations.json"

    def _accepted(self):
        plan = plan_mod.inspect(self.repo, manifest_source=self.manifest)
        return plan_mod.accept(plan, "operator-1"), plan

    def _apply(self, accepted, **kw):
        store = SetupStore(str(self.store_path))
        reg = RegistrationStore(str(self.reg_path))
        return apply_mod.apply_plan(
            accepted, self.repo, store,
            registration_store=reg,
            supported_versions=["factory-kit/0.1.0",
                               "manifest/1"], **kw)

    def test_apply_preserves_everything_and_writes_manifest(self):
        before = fx.snapshot(self.repo)
        accepted, plan = self._accepted()
        result = self._apply(accepted)
        self.assertEqual(result["outcome"], "applied", result)
        self.assertEqual(result["effective_diff"], [])
        after = fx.snapshot(self.repo)
        self.assertEqual(set(after) - set(before), {".factory-kit.yml"},
                         "exactly one factory-owned file appears")
        for path, sha in before.items():
            self.assertEqual(after[path], sha,
                             f"{path} changed — preservation violated")
        self.assertEqual(result["preserved"]["violations"], [])

    def test_one_entry_per_category_and_ownership_recorded(self):
        accepted, _ = self._accepted()
        self._apply(accepted)
        store = SetupStore(str(self.store_path))
        owned = store.owned_files()
        self.assertEqual(list(owned), [".factory-kit.yml"])
        self.assertEqual(len(owned[".factory-kit.yml"]["sha256"]), 64)
        effects = store.effects()
        self.assertIn("webhook:intake-events", effects)
        self.assertIn("dependency:approved-skill-pins", effects)
        reg = RegistrationStore(str(self.reg_path))
        record = reg.get("R_TEST0001")
        self.assertIsNotNone(record, "one registration row")
        self.assertEqual(record["readiness"]["verdict"], "pending",
                         "registration exists but enables nothing until "
                         "readiness passes (A6)")

    def test_second_apply_is_empty_diff_no_duplicates(self):
        accepted, _ = self._accepted()
        first = self._apply(accepted)
        second = self._apply(accepted)
        self.assertEqual(second["effective_diff"], [])
        self.assertEqual(second["applied"], [])
        self.assertEqual(second["outcome"], "idempotent")
        store = SetupStore(str(self.store_path))
        self.assertEqual(len(store.effects()), 2,
                         "webhook + dependency recorded exactly once")
        reg = RegistrationStore(str(self.reg_path))
        self.assertIsNotNone(reg.get("R_TEST0001"))
        events = store.events("setup_applied")
        self.assertEqual(len(events), 2)

    def test_unaccepted_plan_refused(self):
        _, plan = self._accepted()
        plan["acceptance"] = None
        with self.assertRaises(apply_mod.SetupError):
            self._apply(plan)
        accepted, _ = self._accepted()
        accepted["acceptance"]["plan_digest"] = "0" * 64
        with self.assertRaises(apply_mod.SetupError):
            self._apply(accepted)

    def test_non_appliable_plan_refused(self):
        bad = Path(self.tmp.name) / "bad.yml"
        bad.write_text("factory_kit: 99\n", encoding="utf-8")
        plan = plan_mod.inspect(self.repo, manifest_source=bad)
        plan["appliable"] = False
        plan["acceptance"] = {"accepted_by": "x",
                              "plan_digest": plan["plan_digest"]}
        with self.assertRaises(apply_mod.SetupError):
            self._apply(plan)

    def test_drifted_target_is_conflict_not_overwrite(self):
        accepted, _ = self._accepted()
        # Apply once, then edit the owned file as a developer would.
        self._apply(accepted)
        (self.repo / ".factory-kit.yml").write_text(
            "# developer edits to the manifest\nfactory_kit: 1\n",
            encoding="utf-8")
        result = self._apply(accepted)
        statuses = {r["id"]: r["status"] for r in result["entries"]}
        self.assertEqual(statuses["configuration:.factory-kit.yml"],
                         "conflict",
                         "user edits to an owned file must conflict, "
                         "never be overwritten")
        self.assertIn("developer edits",
                      (self.repo / ".factory-kit.yml").read_text())

    def test_unexpected_existing_target_is_conflict(self):
        """A path the plan expects to *create* but that appeared since
        review holding other bytes is preserved, not overwritten."""
        accepted, _ = self._accepted()
        (self.repo / ".factory-kit.yml").write_text(
            "factory_kit: 1\n# unrelated file created after review\n",
            encoding="utf-8")
        result = self._apply(accepted)
        statuses = {r["id"]: r["status"] for r in result["entries"]}
        self.assertEqual(statuses["configuration:.factory-kit.yml"],
                         "conflict")
        self.assertIn("unrelated file created after review",
                      (self.repo / ".factory-kit.yml").read_text())

    def test_path_traversal_entry_refused(self):
        accepted, _ = self._accepted()
        accepted["entries"].append({
            "id": "configuration:evil", "kind": "configuration",
            "path": "../escape.txt", "action": "create",
            "sha256": "0" * 64, "payload": "x\n",
        })
        # Rebind the digests so the traversal guard itself (not the
        # digest mismatch) is what the test exercises.
        accepted["plan_digest"] = plan_mod.plan_digest(accepted)
        accepted["acceptance"]["plan_digest"] = accepted["plan_digest"]
        with self.assertRaises(apply_mod.SetupError):
            self._apply(accepted)

    def test_tampered_plan_digest_refused(self):
        accepted, _ = self._accepted()
        accepted["entries"][0]["payload"] = "tampered: true\n"
        with self.assertRaises(apply_mod.SetupError):
            self._apply(accepted)

    def test_appliable_flag_flip_still_refused(self):
        """``appliable`` is not digest-covered: apply must re-derive it
        from conflicts/manifest, so a hand-flipped flag on a conflicted
        plan cannot pass."""
        accepted, _ = self._accepted()
        accepted["conflicts"].append(
            {"source": "secret-canary", "detail": "simulated"})
        accepted["plan_digest"] = plan_mod.plan_digest(accepted)
        accepted["acceptance"]["plan_digest"] = accepted["plan_digest"]
        # ``appliable`` stays True in the dict despite the conflict.
        with self.assertRaises(apply_mod.SetupError):
            self._apply(accepted)

    def test_symlinked_parent_escape_refused(self):
        """A path whose *parent* is a symlink escaping the repo must be
        refused even though the leaf is not itself a link."""
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        os.symlink(outside, self.repo / "linkdir")
        accepted, _ = self._accepted()
        accepted["entries"].append({
            "id": "configuration:evil", "kind": "configuration",
            "path": "linkdir/escape.txt", "action": "create",
            "sha256": "0" * 64, "payload": "x\n",
        })
        accepted["plan_digest"] = plan_mod.plan_digest(accepted)
        accepted["acceptance"]["plan_digest"] = accepted["plan_digest"]
        with self.assertRaises(apply_mod.SetupError):
            self._apply(accepted)
        self.assertFalse((outside / "escape.txt").exists())

    def test_effects_sink_is_idempotent_and_recorded(self):
        accepted, _ = self._accepted()
        store = SetupStore(str(self.store_path))
        sink = apply_mod.RecordingEffectSink(store)
        entry = {"id": "webhook:intake-events", "kind": "webhook",
                 "action": "subscribe", "detail": {}}
        first = sink.apply(entry)
        second = sink.apply(entry)
        self.assertEqual(first["status"], "recorded-intent")
        self.assertEqual(second["status"], "existing")
        self.assertEqual(len(store.effects()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
