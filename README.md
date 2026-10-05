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
presence. The factory runtime is not implemented yet.

## Documents

- [Product idea and original design discussion](idea.md)
- [Validation and competitive assessment](validate.md)
- [Product requirements and acceptance criteria](prd.md)
- [Development task plan](tasks.md)
- [Spike evidence and boundary map](docs/spike/)
- [Decision records](docs/decisions/)
- [Canonical manifest example](.factory-kit.yml)
- [Reference diagram](assets/warp-ai-factory-reference.jpg)

The PRD records the latest scope decisions. The idea and validation retain earlier proposals and recommendations for context.

## Next step

Run the bounded integration spike described in [PRD release planning](prd.md#8-release-planning). Prove supported Hermes lifecycle integration, durable recovery, preview evidence and guarded human-approved merge before expanding implementation.

## Origin

These documents and the reference image were copied from the [factory-kit idea folder](https://github.com/luongnv89/ideas/tree/main/ideas/2026_10_04_factory_kit) at commit `3db4b26ece720b6b5f37e7456a6d27ab59ae1522`. The original copy remains in the ideas repository. Related-idea links require access to that private repository.
