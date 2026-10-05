#!/usr/bin/env python3
"""Versioned manifest migrations — the registry upgrade plans bind to.

PRD §3.2 F10; issue #26 / Task 4.2.

A *versioned* upgrade is only reviewable when the hop from the installed
manifest schema to the candidate's is a recorded, supported path — an
operator can never be asked to accept a migration the recipe does not
name. This module is that registry:

- ``MIGRATIONS`` maps ``(from_version, to_version)`` to the transform
  that rewrites the *parsed* manifest map. A transform returns a new
  map; it never mutates its argument and never performs I/O — the
  caller validates the result through ``schema.validate`` before it is
  trusted.
- ``migration_path`` walks the registry (breadth-first) and returns the
  ordered hop list, ``[]`` for a same-version re-pin, or ``None`` when
  no recorded path exists — unsupported pairs are data, never guesses.
- ``migrate_manifest`` applies a path end-to-end and validates each hop
  against the schema so a mid-chain transform cannot emit a map the
  contract rejects.

Schema v1 is the only supported version today
(``SUPPORTED_SCHEMA_VERSIONS``), so the registry carries no transforms
yet; v1→v1 is the identity hop — a re-pin of kit/skill versions and
manifest content under one schema, which is exactly what the F10
upgrade path exercises. A future schema v2 lands by registering
``MIGRATIONS[(1, 2)]`` here; upgrade plans then enumerate the hop in
``migration_steps`` for review.
"""

from __future__ import annotations

from factory_kit import SUPPORTED_SCHEMA_VERSIONS

__all__ = [
    "MIGRATIONS",
    "MigrationError",
    "migrate_manifest",
    "migration_path",
]


class MigrationError(Exception):
    """No recorded migration path exists, or a transform produced an
    invalid manifest map — a named, review-blocking failure."""


#: Registered schema-version transforms: (from, to) -> fn(raw) -> raw.
#: Empty on purpose — v1 is the only schema. The registry's emptiness is
#: honest: there is no v1→v2 transform because v2 does not exist.
MIGRATIONS = {}


def migration_path(from_version, to_version):
    """Return the ordered hop list ``[(a, b), …]``, ``[]`` for a
    same-version re-pin, or ``None`` when the pair is unsupported.

    Both versions must be in ``SUPPORTED_SCHEMA_VERSIONS`` — migrating
    *to* an unsupported schema is meaningless (its validation would
    fail), and migrating *from* one is out of contract (the install base
    was never supported).
    """
    if from_version not in SUPPORTED_SCHEMA_VERSIONS or \
            to_version not in SUPPORTED_SCHEMA_VERSIONS:
        return None
    if from_version == to_version:
        return []  # identity hop — a re-pin, not a rewrite
    # Breadth-first walk over the registered adjacency.
    frontier = [(from_version, [])]
    seen = {from_version}
    while frontier:
        version, path = frontier.pop(0)
        for (src, dst) in MIGRATIONS:
            if src != version or dst in seen:
                continue
            if dst == to_version:
                return path + [(src, dst)]
            seen.add(dst)
            frontier.append((dst, path + [(src, dst)]))
    return None


def migrate_manifest(raw, to_version):
    """Migrate parsed manifest map ``raw`` to ``to_version``.

    Returns ``(migrated_map, applied_hops)``. Raises
    :class:`MigrationError` when no path exists or a transform returns
    a non-mapping. Validation of the result is the caller's job —
    ``schema.validate`` — so a buggy transform fails loudly at the
    validation boundary instead of persisting.
    """
    if not isinstance(raw, dict):
        raise MigrationError("manifest map must be a mapping")
    from_version = raw.get("factory_kit")
    path = migration_path(from_version, to_version)
    if path is None:
        raise MigrationError(
            f"no recorded migration path {from_version!r} → "
            f"{to_version!r} — supported schema versions: "
            f"{list(SUPPORTED_SCHEMA_VERSIONS)}")
    node = raw
    for src, dst in path:
        transformed = MIGRATIONS[(src, dst)](node)
        if not isinstance(transformed, dict):
            raise MigrationError(
                f"migration {src}→{dst} returned "
                f"{type(transformed).__name__}, not a mapping")
        node = transformed
    return node, path
