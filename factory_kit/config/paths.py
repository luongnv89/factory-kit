#!/usr/bin/env python3
"""Per-profile factory-kit state locations (PRD §6.3 Identity).

Hermes's ``default`` profile home is ``~/.hermes`` itself — creating
``~/.hermes/profiles/default/`` would register a phantom profile in
Hermes's namespace. The kit's per-profile state therefore lives at
``~/.hermes/factory-kit`` for ``default`` and
``~/.hermes/profiles/<profile>/factory-kit`` for named profiles.
"""

from pathlib import Path

HERMES_HOME = Path.home() / ".hermes"


def profile_state_dir(profile) -> Path:
    """The per-profile factory-kit state directory (registration,
    intake store, setup state, driver workspaces)."""
    name = profile.name if isinstance(profile, Path) else str(profile)
    if name == "default":
        return HERMES_HOME / "factory-kit"
    return HERMES_HOME / "profiles" / name / "factory-kit"
