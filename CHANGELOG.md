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
- `pin --write`, which applies the pinning insertions to the ledger files in
  place using literal line edits (preserving comments and line endings across
  multiple files) instead of printing a diff.
- Hand-written documentation: `docs/concepts.md` and a README quickstart, plus
  an end-to-end acceptance test that backfills a two-year ledger, re-checks it
  and confirms the invariant holds every month.
- An OpenCode skill (`.opencode/skills/beancount-envelope-budget`) outlining the
  safe CLI workflow for setup, topup, backfill and pin, with `references/workflows.md`.
- `report --json`, machine-readable output for the report.
- `AGENTS.md` (with a `CLAUDE.md` pointer) and `docs/style-guide.md`; the
  original design spec moved to `docs/original_design.md`.
- `unplan`, which clears an envelope's plan from its date; the envelope keeps its
  balance and stays in reports, but `topup` stops funding it.
- `monthly: "avgN"`, generalising the average-of-completed-months notation from
  `"avg3"` to any window, e.g. `"avg6"`.
- `docs/concepts.md` gains a "Transfers and clearing accounts" section: cash-to-
  cash transfers are neutral, a `cash-account` clearing account keeps in-flight
  transfers neutral, and off-budget transfers must avoid cash accounts so they
  stay out of the budget.
- The engine honours Beancount's inferred tolerance (including
  `inferred_tolerance_default`): a transaction whose postings do not sum to zero
  is treated as balanced when the residual is within that tolerance, and the
  residual is folded into the pool so the month-end invariant stays exact. A
  residual beyond the tolerance is still reported, by Beancount itself and by
  `E007`. This stops imported trades rounded by a fraction of a cent from raising
  spurious invariant failures.

### Changed

- `open` and `plan` now resolve to the same plan record: `plan` takes the same
  `monthly`, `ahead`, `priority`, `target` and `by` metadata as `open` and
  changes only the fields it names, so anything settable on `open` is settable
  on `plan`, dated. `report --json` carries each envelope's effective `plan`.

### Removed

- The positional monthly amount on `plan` (`plan "<name>" <amount>`), replaced by
  `monthly:` metadata. `monthly` also moves onto `plan`'s allowed metadata, and
  `target`/`by` now apply to `plan` as well as `open`.

### Fixed

- A security bought with cash no longer raises a spurious `E006` and disappears
  from the budget. Postings are valued in the operating currency through their
  cost or price, so a commodity posting whose cost or price is in the operating
  currency is not treated as foreign and counts at that value. Buys and sells
  flow through the pool and envelopes, and the invariant holds. Genuinely
  foreign currencies still raise `E006`. See
  [`docs/concepts.md`](docs/concepts.md#investments-and-securities).
- Postings are valued by their Beancount weight even when their units are already
  in the operating currency, so an operating-currency posting that carries a cost
  or price no longer drifts pool plus envelopes away from cash. A posting whose
  weight is in another currency is now reported as `E006` rather than counted at
  face value.
