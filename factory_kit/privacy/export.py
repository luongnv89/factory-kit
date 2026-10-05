#!/usr/bin/env python3
"""Guarded history export — PRD §5.2/§7.1 (issue #18 / Task 3.4,
F07 A4/A5).

The repo-scoped history export
(:meth:`factory_kit.durable.store.IntakeStore.export_history`) is the
F08 retain/export/delete choice's *export* payload. This module wraps
it in the privacy boundary:

- **Excluded by default (A4):** detail columns — event ``detail``,
  attempt ``usage``, evidence ``checks``/``artifacts``, preview
  ``detail``/``smoke_observed``, control ``detail``, notification
  ``body``/``last_error`` — are dropped outright. The default export
  therefore cannot carry raw code, full logs or private content bodies
  because those columns are simply absent.
- **Expanded only on explicit selection (A4):** ``expanded=True`` is the
  operator's explicit content selection — detail columns come back, but
  every one still passes the §7.1 redaction boundary
  (:func:`factory_kit.events.schema.redact_properties`): sensitive keys
  are dropped and secret-shaped spans rewrite to ``<redacted>``.
- **Canary-free always (A4):** *every* string value in the export —
  expanded or not — passes :data:`~factory_kit.events.schema.SECRET_VALUE_RE`
  substitution, and the payload carries ``secret_scan`` —
  :func:`secret_leaks`' verdict over its own output, so a leaked
  canary would be a self-reporting defect rather than a silent one.
- **Explained (A5):** the payload embeds
  :func:`factory_kit.privacy.retention.describe_retention` so the
  export itself documents what pruning removes and why the identity
  tombstones survive until explicit registration removal.

No centralized telemetry or remote endpoint exists on this path — the
export is a local JSON-serializable dict (A6).
"""

from __future__ import annotations

import json as _json

from factory_kit.events import schema as _schema
from factory_kit.privacy import retention as _retention

__all__ = [
    "DETAIL_COLUMNS",
    "history_export",
    "secret_leaks",
]

#: Columns that carry free-form/content-shaped detail — dropped from the
#: default export outright, and included only under the explicit
#: ``expanded`` selection, still through the §7.1 redactor (A4). The set
#: is deliberately a superset of the retention prune set: anything the
#: retention pass would prune at the audit window is detail-class here
#: too.
DETAIL_COLUMNS = {
    "events": ("detail",),
    "attempt_usage": ("usage",),
    "verification_evidence": ("checks", "artifacts"),
    "preview_records": ("detail", "smoke_observed", "smoke_command"),
    "control_records": ("detail",),
    "notification_outbox": ("body", "last_error"),
    "review_records": ("findings", "artifacts"),
    "publication_intents": ("detail",),
    "merge_intents": ("detail",),
    "approval_requests": ("detail",),
    "approval_decisions": ("detail",),
    "execution_fence": ("termination_detail",),
    "work_queue": ("detail",),
    "alerts": ("detail",),
}


def _scrub(value):
    """Recursively secret-scan a value: every string anywhere in the
    export passes SECRET_VALUE_RE — the canary can never leave (A4)."""
    if isinstance(value, str):
        return _schema.SECRET_VALUE_RE.sub("<redacted>", value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _expand_detail(value):
    """An expanded detail column still passes the §7.1 redactor: JSON
    payloads are parsed and property-redacted; plain strings are
    secret-scanned (A4)."""
    if isinstance(value, dict):
        return _schema.redact_properties(value)
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return _schema.redact_properties(_json.loads(value))
        except ValueError:
            pass
    return _scrub(value)


def _project_row(table, row, *, expanded):
    out = {}
    drop = set(DETAIL_COLUMNS.get(table, ()))
    for key, value in row.items():
        if _schema.SENSITIVE_KEY_RE.search(str(key)):
            continue                     # privacy-boundary keys never leave
        if key in drop:
            if not expanded:
                continue                 # default: column absent entirely
            value = _expand_detail(value)
        else:
            value = _scrub(value)
        out[key] = value
    return out


def history_export(store, authority_key, *, expanded=False,
                   effective=None):
    """The privacy-guarded repo-history export (A4/A5).

    ``expanded=False`` (default) drops every detail column outright;
    ``expanded=True`` — the explicit operator selection — includes them
    through the §7.1 redactor. Either way the payload embeds the
    retention/tombstone explanation and the ``secret_scan`` self-check.
    """
    raw = store.export_history(authority_key)
    tables = {}
    for name, rows in raw.get("tables", {}).items():
        tables[name] = [_project_row(name, r, expanded=expanded)
                        for r in rows]
    payload = {
        "authority_key": raw["authority_key"],
        "exported_at": raw["exported_at"],
        "expanded": bool(expanded),
        "retention": _retention.describe_retention(effective),
        "work_keys": raw.get("work_keys", []),
        "tables": tables,
    }
    leaks = secret_leaks(payload)
    payload["secret_scan"] = "clean" if not leaks else {
        "outcome": "leak", "paths": leaks}
    return payload


def secret_leaks(payload):
    """Walk an export payload for secret-shaped values — the canary
    check (A4). Returns a list of ``table[i].field``-style paths; an
    empty list is the only clean verdict."""
    leaks = []

    def walk(node, path):
        if isinstance(node, str):
            if _schema.SECRET_VALUE_RE.search(node):
                leaks.append(path)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(payload, "$")
    return leaks
