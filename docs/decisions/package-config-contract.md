# Decision record: supported package and configuration contract (issue #6, Task 2.1)

- **Status:** Accepted — 2026-10-05 — finalizes the provisional Q6 choices
  from Sprint-1 spike evidence before any public interface lands
- **Scope:** PRD §6.3 CFG01–CFG09 / CFG-C01 / CFG-C02; §6.4 REC01;
  `tasks.md` Task 2.1 A1–A7; Q6 ownership (TAD, after spike, before public
  interfaces)
- **Evidence:** `docs/spike/hermes-boundary-map.md`,
  `docs/spike/verification-contract.md`,
  `docs/decisions/tested-recipe-selection.md` (Q3/Q5/Q6/Q10),
  `tools/probes/endpoint_walkthrough.py` (`EndpointStore`),
  `tools/probes/spike_faults.py` (`FaultStore`),
  `docs/spike/fault-ledger.md` (all eight gates pass),
  `docs/decisions/day2-go-no-go.md` (convergence contract named as
  Task-2.x scope)

## Contract summary

| Axis | Finalized choice | Spike evidence |
|---|---|---|
| Language | Python 3, standard library only — helpers follow the `gi-*.py` convention | Q6 selection; all four probes run stdlib-only |
| Package | Native general plugin `factory-kit` under `~/.hermes/plugins/factory-kit/`; `register(ctx)` wires tools/CLI/hooks when Task 2.x public interfaces land; admission gate `hermes plugins validate` | Q6 package row; boundary-map plugin interface row (`supported`) |
| Storage | Hermes owns task/attempt/fence state in `kanban.db` (CLI/tools only). Kit owns registration, delivery-dedup, control and approval rows as per-profile data referencing Hermes task IDs — incapable of dispatching alone. This task ships registration as JSON rows (`factory_kit.config.registration`), the same rows the spike proved in `EndpointStore`/`FaultStore` | Q6 storage row; boundary-map §6.4 record table; `FaultStore.EXTRA_SCHEMA` |
| Manifest schema | `factory_kit.config.schema` — restricted-YAML subset (`factory_kit.config.yamlmini`), version `factory_kit: 1`, deny-by-default and unknown-field-rejecting; effective config digested per attempt (CFG-C02) | boundary map marked the schema "provisional — owned by the TAD (Task 2.x)"; this record finalizes it |
| Commands | None advertised. The provisional `hermes plugins install factory-kit` / `factory-setup` syntax stays **unsupported until implemented and tested** (Q6 decision-record note); typed factory Telegram actions remain conditional on the documented extension point | boundary-map no-go list; Telegram typed-actions gap |
| Filename | `.factory-kit.yml` at the **managed project's** repo root — repo-reviewed code. The kit repo ships the canonical annotated example at its own root as the executable schema fixture | §6.3 CFG-C01; A6 |

## Configuration ownership (CFG-C01)

Factory settings live in `.factory-kit.yml`; IDD settings stay in
`.gitissue.yml`. `factory_kit.config.precedence` is the executable
boundary: a manifest key inside an IDD-owned namespace (`platform`,
`issue`, `resolve`, `review`, `projects`, `security`, `agents`, `triage`,
`autopilot`) fails as IDD-owned, and a `.gitissue.yml` key inside a
factory-owned group fails as factory-owned. The factory never writes
`.gitissue.yml`; setup-time additive integration is Task 2.2's reviewed
plan, not a silent overwrite.

## Validated contract groups (CFG01–CFG09)

`identity` (immutable `repo_id` authority; display `owner`/`name` never
re-keys), `registration.owner` (one local owner), `authorization`
(explicit opt-in + GitHub actors/roles + numeric Telegram allowlists,
deny by default), `runtime` (the one supported runtime `hermes-kanban`;
available model per `implementation`/`review` role; tool/capability
allow/deny), `skills.approved` (IDs pinned to immutable revisions;
`auto_discover` stays false), `verification` (nonempty acceptance
commands + provider/contexts/conclusions — rejected even with no
protected checks), `limits` (Q9-proposed defaults 1/2/60/24, bounded),
`evidence` (freshness, retention, export/redaction, tombstones until
explicit removal), `secrets` (approved `env|hermes-secrets|bw|op|vault:`
references only; literals rejected), `endpoint` (Vercel preview only,
squash merge, 60-minute approval expiry, 10-minute maximum smoke age;
`production_deploy`, `package_publish`, `autonomous_merge` disabled with
no enabling key).

## Registration and generation fencing (REC01, CFG-C02)

`factory_kit.config.registration` persists per repository: identity +
authority key, configuration digest, owner, supported version set,
authorization-policy digest and readiness outcome. A policy-surface
change cannot silently expand an active generation — it parks affected
active work, or opens a new generation only under an explicit
`authorized_by`. Generation rows are immutable: a later authorization
never broadens a generation already recorded. Every attempt carries
`generation` + `config_digest` + `policy_digest` from
`attempt_context` — the intake-path contract `FaultStore` proved
(delivery dedup + one active attempt) is honored by keeping these rows
kit-owned and never dispatch-capable.

## What this record does not do

- It does not land `register(ctx)`, tools, CLI commands or webhook
  routes — Task 2.x public interfaces consume this contract.
- It does not resolve the day-2 replan, `base-unprotected`, or the
  carried primitive blockers (`telegram-adapter-live`,
  `kanban-live-write`, `vercel-linkage`) — each still resolves on its
  own evidence leg per `day2-go-no-go.md`.
- It does not claim measured values: every numerical default is a
  Q9-proposed bound, validated as configuration.
- It does not invent a scheduler, transition engine or credential
  broker — no-go per the boundary map.
