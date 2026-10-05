# Pilot consent and external-workload trust admission

Issue #28 (Task 4.4) — the durable gate that stands **before** any
external participant information, report, code or installation
telemetry is collected. Nothing in this document admits a cohort; it
records the obligations, the consent/admission procedure and the
boundary evidence an external workload must carry. Unresolved
prerequisites remain named blockers — no blocker was hidden to make a
record look ready.

## 1. Obligations record (A1)

Before a participant consent can exist, the operator records the
admission context for the collection scope
(`consent.record_obligations` → `pilot_obligations` row +
`pilot_obligations_recorded` event). Every field is required; policy
text is bounded (400 chars) — the row holds terms and references,
never participant content.

| Field | Content | Current record |
| --- | --- | --- |
| `obligations` | Applicable obligations for the collection scope | Pilot obligations per this document; operator acceptance recorded with `actor_ref` |
| `controller_ref` | Data controller | Repository owner (`actor_ref` on the obligations row) |
| `processor_ref` | Data processor | `factory-kit` local pipeline — this repository |
| `provider_handling` | Model-provider data handling | Codex via the scoped runtime credential per `.factory-kit.yml` `runtime.model`; no participant content is sent outside the contracted provider path |
| `minimization_terms` | Collection minimization | Aggregate-safe fields only; typed events carry no bodies, code, usernames or chat text (`events/schema.py`) |
| `retention_terms` | Retention | `privacy/retention.py` defaults: worker-log detail 7 days, audit detail 30 days, identity tombstones retained |
| `export_terms` | Export | Minimized aggregate fields via `pilot_export`, consent-gated (`diagnostics/report.py`) |
| `delete_terms` | Deletion | Identity tombstones preserved; `setup.remove` keeps consent rows — consent outlives a registration |
| `process_ref` | Approved consent/admission process | This document |

The `terms_digest` (`sha256:` over the canonical terms) pins exactly the
terms consent was taken under; re-recording a scope replaces the row
with a fresh digest and event.

## 2. Consent and workload/trust classification (A2)

`consent.admit_participant` decides; `pilot_participants` records.
Collection opens (`consent.open_collection` → `open`) only while all
of these hold:

1. the obligations row exists for the scope — consent ahead of
   obligations is denied `obligations-missing`;
2. the participant row is `consented` — never `denied` and never
   `revoked`;
3. `workload_class` ∈ `SUPPORTED_WORKLOAD_CLASSES`
   (`observation`, `contributor-code`);
4. `trust_class` ∈ `SUPPORTED_TRUST_CLASSES`
   (`external-reviewed`, `external-untrusted`) — `internal` is not an
   external-pilot classification;
5. for `contributor-code`, a `boundary_evidence` reference exists —
   evidence produced by `privacy/boundary.py`, not a claim.

Absent consent denies `consent-missing`; a denied admission denies
`admission-denied:<blocker>`; revocation denies `consent-revoked`.
`consent.revoke_consent` flips the durable row to `revoked` — the row
and its `pilot_consent_revoked` event stay: existing records follow
this recorded procedure, they are never silently deleted. Aggregate
export additionally denies on a revoked scope-level participant
agreement (`participant-agreement-revoked`).

## 3. Boundary evidence for external code (A3–A4)

`contributor-code` workloads run *hostile probes*, not checkboxes:
`python3 -m factory_kit.privacy.boundary` executes filesystem-read
(`/etc/passwd`), outbound-exfiltration (`127.0.0.1` connect) and
privileged-write fixtures under the host's real isolation mechanism —
`sandbox-exec` (seatbelt) on macOS, `bwrap` (bubblewrap) on Linux —
plus a credential-scope check that the workload environment carries no
controller, production or unrestricted GitHub credentials.

- Every probe denied → `outcome: verified`; the admission row stores
  `evidence_ref` = `mechanism:sha256:…` pinning the exact document.
- A probe *succeeds* → `failed` + `boundary-breach:<probe>`; the
  admission is denied and the blocker named.
- A probe is undeterminable → `unverified` +
  `boundary-probe-inconclusive:<probe>`.
- No mechanism on the host → `unverified` +
  `isolation-mechanism-missing`.
- A git **worktree**, a **prompt rule** or **branch protection** is a
  coordination control, never isolation: `verify_boundary` rejects
  evidence claiming them even when marked `verified`
  (`non-isolation:<name>`).

## 4. Preview admission (A5)

A declared preview path must target the supported scoped provider
(`vercel`) with a configured smoke contract — else the admission is
denied (`provider-unsupported:<p>` / `preview-contract-missing`).
`contributor-code` requires the contract at admission; the exact
head/base revision binding, the scoped credential use and the durable
smoke evidence are then enforced by `factory_kit.preview.service` per
deployment. No pilot admission broadens v1.0 authority: the
unsupported-preview and internal-trust denials prove it.

## 5. Aggregate export (A5)

`pilot_export` emits only `aggregate_properties`-safe fields (the §7.1
rules already reject sensitive keys, secret-shaped values and free
text). Two additions bind it to consent:

- the scope-level `participant_agreements` row must exist **and not be
  `revoked`**;
- events minted under a `revoked`/`denied` participant's
  `authority_key` are excluded and counted
  (`consent.excluded_authorities`, `consent.events_excluded`).

The admission trail carries `participant_hash` — never the raw
participant reference — so an export cannot identify a participant.

## 6. Named blockers (current state)

Nothing below is satisfied today; collection stays closed until the
operator records obligations and each admission passes.

- `obligations-missing` — no `pilot_obligations` row recorded for the
  collection scope by default; an operator must record §1.
- `participant-agreement-missing` — no `pilot-export` scope agreement;
  aggregate export denied (pre-existing Task 2.9 behaviour).
- `isolation-mechanism-missing` — hosts without `sandbox-exec` or
  `bwrap` cannot produce boundary evidence; `contributor-code`
  workloads stay denied there.
- `consent-missing` — no external participant is admitted; no
  admission rows exist until an operator runs the consent procedure.
