# Concepts

Envelope budgeting on top of an ordinary Beancount ledger. Every dollar in your
cash accounts is either assigned to an **envelope** or sits in an unassigned
**pool**. Balances — positive or negative — carry from month to month.

The ledger is the only source of truth. There is no second database: allocations
are dated `custom "envelope"` directives, and spending is derived from your
existing postings.

## The seven terms

| Term | Meaning |
| --- | --- |
| **Envelope** | A named bucket with a running balance, declared once with a directive. |
| **Cash account** | An `Assets` or `Liabilities` account whose balance counts as spendable money — checking, savings, credit cards. Marked `cash-account: TRUE` on its `open`. |
| **Pool** | Cash not assigned to any envelope ("to be budgeted"). |
| **Allocation** | A dated amount moved from the pool into an envelope. |
| **Activity** | The effect of postings on an envelope: spending lowers it, refunds and envelope-level income raise it. |
| **Carryover** | An envelope's closing balance, positive or negative, becomes next month's opening balance. |
| **Plan** | An envelope's funding parameters — `monthly`, `ahead`, `priority`, `target`, `by` — set on `open`, changed from a date with `plan`, and cleared with `unplan`. |

Months are calendar months, and everything is attributed by its date. Setting
`cash-account: FALSE` is the same as omitting the key entirely.

## Cash vs. pool

`Cash` is what your accounts actually hold. `Pool` is the part of that cash you
have not assigned to an envelope. They are always related by:

```
Cash = Pool + (the sum of all envelope balances)
```

Show both because the pool is the actionable number ("what is left to assign")
and cash is the ground truth ("what you actually have"). Because the pool and
envelopes are built from flows while cash is read straight from the ledger, the
comparison is a real cross-check: if they disagree, a posting was missed.

How each action moves the lines:

| Action | Pool | Envelope | Cash |
| --- | --- | --- | --- |
| Income arrives | ↑ | – | ↑ |
| Allocate money to an envelope | ↓ | ↑ | – |
| Spend from an envelope (from checking) | – | ↓ | ↓ |
| Spend on a credit card | – | ↓ | ↓ |
| Pay the credit card from checking | – | – | – (cash-to-cash) |
| Refund into an envelope | – | ↑ | ↑ |
| Move money between envelopes | – | ± | – |

Credit cards are cash accounts, so a card balance is negative and *lowers* cash
and the pool. Cash is therefore net spendable money including card debt.

## Transfers and clearing accounts

Moving money between two cash accounts is neutral: cash-to-cash transactions
change neither the pool nor any envelope. When the two legs land on different
days — a transfer in progress — route them through a **clearing account** and
mark it `cash-account: TRUE`:

```beancount
2020-01-01 open Assets:Transfers:Clearing USD
  cash-account: TRUE

2026-01-30 * "Move to savings"        ; the checking side clears immediately
  Assets:Checking                -500.00 USD
  Assets:Transfers:Clearing       500.00 USD
2026-02-01 * "Move to savings"        ; the savings side lands later
  Assets:Savings                  500.00 USD
  Assets:Transfers:Clearing      -500.00 USD
```

Both legs are cash, so the transfer stays neutral, and while it is in flight the
money still counts as cash. The clearing account nets to zero once both legs
post; a non-zero balance is a transfer that has not completed.

The budget decides whether a transaction counts by one test: it touches a cash
account (see [Resolution rules](#resolution-rules)). A clearing account therefore
cannot serve both on-budget and off-budget transfers — marking it cash brings
*every* transfer it carries into the budget. Keep off-budget transfers, such as
an IRA-to-IRA rollover between two accounts that are not cash, on an **unmarked**
clearing account (or post the two legs directly), so they touch no cash account
and the budget ignores them:

```beancount
2026-03-01 * "IRA rollover out"
  Assets:IRA:Old                -10000.00 USD
  Equity:Rollover:Clearing       10000.00 USD
2026-03-03 * "IRA rollover in"
  Assets:IRA:New                 10000.00 USD
  Equity:Rollover:Clearing      -10000.00 USD
```

## Ledger syntax

### Account metadata

Accounts opt in explicitly; nothing is inherited from parent accounts.

```beancount
2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Liabilities:CreditCard:Visa USD
  cash-account: TRUE
2020-01-01 open Expenses:Groceries USD
  envelope: "Groceries"
2020-01-01 open Assets:401k USD
  envelope: "Retirement"
2020-01-01 open Expenses:Taxes:Federal USD
  envelope: "pool"
2020-01-01 open Income:Salary USD
```

The reserved value `"pool"` sends an account's postings to the pool instead of
an envelope. Use it for taxes and other deductions that never reach an envelope.
Income and Equity accounts need no `envelope` — they flow to the pool by default.

### Directives

One directive name, `"envelope"`, with a verb as the first argument. See
[`schema.md`](schema.md) for the full reference.

```beancount
2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Groceries"
2024-01-01 custom "envelope" "open" "Car"
  target: 15000.00 USD
  by: 2028-06-01
  monthly: 500.00
  priority: 1
2024-01-31 custom "envelope" "allocate" "Groceries" 600.00 USD
2024-02-15 custom "envelope" "move" "Groceries" "Gifts" 50.00 USD
2027-12-31 custom "envelope" "close" "Old"
2028-01-01 custom "envelope" "plan" "Mortgage"
  monthly: 2600.00
2029-01-01 custom "envelope" "unplan" "Mortgage"
```

- `start` begins tracking. Earlier postings and allocations are ignored; the
  pool starts at the cash balance just before this date.
- `open` declares an envelope. Its five metadata keys — `monthly`, `ahead`,
  `priority`, `target` and `by` — describe its **plan**: the funding parameters
  that `fund --mode topup` and `suggest` read. With no metadata, the envelope
  exists but is never auto-funded.
- `allocate` moves money from the pool into an envelope. A negative amount moves
  money back to the pool.
- `move` moves money between two envelopes, e.g. to cover an overspend.
- `close` retires an envelope. Its balance must be zero.
- `plan` changes an envelope's plan from its date. It takes the **same** metadata
  as `open` and changes only the fields it names, so `monthly: 2600.00` alone
  leaves `ahead`, `priority`, `target` and `by` as they were. `open` and `plan`
  therefore resolve to the same object.
- `unplan` clears an envelope's plan from its date. The envelope keeps its
  balance and stays in reports, but nothing is planned for it and `topup` stops
  funding it.

`monthly` is a number, or `"avgN"` for the average of the last `N` completed
months of spending — `"avg3"`, `"avg6"`, and so on.

Directives are processed in date order, and a `plan` or `unplan` must name an
envelope that has already been declared. So it must be dated on or after its
`open`, and that `open` must itself be on or after `start` (an `open` before
`start` is ignored, `W002`). A `plan`/`unplan` that names an envelope not yet
declared is reported as `E001`.

### Overrides

The same `envelope` key works on a transaction or on a single posting:

```beancount
2027-03-02 * "Costco" "Groceries and a birthday gift"
  Assets:Checking          -120.00 USD
  Expenses:Groceries         80.00 USD
  Expenses:Groceries         40.00 USD
    envelope: "Gifts"
```

## Resolution rules

Every transaction on or after the start date is processed in four steps:

1. **Filter.** A transaction with no posting to a cash account is ignored, since
   no cash moved. Clearing and transfer accounts must therefore be classified
   deliberately — see [Transfers and clearing accounts](#transfers-and-clearing-accounts).
2. **Classify postings.** A posting is cash if its account has
   `cash-account: TRUE`, otherwise non-cash. A `FALSE` value is the same as
   leaving the key out.
3. **Resolve the envelope** of each non-cash posting, first match wins:
   1. `envelope` metadata on the posting.
   2. `envelope` metadata on the transaction — but only for postings whose
      account already has an envelope. It never redirects a posting that goes to
      the pool.
   3. `envelope` metadata on the account's `open`.
   4. Income and Equity accounts with none of these resolve to the pool. Any
      other account is a validation error.
4. **Apply effects.** A non-cash posting with signed units `u` changes the
   resolved envelope (or the pool) by `-u`.

A purchase (`Expenses +50`) lowers its envelope by 50; a refund (`Expenses -20`)
raises it by 20. Cash-to-cash transactions are neutral, which is why a credit
card payment from checking changes nothing — spending hits its envelope when the
purchase hits the card.

Payroll deferrals need one override. If `Assets:401k` has
`envelope: "Retirement"`, a payroll deduction would lower Retirement without
ever passing through the pool. Put `envelope: "pool"` on the 401k posting to
charge the pool instead:

```beancount
2024-01-31 * "Payroll"
  Assets:Checking             3500.00 USD
  Expenses:Taxes:Federal      1000.00 USD
  Assets:401k                  500.00 USD
    envelope: "pool"
  Income:Salary              -5000.00 USD
```

## Computation

For each envelope and month, the closing balance is the opening balance plus
allocations plus activity:

```
closing(e, m) = closing(e, m-1) + allocated(e, m) + activity(e, m)
```

- **Opening.** The first month starts every envelope at 0. Later months start at
  the previous closing.
- **Allocated.** Allocations dated in the month, plus moves in, minus moves out.
- **Activity.** The summed envelope effects, dated by transaction date.
- **Pool.** Pool closing = pool opening + pool effects − allocations dated in the
  month. Moves between envelopes do not touch it.

Overspending needs no special rule: a negative balance simply carries forward
until later allocations cover it. A pool that goes negative on any day means
more was allocated than the cash available at that point, and is reported as a
warning.

## Validation

The plugin runs these checks at load time and returns them as ordinary
Beancount errors, so Fava lists them on its errors page. Errors never stop the
ledger from loading. See [`errors.md`](errors.md) for the full list and the fix
for each.

The one to know best is the **invariant** (`E007`): at every month end, the pool
plus all envelope balances must equal the ledger's cash balance. A mismatch means
a posting was missed or unsupported.

If there is no `start` directive, the plugin does nothing and warns (`W003`).

## Command line

```sh
beancount-envelope-budget check  LEDGER
beancount-envelope-budget report LEDGER [--month YYYY-MM] [--months N]
beancount-envelope-budget suggest LEDGER
beancount-envelope-budget fund LEDGER --from YYYY-MM --to YYYY-MM [--mode MODE]
beancount-envelope-budget fund LEDGER --mode topup [--date DATE] [--reserve AMOUNT] [--strict]
beancount-envelope-budget pin LEDGER --account REGEX [--through DATE] [--write]
```

See [`cli.md`](cli.md) for the full reference and `beancount-envelope-budget
--help`.

- `check` exits non-zero on any error.
- `report` prints opening, allocated, activity and closing for one to three
  months, plus the pool and cash rows.
- `fund` prints `allocate` directives (redirect them into a file you include); it
  never edits your ledger.
- `pin` freezes an account's current envelope onto its existing postings so you
  can change the account's metadata without reclassifying history.

## Fava report

Add the extension line and an **Envelopes** report appears in the sidebar:

```beancount
2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext"
```

It is read-only, shows one to three months, and offers hover text for envelope
targets and the transactions behind each activity figure.

## BQL recipes

BQL can report what you spent per envelope, but it cannot see allocations,
because those are `custom` directives. Balances, carryover and the pool come from
the CLI or the Fava report.

The effective envelope of a posting is its own metadata, falling back to the
account's:

```sql
COALESCE(any_meta('envelope'), open_meta(account, 'envelope'))
```

Spending per envelope for a month:

```sql
SELECT COALESCE(any_meta('envelope'), open_meta(account, 'envelope')) AS env,
       sum(position) AS amount
WHERE account ~ '^Expenses' AND date >= 2027-03-01 AND date < 2027-04-01
GROUP BY env
```

One known difference from the core: BQL applies a transaction-level override to
every posting without its own, while the core applies it only to postings whose
account already has an envelope. Treat the CLI report as authoritative. If the
expression gets clumsy, run the plugin with the `"resolved"` config to write the
resolved envelope to each posting as `envelope-resolved`, and query
`meta('envelope-resolved')` instead.
