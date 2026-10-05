#!/usr/bin/env python3
"""Typed event contracts — PRD §7.1 (issue #14 / Task 2.9, F07).

One closed vocabulary for the local evidence/usage/effort trail.
Producers append through
:meth:`factory_kit.durable.store.IntakeStore.record_event` /
``record_typed_event``; this module owns:

- ``EVENT_SCHEMAS`` — the required properties per event kind. A missing
  required property is a defect (``IntakeStoreError``), never a silently
  persisted half-record. Fields named in ``nullable`` are the
  declared-known unknowns: absent or ``None`` both mean *unmeasured* —
  missing usage/effort stays unknown, never zero-filled (A2/A3).
- ``normalize_usage`` — the measured-usage shape (A3): token counts
  carry ``provider``/``unit`` attribution, actual billed monetary
  values keep their ``currency``, subscription quota indicators sit in
  a separate field, and queue / model-execution / CI-wait / human-wait
  durations are tracked separately. Anything the runtime did not
  report stays ``None`` — never an invented estimate.
- ``redact_properties`` / ``aggregate_properties`` — the §7.1 privacy
  boundary (A5/A6): no issue bodies, code, full logs, chat text,
  usernames, tokens or other secrets in event properties or exports.
  Raw numeric actor IDs (``telegram:123`` style) remain restricted
  references — opaque identifiers, never expanded to names.

Nothing here performs I/O; the store owns durability.
"""

from __future__ import annotations

import re

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "EVENT_SCHEMAS",
    "INTERVENTION_CATEGORIES",
    "USAGE_DURATION_FIELDS",
    "SENSITIVE_KEY_RE",
    "SECRET_VALUE_RE",
    "validate_event",
    "normalize_usage",
    "redact_properties",
    "aggregate_properties",
    "is_aggregate_safe",
]

#: Schema version carried into every diagnostic export/report (A7) so a
#: seeded fixture's measurements stay reproducible across upgrades.
EVENT_SCHEMA_VERSION = 1

#: Fixed vocabulary for ``operator_effort_recorded`` intervention
#: categories (A2) — an enumerable set, so a category can never carry
#: free text (and with it, private content) into the event trail.
INTERVENTION_CATEGORIES = (
    "baseline",            # the unassisted comparison effort
    "approval",            # review/approve factory output
    "correction",          # fix or redirect factory output
    "steering",            # scope/criteria changes mid-flight
    "monitoring",          # watching status/diagnostics
    "investigation",       # diagnosing a failure/park/quarantine
    "cleanup",             # removal/repair operations
    "other",
)

#: The four wait classes §7.1 requires to be tracked separately (A3).
USAGE_DURATION_FIELDS = (
    "queue_seconds",
    "model_execution_seconds",
    "ci_wait_seconds",
    "human_wait_seconds",
)

# --------------------------------------------------------------------- #
# §7.1 event vocabulary — required properties per kind.
#
# ``required`` fields must be present and non-null (identifiers,
# digests, verdicts — the fields §7.1 lists per event). ``nullable``
# fields are the honest-unknown contract: a runtime that measured
# nothing records ``null`` or omits the key, and the summary layer
# reports "unknown", never zero or an invented estimate.
# ``required_any`` lists alternatives — at least one must be present
# (e.g. a pass-scoped ``recovery_completed`` has no work-level action).
# --------------------------------------------------------------------- #

EVENT_SCHEMAS = {
    # Task 2.1/2.2 — readiness completes (SetupStore mirrors this kind).
    "setup_checked": {
        "required": ("project_id", "version_set", "verdict"),
    },
    "setup_applied": {
        "required": ("plan_digest", "outcome"),
    },
    # Task 2.3 — intake decision events.
    "work_accepted": {
        "required": ("delivery_id",),
    },
    "work_rejected": {
        "required": ("delivery_id", "reason"),
    },
    "delivery_deduplicated": {
        "required": ("delivery_id", "work_key"),
    },
    "work_reconciled": {"required": ()},
    "work_updated": {"required": ("work_key",)},
    "work_parked": {"required": ("reason",)},
    "work_fenced": {"required": ("reason",)},
    "work_paused": {"required": ()},
    "work_resumed": {"required": ()},
    "work_quarantined": {"required": ()},
    # Task 2.4 — role execution boundaries (identity + runtime/model).
    "attempt_started": {
        "required": ("task_id", "attempt_id", "generation", "role"),
        "nullable": ("runtime", "model"),
    },
    "attempt_finished": {
        "required": ("attempt_id", "role", "verdict", "outcome"),
        "nullable": ("task_id", "generation", "duration_s", "usage"),
    },
    "result_rejected": {"required": ("reason",)},
    "dispatch_blocked": {"required": ("reason",)},
    "lane_finished": {"required": ()},
    "lane_recovered": {"required": ()},
    # Task 2.5 — serialized publication intents.
    "publication_intent_recorded": {"required": ("intent_id",)},
    "publication_intent_reopened": {"required": ("intent_id",)},
    "publication_intent_applied": {"required": ("intent_id",)},
    # Task 2.6 — evidence observations + review records.
    "evidence_checked": {
        "required": ("evidence_id", "status"),
        "nullable": ("pr", "head_sha", "base_sha", "checks",
                     "review_id", "review_session", "observed_at"),
    },
    "review_recorded": {
        "required": ("review_id", "attempt_id", "session_id",
                     "verdict", "sha"),
    },
    "review_rejected": {"required": ("reason",)},
    # Task 2.7 — restricted-actor control records.
    "control_recorded": {
        "required": ("command_id", "actor_ref", "action", "outcome"),
        "nullable": ("chat_ref", "reason"),
    },
    # Task 2.8 — restart/reconciliation outcomes. A work-scoped row
    # carries ``action``; the pass-scoped row carries ``scope``.
    "recovery_completed": {
        "required": ("source",),
        "required_any": ("action", "scope"),
        "nullable": ("task_id", "attempt_id", "reused_identity"),
    },
    # Task 2.9 — operator-supplied effort (F07). ``active_minutes`` /
    # ``category`` are *supplied* values: absent runtime-side effort
    # stays null (unknown) rather than zero.
    "operator_effort_recorded": {
        "required": ("effort_id", "work_key", "actor_ref",
                     "recorded_at"),
        "nullable": ("active_minutes", "category", "source",
                     "supersedes"),
    },
    # Task 2.9 — recorded participant agreement gating aggregate pilot
    # export (A6): the scope, the restricted actor reference and the
    # commit time, never a free-text consent transcript.
    "participant_agreement_recorded": {
        "required": ("scope", "actor_ref", "recorded_at"),
    },
    # §7.1 kinds reserved for Sprint-3 producers (preview/approval/
    # merge/notifications) — declared now so the vocabulary is closed.
    "notification_delivered": {
        "required": ("notification_id", "destination_ref"),
        "nullable": ("retries",),
    },
    "notification_failed": {
        "required": ("notification_id", "destination_ref", "reason"),
        "nullable": ("retries",),
    },
    "preview_verified": {
        "required": ("deployment_id", "head_sha", "observed_at"),
    },
    "preview_failed": {
        "required": ("deployment_id", "head_sha", "observed_at",
                     "reason"),
    },
    "approval_decided": {
        "required": ("request_id", "actor_ref", "action", "outcome"),
    },
    "approval_invalidated": {
        "required": ("request_id", "reason"),
    },
    "merge_observed": {
        "required": ("intent_id", "expected_head", "outcome"),
        "nullable": ("merge_sha",),
    },
}


def _present(props, name):
    return name in props and props[name] is not None \
        and props[name] != ""


def validate_event(kind, properties):
    """Validate ``properties`` against the schema for ``kind``.

    Returns ``{"ok": True}`` or ``{"ok": False, "reason": …,
    "missing": […]}``. An unregistered kind fails — the vocabulary is
    closed so a producer cannot drift into an unmeasured event shape.
    ``nullable`` fields need only be present as keys; ``None`` is the
    recorded *unknown*.
    """
    schema = EVENT_SCHEMAS.get(kind)
    if schema is None:
        return {"ok": False, "reason": "unknown-event-kind",
                "missing": []}
    props = dict(properties or {})
    missing = []
    for field in schema.get("required", ()):
        if not _present(props, field):
            missing.append(field)
    # ``nullable`` fields are declared-known unknowns: they may be
    # absent or ``None`` — either spells "unmeasured", and a correction
    # event that changes only one field is still valid (A1). They are
    # documented in the schema, never enforced-as-present.
    any_of = schema.get("required_any", ())
    if any_of and not any(_present(props, f) for f in any_of):
        missing.append("|".join(any_of))
    if missing:
        return {"ok": False, "reason": "missing-fields",
                "missing": missing}
    return {"ok": True}


# --------------------------------------------------------------------- #
# Usage normalization (A3) — measured-or-unknown, separately fielded
# --------------------------------------------------------------------- #

def _norm_token_counts(value):
    """Token counts with provider/unit attribution.

    Accepts a bare count (``123``), a single mapping, or a list of
    per-provider mappings. Always returns ``None`` (absent) or a list of
    ``{"count", "provider", "unit"}`` dicts — attribution fields stay
    ``None`` when the runtime did not report them.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [{"count": value, "provider": None, "unit": "tokens"}]
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            out.append({"count": item, "provider": None,
                        "unit": "tokens"})
        elif isinstance(item, dict):
            out.append({
                "count": item.get("count", item.get("tokens")),
                "provider": item.get("provider"),
                "unit": item.get("unit", "tokens"),
            })
        else:
            out.append({"count": None, "provider": None, "unit": None})
    return out or None


def _norm_billed(value):
    """Actual billed monetary value — ``amount`` + ``currency`` only
    when the provider reported them; never an estimated figure."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"amount": value, "currency": None}
    if isinstance(value, dict):
        return {"amount": value.get("amount"),
                "currency": value.get("currency")}
    return {"amount": None, "currency": None}


def _norm_quota(value):
    """Subscription quota indicator — its own field, never merged into
    token counts or billed amounts."""
    if value is None:
        return None
    if isinstance(value, dict):
        return {"kind": value.get("kind"),
                "used": value.get("used"),
                "limit": value.get("limit"),
                "remaining": value.get("remaining")}
    return None


def _norm_durations(usage):
    """Queue / model-execution / CI-wait / human-wait seconds, each a
    separate field; an unmeasured wait stays ``None``."""
    src = usage.get("durations") if isinstance(
        usage.get("durations"), dict) else {}
    out = {}
    for field in USAGE_DURATION_FIELDS:
        val = src.get(field, usage.get(field))
        out[field] = float(val) if isinstance(val, (int, float)) \
            and not isinstance(val, bool) else None
    return out


def normalize_usage(usage):
    """Canonical per-attempt usage shape (A3).

    Input is whatever the runtime returned: ``None`` stays ``None``
    (usage unknown — never zero-filled); a mapping is normalized to
    ``{"tokens": [...], "billed": {...}, "quota": {...},
    "durations": {…}}`` with each absent piece ``None``. Unknown extra
    keys are preserved under ``extra`` so an attributed field is never
    silently dropped.
    """
    if usage is None:
        return None
    if not isinstance(usage, dict):
        return {"tokens": None, "billed": None, "quota": None,
                "durations": _norm_durations({}),
                "extra": {"raw_type": type(usage).__name__}}
    known = {"tokens", "billed", "billing", "quota", "durations",
             *USAGE_DURATION_FIELDS}
    extra = {k: v for k, v in usage.items() if k not in known}
    out = {
        "tokens": _norm_token_counts(
            usage.get("tokens", usage.get("token_counts"))),
        "billed": _norm_billed(
            usage.get("billed", usage.get("billing"))),
        "quota": _norm_quota(usage.get("quota")),
        "durations": _norm_durations(usage),
    }
    if extra:
        out["extra"] = extra
    return out


# --------------------------------------------------------------------- #
# Redaction (A5/A6) — the §7.1 privacy boundary
# --------------------------------------------------------------------- #

#: Property names that may never survive into an export or an aggregate
#: event: content bodies, code, logs, chat text, usernames and secret
#: material. Matching is on the key, not the value — a producer cannot
#: sneak ``issue_body`` through by keeping it short. Deliberately
#: word-boundaried so *restricted references* survive: ``actor_ref``,
#: ``chat_ref``/``chat_id`` (raw numeric IDs stay opaque refs, A6),
#: ``blocker_codes``, and the ``tokens`` usage-count field are not
#: content and must not be dropped.
SENSITIVE_KEY_RE = re.compile(
    r"(?i)(^|_)(body|bodies|content|contents|code|diff|patch|snippet|"
    r"logs?|username|user_name|login|handle|screen_name|token|"
    r"secrets?|password|passwd|credentials?|signature|private_key|"
    r"api_?keys?|messages?|msg|texts?|transcripts?|attachments?|blob)"
    r"($|_)|(^|_)chats?(_(?!ref\b|id\b)|$)")

#: Secret-shaped values — the same prefixes the pre-commit gate blocks.
#: Redaction rewrites the matched span to ``<redacted>`` rather than
#: dropping the key, so the export still shows a value was recorded.
SECRET_VALUE_RE = re.compile(
    r"(sk-(proj-)?[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]{20,}|"
    r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|"
    r"gho_[A-Za-z0-9]{30,}|ghs_[A-Za-z0-9]{30,}|ghu_[A-Za-z0-9]{30,}|"
    r"github_pat_[A-Za-z0-9_]{40,}|xox[abprs]-[A-Za-z0-9-]{10,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,})")

#: Free-text ceiling for aggregate properties — an aggregate scalar
#: longer than this is a content leak, not a measurement (A6).
_AGGREGATE_STR_MAX = 200


def _redact_string(value):
    if not isinstance(value, str):
        return value
    return SECRET_VALUE_RE.sub("<redacted>", value)


def redact_properties(properties):
    """Return ``properties`` minus the privacy boundary's keys.

    Keys matching :data:`SENSITIVE_KEY_RE` are dropped outright (no
    bodies/code/logs/chat/usernames/secrets — A5/A6). String values
    that still match :data:`SECRET_VALUE_RE` have the secret span
    rewritten to ``<redacted>`` — an *expanded* export still redacts
    secrets (A5). Containers are walked recursively.
    """
    if not isinstance(properties, dict):
        return properties
    out = {}
    for key, value in properties.items():
        if SENSITIVE_KEY_RE.search(str(key)):
            continue
        out[key] = _redact_value(value)
    return out


def _redact_value(value):
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return redact_properties(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return value


def _aggregate_safe_value(value):
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return len(value) <= _AGGREGATE_STR_MAX
    if isinstance(value, (list, tuple)):
        return all(_aggregate_safe_value(v) for v in value)
    if isinstance(value, dict):
        return all(
            not SENSITIVE_KEY_RE.search(str(k))
            and _aggregate_safe_value(v)
            for k, v in value.items())
    return False


def is_aggregate_safe(properties):
    """True when every property could ride an aggregate pilot export
    (A6): no sensitive keys, no secret-shaped values, no long free
    text."""
    if not isinstance(properties, dict):
        return False
    for key, value in properties.items():
        if SENSITIVE_KEY_RE.search(str(key)):
            return False
        if not _aggregate_safe_value(value):
            return False
        if isinstance(value, str) and SECRET_VALUE_RE.search(value):
            return False
    return True


def aggregate_properties(properties):
    """The aggregate-safe projection of an event's properties (A6):
    redacted, sensitive keys removed, and any remaining unsafe value
    (long free text, nested sensitive keys) dropped. Raw numeric actor
    IDs remain restricted references — ``actor_ref``/``chat_ref`` pass
    through unchanged, never resolved to names."""
    redacted = redact_properties(properties)
    out = {}
    for key, value in redacted.items():
        if _aggregate_safe_value(value):
            if isinstance(value, str) and SECRET_VALUE_RE.search(value):
                out[key] = SECRET_VALUE_RE.sub("<redacted>", value)
            else:
                out[key] = value
    return out
