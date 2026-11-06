# Funding

How the budget decides whether an envelope is funded, and how much is still left to allocate. This is the reference for the plan shapes and every figure derived from them; [`concepts.md`](concepts.md) summarises it and links here.

Each envelope-month has two figures. The **target** is what the envelope should reach this month; the **funded** amount is what is credited toward it. The envelope reads **under** plan when funded is below target, **on** plan when they are equal, and **over** plan when funded is above it. What the target and funded amount mean depends on the plan shape, and the shapes fall into two camps: a **flow** (the month's allocation) and a **stock** (a balance or a cumulative total).

## The plan shapes

The schema allows four shapes (from `Plan`), and `priority` orders funding without changing any figure. `monthly` is a number or `"avgN"` — the average of the last `N` completed months of spending, rounded to the cent.

| Shape | Target | Funded (measured against) | Status | Ring fill | Top-up |
| --- | --- | --- | --- | --- | --- |
| No plan | `—` | `—` | none | none | `0` |
| `monthly` | `M` (the month's amount) | `A` (this month's allocation) | compare `A` vs `M` | `A / M` | `max(0, M − A)` |
| `monthly` + `up-to` | `min(up_to, O + M)` | `C` (closing balance) | on when top-up is `0`, else under | `C / up_to` | `min(max(0, M − A), max(0, up_to − C))` |
| `goal` (+ `by`) | `goal × elapsed / total` | cumulative contributions | compare | `funded / goal` | `max(0, target − funded)` |

`O` is the opening balance, `A` the month's allocation, `C = O + A + X` the closing balance (`X` is the month's activity), and `M` the resolved monthly amount. The envelope need — what a "fully fund" action allocates — is always `max(top_up, overspend_cover)`, where `overspend_cover = max(0, −C)`, so an overspent envelope is covered even when its plan status reads on.

## `monthly` is a flow

A `monthly` envelope is judged by the money **assigned that month**, not by what it holds. Receiving the planned amount reads as funded for that month even after the month's spending, and — the point of the flow — a month that allocates nothing reads as unfunded even when a balance carried in. So an envelope funded in January and left alone in February needs February's monthly amount again; carryover does not silently satisfy the plan.

`"avgN"` resolves `M` the same way, from the trailing spending, and the flow is `M − A` for the month.

## `monthly` + `up-to` is a stock ceiling

An `up-to` envelope treats `monthly` as a per-month contribution **allowance** and the balance as the thing to cap: the top-up is the smaller of what is left of the month's allowance (`M − A`) and the room still under the ceiling (`up_to − C`). Once the balance reaches the ceiling the allowance goes unspent and funding stops; spending re-opens room, but only the current month's remaining allowance can refill it, so an envelope that is full, or whose month's allowance is used up, reads as funded. Its target, `min(up_to, O + M)`, is the balance it should reach after the month's intended contribution.

## `goal` is a cumulative total

A goal envelope is funded by its **cumulative contributions**: the goal-start month's opening balance plus every allocation, income and refund since, with spending ignored. It equals the balance plus everything spent since the goal began, so a goal that has been fully saved stays funded after the money is spent — take the trip and the envelope still reads green, with a full ring. The target is the on-track contribution at the month: `goal` grown linearly from the month the goal first took effect to its `by` month, both ends inclusive, so the target is the full `goal` once `by` arrives. `unplan` then `plan` restarts the clock and re-seeds the count with whatever balance remains.

## How the figures are used

- The **target** and **funded** amount drive the closing balance's colour and the ring's fill in the report, and the under/on/over verdict in `explain` and the Fava closing popover.
- The **top-up** is the prefill on the add-allocation form and what `fund --mode topup` allocates.
- The **envelope need** backs the month-level *fully fund* and *fund with available* actions and `fund --mode fully-fund` / `fund-available`, and `priority` orders them when the pool caps the run.
- `funded` is also what `suggest` and the over-funded note read for goal envelopes.

## Worked examples

A `monthly 300` envelope:

| Month | `O` | `A` | Target | Funded | Status | Top-up |
| --- | --- | --- | --- | --- | --- | --- |
| January | 0 | 300 | 300 | 300 | on | 0 |
| February | 300 | 0 | 300 | 0 | under | 300 |
| March | 300 | 150 | 300 | 150 | under | 150 |

February carries the January balance but allocates nothing: the flow is zero, so the month still needs its full `300`.

A `monthly 200` + `up-to 600` envelope:

| Month | `O` | `A` | `C` | Target | Funded | Status | Top-up |
| --- | --- | --- | --- | --- | --- | --- | --- |
| January | 0 | 100 | 100 | 200 | 100 | under | 100 |
| February | 100 | 200 | 300 | 300 | 300 | on | 0 |
| March | 300 | 200 | 500 | 500 | 500 | on | 0 |
| April | 500 | 0 | 500 | 600 | 500 | under | 100 |

## Cleanup of an `up-to` overshoot

An `up-to` envelope can end a month holding more than its ceiling: a refund or
income into it, or an allocation larger than its monthly allowance, pushes the
balance past `up_to`. Its funded amount is its closing balance, so that excess
is money the plan does not want, and **cleanup** releases it back to the pool.

The amount released is `max(0, closing − up_to)` — the balance down to the
ceiling — written as a negative `allocate` dated in the month. Afterward the
funded amount is `up_to` again, so the month reads on plan. Only an envelope with
an `up-to` ceiling is ever cleaned:

| Shape | Cleanup |
| --- | --- |
| No plan | none |
| `monthly` | none — a flow, judged by the month's allocation |
| `avgN` | none — a flow, like `monthly` |
| `monthly` + `up-to` | `max(0, closing − up_to)` |
| `goal` | none — a cumulative total the plan wants to keep |

A plain `monthly`/`avgN` envelope is a flow: its funded amount is the money
assigned that month, not its balance, so a negative allocation would only make
the month read as under-funded, and the next funding run would refill it. A
`goal` keeps its balance after the money is spent, so it is never swept either.
Cleanup is therefore the one deliberate exception to "balances always carry
forward".

Run it with `fund --mode cleanup --date YYYY-MM-DD`, or the month heading's
**Clean up month** action in a write-enabled Fava report; both emit the same
negative `allocate` directives, so nothing is written until you redirect or save
them. Re-running is safe: once the balance sits at the ceiling the amount is
zero. Because the released money only ever *adds* to the pool, cleanup cannot
push the pool negative.
