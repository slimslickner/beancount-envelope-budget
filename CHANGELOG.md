# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffold: a uv-managed package, the ruff/ty/pytest gate, CI and
  pre-commit hooks.
- Schema registry for every directive verb, metadata key and reserved value,
  and a diagnostic registry for every error and warning code.
- `schema` and `docs` CLI commands, with generated `docs/cli.md`,
  `docs/schema.md` and `docs/errors.md`, and a `docs --check` gate.
- The pure `core.compute` engine: resolution rules, monthly carryover, the
  pool, the month-end invariant and the daily pool warning.
- Fixture ledgers and core tests for every computation case in the spec.
- The Beancount plugin: it returns the entries unchanged and appends the
  validation diagnostics as ordinary Beancount errors, and with the `"resolved"`
  config writes each posting's resolved envelope under `envelope-resolved`.
- Read-only CLI commands: `check`, `report [--month]`, `suggest` and
  `completion` (bash/zsh), on top of the existing `schema` and `docs`.
- `fund`, with the `actuals`, `overspend` and `copy` backfill modes and the
  priority-ordered `topup` mode, plus ledger-derived `--envelopes` completion
  cached on the ledger's modification time.
- `pin`, which prints a unified diff that freezes the resolved envelope onto
  matching postings as metadata (within a date range), skipping postings that
  already carry one; account-name completion for `--account` is included. The
  `--write` mode is deferred.
- The Fava extension: a read-only **Envelopes** report over one to three months,
  built from a shared `report.build_view` so it matches the CLI, with target and
  activity hover text and a warnings panel. `report --months N` gives the CLI
  the same slice.
