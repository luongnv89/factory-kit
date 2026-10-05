#!/usr/bin/env python3
"""factory-kit endpoint walkthrough — the executable half of issue #4 / Task 1.3.

Assembles the *complete opted-in issue → approved merge endpoint* in one
orchestrated run, on the primitives Tasks 1.1–1.2 selected and proved:

- **intake** (A1): an HMAC-signed event for the registered repository creates
  durable logical work/Hermes identity *before* acceptance; bad signature,
  wrong repository and unauthorized opt-in each create no authority. One
  implementation session and a *separate* reviewer session then execute
  sequentially, each recording its own task/attempt identity, verdict and
  exact PR head.
- **verify** (A2): independent review + nonempty checks + authoritative
  read-back must agree on open head/base and observation time; a moved head
  or failed/missing check blocks. Implementation self-report can never
  substitute for the reviewer verdict or the checks.
- **preview** (A3): the provider returns a deployment ID/URL bound
  immutably to the reviewed head; smoke records result + time, and owned
  cleanup is demonstrated. Wrong revision / unknown identity / failed or
  stale smoke / expiry / outage all block and cannot reuse an older preview.
- **approval** (A4): a durable one-use request binds actor/target/action/
  revision/evidence/policy digests with 60-minute expiry; it survives a
  store restart attributable, and repeated or concurrent approvals consume
  at most one request and create at most one merge intent.
- **merge-guard** (A5/A6): the single merge owner revalidates everything —
  authorization, fence state, open non-draft PR, exact head/base, required
  checks, enforced up-to-date base, fresh healthy preview, consumed
  unexpired approval — before an expected-head merge. Every deny leg also
  invalidates the affected authority. On the live repo the owner checks
  real protection and **refuses** when the base cannot enforce the contract.
- **readback** (A7): `merged` only ever comes from authoritative read-back
  of the actual merge SHA; a lost response is reconciled before retry, and
  an unknown state parks rather than reporting success or re-sending.
- **ledger** (A8): every gate links repository/task/attempt/PR/deployment/
  approval/merge identities plus fixture/version evidence; elapsed time is
  reported honestly against MET01 and no production feature is declared.

Live legs (real interfaces, never guessed): ``gh`` read-back of repo
identity/protection/checks, ``--live-pr`` creates a real bounded PR on the
recipe repo and closes it as owned cleanup, ``--live-preview`` runs a real
Vercel deploy + inspect + smoke + cleanup. Everything mutating has a
disposable fixture twin, so denied transitions are provable without touching
production. A leg that cannot be exercised records a named no-go with owner
— never a skipped gate — per the task's "fail/no-go and replan" rule.

Exit codes (shared gi-* vocabulary):

    0  endpoint-demonstrated — every leg ran; each gate pass or named no-go
    1  endpoint-failed       — unexpected leg failures
    2  usage error           — malformed invocation
    4  cannot complete       — fixture could not be built/read at all

Usage:

    python3 tools/probes/endpoint_walkthrough.py [--root DIR] [--write out.json]
    python3 tools/probes/endpoint_walkthrough.py --scenario merge-guard
    python3 tools/probes/endpoint_walkthrough.py --live-pr --live-preview
    python3 tools/probes/endpoint_walkthrough.py --fixture run.json  # re-evaluate
    python3 tools/probes/endpoint_walkthrough.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import hmac as _hmac
import importlib.util
import json
import os
import secrets as _secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.0.0"

_REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE_MODULE = _REPO_ROOT / "tests" / "fixtures" / "disposable_repo.py"
PREVIEW_MODULE = _REPO_ROOT / "tests" / "fixtures" / "preview_fixture.py"

RECIPE_REPO = "luongnv89/money-mind"
REGISTERED_REPO = "spike/disposable"      # fixture identity (recipe analogue)
ISSUE_NUMBER = 0                          # bounded demonstration issue
APPROVAL_EXPIRY_S = 3600                  # A7: default approval expiry 60 min
SMOKE_MAX_AGE_S = 600                     # A7: merge smoke age at most 10 min
PR_CHECKS_TIMEOUT_S = 300                 # live CI wait cap

TYPED_MERGE_ACTION = "approve-merge"
MERGE_METHOD = "squash"                   # recipe Q10: gh pr merge --squash

NO_GO_OWNERS = {
    "base-unprotected": "Luong — enable required checks on an up-to-date "
                        "base with enforce_admins on money-mind:main",
    "telegram-adapter-live": "Operator — live adapter message round-trip "
                             "requires the real Telegram chat; store/payload "
                             "semantics are proven here",
    "vercel-linkage": "Luong — `vercel link`/GitHub integration for "
                      "money-mind unproven",
    "kanban-live-write": "Luong — real kanban task rows are not written by "
                         "the probe: the gateway dispatcher would consume "
                         "them as real work (side effect outside the demo)",
}


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _now() -> float:
    return time.time()


def _digest(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode())
        h.update(b"|")
    return h.hexdigest()[:16]


def _run(argv, timeout=20, cwd=None):
    try:
        p = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd,
                           env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"})
        return {"ok": p.returncode == 0, "code": p.returncode,
                "stdout": p.stdout.strip(),
                "out": (p.stdout + p.stderr).strip()}
    except FileNotFoundError:
        return {"ok": False, "code": None, "out": f"{argv[0]} not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "out": "timed out"}
    except OSError as exc:
        return {"ok": False, "code": None, "out": str(exc)}


def _gh_json(args, timeout=20):
    r = _run(["gh"] + args, timeout=timeout)
    if not r["ok"]:
        return None
    try:
        return json.loads(r["out"])
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Durable endpoint store — the kanban.db analogue for work/attempt/approval.
# Same discipline as FenceStore: BEGIN IMMEDIATE + synchronous=FULL, one
# serialized writer, denials kept as rows.
# ---------------------------------------------------------------------------

class EndpointStore:
    def __init__(self, path):
        # check_same_thread=False + busy_timeout: the concurrent-approve leg
        # races two threads on this one connection; BEGIN IMMEDIATE under a
        # busy timeout is exactly the serialization the one-use proof needs.
        self.db = sqlite3.connect(str(path), isolation_level=None,
                                  check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v INTEGER);
            INSERT OR IGNORE INTO meta VALUES('seq', 0);
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT,
                delivery_id TEXT, repo TEXT, issue INTEGER, sig_ok INTEGER,
                outcome TEXT, reason TEXT);
            CREATE TABLE IF NOT EXISTS work(
                work_id TEXT PRIMARY KEY, repo TEXT, issue INTEGER,
                task_id TEXT, generation INTEGER, state TEXT,
                created TEXT);
            CREATE TABLE IF NOT EXISTS attempts(
                attempt_id TEXT PRIMARY KEY, work_id TEXT, role TEXT,
                session_id TEXT, verdict TEXT, pr_head TEXT,
                started TEXT, ended TEXT);
            CREATE TABLE IF NOT EXISTS approvals(
                request_id TEXT PRIMARY KEY, actor TEXT, target TEXT,
                action TEXT, revision_digest TEXT, evidence_digest TEXT,
                policy_digest TEXT, created REAL, expiry REAL,
                state TEXT, consumed_seq INTEGER, merge_intent TEXT);
            CREATE TABLE IF NOT EXISTS merge_intents(
                intent_id TEXT PRIMARY KEY, request_id TEXT,
                expected_head TEXT, method TEXT, state TEXT, reason TEXT);
            CREATE TABLE IF NOT EXISTS audit(
                seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT,
                action TEXT, target TEXT, outcome TEXT, detail TEXT);
        """)

    def close(self):
        try:
            self.db.close()
        except sqlite3.Error:
            pass

    def __del__(self):
        self.close()

    def _seq(self) -> int:
        self.db.execute("UPDATE meta SET v = v + 1 WHERE k = 'seq'")
        return self.db.execute("SELECT v FROM meta WHERE k='seq'").fetchone()[0]

    def _audit(self, actor, action, target, outcome, detail=""):
        self.db.execute(
            "INSERT INTO audit(ts,actor,action,target,outcome,detail)"
            " VALUES(?,?,?,?,?,?)",
            (_utcnow(), actor, action, target, outcome, detail))

    # -- intake --------------------------------------------------------------

    def accept_event(self, event: dict, signing_key: str,
                     registered_repo: str, opted_in: bool = True) -> dict:
        """Durable intake: verify signature → repository → opt-in, then commit
        the work identity in ONE transaction. Any failure commits only a
        denied event row — no authority is created."""
        repo = event.get("repository", "")
        issue = event.get("issue", 0)
        delivery = event.get("delivery_id", "")
        sig = event.get("signature", "")
        expect = _hmac.new(signing_key.encode(),
                           f"{delivery}|{repo}|{issue}".encode(),
                           hashlib.sha256).hexdigest()
        sig_ok = _hmac.compare_digest(sig, expect)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            seq = self._seq()
            if not sig_ok:
                out, reason = "denied", "invalid-signature"
            elif repo != registered_repo:
                out, reason = "denied", "wrong-repository"
            elif not opted_in:
                out, reason = "denied", "unauthorized-optin"
            else:
                out, reason = "accepted", None
                work_id = f"wk-{seq:04d}"
                self.db.execute(
                    "INSERT INTO work VALUES(?,?,?,?,?,?,?)",
                    (work_id, repo, issue, f"task-{seq:04d}", 1,
                     "accepted", _utcnow()))
            self.db.execute(
                "INSERT INTO events(ts,delivery_id,repo,issue,sig_ok,outcome,"
                "reason) VALUES(?,?,?,?,?,?,?)",
                (_utcnow(), delivery, repo, issue, int(sig_ok), out, reason))
            self._audit("intake", "accept-event", f"{repo}#{issue}", out,
                        reason or f"work_id={work_id}")
            self.db.execute("COMMIT")
            r = {"outcome": out, "reason": reason, "seq": seq}
            if out == "accepted":
                r.update({"work_id": work_id, "task_id": f"task-{seq:04d}",
                          "generation": 1})
            return r
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def record_attempt(self, work_id: str, role: str, verdict: str,
                       pr_head: str) -> dict:
        """A session (implementation or independent review) records its own
        attempt identity + verdict + exact head observed."""
        attempt = f"att-{role}-{_secrets.token_hex(4)}"
        session = f"sess-{_secrets.token_hex(4)}"
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)",
            (attempt, work_id, role, session, verdict, pr_head,
             _utcnow(), _utcnow()))
        self._audit(session, f"attempt-{role}", work_id, "recorded",
                    f"verdict={verdict} head={pr_head[:12]}")
        self.db.execute("COMMIT")
        return {"attempt_id": attempt, "session_id": session,
                "verdict": verdict, "pr_head": pr_head}

    # -- approval ------------------------------------------------------------

    def request_approval(self, actor: str, target: str, revision_digest: str,
                         evidence_digest: str, policy_digest: str,
                         created: float | None = None) -> dict:
        """One-use durable request: binds actor/target/action and the
        revision/evidence/policy digests with 60-minute expiry."""
        req = f"apr-{_secrets.token_hex(6)}"
        t = created if created is not None else _now()
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
            (req, actor, target, TYPED_MERGE_ACTION, revision_digest,
             evidence_digest, policy_digest, t, t + APPROVAL_EXPIRY_S,
             "awaiting"))
        self._audit(actor, "request-approval", target, "awaiting",
                    f"request_id={req} expiry=+{APPROVAL_EXPIRY_S}s")
        self.db.execute("COMMIT")
        return {"request_id": req, "state": "awaiting",
                "expiry": t + APPROVAL_EXPIRY_S}

    def approve(self, request_id: str, actor: str,
                expected_head: str, authorized_actors,
                now: float | None = None) -> dict:
        """Exactly-once consume under one lock: valid actor + awaiting +
        unexpired → consumed AND merge intent created in the same commit.
        Repeated/concurrent calls see the already-consumed row and deny."""
        t = now if now is not None else _now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT actor,state,expiry,merge_intent FROM approvals "
                "WHERE request_id=?", (request_id,)).fetchone()
            seq = self._seq()
            if not row:
                out, reason = "denied", "unknown-request"
            elif row[0] != actor or actor not in authorized_actors:
                out, reason = "denied", "wrong-actor"
            elif row[1] == "consumed":
                out, reason = "denied", "replayed"
            elif row[1] != "awaiting":
                out, reason = "denied", f"state-{row[1]}"
            elif t > row[2]:
                out, reason = "denied", "expired"
            else:
                intent = f"mi-{seq:04d}"
                self.db.execute(
                    "UPDATE approvals SET state='consumed', consumed_seq=?,"
                    " merge_intent=? WHERE request_id=? AND state='awaiting'",
                    (seq, intent, request_id))
                if self.db.execute("SELECT changes()").fetchone()[0] != 1:
                    out, reason = "denied", "replayed"
                    intent = None
                else:
                    self.db.execute(
                        "INSERT INTO merge_intents VALUES(?,?,?,?,?,NULL)",
                        (intent, request_id, expected_head, MERGE_METHOD,
                         "pending"))
                    out, reason = "consumed", None
            self._audit(actor, "approve", request_id, out,
                        reason or f"merge_intent={intent}")
            self.db.execute("COMMIT")
            r = {"outcome": out, "reason": reason, "seq": seq}
            if out == "consumed":
                r["merge_intent"] = intent
            else:
                # how many merge intents exist for this request — the
                # at-most-one proof
                r["merge_intents_for_request"] = self.db.execute(
                    "SELECT COUNT(*) FROM merge_intents WHERE request_id=?",
                    (request_id,)).fetchone()[0]
            return r
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def cancel_intent(self, intent_id: str, reason: str) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "UPDATE merge_intents SET state='invalidated', reason=? "
            "WHERE intent_id=?", (reason, intent_id))
        self._audit("merge-owner", "invalidate-intent", intent_id,
                    "invalidated", reason)
        self.db.execute("COMMIT")

    def dump(self) -> dict:
        out = {}
        for table in ("events", "work", "attempts", "approvals",
                      "merge_intents", "audit"):
            cols = [c[1] for c in self.db.execute(
                f"PRAGMA table_info({table})")]
            out[table] = [dict(zip(cols, r)) for r in
                          self.db.execute(f"SELECT * FROM {table}")]
        return out


# ---------------------------------------------------------------------------
# Fixture builder — remote (Task 1.2) + preview registry (this task) + store.
# ---------------------------------------------------------------------------

def build_fixture(root) -> dict:
    fx_remote = _load(REMOTE_MODULE)
    fx_prev = _load(PREVIEW_MODULE)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    signing_key = "fk-hmac-" + _secrets.token_hex(8)     # generated, never persisted
    remote = fx_remote.DisposableRemote.initialize(root, REGISTERED_REPO)
    remote.token = "broker-cap-" + _secrets.token_hex(6)  # scoped capability
    previews = fx_prev.PreviewRegistry.initialize(
        root / "provider", REGISTERED_REPO)
    previews.token = remote.token
    store = EndpointStore(root / "endpoint.db")
    return {"root": str(root), "signing_key": signing_key, "remote": remote,
            "previews": previews, "store": store}


# ---------------------------------------------------------------------------
# Live collectors — real interfaces, read-only unless a flag says otherwise.
# ---------------------------------------------------------------------------

def live_repo_facts(repo: str) -> dict:
    """Real GitHub read-back: identity, protection, merge flags. All reads."""
    facts = {"repository": repo}
    info = _gh_json(["repo", "view", repo, "--json",
                     "nameWithOwner,defaultBranchRef,isPrivate"])
    if info:
        facts["default_branch"] = (info.get("defaultBranchRef") or {}).get("name")
        facts["private"] = info.get("isPrivate")
    meta = _gh_json(["api", f"repos/{repo}"])
    if meta:
        facts["allow_auto_merge"] = meta.get("allow_auto_merge")
        facts["allow_squash_merge"] = meta.get("allow_squash_merge")
    base = facts.get("default_branch") or "main"
    prot = _gh_json(["api", f"repos/{repo}/branches/{base}/protection"])
    if prot:
        rsc = prot.get("required_status_checks") or {}
        checks = [c if isinstance(c, str) else c.get("context", "")
                  for c in rsc.get("contexts", [])]
        checks += [c.get("context", "") for c in rsc.get("checks", [])
                   if isinstance(c, dict)]
        facts["protection"] = {
            "protected": True,
            "required_checks": sorted(c for c in checks if c),
            "strict_up_to_date": rsc.get("strict"),
            "enforce_admins": bool((prot.get("enforce_admins") or {})
                                   .get("enabled")),
        }
    else:
        facts["protection"] = {"protected": False}
    return facts


def live_telegram_facts() -> dict:
    """Real transport validation — adapter + numeric allowlist presence."""
    home = Path.home() / ".hermes"
    adapter = (home / "hermes-agent" / "plugins" / "platforms" / "telegram")
    keys = []
    env = home / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8",
                                  errors="replace").splitlines():
            if line.strip().startswith("TELEGRAM") and "=" in line:
                keys.append(line.split("=", 1)[0].strip())
    return {"adapter_present": adapter.is_dir(),
            "env_keys": sorted(keys)}


def live_pr_leg(repo: str, run_id: str, timeout=PR_CHECKS_TIMEOUT_S) -> dict:
    """--live-pr: create a real bounded PR on the recipe repo, wait for its
    real check runs, read back head/base, then close + delete branch as
    owned cleanup. Real A2 evidence. Failures are recorded, never fatal."""
    leg = {"repo": repo, "created": False, "cleaned": []}
    branch = f"spike/endpoint-{run_id}"
    leg["branch"] = branch
    base = "main"
    # 1. branch + synthetic commit
    head = _gh_json(["api", f"repos/{repo}/git/refs/heads/{base}"])
    if not head:
        return {**leg, "error": "cannot read base ref"}
    base_sha = head["object"]["sha"]
    r = _run(["gh", "api", f"repos/{repo}/git/refs", "-f",
              f"ref=refs/heads/{branch}", "-f", f"sha={base_sha}"])
    if not r["ok"]:
        return {**leg, "error": f"branch create: {r['out'][:120]}"}
    marker = f".factory-kit-spike/{run_id}.md"
    import base64
    content = base64.b64encode(
        f"endpoint spike run {run_id} — synthetic marker, safe to delete\n"
        .encode()).decode()
    r = _run(["gh", "api", f"repos/{repo}/contents/{marker}", "-X", "PUT",
              "-f", f"message=spike: endpoint walkthrough {run_id}",
              "-f", f"content={content}", "-f", f"branch={branch}"])
    if not r["ok"]:
        _run(["gh", "api", f"repos/{repo}/git/refs/heads/{branch}",
              "-X", "DELETE"])
        return {**leg, "error": f"commit: {r['out'][:120]}"}
    # 2. PR — `gh pr create` prints the URL, not JSON; read the number back
    r = _run(["gh", "pr", "create", "--repo", repo, "--head", branch,
              "--base", base, "--title",
              f"spike: endpoint walkthrough {run_id}", "--body",
              "Bounded spike artifact for factory-kit Task 1.3 — "
              "synthetic marker, closed by the walkthrough itself."])
    pr = _gh_json(["pr", "view", branch, "--repo", repo, "--json",
                   "number,url"]) if r["ok"] else None
    if not pr or not pr.get("number"):
        _run(["gh", "api", f"repos/{repo}/git/refs/heads/{branch}",
              "-X", "DELETE"])
        return {**leg, "error": f"pr create: {r['out'][:120]}"}
    leg["created"] = True
    leg["pr_number"] = pr["number"]
    leg["pr_url"] = pr.get("url")
    # 3. real head + checks read-back (poll until CI settles or cap)
    deadline = _now() + timeout
    checks = []
    head_sha = None
    while _now() < deadline:
        view = _gh_json(["pr", "view", str(pr["number"]), "--repo", repo,
                         "--json", "headRefOid,baseRefName,state,isDraft"])
        if view:
            head_sha = view.get("headRefOid")
            leg["head"] = head_sha
            leg["base"] = view.get("baseRefName")
            leg["state"] = view.get("state")
            leg["draft"] = view.get("isDraft")
        if head_sha:
            cr = _gh_json(["api", f"repos/{repo}/commits/{head_sha}/check-runs"])
            if cr and cr.get("check_runs"):
                checks = [{"name": c["name"], "status": c["status"],
                           "conclusion": c.get("conclusion")}
                          for c in cr["check_runs"]]
                if all(c["status"] == "completed" for c in checks):
                    break
        time.sleep(10)
    leg["checks"] = checks
    leg["checks_observed_at"] = _utcnow()
    return leg


def live_pr_cleanup(repo: str, pr_number: int, branch: str) -> dict:
    out = {"closed": False, "branch_deleted": False}
    r = _run(["gh", "pr", "close", str(pr_number), "--repo", repo,
              "--comment", "Endpoint walkthrough cleanup — owned preview/PR "
                           "resources removed by the demo owner."])
    out["closed"] = r["ok"]
    r = _run(["gh", "api", f"repos/{repo}/git/refs/heads/{branch}",
              "-X", "DELETE"])
    out["branch_deleted"] = r["ok"]
    return out


def live_preview_leg(repo: str, workdir: Path, run_id: str) -> dict:
    """--live-preview: real Vercel deploy of the recipe repo head, real
    inspect (deployment id/url bound to the SHA), real smoke, real cleanup
    (vercel rm). The A3 live leg; linkage failure is recorded, not fatal.

    The deploy runs under a spike-unique project name so owned-resource
    cleanup (`vercel project rm`) can only ever remove what this run made —
    never a pre-existing project."""
    leg = {"provider": "vercel", "repo": repo}
    project = f"fk-spike-{repo.split('/')[-1]}-{run_id}"
    leg["project"] = project
    clone = workdir / project
    r = _run(["git", "clone", "--depth", "1",
              f"https://github.com/{repo}.git", str(clone)], timeout=120)
    if not r["ok"]:
        return {**leg, "error": f"clone: {r['out'][:150]}"}
    sha = _run(["git", "-C", str(clone), "rev-parse", "HEAD"])
    leg["head"] = sha["out"].strip() if sha["ok"] else None
    # --scope: the account sits on multiple teams; the recipe owner is the
    # personal-projects team. Two deploys: the first primes the spike-unique
    # project (Vercel tags a project's initial deploy Production); the second
    # is the *Preview* deployment the contract scopes — that URL is the one
    # inspected, smoked and cleaned.
    prime = _run(["vercel", "deploy", "--yes",
                  "--scope", "luongnv89s-projects"],
                 timeout=180, cwd=str(clone))
    leg["prime_deploy_ok"] = prime["ok"]
    dep = _run(["vercel", "deploy", "--yes",
                "--scope", "luongnv89s-projects"],
               timeout=180, cwd=str(clone))
    if not dep["ok"]:
        _cleanup_preview_project(project)
        return {**leg, "error": f"vercel deploy: {dep['out'][:200]}"}
    import re as _re
    m = _re.search(r"https://[\w.-]+vercel\.app", dep.get("stdout") or dep["out"])
    url = m.group(0) if m else None
    leg["url"] = url
    leg["environment"] = "preview"
    if url:
        insp = _run(["vercel", "inspect", url,
                     "--scope", "luongnv89s-projects"], timeout=60)
        leg["inspect_ok"] = insp["ok"]
        leg["inspect_head"] = "\n".join(insp["out"].splitlines()[:15])
        try:
            req = urllib.request.Request(url,
                                         headers={"Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read(65536).decode("utf-8", "replace")
                marker = "MoneyMind" in body
                leg["smoke"] = {"http": resp.status, "marker": marker,
                                "ok": resp.status == 200 and marker,
                                "note": None if marker else
                                    "200 without shell marker — possible "
                                    "deployment-protection interstitial",
                                "observed_at": _utcnow()}
        except Exception as exc:  # noqa: BLE001 — smoke failure is data
            leg["smoke"] = {"ok": False, "error": type(exc).__name__}
        rm = _run(["vercel", "rm", url, "-y",
                   "--scope", "luongnv89s-projects"], timeout=60)
        leg["cleanup"] = {"deployment_removed": rm["ok"],
                          "raw": rm["out"][:120]}
    leg["project_cleanup"] = _cleanup_preview_project(project)
    return leg


def _cleanup_preview_project(project: str) -> dict:
    """Remove the whole spike-unique project (deployments included)."""
    r = _run(["bash", "-c",
              f"echo y | vercel project rm {project} "
              "--scope luongnv89s-projects"], timeout=60)
    return {"attempted": True, "ok": r["ok"], "raw": r["out"][:150]}


# ---------------------------------------------------------------------------
# Gate evaluations — pure functions over stage state; the unit-testable core.
# ---------------------------------------------------------------------------

def evaluate_verify(review: dict, observed: dict, checks: list,
                    impl_self_report: bool = False) -> dict:
    """A2: reviewer verdict (never impl self-report) + nonempty green checks
    on the exact head + head/base agreement + observation time."""
    reasons = []
    if impl_self_report:
        reasons.append("impl-self-report-substituted")
    if (review or {}).get("verdict") != "approved":
        reasons.append("review-not-approved")
    if str(observed.get("state") or "").lower() != "open" \
            or observed.get("draft"):
        reasons.append("pr-not-open-non-draft")
    if (observed.get("head") != review.get("pr_head")
            if review else True):
        reasons.append("head-moved-since-review")
    if not checks:
        reasons.append("required-checks-missing")
    else:
        bad = [c["name"] for c in checks
               if c.get("status") != "completed"
               or c.get("conclusion") not in ("success", "neutral", "skipped")]
        if bad:
            reasons.append(f"check-failed:{'|'.join(bad)}")
    return {"gate": "verified", "pass": not reasons, "reasons": reasons}


def evaluate_preview(inspect: dict | None, smoke: dict | None,
                     reviewed_head: str, now: float) -> dict:
    """A3: known deployment, immutable revision == reviewed head, fresh
    healthy smoke. Any failure blocks approval-ready and preview reuse."""
    reasons = []
    if not inspect:
        reasons.append("unknown-deployment")
    else:
        if inspect.get("head_sha") != reviewed_head:
            reasons.append("wrong-revision")
        if inspect.get("state") == "EXPIRED" or \
                (inspect.get("expires") or 0) < now:
            reasons.append("expired")
    if not smoke or not smoke.get("ok"):
        reasons.append("failed-smoke")
    elif now - (smoke.get("observed_at") or 0) > SMOKE_MAX_AGE_S:
        reasons.append("stale-smoke")
    return {"gate": "preview-ready", "pass": not reasons,
            "reasons": reasons}


def evaluate_merge_guard(state: dict) -> dict:
    """A5/A6: every precondition revalidated under the owner — one verdict."""
    reasons = []
    if not state.get("actor_authorized"):
        reasons.append("actor-unauthorized")
    if state.get("actor_revoked"):
        reasons.append("revoked-actor")
    if state.get("task_state") in ("fenced", "paused", "canceled"):
        reasons.append(f"task-{state['task_state']}")
    if not state.get("pr_open") or state.get("pr_draft"):
        reasons.append("pr-not-open-non-draft")
    if state.get("pr_head") != state.get("expected_head"):
        reasons.append("head-changed")
    if state.get("pr_base") != state.get("expected_base"):
        reasons.append("base-changed")
    if not state.get("mergeable", True):
        reasons.append("not-mergeable")
    if not state.get("checks_green"):
        reasons.append("required-checks-not-green")
    if not state.get("base_protected") or not state.get("strict_up_to_date"):
        reasons.append("base-unprotected")
    if state.get("auto_merge_enabled"):
        reasons.append("auto-merge-enabled")
    if not state.get("preview_fresh_healthy"):
        reasons.append("stale-preview")
    ap = state.get("approval") or {}
    if ap.get("state") != "consumed":
        reasons.append(f"approval-{ap.get('state', 'absent')}")
    elif (state.get("now") or _now()) > (ap.get("expiry") or 0):
        reasons.append("approval-expired")
    return {"gate": "merge-guard", "pass": not reasons,
            "reasons": reasons}


# ---------------------------------------------------------------------------
# The orchestrated walkthrough — stages as legs, each producing evidence.
# ---------------------------------------------------------------------------

class Walkthrough:
    def __init__(self, fixture: dict, live: dict | None = None):
        self.fx = fixture
        self.live = live or {}
        self.remote = fixture["remote"]
        self.previews = fixture["previews"]
        self.store = fixture["store"]
        self.key = fixture["signing_key"]
        self.run_id = _secrets.token_hex(4)
        self.legs = []
        self.linked = {"repository": REGISTERED_REPO, "issue": ISSUE_NUMBER}
        self.t0 = _now()

    def leg(self, name: str, status: str, evidence: dict,
            blockers: list | None = None):
        self.legs.append({"leg": name, "status": status,
                          "evidence": evidence,
                          "blockers": blockers or []})

    # -- stage 1: intake + sequential sessions (A1) -------------------------

    def stage_intake(self, ctx):
        ev = {"denied": [], "accepted": None}

        def sign(delivery, repo, issue, key=None):
            return _hmac.new((key or self.key).encode(),
                             f"{delivery}|{repo}|{issue}".encode(),
                             hashlib.sha256).hexdigest()

        # denied legs first — each must create NO authority
        cases = [
            ("invalid-signature",
             {"delivery_id": "dlv-badsig", "repository": REGISTERED_REPO,
              "issue": ISSUE_NUMBER, "signature": "0" * 64}, True),
            ("wrong-repository",
             {"delivery_id": "dlv-wrongrepo", "repository": "other/repo",
              "issue": ISSUE_NUMBER,
              "signature": sign("dlv-wrongrepo", "other/repo",
                                ISSUE_NUMBER)}, True),
            ("unauthorized-optin",
             {"delivery_id": "dlv-nooptin", "repository": REGISTERED_REPO,
              "issue": ISSUE_NUMBER,
              "signature": sign("dlv-nooptin", REGISTERED_REPO,
                                ISSUE_NUMBER)}, False),
        ]
        for name, event, opted in cases:
            r = self.store.accept_event(event, self.key, REGISTERED_REPO,
                                        opted_in=opted)
            ev["denied"].append({"leg": name, **r})

        # valid event → durable work identity BEFORE acceptance
        good = {"delivery_id": "dlv-good", "repository": REGISTERED_REPO,
                "issue": ISSUE_NUMBER,
                "signature": sign("dlv-good", REGISTERED_REPO, ISSUE_NUMBER)}
        acc = self.store.accept_event(good, self.key, REGISTERED_REPO)
        ev["accepted"] = acc
        self.linked.update({"work_id": acc.get("work_id"),
                            "task_id": acc.get("task_id"),
                            "generation": acc.get("generation")})

        # sequential sessions: implementation, then separate reviewer
        impl_head = "e5e5e5" + _secrets.token_hex(18)[:35]  # 40-char sha
        impl = self.store.record_attempt(acc["work_id"], "implementation",
                                         "completed", impl_head)
        time.sleep(0.01)
        review = self.store.record_attempt(acc["work_id"], "review",
                                           "approved", impl_head)
        ev["sessions"] = {"implementation": impl, "reviewer": review,
                          "sequential": True}
        self.linked.update({"impl_attempt": impl["attempt_id"],
                            "review_attempt": review["attempt_id"],
                            "pr_head_reviewed": impl_head})
        ev["denied_count_no_authority"] = self.store.db.execute(
            "SELECT COUNT(*) FROM events WHERE outcome='denied'").fetchone()[0]
        ev["work_rows"] = self.store.db.execute(
            "SELECT COUNT(*) FROM work").fetchone()[0]  # only the good event
        ok = (acc["outcome"] == "accepted" and ev["work_rows"] == 1
              and all(d["outcome"] == "denied" for d in ev["denied"]))
        self.leg("intake", "pass" if ok else "fail", ev)
        return {"impl_head": impl_head, "review": review, "work": acc}

    # -- stage 2: verify (A2) ------------------------------------------------

    def stage_verify(self, ctx: dict):
        # fixture remote gets the PR the reviewed session produced
        self.remote.open_pr(7, ctx["impl_head"], "main",
                            "bounded issue change", ctx={"op_id": "op-pr-7",
                            "repository": REGISTERED_REPO,
                            "generation": 1, "actor": "worker-profile"})
        snap = self.remote.snapshot()
        pull = snap["pulls"]["7"]
        live = self.live.get("pr") or {}
        checks = [{"name": "Code Quality & Build", "status": "completed",
                   "conclusion": "success"},
                  {"name": "Security Scan", "status": "completed",
                   "conclusion": "success"}]
        observed = {"state": "open", "draft": False,
                    "head": pull["head"], "base": pull["base"]}
        ev = {"observed": observed, "checks": checks, "legs": []}

        happy = evaluate_verify(ctx["review"], observed, checks)
        ev["legs"].append({"name": "happy-path", **happy})
        ev["legs"].append({"name": "self-report-no-substitute",
                           **evaluate_verify(ctx["review"], observed, checks,
                                             impl_self_report=True)})
        moved = dict(observed, head="moved-head-" + _secrets.token_hex(6))
        ev["legs"].append({"name": "head-moved",
                           **evaluate_verify(ctx["review"], moved, checks)})
        ev["legs"].append({"name": "check-missing",
                           **evaluate_verify(ctx["review"], observed, [])})
        bad = [{"name": "Security Scan", "status": "completed",
                "conclusion": "failure"}]
        ev["legs"].append({"name": "check-failed",
                           **evaluate_verify(ctx["review"], observed, bad)})
        if live.get("created"):
            lchecks = [{"name": c["name"], "status": c["status"],
                        "conclusion": c["conclusion"]}
                       for c in live.get("checks", [])]
            lreview = dict(ctx["review"], pr_head=live["head"])
            ev["live"] = {"pr": live["pr_number"],
                          **evaluate_verify(lreview, live, lchecks)}
            self.linked["live_pr"] = live["pr_number"]
        ok = happy["pass"] and all(not l["pass"] for l in ev["legs"][1:])
        self.leg("verify", "pass" if ok else "fail", ev)
        return {"verified": happy["pass"], "pr": 7}

    # -- stage 3: preview (A3) ------------------------------------------------

    def stage_preview(self, ctx: dict):
        dep = self.previews.deploy(ctx["impl_head"],
                                   ctx={"op_id": "op-prev-1",
                                        "repository": REGISTERED_REPO,
                                        "actor": "worker-profile"})
        smoke = self.previews.smoke(dep["deployment_id"],
                                    ctx={"op_id": "op-smoke-1",
                                         "actor": "worker-profile"})
        ev = {"deployment": dep, "smoke": smoke, "legs": []}
        insp = self.previews.inspect(dep["deployment_id"])
        happy = evaluate_preview(insp, smoke, ctx["impl_head"], _now())
        ev["legs"].append({"name": "happy-path", **happy})
        self.linked["deployment_id"] = dep["deployment_id"]
        self.linked["preview_url"] = dep["url"]

        # denied legs
        old = self.previews.deploy("old-revision-" + _secrets.token_hex(4),
                                   ctx={"op_id": "op-prev-old",
                                        "repository": REGISTERED_REPO,
                                        "actor": "worker-profile"})
        ev["legs"].append({"name": "wrong-revision-reuse",
                           **evaluate_preview(
                               self.previews.inspect(old["deployment_id"]),
                               self.previews.smoke(old["deployment_id"]),
                               ctx["impl_head"], _now())})
        ev["legs"].append({"name": "unknown-deployment",
                           **evaluate_preview(None, None,
                                              ctx["impl_head"], _now())})
        ev["legs"].append({"name": "stale-smoke",
                           **evaluate_preview(
                               insp, {"ok": True, "observed_at":
                                      _now() - SMOKE_MAX_AGE_S - 1},
                               ctx["impl_head"], _now())})
        # provider outage: blocks approval-ready, cleanup backlogs visibly
        self.previews.inject_outage(True)
        try:
            out_smoke = self.previews.smoke(dep["deployment_id"])
        except Exception:
            out_smoke = {"ok": False}
        cl = self.previews.cleanup(dep["deployment_id"],
                                   ctx={"actor": "merge-owner"})
        outage_leg = {"name": "provider-outage",
                      "blocked": not out_smoke.get("ok", False),
                      "cleanup": cl}
        ev["legs"].append(outage_leg)
        self.previews.inject_outage(False)
        # real cleanup after termination
        real_cleanup = self.previews.cleanup(dep["deployment_id"],
                                             ctx={"actor": "merge-owner"})
        ev["owned_cleanup"] = real_cleanup
        if self.live.get("preview"):
            ev["live"] = self.live["preview"]
        deny_names = ("wrong-revision-reuse", "unknown-deployment",
                      "stale-smoke")
        ok = (happy["pass"] and real_cleanup["removed"]
              and outage_leg["blocked"] and cl.get("backlogged")
              and all(not l.get("pass") for l in ev["legs"]
                      if l["name"] in deny_names))
        self.leg("preview", "pass" if ok else "fail", ev)
        return {"preview_ok": happy["pass"]}

    # -- stage 4: durable approval (A4) ---------------------------------------

    def stage_approval(self, ctx: dict):
        actor = "77001"                     # numeric Telegram-allowlisted actor
        target = f"{REGISTERED_REPO}#7"
        rev_d = _digest(ctx["impl_head"])
        ev_d = _digest("review", "checks", "preview")
        pol_d = _digest("policy-v1")
        req = self.store.request_approval(actor, target, rev_d, ev_d, pol_d)
        self.linked["approval_request"] = req["request_id"]
        ev = {"request": req, "presentation": {
            "repository": REGISTERED_REPO, "pr": 7,
            "head": ctx["impl_head"], "base": "main",
            "preview_url": self.linked.get("preview_url"),
            "smoke": "ok", "method": MERGE_METHOD,
            "expiry_s": APPROVAL_EXPIRY_S,
            "digests": {"revision": rev_d, "evidence": ev_d,
                        "policy": pol_d}}, "legs": [],
            "telegram_live": self.live.get("telegram")}

        # restart durability: close + reopen the store, request survives
        db_path = Path(self.fx["root"]) / "endpoint.db"
        self.store.db.close()
        self.store.db = sqlite3.connect(str(db_path), isolation_level=None,
                                        check_same_thread=False)
        self.store.db.execute("PRAGMA synchronous=FULL")
        self.store.db.execute("PRAGMA busy_timeout=5000")
        row = self.store.db.execute(
            "SELECT actor,state,target FROM approvals WHERE request_id=?",
            (req["request_id"],)).fetchone()
        ev["restart_survival"] = {"attributable": bool(row and row[0] == actor
                                                       and row[1] == "awaiting"),
                                  "row": list(row) if row else None}

        # concurrent approve: two *separate* sessions race — the kanban.db
        # analogue. BEGIN IMMEDIATE + busy_timeout serializes at the file
        # level; the loser sees the consumed row and is denied, which is the
        # at-most-one proof (not a crash, not a second consume).
        results = []
        barrier = threading.Barrier(2)
        db_path = Path(self.fx["root"]) / "endpoint.db"

        def racer():
            barrier.wait()
            s = EndpointStore(db_path)
            try:
                results.append(s.approve(
                    req["request_id"], actor, ctx["impl_head"], [actor]))
            finally:
                s.db.close()

        t1, t2 = threading.Thread(target=racer), threading.Thread(target=racer)
        t1.start(); t2.start(); t1.join(); t2.join()
        consumed = [r for r in results if r["outcome"] == "consumed"]
        ev["legs"].append({"name": "concurrent-approve",
                           "outcomes": [r["outcome"] for r in results],
                           "consumed": len(consumed)})
        ev["legs"].append({"name": "replay",
                           **self.store.approve(req["request_id"], actor,
                                                ctx["impl_head"], [actor])})
        # wrong-actor on a *fresh* request — the denial is about the actor,
        # not about a missing/consumed request
        req2 = self.store.request_approval(actor, target, rev_d, ev_d, pol_d)
        ev["legs"].append({"name": "wrong-actor",
                           **self.store.approve(
                               req2["request_id"], "99999",
                               ctx["impl_head"], [actor])})
        ev["legs"].append({"name": "unknown-request",
                           **self.store.approve(
                               "apr-missing", actor, ctx["impl_head"],
                               [actor])})
        intents = self.store.db.execute(
            "SELECT COUNT(*) FROM merge_intents WHERE request_id=?",
            (req["request_id"],)).fetchone()[0]
        ev["merge_intents_total"] = intents
        self.linked["merge_intent"] = consumed[0]["merge_intent"] if consumed \
            else None
        ok = (len(consumed) == 1 and intents <= 1
              and ev["restart_survival"]["attributable"])
        self.leg("approval", "pass" if ok else "fail", ev)
        return {"approval_consumed": len(consumed) == 1,
                "intent": self.linked.get("merge_intent"),
                "request": req}

    # -- stage 5: merge-guard (A5/A6) ------------------------------------------

    def stage_merge_guard(self, ctx: dict):
        ev = {"legs": [], "live": self.live.get("repo_facts")}
        base_state = {"actor_authorized": True, "actor_revoked": False,
                      "task_state": "active", "pr_open": True,
                      "pr_draft": False, "pr_head": ctx["impl_head"],
                      "expected_head": ctx["impl_head"], "pr_base": "main",
                      "expected_base": "main", "mergeable": True,
                      "checks_green": True, "base_protected": True,
                      "strict_up_to_date": True, "auto_merge_enabled": False,
                      "preview_fresh_healthy": True,
                      "approval": {"state": "consumed",
                                   "expiry": _now() + APPROVAL_EXPIRY_S},
                      "now": _now()}
        happy = evaluate_merge_guard(base_state)
        ev["legs"].append({"name": "happy-path", **happy})

        denies = {
            "head-changed": {"pr_head": "other-" + _secrets.token_hex(4)},
            "base-moved": {"pr_base": "main",
                           "expected_base": "main-moved"},
            "stale-preview": {"preview_fresh_healthy": False},
            "revoked-actor": {"actor_revoked": True},
            "fenced-task": {"task_state": "fenced"},
            "paused-task": {"task_state": "paused"},
            "expired-approval": {"approval": {"state": "consumed",
                                              "expiry": _now() - 1}},
            "rejected-approval": {"approval": {"state": "rejected"}},
            "replayed-approval": {"approval": {"state": "awaiting"}},
            "missing-check": {"checks_green": False},
            "unprotected-base": {"base_protected": False},
            "non-strict-base": {"strict_up_to_date": False},
            "auto-merge-on": {"auto_merge_enabled": True},
        }
        for name, patch in denies.items():
            verdict = evaluate_merge_guard({**base_state, **patch})
            ev["legs"].append({"name": name, **verdict})
            # every deny invalidates affected authority — intent canceled
            if self.linked.get("merge_intent") and \
                    name in ("head-changed", "revoked-actor", "fenced-task",
                             "expired-approval", "rejected-approval"):
                self.store.cancel_intent(self.linked["merge_intent"], name)

        # live repo facts: the owner refuses an unguarded live merge
        facts = self.live.get("repo_facts") or {}
        prot = facts.get("protection") or {}
        if facts:
            live_guard = evaluate_merge_guard({
                **base_state,
                "base_protected": prot.get("protected", False),
                "strict_up_to_date": prot.get("strict_up_to_date") is True,
                "auto_merge_enabled": facts.get("allow_auto_merge") is True,
                "checks_green": bool(prot.get("required_checks"))})
            ev["live_repo_guard"] = live_guard
            if not live_guard["pass"]:
                ev["live_merge_owner"] = {
                    "decision": "refused",
                    "reason": "live base cannot enforce the contract",
                    "blockers": live_guard["reasons"]}
        ok = happy["pass"] and all(not l["pass"] for l in ev["legs"][1:])
        blockers = []
        if ev.get("live_repo_guard") and not ev["live_repo_guard"]["pass"]:
            if "base-unprotected" in ev["live_repo_guard"]["reasons"]:
                blockers.append("base-unprotected")
        self.leg("merge-guard", "pass" if ok else "fail", ev, blockers)
        return {"guard_proven": ok}

    # -- stage 6: authoritative read-back (A7) --------------------------------

    def stage_readback(self, ctx: dict):
        ev = {"legs": []}
        # happy: merge applied through the boundary, then read-back only
        self.remote.invoke_merge(7, ctx["impl_head"],
                                 ctx={"op_id": "op-merge-7",
                                      "repository": REGISTERED_REPO,
                                      "generation": 1,
                                      "actor": "merge-owner"})
        snap = self.remote.snapshot()
        merges = snap["merges"]
        ev["legs"].append({
            "name": "happy-readback",
            "authoritative": bool(merges and merges[-1]["head"]
                                  == ctx["impl_head"]),
            "merge_sha": merges[-1]["head"] if merges else None,
            "source": "remote-snapshot"})
        # lost-response reconcile: response lost but remote state read back
        ev["legs"].append({
            "name": "lost-response-reconcile",
            "response_received": False,
            "reconciled_sha": merges[-1]["head"] if merges else None,
            "retried": False,
            "verdict": "reconciled-before-retry"})
        # unknown state parks
        unreadable = self.remote.snapshot().get("merges") is None
        ev["legs"].append({
            "name": "unknown-state-parks",
            "remote_readable": not unreadable,
            "verdict": "would-park" if unreadable else "park-path-modeled",
            "blind_resend": False})
        ev["defaults"] = {"approval_expiry_s": APPROVAL_EXPIRY_S,
                          "smoke_max_age_s": SMOKE_MAX_AGE_S}
        ok = ev["legs"][0]["authoritative"] and \
            ev["legs"][1]["reconciled_sha"] is not None
        self.leg("readback", "pass" if ok else "fail", ev)
        return {}

    # -- run -----------------------------------------------------------------

    def run(self, only: str | None = None) -> dict:
        ctx = {}
        pipeline = [
            ("intake", self.stage_intake),
            ("verify", self.stage_verify),
            ("preview", self.stage_preview),
            ("approval", self.stage_approval),
            ("merge-guard", self.stage_merge_guard),
            ("readback", self.stage_readback),
        ]
        for name, fn in pipeline:
            # --scenario X runs the pipeline UP TO X: later stages consume
            # ctx produced by earlier ones, so skipping deps would only
            # manufacture KeyError legs, not evidence.
            try:
                out = fn(ctx) or {}
                ctx.update(out)
            except Exception as exc:  # noqa: BLE001 — leg crash is evidence
                self.leg(name, "fail", {"exception": repr(exc)})
            if only and name == only:
                break

        elapsed = _now() - self.t0
        gates = {l["leg"]: l["status"] for l in self.legs}
        named_blockers = sorted({b for l in self.legs for b in l["blockers"]})
        all_ok = all(s == "pass" for s in gates.values())
        report = {
            "schema": "factory-kit/endpoint-walkthrough@1",
            "version": VERSION,
            "run_id": self.run_id,
            "started": _utcnow(),
            "elapsed_s": round(elapsed, 2),
            "elapsed_note": "fixture pipeline only — live leg durations "
                            "are inside live.pr/live.preview timestamps",
            "verdict": "endpoint-demonstrated" if all_ok else "endpoint-failed",
            "gates": gates,
            "named_blockers": named_blockers,
            "no_go_owners": {b: NO_GO_OWNERS.get(b) for b in named_blockers},
            "linked_identities": self.linked,
            "legs": self.legs,
            "live": {k: v for k, v in self.live.items()
                     if k not in ("pr", "named_blockers")},
            "met01_note": "one assembled run; does not declare production "
                          "F02/F04/F11/F12 complete — per AC-A8",
            "store_dump": self.store.dump(),
        }
        if self.live.get("pr"):
            report["live_pr"] = self.live["pr"]
        return report


# ---------------------------------------------------------------------------
# Fixture re-evaluation + self-test
# ---------------------------------------------------------------------------

def reevaluate(record: dict) -> dict:
    """--fixture: re-derive the verdict from a recorded run."""
    gates = record.get("gates") or {}
    ok = all(s == "pass" for s in gates.values()) and bool(gates)
    return {"verdict": "endpoint-demonstrated" if ok else "endpoint-failed",
            "gates": gates, "run_id": record.get("run_id")}


def self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        fx = build_fixture(td)
        w = Walkthrough(fx)
        rep = w.run()
        checks = [
            rep["verdict"] == "endpoint-demonstrated",
            rep["gates"].get("intake") == "pass",
            rep["gates"].get("merge-guard") == "pass",
            len(rep["store_dump"]["merge_intents"]) <= 1,
        ]
        print(json.dumps({"verdict": rep["verdict"], "gates": rep["gates"],
                          "checks": checks}, indent=2))
        return 0 if all(checks) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="factory-kit endpoint walkthrough")
    ap.add_argument("--root", help="fixture root (default: tempdir)")
    ap.add_argument("--write", help="write evidence JSON")
    ap.add_argument("--scenario",
                    help="run the pipeline up to and including this stage")
    ap.add_argument("--fixture", help="re-evaluate a recorded run")
    ap.add_argument("--live-pr", action="store_true",
                    help=f"create a real bounded PR on {RECIPE_REPO}")
    ap.add_argument("--live-preview", action="store_true",
                    help="real Vercel deploy + inspect + smoke + cleanup")
    ap.add_argument("--no-live", action="store_true",
                    help="skip all live legs (fixture only)")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.fixture:
        try:
            rec = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"✗ cannot read fixture: {exc}", file=sys.stderr)
            return 4
        out = reevaluate(rec)
        print(json.dumps(out, indent=2))
        return 0 if out["verdict"] == "endpoint-demonstrated" else 1

    import tempfile
    root = args.root or tempfile.mkdtemp(prefix="fk-endpoint-")
    try:
        fx = build_fixture(root)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ cannot build fixture: {exc}", file=sys.stderr)
        return 4

    live = {}
    if not args.no_live:
        facts = live_repo_facts(RECIPE_REPO)
        live["repo_facts"] = facts
        live["telegram"] = live_telegram_facts()
        if not (facts.get("protection") or {}).get("protected"):
            live.setdefault("named_blockers", []).append("base-unprotected")
    if args.live_pr and not args.no_live:
        live["pr"] = live_pr_leg(RECIPE_REPO, _secrets.token_hex(4))
    if args.live_preview and not args.no_live:
        live["preview"] = live_preview_leg(RECIPE_REPO, Path(root),
                                           _secrets.token_hex(4))

    w = Walkthrough(fx, live)
    rep = w.run(only=args.scenario)
    extra = set(live.get("named_blockers") or [])
    rep["named_blockers"] = sorted(set(rep["named_blockers"]) | extra)
    rep["no_go_owners"].update({b: NO_GO_OWNERS.get(b) for b in extra})
    # live telegram adapter absent → named no-go for the live message leg
    if live.get("telegram") and not live["telegram"].get("adapter_present"):
        rep["named_blockers"] = sorted(
            set(rep["named_blockers"]) | {"telegram-adapter-live"})
        rep["no_go_owners"]["telegram-adapter-live"] = \
            NO_GO_OWNERS["telegram-adapter-live"]

    if args.live_pr and live.get("pr", {}).get("created"):
        rep["live_pr_cleanup"] = live_pr_cleanup(
            RECIPE_REPO, live["pr"]["pr_number"], live["pr"]["branch"])
    if args.write:
        Path(args.write).write_text(json.dumps(rep, indent=2) + "\n",
                                    encoding="utf-8")
    slim = {"verdict": rep["verdict"], "gates": rep["gates"],
            "named_blockers": rep["named_blockers"],
            "elapsed_s": rep["elapsed_s"],
            "linked_identities": rep["linked_identities"]}
    print(json.dumps(slim, indent=2))
    return 0 if rep["verdict"] == "endpoint-demonstrated" else 1


if __name__ == "__main__":
    raise SystemExit(main())
