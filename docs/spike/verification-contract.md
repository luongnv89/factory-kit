# Verification contract — `luongnv89/money-mind` recipe

A4 deliverable for issue #2: the nonempty verification contract the tested
recipe runs under. A missing meaningful preview or incompatible GitHub rules
blocks the full endpoint (§8.1 go/no-go).

## 1. Target-project CI — preserved, authoritative

`.github/workflows/ci.yml` (`CI Quality Checks`) is **not modified** by the
kit. Required check contexts (job names):

| Context | Steps |
|---|---|
| `Code Quality & Build` | `npm ci` → `format:check` → `lint` → `typecheck` → `test` (Vitest) → `build` |
| `Security Scan` | Gitleaks, `npm audit --audit-level=high`, Trivy fs scan |

Setup-time explicit acceptance commands (local parity, recorded in the
project manifest): `npm run format:check && npm run lint && npm run typecheck
&& npm test && npm run build`. A green worker exit is **never** the
completion contract — GitHub check runs on the exact PR head are (F04).

## 2. Preview — one non-production meaningful preview

| Field | Value |
|---|---|
| Provider | Vercel (account `luongnv89`, CLI 55.0.0) |
| Environment | **Preview** deployments only — production deploys are out of scope |
| Surface | Static Vite SPA; `vercel.json` headers + `/typesafe-api/:path*` rewrite already shipped |
| Artifact identity | Vercel deployment ID + URL bound to the PR head SHA (via `vercel inspect`/GitHub Deployments API) |
| Visibility | **Agreed:** preview URL reachable by link (not indexed); the repository is public. If Vercel deployment protection is enabled on the project, the smoke path uses the documented bypass or the protection stays off for previews — recorded at project link time |
| Synthetic data | `lib/demoData.ts` deterministic fixed-seed dataset only; **no real bank/CSV data, no user API keys** in the preview environment |
| Smoke command | `curl -fsS -o /dev/null -w '%{http_code}' "$PREVIEW_URL" == 200` **and** `curl -fsS "$PREVIEW_URL" \| grep -q 'MoneyMind'` (SPA shell marker) |
| Limits | One active preview per task; 24 h TTL; cleanup ≤60 min after task termination; provider outage → visible cleanup backlog, never a false removal claim (F11) |

**Meaningful-preview test:** the SPA must actually load — a `404`/`401`/empty
shell is a failed preview, and no preview at all (`"not applicable"`) blocks
the endpoint by rule.

## 3. GitHub rules — checks on an up-to-date base, non-bypass credentials

The recipe requires, on `main`:

```text
required_status_checks.contexts = ["Code Quality & Build", "Security Scan"]
required_status_checks.strict   = true    # branch must be up to date with base
enforce_admins                  = true    # no credential may bypass
allow_auto_merge                = false   # merge never outlives approval expiry (F12)
squash merge                    = enabled (selected method)
```

Current observed state (probe `readings.repo_rules`): `protected: false` —
so this contract is **not yet satisfied** and the full endpoint is blocked
until the rules are enabled (named blocker `base-unprotected`). Enabling is
an owner action (decision record, unresolved items).

Credentials: the merge owner runs `gh pr merge --squash` under the assignee
profile's own `gh` login; with `enforce_admins` the token cannot bypass
required checks. Merge read-back (`mergeCommit.sha`) is authoritative —
never inferred from a sent request (F12).

## 4. Failure → blocked mapping

| Missing/failed | Effect |
|---|---|
| No meaningful preview (provider absent, URL unverifiable) | `preview` gate fails → full endpoint blocked |
| Smoke check fails / stale (>10 min at merge) | merge denied; approval invalidated (F11/F12) |
| Required checks absent or branch protection off | `base-unprotected` blocker; nothing dispatches toward merge |
| Auto-merge or competing merge automation enabled | `conflicting-task-owner` blocker |
| Provider credentials missing on controller | readiness `model-unavailable`/credential gaps; no dispatch |

## 5. Gate evidence (GATE-S01/S02/S07 pointers)

| Gate | Evidence |
|---|---|
| GATE-S01 supported recipe | `docs/spike/evidence/readiness-2026-10-05.json` (live run, two named blockers — probe denies dispatch correctly) |
| GATE-S02 boundary map | `docs/spike/hermes-boundary-map.md` |
| GATE-S07 endpoint decision record | `docs/decisions/tested-recipe-selection.md` (Q1/Q2 preserved; Q3/Q5/Q6/Q7/Q10 selected) |
