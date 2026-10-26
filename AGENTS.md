# AGENTS.md

Guidance for AI agents working **on this repository** (for using the tool, see
the skill at `.opencode/skills/beancount-envelope-budget`).

`beancount-envelope-budget` adds envelope budgeting on top of an ordinary
Beancount ledger: every dollar in a cash account is either assigned to an
envelope or sits in an unassigned pool, and balances carry month to month. It
ships a Beancount plugin, a CLI, and a read-only Fava report, all built on one
pure core. The ledger is the only source of truth. A slimmed-down user overview
is in [`README.md`](README.md); the model is in
[`docs/concepts.md`](docs/concepts.md); the original design spec (historical) is
in [`docs/original_design.md`](docs/original_design.md).

## Structure

| Path | Role |
| --- | --- |
| `src/beancount_envelope_budget/core.py` | Pure engine: envelope plans (base plus dated overrides), resolution rules, monthly carryover, pool and cash, the invariant. |
| `src/beancount_envelope_budget/report.py` | Pure view model (`build_view`, `to_jsonable`) shared by the CLI and Fava. |
| `src/beancount_envelope_budget/schema.py` | The registries: directive verbs, metadata keys, reserved values, error codes. |
| `src/beancount_envelope_budget/plugin.py` | Beancount plugin: runs the core, returns entries plus diagnostics. |
| `src/beancount_envelope_budget/cli.py` | `beancount-envelope-budget`: check, report, suggest, fund, pin, schema, docs, completion. |
| `src/beancount_envelope_budget/fava_ext.py` + `templates/` | The read-only Fava report. Only module that imports Fava. |
| `tests/` | pytest; `tests/ledgers/` has one fixture per case. |
| `docs/` | `concepts.md`, `style-guide.md`, `original_design.md`; generated `cli.md`, `schema.md`, `errors.md`. |
| `examples/household.bean` | Complete example ledger; also an integration test. |
| `.opencode/skills/` | The CLI-usage skill. |

## The gate

A change is complete when this passes — it is the same command CI runs:

```sh
uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest && uv run beancount-envelope-budget docs --check
```

- **ruff** (format + lint, `select = ["ALL"]`, commented ignores) and **ty** must
  both be clean. Do not merge a change that fails either.
- `pytest` runs doctests and enforces a coverage floor on `core.py`.
- `docs --check` fails if a generated doc is stale; regenerate with
  `uv run beancount-envelope-budget docs`.

## Conventions

See [`docs/style-guide.md`](docs/style-guide.md) for the full guide. The short
version: money is `Decimal`; records are frozen slotted dataclasses; every public
symbol has a Google-style docstring with doctests where useful; `core.py` imports
only `beancount` (plus our `schema` and the standard library), and only
`fava_ext.py` imports Fava; only `pin --write` edits a
ledger file; the registries in `schema.py` are the source of truth for schema and
errors; update `CHANGELOG.md` for every user-visible change.

## Commits

Write [Conventional Commits](https://www.conventionalcommits.org/) and reference
the issue with `(#N)`:

```
fix(topup): fund goal envelopes to their on-track level (#8)
```

Use `feat`, `fix`, `docs`, `test`, `refactor` or `chore` as the type. Keep the
subject imperative and under about 72 characters.

## Gotchas

- Never rewrite a ledger by regenerating it; `pin` edits files by literal line
  insertion using each posting's `filename`/`lineno`.
- `docs/cli.md`, `docs/schema.md` and `docs/errors.md` are generated — edit the
  parser or the registry, then run `docs`.
- The `report`/`fava_ext` numbers must come from `report.build_view`, not be
  recomputed, so the CLI and the web report never disagree.
