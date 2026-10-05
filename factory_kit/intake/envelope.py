#!/usr/bin/env python3
"""Canonical intake envelope — normalization, fingerprint, content gates.

Both intake channels (webhook push and reconciliation polling) produce
one canonical envelope shape, so the durable decision pipeline in
:mod:`factory_kit.intake.service` evaluates identical fields regardless
of where the event arrived from (F02 — "webhook, polling and restart
agree").

Envelope fields:

- ``delivery_id`` — the transport's unique delivery identity (GitHub
  ``X-GitHub-Delivery`` GUID for webhooks; a poller-minted ID for
  reconciliation). Required; dedup keys on it.
- ``channel`` — ``"webhook"`` or ``"reconciliation"``.
- ``repo_id`` — the immutable GitHub repository node ID, matching
  ``identity.repo_id`` in the manifest. This is the only authority key.
- ``repository`` — display ``owner/name`` (informational only).
- ``issue`` — the issue number.
- ``action`` — ``opened`` / ``edited`` / ``labeled`` / ``unlabeled`` /
  ``reopened`` / ``closed`` / ``reconcile``.
- ``sender`` — the GitHub login that triggered the event (the label
  actor for ``labeled``/``unlabeled``, the author for ``opened``).
- ``labels`` — current label names on the issue, at observation time.
- ``issue_revision`` — the observed revision marker (``updated_at``),
  used to detect edits.
- ``signature`` — the presented HMAC-SHA256 hex for ``webhook``
  deliveries; unused on the reconciliation channel.

Derived fields (computed here, never trusted from the payload):

- ``context_fingerprint`` — SHA-256 over title/body/labels/revision;
  persisted so edits are observable without storing content.
- ``has_acceptance_criteria`` — the issue body carries an acceptance
  section; intake creates no execution authority without one (A3).
- ``permission_prose`` — name of the matched permission-granting-prose
  rule, or ``None``. Prose can never grant authority (A3, F02).

``issue_title``/``issue_body`` ride along on the envelope **in memory
only** so ``normalize`` is idempotent and the gates always recompute
from content — a pre-set flag on a raw payload is never trusted. Only
the fingerprint is persisted; bodies are inspected for the gates and
never reach the store.
"""

from __future__ import annotations

import hashlib
import re

__all__ = [
    "EnvelopeError",
    "CHANNELS",
    "ACTIONS",
    "context_fingerprint",
    "has_acceptance_criteria",
    "permission_prose_reason",
    "normalize",
]

CHANNELS = ("webhook", "reconciliation")

#: Actions the intake understands. ``unlabeled``/``closed`` route to the
#: revocation path (current labels decide); ``edited`` routes to the
#: fingerprint/reevaluation path; everything else is an accept
#: evaluation.
ACTIONS = ("opened", "edited", "labeled", "unlabeled", "reopened",
           "closed", "reconcile")

#: Actions that revoke unconditionally — a closed issue can never hold
#: execution authority, whatever its remaining labels. ``unlabeled`` is
#: deliberately absent: removing a *different* label is not a revocation,
#: so the opt-in label check evaluates the *current* label set and a
#: reordered delivery reconciles on what GitHub says now (A4).
REVOKE_ACTIONS = ("closed",)

#: Actions that mean "content changed" — pending work refreshes its
#: fingerprint; active work is flagged for re-evaluation (A4).
EDIT_ACTIONS = ("edited", "reconcile")


class EnvelopeError(Exception):
    """The event cannot be normalized into an intake envelope."""


def context_fingerprint(title, body, labels, revision) -> str:
    """SHA-256 over the source context intake binds to — title, body,
    label set and observed revision. Persisted instead of content, so an
    edit changes the fingerprint without the store ever holding prose."""
    h = hashlib.sha256()
    h.update((title or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((body or "").encode("utf-8"))
    h.update(b"\x00")
    for label in sorted(labels or []):
        h.update(str(label).encode("utf-8"))
        h.update(b"\x00")
    h.update(str(revision or "").encode("utf-8"))
    return h.hexdigest()


_AC_HEADING_RE = re.compile(r"acceptance\s+criteria", re.IGNORECASE)
_AC_ITEM_RE = re.compile(
    r"^\s*(?:[-*]\s*\[[ xX]\]|\*\*A\d+\.\*\*|A\d+\.)", re.MULTILINE)


def has_acceptance_criteria(body) -> bool:
    """True when the issue body carries an acceptance-criteria section:
    either the heading itself or at least one checklist/`A<n>.` criterion
    line. No acceptance contract → no execution authority (A3)."""
    if not isinstance(body, str) or not body.strip():
        return False
    if _AC_HEADING_RE.search(body):
        return True
    return _AC_ITEM_RE.search(body) is not None


#: Permission-granting prose. An issue body is *untrusted content*: text
#: that reads as a grant of permission, a policy override or an
#: instruction to bypass verification can never create authority (F02:
#: "an instruction embedded in issue prose … no new execution authority
#: is granted"). Matching persists only the rule name — never the text.
_PERMISSION_PROSE_RES = tuple((name, re.compile(pattern, re.IGNORECASE))
                              for name, pattern in (
    ("self-authorize",
     r"\b(i|we|the author)\s+(hereby\s+)?authoriz[ae]\b"),
    ("you-are-authorized",
     r"\byou are (now\s+)?authoriz[ae]d\b"),
    ("permission-granted",
     r"\bpermission (is )?granted\b|\bgrant(ed)? (you |it |them )?"
     r"(yourself )?(access|permission)\b"),
    ("ignore-policy",
     r"\bignore\b.{0,40}\b(polic|instruction|rule|check|guard|previous)"),
    ("bypass",
     r"\bbypass\b|\bskip\b.{0,30}\b(verif|check|review|approv|policy)"),
    ("disable-gates",
     r"\b(disable|turn off|deactivate)\b.{0,30}"
     r"\b(check|verif|policy|guard|gate|approv|security)"),
    ("pre-approve",
     r"\b(pre|already|auto)[- ]?(approve|authoriz)\w*\b.{0,40}"
     r"\b(merge|deploy|publish|release)\b"),
))


def permission_prose_reason(body):
    """Return the rule name of the first permission-granting-prose match
    in ``body``, or ``None``. The rule name is safe to persist; the
    matched text is never returned or stored."""
    if not isinstance(body, str) or not body:
        return None
    for name, pattern in _PERMISSION_PROSE_RES:
        if pattern.search(body):
            return name
    return None


def normalize(fields) -> dict:
    """Validate and complete a canonical intake envelope.

    Raises :class:`EnvelopeError` on missing/malformed required fields —
    an envelope that cannot be normalized is denied upstream as
    ``malformed-event`` rather than evaluated.
    """
    env = dict(fields)
    delivery_id = env.get("delivery_id")
    if not isinstance(delivery_id, str) or not delivery_id.strip():
        raise EnvelopeError("delivery_id is required")
    channel = env.get("channel")
    if channel not in CHANNELS:
        raise EnvelopeError(f"channel must be one of {CHANNELS}")
    repo_id = env.get("repo_id")
    if not isinstance(repo_id, str) or not repo_id.strip():
        raise EnvelopeError("repo_id is required")
    action = env.get("action") or "opened"
    if action not in ACTIONS:
        raise EnvelopeError(f"action must be one of {ACTIONS}")
    try:
        issue = int(env.get("issue"))
    except (TypeError, ValueError):
        raise EnvelopeError("issue must be an integer")
    labels = env.get("labels") or []
    if not isinstance(labels, (list, tuple)) or \
            not all(isinstance(x, str) for x in labels):
        raise EnvelopeError("labels must be a list of strings")

    title = env.get("issue_title") or ""
    body = env.get("issue_body") or ""
    revision = env.get("issue_revision") or ""

    out = {
        "delivery_id": delivery_id.strip(),
        "channel": channel,
        "repo_id": repo_id.strip(),
        "repository": env.get("repository") or "",
        "issue": issue,
        "action": action,
        "sender": env.get("sender") or "",
        "labels": sorted(set(labels)),
        "issue_revision": revision,
        "signature": env.get("signature"),
        # In-memory only — never persisted; carried so re-normalizing an
        # envelope recomputes the gates from content, not from flags a
        # caller could have preset.
        "issue_title": title,
        "issue_body": body,
        "context_fingerprint":
            context_fingerprint(title, body, labels, revision),
        "has_acceptance_criteria": has_acceptance_criteria(body),
        "permission_prose": permission_prose_reason(body),
    }
    return out
