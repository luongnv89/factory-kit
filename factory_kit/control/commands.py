#!/usr/bin/env python3
"""Typed control commands — the validator every Telegram message and
every natural-language proposal must pass (issue #12 / Task 2.7,
PRD §6.4 Control, CFG02).

One command, one durable record. :func:`validate` turns a transport
message into a typed command *candidate*; :func:`propose` maps natural
language onto the same candidate shape, so a chat sentence can never
reach the control surface without passing the identical typed
validator (A5). Neither function authorizes anything — allowlisting,
target resolution and generation freshness all happen in
:mod:`factory_kit.control.service` under the durable store.

Restricted references (CFG02): actors and chats are *numeric* Telegram
IDs rendered ``telegram:<digits>`` — a username string can never pass,
matching the manifest schema where allowlists are numeric IDs only.

Targets are explicit: mutating commands (pause/resume/cancel and the
Task-3.2 ``approve``/``reject`` decisions) require ``repo_id`` +
``issue`` + ``generation`` — an ambiguous or missing target is
rejected with an ask for a concrete one; ``status`` needs ``repo_id``
+ ``issue``; ``retry`` needs ``repo_id`` + ``issue`` (the retried
generation is resolved durably, fresh authority comes from
``authorized_by`` at commit time). Button callbacks may additionally
carry ``request_id`` — the durable approval request the decision
binds (F12 A2).
"""

from __future__ import annotations

import re

__all__ = [
    "ACTIONS",
    "MUTATING_ACTIONS",
    "COMMAND_REFERENCE",
    "command_reference",
    "reference_text",
    "validate",
    "propose",
]

#: The recognized control verbs (A1).
ACTIONS = ("status", "pause", "resume", "cancel", "retry",
           "approve", "reject", "steer")

#: Commands that change execution state — they require the full
#: explicit target: repository + task + generation (A1).
MUTATING_ACTIONS = ("pause", "resume", "cancel", "approve", "reject",
                    "steer")

#: Bounds on the revised acceptance criteria a ``steer`` command
#: carries — the criteria text persists in ``scope_revisions``, so it
#: is bounded like every other durable payload (Task 4.1 / F09 A1).
CRITERIA_MAX_CHARS = 4000

#: A3 — the documented text equivalent of every interactive affordance.
#: Each entry names the button/action, the exact text command that does
#: the same thing, the target scope the command binds, and what it does
#: — the table the Telegram footer, the help surface and the docs all
#: render verbatim, so a screen-reader user and a button user get the
#: same capabilities with the same identifiers.
COMMAND_REFERENCE = (
    {"action": "status",
     "command": "status <repo> <issue>",
     "scope": "repository + issue (latest generation)",
     "mutates": False,
     "effect": "read the task's committed state, blocker, revision, "
               "committed-effect links, heartbeat and limits"},
    {"action": "pause",
     "command": "pause <repo> <issue> <generation>",
     "scope": "repository + issue + live generation",
     "mutates": True,
     "effect": "hold work at the next stage boundary"},
    {"action": "resume",
     "command": "resume <repo> <issue> <generation>",
     "scope": "repository + issue + live generation",
     "mutates": True,
     "effect": "release the pause boundary; work re-enters the lane"},
    {"action": "cancel",
     "command": "cancel <repo> <issue> <generation>",
     "scope": "repository + issue + live generation",
     "mutates": True,
     "effect": "fence the generation and terminate the worker; "
               "terminal for that generation"},
    {"action": "retry",
     "command": "retry <repo> <issue>",
     "scope": "repository + issue",
     "mutates": True,
     "effect": "mint a fresh authorized generation (audited)"},
    {"action": "approve",
     "command": "approve <repo> <issue> <generation>",
     "scope": "repository + issue + live generation",
     "mutates": True,
     "effect": "record the one-use human approval on the durable "
               "request (bound action/revision)"},
    {"action": "reject",
     "command": "reject <repo> <issue> <generation>",
     "scope": "repository + issue + live generation",
     "mutates": True,
     "effect": "record the human rejection; work blocks for a human "
               "decision"},
    {"action": "steer",
     "command": "steer <repo> <issue> <generation> "
                "criteria=<revised criteria>",
     "scope": "repository + issue + live generation",
     "mutates": True,
     "effect": "persist the authorized scope change; the old attempt is "
               "fenced at its stage checkpoint and the replacement runs "
               "the revised criteria within the remaining budgets"},
)


def command_reference():
    """A3 — the action → text-command mapping as data: one entry per
    recognized verb, each with its exact syntax, the target scope it
    binds and whether it mutates. Every Telegram button has a row here —
    the documented text alternative is never a separate invention."""
    return list(COMMAND_REFERENCE)


def reference_text():
    """Render :data:`COMMAND_REFERENCE` as plain text — stable command
    words, explicit scope, no color or emoji (A3)."""
    lines = ["TEXT COMMANDS - every button has this equivalent:"]
    for entry in COMMAND_REFERENCE:
        lines.append(
            f"  {entry['command']} - {entry['effect']} "
            f"(scope: {entry['scope']}).")
    lines.append(
        "An ambiguous or missing target is rejected with a request for "
        "the concrete one - nothing executes on a guess.")
    return "\n".join(lines)

#: Per-action required target fields. Everything else about the
#: request shape is common: command_id, actor, chat.
_REQUIRED_TARGET = {
    "status": ("repo_id", "issue"),
    "pause": ("repo_id", "issue", "generation"),
    "resume": ("repo_id", "issue", "generation"),
    "cancel": ("repo_id", "issue", "generation"),
    "retry": ("repo_id", "issue"),
    "approve": ("repo_id", "issue", "generation"),
    "reject": ("repo_id", "issue", "generation"),
    "steer": ("repo_id", "issue", "generation", "criteria"),
}

_REF_RE = re.compile(r"^telegram:(-?\d+)$")

#: Natural-language proposal patterns — deliberately small and
#: conservative: anything ambiguous produces no proposal and the
#: sender is asked for a concrete command (never a guessed effect).
_NL_ACTION = (
    (re.compile(r"\b(status|state|progress|report)\b", re.I), "status"),
    (re.compile(r"\b(pause|hold)\b", re.I), "pause"),
    (re.compile(r"\b(resume|continue|unpause)\b", re.I), "resume"),
    (re.compile(r"\b(cancel|stop|abort)\b", re.I), "cancel"),
    (re.compile(r"\b(retry|rerun|re-?run)\b", re.I), "retry"),
    (re.compile(r"\b(approve|accept|lgtm|ship)\b", re.I), "approve"),
    (re.compile(r"\b(reject|decline)\b", re.I), "reject"),
    (re.compile(r"\b(steer|re-?scope|redirect)\b", re.I), "steer"),
)
_NL_REPO = re.compile(
    r"\b(?:repo(?:sitory)?|gh)[:# ]+([A-Za-z0-9_-]+)\b", re.I)
_NL_ISSUE = re.compile(r"(?:issue|task|#)\s*#?(\d+)\b", re.I)
_NL_GEN = re.compile(
    r"\b(?:generation|gen|g)\s*[:#]?\s*(\d+)\b", re.I)
_SLASH = re.compile(r"^/([a-z]+)\b")


def _num(value):
    """Coerce a numeric ID field — Telegram IDs are integers; a
    digit-string is accepted (Telegram chat IDs are *negative*), a
    sign-only or non-numeric string is not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
    return None


def _ref(value):
    """Normalize an actor/chat reference to ``telegram:<digits>`` —
    usernames and non-numeric IDs fail (CFG02). Group chat IDs are
    negative, so the canonical ``telegram:-<digits>`` form must
    round-trip: a transport echoing the emitted ref is not a forgery."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("user_id", value.get("chat_id",
                                               value.get("id")))
    number = _num(value)
    if number is not None:
        return f"telegram:{number}"
    match = _REF_RE.match(str(value).strip())
    return f"telegram:{int(match.group(1))}" if match else None


def validate(message):
    """Validate a raw message into a typed control command.

    Returns ``{"ok": True, "value": command}`` where command carries
    ``command_id``, restricted ``actor_ref``/``chat_ref``, ``action``
    and the explicit ``repo_id``/``issue``/``generation`` target — or
    ``{"ok": False, "reason": code}``. ``reason`` is one of:

    ``malformed`` · ``no-command-id`` · ``unsupported-action`` ·
    ``no-actor`` · ``no-chat`` · ``target-required`` ·
    ``bad-generation`` · ``criteria-too-large``
    """
    if not isinstance(message, dict):
        return {"ok": False, "reason": "malformed"}

    # A natural-language message is a *proposal*: it becomes the same
    # typed shape, then passes through this same validator (A5).
    if message.get("action") is None and message.get("text"):
        proposal = propose(message["text"])
        if proposal is None:
            return {"ok": False, "reason": "unsupported-action"}
        merged = dict(message)
        merged.update(proposal)
        message = merged

    command_id = str(message.get("command_id") or "").strip()
    if not command_id:
        return {"ok": False, "reason": "no-command-id"}
    actor_ref = _ref(message.get("actor_ref", message.get("actor")))
    if actor_ref is None:
        return {"ok": False, "reason": "no-actor"}
    chat_ref = _ref(message.get("chat_ref", message.get("chat")))
    if chat_ref is None:
        return {"ok": False, "reason": "no-chat"}

    action = str(message.get("action") or "").strip().lower()
    if action not in ACTIONS:
        return {"ok": False, "reason": "unsupported-action"}

    repo_id = message.get("repo_id")
    repo_id = str(repo_id).strip() if repo_id is not None else ""
    issue = _num(message.get("issue"))
    generation = message.get("generation")
    generation = _num(generation) if generation is not None else None
    if generation is not None and generation < 1:
        return {"ok": False, "reason": "bad-generation"}

    criteria = message.get("criteria")
    criteria = str(criteria).strip() if criteria is not None else None
    if criteria is not None and not criteria:
        criteria = None
    if criteria is not None and len(criteria) > CRITERIA_MAX_CHARS:
        return {"ok": False, "reason": "criteria-too-large"}

    missing = [name for name in _REQUIRED_TARGET[action]
               if {"repo_id": repo_id, "issue": issue,
                   "generation": generation,
                   "criteria": criteria}[name] in (None, "")]
    if missing:
        return {"ok": False, "reason": "target-required",
                "missing": missing}

    request_id = message.get("request_id")
    request_id = str(request_id).strip() if request_id else None

    # F09 — the steering mode is a *request*: ``checkpoint`` (default)
    # applies at the next supported role/stage boundary; ``live`` asks
    # for runtime injection, which the service denies unless the
    # configured runtime capability allows it (no unsupported harness
    # is ever dispatched). Other actions carry no mode.
    mode = None
    if action == "steer":
        mode = str(message.get("mode") or "checkpoint").strip().lower()
        if mode not in ("checkpoint", "live"):
            return {"ok": False, "reason": "unsupported-mode"}

    return {"ok": True, "value": {
        "command_id": command_id,
        "actor_ref": actor_ref,
        "chat_ref": chat_ref,
        "action": action,
        "repo_id": repo_id,
        "issue": issue,
        "generation": generation,
        "criteria": criteria,
        "mode": mode,
        "request_id": request_id,
        "received_text": message.get("text"),
    }}


def propose(text):
    """Map a natural-language sentence to a typed command *proposal*
    (fields only — actor/chat/command identity come from the transport
    envelope). Returns ``None`` when no single action or no explicit
    issue target parses — ambiguous prose is never guessed at."""
    if not isinstance(text, str) or not text.strip():
        return None
    body = text.strip()
    action = None
    slash = _SLASH.match(body)
    if slash and slash.group(1) in ACTIONS:
        action = slash.group(1)
        body = body[slash.end():]
    else:
        for pattern, candidate in _NL_ACTION:
            if pattern.search(body):
                action = candidate
                break
    if action is None:
        return None
    proposal = {"action": action}
    repo = _NL_REPO.search(body)
    if repo:
        proposal["repo_id"] = repo.group(1)
    issue = _NL_ISSUE.search(body)
    if issue:
        proposal["issue"] = int(issue.group(1))
    gen = _NL_GEN.search(body)
    if gen:
        proposal["generation"] = int(gen.group(1))
    if action == "steer":
        # The proposal's criteria is the operator's own sentence — the
        # revised instruction they typed. It still passes the typed
        # required-target check, so an unusable sentence is rejected
        # rather than guessed at.
        proposal["criteria"] = body[:CRITERIA_MAX_CHARS]
    return proposal
