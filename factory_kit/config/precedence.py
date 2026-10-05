#!/usr/bin/env python3
"""``.gitissue.yml`` ↔ ``.factory-kit.yml`` ownership and precedence (A6).

PRD §6.3 (CFG-C01): factory settings live in ``.factory-kit.yml``; existing
IDD settings stay in ``.gitissue.yml`` with **explicit mapping and conflict
detection**. The factory never writes ``.gitissue.yml`` — setup-time
integration is a reviewed, additive diff owned by Task 2.2. This module is
the executable half of that contract:

- ``FACTORY_OWNED``: top-level manifest groups the kit owns.
- ``IDD_OWNED``: first-segment prefixes inside ``.gitissue.yml`` that IDD
  owns (per IDD's config-schema: ``platform``, ``issue``, ``resolve``,
  ``review``, ``projects``, ``security``, ``agents``, ``triage``,
  ``autopilot``).

``check`` fails — never merges — when either file reaches across the line:

- a factory manifest key in the IDD-owned namespace is rejected as
  IDD-owned (the manifest cannot override IDD behaviour);
- a ``.gitissue.yml`` key whose first segment is factory-owned is rejected
  as factory-owned (factory settings do not belong there — and the factory
  will not silently pick one of two competing values).

The report returned by ``check``/``mapping_report`` explains *why* in
ownership terms, which is what A6 requires of a conflict failure.
"""

from __future__ import annotations

from . import yamlmini

__all__ = [
    "FACTORY_OWNED",
    "IDD_OWNED",
    "PrecedenceError",
    "check",
    "mapping_report",
    "SHARED_CONCEPTS",
]

#: Top-level groups owned by ``.factory-kit.yml`` (schema v1).
FACTORY_OWNED = (
    "factory_kit",
    "identity",
    "registration",
    "authorization",
    "runtime",
    "skills",
    "verification",
    "limits",
    "evidence",
    "secrets",
    "endpoint",
)

#: First-segment namespaces owned by IDD inside ``.gitissue.yml``.
IDD_OWNED = (
    "platform",
    "issue",
    "resolve",
    "review",
    "projects",
    "security",
    "agents",
    "triage",
    "autopilot",
)

#: Concepts both files talk about but where ownership is explicit rather
#: than shared: each row documents which file is authoritative and why, so
#: a future overlap is a conflict to explain, not a silent precedence pick.
SHARED_CONCEPTS = {
    "secrets": (
        "factory",
        ".factory-kit.yml secrets:* are approved-storage references for "
        "the kit; .gitissue.yml security.* still owns IDD-side scan "
        "policy — neither writes the other",
    ),
    "skills": (
        "factory",
        ".factory-kit.yml skills.approved pins the IDD bundle revisions "
        "the recipe uses; the skills themselves remain IDD-owned",
    ),
    "limits": (
        "factory",
        "attempt/wall limits are factory contract; resolve.test_timeout "
        "and friends remain IDD-owned runner knobs",
    ),
}


class PrecedenceError(Exception):
    """Ownership conflict between the manifest and ``.gitissue.yml``.

    ``conflicts`` is a list of dicts — ``key``, ``owner`` (``"factory"`` or
    ``"idd"``), ``found_in`` and ``message`` — one per offending key.
    """

    def __init__(self, conflicts):
        self.conflicts = list(conflicts)
        detail = "; ".join(c["message"] for c in self.conflicts[:5])
        if len(self.conflicts) > 5:
            detail += f"; … and {len(self.conflicts) - 5} more"
        super().__init__(f"config ownership conflict — {detail}")


def check(manifest: dict, gitissue: dict) -> dict:
    """Validate ownership boundaries between the two parsed configs.

    ``manifest`` is the parsed ``.factory-kit.yml`` mapping; ``gitissue``
    the parsed ``.gitissue.yml`` mapping (nested or already-flattened
    dotted keys both work). Returns a mapping report on success; raises
    :class:`PrecedenceError` listing every conflict on failure. Neither
    input is mutated — this is a read-only check; ``.gitissue.yml`` is
    never written by the factory.
    """
    conflicts = []
    manifest = manifest or {}
    gitissue_flat = yamlmini.flatten(gitissue or {})

    for key in manifest:
        if key in IDD_OWNED:
            conflicts.append({
                "key": key,
                "owner": "idd",
                "found_in": ".factory-kit.yml",
                "message":
                    f"'{key}' is IDD-owned (.gitissue.yml) — the factory "
                    "manifest must not set or override it",
            })

    for dotted in gitissue_flat:
        head = dotted.split(".", 1)[0]
        if head in FACTORY_OWNED:
            conflicts.append({
                "key": dotted,
                "owner": "factory",
                "found_in": ".gitissue.yml",
                "message":
                    f"'{dotted}' is factory-owned — it belongs in "
                    ".factory-kit.yml; .gitissue.yml is IDD-owned and the "
                    "factory never writes it",
            })

    if conflicts:
        raise PrecedenceError(conflicts)
    return mapping_report(manifest, gitissue_flat)


def mapping_report(manifest: dict, gitissue_flat: dict) -> dict:
    """The explicit mapping A6 requires: which file owns what."""
    return {
        "factory_owned": {
            key: "owned by .factory-kit.yml (schema v1)"
            for key in sorted(FACTORY_OWNED) if key in (manifest or {})
        },
        "idd_owned": {
            key: "owned by IDD (.gitissue.yml) — left untouched"
            for key in sorted({d.split(".", 1)[0] for d in gitissue_flat})
            if key in IDD_OWNED
        },
        "shared_concepts": dict(SHARED_CONCEPTS),
        "rule": ".gitissue.yml is never written by the factory; "
                "conflicting keys fail with the owning file named",
    }
