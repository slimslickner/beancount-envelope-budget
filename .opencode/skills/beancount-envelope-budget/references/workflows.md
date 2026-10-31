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
their level — `monthly`, the capped allowance of a `monthly` + `up-to` plan, or
the on-track balance for a goal — and holding `--reserve` back in the pool.
Anything that could not be funded prints as a comment, and a goal already above
its level prints as overfunded:

```beancount
2027-03-01 custom "envelope" "allocate" "Mortgage" 2500.00 USD
2027-03-01 custom "envelope" "allocate" "Groceries" 500.00 USD
; UNFUNDED Groceries: needs 120.00 USD
; OVERFUNDED Emergency Fund: 500.00 USD above plan
```

Append the output to a file you `include`, then re-check. Rerunning after another
paycheck prints only what is still missing; once nothing is missing it prints
nothing. With `--strict` it exits non-zero when anything is unfunded.

`topup` sizes each envelope from its plan (`monthly`, `monthly` + `up-to`, or a
`target` + `by`), so with no plan directives it prints nothing — there is no
level to fund to. If you would rather not write any plans, top spent envelopes
back to zero instead by running `overspend` over a single month:

```sh
# bring every negative envelope back to zero for the current month
beancount-envelope-budget fund main.bean --from 2027-03 --to 2027-03 --mode overspend
```

`overspend` only covers what is already negative; it never pre-funds the next
month. Use `--envelopes REGEX` to restrict it, and `actuals` over a range if you
want each month to close flat.

To fund a whole month in one pass — meeting each plan *and* covering any
overspend — use the month actions. `fully-fund` funds every envelope to its plan
level or its overspend, whichever is greater, and `fund-available` does the same
in priority order while keeping the pool non-negative:

```sh
# meet every plan and cover every overspend for the current month
beancount-envelope-budget fund main.bean --mode fully-fund --date 2027-03-31

# the same, but stop before the pool would go negative
beancount-envelope-budget fund main.bean --mode fund-available --reserve 500
```

Both take `--date` (defaulting to today) and print one `allocate` per envelope
that needs funding; `fund-available` prints `; UNFUNDED` for anything the pool
could not cover, and `--strict` makes that a non-zero exit.

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

An envelope's plan can change over time without touching history:

```beancount
2024-01-01 custom "envelope" "open" "Mortgage"
  monthly: 2500.00
  priority: 1
2028-01-01 custom "envelope" "plan" "Mortgage"
  monthly: 2600.00
2029-01-01 custom "envelope" "unplan" "Mortgage"
```

`plan` takes the same metadata as `open` and changes only the fields it names;
`unplan` clears the plan from its date while the envelope keeps its balance and
stays in reports. Use `monthly: "avg3"` (or `"avg6"`, and so on) to plan from
the average of the last N completed months of spending.

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
