#!/usr/bin/env python3
"""``config.paths.profile_state_dir`` — the shared per-profile state
location (PRD §6.3).

Hermes's ``default`` profile home *is* ``~/.hermes``; materializing
``~/.hermes/profiles/default/`` would mint a phantom profile in
Hermes's namespace. The helper maps ``default`` → ``~/.hermes/
factory-kit`` and named profiles → ``~/.hermes/profiles/<p>/
factory-kit``, and both setup's CLI and the run driver resolve their
state files through it. These tests patch ``HERMES_HOME`` — nothing
touches the real ``~/.hermes``.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.config import paths  # noqa: E402


class TestProfileStateDir(unittest.TestCase):

    def test_default_profile_lives_at_hermes_home(self):
        self.assertEqual(
            paths.profile_state_dir("default"),
            paths.HERMES_HOME / "factory-kit")

    def test_named_profile_lives_under_profiles(self):
        self.assertEqual(
            paths.profile_state_dir("fk-ops"),
            paths.HERMES_HOME / "profiles" / "fk-ops" / "factory-kit")

    def test_default_never_enters_the_profiles_namespace(self):
        self.assertNotIn("profiles",
                         paths.profile_state_dir("default").parts)


class TestConsumers(unittest.TestCase):
    """Setup's CLI and the run driver resolve through the helper —
    with an explicit override still winning (``--state`` /
    ``--state-dir`` / ``--registrations``)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / ".hermes"

    def _patch(self):
        return mock.patch.object(paths, "HERMES_HOME", self.home)

    def test_setup_paths_follow_the_helper(self):
        from factory_kit.setup import cli as setup_cli
        args = type("A", (), {"profile": "default", "state": None,
                              "registrations": None,
                              "intake_db": None})()
        with self._patch():
            self.assertEqual(
                setup_cli._state_path(args),
                str(self.home / "factory-kit" / "setup-state.json"))
            self.assertEqual(
                setup_cli._registrations_path(args),
                str(self.home / "factory-kit" / "registrations.json"))
            # No intake.db exists → no store is opened, and nothing is
            # created under the (fake) Hermes home.
            self.assertIsNone(setup_cli._intake_store(args))
            self.assertFalse(self.home.exists())

    def test_setup_explicit_overrides_win(self):
        from factory_kit.setup import cli as setup_cli
        args = type("A", (), {"profile": "default",
                              "state": "/tmp/x/state.json",
                              "registrations": "/tmp/x/regs.json"})()
        with self._patch():
            self.assertEqual(setup_cli._state_path(args),
                             "/tmp/x/state.json")
            self.assertEqual(setup_cli._registrations_path(args),
                             "/tmp/x/regs.json")

    def test_driver_state_dir_follows_the_helper(self):
        from factory_kit.run import cli as run_cli
        args = type("A", (), {"profile": "fk-ops", "state_dir": None})()
        with self._patch():
            self.assertEqual(
                run_cli._state_dir(args),
                str(self.home / "profiles" / "fk-ops" / "factory-kit"))

    def test_driver_state_dir_override_wins(self):
        from factory_kit.run import cli as run_cli
        args = type("A", (), {"profile": "fk-ops",
                              "state_dir": "/tmp/explicit"})()
        with self._patch():
            self.assertEqual(run_cli._state_dir(args), "/tmp/explicit")


if __name__ == "__main__":
    unittest.main()
