#!/usr/bin/env python3
"""Reconciliation channel adapter — F02 polling intake (issue #8).

Periodic reconciliation (``hermes cron`` per the boundary map — never a
kit-invented scheduler) observes the *current* GitHub issue set and feeds
each observation through the same :meth:`IntakeService.deliver` pipeline
the webhook channel uses. That is the whole point of the contract:
delivery deduplication is distinct from logical work identity, so a poll
that re-observes an already-accepted issue converges on its existing row
(``reconciled``) and a poll observing a revoked opt-in parks it — the
channels can never disagree.

An observation carries what the poller read *now*: labels, title, body,
revision and the actor attribution when known (the labeler for opt-in
labels, else the issue author). Reconciliation events skip the webhook
HMAC — the channel is authenticated by the poller's own local GitHub
credentials, which is exactly why webhook, polling and restart agree:
authorization decisions never depend on transport proof, only on the
persisted registration and the effective configuration.
"""

from __future__ import annotations

from .envelope import normalize

__all__ = ["observation_envelope", "reconcile_observations"]


def observation_envelope(observation) -> dict:
    """Normalize one poller observation into a canonical envelope.

    ``observation`` keys: ``delivery_id`` (poller-minted, unique per poll
    pass — distinct deliveries stay separately inspectable), ``repo_id``,
    ``repository``, ``issue``, ``issue_title``, ``issue_body``,
    ``issue_revision``, ``labels``, ``sender`` (the observed actor — the
    label applier when known, else the issue author).
    """
    fields = dict(observation)
    fields["channel"] = "reconciliation"
    fields.setdefault("action", "reconcile")
    fields.setdefault("signature", None)
    return normalize(fields)


def reconcile_observations(service, observations) -> list:
    """Feed each observation through the shared pipeline and return the
    acknowledgements in order. ``deliver`` re-normalizes, so a malformed
    observation still records its redacted rejection rather than being
    dropped."""
    acks = []
    for obs in observations:
        # A non-mapping observation cannot even normalize — deliver an
        # empty envelope so the pass records a redacted malformed-event
        # rejection instead of aborting the whole reconciliation.
        fields = dict(obs) if isinstance(obs, dict) else {}
        fields.setdefault("channel", "reconciliation")
        fields.setdefault("action", "reconcile")
        acks.append(service.deliver(fields))
    return acks
