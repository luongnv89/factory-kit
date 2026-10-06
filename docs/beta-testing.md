# Beta testing factory-kit 0.2.0b1

This is the first beta-testing version of factory-kit. It is intended for
careful evaluation from a source checkout, not package installation or
unattended production use. See the [changelog](../CHANGELOG.md) for the
changes since 0.1.0.

## Prerequisites and tested pins

Use only the versions and configuration in the
[tested support matrix](recipes/support-matrix.md). At a minimum, the tested
recipe uses Python 3.14.7 (standard library only), git 2.55.0, GitHub CLI
2.100.0 with the required scoped login, Hermes 0.21.5 or later, and the
`hermes-kanban` profile worker. The matrix lists the complete tested pins,
permissions and explicit unsupported combinations. Read the
[installation recipe](recipes/installation.md) before setting up a repository.

## Run from a source checkout

Clone the repository and work from its root; there is no package installation
step. Run the setup and driver modules with Python from the checkout:

```bash
cd /path/to/factory-kit
python3 -m factory_kit.setup --help
python3 -m factory_kit.run --help
```

The tested installation path and its tested prerequisites are documented in
[recipes/installation.md](recipes/installation.md). Do not substitute
`pip install factory-kit` or `hermes plugins install`; neither is supported.

## Set up a repository

Use a clean target repository and the reviewed plan/apply/readiness flow from
the [tested installation recipe](recipes/installation.md). The following
commands use its supported module invocation; review the plan before applying
it, and substitute real paths and the operator identity:

```bash
python3 -m factory_kit.setup plan --repo /path/to/project \
    --manifest /path/to/candidate.factory-kit.yml --out plan.json

python3 -m factory_kit.setup apply --repo /path/to/project \
    --plan plan.json --accepted-by <operator> \
    --state ~/.hermes/factory-kit/setup-state.json \
    --registrations ~/.hermes/factory-kit/registrations.json

python3 -m factory_kit.setup readiness --repo /path/to/project \
    --readings /path/to/recorded-readings.json \
    --state ~/.hermes/factory-kit/setup-state.json \
    --registrations ~/.hermes/factory-kit/registrations.json
```

Readiness with recorded readings is a fixture/replay, not a live readiness
claim. Follow the installation recipe for actual operator readings and
verify that it reports `verdict: ready` and `dispatch: allowed` before
running the driver. A refused plan or not-ready result is a stop, not a
reason to bypass a check.

## One watch pass

After live setup has enabled the registration and the required Hermes/GitHub
integrations are configured, run one bounded pass from the checkout:

```bash
python3 -m factory_kit.run watch --repo /path/to/project \
    --interval 1 --max-passes 1
```

This is the `factory-run watch` command invoked as a module from the source
checkout. It performs one driver pass and exits; it does not promise that work
will be found or completed. Inspect the reported outcome. Do not run a second
driver while another holds the repository's run lock. See
[recipes/operations.md](recipes/operations.md) for tested operational and
recovery guidance.

## Known limitations

- Source checkout is the only supported distribution path. There is no
  `pip install`, `hermes plugins install`, or package-registry publication.
- Support is limited to the tested pins and the `hermes-kanban` worker path
  in the support matrix. Unlisted combinations are unsupported.
- The lane is single-task by contract; concurrent multi-project execution,
  autonomous merge, production deployment and package publication are not
  supported.
- The no-preview contract does not provide a preview deployment.
- The repository has no selected LICENSE. Do not redistribute or reuse the
  code externally unless and until the owner publishes licensing terms.
- This beta is not a claim of external-pilot validation or production
  readiness; use the tested recipes and review every proposed operation.

## Report feedback

Report reproducible bugs and beta feedback as GitHub issues in the
[luongnv89/factory-kit repository](https://github.com/luongnv89/factory-kit/issues).
Include the version (`0.2.0b1`), relevant command, expected and observed
behavior, and redacted logs. Do not include credentials, tokens, or private
repository data.
