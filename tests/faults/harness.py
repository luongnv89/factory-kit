#!/usr/bin/env python3
"""Shared fault-matrix harness — issues #20/#21 (tasks 3.6/3.7, §8.2).

Two cooperating pieces:

- :class:`World` — *one independent repetition world*: a fresh temp
  root, the durable ``IntakeStore``, and the whole wired service stack
  (intake → lane → publication broker → verification → preview →
  approval → merge → control → recovery → notifications) over the
  scripted GitHub remote, preview provider, worker and issue-source
  fixtures. Every fault-injection seam the packaged fixtures expose is
  reachable from one place: ``remote.faults``, ``provider.faults`` /
  ``provider.outage``, ``worker.terminations``, ``source.faults`` and
  the notification ``fail_with`` transport seam. ``reopen()`` simulates
  a controller restart — the durable store is rebuilt from disk while
  the remote/provider/worker (which *are* GitHub / the provider /
  Hermes) keep their state.

- :class:`MatrixRunner` — executes each §8.2 row scenario ``REPS``
  times, each repetition on a *fresh* ``World`` (independent
  repetitions, no shared durable state), collects the named-assertion
  table, the bound identities (work/attempt/intent/request/PR/preview/
  notification ids) and the release-gate audits: the zero counters
  (duplicate PRs, unauthorized merges, accepted fenced results, lost
  acknowledged tasks) plus the verified-outcome audit (every
  ``verified`` evidence row carries an independent review, the
  required check identities and a head that still matches the remote;
  nothing stale or unknown is ever counted passing).

The suite is deterministic: one fake clock per world, scripted ports
only, no network, no sleeps. It validates the finite §8.2 scenario set
— it is not a proof against every security failure.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import platform
import secrets
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factory_kit.approval import ApprovalService  # noqa: E402
from factory_kit.config import registration, schema  # noqa: E402
from factory_kit.control import ControlService  # noqa: E402
from factory_kit.durable import store as durable  # noqa: E402
from factory_kit.execution import (  # noqa: E402
    ExecutionLane, ScriptedWorker)
from factory_kit.intake import service as intake_service  # noqa: E402
from factory_kit.merge import MergeService  # noqa: E402
from factory_kit.notifications.outbox import (  # noqa: E402
    NotificationService)
from factory_kit.preview import (  # noqa: E402
    PreviewService, ScriptedPreview)
from factory_kit.publication import (  # noqa: E402
    OPERATION_PR_PUBLISH, PublicationBroker)
from factory_kit.publication.remote import (  # noqa: E402
    ScriptedRemote)
from factory_kit.recovery import (  # noqa: E402
    RecoveryService, ScriptedIssueSource)
from factory_kit.verification import (  # noqa: E402
    VerificationService)

VERSION = "1.0.0"
REPS = 3

MANIFEST = ROOT / ".factory-kit.yml"
READY = {"verdict": "ready", "dispatch": "allowed", "blockers": [],
         "checked_at": "2026-10-05T00:00:00Z"}
VERSIONS = ["factory-kit/0.1.0", "manifest/1", "hermes/0.21.5"]
READINGS = {
    "tools": {"git": "/usr/bin/git", "gh": "/x/gh", "hermes": "/x/hermes"},
    "models": {"openai-codex": {"authenticated": True,
                                "available": ["gpt-6-luna"]}},
    "skills": {"issue-resolver": "0.19.0", "issue-pr-review": "0.19.0"},
}
BODY = "Intent.\n\n## Acceptance Criteria\n\n- [ ] A1. it works\n"
ACTOR = "luongnv89"
USER = 123456789
CHAT = -1001234567890
SIGNING = "s"
OPT_IN = "factory-kit"

HEAD_A = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
HEAD_B = "bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222"
BASE_SHA = "cccc3333cccc3333cccc3333cccc3333cccc3333"
BASE_B = "dddd4444dddd4444dddd4444dddd4444dddd4444"
BRANCH = "task-42"
PR = "17"

GREEN_RUNS = [
    {"id": "1", "name": "Code Quality & Build", "status": "completed",
     "conclusion": "success", "app": "github-actions",
     "details_url": "https://ci.test/1"},
    {"id": "2", "name": "Security Scan", "status": "completed",
     "conclusion": "success", "app": "github-actions",
     "details_url": "https://ci.test/2"},
]


def effective(**overrides):
    """The registered manifest's effective config + per-group patches."""
    eff = schema.load_manifest_file(MANIFEST)
    for group, patch in overrides.items():
        eff[group].update(patch)
    return eff


def _utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class World:
    """One independent repetition world (see module docstring)."""

    def __init__(self, root, *, eff_overrides=None, readings=None,
                 now0=None, remote=None, provider=None):
        self.root = Path(root)
        self.clock = [time.time() if now0 is None else float(now0)]
        self.eff = effective(**(eff_overrides or {}))
        self.repo_id = self.eff["identity"]["repo_id"]
        self.full_name = (f"{self.eff['identity']['owner']}/"
                          f"{self.eff['identity']['name']}")
        self.readings = READINGS if readings is None else readings
        self.registrations = registration.RegistrationStore(
            self.root / "registration.json")
        self.registrations.register(self.eff, readiness=READY,
                                    supported_versions=VERSIONS)
        self.db_path = self.root / "in.db"
        self.store = durable.IntakeStore(self.db_path)
        # The scripted ports *are* GitHub / the provider / Hermes — a
        # controller ``reopen`` rebuilds the services, never them.
        self.remote = remote or ScriptedRemote(self.repo_id,
                                               self.full_name)
        self.provider = provider or ScriptedPreview(now=self._now)
        self.source = ScriptedIssueSource(self.repo_id, self.full_name)
        self.worker = ScriptedWorker()
        self.alerts = []
        self.sent = []            # delivered telegram payloads
        self.fail_with = [None]   # telegram outage seam
        self._cid = [0]
        self._build_services()

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------

    def _now(self):
        return self.clock[0]

    def _transport(self, dest, text):
        if self.fail_with[0] is not None:
            raise self.fail_with[0]
        self.sent.append((dest, text))

    def _build_services(self):
        cfgs = {self.repo_id: self.eff}
        self.intake = intake_service.IntakeService(
            self.store, self.registrations, cfgs,
            signing_secret=SIGNING, opt_in_label=OPT_IN)
        self.approval = ApprovalService(
            self.store, self.registrations, cfgs, now=self._now)
        self.verifier = VerificationService(
            self.store, now=self._now, approval=self.approval)
        self.preview = PreviewService(
            self.store, self.provider, self.registrations, cfgs,
            now=self._now, approval=self.approval)
        self.lane = ExecutionLane(
            self.store, self.registrations, cfgs, worker=self.worker,
            workspace_root=self.root / "ws", readings=self.readings,
            now=self._now)
        self.control = ControlService(
            self.store, self.lane, self.registrations, cfgs,
            preview=self.preview, approval=self.approval,
            alert_sink=self.alerts, now=self._now)
        self.merge = MergeService(
            self.store, self.remote, self.approval,
            self.registrations, cfgs, now=self._now,
            alert_sink=self.alerts)
        self.notifier = NotificationService(
            self.store, self._transport, configs=cfgs, now=self._now)
        self.broker = PublicationBroker(
            self.store, self.remote, self.registrations, self.eff,
            clock=self._now, alert_sink=self.alerts)
        self.recovery = RecoveryService(
            self.store, self.registrations, cfgs, intake=self.intake,
            lane=self.lane, broker=self.broker,
            verification=self.verifier, preview=self.preview,
            merge=self.merge, issue_source=self.source,
            remote=self.remote, now=self._now,
            alert_sink=self.alerts, notifier=self.notifier)

    def reopen(self):
        """Simulated controller/host restart: the durable store is
        rebuilt from disk; GitHub/provider/Hermes keep their state;
        live worker handles are gone (a restart owns no live handle,
        so unended attempts read uncertain until evidence lands)."""
        self.store.close()
        self.store = durable.IntakeStore(self.db_path)
        self._build_services()

    # ------------------------------------------------------------------
    # intake helpers
    # ------------------------------------------------------------------

    def sign(self, delivery_id, repo_id, issue):
        return _hmac.new(
            SIGNING.encode(),
            f"{delivery_id}|{repo_id}|{issue}".encode(),
            hashlib.sha256).hexdigest()

    def env(self, issue, *, channel="reconciliation", delivery=None,
            sender=ACTOR, labels=(OPT_IN,), body=BODY, action=None,
            title="t", revision="2026-10-05", repo_id=None,
            repository=None, sign=True):
        if delivery is None:
            delivery = f"d-{channel[:3]}-{issue}-{uuid.uuid4().hex[:8]}"
        env = {"delivery_id": delivery, "channel": channel,
               "repo_id": repo_id or self.repo_id,
               "repository": repository or self.full_name,
               "issue": issue,
               "action": action or
               ("labeled" if channel == "webhook" else "reconcile"),
               "sender": sender, "labels": list(labels),
               "issue_title": title, "issue_body": body,
               "issue_revision": revision}
        if channel == "webhook":
            env["signature"] = (self.sign(delivery, env["repo_id"],
                                          issue) if sign else "0" * 64)
        return env

    def deliver(self, env):
        return self.intake.deliver(env)

    def accept(self, issue=42, *, bind=True, **env_kw):
        env = self.env(issue, **env_kw)
        ack = self.intake.deliver(env)
        assert ack["outcome"] == "accepted", ack
        work_key = ack["work_key"]
        self.registrations.record_work(self.repo_id, work_key)
        if bind:
            self.store.activate_work(work_key)
        return work_key

    # ------------------------------------------------------------------
    # execution / verification helpers
    # ------------------------------------------------------------------

    def attempt(self, work_key, role, verdict="completed", *,
                session=None, active_seconds=10, generation=None):
        """Mint one durable finished attempt — the review/independence
        fixture the verification gate binds (verify-fixture pattern)."""
        n = self.store.count_attempts(work_key) + 1
        task_id = self.store.get_work(work_key)["task_id"]
        aid = f"{task_id}-a{n:02d}"
        session = session or f"sess-{aid}"
        gen = generation if generation is not None else \
            self.store.get_work(work_key)["generation"]
        out = self.store.begin_execution_attempt(
            work_key, attempt_id=aid, role=role, task_id=task_id,
            generation=gen, session_id=session, runtime="hermes-kanban",
            model="gpt-6-luna", skills="{}", config_digest="c",
            policy_digest="p", workspace=str(self.root / "ws"),
            limits="{}", now_epoch=self.clock[0])
        assert out["outcome"] == "active", out
        self.store.finish_execution_attempt(
            aid, verdict=verdict, outcome="completed",
            active_seconds=active_seconds, usage={"t": 1},
            duration_s=10)
        return aid, session

    def pair(self, work_key, *, impl_verdict="completed",
             review_verdict="approved"):
        impl = self.attempt(work_key, "implementation", impl_verdict)
        rev = self.attempt(work_key, "review", review_verdict)
        return {"impl": impl, "review": rev}

    def review(self, work_key, pair=None, sha=HEAD_A,
               verdict="approved", **kw):
        pair = pair or self.pair(work_key)
        out = self.verifier.record_review(
            work_key, attempt_id=pair["review"][0],
            session_id=pair["review"][1], sha=sha, verdict=verdict,
            findings=kw.pop("findings", ["diff meets criteria"]),
            artifacts=kw.pop("artifacts", [{"kind": "review-log",
                                            "ref": "logs/r.jsonl"}]),
            **kw)
        assert out["outcome"] == "recorded", out
        return out["review_id"]

    def publish(self, work_key, sha=HEAD_A, *, branch=BRANCH,
                base="main", checks=None, pr=PR):
        """Remote PR + branch + checks the verification gate reads."""
        self.remote.branches[branch] = sha
        self.remote.branches.setdefault(base, BASE_SHA)
        rec = self.remote.open_pr(branch, base, "implement issue",
                                  {"work_key": work_key,
                                   "authority_key":
                                       self.store.get_work(work_key)
                                       ["authority_key"],
                                   "generation": 1,
                                   "actor_ref": "w"})
        self.store.link_pr(work_key, rec["number"])
        self.remote.checks[sha] = list(
            GREEN_RUNS if checks is None else checks)
        return rec["number"]

    def verify(self, work_key, sha=HEAD_A, **kw):
        return self.verifier.verify(work_key, sha, remote=self.remote,
                                    effective=self.eff, **kw)

    def verified(self, work_key, sha=HEAD_A, **kw):
        """A verified observation through the real gate (review + PR +
        checks), the same path production evidence takes."""
        self.pair(work_key)
        self.review(work_key, sha=sha)
        self.publish(work_key, sha, **kw)
        return self.verify(work_key, sha)

    def evidence(self, work_key, head_sha=HEAD_A, base_sha=BASE_SHA, *,
                 status="verified", pr_number=PR, checks=None):
        """Direct verified-evidence row — the merge-fixture pattern,
        used where verification itself is not the row under test."""
        with self.store.transact() as tx:
            tx.record_evidence(
                f"ev-{head_sha[:6]}-{len(self.store.evidence_rows())}",
                work_key=work_key, seq=tx.next_seq(),
                pr_number=str(pr_number),
                pr_url=f"https://github.test/{self.full_name}/pull/"
                       f"{pr_number}",
                head_sha=head_sha, base_name="main", base_sha=base_sha,
                checks=list(GREEN_RUNS if checks is None else checks),
                review_id="rev-1", review_session="sess-1",
                contract_digest="cd-1", status=status,
                reason="checks-green" if status == "verified"
                else status)
        return self.store.latest_evidence(work_key)

    def seed_merge_remote(self, head=HEAD_A, base=BASE_SHA, pr=PR):
        """The guarded merge's remote truth: branch/PR/checks/protection."""
        self.remote.branches[BRANCH] = head
        self.remote.branches["main"] = base
        self.remote.pulls[str(pr)] = {
            "number": str(pr), "head": BRANCH, "base": "main",
            "base_sha": base, "title": "t", "state": "open",
            "url": f"https://example.test/{self.full_name}/pull/{pr}",
            "identity": {}}
        self.remote.checks[head] = [dict(r) for r in GREEN_RUNS]

    # ------------------------------------------------------------------
    # approval / merge run-up helpers
    # ------------------------------------------------------------------

    def deploy(self, work_key, head_sha=HEAD_A, **kw):
        return self.preview.deploy_preview(work_key, head_sha=head_sha,
                                           **kw)

    def approved(self, issue=42, *, head_sha=HEAD_A,
                 with_evidence="service"):
        """The full F11/F12 run-up: accepted work + verified evidence +
        verified preview + issued request + approved decision.

        ``with_evidence="service"`` builds evidence through the real
        verification gate (``verified()`` publishes + links the remote
        PR itself — never a second, stray pull record); ``"direct"``
        writes the evidence row and seeds the pull it names
        (merge-fixture style)."""
        work_key, request_id = self.requested(
            issue, head_sha=head_sha, with_evidence=with_evidence)
        dec = self.approval.decide(
            request_id=request_id,
            actor_ref=f"telegram:{USER}",
            chat_ref=f"telegram:{CHAT}", verdict="approve")
        assert dec["outcome"] == "approved", dec
        return work_key, request_id

    def requested(self, issue=42, *, head_sha=HEAD_A,
                  with_evidence="service"):
        """Like :meth:`approved` minus the decision — the durable
        request sits ``awaiting`` (forged/replayed/expired/rejected/
        revoked-decision rows need the grant unspent)."""
        work_key = self.accept(issue)
        if with_evidence == "service":
            out = self.verified(work_key, head_sha)
            assert out["status"] == "verified", out
        else:
            self.seed_merge_remote(head_sha)
            self.evidence(work_key, head_sha)
        dep = self.deploy(work_key, head_sha)
        assert dep["outcome"] in ("verified", "converged"), dep
        req = self.approval.request_approval(work_key)
        assert req["outcome"] == "requested", req
        return work_key, req["request_id"]

    # ------------------------------------------------------------------
    # control helpers
    # ------------------------------------------------------------------

    def msg(self, action, *, issue=1, generation=1, user=USER,
            chat=CHAT, command_id=None, **fields):
        self._cid[0] += 1
        m = {"command_id": command_id or f"c-{self._cid[0]:03d}",
             "actor": {"user_id": user}, "chat": {"chat_id": chat},
             "action": action, "repo_id": self.repo_id,
             "issue": issue, "generation": generation}
        m.update(fields)
        return m

    # ------------------------------------------------------------------
    # publication helper
    # ------------------------------------------------------------------

    def publish_request(self, work_key=None, *, sha=None,
                        operation=OPERATION_PR_PUBLISH,
                        target="factory-kit/impl-42", base="main",
                        actor=ACTOR, **overrides):
        req = {"work_key": work_key, "operation": operation,
               "target": target, "base": base,
               "title": "implement issue",
               "repository": {"repo_id": self.repo_id,
                              "full_name": self.full_name},
               "expected_revision": sha or HEAD_A,
               "actor_ref": actor,
               "claim_expires_epoch": self.clock[0] + 600}
        req.update(overrides)
        return req


# ---------------------------------------------------------------------------
# The release-gate audits — issue #20 A7/A8, issue #21 A8.
# ---------------------------------------------------------------------------

def audit_world(world):
    """Compute the zero counters + verified-outcome audit for a world.

    Returns ``{"counters": {...}, "violations": [...], "evidence_audit":
    {...}}``. Any nonempty ``violations`` entry fails the repetition —
    the release gate stays closed.
    """
    store, remote = world.store, world.remote
    violations = []
    counters = {"duplicate_prs": 0, "unauthorized_merges": 0,
                "accepted_fenced_results": 0,
                "lost_acknowledged_tasks": 0,
                "stale_counted_passing": 0}

    # -- duplicate PRs: one work item reaching >1 distinct remote pull
    #    through its publication intents. (Several works each opening
    #    their own pull — even at the same head — is not duplication.)
    pr_by_work = {}
    for intent in store._rows("publication_intents"):
        pr = intent.get("pr_number")
        if pr:
            pr_by_work.setdefault(intent["work_key"], set()).add(
                str(pr))
    counters["duplicate_prs"] = sum(
        len(v) - 1 for v in pr_by_work.values() if len(v) > 1)
    if counters["duplicate_prs"]:
        violations.append(
            {"counter": "duplicate_prs",
             "detail": {k: sorted(v) for k, v in pr_by_work.items()
                        if len(v) > 1}})

    # -- unauthorized merges: a factory-actor merge with no merged
    #    intent carrying its SHA (external/human merges record the
    #    actual actor — reconciled, not unauthorized).
    merged_intents = {}
    for intent in store._rows("merge_intents"):
        if intent.get("merged"):
            merged_intents.setdefault(intent["work_key"], set()).add(
                intent.get("merge_sha"))
    for num, m in (remote.mergers or {}).items():
        pull = remote.pulls.get(str(num)) or {}
        wk = (pull.get("identity") or {}).get("work_key")
        factory_merge = m.get("by") == remote.actor_login
        bound = wk and m.get("sha") in merged_intents.get(wk, set())
        if factory_merge and not bound:
            counters["unauthorized_merges"] += 1
            violations.append({"counter": "unauthorized_merges",
                               "detail": {"pr": num, "by": m.get("by"),
                                          "sha": m.get("sha")}})

    # -- accepted fenced results: a result committed ``accepted`` whose
    #    work's generation fence predates the result's commit — the
    #    "no stale completion" invariant, from durable rows.
    fenced = {r["work_key"]: r for r in store._rows("execution_fence")
              if r["fenced"]}
    for res in store.result_rows():
        if not res["accepted"]:
            continue
        fence = fenced.get(res["work_key"])
        if fence and str(res["ts"]) > str(fence["updated_at"]):
            counters["accepted_fenced_results"] += 1
            violations.append(
                {"counter": "accepted_fenced_results",
                 "detail": {"result_id": res["result_id"],
                            "work_key": res["work_key"]}})

    # -- lost acknowledged tasks (issue #20 A8): deliveries the intake
    #    acknowledged (accepted/reconciled) whose work identity has no
    #    durable work row.
    lost = store._q(
        "SELECT d.delivery_id, d.work_key FROM deliveries d "
        "LEFT JOIN work w ON w.work_key = d.work_key "
        "WHERE d.outcome IN ('accepted','reconciled') "
        "AND w.work_key IS NULL").fetchall()
    counters["lost_acknowledged_tasks"] = len(lost)
    if lost:
        violations.append({"counter": "lost_acknowledged_tasks",
                           "detail": [list(r) for r in lost]})

    # -- verified-outcome audit (issue #20 A8): every ``verified``
    #    evidence row carries an independent review + the required check
    #    identities, and a *latest* verified row's head still matches
    #    the authoritative remote head (stale/unknown never pass).
    required = set((world.eff.get("verification") or {})
                   .get("required_checks", {}).get("contexts") or [])
    audited, bad = 0, []
    for work in store._rows("work"):
        wk = work["work_key"]
        latest = store.latest_evidence(wk)
        for ev in store.evidence_rows(wk):
            if ev["status"] != "verified":
                continue
            audited += 1
            checks = json.loads(ev["checks"]) if ev["checks"] else []
            names = {c.get("name") for c in checks}
            problems = []
            if not ev.get("review_id"):
                problems.append("missing-independent-review")
            missing = required - names
            if missing:
                problems.append(f"missing-checks:{sorted(missing)}")
            green = all(c.get("conclusion") == "success"
                        for c in checks if c.get("name") in required)
            if required and not green:
                problems.append("required-check-not-green")
            if problems:
                bad.append({"work_key": wk,
                            "evidence_id": ev["evidence_id"],
                            "problems": problems})
        # a *current* verified observation must still match the remote
        # head — a head that moved must have been marked stale
        if latest and latest["status"] == "verified" and \
                work["linked_pr"]:
            pull = remote.pulls.get(str(work["linked_pr"]))
            live_head = (remote.branches.get(pull["head"])
                         if pull else None)
            if live_head is not None and \
                    live_head != latest["head_sha"]:
                counters["stale_counted_passing"] += 1
                bad.append({"work_key": wk,
                            "evidence_id": latest["evidence_id"],
                            "problems": [
                                f"verified-head-stale:"
                                f"{latest['head_sha'][:8]}!="
                                f"{live_head[:8]}"]})
    if bad:
        violations.append({"counter": "verified_outcome_audit",
                           "detail": bad})
    return {"counters": counters,
            "violations": violations,
            "evidence_audit": {"verified_rows": audited,
                               "defective_rows": len(bad)}}


# ---------------------------------------------------------------------------
# The runner — REPS independent repetitions per row, fresh world each rep.
# ---------------------------------------------------------------------------

def _safe_str(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def run_row(row_id, scenario, *, reps=REPS, world_kw=None):
    """Execute ``scenario`` ``reps`` times on fresh worlds.

    ``scenario(world, ctx)`` drives the fault and records assertions via
    ``ctx["check"](name, condition, **measured)`` plus bound identities
    via ``ctx["ids"].setdefault(key, []).append(value)``. Each
    repetition additionally runs :func:`audit_world`; any failed
    assertion or audit violation fails the repetition (the release
    gate stays closed for that row).
    """
    rep_reports = []
    for rep in range(1, reps + 1):
        assertions, ids, notes = [], {}, []
        ctx = {"assertions": assertions, "ids": ids, "notes": notes}

        def check(name, condition, **measured):
            assertions.append({
                "name": name, "pass": bool(condition),
                "measured": {k: _safe_str(v)
                             for k, v in measured.items()}})
            return bool(condition)

        ctx["check"] = check
        error = None
        with tempfile.TemporaryDirectory(
                prefix=f"fk-matrix-{row_id.lower()}-") as td:
            world = World(Path(td), **(world_kw or {}))
            try:
                scenario(world, ctx)
            except Exception as exc:  # a crash IS a failed repetition
                error = {"type": type(exc).__name__,
                         "detail": str(exc)[:400]}
                check("scenario-completed", False,
                      error=error["type"], detail=error["detail"])
            try:
                audit = audit_world(world)
            except Exception as exc:
                audit = {"counters": {}, "evidence_audit": {},
                         "violations": [{"counter": "audit-crashed",
                                         "detail":
                                         type(exc).__name__}]}
            try:
                world.store.close()
            except Exception:
                pass
        failed = [a["name"] for a in assertions if not a["pass"]]
        rep_ok = not failed and not audit["violations"] and \
            error is None
        rep_reports.append({
            "rep": rep, "pass": rep_ok,
            "assertions": assertions,
            "failed_assertions": failed,
            "identities": ids,
            "audit": audit,
            "error": error,
            "notes": notes})
    passed = sum(1 for r in rep_reports if r["pass"])
    return {"row": row_id, "status": "pass" if passed == reps
            else "fail", "passed_reps": passed, "required_reps": reps,
            "reps": rep_reports}


def run_matrix(rows, *, reps=REPS, world_kw=None):
    """Run every ``{row_id: scenario}`` row × ``reps`` fresh worlds."""
    started = time.time()
    results = {rid: run_row(rid, fn, reps=reps, world_kw=world_kw)
               for rid, fn in rows.items()}
    failed = [rid for rid, r in results.items()
              if r["status"] != "pass"]
    return {"schema": "factory-kit/fault-matrix@1",
            "version": VERSION,
            "run_id": secrets.token_hex(4),
            "started": _utcnow(),
            "elapsed_s": round(time.time() - started, 2),
            "verdict": ("fault-matrix-passed" if not failed
                        else "fault-matrix-failed"),
            "rows": results,
            "failed_rows": failed,
            "totals": {
                "rows": len(results),
                "repetitions": sum(r["required_reps"]
                                   for r in results.values()),
                "passed_repetitions": sum(r["passed_reps"]
                                          for r in results.values())},
            "environment": environment()}


def environment():
    """The exact host/runtime/repo configuration the archive records
    (issue #20 A7 / issue #21 A8) — versions, fixture identity and the
    bound manifest digests."""
    eff = effective()
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "harness_version": VERSION,
        "manifest": str(MANIFEST),
        "manifest_digest": schema.effective_digest(eff),
        "policy_digest": schema.policy_digest(eff),
        "supported_versions": VERSIONS,
        "fixture_identity": {
            "remote": "ScriptedRemote (deterministic remote.json)",
            "preview": "ScriptedPreview",
            "worker": "ScriptedWorker",
            "issue_source": "ScriptedIssueSource",
            "stores": "IntakeStore SQLite — BEGIN IMMEDIATE + "
                      "synchronous=FULL",
            "repository": eff["identity"]["owner"] + "/" +
                          eff["identity"]["name"],
            "repo_id": eff["identity"]["repo_id"],
        },
        "reproduce": "python3 tools/probes/fault_matrix.py "
                     "[--row FAULTNN] [--write out.json]",
    }


def reevaluate(report):
    """Re-check a recorded matrix report — same verdict, no re-run."""
    rows = report.get("rows") or {}
    failed = [rid for rid, r in rows.items()
              if r.get("status") != "pass"]
    totals = report.get("totals") or {}
    return {"verdict": ("fault-matrix-passed" if not failed
                        else "fault-matrix-failed"),
            "rows": len(rows), "failed_rows": failed,
            "repetitions": totals.get("repetitions"),
            "passed_repetitions":
            totals.get("passed_repetitions")}
