# Support matrix — tested pins only (issue #23 / Task 3.9, A1/A4/A5)

The single supported configuration. **Only rows exercised by the test
suite are listed**; anything not listed is unsupported, not merely
unverified. Readiness enforces this matrix — deviations are named
blockers, never silent degradation.

## Tested pin set

| Component | Supported pin | Evidence |
|---|---|---|
| Kit version | `factory-kit 0.1.0` | `factory_kit.VERSION`; `version_set` in readiness reports |
| Manifest schema | `factory_kit: 1` | `tests/config/`; non-appliable-plan leg in recipe tests |
| Host | Local operator host M1 | `docs/decisions/tested-recipe-selection.md` |
| OS / arch | macOS 27.0.0 arm64; Linux also accepted by readiness | readiness `unsupported-host` blocker; recipe walkthrough on macOS |
| Python | 3.14.7, stdlib-only | suite runner |
| git | 2.55.0 | host probe |
| GitHub CLI | 2.100.0, `gh auth status` → scoped assignee login | `docs/decisions/credential-mechanism.md` |
| Node / npm | 26.7.0 / 11.19.0 (target-project CI toolchain, not kit runtime) | host probe |
| Hermes | **≥ 0.21.5**, tested at 0.21.5 | `readiness.MIN_HERMES`; `unsupported-version` fixture |
| Runtime | `hermes-kanban` (kanban profile worker, gateway-embedded dispatcher) | `unsupported-runtime` blocker; spike boundary map |
| Model | `openai-codex / gpt-6-luna` — implementation and review roles | `docs/decisions/tested-recipe-selection.md` |
| Optional model | `dspark / montimage-dgx-spark` — only when the endpoint probes reachable; unreachable → `model-unavailable` blocker | `model-unavailable` fixture |
| Skills (pinned) | `issue-resolver: 0.19.0`, `issue-pr-review: 0.19.0` | `skill-version-mismatch` blocker |
| Repository identity | Node ID keyed (fixture `R_TEST0001`; selected repo `R_kgDOQncOdA`) | manifest `identity.repo_id` |
| GitHub permissions | scoped assignee `gh` login (`repo`, `read:org`, `gist`, `admin:public_key` at probe time); base protection enforcing required checks `Code Quality & Build` + `Security Scan` | `base-unprotected`, `verification-contract-unmet` fixtures |
| Preview provider | Vercel preview, unlisted, ≤24 h TTL, cleanup ≤60 min | `docs/decisions/tested-recipe-selection.md` Q10 |
| Merge method | Squash via human-approved `gh pr merge --squash`, expected-head recheck; auto-merge disabled | PRD F12; approval tests |
| Telegram transport | Gateway-authorized bot; numeric user/chat allowlists (manifest `authorization.telegram_users/chats`) | `factory_kit/notification`, control tests |
| Persistence | kit SQLite `in.db` + `registration.json` under `~/.hermes/profiles/<profile>/factory-kit/`; Hermes `kanban.db` is Hermes-owned | `tests/recovery/` |

## Explicitly unsupported (named so nobody claims them)

- `pip install factory-kit`, `hermes plugins install`, any package or
  public-registry distribution — **no install command exists or is
  documented** (Q8 unresolved; see `docs/decisions/q8-distribution.md`).
- External CLI harness lanes (`claude`, `codex`, `opencode`) — present
  on the probe host but explicitly *not* a paved integration path.
- Concurrent multi-project or multi-active-task execution — the lane is
  one task by contract (F03).
- Autonomous merge, GitHub auto-merge, production deploy, package
  publish — disabled operations in the manifest.
- Public distribution / external pilot — blocked on Q8 decisions
  (§9.1); this is an explicitly internal recipe (Task 3.9 A5).

## Kit CI vs target-project CI (A4)

- **Kit CI** (this repository) runs the identity/lifecycle/security
  contracts: `python3 -m unittest discover -s tests -v` — intake
  convergence, fencing, recovery, publication intents, approvals,
  setup/removal, webhook verification, recipe walkthroughs.
- **Target-project CI remains authoritative**: the default plan
  inventories existing workflows, checksums them into `preserved[]`, and
  proposes zero edits; required checks (`Code Quality & Build`,
  `Security Scan`) are enforced by base protection, not by the kit.
- **Compatibility/version evidence** captured for the record:
  `docs/measurements/v1-targets.md`, `docs/evidence/fault-matrix.md`,
  `tests/fixtures/readiness/*.json` (six named probe fixtures),
  `tests/fixtures/setup_repo.py` (disposable-repository fixture the
  recipe tests run against).
