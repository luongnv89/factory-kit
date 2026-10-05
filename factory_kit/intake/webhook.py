#!/usr/bin/env python3
"""GitHub webhook transport adapter — F02 intake (issue #8).

The webhook route (``hermes webhook subscribe``, per the setup plan's
``webhook:intake-events`` entry) delivers an ``X-GitHub-Delivery`` GUID,
an ``X-Hub-Signature-256`` HMAC and the event JSON. This module is the
*transport* boundary: it verifies the HMAC over the canonical intake
payload and normalizes the GitHub ``issues`` event into the canonical
envelope :meth:`IntakeService.deliver` evaluates.

The kit signs the canonical ``delivery|repo_id|issue`` payload — the
same form the Sprint-1 spike probes used — so a signature proves the
delivery identity, the target repository and the issue it claims. The
HMAC secret is the resolved ``secrets.webhook_signing_key`` manifest
reference; it is verified in memory and never persisted.

Payload trust boundary: after signature verification the payload is
still *content* — title, body and labels are inspected by the intake
gates, fingerprinted, and never stored. An ``envelope_from_github``
that cannot normalize returns ``None``; the caller delivers the raw
fields so the denial is still recorded redacted.
"""

from __future__ import annotations

from .envelope import EnvelopeError, normalize
from .service import compute_signature, signature_ok as _signature_ok

__all__ = [
    "verify_signature",
    "envelope_from_github",
]

#: GitHub issue actions the intake consumes.
_ACTION_MAP = {
    "opened": "opened",
    "edited": "edited",
    "labeled": "labeled",
    "unlabeled": "unlabeled",
    "reopened": "reopened",
    "closed": "closed",
}


def verify_signature(secret, delivery_id, repo_id, issue,
                     presented) -> bool:
    """Verify the presented HMAC-SHA256 (``sha256=<hex>`` or bare hex)
    over the canonical signed payload — constant-time compare."""
    return _signature_ok(secret, delivery_id, repo_id, issue, presented)


def envelope_from_github(delivery_id, payload, *, signature=None):
    """Normalize a GitHub ``issues`` event payload into the canonical
    envelope. Returns ``None`` when the payload lacks the fields intake
    needs — the caller still hands the raw fields to ``deliver`` so the
    rejection is persisted redacted rather than silently dropped.

    ``signature`` is the value the transport presented
    (``X-Hub-Signature-256``); verification itself happens inside the
    service's durable transaction, so a forged envelope cannot skip the
    check by omitting the field.
    """
    if not isinstance(payload, dict):
        return None
    action = _ACTION_MAP.get(payload.get("action"))
    issue = payload.get("issue") or {}
    repo = payload.get("repository") or {}
    sender = (payload.get("sender") or {}).get("login") or ""
    repo_id = repo.get("node_id") or repo.get("id")
    if action is None or repo_id in (None, "") or not issue:
        return None
    labels = [l.get("name", "") for l in (issue.get("labels") or [])
              if isinstance(l, dict)]
    try:
        return normalize({
            "delivery_id": delivery_id,
            "channel": "webhook",
            "repo_id": str(repo_id),
            "repository": repo.get("full_name") or "",
            "issue": issue.get("number"),
            "action": action,
            "sender": sender,
            "labels": labels,
            "issue_title": issue.get("title") or "",
            "issue_body": issue.get("body") or "",
            "issue_revision": issue.get("updated_at") or "",
            "signature": signature,
        })
    except EnvelopeError:
        return None
