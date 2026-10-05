#!/usr/bin/env python3
"""External-pilot consent/admission gate (issue #28 / Task 4.4).

Acceptance mapping:

- A1 — the obligations record (applicable obligations, controller/
  processor responsibility, model-provider handling, minimization,
  retention, export/delete terms, approved consent/admission process)
  is durable and required *before* any participant consent can stand;
  every term field is required.
- A2 — opening collection requires recorded consent plus an accepted
  supported workload/trust classification; absent or revoked consent
  blocks new collection and the aggregate-export gate; revocation is
  durable, never a silent delete.
- A4 — denied admissions name the readiness blocker and stay as audit
  rows; ``contributor-code`` requires boundary evidence that
  ``verify_boundary`` accepts — worktrees, prompt rules and branch
  protection are never evidence.
- A5 — the event trail carries the participant *hash*, never the raw
  reference, and preview admission verifies the supported scoped
  provider plus a smoke contract without broadening v1.0 authority.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from factory_kit.durable.store import (  # noqa: E402
    IntakeStore, IntakeStoreError, _participant_hash)
from factory_kit.privacy import boundary, consent  # noqa: E402

SCOPE = "pilot-collection"
ACTOR = "tg:owner-1"
PARTICIPANT = "gh:acme/service"
AUTHORITY = "acme/service"


def _terms():
    return dict(
        obligations="pilot obligations per docs/pilot/",
        controller_ref="repo-owner",
        processor_ref="factory-kit",
        provider_handling="codex via scoped token",
        minimization_terms="aggregate fields only",
        retention_terms="detail 30d, tombstone kept",
        export_terms="minimized aggregate only",
        delete_terms="explicit removal keeps tombstone",
        process_ref="docs/pilot/consent-admission.md")


def _verified_boundary():
    """A boundary evidence document shaped exactly like a real
    ``run_probes`` verdict — every probe denied under a real
    mechanism."""
    return {"mechanism": "seatbelt", "outcome": "verified",
            "blocker": None,
            "probes": {n: {"result": "denied", "detail": "EPERM"}
                       for n in boundary.PROBE_NAMES},
            "credentials": {"unrestricted_absent": True,
                            "granted": ["PATH"], "rejected": []},
            "workspace": "/tmp/ws", "verified_at": "now"}


def _preview(provider="vercel", smoke=True):
    p = {"provider": provider}
    if smoke:
        p["smoke"] = {"command": "curl -sf $URL",
                      "expect": "exit 0"}
    return p


class ConsentFixture(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = IntakeStore(Path(self.tmp.name) / "i.db")

    def _obligations(self, scope=SCOPE):
        return consent.record_obligations(
            self.store, scope, actor_ref=ACTOR, **_terms())

    def _events(self, kind):
        return self.store.event_rows(kind)


# ---------------------------------------------------------------------------
# A1 — obligations before collection
# ---------------------------------------------------------------------------

class TestObligations(ConsentFixture):

    def test_obligations_record_durable_with_event(self):
        out = self._obligations()
        self.assertEqual(out["outcome"], "recorded")
        self.assertTrue(out["terms_digest"].startswith("sha256:"))
        row = self.store.pilot_obligations(SCOPE)
        self.assertEqual(row["controller_ref"], "repo-owner")
        self.assertEqual(row["process_ref"],
                         "docs/pilot/consent-admission.md")
        self.assertEqual(row["terms_digest"], out["terms_digest"])
        ev = self._events("pilot_obligations_recorded")
        self.assertEqual(len(ev), 1)
        detail = json.loads(ev[0]["detail"])
        self.assertEqual(detail["scope"], SCOPE)

    def test_every_term_required(self):
        for missing in ("obligations", "controller_ref",
                        "processor_ref", "provider_handling",
                        "minimization_terms", "retention_terms",
                        "export_terms", "delete_terms", "process_ref"):
            fields = _terms()
            fields[missing] = ""
            with self.assertRaises(IntakeStoreError):
                consent.record_obligations(
                    self.store, SCOPE, actor_ref=ACTOR, **fields)

    def test_terms_fields_bounded_not_content(self):
        fields = _terms()
        fields["retention_terms"] = "x" * 1000
        with self.assertRaises(IntakeStoreError):
            consent.record_obligations(
                self.store, SCOPE, actor_ref=ACTOR, **fields)


# ---------------------------------------------------------------------------
# A2 — consent + supported classification before collection opens
# ---------------------------------------------------------------------------

class TestAdmission(ConsentFixture):

    def test_admission_denied_without_obligations(self):
        """Consent taken ahead of the obligations record is not
        consent — the denial and its blocker are durable (A1/A4)."""
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            authority_key=AUTHORITY, workload_class="observation",
            trust_class="external-reviewed")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["blocker"], "obligations-missing")
        row = self.store.pilot_participant(PARTICIPANT)
        self.assertEqual(row["state"], "denied")
        self.assertEqual(row["blocker"], "obligations-missing")
        ev = self._events("pilot_admission_denied")
        self.assertEqual(len(ev), 1)

    def test_observation_participant_admitted(self):
        self._obligations()
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            authority_key=AUTHORITY, workload_class="observation",
            trust_class="external-reviewed")
        self.assertEqual(out["outcome"], "admitted")
        row = self.store.pilot_participant(PARTICIPANT)
        self.assertEqual(row["state"], "consented")
        self.assertEqual(row["authority_key"], AUTHORITY)
        check = consent.open_collection(self.store, PARTICIPANT)
        self.assertEqual(check["outcome"], "open")
        self.assertEqual(check["workload_class"], "observation")

    def test_consent_event_carries_hash_not_ref(self):
        """A5: the aggregate-visible trail must never name the
        participant — the durable row keeps the reference."""
        self._obligations()
        consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="observation",
            trust_class="external-reviewed")
        ev = self._events("pilot_consent_recorded")
        self.assertEqual(len(ev), 1)
        self.assertNotIn(PARTICIPANT, ev[0]["detail"])
        detail = json.loads(ev[0]["detail"])
        self.assertEqual(detail["participant_hash"],
                         _participant_hash(PARTICIPANT))

    def test_open_collection_denied_without_consent(self):
        check = consent.open_collection(self.store, "gh:nobody/repo")
        self.assertEqual(check["outcome"], "denied")
        self.assertEqual(check["reason"], "consent-missing")

    def test_open_collection_denied_after_denial(self):
        self._obligations()
        consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="observation", trust_class="internal")
        check = consent.open_collection(self.store, PARTICIPANT)
        self.assertEqual(check["outcome"], "denied")
        self.assertTrue(check["reason"].startswith("admission-denied:"))

    def test_revoked_consent_blocks_collection(self):
        self._obligations()
        consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="observation",
            trust_class="external-reviewed")
        out = consent.revoke_consent(self.store, PARTICIPANT,
                                   actor_ref=ACTOR)
        self.assertEqual(out["outcome"], "revoked")
        row = self.store.pilot_participant(PARTICIPANT)
        self.assertEqual(row["state"], "revoked")
        self.assertIsNotNone(row["revoked_at"])
        check = consent.open_collection(self.store, PARTICIPANT)
        self.assertEqual(check["outcome"], "denied")
        self.assertEqual(check["reason"], "consent-revoked")
        self.assertEqual(len(self._events("pilot_consent_revoked")), 1)

    def test_revoke_is_idempotent_and_unknown_fails(self):
        self._obligations()
        consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="observation",
            trust_class="external-reviewed")
        consent.revoke_consent(self.store, PARTICIPANT, actor_ref=ACTOR)
        out = consent.revoke_consent(self.store, PARTICIPANT,
                                   actor_ref=ACTOR)
        self.assertEqual(out["outcome"], "already-revoked")
        with self.assertRaises(IntakeStoreError):
            consent.revoke_consent(self.store, "gh:ghost/x",
                                   actor_ref=ACTOR)

    def test_readmission_after_revoke(self):
        """Consent can be re-given — a fresh admission row flips the
        state back; the earlier revoke event stays in the trail."""
        self._obligations()
        consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="observation",
            trust_class="external-reviewed")
        consent.revoke_consent(self.store, PARTICIPANT, actor_ref=ACTOR)
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="observation",
            trust_class="external-reviewed")
        self.assertEqual(out["outcome"], "admitted")
        self.assertEqual(
            consent.open_collection(self.store, PARTICIPANT)["outcome"],
            "open")

    def test_open_rechecks_obligations_and_classification(self):
        """Consent recorded under terms that no longer exist does not
        silently reopen collection (A2)."""
        self._obligations()
        consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="observation",
            trust_class="external-reviewed")
        self.store._q("DELETE FROM pilot_obligations WHERE scope=?",
                      (SCOPE,))
        check = consent.open_collection(self.store, PARTICIPANT)
        self.assertEqual(check["reason"], "obligations-missing")
        # A stale vocabulary value can never reopen either.
        self._obligations()
        self.store._q(
            "UPDATE pilot_participants SET workload_class='rogue'"
            " WHERE participant_ref=?", (PARTICIPANT,))
        check = consent.open_collection(self.store, PARTICIPANT)
        self.assertTrue(
            check["reason"].startswith("unsupported-workload:"))


# ---------------------------------------------------------------------------
# A3/A4 — classification vocabulary + boundary evidence at admission
# ---------------------------------------------------------------------------

class TestAdmissionClassification(ConsentFixture):

    def setUp(self):
        super().setUp()
        self._obligations()

    def test_unsupported_workload_denied_and_named(self):
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="firmware-flash",
            trust_class="external-reviewed")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["blocker"],
                         "unsupported-workload:firmware-flash")
        row = self.store.pilot_participant(PARTICIPANT)
        self.assertEqual(row["state"], "denied")
        self.assertEqual(row["blocker"],
                         "unsupported-workload:firmware-flash")

    def test_internal_trust_is_not_an_external_classification(self):
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="observation", trust_class="internal")
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["blocker"], "unsupported-trust:internal")

    def test_contributor_code_requires_boundary_evidence(self):
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="contributor-code",
            trust_class="external-untrusted")
        self.assertEqual(out["outcome"], "denied")
        self.assertTrue(out["blocker"].startswith(
            "boundary-unverified:"))

    def test_worktree_is_never_isolation_evidence(self):
        """A4: a worktree/prompt-rule/branch-protection claim is denied
        even when the evidence document asserts ``verified``."""
        for fake in boundary.NON_ISOLATION:
            ev = {"mechanism": fake, "outcome": "verified",
                  "probes": {n: {"result": "denied"}
                             for n in boundary.PROBE_NAMES},
                  "credentials": {"unrestricted_absent": True}}
            out = consent.admit_participant(
                self.store, PARTICIPANT, actor_ref=ACTOR,
                workload_class="contributor-code",
                trust_class="external-untrusted", boundary=ev,
                preview=_preview())
            self.assertEqual(out["outcome"], "denied")
            self.assertIn(f"non-isolation:{fake}", out["blocker"])

    def test_contributor_code_admitted_with_verified_boundary(self):
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            authority_key=AUTHORITY,
            workload_class="contributor-code",
            trust_class="external-untrusted",
            boundary=_verified_boundary(), preview=_preview())
        self.assertEqual(out["outcome"], "admitted")
        row = self.store.pilot_participant(PARTICIPANT)
        self.assertEqual(row["state"], "consented")
        self.assertTrue(row["boundary_evidence"].startswith(
            "seatbelt:sha256:"))
        self.assertEqual(
            consent.open_collection(self.store, PARTICIPANT)["outcome"],
            "open")

    def test_contributor_code_requires_preview_contract(self):
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="contributor-code",
            trust_class="external-untrusted",
            boundary=_verified_boundary())
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["blocker"], "preview-contract-missing")

    def test_preview_must_be_supported_scoped_provider(self):
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="contributor-code",
            trust_class="external-untrusted",
            boundary=_verified_boundary(),
            preview=_preview(provider="netlify"))
        self.assertEqual(out["outcome"], "denied")
        self.assertEqual(out["blocker"],
                         "provider-unsupported:netlify")
        out = consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            workload_class="contributor-code",
            trust_class="external-untrusted",
            boundary=_verified_boundary(),
            preview=_preview(smoke=False))
        self.assertEqual(out["blocker"], "preview-contract-missing")


# ---------------------------------------------------------------------------
# Export gate + status
# ---------------------------------------------------------------------------

class TestExportGate(ConsentFixture):

    def test_export_gate_denies_missing_and_revoked_agreement(self):
        gate = consent.export_gate(self.store)
        self.assertEqual(gate["outcome"], "denied")
        self.assertEqual(gate["reason"],
                         "participant-agreement-missing")
        self.store.record_participant_agreement(
            "pilot-export", actor_ref=ACTOR)
        self.assertEqual(
            consent.export_gate(self.store)["outcome"], "open")
        self.store.revoke_participant_agreement(
            "pilot-export", actor_ref=ACTOR)
        gate = consent.export_gate(self.store)
        self.assertEqual(gate["outcome"], "denied")
        self.assertEqual(gate["reason"],
                         "participant-agreement-revoked")
        self.assertEqual(
            len(self._events("participant_agreement_revoked")), 1)

    def test_excluded_authorities_track_consent_state(self):
        self._obligations()
        consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            authority_key=AUTHORITY, workload_class="observation",
            trust_class="external-reviewed")
        self.assertEqual(
            consent.export_excluded_authorities(self.store), set())
        consent.revoke_consent(self.store, PARTICIPANT,
                             actor_ref=ACTOR)
        self.assertEqual(
            consent.export_excluded_authorities(self.store),
            {AUTHORITY})

    def test_revoke_unknown_scope_agreement_fails(self):
        with self.assertRaises(IntakeStoreError):
            self.store.revoke_participant_agreement(
                "never-agreed", actor_ref=ACTOR)

    def test_admission_status_summarizes(self):
        self._obligations()
        consent.admit_participant(
            self.store, PARTICIPANT, actor_ref=ACTOR,
            authority_key=AUTHORITY, workload_class="observation",
            trust_class="external-reviewed")
        consent.admit_participant(
            self.store, "gh:other/x", actor_ref=ACTOR,
            workload_class="rogue", trust_class="external-reviewed")
        st = consent.admission_status(self.store)
        self.assertIsNotNone(st["obligations"])
        self.assertEqual(st["counts"]["consented"], 1)
        self.assertEqual(st["counts"]["denied"], 1)


if __name__ == "__main__":
    unittest.main()
