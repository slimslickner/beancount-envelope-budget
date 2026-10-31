# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Breaking:** a plan's `target` + `by` is now `goal` + `by`, and is a one-shot
  savings goal funded by **cumulative contributions** rather than a balance. A
  goal envelope is credited with its goal month's opening balance plus every
  allocation, income and refund since, and spending is ignored — so a
  goal that has been fully saved stays funded after the money is spent (save for
  a trip, take the trip, and the envelope stays green). `fund --mode topup` and
  `suggest` follow the same contributions measure, so they no longer re-fund
  spending. To hold and refill a balance instead, use `monthly` with `up-to`
  (#22). Update ledgers to the new key; there are no users before 1.0.

### Removed

- **Breaking:** the `move` directive verb, the `move` CLI command and the Fava
  move action. A `move A B X` was only ever two allocations — `allocate A -X`
  and `allocate B X` — with the pool untouched either way, so it carried no
  unique semantics. Write the two `allocate` directives instead; a leftover
  `move` line is now reported as `E008` (unknown verb) (#55).

### Added

- The Fava report's month navigation is a selectable strip of three-letter
  months, showing a sliding block of at most eight around the visible window
  with a marker whenever the year turns, so you can jump several months at once
  and keep stepping into projected months. Each link sets the window's first
  month and keeps the current month count; today's month and the visible window
  are marked, a **Today** link returns to the current month, and the ledger's
  name column is sized so it no longer resizes as you navigate (#20).
- An `up-to` metadata key on a plan pairs with `monthly` to cap a balance: fund
  by the monthly amount each month, but never past the ceiling. Once full, or
  once the month's allowance is used up, funding stops; spending re-opens room.
  It works through `fund --mode topup`, the month funding actions and the Fava
  report, all sharing one rule (#22).
- The Fava report gains a **Reports** tab (`?view=reports`, renamed from
  Dashboard; the ledger grid is now headed **Envelopes**) that spans the budget's
  whole history as table rows of month columns: the cash and pool balances, then
  the per-month top spending, allocated and ending-balance envelopes (#42).
- The Reports rows scroll together in one horizontal strip, their month columns
  a fixed shared width sized to the longest name they hold, with each row's
  title pinned as the months scroll past (#42).

- `E010` reports a plan whose metadata has no effect: `goal` without `by`,
  `by` without `goal`, or `monthly` with `goal`.
- The report view exposes each envelope's funding `level` and whether it is
  under, on or over it, for the CLI and Fava.
- The Fava report reveals the postings behind an activity figure in a popover on
  hover or focus, each linking to that transaction in Fava's entry popup.
- The activity drill-down reads `payee - narration`, preferring a posting's
  `narration` metadata (the `autobean.narration` convention) and falling back to
  the transaction's narration, so the line items of one transaction read apart.
- The report view exposes each envelope's goal `goal` and how full it is toward
  it (`fill`), for the CLI and Fava.
- A planned envelope in the Fava report carries a ring beside its closing
  balance, filled in proportion to its goal (or its monthly level) and coloured
  by its funding status.
- Clicking a planned envelope's closing figure in the Fava report opens a
  popover with the plan in force for that month — its goal, how funded it is,
  and whether it is under, on or over plan — linking to that directive (#39).
- The Fava report's parent categories can be collapsed to their subtotal row by
  clicking the category name; the collapsed state is not persisted across page
  loads.
- The Fava report blanks an envelope's `0.00` values while parent subtotals, the
  pool and cash always show numbers.
- The Fava report keeps its header, envelope column and pool/cash rows in place
  while the table scrolls, and its month links stay reachable at the top.
- The Fava report pins each parent heading under the table header while its
  group scrolls, and a single "Collapse all" control folds every parent.
- The Fava report labels the current month's header `(current)` and adds a
  "this month" link that jumps to it.
- The Fava report balances are colour-coded: a closing balance is red when
  overspent, green when funded to its plan, and amber when underfunded; an
  opening balance turns red when negative. The colours follow Fava's light/dark
  scheme.
- The CLI `allocate`, `move`, `plan` and `unplan` commands print one directive,
  and `fund` and those commands take `--write FILE` to append it to a ledger file
  (relative to the ledger's directory) and then reload the ledger and report any
  diagnostics.
- The Fava report can edit the budget when a `write` target is configured on the
  `fava-extension` line. Clicking an allocation figure opens a popover listing
  that month's `allocate` directives, and clicking an envelope name opens one
  listing its whole plan history; each directive links to Fava's own editor to be
  changed or deleted, and a click anywhere else closes the popover. A
  `custom "envelope"` directive can be added from the same popover — a plan's
  editor opens on the plan in force — shown as editable Beancount, rendered by
  Beancount's printer, in a small modal (vanilla JS, no build), with the form's
  fields as the no-JS fallback. Writing is off by default and refuses a target
  that escapes the ledger directory.
- Month-level funding actions. The Fava month heading offers *fully fund*, which
  funds every envelope to its plan or its overspend, whichever is greater, and
  *fund with available*, the same in priority order from the pool, stopping
  before it would go negative. Their CLI equivalents are `fund --mode
  fully-fund` and `fund --mode fund-available`; both expand to one `allocate`
  per envelope that needs funding (#7).
- A `pool` command prints the unassigned pool's per-transaction ledger — income and
  spending charged to the pool, plus every allocation leaving it — in date order
  with the running balance after each line. A bare run opens on the pool's
  lowest month when it ever went negative, else the latest month; `--all`,
  `--month`, `--date` and `--json` cover the rest (#29).
- The Fava pool row's activity figure opens the same ledger in a popover, both
  directions with the running balance, each line linking to its transaction
  (#29).
- The shared report view exposes `pool_ledger`, the pool's ledger, so the CLI,
  the Fava popover and a future dashboard read one source (#29, #42).
- A generated benchmark ledger at `tests/ledgers/sample.bean`, produced by
  `scripts/sample_ledger.py` and kept current by a `--check` in the gate, that
  exercises every feature; and `scripts/benchmark.py`, which times the loader and
  the budget over it at scale. A test runs the benchmark as a smoke test (#57).
- An `explain` command shows how each planned envelope is evaluated for a month:
  the resolved `monthly` and the completed months an `avgN` averaged, the
  `up-to` ceiling formula, or the goal schedule, plus the funding level, the
  amount credited toward it, the under/on/over verdict and the suggested
  top-up. The Fava closing popover shows the same derivation, the "add
  allocation" form explains the amount it prefills, and the report view carries
  it as a `FundingExplanation` on each cell (#62).

### Changed

- The budget computation no longer hashes the ledger. Each activity, allocation
  and pool event keeps its source directive and the report derives the hash only
  for the detail lines it shows, so `compute` no longer takes `collect_hashes`
  and hashing is paid per displayed line rather than per posting (#57).
- The Fava report builds its history-wide lookups (the activity and allocation
  indexes and the pool ledger) once per ledger load and slices only the shown
  months on each request, instead of rebuilding them on every page load, and the
  pool ledger no longer rebuilds a line per entry to fill in its running balance
  (#57).
- The plugin no longer computes Beancount entry hashes it never uses, and the
  CLI reuses the budget the plugin already computed instead of scanning the
  ledger a second time, so loading a year of transactions is materially faster
  (#47).
- A `monthly` envelope is judged funded by the money made available that month
  (its opening balance plus allocation) rather than its closing balance, so it
  stays green and full-ringed after spending its planned amount; a goal envelope
  is still judged by its balance. This drives both the report's colour and ring
  and `fund --mode topup`, which no longer re-requests an amount already
  allocated for the month.
- `fund --mode topup` funds a goal envelope (`target` + `by`) up to its on-track
  level for the run date, so a plan that raises a target mid-month now funds the
  difference, and reruns stay safe.
- A goal envelope's level grows linearly from the month its target first took
  effect to its `by` month, counting both ends.
- `suggest` and the report evaluate a plan at the latest date (or the month a
  cell represents), so a `plan` dated mid-month is reflected.
- The report and Fava view order parent headings and envelopes alphabetically,
  with ungrouped envelopes last, instead of declaration order. A leading number
  sorts as text, so a zero-padded prefix (`01`, `02`, `10`) controls the order.
- `fund --mode topup` prints `; OVERFUNDED <name>: ... above plan` for a goal
  envelope already above its level.
- The CLI report and Fava page render one table spanning all visible months,
  with each envelope on a single row (opening once, then allocated, activity and
  closing per month). An envelope open in any visible month spans the whole
  window, blanking the months before it opens or after it closes.
- The `W001` pool-floor warning now names the pool's balance at the start of the
  day it went negative and points at the `pool` command (#29).
- A folded rounding residual is now recorded in the collected activity detail, so
  the pool ledger and the Fava activity popover include the same rounding the
  warning measures.

### Removed

- The `ahead` metadata key. A buffer is an explicit `allocate`; an envelope's
  level is already its planned balance.

### Fixed

- The Fava popovers now size to their content and stay inside the window: the
  activity and pool-ledger popovers no longer clip a long line like
  `2026-10-21 · Emergency Fund · -35000.00 → 64316.97`, and every popover grows
  to the side of the cell with the most room instead of running under Fava's
  sidebar. A ledger line is never wrapped — it scrolls sideways only when it
  truly cannot fit — and a popover switches to a fixed overlay so the report's
  scroll box cannot clip it. This is the extension's JavaScript; without it the
  popovers still open, anchored to the cell (#43).
- A goal envelope's progress is now judged only up to the month being shown, so
  a month is no longer credited with allocations, income or refunds made in
  later months. The Fava report's funding ring and `fund`, `suggest` and the
  report all follow the same bound (#57).
- The Fava report now suggests funding for the month being actioned, not the
  current one: a form for a future month evaluates that month's plan and
  balances, and dates its new directive inside that month, instead of collapsing
  onto today (#22).
- The Fava **Reports** tab now respects the window's date filter: with a period
  selected it shows only the months inside it, drawing the cumulative cash and
  pool balances from the whole budget rather than a truncated history. With no
  filter it still spans full history, as does the CLI `report` (#50).
- The pool ledger (`pool` and the Fava popover) now nets each transaction's
  postings into one line, and `W001` walks the same per-transaction events, so a
  payroll's withholding and its deposit no longer read as a pool dip and the
  warning and the ledger agree line for line.
- `W001` now names the day of the pool's lowest balance, and that day's opening
  balance, rather than the first day the pool went negative — which could be a
  different day, making the warning read as wrong.
- The plugin now applies Beancount's `beancount.ops.pad` before it computes, so a
  ledger that uses `pad` reports the same pool as the CLI and the Fava report.
  Beancount runs `pad` after every user plugin, so previously the plugin saw a
  ledger without the transactions `pad` synthesizes and could warn of a negative
  pool the report never showed, or miss a padding entry that changed an envelope
  (#29). `option "plugin_processing_mode" "raw"` leaves ordering to the ledger.
- The Fava "add allocation" form no longer prefills the month's total spending.
  It prefills the envelope's plan top-up — the gap to its funding level, so money
  already allocated is not asked for twice — and, when the plan is already met
  or there is none, the amount that covers the month's overspending (#35). A
  labelled quick-fill covers that overspending directly when it differs.
- A goal envelope's funding level is rounded to the cent before it is compared
  with a balance, so re-running `fund --mode topup` on a goal whose prorated
  target is not a whole cent no longer prints a spurious
  `; OVERFUNDED ... 0.00 USD above plan` note, and the report no longer reads
  such a goal as over its level.
- Amounts are valued at the precision Beancount displays the operating currency,
  so an imported investment trade whose cost basis has a sub-cent tail no longer
  leaves an envelope at a spurious `-0.00`. Previously the residue showed as a
  red `-0.00`, was not blanked as a zero, and could trip the pool-floor warning
  (`W001`) when the pool dipped by a fraction of a cent.
- The CLI `report` and the Fava Envelopes page default to the current month
  rather than the latest month in the ledger, so a future-dated `custom
  "envelope"` directive no longer opens the report on a month in the future.
- The Fava report remembers the table's scroll position, so clicking a month
  link or saving an edit no longer jumps the table back to the top (#28).
- The Fava report refreshes itself after an edit, instead of waiting for Fava's
  watcher to notice the change and clicking the yellow reload button (#28).
