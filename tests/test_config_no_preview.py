#!/usr/bin/env python3
"""``endpoint.preview.provider: none`` — schema, merge guard and port.

The schema accepts a *bare* declared no-preview contract and nothing
looser; :func:`schema.preview_required` fails closed; the pure merge
guard skips preview/smoke evidence only when the bound config says
``none`` **and** the request binds :data:`schema.NO_PREVIEW_BINDING`
**and** no preview row exists; :class:`NullPreview` refuses every
deployment action instead of pretending to succeed.
"""

import copy
import re
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.config import schema, yamlmini  # noqa: E402
from factory_kit.merge.guard import evaluate_merge_guard  # noqa: E402
from factory_kit.preview.port import NullPreview, PreviewError  # noqa: E402

REFERENCE = ROOT / "docs" / "examples" / "reference.factory-kit.yml"
PREVIEW_BLOCK = re.compile(r"  preview:\n(?:    .*\n)+")


def _manifest(preview_yaml):
    text = REFERENCE.read_text(encoding="utf-8")
    text, n = PREVIEW_BLOCK.subn(preview_yaml, text, count=1)
    assert n == 1
    return yamlmini.load(text)


def _problems(raw):
    try:
        schema.validate(raw)
    except schema.ConfigError as exc:
        return str(exc)
    return None


class TestSchemaNoPreview(unittest.TestCase):

    def test_bare_none_is_accepted_and_effective_is_minimal(self):
        eff = schema.validate(_manifest("  preview:\n    provider: none\n"))
        self.assertEqual(eff["endpoint"]["preview"], {"provider": "none"})
        self.assertFalse(schema.preview_required(eff))

    def test_none_with_preview_keys_is_rejected(self):
        for extra in ("    ttl_hours: 24\n",
                      "    smoke:\n      command: \"curl {url}\"\n"
                      "      expect: \"200\"\n",
                      "    visibility: public\n"):
            with self.subTest(extra=extra.strip()):
                err = _problems(_manifest(
                    "  preview:\n    provider: none\n" + extra))
                self.assertIsNotNone(err)
                self.assertIn("provider 'none' declares no preview", err)

    def test_unknown_provider_still_rejected(self):
        err = _problems(_manifest(
            "  preview:\n    provider: netlify\n    smoke:\n"
            "      command: \"curl {url}\"\n      expect: \"200\"\n"))
        self.assertIn("unsupported provider", err)

    def test_preview_required_fails_closed(self):
        self.assertTrue(schema.preview_required(None))
        self.assertTrue(schema.preview_required({}))
        self.assertTrue(schema.preview_required({"endpoint": {}}))
        self.assertTrue(schema.preview_required(
            schema.load_manifest_file(REFERENCE)))

    def test_reference_digest_unaffected_by_new_branch(self):
        # The provider-specific effective shape is unchanged, so
        # existing registrations keep their digests.
        eff = schema.load_manifest_file(REFERENCE)
        self.assertEqual(
            set(eff["endpoint"]["preview"]),
            {"provider", "environment", "visibility", "ttl_hours",
             "cleanup_minutes", "smoke"})

    def test_none_choice_changes_both_digests(self):
        vercel = schema.load_manifest_file(REFERENCE)
        none = schema.validate(_manifest("  preview:\n    provider: none\n"))
        self.assertNotEqual(schema.effective_digest(vercel),
                            schema.effective_digest(none))
        self.assertNotEqual(schema.policy_digest(vercel),
                            schema.policy_digest(none))


class TestMergeGuardNoPreview(unittest.TestCase):
    """Only the preview/smoke blockers are asserted — the other inputs
    are deliberately absent, so unrelated blockers are expected."""

    PREVIEW_BLOCKERS = {"preview-moved", "preview-not-verified",
                        "preview-unexpected", "preview-contract-mismatch",
                        "smoke-unobserved", "smoke-failed", "smoke-stale"}

    def setUp(self):
        self.none_eff = schema.validate(
            _manifest("  preview:\n    provider: none\n"))
        self.vercel_eff = schema.load_manifest_file(REFERENCE)
        self.now = time.time()

    def _guard(self, effective, request, preview=None):
        req = dict({"expires_epoch": self.now + 600,
                    "config_digest": schema.effective_digest(effective),
                    "policy_digest": schema.policy_digest(effective),
                    "merge_method": "squash"}, **request)
        out = evaluate_merge_guard(
            request=req, work={"state": "active"}, effective=effective,
            fence=None, pause=None,
            evidence={"status": "verified", "review_id": "rv-1"},
            preview=preview, pr=None, base_sha=None, runs=[],
            protection=None, capabilities=None, actor_login=None,
            now=self.now, max_smoke_age_s=600)
        return set(out.get("blockers", [])) & self.PREVIEW_BLOCKERS

    def test_bound_none_request_has_no_preview_blockers(self):
        self.assertEqual(self._guard(self.none_eff, {
            "preview_id": None,
            "preview_digest": schema.NO_PREVIEW_BINDING}), set())

    def test_none_config_with_preview_row_blocks(self):
        self.assertEqual(self._guard(
            self.none_eff,
            {"preview_id": None,
             "preview_digest": schema.NO_PREVIEW_BINDING},
            preview={"preview_id": "prev-1", "state": "verified"}),
            {"preview-unexpected"})

    def test_none_config_with_foreign_binding_blocks(self):
        self.assertEqual(self._guard(self.none_eff, {
            "preview_id": "prev-1", "preview_digest": "abc"}),
            {"preview-contract-mismatch"})
        self.assertEqual(self._guard(self.none_eff, {
            "preview_id": None, "preview_digest": None}),
            {"preview-contract-mismatch"})

    def test_required_config_never_accepts_missing_preview(self):
        blockers = self._guard(self.vercel_eff, {
            "preview_id": None,
            "preview_digest": schema.NO_PREVIEW_BINDING})
        self.assertIn("preview-moved", blockers)

    def test_missing_config_fails_closed(self):
        out = evaluate_merge_guard(
            request={"expires_epoch": self.now + 600, "preview_id": None,
                     "preview_digest": schema.NO_PREVIEW_BINDING},
            work={"state": "active"}, effective=None, fence=None,
            pause=None, evidence=None, preview=None, pr=None,
            base_sha=None, runs=[], protection=None, capabilities=None,
            now=self.now, max_smoke_age_s=600)
        self.assertIn("stale-config", out["blockers"])
        self.assertIn("preview-moved", out["blockers"])


class TestNullPreview(unittest.TestCase):

    def test_refuses_every_deployment_action(self):
        port = NullPreview()
        self.assertEqual(port.provider_name(), "none")
        self.assertEqual(port.find_deployments({"work_key": "w"}), [])
        for call in (lambda: port.deploy({}, {}),
                     lambda: port.inspect("d-1"),
                     lambda: port.smoke("d-1", {}),
                     lambda: port.remove("d-1", {})):
            with self.assertRaises(PreviewError):
                call()


if __name__ == "__main__":
    unittest.main()
