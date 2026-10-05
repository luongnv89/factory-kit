#!/usr/bin/env python3
"""``.factory-kit.yml`` manifest schema — validation, defaults and digests.

Finalizes the spike-proven configuration contract (PRD §6.3 CFG01–CFG09,
CFG-C01/C02; issue #6 / Task 2.1) for the recipe selected in Sprint 1
(``docs/decisions/tested-recipe-selection.md``):

- runtime: the Hermes Kanban profile worker — the only paved worker path
- preview provider: Vercel preview deployments (never production)
- merge: ``gh pr merge --squash`` by the human-approved merge owner
- package: Python 3 stdlib-only native plugin ``factory-kit``

Every check below is configuration validation, not a measured result: the
numerical defaults (1 active task, 2 implementation attempts, 60 active
worker minutes, 24 wall hours, 60-minute approval expiry, 10-minute maximum
merge smoke age, 7-day worker logs, 30-day detailed audit) remain proposed
under Q9 and are bounded here, not claimed as measurements.

Validation is deny-by-default and fail-explicit: unknown fields, missing
roles, unpinned skills, incompatible versions, literal secrets and
worker-controllable escape hatches all fail with actionable errors naming
the offending path.
"""

from __future__ import annotations

import hashlib
import json
import re

from factory_kit import SUPPORTED_SCHEMA_VERSIONS
from . import yamlmini

__all__ = [
    "ConfigError",
    "SCHEMA_VERSION",
    "SUPPORTED_RUNTIMES",
    "SUPPORTED_PREVIEW_PROVIDERS",
    "SUPPORTED_MERGE_METHODS",
    "REQUIRED_ROLES",
    "REQUIRED_DISABLED",
    "SECRET_REF_SCHEMES",
    "DEFAULT_LIMITS",
    "LIMIT_BOUNDS",
    "authority_key",
    "effective_digest",
    "policy_digest",
    "is_execution_authorized",
    "load_manifest",
    "load_manifest_file",
    "validate",
]

SCHEMA_VERSION = 1

#: The single supported worker runtime (spike boundary map: the Kanban
#: profile worker is the only paved path; external CLI lanes are no-go).
SUPPORTED_RUNTIMES = ("hermes-kanban",)

#: The one preview provider selected by Q10 — production deploys are out of
#: scope for the MVP endpoint.
SUPPORTED_PREVIEW_PROVIDERS = ("vercel",)

#: The one merge method selected by Q10.
SUPPORTED_MERGE_METHODS = ("squash",)

#: Roles that must each declare an available model (§6.3 Runtime and roles).
REQUIRED_ROLES = ("implementation", "review")

#: Capabilities permanently off for the MVP; the schema carries no key that
#: can enable them, so worker input can never turn them on (A4).
REQUIRED_DISABLED = (
    "production_deploy",
    "package_publish",
    "autonomous_merge",
)

#: Approved secret-storage reference schemes (CFG08; Q5 mechanism +
#: documented hardening: hermes secrets → Bitwarden/1Password, vault).
SECRET_REF_SCHEMES = ("env", "hermes-secrets", "bw", "op", "vault")
_SECRET_REF_RE = re.compile(
    r"^(env|hermes-secrets|bw|op|vault):[A-Za-z0-9_][A-Za-z0-9_./\-]*$")

#: Literal-secret canaries rejected under ``secrets:`` (CFG08). These mirror
#: the pre-commit security gate's known-prefix approach plus generic blob
#: shapes; fixtures/tests build canary strings programmatically so no
#: real-looking token is ever committed.
SECRET_CANARY_RES = tuple(
    re.compile(p) for p in (
        r"sk-(proj-)?[A-Za-z0-9_\-]{16,}",
        r"sk_live_[A-Za-z0-9]{16,}",
        r"AKIA[0-9A-Z]{16}",
        r"ASIA[0-9A-Z]{16}",
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"github_pat_[A-Za-z0-9_]{30,}",
        r"xox[abprs]-[A-Za-z0-9\-]{10,}",
        r"glpat-[A-Za-z0-9_\-]{16,}",
        r"AIza[0-9A-Za-z_\-]{30,}",
        r"-{3,}\s*BEGIN\s+[A-Z ]*PRIVATE\s+KEY",
        r"[A-Fa-f0-9]{40,}",          # raw hex key/token blobs
        r"[A-Za-z0-9+/]{48,}={0,2}",  # long base64 blobs
    )
)

#: Q9-proposed defaults, validated as configuration (Task 2.1 notes).
DEFAULT_LIMITS = {
    "active_tasks": 1,
    "implementation_attempts": 2,
    "active_worker_minutes": 60,
    "wall_hours": 24,
}

#: Bounded configurability — a limit outside its bound fails validation.
LIMIT_BOUNDS = {
    "active_tasks": (1, 8),
    "implementation_attempts": (1, 5),
    "active_worker_minutes": (5, 480),
    "wall_hours": (1, 168),
}

DEFAULT_EVIDENCE = {
    "observation_freshness_minutes": 15,
    "event_retention_days": 30,
    "worker_log_retention_days": 7,
    "audit_retention_days": 30,
}
_EVIDENCE_BOUNDS = {
    "observation_freshness_minutes": (1, 1440),
    "event_retention_days": (1, 365),
    # Floors are the contract itself: worker logs keep ≥7 days, detailed
    # audit ≥30 — retention may lengthen, never shrink below them (A5).
    "worker_log_retention_days": (7, 30),
    "audit_retention_days": (30, 3650),
}
#: §7.1: these fields never belong in aggregate export; the manifest can
#: only widen the set, never drop a required redaction.
REQUIRED_REDACTIONS = (
    "issue_body",
    "code",
    "usernames",
    "tokens",
    "chat_text",
)

DEFAULT_ENDPOINT = {
    "approval_expiry_minutes": 60,
    "max_smoke_age_minutes": 10,
}
_ENDPOINT_BOUNDS = {
    "approval_expiry_minutes": (15, 120),
    "max_smoke_age_minutes": (1, 30),
}

#: Every key the schema knows, per group. Anything outside these sets is an
#: unknown field and fails (A2) — including would-be escape hatches such as
#: ``endpoint.production_deploy`` or ``skills.auto_discover: true`` (handled
#: as a known key that must stay false, so the error names the policy).
_TOP_LEVEL_KEYS = {
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
}
_GROUP_KEYS = {
    "identity": {"repo_id", "owner", "name"},
    "registration": {"owner"},
    "authorization": {
        "execution_opt_in", "github_actors", "github_roles",
        "telegram_users", "telegram_chats",
    },
    "runtime": {"name", "roles", "tools", "capabilities"},
    "skills": {"approved", "auto_discover"},
    "verification": {"acceptance_commands", "required_checks"},
    "limits": set(DEFAULT_LIMITS),
    "evidence": set(DEFAULT_EVIDENCE) | {"export", "tombstones"},
    "endpoint": {"preview", "merge", "disabled"},
}
_SUB_KEYS = {
    "runtime.roles": {"model", "profile"},
    "runtime.tools": {"allow", "deny"},
    "runtime.capabilities": {"allow", "deny"},
    "verification.required_checks": {"provider", "contexts", "conclusions"},
    "evidence.export": {"aggregate_only", "redact"},
    "evidence.tombstones": {"retain_until_registration_removal"},
    "endpoint.preview": {"provider", "environment"},
    "endpoint.merge": {
        "method", "approval_expiry_minutes", "max_smoke_age_minutes",
    },
}

_REVISION_RE = re.compile(
    r"^(v?[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.\-]+)?|[0-9a-f]{40})$")


class ConfigError(Exception):
    """Manifest validation failed. ``problems`` is a list of dicts with
    ``path`` and ``message`` — one actionable entry per defect."""

    def __init__(self, problems):
        self.problems = list(problems)
        summary = "; ".join(
            f"{p['path']}: {p['message']}" for p in self.problems[:5])
        if len(self.problems) > 5:
            summary += f"; … and {len(self.problems) - 5} more"
        super().__init__(
            f"invalid .factory-kit.yml — {summary or 'no detail'}")


def _err(problems, path, message):
    problems.append({"path": path, "message": message})


def _is_int(value) -> bool:
    return type(value) is int  # bool is an int subclass; never numeric here


def _require_map(problems, node, path):
    if not isinstance(node, dict):
        _err(problems, path, f"must be a mapping, got "
                             f"{type(node).__name__}")
        return None
    return node


def _require_list(problems, node, path, *, allow_empty=False):
    if not isinstance(node, list):
        _err(problems, path, f"must be a list, got {type(node).__name__}")
        return None
    if not allow_empty and not node:
        _err(problems, path, "must not be empty")
        return None
    return node


def _require_str(problems, node, path):
    if not isinstance(node, str) or not node.strip():
        _err(problems, path, "must be a non-empty string")
        return None
    return node


def _check_unknown(problems, node, path, known):
    if isinstance(node, dict):
        for key in node:
            if key not in known:
                _err(problems, f"{path}.{key}" if path else key,
                     "unknown field — remove it (manifest schema "
                     f"{SCHEMA_VERSION})")


def _check_bounded(problems, node, path, defaults, bounds):
    for key, (lo, hi) in bounds.items():
        value = node.get(key, defaults[key])
        if not _is_int(value):
            _err(problems, f"{path}.{key}", "must be an integer")
        elif not lo <= value <= hi:
            _err(problems, f"{path}.{key}",
                 f"must be between {lo} and {hi} (got {value})")


# --------------------------------------------------------------------------
# Group validators — each appends to ``problems`` and never raises directly.
# --------------------------------------------------------------------------

def _v_identity(problems, node):
    node = _require_map(problems, node, "identity")
    if node is None:
        return
    _check_unknown(problems, node, "identity", _GROUP_KEYS["identity"])
    _require_str(problems, node.get("repo_id"), "identity.repo_id")
    _require_str(problems, node.get("owner"), "identity.owner")
    _require_str(problems, node.get("name"), "identity.name")


def _v_registration(problems, node):
    node = _require_map(problems, node, "registration")
    if node is None:
        return
    _check_unknown(problems, node, "registration",
                   _GROUP_KEYS["registration"])
    _require_str(problems, node.get("owner"), "registration.owner")


def _v_authorization(problems, node):
    node = _require_map(problems, node, "authorization")
    if node is None:
        return
    _check_unknown(problems, node, "authorization",
                   _GROUP_KEYS["authorization"])
    opt_in = node.get("execution_opt_in", False)
    if type(opt_in) is not bool:
        _err(problems, "authorization.execution_opt_in",
             "must be true or false (deny by default when absent)")
    for key in ("github_actors", "github_roles"):
        items = _require_list(problems, node.get(key, []),
                              f"authorization.{key}", allow_empty=True)
        if items:
            for i, item in enumerate(items):
                if not isinstance(item, str) or not item:
                    _err(problems, f"authorization.{key}[{i}]",
                         "must be a non-empty string")
    # CFG02: Telegram allowlists are numeric IDs only — a username string
    # can never authorize. Empty/absent lists deny everyone (default).
    for key in ("telegram_users", "telegram_chats"):
        items = _require_list(problems, node.get(key, []),
                              f"authorization.{key}", allow_empty=True)
        if items:
            for i, item in enumerate(items):
                if not _is_int(item):
                    _err(problems, f"authorization.{key}[{i}]",
                         f"must be a numeric Telegram ID, got {item!r}")


def _v_runtime(problems, node):
    node = _require_map(problems, node, "runtime")
    if node is None:
        return
    _check_unknown(problems, node, "runtime", _GROUP_KEYS["runtime"])
    name = node.get("name")
    if name not in SUPPORTED_RUNTIMES:
        _err(problems, "runtime.name",
             f"unsupported runtime {name!r} — supported: "
             f"{', '.join(SUPPORTED_RUNTIMES)}")
    roles = _require_map(problems, node.get("roles"), "runtime.roles")
    if roles is not None:
        for role in REQUIRED_ROLES:
            spec = roles.get(role)
            if spec is None:
                _err(problems, f"runtime.roles.{role}",
                     "missing required role — declare an available model "
                     "per implementation/review role")
                continue
            spec = _require_map(problems, spec, f"runtime.roles.{role}")
            if spec is not None:
                _check_unknown(problems, spec, f"runtime.roles.{role}",
                               _SUB_KEYS["runtime.roles"])
                _require_str(problems, spec.get("model"),
                             f"runtime.roles.{role}.model")
        for role in roles:
            if role not in REQUIRED_ROLES:
                _err(problems, f"runtime.roles.{role}",
                     f"unknown role — supported roles: "
                     f"{', '.join(REQUIRED_ROLES)}")
    for group in ("tools", "capabilities"):
        policy = node.get(group)
        if policy is None:
            _err(problems, f"runtime.{group}",
                 "missing tool/capability policy — declare allow/deny lists")
            continue
        policy = _require_map(problems, policy, f"runtime.{group}")
        if policy is None:
            continue
        _check_unknown(problems, policy, f"runtime.{group}",
                       _SUB_KEYS[f"runtime.{group}"])
        for side in ("allow", "deny"):
            items = _require_list(problems, policy.get(side, []),
                                  f"runtime.{group}.{side}",
                                  allow_empty=True)
            if items:
                for i, item in enumerate(items):
                    if not isinstance(item, str) or not item:
                        _err(problems, f"runtime.{group}.{side}[{i}]",
                             "must be a non-empty string")


def _v_skills(problems, node):
    node = _require_map(problems, node, "skills")
    if node is None:
        return
    _check_unknown(problems, node, "skills", _GROUP_KEYS["skills"])
    if node.get("auto_discover", False) is not False:
        _err(problems, "skills.auto_discover",
             "must be false — newly discovered skills never execute "
             "without an approved pin (CFG04)")
    approved = _require_map(problems, node.get("approved"),
                            "skills.approved")
    if approved is None:
        return
    if not approved:
        _err(problems, "skills.approved",
             "must name at least one approved skill with an immutable "
             "revision")
    for skill_id, revision in approved.items():
        if not isinstance(revision, str) or \
                not _REVISION_RE.match(revision.strip()):
            _err(problems, f"skills.approved.{skill_id}",
                 f"unpinned revision {revision!r} — pin an immutable "
                 "revision (semver or commit SHA), never latest/*")


def _v_verification(problems, node):
    node = _require_map(problems, node, "verification")
    if node is None:
        return
    _check_unknown(problems, node, "verification",
                   _GROUP_KEYS["verification"])
    commands = _require_list(problems, node.get("acceptance_commands"),
                             "verification.acceptance_commands")
    if commands:
        for i, command in enumerate(commands):
            if not isinstance(command, str) or not command.strip():
                _err(problems, f"verification.acceptance_commands[{i}]",
                     "must be a non-empty command string")
    checks = _require_map(problems, node.get("required_checks"),
                          "verification.required_checks")
    if checks is None:
        # CFG05: the contract must exist even when the project declares no
        # protected checks — absence of the whole block is the empty
        # contract and fails.
        _err(problems, "verification.required_checks",
             "required — an empty verification contract is rejected even "
             "with no protected checks")
        return
    _check_unknown(problems, checks, "verification.required_checks",
                   _SUB_KEYS["verification.required_checks"])
    _require_str(problems, checks.get("provider"),
                 "verification.required_checks.provider")
    for key in ("contexts", "conclusions"):
        items = _require_list(
            problems, checks.get(key),
            f"verification.required_checks.{key}")
        if items:
            for i, item in enumerate(items):
                if not isinstance(item, str) or not item:
                    _err(problems,
                         f"verification.required_checks.{key}[{i}]",
                         "must be a non-empty string")


def _v_limits(problems, node):
    node = _require_map(problems, node, "limits")
    if node is None:
        return
    _check_unknown(problems, node, "limits", _GROUP_KEYS["limits"])
    _check_bounded(problems, node, "limits", DEFAULT_LIMITS, LIMIT_BOUNDS)


def _v_evidence(problems, node):
    node = _require_map(problems, node, "evidence")
    if node is None:
        return
    _check_unknown(problems, node, "evidence", _GROUP_KEYS["evidence"])
    _check_bounded(problems, node, "evidence", DEFAULT_EVIDENCE,
                   _EVIDENCE_BOUNDS)
    export = node.get("export")
    if export is not None:
        export = _require_map(problems, export, "evidence.export")
        if export is not None:
            _check_unknown(problems, export, "evidence.export",
                           _SUB_KEYS["evidence.export"])
            agg = export.get("aggregate_only", True)
            if agg is not True:
                _err(problems, "evidence.export.aggregate_only",
                     "must be true — only aggregate pilot results may be "
                     "exported (§7.1)")
            redact = _require_list(
                problems, export.get("redact", list(REQUIRED_REDACTIONS)),
                "evidence.export.redact", allow_empty=True)
            if redact is not None:
                missing = set(REQUIRED_REDACTIONS) - set(redact)
                if missing:
                    _err(problems, "evidence.export.redact",
                         f"must redact {sorted(missing)} — required "
                         "redactions can widen, never shrink")
    tomb = node.get("tombstones")
    if tomb is not None:
        tomb = _require_map(problems, tomb, "evidence.tombstones")
        if tomb is not None:
            _check_unknown(problems, tomb, "evidence.tombstones",
                           _SUB_KEYS["evidence.tombstones"])
            keep = tomb.get("retain_until_registration_removal", True)
            if keep is not True:
                _err(problems,
                     "evidence.tombstones."
                     "retain_until_registration_removal",
                     "must be true — identity tombstones survive until "
                     "explicit registration removal (replay resistance)")


def _v_secrets(problems, node):
    node = _require_map(problems, node, "secrets")
    if node is None:
        return
    for key, value in node.items():
        path = f"secrets.{key}"
        if not isinstance(value, str):
            _err(problems, path,
                 "must be an approved secret-storage reference string "
                 f"({'|'.join(SECRET_REF_SCHEMES)}:<ref>)")
            continue
        if _SECRET_REF_RE.match(value):
            continue
        if any(rx.search(value) for rx in SECRET_CANARY_RES):
            _err(problems, path,
                 "literal secret rejected — store it in approved secret "
                 "storage and reference it as "
                 f"{'|'.join(SECRET_REF_SCHEMES)}:<ref>")
        else:
            _err(problems, path,
                 "must be an approved secret-storage reference "
                 f"({'|'.join(SECRET_REF_SCHEMES)}:<ref>), got {value!r}")


def _v_endpoint(problems, node):
    node = _require_map(problems, node, "endpoint")
    if node is None:
        return
    _check_unknown(problems, node, "endpoint", _GROUP_KEYS["endpoint"])
    preview = _require_map(problems, node.get("preview"), "endpoint.preview")
    if preview is not None:
        _check_unknown(problems, preview, "endpoint.preview",
                       _SUB_KEYS["endpoint.preview"])
        provider = preview.get("provider")
        if provider not in SUPPORTED_PREVIEW_PROVIDERS:
            _err(problems, "endpoint.preview.provider",
                 f"unsupported provider {provider!r} — supported: "
                 f"{', '.join(SUPPORTED_PREVIEW_PROVIDERS)}")
        env = preview.get("environment", "preview")
        if env != "preview":
            _err(problems, "endpoint.preview.environment",
                 "must be 'preview' — production deployment is disabled "
                 "for the MVP endpoint")
    merge = _require_map(problems, node.get("merge"), "endpoint.merge")
    if merge is not None:
        _check_unknown(problems, merge, "endpoint.merge",
                       _SUB_KEYS["endpoint.merge"])
        method = merge.get("method")
        if method not in SUPPORTED_MERGE_METHODS:
            _err(problems, "endpoint.merge.method",
                 f"unsupported method {method!r} — supported: "
                 f"{', '.join(SUPPORTED_MERGE_METHODS)}")
        _check_bounded(problems, merge, "endpoint.merge",
                       DEFAULT_ENDPOINT, _ENDPOINT_BOUNDS)
    disabled = node.get("disabled", list(REQUIRED_DISABLED))
    disabled = _require_list(problems, disabled, "endpoint.disabled",
                             allow_empty=True)
    if disabled is not None:
        unknown = [d for d in disabled if d not in REQUIRED_DISABLED]
        missing = [d for d in REQUIRED_DISABLED if d not in disabled]
        if unknown:
            _err(problems, "endpoint.disabled",
                 f"unknown capabilities {unknown} — the disable list may "
                 f"only name {list(REQUIRED_DISABLED)}")
        if missing:
            _err(problems, "endpoint.disabled",
                 f"must keep {missing} disabled — production deployment, "
                 "package publication and autonomous merge cannot be "
                 "enabled by manifest or worker input")


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def validate(raw):
    """Validate a parsed manifest; return the effective configuration.

    The result carries every declared value plus applied defaults, so the
    effective digest is over the *complete* contract an attempt runs under
    — never the partial document on disk. Raises :class:`ConfigError` with
    every problem found, not just the first.
    """
    problems = []
    if not isinstance(raw, dict):
        raise ConfigError([{"path": "<root>",
                            "message": "manifest must be a mapping"}])
    for key in raw:
        if key not in _TOP_LEVEL_KEYS:
            _err(problems, key,
                 "unknown field — remove it (manifest schema "
                 f"{SCHEMA_VERSION}); factory-owned keys are: "
                 f"{sorted(_TOP_LEVEL_KEYS - {'factory_kit'})}")
    version = raw.get("factory_kit")
    if version is None:
        _err(problems, "factory_kit",
             "missing schema version — set 'factory_kit: "
             f"{SCHEMA_VERSION}'")
    elif version not in SUPPORTED_SCHEMA_VERSIONS:
        _err(problems, "factory_kit",
             f"incompatible schema version {version!r} — supported: "
             f"{list(SUPPORTED_SCHEMA_VERSIONS)}")
    for group, validator in (
            ("identity", _v_identity),
            ("registration", _v_registration),
            ("authorization", _v_authorization),
            ("runtime", _v_runtime),
            ("skills", _v_skills),
            ("verification", _v_verification),
            ("limits", _v_limits),
            ("evidence", _v_evidence),
            ("secrets", _v_secrets),
            ("endpoint", _v_endpoint)):
        node = raw.get(group)
        if node is None:
            if group == "limits":
                node = {}   # fully defaulted group
            elif group == "secrets":
                node = {}   # empty secrets section is valid
            else:
                _err(problems, group, "required configuration group")
                continue
        validator(problems, node)
    if problems:
        raise ConfigError(problems)
    effective = {
        "factory_kit": version,
        "identity": dict(raw["identity"]),
        "registration": dict(raw["registration"]),
        "authorization": _eff_authorization(raw["authorization"]),
        "runtime": _eff_runtime(raw["runtime"]),
        "skills": {
            "auto_discover": raw["skills"].get("auto_discover", False),
            "approved": dict(raw["skills"]["approved"]),
        },
        "verification": {
            "acceptance_commands":
                list(raw["verification"]["acceptance_commands"]),
            "required_checks":
                dict(raw["verification"]["required_checks"]),
        },
        "limits": {k: (raw.get("limits") or {}).get(k, d)
                   for k, d in DEFAULT_LIMITS.items()},
        "evidence": _eff_evidence(raw["evidence"]),
        "secrets": dict(raw.get("secrets") or {}),
        "endpoint": _eff_endpoint(raw["endpoint"]),
    }
    return effective


def _eff_authorization(node):
    return {
        "execution_opt_in": node.get("execution_opt_in", False),
        "github_actors": list(node.get("github_actors", [])),
        "github_roles": list(node.get("github_roles", [])),
        "telegram_users": list(node.get("telegram_users", [])),
        "telegram_chats": list(node.get("telegram_chats", [])),
    }


def _eff_runtime(node):
    return {
        "name": node["name"],
        "roles": {role: dict(spec) for role, spec in node["roles"].items()},
        "tools": {side: list(node["tools"].get(side, []))
                  for side in ("allow", "deny")},
        "capabilities": {
            side: list(node["capabilities"].get(side, []))
            for side in ("allow", "deny")
        },
    }


def _eff_evidence(node):
    eff = {k: node.get(k, d) for k, d in DEFAULT_EVIDENCE.items()}
    export = node.get("export") or {}
    eff["export"] = {
        "aggregate_only": export.get("aggregate_only", True),
        "redact": list(export.get("redact", REQUIRED_REDACTIONS)),
    }
    tomb = node.get("tombstones") or {}
    eff["tombstones"] = {
        "retain_until_registration_removal":
            tomb.get("retain_until_registration_removal", True),
    }
    return eff


def _eff_endpoint(node):
    merge = node["merge"]
    return {
        "preview": {
            "provider": node["preview"]["provider"],
            "environment": node["preview"].get("environment", "preview"),
        },
        "merge": {
            "method": merge["method"],
            "approval_expiry_minutes": merge.get(
                "approval_expiry_minutes",
                DEFAULT_ENDPOINT["approval_expiry_minutes"]),
            "max_smoke_age_minutes": merge.get(
                "max_smoke_age_minutes",
                DEFAULT_ENDPOINT["max_smoke_age_minutes"]),
        },
        "disabled": sorted(
            set(node.get("disabled", REQUIRED_DISABLED)) |
            set(REQUIRED_DISABLED)),
    }


def load_manifest(text):
    """Parse restricted-YAML ``text`` and validate it."""
    return validate(yamlmini.load(text))


def load_manifest_file(path):
    """Load and validate a ``.factory-kit.yml`` file."""
    return validate(yamlmini.load_file(path))


def authority_key(identity) -> str:
    """Repository authority key — the immutable GitHub repository ID only.

    Display ``owner``/``name`` are presentation: renaming either can never
    change repository authority (A1/CFG01), so they are excluded here.
    """
    return f"gh:{identity['repo_id']}"


def _canonical(node) -> str:
    return json.dumps(node, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def effective_digest(effective) -> str:
    """Immutable digest of the validated effective configuration.

    Attached to every attempt (CFG-C02): two identical manifests digest
    identically, and any contract change — however small — rekeys it.
    """
    return hashlib.sha256(_canonical(effective).encode()).hexdigest()


def policy_digest(effective) -> str:
    """Digest over the policy surface only.

    Display fields (``identity.owner``/``identity.name``) are excluded so a
    repository rename never reparks work; every policy-relevant byte —
    authorization, runtime, skills, verification, limits, evidence,
    secrets, endpoint — is inside.
    """
    surface = {k: v for k, v in effective.items() if k != "identity"}
    surface["identity"] = {"repo_id": effective["identity"]["repo_id"]}
    return hashlib.sha256(_canonical(surface).encode()).hexdigest()


def is_execution_authorized(effective, *, github_actor=None,
                            github_role=None, telegram_user=None,
                            telegram_chat=None) -> bool:
    """Deny-by-default authorization check (CFG02).

    Returns True only when execution was explicitly opted into AND every
    supplied principal is on *its own* allowlist — actors against
    ``github_actors``, roles against ``github_roles``. Categories never
    cross: an actor name matching a role string authorizes nothing. Any
    principal not listed — or no principal named at all — denies.
    """
    authz = effective.get("authorization", {})
    if authz.get("execution_opt_in") is not True:
        return False
    checks = []
    if github_actor is not None:
        checks.append(github_actor in authz.get("github_actors", []))
    if github_role is not None:
        checks.append(github_role in authz.get("github_roles", []))
    if telegram_user is not None:
        checks.append(telegram_user in authz.get("telegram_users", []))
    if telegram_chat is not None:
        checks.append(telegram_chat in authz.get("telegram_chats", []))
    if not checks:
        # Opted in but nobody asked anything — the opt-in alone authorizes
        # no one; callers must always name a principal.
        return False
    return all(checks)
