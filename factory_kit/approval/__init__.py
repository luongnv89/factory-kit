"""One-use revision-bound approval decisions (issue #16 / Task 3.2,
PRD §3.2 F12, §6.4 Approval).

``ApprovalService`` is the coordinator-owned human-authority boundary:
the durable request commits before any presentation, decisions are
typed one-use records bound to the request's action/target/revision —
never Telegram conversation state.
"""

from .service import ApprovalService  # noqa: F401

__all__ = ["ApprovalService"]
