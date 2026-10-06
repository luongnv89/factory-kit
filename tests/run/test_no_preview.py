#!/usr/bin/env python3
"""A declared ``endpoint.preview.provider: none`` contract through the
live driver composition.

Same scripted world as ``test_driver`` — only the manifest's preview
block becomes ``provider: none``. The scripted preview port stays wired
so any code path that forgot the contract shows up as a recorded call.

Pinned directions:

- the full issue → verified → approval → merge walk completes with zero
  preview-port calls and a ``not-applicable`` preview stage;
- the approval request binds ``schema.NO_PREVIEW_BINDING`` (no preview
  id) and a repeated ask converges instead of minting a second grant;
- the presentation states *why* there is no preview;
- the choice is bound: flipping the registered config to a preview
  provider kills the request (``stale-config``);
- a preview row appearing under the ``none`` contract fails closed
  (``preview-unexpected``) at request time;
- the reverse regression — a preview-requiring config with no preview
  row — still denies ``preview-not-current``.
"""

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory_kit.config import schema

from . import test_driver as world
from .test_driver import GREEN, DriverWorld

NONE_PREVIEW = "  preview:\n    provider: none\n"


def _none_manifest(dest: Path) -> Path:
    text = world.MANIFEST.read_text(encoding="utf-8")
    text, n = re.subn(r"  preview:\n(?:    .*\n)+", NONE_PREVIEW, text,
                      count=1)
    assert n == 1, "reference manifest lost its endpoint.preview block"
    dest.write_text(text, encoding="utf-8")
    return dest


class NoPreviewWorld(DriverWorld):
    """DriverWorld over a ``provider: none`` manifest."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        manifest = _none_manifest(Path(tmp.name) / ".factory-kit.yml")
        with mock.patch.object(world, "MANIFEST", manifest):
            super().setUp()
        assert not schema.preview_required(self.eff)

    def _to_awaiting(self):
        work_key = self._accept(42)
        self.driver.tick()
        self.remote.checks[self._head(work_key)] = list(GREEN)
        report = self.driver.tick()
        req = self.services.approval.status(work_key)["live_request"]
        return work_key, req, report


class TestNoPreviewLifecycle(NoPreviewWorld):

    def test_merges_with_zero_preview_calls(self):
        work_key, req, report = self._to_awaiting()
        self.assertEqual(self.store.latest_evidence(work_key)["status"],
                         "verified")
        preview_stages = [s for s in report["stages"][work_key]
                          if s["stage"] == "preview"]
        self.assertEqual(preview_stages,
                         [{"stage": "preview", "outcome": "not-applicable",
                           "reason": "provider-none"}])
        self.assertIsNotNone(req)
        self.assertEqual(req["state"], "awaiting")
        self.assertIsNone(req["preview_id"])
        self.assertEqual(req["preview_digest"], schema.NO_PREVIEW_BINDING)

        dec = self.services.approval.decide_operator(
            request_id=req["request_id"], github_login="luongnv89",
            verified_login="luongnv89", verdict="approve", host="t")
        self.assertEqual(dec["outcome"], "approved")
        self.driver.tick()

        self.assertEqual(self.store.get_work(work_key)["state"],
                         "completed")
        intent = self.store.merge_intent_for_request(req["request_id"])
        self.assertEqual(intent["state"], "merged")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.store.preview_rows(work_key), [])

    def test_presentation_explains_missing_preview(self):
        _, req, _ = self._to_awaiting()
        text = self.services.approval._presentation(req)["text"]
        self.assertIn("endpoint.preview.provider: none", text)
        self.assertNotIn("smoke unobserved", text)

    def test_repeated_request_converges(self):
        work_key, req, _ = self._to_awaiting()
        again = self.services.approval.request_approval(work_key)
        self.assertEqual(again["outcome"], "converged")
        live = self.services.approval.status(work_key)["live_request"]
        self.assertEqual(live["request_id"], req["request_id"])


class TestNoPreviewBinding(NoPreviewWorld):

    def test_config_flip_to_preview_provider_is_stale(self):
        work_key, req, _ = self._to_awaiting()
        # Same config except the preview block — the only bound
        # difference left is the preview contract itself.
        flipped = schema.load_manifest_file(
            world.ROOT / "docs" / "examples" / "reference.factory-kit.yml")
        flipped["endpoint"]["merge"]["approval_channels"] = \
            list(self.eff["endpoint"]["merge"]["approval_channels"])
        self.assertTrue(schema.preview_required(flipped))
        self.assertEqual(
            {k: v for k, v in flipped.items() if k != "endpoint"},
            {k: v for k, v in self.eff.items() if k != "endpoint"})
        self.services.approval.configs[self.repo_id] = flipped
        dec = self.services.approval.decide_operator(
            request_id=req["request_id"], github_login="luongnv89",
            verified_login="luongnv89", verdict="approve", host="t")
        self.assertNotEqual(dec["outcome"], "approved")
        self.assertIn("stale-config", str(dec))

    def test_preview_row_under_none_fails_closed(self):
        work_key = self._accept(42)
        self.driver.tick()
        self.remote.checks[self._head(work_key)] = list(GREEN)
        # Verify without the driver's stage (f), then plant a row.
        with mock.patch.object(self.services.approval, "request_approval",
                               return_value={"outcome": "denied"}):
            self.driver.tick()
        head = self._head(work_key)
        with self.store.transact() as tx:
            tx.insert_preview(
                "prev-stray", work_key=work_key, provider="vercel",
                head_sha=head, state="verified")
        out = self.services.approval.request_approval(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "preview-unexpected")


class TestPreviewStillRequired(DriverWorld):
    """Regression guard: a preview-requiring contract never treats a
    missing preview row as ``none``."""

    def test_missing_preview_row_still_denies(self):
        self.assertTrue(schema.preview_required(self.eff))
        work_key = self._accept(42)
        self.driver.tick()
        self.remote.checks[self._head(work_key)] = list(GREEN)
        with mock.patch.object(self.services.preview, "deploy_preview",
                               return_value={"outcome": "denied"}):
            self.driver.tick()
        self.assertEqual(self.store.latest_evidence(work_key)["status"],
                         "verified")
        out = self.services.approval.request_approval(work_key)
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["reason"], "preview-not-current")


if __name__ == "__main__":
    unittest.main()
