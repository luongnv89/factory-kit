# Decision record: provenance, trust/license review and Q8 distribution (issue #23 / Task 3.9, A5)

- **Status:** Internal recipe accepted — **public distribution unresolved, pending Luong's Q8 decision** (PRD §9.1; `tasks.md` Q-table)
- **Owner:** Luong — Q8 is an explicit release decision, not silently defaulted
- **Scope:** dependency/skill provenance, trust/license notice review, and the Q8 license/channel/support/notice decisions
- **Rule (Task 3.9 A5):** unresolved public-release decisions **block public publication** — they do **not** block this explicitly internal support recipe

## Dependency and component provenance

| Component | Origin | License/notice | Trust review |
|---|---|---|---|
| `factory_kit.*` (setup, durable, intake, execution, recovery, publication, approval, notification, preview, verification, config) | This repository, first-party | **No root LICENSE file today** — all rights reserved by default; a Q8 blocker | Authored in-repo; full unittest coverage under `tests/` |
| `skills/factory-setup` SKILL.md | This repository | MIT (front-matter `license: MIT`) | Versioned 0.1.0, authored Luong NGUYEN |
| IDD skill pins `issue-resolver: 0.19.0`, `issue-pr-review: 0.19.0` | Luong's IDD skill bundle (`~/.agents/skills/`) | Internal skill bundle | Pinned revision enforced by readiness (`skill-version-mismatch`); never auto-discovered (`skills.auto_discover: false`) |
| Hermes (`hermes-kanban`) | External runtime, ≥ 0.21.5 tested | Vendor terms | Probed live by readiness; boundary map in `docs/spike/hermes-boundary-map.md` |
| Python 3.14.7 stdlib | PSF | PSF license | No third-party Python dependencies — the kit is stdlib-only by construction |
| git 2.55.0, GitHub CLI 2.100.0 | Host toolchain | GPL-2.0 / MIT respectively | Executables probed, versions recorded |
| Vercel (preview) | External service | Vercel terms | Selected provider Q10; unlisted previews, TTL-bound |
| Telegram Bot API | External service | Telegram terms | Gateway-authorized; numeric allowlists; notifications carry summaries/links only |

## Trust and license notice review

- **No vendored third-party code** — the kit tree is first-party;
  `skills/factory-setup` carries an MIT notice in front-matter; the
  repository has **no root `LICENSE`** — so nothing here is licensed for
  external reuse today. That absence is itself a Q8 blocker, not an
  oversight to paper over.
- **Notices to preserve on any distribution:** the selected license file
  (once Q8 lands), `skills/factory-setup` MIT line, and this provenance
  table.
- **Trust posture:** GitHub credentials are the scoped assignee `gh`
  login; secrets are references (`env:` / `hermes-secrets:`), never
  literals in plans, reports or committed files (canary-scanned).
- **Honest gaps (recorded, not papered):** external CLIs unpaved; dspark
  optional-only; the full endpoint live run is Task 3.10 scope.

## Q8 — distribution decisions (owner: Luong)

| Question | Decision | Effect |
|---|---|---|
| License for public release | **Unresolved** | No root `LICENSE` exists — external reuse is unlicensed until Luong selects one; the setup skill's in-file MIT notice does not extend to the kit |
| Channel (PyPI / hermes plugin / source-only) | **Unresolved — source-only internal use today** | No install command exists or is advertised; `docs/recipes/installation.md` documents the tested checkout-based commands only |
| Support commitment | **Unresolved** | Internal single-recipe support only; no external SLA claimed |
| Dependency notice process | **Unresolved** | This record + the support matrix are the internal notice; a NOTICE/THIRD-PARTY process must be selected before public release |

**Blocking relationship:** these four rows unresolved ⇒ **public
publication is blocked**. The recipe, tests and decision record ship
internally under Task 3.9 A5's explicit carve-out; GATE-M08 (Task 3.10)
exposes these as separately unresolved rather than smuggling a default.
