# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `E010` reports a plan whose metadata has no effect: `target` without `by`,
  `by` without `target`, or `monthly` with `target`.
- The report view exposes each envelope's funding `level` and whether it is
  under, on or over it, for the CLI and Fava.
- The Fava report reveals the postings behind an activity figure in a popover on
  hover or focus, each linking to that transaction in Fava's entry popup.
- The activity drill-down reads `payee - narration`, preferring a posting's
  `narration` metadata (the `autobean.narration` convention) and falling back to
  the transaction's narration, so the line items of one transaction read apart.
- The report view exposes each envelope's goal `target` and how full it is toward
  it (`fill`), for the CLI and Fava.
- A planned envelope in the Fava report carries a ring beside its closing
  balance, filled in proportion to its goal (or its monthly level) and coloured
  by its funding status.
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
  `fava-extension` line: an **Edit budget** panel adds an allocation (prefilled
  with the month's spending, as `fund` would), moves money between envelopes, and
  sets or clears a plan. Writing is off by default and refuses a target that
  escapes the ledger directory.

### Changed

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

### Removed

- The `ahead` metadata key. A buffer is an explicit `allocate`; an envelope's
  level is already its planned balance.

### Fixed

- The CLI `report` and the Fava Envelopes page default to the current month
  rather than the latest month in the ledger, so a future-dated `custom
  "envelope"` directive no longer opens the report on a month in the future.
