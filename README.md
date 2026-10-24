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

## Development

A change is complete when the gate passes:

```sh
uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest && uv run beancount-envelope-budget docs --check
```

`pytest` runs the doctests, enforces a coverage floor on the core, and the
fixture ledgers double as tests.

See `docs/` for the concepts and the generated CLI, schema and error reference.
