"""Protected conditional merge + authoritative outcome reconciliation
(issue #17 / Task 3.3, PRD §3.2 F12, §6.4 Merge outcome).

``MergeService`` is the single deterministic merge owner: it revalidates
every merge condition immediately before the spend, atomically consumes
the one-use approval into the durable ``merge_intents`` row, sends only
the supported expected-head conditional merge under repository-enforced
protections, and reports ``merged`` solely from authoritative read-back —
parking and alerting on any unknown outcome. ``evaluate_merge_guard`` is
the pure condition gate the service runs.
"""

from .guard import evaluate_merge_guard  # noqa: F401
from .service import MergeService  # noqa: F401

__all__ = ["MergeService", "evaluate_merge_guard"]
