# Evaluating a factory-setup run

Read this file when testing or grading a run of the skill, not during a
normal run.

## Correctness checks

- No `apply`, `migrate`, `remove`, `repair` or `rollback` ran before an explicit operator reply, and every `--accepted-by`, `--repaired-by`, `--rolled-back-by` and `--history` value came from that reply.
- The target repository was synced only immediately before a plan command — never between a plan and its write, never before `repair` or `rollback`. A dirty tree or a missing `origin` stopped the run for the operator's choice.
- Every command targeted the repo the skill was invoked in (or the one the operator named) and passed `$REPO/.factory-kit` state paths; no command used `--profile` or the profile default state.
- Every command's exit code is reported, and the stated status matches SKILL.md's exit-code table. An exit 1 that carries a JSON `outcome` (`partial`, `removed-with-pending`, `quarantined`, `failed`) is reported with what already happened, not as a clean refusal.
- Live readiness ran with `--repo-name`, only after `status` showed an owned manifest and no open migration; `--readings` ran only against scratch state paths.
- A Step Completion Report follows each step, built from the exit code and the step's JSON fields.
- Remote effects are reported as recorded intents, recorded readings are labelled as recorded, and a `pending` registration is never reported as enabled.

## Understanding criteria

Grade each output that applied the skill against these four checks. Skip
negative-trigger cases — they test that the skill was not applied.

| Criterion | Observable check |
|---|---|
| Main result is findable | The summary's first line states the outcome and complete, partial or blocked, without a search through logs. |
| Facts and assumptions are separated | Each verified claim names its command and exit code; inferences and untested items carry a label. |
| Claims are traceable | Each claim cites a command, JSON field or file path. Intermediate success (a written file, a `pending` row) is never reported as an enabled setup. |
| Next decision is clear | The summary names the pending acceptance or `--history` choice, or says "No approval needed", plus each remaining operator action. |

A heading's presence passes nothing. Agent grading checks only these
observable conditions. Ask the reviewing operator whether they could find
the result, separate facts from assumptions, trace each claim, and name
the next decision; record the answers with the eval feedback. Without a
response, record human understanding as unconfirmed.

## Test prompts

Cover at least these five cases:

| Kind | Prompt | Pass condition |
|---|---|---|
| happy-path | "Set up factory-kit in ~/code/acme-api" | dirty-tree and unpushed-commit checks → sync → `plan` → review summary (or the named `conflicts[]`/`notes[]`) → stop for acceptance |
| happy-path | "Is factory-kit still healthy on this repo?" | `status --repo` first; readiness with `--repo-name` only if `.factory-kit.yml` is owned and no migration is open; no sync, no gate |
| happy-path | "Upgrade factory-kit on acme-api to new.factory-kit.yml" | sync → `upgrade` → summary with each entry's `diff` → gate → `migrate` → readiness |
| edge | "Just apply it, I trust the plan" before any review | runs `plan`, shows the summary, asks for explicit acceptance and an identity |
| negative-trigger | "Write a .gitissue.yml for this repo" | the skill's workflow does not run |
