#!/usr/bin/env python3
"""Pre-dispatch capability gate — F03/A3 (issue #9 / Task 2.4).

Nothing dispatches until the *configured* execution surface checks out:
the runtime must be the supported one, every required role must name a
model, every allowed tool must resolve on this host, the workspace
capability must be permitted, every approved skill pin must be
satisfied, and each configured model must be authenticated and
available. A failure is a *blocker* — the lane parks the dispatch with
the reason recorded before a single worker session exists (A3: "blocks
before dispatch").

The evaluator is pure: host facts arrive in ``readings`` (the same
shape ``factory_kit.setup.readiness.collect_readings`` produces, plus
``tools``/``models`` maps) so tests inject them deterministically. When
no readings are supplied the gate fails *closed* — an unverified model
or skill set is a blocker, never a pass.
"""

from __future__ import annotations

import shutil

from ..config import schema

__all__ = [
    "REQUIRED_WORKSPACE_CAPABILITIES",
    "dispatch_gate",
    "skill_pin_satisfied",
]

#: Workspace modes the lane may select, in preference order —
#: ``worktree`` first (the spike's isolated-workspace recipe), then
#: ``scratch``. ``workspace.dir-ambient`` is never requested: the
#: manifest already denies it for worker sessions (CFG03).
REQUIRED_WORKSPACE_CAPABILITIES = ("workspace.worktree",
                                   "workspace.scratch")

#: Tools the lane itself needs — present on the host and inside the
#: configured allow policy. ``git`` builds the isolated workspace,
#: ``hermes`` drives the supported kanban claims/transitions.
LANE_TOOLS = ("git", "hermes")


def _blocker(code, detail):
    return {"code": code, "detail": detail}


def skill_pin_satisfied(pin, installed) -> bool:
    """An installed version satisfies an approved pin: exact semver
    (``v`` prefix tolerated) or a commit-SHA containment — the same rule
    the readiness evaluator applies."""
    if installed is None:
        return False
    pin, installed = str(pin).strip(), str(installed).strip()
    if len(pin) == 40 and all(c in "0123456789abcdef" for c in pin):
        return pin == installed
    return pin.lstrip("v") == installed.lstrip("v")


def dispatch_gate(effective, *, readings=None, which=None) -> dict:
    """Evaluate the pre-dispatch gate; return blockers + the selected
    execution surface.

    ``readings`` keys (all optional — absent ⇒ fail closed):

    - ``tools``: ``{name: path-or-None}`` host resolutions
    - ``models``: ``{provider: {"authenticated": bool, "available":
      [model-names] or None}}``
    - ``skills``: ``{skill_id: installed-version}``

    Returns ``{"dispatchable": bool, "blockers": [...],
    "selected": {...}}`` — ``selected`` is the role/model/runtime,
    pinned-skill map, workspace capability and tool allowlist a dispatch
    binds to its attempt record.
    """
    which = which or shutil.which
    blockers = []
    readings = readings or {}

    runtime = (effective.get("runtime") or {})
    name = runtime.get("name")
    if name not in schema.SUPPORTED_RUNTIMES:
        blockers.append(_blocker(
            "unsupported-runtime",
            f"runtime {name!r} not supported — "
            f"{list(schema.SUPPORTED_RUNTIMES)}"))

    # -- roles + configured models --------------------------------------
    roles = runtime.get("roles") or {}
    models = readings.get("models") or {}
    selected_models = {}
    for role in schema.REQUIRED_ROLES:
        spec = roles.get(role) or {}
        model = spec.get("model")
        if not model:
            blockers.append(_blocker(
                "model-unavailable",
                f"role {role}: no model configured"))
            continue
        selected_models[role] = model
        provider, _, model_name = model.partition("/")
        probe = models.get(provider) if provider else None
        if probe is None:
            blockers.append(_blocker(
                "model-unverified",
                f"role {role}: provider {provider or '?'} not probed — "
                "availability unverified"))
            continue
        if probe.get("authenticated") is not True:
            blockers.append(_blocker(
                "model-auth-failed",
                f"role {role}: provider {provider} not authenticated"))
            continue
        available = probe.get("available")
        if probe.get("reachable") is False:
            blockers.append(_blocker(
                "model-unavailable",
                f"role {role}: provider {provider} unreachable"))
            continue
        if available is not None and model_name not in available:
            blockers.append(_blocker(
                "model-unavailable",
                f"role {role}: model {model_name!r} not listed by "
                f"{provider}"))

    # -- tools: configured allowlist + lane requirements -----------------
    tools = runtime.get("tools") or {}
    allow = set(tools.get("allow") or [])
    deny = set(tools.get("deny") or [])
    probed = readings.get("tools") or {}
    missing = []
    for tool in sorted(allow | set(LANE_TOOLS)):
        if tool in deny:
            blockers.append(_blocker(
                "tool-denied",
                f"tool {tool!r} is on the deny list"))
            continue
        path = probed.get(tool) if tool in probed else which(tool)
        if not path:
            missing.append(tool)
    if missing:
        blockers.append(_blocker(
            "missing-tool",
            f"required tools not on PATH: {missing}"))

    # -- capabilities: workspace mode must be permitted -------------------
    caps = runtime.get("capabilities") or {}
    cap_allow = set(caps.get("allow") or [])
    cap_deny = set(caps.get("deny") or [])
    workspace_cap = None
    for cap in REQUIRED_WORKSPACE_CAPABILITIES:
        if cap in cap_allow and cap not in cap_deny:
            workspace_cap = cap
            break
    if workspace_cap is None:
        blockers.append(_blocker(
            "unsupported-capability",
            "no isolated workspace capability allowed — need one of "
            f"{list(REQUIRED_WORKSPACE_CAPABILITIES)}"))
    if "workspace.dir-ambient" in cap_allow and \
            "workspace.dir-ambient" not in cap_deny:
        blockers.append(_blocker(
            "unsupported-capability",
            "workspace.dir-ambient is permitted — ambient-directory "
            "execution is never a lane workspace"))

    # -- pinned skills -----------------------------------------------------
    skills = (effective.get("skills") or {})
    approved = skills.get("approved") or {}
    installed = readings.get("skills") or {}
    unapproved = []
    for skill_id, pin in approved.items():
        if not skill_pin_satisfied(pin, installed.get(skill_id)):
            unapproved.append(
                f"{skill_id}@{pin}"
                f" (installed: {installed.get(skill_id)!r})")
    if unapproved:
        blockers.append(_blocker(
            "skill-not-approved",
            f"approved skill pins unsatisfied: {unapproved}"))
    if skills.get("auto_discover") is not False:
        blockers.append(_blocker(
            "skill-auto-discover",
            "skills.auto_discover must stay false — undiscovered skill "
            "input can never grant authority"))

    # -- endpoint safety: nothing may silently enable forbidden surfaces ----
    disabled = set((effective.get("endpoint") or {})
                   .get("disabled") or [])
    missing_disabled = [d for d in schema.REQUIRED_DISABLED
                        if d not in disabled]
    if missing_disabled:
        blockers.append(_blocker(
            "unsafe-endpoint",
            f"required-disabled capabilities not disabled: "
            f"{missing_disabled}"))

    return {
        "dispatchable": not blockers,
        "blockers": blockers,
        "selected": {
            "runtime": name,
            "models": selected_models,
            "skills": dict(approved),
            "workspace_capability": workspace_cap,
            "tools": sorted(allow),
        },
    }
