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
import shutil
import subprocess
import tarfile
import tempfile
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
    """Thin adapter over the ``vercel`` CLI (v55) + the configured
    ``curl`` smoke probe — the only paved preview path (Q10's selected
    recipe).

    The provider token is read from the environment *by name*
    (``token_env``, default ``VERCEL_TOKEN``) at subprocess spawn —
    it is never interpolated into argv, never stored, and never
    returned. ``runner`` is injectable for tests; it receives
    ``(argv, env, timeout_s, cwd)`` and must return a
    ``subprocess.CompletedProcess``-compatible object.

    ``deploy`` uploads an *immutable export* of the exact
    ``spec["head_sha"]`` tree (``git archive`` → temp dir → ``vercel
    deploy`` with that cwd) — a dirty worktree can never leak into the
    deployment, and the durable ``factory-*`` metadata is what
    read-back and rediscovery bind on.
    """

    def __init__(self, repo_root=None, project=None, *, project_id=None,
                 org_id=None, vercel_bin="vercel", curl_bin="curl",
                 git_bin="git", token_env="VERCEL_TOKEN",
                 timeout_s=600, runner=None, now=None,
                 inspect_source="api"):
        self.repo_root = repo_root
        self.project = project
        self.project_id = project_id
        self.org_id = org_id
        self.vercel_bin = vercel_bin
        self.curl_bin = curl_bin
        self.git_bin = git_bin
        self.token_env = token_env
        self.timeout_s = timeout_s
        self._runner = runner or subprocess.run
        self._now = now or time.time
        #: Read-back surface — ``"api"`` (``vercel api
        #: /v13/deployments/<id>``) or ``"cli"`` (``vercel inspect
        #: --format json``). One switch point while the live wire is
        #: verified against the real CLI.
        self.inspect_source = inspect_source

    def provider_name(self) -> str:
        return "vercel"

    # -- argv boundary ---------------------------------------------------

    def _env(self):
        """Minimal provider credential environment — PATH for the
        binaries, HOME so a logged-in CLI finds its config dir (the
        ``.vercel`` auth lives under it), the org/project scope the
        link provides, plus the token *by env*, so it is never visible
        in ``ps``/argv or any persisted row."""
        env = {"PATH": os.environ.get("PATH", ""),
               "HOME": os.environ.get("HOME", "")}
        if self.org_id:
            env["VERCEL_ORG_ID"] = str(self.org_id)
        if self.project_id:
            env["VERCEL_PROJECT_ID"] = str(self.project_id)
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

    # -- immutable export --------------------------------------------------

    def _export_head(self, head_sha):
        """Materialize exactly ``head_sha``'s tree into a fresh temp
        dir — the deploy cwd — via ``git archive``. The archive is
        written to a file (binary tar never crosses the text-mode
        runner boundary), then extracted with :mod:`tarfile`."""
        if not self.repo_root:
            raise PreviewError(
                "no repo_root configured — cannot export the bound "
                "revision")
        export_dir = tempfile.mkdtemp(prefix="fk-export-")
        tar_path = os.path.join(export_dir + ".tar")
        try:
            self._run([self.git_bin, "-C", self.repo_root, "archive",
                       "--format=tar", "--output", tar_path,
                       str(head_sha)])
            with tarfile.open(tar_path) as tar:
                tar.extractall(export_dir)
        except Exception:
            shutil.rmtree(export_dir, ignore_errors=True)
            try:
                os.unlink(tar_path)
            except OSError:
                pass
            raise
        return export_dir, tar_path

    def _tree_sha(self, head_sha):
        return self._run([self.git_bin, "-C", self.repo_root,
                          "rev-parse", f"{head_sha}^{{tree}}"]).strip()

    # -- provider verbs --------------------------------------------------

    def deploy(self, spec, identity) -> dict:
        head = str(spec.get("head_sha") or "")
        tree = self._tree_sha(head)
        argv = [self.vercel_bin, "deploy", "--yes",
                "--format", "json"]
        for key in _IDENTITY_META_KEYS:
            if identity.get(key) is not None:
                argv += ["-m", f"factory-{key}={identity[key]}"]
        argv += ["-m", f"factory-head_sha={head}",
                 "-m", f"factory-tree_sha={tree}"]
        export_dir, tar_path = self._export_head(head)
        try:
            out = self._run_json(argv, cwd=export_dir)
        finally:
            shutil.rmtree(export_dir, ignore_errors=True)
            try:
                os.unlink(tar_path)
            except OSError:
                pass
        # ``--format json`` non-interactive output wraps the deployment
        # as {"status", "deployment": {…}}; bare mode emits the object.
        if isinstance(out, dict) and out.get("status") == "error":
            raise PreviewError(
                f"vercel deploy failed: "
                f"{str(out.get('message') or out.get('reason') or '')[:200]}")
        deployment = (out.get("deployment") if isinstance(out, dict)
                      else None) or out
        dep_id = (deployment or {}).get("id") or \
            (deployment or {}).get("uid")
        if not dep_id:
            # The upload may still have landed — reconcile by identity,
            # never by re-deploy.
            raise PreviewAmbiguity(
                "vercel deploy returned no deployment id — outcome "
                "uncertain")
        observed = self.inspect(dep_id)
        if observed is None:
            raise PreviewAmbiguity(
                "deployment created but read-back could not resolve it")
        if observed.get("head_sha") and \
                str(observed["head_sha"]) != head:
            raise PreviewAmbiguity(
                f"deployment head {observed['head_sha']} does not "
                f"match bound revision {head}")
        return observed

    def inspect(self, deployment_id) -> dict:
        if self.inspect_source == "cli":
            return self._inspect_cli(deployment_id)
        return self._inspect_api(deployment_id)

    def _inspect_api(self, deployment_id):
        try:
            out = self._run_json(
                [self.vercel_bin, "api",
                 f"/v13/deployments/{deployment_id}"])
        except PreviewError as exc:
            if "404" in str(exc) or "not_found" in str(exc).lower() \
                    or "not found" in str(exc).lower():
                return None
            raise
        return self._map_inspect(deployment_id, out)

    def _inspect_cli(self, deployment_id):
        try:
            out = self._run_json(
                [self.vercel_bin, "inspect", str(deployment_id),
                 "--format", "json"])
        except PreviewError as exc:
            if "404" in str(exc) or "not found" in str(exc).lower():
                return None
            raise
        return self._map_inspect(deployment_id, out)

    def _map_inspect(self, deployment_id, out):
        """One field mapping shared by both read-back surfaces: the
        durable record binds deployment id, URL, the factory-bound
        ``head_sha``/``tree_sha`` metadata, readiness and ownership —
        never the whole provider blob."""
        if not isinstance(out, dict):
            return None
        dep_id = out.get("id") or out.get("uid") or deployment_id
        url = out.get("url") or out.get("deploymentUrl")
        if not url:
            return None
        if not str(url).startswith("http"):
            url = f"https://{url}"
        meta = out.get("meta") or {}
        head = meta.get("factory-head_sha") or \
            ((out.get("meta") or {}).get("commit")) or \
            out.get("gitCommitSha")
        return {"deployment_id": str(dep_id), "url": url,
                "head_sha": head,
                "artifact_identity": meta.get("factory-tree_sha"),
                "state": str(out.get("readyState") or
                              out.get("state") or "READY").upper(),
                "expires_epoch": None,
                "owned": "factory-preview_id" in meta}

    def find_deployments(self, identity) -> list:
        argv = [self.vercel_bin, "ls"]
        if self.project:
            argv.append(str(self.project))
        if identity.get("preview_id") is not None:
            argv += ["-m",
                     f"factory-preview_id={identity['preview_id']}"]
        # ``ls`` accepts ``-F/--format json`` only — ``--json`` is an
        # unknown-option error on v55.
        argv += ["--format", "json"]
        # A provider error propagates: callers must distinguish "no
        # deployments" (a clean empty list) from "could not ask"
        # (PreviewError). Flattening an outage into [] would tell the
        # lifecycle an owned deployment never existed — the false answer
        # the rediscovery path exists to prevent (A5).
        out = self._run_json(argv)
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
                              "head_sha": meta.get("factory-head_sha"),
                              "artifact_identity":
                                  meta.get("factory-tree_sha"),
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
