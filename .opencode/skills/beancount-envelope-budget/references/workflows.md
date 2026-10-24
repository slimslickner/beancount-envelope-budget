# Workflows

Longer worked examples for the `beancount-envelope-budget` CLI. Prefix commands
with `uv run` when working inside this repository.

## First-time setup

Mark each cash account, give every budgeted account an envelope, then start
tracking.

```beancount
option "operating_currency" "USD"

2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Liabilities:CreditCard:Visa USD
  cash-account: TRUE
2020-01-01 open Expenses:Groceries USD
  envelope: "Groceries"
2020-01-01 open Expenses:Taxes:Federal USD
  envelope: "pool"
2020-01-01 open Income:Salary USD
2020-01-01 open Equity:Opening-Balances USD

2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Groceries"
```

```sh
beancount-envelope-budget check main.bean
```

`start` begins tracking; the pool opens at the cash balance just before that
date. Everything before it is ignored.

## Business as usual

After a paycheck, top the envelopes back up to their planned levels.

```sh
beancount-envelope-budget fund main.bean --mode topup --date 2027-03-01 --reserve 500
```

This prints `allocate` directives, funding envelopes in priority order up to
`monthly × (1 + ahead)` and holding `--reserve` back in the pool. Anything that
could not be funded prints as a comment:

```beancount
2027-03-01 custom "envelope" "allocate" "Mortgage" 2500.00 USD
2027-03-01 custom "envelope" "allocate" "Groceries" 500.00 USD
; UNFUNDED Groceries: needs 120.00 USD
```

Append the output to a file you `include`, then re-check. Rerunning after another
paycheck prints only what is still missing; once nothing is missing it prints
nothing. With `--strict` it exits non-zero when anything is unfunded.

## Backfill history

If you already have transactions, generate allocations that match what you
actually spent, so each month closes flat and the pool absorbs the surplus.

```sh
beancount-envelope-budget fund main.bean --from 2024-01 --to 2025-12 --mode actuals > envelope_backfill.bean
```

Add `include "envelope_backfill.bean"` to the ledger and check. The other modes:

```sh
# only top negative envelopes back to zero
beancount-envelope-budget fund main.bean --from 2024-01 --to 2025-12 --mode overspend

# roll one month forward by repeating last month's allocation
beancount-envelope-budget fund main.bean --from 2027-04 --to 2027-04 --mode copy
```

All three skip envelope-months that already have an allocation; add `--force` to
regenerate one, or `--envelopes '^Car$'` to restrict the run.

## Report and plan

```sh
# the latest month, as a table
beancount-envelope-budget report main.bean

# three months forward, machine readable
beancount-envelope-budget report main.bean --month 2027-03 --months 3 --json

# what goal envelopes need each month
beancount-envelope-budget suggest main.bean
```

The report's `Pool` row is unassigned money and `Cash` is what the accounts
actually hold; they satisfy `Cash = Pool + sum(envelopes)`.

## Remap an account

To move `Expenses:Groceries` from the `Groceries` envelope to a new `Food`
envelope **without** reclassifying history, first freeze the current envelope
onto the existing postings.

```sh
# 1. review the change
beancount-envelope-budget pin main.bean --account '^Expenses:Groceries$' --through 2025-12-31

# 2. write it once you are happy
beancount-envelope-budget pin main.bean --account '^Expenses:Groceries$' --through 2025-12-31 --write

# 3. now change the account's metadata to the new envelope
```

`pin` adds `envelope:` metadata to each matching posting (skipping any that
already have one), using literal line edits that preserve comments and line
endings. After step 2, changing the account's `open` metadata only affects
postings after `--through`.
