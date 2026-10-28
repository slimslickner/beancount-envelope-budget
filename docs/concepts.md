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
| **Plan** | An envelope's funding parameters — `monthly`, `priority`, `target`, `by` — set on `open`, changed from a date with `plan`, and cleared with `unplan`. |

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
| Buy a security from cash | – | ↓ | ↓ |
| Sell a security (cost refunded, gain to pool) | ↑ | ↑ | ↑ |
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

## Investments and securities

The budget's ground truth is cash in the operating currency. Investments are
**off-budget**: a share is not a budget dollar, and its price moves for reasons
unrelated to your spending. This mirrors YNAB and Actual Budget, where
investment accounts are "tracking" accounts whose value never enters the budget.

A brokerage usually has two parts:

- **Cash** (the sweep) may be on-budget with `cash-account: TRUE`; it is as
  liquid as a savings account.
- **Securities** are off-budget. Do not mark them `cash-account`; instead give
  each one `envelope: "<name>"`, so buying spends from that envelope and selling
  refunds it.

```beancount
2020-01-01 open Assets:Brokerage:Cash USD
  cash-account: TRUE
2020-01-01 open Assets:Brokerage:VTI VTI
  envelope: "Taxable Investments"
2024-01-01 custom "envelope" "open" "Taxable Investments"

2026-01-05 * "Buy VTI"
  Assets:Brokerage:VTI           5 VTI {200.00 USD}
  Assets:Brokerage:Cash  -1000.00 USD
```

Naming an envelope in metadata only *refers* to it; the envelope still has to
be declared once with `custom "envelope" "open" "<name>"` on or after `start`.
A sale that Beancount books across several lots resolves each lot posting, so an
undeclared name is reported for every lot (`E001`).

The purchase is valued at its cost (`5 × 200.00 = 1000.00 USD`): the Taxable
Investments envelope falls by 1000 and cash falls by 1000, so the invariant
still holds. A sale with a capital gain refunds the cost basis to the envelope
and sends the gain to the pool:

```beancount
2026-02-05 * "Sell VTI"
  Assets:Brokerage:VTI          -5 VTI {200.00 USD}
  Assets:Brokerage:Cash   1100.00 USD
  Income:Brokerage:Gains  -100.00 USD
```

A posting already in the operating currency is unchanged. A currency with no
cost or price in the operating currency is still a foreign currency (`E006`),
and a security with no envelope is `E002` — the cash has to go somewhere.

To keep the whole brokerage out of the budget instead, leave `cash-account` off
the sweep too. Then none of its transactions move budget cash, so none are
reported.

Treating a *stable* cash equivalent — a money-market fund, or T-bills pegged
near 1.00 — as budget cash is conceivable, but volatile securities are not. If
that ever lands it should be a separate notion (a commodity account valued at
cost or mark), not `cash-account: TRUE`.

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
- `open` declares an envelope. Its four metadata keys — `monthly`, `priority`,
  `target` and `by` — describe its **plan**: the funding parameters that
  `fund --mode topup` and `suggest` read. With no metadata, the envelope exists
  but is never auto-funded.
- `open` also accepts `parent`, a free-form grouping label. Envelopes sharing a
  parent are shown together under a heading in the report, with a subtotal of
  their balances. It does not need to name another envelope, and it does not
  affect funding, balances or the invariant. Parent headings and the envelopes
  within them are ordered alphabetically; a leading number sorts as text, so
  prefix a name with a zero-padded number (`01`, `02`, `10`) to put it in a
  chosen place.
- `allocate` moves money from the pool into an envelope. A negative amount moves
  money back to the pool.
- `move` moves money between two envelopes, e.g. to cover an overspend.
- `close` retires an envelope. Its balance must be zero.
- `plan` changes an envelope's plan from its date. It takes the **same** metadata
  as `open` and changes only the fields it names, so `monthly: 2600.00` alone
  leaves `priority`, `target` and `by` as they were. `open` and `plan`
  therefore resolve to the same object.
- A plan funds an envelope one of two ways: `monthly` holds about a month's
  spending, or a `target` with a `by` date holds a goal. The two are mutually
  exclusive, and a `target` needs a `by`. A plan that names only one half of a
  pair, or both `monthly` and `target`, is reported as `E010` rather than being
  silently ignored.
- A goal envelope's **level** is the balance it should hold to stay on schedule:
  its `target`, grown linearly from the month the target first took effect to
  its `by` month, counting both ends, so the level is the full `target` once
  `by` arrives. `fund --mode topup` funds up to that level, and the report shows
  each envelope's level and whether it is under, on or over it.
- An envelope's **funded amount** for a month is what its level is compared
  against. A `monthly` envelope is funded by the money made available that month
  — its opening balance plus the month's allocation — so receiving the planned
  amount reads as funded even after the month's spending. A goal envelope is
  funded by its closing balance, because a `target` is a balance to hold. The
  report colours and rings envelopes by this comparison, and `fund --mode topup`
  funds up to it.
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

**Only income that moves cash is counted.** The filter in step 1 is
transaction-wide: a transaction is processed only if it contains a posting to a
cash account. Income counts only when it rides along in such a transaction —
a paycheque (`Income:Salary` alongside `Assets:Checking`), say. Income that
never touches a cash account is invisible to the budget: accrued interest,
reinvested dividends, or an employer 401k match credited straight to a
brokerage account produce no cash posting, so the whole transaction is skipped.
The model is deliberately cash-centric — every dollar in a cash account is
either assigned to an envelope or sits in the pool.

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

Because the invariant cross-checks flows against cash, it is sensitive to
transactions that do not balance exactly. Beancount accepts a small imbalance,
sized by its inferred tolerance and by `option "inferred_tolerance_default"`. The
budget needs exact arithmetic, so it treats any residual within that same
tolerance as rounding and folds it into the pool — the same place an
`Equity:Rounding-Errors` posting would land. A residual beyond the tolerance is
left alone: Beancount reports the imbalance itself, and the month-end invariant
(`E007`) reflects it until it is booked.

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
- `fund` prints `allocate` directives (redirect them into a file you include), or
  appends them with `--write FILE`; plain `fund` never edits your ledger.
- `allocate`, `move`, `plan` and `unplan` print one directive, or append it with
  `--write FILE`, so the CLI can make the same ledger edits the Fava report can.
- `pin` freezes an account's current envelope onto its existing postings so you
  can change the account's metadata without reclassifying history.

## Fava report

Add the extension line and an **Envelopes** report appears in the sidebar:

```beancount
2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext"
```

It is read-only unless you configure a write target; by default it shows one to
three months and offers hover text for envelope targets. Hovering or focusing an activity figure reveals a popover listing the
postings behind it; each line links to that transaction in Fava's entry popup.
The line reads `payee - narration`: the transaction's payee, then the posting's
`narration` metadata — the `autobean.narration` convention — falling back to the
transaction's narration, so the line items of one transaction read apart, such as
a mortgage payment's `Principal`, `Escrow` and `Interest`. The months sit side by
side when the window is wide enough and stack when it is narrow, and envelopes
are grouped by their `parent` label with a subtotal per group, parent headings
and envelopes in alphabetical order. Parent categories collapse to their subtotal
when their name is clicked.

The table is a self-contained scroll area: its header, envelope column and
pool/cash rows stay in place, and the month links stay reachable at the top, so
you can move around a long or wide report. Each parent heading pins under the
table header while its group scrolls, and a single "Collapse all" control folds
every parent. The current month's header is labelled `(current)`, alongside the
`(projected)` label, and a "this month" link jumps to it. Closing balances are
colour-coded — red when overspent, green when funded to their plan, amber when
underfunded — and an opening balance turns red when negative. Whether an
envelope is funded is judged by its funded amount, as above: a `monthly`
envelope stays green once it has received its month's money even if it has since
been spent, while a goal envelope is measured by its balance. A planned envelope
also carries a small ring beside its closing balance, filled in proportion to
that funded amount against its goal (its `target`, or its monthly `level` when it
has no target); the ring takes the balance's colour. The colours come from
Fava's own variables, so they follow its light and dark schemes.

Set the default number of months (1 to 3) with an optional config, a
stringified Python mapping on the extension line; the `months` URL parameter
still overrides it:

```beancount
2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext" "{'months': 2}"
```

### Editing from the report

Add a `write` key naming a Beancount file, relative to the ledger's directory, to
let the report append `custom "envelope"` directives:

```beancount
2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext" "{'write': 'beans/Envelope.bean'}"
```

A target that escapes the ledger directory or is not a `.bean`/`.beancount` file
is refused, and writing stays off when the key is absent. With it set, the report
gains an **Edit budget** panel whose forms add an allocation (prefilled with the
month's spending, as `fund` would), move money between envelopes, set or clear a
plan. Each form appends exactly one directive, printed by Beancount's own printer
via `actions.py` — the same generators the CLI's `allocate`, `move`, `plan` and
`unplan` commands print or write. Writes go through Fava's own file API, so its
lock, watcher and editor stay consistent. This makes a write-enabled Fava a
remote ledger mutation if Fava is exposed beyond localhost; keep it local.

The invariant is unchanged: pure code still never touches disk. Writing is an
explicit, opt-in surface, here and in the CLI.

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
