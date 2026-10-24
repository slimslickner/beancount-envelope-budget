# beancount-envelope-budget

Ledger-driven envelope budgeting for [Beancount](https://beancount.github.io/),
with a read-only [Fava](https://beancount.github.io/fava/) report.

Every dollar in your cash accounts is either assigned to an envelope or sits in
an unassigned pool. Envelope balances — positive or negative — carry from month
to month. The Beancount ledger stays the only source of truth: allocations are
dated `custom "envelope"` directives, and spending is derived from postings.

> **Status:** early scaffolding. The core computation and full documentation are
> under construction.

## Install

This project is managed with [uv](https://docs.astral.sh/uv/).

```sh
uv sync              # create the environment from uv.lock
uv run beancount-envelope-budget --help
```

Install the optional Fava report with:

```sh
uv sync --extra fava
```

## Use it

Add two lines to your ledger and load it as usual:

```beancount
plugin "beancount_envelope_budget.plugin"
2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext"
```

Validation errors then appear as ordinary Beancount errors (on Fava's errors
page). Add `"resolved"` after the plugin path to also write each posting's
resolved envelope to `envelope-resolved` metadata for BQL.

With Fava, the second line adds an **Envelopes** report: a read-only table of
opening / allocated / activity / closing for one to three months, with the pool
and cash rows, hover text for envelope targets and contributing transactions,
and a warnings panel.

## Commands

```sh
beancount-envelope-budget check  LEDGER
beancount-envelope-budget report LEDGER [--month YYYY-MM]
beancount-envelope-budget suggest LEDGER
beancount-envelope-budget fund LEDGER --from YYYY-MM --to YYYY-MM [--mode actuals|overspend|copy] [--envelopes REGEX] [--force]
beancount-envelope-budget fund LEDGER --mode topup [--date YYYY-MM-DD] [--reserve AMOUNT] [--strict]
beancount-envelope-budget pin LEDGER --account REGEX [--through YYYY-MM-DD]
beancount-envelope-budget schema [--json]
beancount-envelope-budget docs [--check]
beancount-envelope-budget completion SHELL   # bash or zsh
```

`fund` prints `allocate` directives to stdout (redirect them into a file you
include); it never edits your ledger. The `actuals`, `overspend` and `copy`
modes backfill a month range and skip months that already have an allocation;
`topup` funds envelopes up to their level in priority order from the pool.

`pin` prints a diff (it never edits your ledger) that freezes the resolved
envelope onto matching postings as metadata, so you can change an account's
envelope afterwards without reclassifying history.

`check` exits non-zero on any error. `report` prints opening, allocated,
activity and closing for every envelope plus the pool and cash rows. `suggest`
prints the monthly amount each goal envelope needs. See `docs/cli.md` for the
full reference.

## Development

A change is complete when the gate passes:

```sh
uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest && uv run beancount-envelope-budget docs --check
```

`pytest` runs the doctests, enforces a coverage floor on the core, and the
fixture ledgers double as tests.

See `docs/` for the concepts and the generated CLI, schema and error reference.
