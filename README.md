# beancount-envelope-budget

Ledger-driven envelope budgeting for [Beancount](https://beancount.github.io/),
with a read-only [Fava](https://beancount.github.io/fava/) report.

Every dollar in your cash accounts is either assigned to an envelope or sits in
an unassigned pool. Envelope balances — positive or negative — carry from month
to month. The Beancount ledger stays the only source of truth: allocations are
dated `custom "envelope"` directives, and spending is derived from postings.

> **Status:** the first release is feature-complete — core engine, Beancount
> plugin, CLI and Fava report all work. See `docs/concepts.md` for the model.

## Install

This project is managed with [uv](https://docs.astral.sh/uv/).

```sh
uv sync              # create the environment from uv.lock
uv run beancount-envelope-budget --help
```

The Fava report is an optional extra:

```sh
uv sync --extra fava
```

## Quickstart

Create `main.bean`:

```beancount
option "operating_currency" "USD"

2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Expenses:Groceries USD
  envelope: "Groceries"
2020-01-01 open Income:Salary USD
2020-01-01 open Equity:Opening-Balances USD

2023-12-31 * "Opening balance"
  Assets:Checking            1000.00 USD
  Equity:Opening-Balances   -1000.00 USD

2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Groceries"
2024-01-01 custom "envelope" "allocate" "Groceries" 500.00 USD

2024-01-05 * "Salary"
  Assets:Checking            3000.00 USD
  Income:Salary             -3000.00 USD

2024-01-06 * "Groceries"
  Expenses:Groceries          120.00 USD
  Assets:Checking            -120.00 USD
```

Then check it and print the report:

```sh
uv run beancount-envelope-budget check  main.bean
uv run beancount-envelope-budget report main.bean
```

`check` exits zero (no errors), and the report shows Groceries at 380, the pool
at 3500 and cash at 3880 — `3500 + 380 = 3880`, the invariant holding.

### Loading it into your own ledger

Add two lines and load your ledger as usual:

```beancount
plugin "beancount_envelope_budget.plugin"
2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext"
```

Validation errors then appear as ordinary Beancount errors (on Fava's errors
page). Add `"resolved"` after the plugin path to also write each posting's
resolved envelope to `envelope-resolved` metadata for BQL.

### Backfilling history

If you already have years of transactions, generate the allocations to match
what you actually spent, then include the output:

```sh
uv run beancount-envelope-budget fund main.bean --from 2024-01 --to 2025-12 > envelope_backfill.bean
```

Add `include "envelope_backfill.bean"` to your ledger and re-run `check`.

### Planning envelopes

An envelope's **plan** is its `monthly`, `priority`, `target` and `by` metadata.
Set it on `open`, change it from a date with `plan`, or clear it with `unplan`:

```beancount
2024-01-01 custom "envelope" "open" "Mortgage"
  monthly: 2500.00
  priority: 1

2028-01-01 custom "envelope" "plan" "Mortgage"
  monthly: 2600.00                              ;; only monthly changes

2029-01-01 custom "envelope" "unplan" "Mortgage"  ;; stop funding, keep balance
```

`plan` takes the same metadata as `open` and changes only the fields it names, so
`open` and `plan` resolve to the same plan. `monthly` may be a number or
`"avgN"` — the average of the last N completed months of spending — so `"avg3"`
tracks spending without a fixed figure. A plan funds an envelope one of two
ways: `monthly` holds about a month's spending, or a `target` with a `by` date
holds a goal. The two are mutually exclusive and a `target` needs a `by`; a plan
that names only one half of a pair, or both `monthly` and `target`, is reported
as `E010` rather than silently ignored. `unplan` stops `fund --mode topup`
funding the envelope while it keeps its balance.

A goal envelope's **level** is the balance it should hold to stay on schedule —
its `target`, grown linearly from the month the target took effect to its `by`
month, counting both ends. `fund --mode topup` funds up to that level and reports
`; OVERFUNDED <name>: ... above plan` when a reduced plan leaves the envelope
above it; `suggest` prints the monthly pace to reach the goal on time.

## Commands

```sh
beancount-envelope-budget check  LEDGER
beancount-envelope-budget report LEDGER [--month YYYY-MM] [--months N] [--json]
beancount-envelope-budget suggest LEDGER
beancount-envelope-budget fund LEDGER --from YYYY-MM --to YYYY-MM [--mode actuals|overspend|copy] [--envelopes REGEX] [--force]
beancount-envelope-budget fund LEDGER --mode topup [--date YYYY-MM-DD] [--reserve AMOUNT] [--strict]
beancount-envelope-budget pin LEDGER --account REGEX [--through YYYY-MM-DD] [--write]
beancount-envelope-budget schema [--json]
beancount-envelope-budget docs [--check]
beancount-envelope-budget completion SHELL   # bash or zsh
```

- `check` exits non-zero on any error.
- `report` prints one table spanning one to three months: a row per envelope
  with its opening once and then allocated, activity and closing per month, plus
  the pool and cash rows. Parent headings and envelopes are ordered
  alphabetically; a zero-padded number prefix (`01`, `02`, `10`) puts a name in a
  chosen place.
- `fund` prints `allocate` directives to stdout (redirect them into a file you
  include); it never edits your ledger. The `actuals`, `overspend` and `copy`
  modes backfill a month range and skip months that already have an allocation;
  `topup` funds envelopes up to their level in priority order from the pool, and
  reports any envelope already above its level.
- `pin` freezes the resolved envelope onto matching postings as metadata, so you
  can change an account's envelope afterwards without reclassifying history. It
  prints a diff by default; `--write` edits the files in place.

See `docs/cli.md` for the full reference and `beancount-envelope-budget --help`.

## Documentation

- [`docs/concepts.md`](docs/concepts.md) — the model, syntax, resolution rules
  and computation, written for users.
- [`docs/cli.md`](docs/cli.md) — generated CLI reference.
- [`docs/schema.md`](docs/schema.md) — generated directive, metadata and
  reserved-value reference.
- [`docs/errors.md`](docs/errors.md) — generated error and warning reference.
- [`docs/style-guide.md`](docs/style-guide.md) — conventions for code and docs.
- [`docs/original_design.md`](docs/original_design.md) — the original design
  spec (historical).
- [`AGENTS.md`](AGENTS.md) — repository guide for AI agents.
- [`examples/household.bean`](examples/household.bean) — a complete example
  ledger that also runs as an integration test.

## Development

A change is complete when the gate passes:

```sh
uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest && uv run beancount-envelope-budget docs --check
```

`pytest` runs the doctests, enforces a coverage floor on the core, and the
fixture ledgers double as tests.

### Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/).
Use a type and an optional scope, a short imperative subject, and reference the
issue with `(#N)`:

```
fix(topup): fund goal envelopes to their on-track level (#8)
docs: document the plan level and overfunded note (#8)
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`.
