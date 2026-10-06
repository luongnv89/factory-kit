# Per-repo state — layout, tracking and moving machines

Read this file when creating a repo's state folder, when the operator asks
to track state in git, or when moving a repo to another machine.

## Layout

All factory-kit state for a repository lives in `$REPO/.factory-kit/`.
The kit's CLIs still default to the operator profile
(`~/.hermes/factory-kit/`), so pass the paths explicitly on every command:

| File | Written by | Passed as |
|---|---|---|
| `setup-state.json` | setup (ledger, ownership, events) | `--state` |
| `registrations.json` | setup + run | `--registrations` (setup), via `--state-dir` (run) |
| `intake.db` | run (work, approvals, history) | `--intake-db` (setup), via `--state-dir` (run) |
| `pages/`, `workspaces/` | run (preview clone, worker clones) | via `--state-dir` (run) |
| `run.lock` | run (one driver per machine) | via `--state-dir` (run) |
| `OPERATOR.md` | operator runbook (optional) | — |

`factory-run --state-dir "$STATE"` reads the whole folder.

## Create the folder (first setup in a repo)

If `$STATE` is missing, create it before `plan` with a `.gitignore` that
ignores everything, itself included, so the working tree stays clean:

```bash
mkdir -p "$STATE" && printf '%s\n' \
  '# factory-kit per-repo state; untracked by default (see factory-setup references/state.md)' \
  '*' > "$STATE/.gitignore"
```

## Track state in git (operator opt-in)

Tracking carries the setup to another machine through git. Only the
operator decides it, per repository. Replace `$STATE/.gitignore` with:

```
# Never tracked: machine-local, volatile, or nested clones
run.lock
workspaces/
pages/
*.db-wal
*.db-shm
*.db-journal
```

Then commit `.factory-kit/.gitignore` plus the files the operator chose.
Before suggesting it, tell the operator:

- **Public repository:** `intake.db` and `registrations.json` hold actor
  logins, approval records, issue/PR history and event detail — the data
  the manifest's `evidence.export.redact` keeps out of exports. Check
  `gh repo view --json visibility`; on a public repo recommend tracking
  only `setup-state.json` and `registrations.json`, or nothing.
- **One driver at a time:** `run.lock` only excludes drivers on the same
  machine. Running `factory-run` on two machines over synced state can
  double-dispatch work and fork the history. Stop the driver, commit and
  push state, then start it on the other machine.
- **`intake.db` is binary:** it cannot be merged. Commit it only from the
  one machine that ran the driver last, and only while no driver runs.
- **Commit state before a sync.** Modified tracked state makes the Repo
  Sync's dirty-tree check stop; ask the operator to commit it first.

## Moving to a new machine

1. On the old machine: stop the driver, commit and push the tracked state.
2. On the new machine: clone the repo; tracked state arrives with it.
   Untracked state (the default) must be copied by hand — copy
   `intake.db` with `sqlite3 old.db ".backup new.db"`, never a plain copy
   of a database in use.
3. Run the health check (SKILL.md → Choose the branch) before any `factory-run`.

## Known effects of in-repo state

- `plan`, `upgrade` and `uninstall` checksum every file in the repo,
  `.factory-kit/` included. The kit's own writes there appear in
  `preserved.violations` — expected, not drift. Report them separately.
- Worker clones under `workspaces/` count toward the plan's 5000-file
  checksum cap. Plan when no work is in flight, or the walk may truncate
  (`preserved_truncated: true`) before reaching real repo files.
- `eslint .` and similar tools in the operator checkout can descend into
  `.factory-kit/workspaces/` while work is in flight; add it to that
  tool's ignore list if lint runs locally.
