# Changelog

All notable changes to factory-kit are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0b1] - 2026-10-06

First beta-testing version, covering the user-visible work since 0.1.0.

### Added

- Live driver and real Hermes/GitHub adapters (#66, #67), including bounded
  `tick`/`watch` operation and guarded operator decisions.
- A declared no-preview contract and a `Unit tests` CI workflow (#75).
- Self-management support: factory-kit can use the same setup and workflow
  in its own repository (#76).
- A source-checkout beta guide and this changelog.

### Changed

- Kit version and readiness `version_set` now report `0.2.0b1`. Existing
  registrations recorded with `factory-kit/0.1.0` remain valid; readiness
  reports the running kit version without requiring registration migration.

### Not supported

- Package-registry publication, `pip install`, and `hermes plugins install`.
- Autonomous merge, production deployment, and package publication.
