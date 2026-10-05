"""Hostile boundary probes for external-workload admission (Task 4.4 /
§8.3 A3–A4, GATE-P04 evidence).

External contributor code/tests must be *denied* access to the
filesystem outside the workspace, the network, and privileged paths —
and the denial must be demonstrated by *executing* the hostile
fixtures, never by trusting the admission code's own claims. This
module is that independent check: it runs the fixture suite inside the
isolation mechanism the host actually provides and returns a signed
evidence document the consent gate validates.

Isolation mechanisms recognised:

- ``seatbelt`` — macOS ``sandbox-exec``; per-probe profiles deny
  ``file-read*`` on ``/etc``, ``network*`` and ``file-write*``.
- ``bubblewrap`` — Linux ``bwrap``; ``--unshare-all`` drops the
  network, a minimal bind set leaves ``/etc`` absent, and the system
  binds stay read-only so privileged writes fail.

A4's standing rule: a git worktree, a prompt rule and branch protection
are *coordination* controls, not isolation — :data:`NON_ISOLATION`
names them, and :func:`verify_boundary` rejects evidence claiming them
even when the document asserts ``verified``.

Outcome vocabulary:

- ``verified`` — every hostile probe was denied under a real
  mechanism and the workload environment carries no restricted
  credential;
- ``failed`` — at least one probe *succeeded* (a real breach);
- ``unverified`` — no mechanism on the host, or a probe result could
  not be determined; the blocker names what is missing.

``evidence_ref`` pins the whole document (``mechanism:sha256:…``) so
the durable admission row references exactly the evidence it accepted.

Independently executable: ``python3 -m factory_kit.privacy.boundary``
runs the suite on the operator host and prints the evidence JSON —
exit 0 only when every boundary is demonstrated.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

#: Claims that can never be isolation evidence, whatever else the
#: document says (A4 — worktrees, prompt rules and branch protection
#: are coordination controls, not a sandbox).
NON_ISOLATION = ("worktree", "prompt-rule", "branch-protection")

#: Isolation mechanisms the harness knows how to exercise.
MECHANISMS = ("seatbelt", "bubblewrap")

#: The hostile-fixture suite: every probe must end ``denied``.
PROBE_NAMES = ("fs-read-outside", "net-egress", "privileged-write",
               "credential-scope")

#: Environment names a workload process must never inherit — controller
#: and provider credentials (Task 4.4 / A3: absence of privileged,
#: controller and unrestricted GitHub credentials). Scoped, short-lived
#: credentials are minted per deployment by the services that need
#: them; they never enter the workload environment.
RESTRICTED_ENV = (
    "GH_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_TOKEN",
    "GITHUB_APP_TOKEN", "GITHUB_PAT",
    "VERCEL_TOKEN", "VERCEL_OIDC_TOKEN",
    "TELEGRAM_BOT_TOKEN", "SLACK_BOT_TOKEN", "GITLAB_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
)

_PROBE_TIMEOUT = 20

# Each probe prints exactly one marker: ``PROBE:ALLOWED`` when the
# hostile action succeeded (a breach), ``PROBE:DENIED:<ErrName>`` when
# the platform refused it, ``PROBE:ERROR:<ErrName>`` otherwise.

_FS_READ_SRC = """\
try:
    with open("/etc/passwd") as fh:
        fh.read()
except OSError as exc:
    print("PROBE:DENIED:%s" % type(exc).__name__)
except Exception as exc:
    print("PROBE:ERROR:%s" % type(exc).__name__)
else:
    print("PROBE:ALLOWED")
"""

_NET_EGRESS_SRC = """\
import socket
try:
    s = socket.create_connection(("127.0.0.1", 1), timeout=2)
except ConnectionRefusedError:
    # The connect syscall was permitted — the endpoint refused, but
    # egress itself is not denied.
    print("PROBE:ALLOWED")
except OSError as exc:
    print("PROBE:DENIED:%s" % type(exc).__name__)
except Exception as exc:
    print("PROBE:ERROR:%s" % type(exc).__name__)
else:
    s.close()
    print("PROBE:ALLOWED")
"""

_PRIV_WRITE_SRC = """\
import os
targets = [
    os.path.join(os.path.expanduser("~"), ".fk-boundary-probe"),
    "/etc/fk-boundary-probe",
]
denied, allowed, errors = 0, 0, 0
for target in targets:
    try:
        with open(target, "w") as fh:
            fh.write("x")
    except OSError:
        denied += 1
    except Exception:
        errors += 1
    else:
        allowed += 1
        try:
            os.remove(target)
        except OSError:
            pass
if allowed:
    print("PROBE:ALLOWED")
elif denied:
    print("PROBE:DENIED")
else:
    print("PROBE:ERROR:no-targets")
"""

_PROBE_SRC = {
    "fs-read-outside": _FS_READ_SRC,
    "net-egress": _NET_EGRESS_SRC,
    "privileged-write": _PRIV_WRITE_SRC,
}

#: sandbox-exec profiles per probe — permissive default so the
#: interpreter itself runs, with the one hostile action explicitly
#: denied. Seatbelt denial surfaces as EPERM in the child.
_SEATBELT_PROFILES = {
    "fs-read-outside":
        "(version 1)\n(allow default)\n(deny file-read* (subpath \"/etc\"))",
    "net-egress":
        "(version 1)\n(allow default)\n(deny network*)",
    "privileged-write":
        "(version 1)\n(allow default)\n(deny file-write*)",
}


class _Result:
    """Runner return shape (mirrors subprocess.CompletedProcess)."""

    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _default_runner(argv, *, env=None, cwd=None, timeout=None):
    try:
        return subprocess.run(argv, env=env, cwd=cwd, timeout=timeout,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return _Result(-1, stderr="timeout")
    except OSError as exc:
        return _Result(-1, stderr=f"{type(exc).__name__}:{exc}")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def detect_mechanism(which=None):
    """The isolation mechanism this host can actually enforce:
    ``seatbelt`` when ``sandbox-exec`` exists, ``bubblewrap`` when
    ``bwrap`` exists, else ``None`` — the honest 'no boundary' verdict
    the admission gate refuses to admit (A4)."""
    which = which or shutil.which
    if which("sandbox-exec"):
        return "seatbelt"
    if which("bwrap"):
        return "bubblewrap"
    return None


def credential_scope_ok(env):
    """Whether the environment a workload would inherit carries no
    restricted credential (A3). Returns the check evidence — the names
    found are listed, their values never are."""
    env = env or {}
    rejected = sorted(k for k in env if k in RESTRICTED_ENV)
    return {"unrestricted_absent": not rejected,
            "granted": sorted(env),
            "rejected": rejected}


def _workload_env(env):
    """The environment the hostile fixture runs under: the caller's
    declared workload env, or the minimal scrubbed default."""
    if env is not None:
        return dict(env)
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": "/nonexistent",
            "PYTHONDONTWRITEBYTECODE": "1"}


def _classify(result):
    """Map a probe's process result to ``denied`` / ``allowed`` /
    ``inconclusive`` + detail."""
    marker = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("PROBE:"):
            marker = line.strip()
    if marker is None:
        tail = (result.stderr or "").strip().splitlines()
        return "inconclusive", (tail[-1][:120] if tail else
                                f"rc={result.returncode}")
    parts = marker.split(":", 2)
    verdict = parts[1]
    detail = parts[2] if len(parts) > 2 else ""
    if verdict == "DENIED":
        return "denied", detail
    if verdict == "ALLOWED":
        return "allowed", detail
    return "inconclusive", detail or f"unexpected-marker:{marker}"


def _probe_argv(mechanism, probe, workspace, interpreter, which):
    """The sandboxed command line for one hostile fixture, or ``None``
    when the mechanism's binary is absent."""
    if mechanism == "seatbelt":
        exe = which("sandbox-exec")
        if not exe:
            return None
        return [exe, "-p", _SEATBELT_PROFILES[probe],
                interpreter, "-B", "-c", _PROBE_SRC[probe]]
    if mechanism == "bubblewrap":
        exe = which("bwrap")
        if not exe:
            return None
        # Minimal root: workspace rw at /ws, system dirs read-only,
        # /etc deliberately absent, --unshare-all drops the network.
        return [exe, "--die-with-parent", "--unshare-all",
                "--proc", "/proc", "--dev", "/dev",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind-try", "/lib", "/lib",
                "--ro-bind-try", "/lib64", "/lib64",
                "--bind", workspace, "/ws", "--chdir", "/ws",
                interpreter, "-B", "-c", _PROBE_SRC[probe]]
    return None


def evidence_ref(evidence):
    """The durable reference an admission row stores: mechanism plus a
    digest of the canonical evidence document (minus the ref itself)
    — the row pins exactly what was verified."""
    if not isinstance(evidence, dict):
        return "malformed"
    body = {k: v for k, v in evidence.items() if k != "evidence_ref"}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return f"{evidence.get('mechanism') or 'none'}:sha256:{digest}"


def run_probes(mechanism=None, *, workspace=None, runner=None, env=None,
               which=None):
    """Execute the hostile-fixture suite and return the evidence
    document (A3/A4).

    Never runs a hostile fixture without a real isolation mechanism —
    ``None``/``none`` mechanisms and every :data:`NON_ISOLATION` claim
    return ``unverified`` evidence without executing anything. A probe
    that *succeeds* is a breach: ``failed`` with a named
    ``boundary-breach:<probe>`` blocker.
    """
    which = which or shutil.which
    runner = runner or _default_runner
    workspace = os.path.abspath(workspace or os.getcwd())
    workload_env = _workload_env(env)
    if mechanism is None:
        mechanism = detect_mechanism(which=which)
    creds = credential_scope_ok(workload_env)
    evidence = {"mechanism": mechanism, "outcome": "unverified",
                "blocker": "isolation-mechanism-missing",
                "probes": {}, "credentials": creds,
                "workspace": workspace,
                "verified_at": _utcnow()}
    if mechanism in NON_ISOLATION:
        evidence["blocker"] = f"non-isolation:{mechanism}"
        evidence["evidence_ref"] = evidence_ref(evidence)
        return evidence
    if mechanism in (None, "none") or mechanism not in MECHANISMS:
        evidence["mechanism"] = mechanism or "none"
        evidence["evidence_ref"] = evidence_ref(evidence)
        return evidence

    interpreter = shutil.which("python3") or sys.executable
    probes = {}
    first_breach = first_inconclusive = None
    for name in PROBE_NAMES:
        if name == "credential-scope":
            verdict = "denied" if creds["unrestricted_absent"] \
                else "allowed"
            detail = (",".join(creds["rejected"]) if creds["rejected"]
                      else "no-restricted-env")
            probes[name] = {"result": verdict, "detail": detail}
        else:
            argv = _probe_argv(mechanism, name, workspace, interpreter,
                               which)
            if argv is None:
                verdict, detail = "inconclusive", "mechanism-absent"
            else:
                verdict, detail = _classify(
                    runner(argv, env=workload_env, cwd=workspace,
                           timeout=_PROBE_TIMEOUT))
            probes[name] = {"result": verdict, "detail": detail}
        if verdict == "allowed" and first_breach is None:
            first_breach = name
        elif verdict == "inconclusive" and first_inconclusive is None:
            first_inconclusive = name

    if first_breach:
        evidence["outcome"] = "failed"
        evidence["blocker"] = f"boundary-breach:{first_breach}"
    elif first_inconclusive:
        evidence["blocker"] = \
            f"boundary-probe-inconclusive:{first_inconclusive}"
    else:
        evidence["outcome"] = "verified"
        evidence["blocker"] = None
    evidence["probes"] = probes
    evidence["evidence_ref"] = evidence_ref(evidence)
    return evidence


def verify_boundary(evidence):
    """Validate a boundary evidence document the admission gate was
    handed (A4).

    Returns ``{"ok": True}`` only when every probe was denied under a
    real mechanism with no restricted credentials. A forged
    ``outcome: verified`` cannot pass: the checks re-derive the verdict
    from the per-probe results, and :data:`NON_ISOLATION` mechanisms
    are rejected whatever the document claims.
    """
    if not isinstance(evidence, dict):
        return {"ok": False, "reason": "boundary-evidence-malformed"}
    mechanism = evidence.get("mechanism")
    if mechanism in NON_ISOLATION:
        return {"ok": False, "reason": f"non-isolation:{mechanism}"}
    if not mechanism or mechanism in ("none",) \
            or mechanism not in MECHANISMS:
        return {"ok": False, "reason": "isolation-mechanism-missing"}
    probes = evidence.get("probes") or {}
    for name in PROBE_NAMES:
        if (probes.get(name) or {}).get("result") != "denied":
            return {"ok": False, "reason": f"probe-not-denied:{name}"}
    if not (evidence.get("credentials") or {}).get(
            "unrestricted_absent"):
        return {"ok": False, "reason": "unrestricted-credentials"}
    if evidence.get("outcome") != "verified":
        return {"ok": False, "reason":
                f"boundary-{evidence.get('outcome') or 'unverified'}"}
    return {"ok": True, "reason": None}


def main(argv=None):
    """Independent entry point — run the suite, print the evidence,
    exit 0 only when every boundary is demonstrated."""
    argv = list(argv if argv is not None else sys.argv)
    mechanism = argv[1] if len(argv) > 1 else None
    evidence = run_probes(mechanism, workspace=os.getcwd())
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if evidence["outcome"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
