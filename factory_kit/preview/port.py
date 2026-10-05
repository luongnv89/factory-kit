#!/usr/bin/env python3
"""Preview provider port — the seam between the preview lifecycle and
the deployment authority (issue #15 / Task 3.1, PRD §3.2 F11, §5.2,
§6.4).

The scoped provider credential lives *only* behind this port. Workers,
tests and descendant processes never see it: the coordinator calls
:class:`factory_kit.preview.service.PreviewService`, which re-checks
authorization, generation, configuration and the fence at the durable
boundary and is the sole caller of this port — the same shape the
publication broker proved (``factory_kit.publication.remote``). A
worker process receives no port handle and no ``VERCEL_TOKEN``; the
only evidence it can produce is a candidate self-report, which is
never what the lifecycle records.

Two implementations ship:

- :class:`VercelCliPreview` — the production seam: thin argv mapping
  onto the selected provider (Vercel preview deployments) plus the
  configured ``curl`` smoke probe. The token reaches subprocesses
  through the environment *only* (``VERCEL_TOKEN`` by name — the value
  never lands on argv, in a durable row, or in an event). Deliberately
  not exercised live in unit tests (no Vercel account on the test
  host); the command shape is asserted.
- :class:`ScriptedPreview` — the deterministic fixture port: in-memory
  deployments bound immutably to ``head_sha`` + artifact digest, an
  identity-keyed rediscovery surface, an outage switch and a foreign
  (unowned) deployment set the removal path must preserve — the
  packaged analogue of the spike's ``PreviewRegistry``
  (``tests/fixtures/preview_fixture.py``).
"""

from __future__ import annotations

import hashlib
import json as _json
import os
import re
import shlex
import subprocess
import time

__all__ = [
    "PreviewError",
    "PreviewAmbiguity",
    "PreviewPort",
    "VercelCliPreview",
    "ScriptedPreview",
]


class PreviewError(Exception):
    """The provider refused or failed the operation (a clean answer).

    An outage is never translated into success by callers — it surfaces
    as ``preview_failed`` evidence and, on the cleanup path, as a
    visible backlog entry rather than a false removal claim.
    """


class PreviewAmbiguity(PreviewError):
    """The provider call's outcome is *uncertain* — the deployment may
    have been created (lost response, timed-out read). Reconciled by
    recorded identity on the next sweep, never retried blindly (A5).
    """


class PreviewPort:
    """The provider contract the preview lifecycle consumes.

    ``spec`` carries the deployment request — ``head_sha`` (the exact
    revision bound), ``base_sha``/``base_name``, ``visibility``,
    ``environment``, ``ttl_s`` and the optional ``artifact`` body the
    scripted port serves. ``identity`` carries the durable record's
    binding — ``preview_id``, ``work_key``, ``generation`` — recorded
    with the deployment so read-back and callbacks bind to *this*
    factory's resource, never a look-alike (A1/A4).
    """

    def provider_name(self) -> str:
        """The configured provider identity (``endpoint.preview.
        provider``) — a mismatched port denies before any effect."""
        raise NotImplementedError

    def deploy(self, spec, identity) -> dict:
        """Create one preview bound immutably to ``spec["head_sha"]``;
        return ``{"deployment_id", "url", "artifact_identity",
        "expires_epoch", "head_sha"}``."""
        raise NotImplementedError

    def inspect(self, deployment_id) -> dict:
        """Authoritative read-back: ``{"deployment_id", "head_sha",
        "artifact_identity", "url", "state", "expires_epoch",
        "owned"}``. Returns ``None`` when the id is unknown to the
        provider — an uncertain identity is never invented."""
        raise NotImplementedError

    def find_deployments(self, identity) -> list:
        """Identity-keyed rediscovery for the crash window: deployments
        this factory created carrying ``identity``'s binding."""
        raise NotImplementedError

    def smoke(self, deployment_id, contract) -> dict:
        """Run the *configured* smoke probe against the deployment's
        recorded URL: ``{"ok", "http", "marker", "observed_at",
        "detail"}`` — provider-correlated evidence, never a worker
        self-report."""
        raise NotImplementedError

    def remove(self, deployment_id, identity) -> dict:
        """Remove the *owned* deployment; returns ``{"removed": True}``
        only when the provider confirmed. An unowned id refuses —
        human-created resources are preserved (A5)."""
        raise NotImplementedError


#: Identity fields the port echoes into provider-side deployment
#: metadata — read-back and callbacks bind on these, and only these.
_IDENTITY_META_KEYS = ("preview_id", "work_key", "generation")


class VercelCliPreview(PreviewPort):
    """Thin adapter over the ``vercel`` CLI + the configured ``curl``
    smoke probe — the only paved preview path (Q10's selected recipe).

    The provider token is read from the environment *by name*
    (``token_env``, default ``VERCEL_TOKEN``) at subprocess spawn —
    it is never interpolated into argv, never stored, and never
    returned. ``runner`` is injectable for tests; it receives
    ``(argv, env, timeout_s, cwd)`` and must return a
    ``subprocess.CompletedProcess``-compatible object.
    """

    def __init__(self, project=None, *, vercel_bin="vercel",
                 curl_bin="curl", token_env="VERCEL_TOKEN",
                 timeout_s=120, runner=None, now=None):
        self.project = project
        self.vercel_bin = vercel_bin
        self.curl_bin = curl_bin
        self.token_env = token_env
        self.timeout_s = timeout_s
        self._runner = runner or subprocess.run
        self._now = now or time.time

    def provider_name(self) -> str:
        return "vercel"

    # -- argv boundary ---------------------------------------------------

    def _env(self):
        """Minimal provider credential environment — PATH for the
        binary plus the token *by env*, so it is never visible in
        ``ps``/argv or any persisted row."""
        env = {"PATH": os.environ.get("PATH", "")}
        token = os.environ.get(self.token_env)
        if token:
            env[self.token_env] = token
        return env

    def _run(self, argv, *, cwd=None):
        try:
            proc = self._runner(argv, env=self._env(),
                                timeout=self.timeout_s, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise PreviewAmbiguity(
                f"{argv[0]} timed out: {exc}") from exc
        except OSError as exc:
            raise PreviewError(f"{argv[0]}: {exc}") from exc
        out = getattr(proc, "stdout", "") or ""
        err = getattr(proc, "stderr", "") or ""
        if getattr(proc, "returncode", 1) != 0:
            raise PreviewError(
                f"{' '.join(argv[:2])} exited "
                f"{getattr(proc, 'returncode', '?')}: "
                f"{err.strip()[:200]}")
        return out

    def _run_json(self, argv, *, cwd=None):
        out = self._run(argv, cwd=cwd)
        try:
            return _json.loads(out or "{}")
        except ValueError:
            raise PreviewError(
                f"{argv[0]} returned non-JSON output — cannot bind "
                "deployment identity authoritatively")

    # -- provider verbs --------------------------------------------------

    def deploy(self, spec, identity) -> dict:
        argv = [self.vercel_bin, "deploy", "--yes"]
        for key in _IDENTITY_META_KEYS:
            if identity.get(key) is not None:
                argv += ["-m", f"factory-{key}={identity[key]}"]
        if self.project:
            argv += ["--name", str(self.project)]
        out = self._run(argv, cwd=spec.get("cwd"))
        url = ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("https://"):
                url = line
        if not url:
            raise PreviewAmbiguity(
                "vercel deploy returned no URL — outcome uncertain")
        # Read back by the returned URL so the deployment id is the
        # provider's own identity, never an invented one.
        observed = self.inspect(url)
        if observed is None:
            raise PreviewAmbiguity(
                "deployment created but read-back could not resolve it")
        return observed

    def inspect(self, deployment_id) -> dict:
        argv = [self.vercel_bin, "inspect", str(deployment_id)]
        out = self._run(argv)
        return self._inspect_fields(str(deployment_id), out)

    def _inspect_fields(self, deployment_id, text):
        """Normalize ``vercel inspect`` key: value output — the CLI
        emits a table; the durable record binds the fields it
        corroborates (uid/url/created/meta), never the whole blob."""
        fields = {}
        for line in (text or "").splitlines():
            match = re.match(r"^\s*([A-Za-z][\w-]*)\s+(.+?)\s*$", line)
            if match:
                fields[match.group(1)] = match.group(2)
        dep_id = fields.get("id") or fields.get("uid") or deployment_id
        url = fields.get("url") or fields.get("deploymentUrl")
        if not url:
            return None
        if not str(url).startswith("http"):
            url = f"https://{url}"
        head = (fields.get("commit") or fields.get("sha") or
                fields.get("revision") or fields.get("gitCommitSha"))
        return {"deployment_id": str(dep_id), "url": url,
                "head_sha": head,
                "artifact_identity": fields.get("digest") or
                fields.get("buildDigest"),
                "state": (fields.get("state") or "READY").upper(),
                "expires_epoch": None, "owned": True}

    def find_deployments(self, identity) -> list:
        argv = [self.vercel_bin, "ls"]
        if self.project:
            argv.append(str(self.project))
        argv += ["--json"]
        try:
            out = self._run_json(argv)
        except PreviewError:
            return []
        rows = out if isinstance(out, list) else \
            out.get("deployments", [])
        wanted = {k: str(identity.get(k)) for k in _IDENTITY_META_KEYS
                  if identity.get(k) is not None}
        found = []
        for row in rows if isinstance(rows, list) else []:
            meta = row.get("meta") or {}
            dep_id = str(row.get("uid") or row.get("id") or "")
            bound = {k: str(meta.get(f"factory-{k}"))
                     for k in _IDENTITY_META_KEYS}
            if wanted and all(bound.get(k) == v
                              for k, v in wanted.items()):
                found.append({"deployment_id": dep_id,
                              "url": row.get("url"),
                              "head_sha": row.get("commit"),
                              "artifact_identity": None,
                              "state": str(row.get("state") or
                                           "READY").upper(),
                              "expires_epoch": None, "owned": True})
        return found

    def smoke(self, deployment_id, contract) -> dict:
        """The configured contract verbatim: ``command`` (with the
        recorded URL interpolated) must produce ``expect`` on stdout,
        and a configured ``marker`` must appear in the served body.
        Args go through ``shlex.split`` — the operator-authored command
        is never re-parsed by a shell."""
        observed = self.inspect(deployment_id)
        if observed is None:
            raise PreviewError(
                f"unknown deployment {deployment_id!r} — smoke cannot "
                "run against an unrecorded identity")
        url = observed["url"]
        # Literal ``{url}`` substitution — ``str.format`` would choke on
        # the contract's ``%{http_code}`` curl format spec, which must
        # pass through untouched.
        command = (contract or {}).get("command", "")
        argv = shlex.split(command.replace("{url}", url))
        if not argv:
            return {"ok": False, "http": None, "marker": None,
                    "observed_at": self._now(),
                    "detail": "empty smoke command"}
        try:
            output = self._run(argv).strip()
        except PreviewAmbiguity:
            raise
        except PreviewError as exc:
            return {"ok": False, "http": None, "marker": None,
                    "observed_at": self._now(), "detail": str(exc)[:200]}
        expect = str((contract or {}).get("expect") or "").strip()
        expected_ok = (not expect) or output == expect
        marker = (contract or {}).get("marker")
        marker_hit = None
        if marker:
            try:
                body = self._run([self.curl_bin, "-fsS", url])
            except PreviewError:
                body = ""
            marker_hit = marker in body
        ok = expected_ok and marker_hit is not False and \
            str(observed.get("state") or "READY").upper() == "READY"
        http = output if output.isdigit() else None
        return {"ok": bool(ok), "http": http, "marker": marker_hit,
                "observed_at": self._now(),
                "detail": f"expect={expect!r} got={output!r}"}

    def remove(self, deployment_id, identity) -> dict:
        observed = self.inspect(deployment_id)
        if observed is None:
            return {"removed": True, "already_gone": True}
        if not observed.get("owned", True):
            raise PreviewError(
                f"refusing removal of unowned deployment "
                f"{deployment_id!r}")
        self._run([self.vercel_bin, "rm", str(deployment_id), "--yes"])
        return {"removed": True}


class ScriptedPreview(PreviewPort):
    """Deterministic fixture provider — the packaged ``PreviewRegistry``
    analogue.

    - ``deployments`` maps ``deployment_id`` to the immutable record the
      provider corroborates (bound ``head_sha`` + artifact digest,
      created/expiry epochs, the factory identity it was minted under).
    - ``bodies`` is the served content — the smoke marker check reads
      it, so a deploy without the marker fails smoke for real.
    - ``foreign`` holds deployments the fixture did *not* create for
      this factory — removal refuses them (human-created provider
      resources are preserved, A5).
    - ``faults`` injects per-op failures: ``"down"`` (provider
      unreachable), ``"crash-after-create"`` (effect landed, response
      lost — the deploy ambiguity), ``"crash-before-create"``.
    - ``outage`` is the one-switch provider outage used by the
      acceptance criteria (deploy/inspect/smoke/remove all refuse).
    """

    def __init__(self, *, provider="vercel", now=None, faults=None):
        self.provider = provider
        self._now = now or time.time
        self.faults = dict(faults or {})
        self.outage = False
        self.deployments = {}
        self.bodies = {}
        self.foreign = {}
        self.calls = []
        self._n = 0

    # -- fixture plumbing --------------------------------------------------

    def provider_name(self) -> str:
        return self.provider

    def _fault(self, op):
        if self.outage and op != "snapshot":
            raise PreviewError(f"{op}: provider unreachable (outage)")
        fault = self.faults.get(op)
        if isinstance(fault, dict):
            fault = fault.get("kind")
        if fault == "down" or fault == "raise":
            raise PreviewError(f"{op} refused by provider")
        if fault == "crash-before-create":
            raise PreviewAmbiguity(f"{op} request lost")
        return fault

    # -- provider verbs ----------------------------------------------------

    def deploy(self, spec, identity) -> dict:
        self.calls.append({"op": "deploy",
                           "head_sha": spec.get("head_sha")})
        fault = self._fault("deploy")
        head = str(spec.get("head_sha") or "")
        artifact = spec.get("artifact")
        self._n += 1
        dep_id = (f"dpl-{self._n:04d}-"
                  + hashlib.sha256(head.encode()).hexdigest()[:8])
        ttl_s = float(spec.get("ttl_s") or 24 * 3600)
        body = artifact if artifact is not None else (
            f"<html><title>preview</title><body>"
            f"{spec.get('marker') or 'preview'} build of "
            f"{head[:12]}</body></html>")
        record = {
            "deployment_id": dep_id,
            "head_sha": head,
            "artifact_identity": hashlib.sha256(
                (head + (artifact or "")).encode()).hexdigest()[:16],
            "url": f"https://preview-{dep_id}.example.test",
            "state": "READY",
            "created_epoch": self._now(),
            "expires_epoch": self._now() + ttl_s,
            "owned": True,
            "identity": {k: identity.get(k) for k in
                         _IDENTITY_META_KEYS},
        }
        self.deployments[dep_id] = record
        self.bodies[dep_id] = body
        if fault == "crash-after-create":
            # The deployment exists; the response was lost — the exact
            # crash window the sweep reconciles by identity.
            raise PreviewAmbiguity("deploy response lost")
        return dict(record)

    def inspect(self, deployment_id) -> dict:
        self.calls.append({"op": "inspect",
                           "deployment_id": deployment_id})
        self._fault("inspect")
        dep = self.deployments.get(deployment_id)
        if dep is None:
            foreign = self.foreign.get(deployment_id)
            if foreign is not None:
                out = dict(foreign)
                out["owned"] = False
                return out
            return None
        dep = dict(dep)
        if dep.get("expires_epoch") is not None and \
                dep["expires_epoch"] < self._now():
            dep["state"] = "EXPIRED"
        return dep

    def find_deployments(self, identity) -> list:
        self.calls.append({"op": "find",
                           "identity": dict(identity)})
        self._fault("find")
        wanted = {k: identity.get(k) for k in _IDENTITY_META_KEYS
                  if identity.get(k) is not None}
        return [dict(d) for d in self.deployments.values()
                if all(d["identity"].get(k) == v
                       for k, v in wanted.items())]

    def smoke(self, deployment_id, contract) -> dict:
        self.calls.append({"op": "smoke",
                           "deployment_id": deployment_id})
        self._fault("smoke")
        dep = self.deployments.get(deployment_id)
        if dep is None:
            return {"ok": False, "http": 404, "marker": None,
                    "state": "UNKNOWN", "observed_at": self._now(),
                    "detail": "deployment id unknown to provider"}
        expired = dep.get("expires_epoch") is not None and \
            dep["expires_epoch"] < self._now()
        state = "EXPIRED" if expired else dep["state"]
        body = self.bodies.get(deployment_id, "")
        marker = (contract or {}).get("marker")
        marker_hit = (marker in body) if marker else None
        ready = state == "READY"
        http = "200" if ready else "503"
        expect = str((contract or {}).get("expect") or "").strip()
        ok = bool(ready and (not expect or http == expect)
                  and marker_hit is not False)
        return {"ok": ok, "http": http, "marker": marker_hit,
                "state": state, "observed_at": self._now(),
                "detail": f"expect={expect!r} http={http}"
                          f" marker={marker_hit}"}

    def remove(self, deployment_id, identity) -> dict:
        self.calls.append({"op": "remove",
                           "deployment_id": deployment_id})
        self._fault("remove")
        if deployment_id in self.foreign:
            raise PreviewError(
                f"refusing removal of unowned deployment "
                f"{deployment_id!r}")
        if deployment_id not in self.deployments:
            raise PreviewError(
                f"unknown deployment {deployment_id!r}")
        del self.deployments[deployment_id]
        self.bodies.pop(deployment_id, None)
        return {"removed": True}

    # -- test helpers --------------------------------------------------------

    def add_foreign(self, deployment_id, *, url=None):
        """Register a deployment the fixture provider knows but this
        factory never created — the preservation fixture."""
        self.foreign[deployment_id] = {
            "deployment_id": deployment_id, "head_sha": None,
            "artifact_identity": None,
            "url": url or f"https://{deployment_id}.example.test",
            "state": "READY", "owned": False}
        return deployment_id
