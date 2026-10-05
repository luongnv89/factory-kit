# factory-kit

A Hermes-native, Telegram-first engineering workflow kit for GitHub projects, reusing IDD and existing skills.

## Project status

The Sprint-1 spike (issues #2–#5) is complete: the tested recipe, Hermes
boundary map, endpoint walkthrough and fault evidence live under
`tools/probes/`, `tests/fixtures/` and `docs/spike/`, with decision records
in `docs/decisions/`. Sprint 2 has started: the validated configuration and
registration contract (issue #6 / Task 2.1) now ships as the
`factory_kit.config` package — `.factory-kit.yml` schema validation,
`.gitissue.yml` ownership precedence, and durable registration records
with generation fencing. Reviewed additive setup and substantive
readiness (issue #7 / Task 2.2) now ship as the `factory_kit.setup`
package — read-only inspection producing an operator-accepted plan,
idempotent apply that preserves developer work byte-for-byte, and a
readiness gate that names blockers rather than trusting executable
presence. Reviewed ownership-aware removal (issue #19 / Task 3.5, F08)
ships alongside it in `factory_kit.setup.remove`: `python3 -m
factory_kit.setup uninstall` emits an operator-reviewable removal plan
from the recorded ownership ledger, and `python3 -m factory_kit.setup
remove --plan <file> --history retain|export|delete --accepted-by
<operator>` applies it — intake stops first, the active-generation
fence commits before termination, user edits and shared infrastructure
survive, provider outages leave a visible cleanup-pending backlog, and
the registration ends as an identity tombstone. Versioned upgrade,
repair and rollback (issue #26 / Task 4.2, F10) extend the same
contract: `python3 -m factory_kit.setup upgrade` emits a reviewable
compatibility-checked plan; `migrate` applies it across durable stage
boundaries — intake fence first, then a checkpoint of every owned byte
and the registration digests, then owned-file writes, a new authorized
generation, re-validation and dependency/skill provenance pinning —
leaving dispatch denied until substantive readiness re-passes;
`repair` restores the validated checkpoint or parks on a named
conflict after any interruption; `rollback` restores the previous
validated registration/configuration byte-for-byte. The packaged support
recipe (issue #23 / Task 3.9) lives in `docs/recipes/` — tested
installation/removal commands, operations and recovery vocabulary, the
backup/restore rehearsal and the tested-only support matrix — exercised
end-to-end by `tests/recipes/` and `tests/recovery/test_backup_restore.py`.
The internal v1.0 evidence gate (issue #24 / Task 3.10) is assembled in
`docs/evidence/v1.0-gate.md` — every Must-feature criterion and
GATE-M01–M08 traced to reproducible evidence — and the owner acceptance
record in `docs/decisions/v1.0-acceptance.md`. The audit reports
**NO-GO pending owner decision**: GATE-M02 stays closed on the
outstanding live endpoint legs (`base-unprotected` — verified live —
`telegram-adapter-live`, `vercel-linkage`, `kanban-live-write`) and Q9
acceptance is still pending; no gate was waived for schedule. The
external-pilot consent and workload-trust admission gate (issue #28 /
Task 4.4) ships in `factory_kit.privacy`: the durable obligations
record that must exist before any participant consent, per-participant
consent + supported workload/trust classification with durable
revocation that blocks collection and aggregate export, hostile
boundary probes (`python3 -m factory_kit.privacy.boundary`) that deny
external code without demonstrated filesystem/network/privileged-write
and credential-scope isolation — a worktree, prompt rule or branch
protection is never evidence — and consent-aware aggregate exports in
`diagnostics/report.py`, per `docs/pilot/consent-admission.md`.

## Documents

- [Product idea and original design discussion](idea.md)
- [Validation and competitive assessment](validate.md)
- [Product requirements and acceptance criteria](prd.md)
- [Development task plan](tasks.md)
- [Spike evidence and boundary map](docs/spike/)
- [Fault-matrix evidence](docs/evidence/fault-matrix.md)
- [Internal v1.0 evidence gate](docs/evidence/v1.0-gate.md) — requirement-to-evidence audit, 7/8 gates open (M02 closed)
- [v1.0 acceptance record](docs/decisions/v1.0-acceptance.md) — no-go pending owner decision
- [§5.1 measured local targets](docs/measurements/v1-targets.md)
- [Tested support recipe](docs/recipes/) — installation, operations, backup/restore, upgrade/repair/rollback, support matrix
- [Pilot consent and workload-trust admission](docs/pilot/consent-admission.md) — obligations record, consent gate, boundary probes, named blockers
- [Decision records](docs/decisions/)
- [Canonical manifest example](.factory-kit.yml)
- [Reference diagram](assets/warp-ai-factory-reference.jpg)

The PRD records the latest scope decisions. The idea and validation retain earlier proposals and recommendations for context.

## Next step

Run the bounded integration spike described in [PRD release planning](prd.md#8-release-planning). Prove supported Hermes lifecycle integration, durable recovery, preview evidence and guarded human-approved merge before expanding implementation.

## Origin

These documents and the reference image were copied from the [factory-kit idea folder](https://github.com/luongnv89/ideas/tree/main/ideas/2026_10_04_factory_kit) at commit `3db4b26ece720b6b5f37e7456a6d27ab59ae1522`. The original copy remains in the ideas repository. Related-idea links require access to that private repository.
