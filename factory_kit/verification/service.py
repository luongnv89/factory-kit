#!/usr/bin/env python3
"""Independent review + current-revision verification (issue #11 /
Task 2.6, PRD §3.2 F04, §6.4 Evidence, §7.1 ``evidence_checked``).

:class:`VerificationService` is the coordinator-owned F04 gate. It
turns three inputs — the durable review record a *separate* reviewer
session persisted (A1), the linked PR's authoritative state, and the
check-run observations on the exact head — into one timestamped
evidence row per evaluation. ``verified`` is an *intermediate
observation*: it names the SHA and the observation time, it goes stale
the moment a later read sees the head/base/contract move, and it is
never merge authority (A6) — preview, human approval and the guarded
merge are Sprint-3 prerequisites this task deliberately does not reach.

The vocabulary the service answers in (A3):

- ``verified`` — independent review approved, linked PR open and
  non-draft, authoritative head still equals the reviewed SHA, and
  every required check concluded with a permitted conclusion;
- ``stale`` — the authoritative head moved off the reviewed SHA, or a
  later observation saw the base revision or the contract digest
  change;
- ``failed`` — no usable independent review, a rejected/changes-
  requested verdict, or a required check missing/failed;
- ``blocked`` — the verification contract itself is missing, no PR is
  linked, the PR is closed/draft, or a required check is still
  running;
- ``unknown`` — GitHub could not be read authoritatively (transport
  error, lost response, absent PR). A worker's green exit is never an
  input: without the durable review record and the authoritative
  check-read, a green exit verifies nothing (A3).

The service never writes to the remote: verification is read-only —
human-authored commits are never overwritten to restore an old head
(A3), and the remote port surface it uses carries no mutation verb.
"""

from __future__ import annotations

import json as _json
import re
import time
import uuid
from datetime import datetime

from factory_kit.execution import limits as _limits
from factory_kit.publication.remote import (
    RemoteAmbiguity,
    RemoteError,
)
from .contract import (
    contract_digest,
    contract_from_effective,
    evaluate_checks,
)

__all__ = [
    "REVIEW_VERDICTS",
    "VerificationService",
]

#: Verdicts the reviewer session may record (A1). ``completed`` is not
#: a review verdict — it is the worker's generic success word, and a
#: review record must say what the reviewer *decided*.
REVIEW_VERDICTS = ("approved", "changes-requested", "rejected")

#: Worker-verdict spellings that map onto a review verdict when the
#: durable attempt record is cross-checked — ``completed`` is the
#: runtime's generic pass, read as approval here.
_ATTEMPT_VERDICT_MAP = {"completed": "approved", "approved": "approved",
                        "changes-requested": "changes-requested",
                        "rejected": "rejected"}

_REVISION_RE = re.compile(r"[0-9A-Fa-f]{4,64}")


def _iso_to_epoch(ts) -> float:
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return 0.0


class VerificationService:
    """The F04 independent-review + current-head verification gate.

    - ``store`` — :class:`factory_kit.durable.store.IntakeStore`; the
      review records, evidence rows and ``evidence_checked`` events it
      persists are the §6.4/§7.1 trail.
    - ``now`` — epoch-seconds clock, injectable for deterministic tests.
    """

    def __init__(self, store, *, now=None):
        self.store = store
        self._now = now or time.time

    # ------------------------------------------------------------------ #
    # A1 — the separate reviewer session's own durable record
    # ------------------------------------------------------------------ #

    def record_review(self, work_key, *, attempt_id, session_id, sha,
                      verdict, model=None, findings=None,
                      artifacts=None):
        """Persist the reviewer session's verdict against ``sha``.

        The store accepts the record only when ``attempt_id`` is a real,
        finished ``review``-role attempt on this work, the claimed
        ``session_id`` is that attempt's own session, and no
        implementation attempt ever used it — self-reported
        implementation review cannot mint an independent record (A1).
        Denials stay durable as ``review_rejected`` events.
        """
        sha = str(sha or "").strip()
        if not _REVISION_RE.fullmatch(sha):
            return {"outcome": "denied", "reason": "bad-revision"}
        if verdict not in REVIEW_VERDICTS:
            return {"outcome": "denied", "reason": "bad-verdict"}
        recorded = None
        for row in self.store.attempt_record_rows(work_key):
            if row["attempt_id"] == attempt_id:
                recorded = row["verdict"]
                break
        if recorded is not None and \
                _ATTEMPT_VERDICT_MAP.get(recorded, recorded) != verdict:
            # The attempt ledger recorded a different verdict than the
            # one now claimed — a re-graded review is a substitution,
            # not evidence.
            self.store.record_event(
                "review_rejected", work_key=work_key,
                reason="verdict-mismatch",
                detail=f"attempt={attempt_id}")
            return {"outcome": "denied", "reason": "verdict-mismatch"}
        return self.store.record_review(
            f"rev-{uuid.uuid4().hex[:12]}", work_key=work_key,
            attempt_id=attempt_id, session_id=session_id, model=model,
            verdict=verdict, findings=findings, sha=sha,
            artifacts=artifacts)

    # ------------------------------------------------------------------ #
    # A2–A4 — evaluate the current linked-PR revision
    # ------------------------------------------------------------------ #

    def verify(self, work_key, sha, *, remote, effective,
               pr_number=None):
        """One verification observation for the linked PR at ``sha``.

        Reads GitHub authoritatively (PR head/state/draft, check-runs on
        the exact head), evaluates against the contract and the durable
        review record, then persists the ``verification_evidence`` row +
        ``evidence_checked`` event in one transaction. Returns the
        observation dict; the status is *never* synthesized from worker
        self-report.
        """
        sha = str(sha or "").strip()
        work = self.store.get_work(work_key)
        if work is None:
            return {"outcome": "denied", "reason": "unknown-work"}
        if not _REVISION_RE.fullmatch(sha):
            return self._evidence(work_key, status="failed",
                                  reason="bad-revision")

        # A4 — the contract must exist and be nonempty; a missing or
        # empty one blocks before any remote read.
        contract = contract_from_effective(effective)
        if contract is None:
            return self._evidence(
                work_key, status="blocked",
                reason="verification-contract-missing")
        digest = contract_digest(contract)

        # A1 — the review binding: the *latest* independent review that
        # inspected exactly this SHA. No review → nothing to verify:
        # a green worker exit alone can never satisfy the gate (A3).
        review = self.store.latest_review(work_key, sha)
        if review is None:
            return self._evidence(
                work_key, status="failed",
                reason="independent-review-missing",
                head_sha=sha, contract_digest=digest)
        review_ref = {"review_id": review["review_id"],
                      "review_session": review["session_id"]}
        if review["verdict"] == "rejected":
            return self._evidence(
                work_key, status="failed", reason="review-rejected",
                head_sha=sha, contract_digest=digest,
                artifacts=_review_artifacts(review) or None,
                **review_ref)
        if review["verdict"] == "changes-requested":
            # A5 — one bounded fix only while the cumulative
            # attempt/time budget remains; exhausted limits park, they
            # do not add another attempt.
            fix = self._fix_budget(work_key, effective)
            reason = ("review-changes-requested:" + fix["reason"]
                      if not fix["permitted"]
                      else "review-changes-requested")
            park = not fix["permitted"]
            out = self._evidence(
                work_key, status="failed", reason=reason,
                head_sha=sha, contract_digest=digest, park=park,
                artifacts=_review_artifacts(review) or None,
                **review_ref)
            out["fix"] = fix
            return out

        # A2 — authoritative current-revision read of the linked PR.
        pr_number = self._linked_pr(work, pr_number)
        if pr_number is None:
            return self._evidence(
                work_key, status="blocked", reason="no-linked-pr",
                head_sha=sha, contract_digest=digest, **review_ref)
        try:
            pr = remote.read_pr(pr_number)
        except (RemoteError, RemoteAmbiguity) as exc:
            return self._evidence(
                work_key, status="unknown",
                reason=f"remote-unavailable:{type(exc).__name__}",
                pr_number=pr_number, head_sha=sha,
                contract_digest=digest, **review_ref)
        if pr is None:
            return self._evidence(
                work_key, status="unknown", reason="pr-not-found",
                pr_number=pr_number, head_sha=sha,
                contract_digest=digest, **review_ref)
        pr_ref = {"pr_number": pr.get("number"), "pr_url": pr.get("url"),
                  "head_sha": pr.get("head"), "base_name": pr.get("base")}

        state = str(pr.get("state") or "").upper()
        if state != "OPEN":
            return self._evidence(
                work_key, status="blocked",
                reason=f"pr-not-open:{state.lower() or 'unknown'}",
                contract_digest=digest, **pr_ref, **review_ref)
        if pr.get("draft"):
            return self._evidence(
                work_key, status="blocked", reason="pr-draft",
                contract_digest=digest, **pr_ref, **review_ref)

        # A3/A6 — the authoritative head must still equal the reviewed
        # SHA: A→B is ``stale``, never verified. We never publish a
        # force-push to restore A — verification is read-only.
        if pr.get("head") != sha:
            return self._evidence(
                work_key, status="stale",
                reason=f"head-moved:{sha}->{pr.get('head')}",
                contract_digest=digest, **pr_ref, **review_ref)

        base_sha = None
        try:
            base_sha = remote.read_branch(pr.get("base")) \
                if pr.get("base") else None
        except (RemoteError, RemoteAmbiguity):
            base_sha = None        # honest gap — head gate still holds
        pr_ref["base_sha"] = base_sha

        # Required checks on the exact head — provider identities and
        # conclusions are persisted verbatim (A2/§6.4 Evidence).
        try:
            runs = remote.check_runs(sha)
        except (RemoteError, RemoteAmbiguity) as exc:
            return self._evidence(
                work_key, status="unknown",
                reason=f"remote-unavailable:{type(exc).__name__}",
                contract_digest=digest, **pr_ref, **review_ref)
        gate = evaluate_checks(runs, contract)
        evidence_fields = dict(
            pr_ref, checks=gate["checks"], contract_digest=digest,
            artifacts=self._artifacts(review, gate["checks"]))
        if gate["status"] != "passed":
            return self._evidence(
                work_key, status=gate["status"],
                reason=";".join(gate["reasons"]),
                **evidence_fields, **review_ref)
        # A6 — a *prior* verified observation anchors drift detection:
        # the same head under a moved base or a changed contract is a
        # new observation, and the new observation is ``stale``.
        prior = self._latest_verified(work_key)
        if prior is not None:
            if prior["base_sha"] is not None and base_sha is not None \
                    and prior["base_sha"] != base_sha:
                return self._evidence(
                    work_key, status="stale",
                    reason=f"base-moved:{prior['base_sha']}->{base_sha}",
                    **evidence_fields, **review_ref)
            if prior["contract_digest"] != digest:
                return self._evidence(
                    work_key, status="stale",
                    reason="contract-changed",
                    **evidence_fields, **review_ref)
        return self._evidence(
            work_key, status="verified", reason="verified",
            **evidence_fields, **review_ref)

    # ------------------------------------------------------------------ #
    # A6 — status + invalidation
    # ------------------------------------------------------------------ #

    def verification_status(self, work_key):
        """The *current* observation — always with the SHA and the
        observation time it was made at. ``merge_authorized`` is a
        structural ``False``: a verified observation is evidence for a
        later human approval, never permission to merge (A6)."""
        latest = self.store.latest_evidence(work_key)
        if latest is None:
            return {"status": "unknown", "reason": "no-evidence",
                    "head_sha": None, "observed_at": None,
                    "merge_authorized": False}
        return {"status": latest["status"], "reason": latest["reason"],
                "evidence_id": latest["evidence_id"],
                "pr_number": latest["pr_number"],
                "pr_url": latest["pr_url"],
                "head_sha": latest["head_sha"],
                "base_sha": latest["base_sha"],
                "contract_digest": latest["contract_digest"],
                "observed_at": latest["observed_at"],
                "merge_authorized": False}

    def mark_stale(self, work_key, reason):
        """Invalidate the latest ``verified`` observation because a
        *later* read (reconciliation, a webhook) saw revision/base/
        policy move — without needing a full re-evaluation first (A6).

        The stale row copies the prior observation's identities so the
        trail shows exactly which verified result lapsed and why."""
        latest = self.store.latest_evidence(work_key)
        if latest is None:
            return {"outcome": "denied", "reason": "no-evidence"}
        if latest["status"] != "verified":
            return {"outcome": "unchanged",
                    "status": latest["status"],
                    "evidence_id": latest["evidence_id"]}
        with self.store.transact() as tx:
            out = tx.record_evidence(
                f"ev-{uuid.uuid4().hex[:12]}", work_key=work_key,
                seq=tx.next_seq(),
                pr_number=latest["pr_number"],
                pr_url=latest["pr_url"],
                head_sha=latest["head_sha"],
                base_name=latest["base_name"],
                base_sha=latest["base_sha"],
                checks=_json.loads(latest["checks"])
                if latest["checks"] else None,
                review_id=latest["review_id"],
                review_session=latest["review_session"],
                artifacts=_json.loads(latest["artifacts"])
                if latest["artifacts"] else None,
                contract_digest=latest["contract_digest"],
                status="stale", reason=reason)
        return {"outcome": "stale", "reason": reason,
                "evidence_id": out["evidence_id"]}

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _linked_pr(self, work, pr_number):
        if pr_number is not None:
            return str(pr_number)
        if work.get("linked_pr"):
            return str(work["linked_pr"])
        for intent in self.store.intent_rows(work["work_key"]):
            if intent["operation"] == "pr-publish" and \
                    intent["state"] == "linked" and \
                    intent["remote_ref"]:
                return str(intent["remote_ref"])
        return None

    def _latest_verified(self, work_key):
        return self.store.latest_verified_evidence(work_key)

    def _fix_budget(self, work_key, effective):
        """A5's one-fix rule against the *cumulative* durable budget —
        attempts and measured/unknown usage across every role, the wall
        clock from the first durable attempt start (a restart can never
        gift fresh hours)."""
        limits = _limits.LaneLimits.from_effective(effective)
        used = self.store.count_attempts(work_key,
                                         role="implementation")
        measured, unknown = self.store.work_active_seconds(work_key)
        starts = [_iso_to_epoch(r["started"]) for r in
                  self.store.attempt_record_rows(work_key)]
        wall = self._now() - min(starts) if starts else 0.0
        status = _limits.budget_status(
            measured_active_s=measured, unknown_usage=unknown,
            attempts_used=used, wall_elapsed_s=wall, limits=limits)
        if used >= limits.implementation_attempts:
            return {"permitted": False, "reason": "attempts-exhausted",
                    "attempts_used": used,
                    "attempts_limit": limits.implementation_attempts,
                    "budget": status}
        if status["status"] == "exhausted":
            return {"permitted": False,
                    "reason": "budget-exhausted:"
                              + ",".join(status["exceeded"]),
                    "attempts_used": used,
                    "attempts_limit": limits.implementation_attempts,
                    "budget": status}
        return {"permitted": True, "reason": None,
                "attempts_used": used,
                "attempts_limit": limits.implementation_attempts,
                "budget": status}

    def _artifacts(self, review, checks):
        """Artifact/log references for the evidence row (A2/§6.4):
        the review's own references plus every check-run details
        URL — the reproducible CI fixture pointers (A6)."""
        refs = list(_review_artifacts(review))
        for run in checks or []:
            url = run.get("details_url")
            if url:
                refs.append({"kind": "check-run",
                             "name": run.get("name"),
                             "id": run.get("id"), "url": url})
        return refs or None

    def _evidence(self, work_key, *, status, reason, park=False,
                  **fields):
        """Append the observation row + ``evidence_checked`` event in
        one commit — and, when ``park`` is set, the work's durable
        park in the *same* transaction so an exhausted fix budget can
        never leave the work looking fixable (A5)."""
        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"
        with self.store.transact() as tx:
            out = tx.record_evidence(
                evidence_id, work_key=work_key, seq=tx.next_seq(),
                status=status, reason=reason, **fields)
            if park:
                tx.set_work_state(work_key, "parked",
                                  reason=f"verification-{reason}")
                tx.record_event("work_parked", work_key=work_key,
                                reason=f"verification-{reason}")
        return {"status": status, "reason": reason,
                "evidence_id": evidence_id,
                "observed_at": out["observed_at"], "parked": park}


def _review_artifacts(review):
    raw = (review or {}).get("artifacts")
    if not raw:
        return []
    try:
        refs = _json.loads(raw)
    except ValueError:
        return []
    return list(refs) if isinstance(refs, list) else []
