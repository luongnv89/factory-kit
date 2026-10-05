"""External-pilot consent/admission gate (Task 4.4 / §8.3, A1–A5).

Issue #28's gate before any external participant information, report,
code or installation telemetry is collected:

- **A1 — obligations first.** :func:`record_obligations` persists the
  durable record every collection scope needs *before* consent is
  meaningful: applicable obligations, controller/processor
  responsibility, model-provider handling, minimization, retention,
  export and deletion terms, and the approved consent/admission
  process. :func:`admit_participant` refuses every admission under a
  scope without one — ``obligations-missing``.
- **A2 — consent + supported classification.** A participant may open
  collection only when its durable row is ``consented`` under a
  supported workload/trust classification. Absent or revoked consent
  blocks new collection (:func:`open_collection`) and excludes the
  participant's authority from aggregate export
  (:func:`export_excluded_authorities`); a revoked scope-level
  participant agreement denies the export outright. Existing durable
  rows stay — the recorded procedure handles them, never a silent
  delete.
- **A3/A4 — boundary evidence.** ``contributor-code`` workloads must
  carry boundary evidence :func:`boundary.verify_boundary` accepts —
  independently executed hostile probes under a real isolation
  mechanism. Anything missing or failed denies the admission *and*
  names the readiness blocker in the durable row.
- **A5 — preview admission without new authority.** A declared preview
  path must target the supported scoped provider with a smoke contract
  (revision-bound evidence is then enforced by PreviewService at deploy
  time); nothing here broadens v1.0 authority.

The gate decides; the store records. Denials are durable rows with the
named blocker — an audit trail of refused admissions, not silent drops.
"""

from __future__ import annotations

import hashlib
import json as _json

from factory_kit.privacy import boundary as _boundary

#: The default scope external-pilot collection is admitted under.
PILOT_COLLECTION_SCOPE = "pilot-collection"

#: Workload classes the pilot supports. ``observation`` covers
#: participant info, reports and installation telemetry;
#: ``contributor-code`` covers external code/tests — the class that
#: additionally requires boundary evidence (A3).
SUPPORTED_WORKLOAD_CLASSES = ("observation", "contributor-code")

#: Trust classes admitted to the external pilot. Both are external;
#: ``internal`` is not a valid classification for an external
#: participant row.
SUPPORTED_TRUST_CLASSES = ("external-reviewed", "external-untrusted")

#: Workload classes that cannot be admitted without verified boundary
#: evidence (A3).
BOUNDARY_REQUIRED_WORKLOADS = ("contributor-code",)

#: Preview providers a workload may target — the scoped, token-env
#: provider PreviewService already enforces (A5; no authority is added).
SUPPORTED_PREVIEW_PROVIDERS = ("vercel",)

_TERMS_FIELDS = ("obligations", "controller_ref", "processor_ref",
                 "provider_handling", "minimization_terms",
                 "retention_terms", "export_terms", "delete_terms",
                 "process_ref")


def terms_digest(**fields):
    """Canonical digest over the recorded obligations terms — the row
    pins exactly the terms the consent was taken under."""
    body = {name: fields.get(name) for name in _TERMS_FIELDS}
    return "sha256:" + hashlib.sha256(
        _json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]


def record_obligations(store, scope, *, actor_ref, obligations,
                       controller_ref, processor_ref,
                       provider_handling, minimization_terms,
                       retention_terms, export_terms, delete_terms,
                       process_ref):
    """Record the admission context for ``scope`` (A1). Every term is
    required and bounded; the digest is computed here so the durable
    row is self-consistent. Returns the store's record verdict."""
    digest = terms_digest(
        obligations=obligations, controller_ref=controller_ref,
        processor_ref=processor_ref, provider_handling=provider_handling,
        minimization_terms=minimization_terms,
        retention_terms=retention_terms, export_terms=export_terms,
        delete_terms=delete_terms, process_ref=process_ref)
    return store.record_pilot_obligations(
        scope, actor_ref=actor_ref, obligations=obligations,
        controller_ref=controller_ref, processor_ref=processor_ref,
        provider_handling=provider_handling,
        minimization_terms=minimization_terms,
        retention_terms=retention_terms, export_terms=export_terms,
        delete_terms=delete_terms, process_ref=process_ref,
        terms_digest=digest)


def _verify_preview(preview):
    """A workload's declared preview path must be the supported scoped
    provider with a smoke contract — A5's admission check (the exact
    revision binding itself is enforced by PreviewService on deploy)."""
    if not isinstance(preview, dict):
        return {"ok": False, "blocker": "preview-contract-missing"}
    provider = preview.get("provider")
    if provider not in SUPPORTED_PREVIEW_PROVIDERS:
        return {"ok": False,
                "blocker": f"provider-unsupported:{provider or 'none'}"}
    smoke = preview.get("smoke")
    if not isinstance(smoke, dict) \
            or not smoke.get("command") or not smoke.get("expect"):
        return {"ok": False, "blocker": "preview-contract-missing"}
    return {"ok": True, "blocker": None}


def _admission_blocker(store, scope, workload_class, trust_class,
                       boundary, preview):
    """The named readiness blocker for this admission, or ``None``
    when every gate passes (A2–A5)."""
    if store.pilot_obligations(scope) is None:
        return "obligations-missing"
    if workload_class not in SUPPORTED_WORKLOAD_CLASSES:
        return f"unsupported-workload:{workload_class or 'none'}"
    if trust_class not in SUPPORTED_TRUST_CLASSES:
        return f"unsupported-trust:{trust_class or 'none'}"
    if workload_class in BOUNDARY_REQUIRED_WORKLOADS:
        verdict = _boundary.verify_boundary(boundary)
        if not verdict["ok"]:
            return f"boundary-unverified:{verdict['reason']}"
    if workload_class in BOUNDARY_REQUIRED_WORKLOADS or preview:
        check = _verify_preview(preview)
        if not check["ok"]:
            return check["blocker"]
    return None


def admit_participant(store, participant_ref, *,
                      scope=PILOT_COLLECTION_SCOPE, actor_ref,
                      authority_key=None, workload_class, trust_class,
                      boundary=None, preview=None):
    """Decide and durably record one external-participant admission
    (A2–A5).

    ``boundary`` is the evidence document produced by
    :func:`factory_kit.privacy.boundary.run_probes` on the deployment
    host — required for ``contributor-code``. ``preview`` is the
    effective preview contract the workload would flow through
    (``{"provider": ..., "smoke": {"command", "expect"}}``) — required
    for ``contributor-code``, validated whenever supplied.

    Returns ``{"outcome": "admitted"|"denied", ...}``; a denial is a
    durable ``denied`` row carrying the named blocker, never a silent
    drop (A4).
    """
    blocker = _admission_blocker(store, scope, workload_class,
                                 trust_class, boundary, preview)
    state = "denied" if blocker else "consented"
    boundary_ref = (_boundary.evidence_ref(boundary)
                    if isinstance(boundary, dict) else None)
    store.record_pilot_admission(
        participant_ref, scope=scope, actor_ref=actor_ref, state=state,
        authority_key=authority_key, workload_class=workload_class,
        trust_class=trust_class, boundary_evidence=boundary_ref,
        blocker=blocker, obligations_scope=scope)
    result = {"outcome": "admitted" if state == "consented"
              else "denied",
              "participant_ref": participant_ref, "scope": scope,
              "workload_class": workload_class,
              "trust_class": trust_class,
              "boundary_evidence": boundary_ref}
    if blocker:
        result["blocker"] = blocker
    return result


def open_collection(store, participant_ref):
    """Whether new collection may open for this participant (A2).

    ``{"outcome": "open"}`` only while the durable row is ``consented``
    under an obligations-covered scope with a supported classification
    — absent consent, revoked consent and denied admissions all return
    ``denied`` with the named reason. Re-verifies the obligations row
    and classification at open time: consent recorded under terms that
    no longer exist does not silently reopen.
    """
    row = store.pilot_participant(participant_ref)
    if row is None:
        return {"outcome": "denied", "reason": "consent-missing",
                "participant_ref": participant_ref}
    if row["state"] == "revoked":
        return {"outcome": "denied", "reason": "consent-revoked",
                "participant_ref": participant_ref,
                "revoked_at": row["revoked_at"]}
    if row["state"] == "denied":
        return {"outcome": "denied",
                "reason": f"admission-denied:{row['blocker']}",
                "participant_ref": participant_ref}
    if store.pilot_obligations(row["scope"]) is None:
        return {"outcome": "denied", "reason": "obligations-missing",
                "participant_ref": participant_ref}
    if row["workload_class"] not in SUPPORTED_WORKLOAD_CLASSES:
        return {"outcome": "denied",
                "reason": f"unsupported-workload:"
                          f"{row['workload_class']}",
                "participant_ref": participant_ref}
    if row["workload_class"] in BOUNDARY_REQUIRED_WORKLOADS \
            and not row["boundary_evidence"]:
        return {"outcome": "denied",
                "reason": "boundary-evidence-missing",
                "participant_ref": participant_ref}
    return {"outcome": "open", "participant_ref": participant_ref,
            "scope": row["scope"], "workload_class":
            row["workload_class"], "trust_class": row["trust_class"],
            "authority_key": row["authority_key"]}


def revoke_consent(store, participant_ref, *, actor_ref):
    """Withdraw one participant's consent (A2) — durable, blocks new
    collection, excludes the participant's authority from aggregate
    export. Existing rows follow the recorded procedure: they stay as
    the audit record."""
    return store.revoke_pilot_participant(participant_ref,
                                          actor_ref=actor_ref)


def export_excluded_authorities(store, scope=None):
    """Authority keys whose events aggregate export must drop (A2):
    participants whose consent is revoked or whose admission was
    denied. Consent state is durable — the exclusion can never silently
    re-include a withdrawn participant."""
    return {row["authority_key"]
            for row in store.pilot_participant_rows(scope)
            if row["state"] in ("revoked", "denied")
            and row["authority_key"]}


def export_gate(store, scope="pilot-export"):
    """The consent gate an aggregate export must pass (A2/A5): the
    scope-level participant agreement recorded and not revoked, plus
    the excluded-authority set the exporter must honour."""
    agreement = store.participant_agreement(scope)
    if agreement is None:
        return {"outcome": "denied",
                "reason": "participant-agreement-missing",
                "scope": scope}
    if agreement.get("state") == "revoked":
        return {"outcome": "denied",
                "reason": "participant-agreement-revoked",
                "scope": scope,
                "revoked_at": agreement.get("revoked_at")}
    return {"outcome": "open", "scope": scope,
            "agreement": agreement,
            "excluded_authorities":
                sorted(export_excluded_authorities(store))}


def admission_status(store, *, scope=PILOT_COLLECTION_SCOPE):
    """Inspectable summary of the pilot admission state — the
    obligations row (or its absence) and participant rows with their
    states/blockers, for diagnostics and tests."""
    participants = store.pilot_participant_rows(scope)
    counts = {"consented": 0, "denied": 0, "revoked": 0}
    for row in participants:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"scope": scope,
            "obligations": store.pilot_obligations(scope),
            "participants": participants,
            "counts": counts}
