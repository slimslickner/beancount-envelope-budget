---
name: Envelope Budgeting CLI
description: Use when working with a Beancount envelope budget and the beancount-envelope-budget CLI — validating a ledger, reporting envelope balances, funding envelopes after a paycheck, backfilling historical allocations, consolidating duplicated allocations, or remapping an account with pin. Covers the safe workflow and the command outline.
---

# Envelope Budgeting CLI

`beancount-envelope-budget` derives an envelope budget from an ordinary Beancount
ledger. Every dollar in your cash accounts is either assigned to an **envelope**
or sits in an unassigned **pool**; balances carry from month to month. The ledger
is the only source of truth — allocations are dated `custom "envelope"`
directives and spending is derived from postings.

The full reference lives in `docs/cli.md`; the model is in `docs/concepts.md`;
directive/metadata syntax in `docs/schema.md`; error codes in `docs/errors.md`.
Worked examples are in `references/workflows.md` beside this file.

## Safety rules

- **Always run `check` first**, and again after any change.
- `check`, `report` and `suggest` are **read-only**.
- `fund` only **prints** directives: redirect its output into a file you
  `include`; never paste allocations by hand.
- `pin` and `consolidate` **print a diff** and change nothing. `pin --write` and
  `consolidate --write` **edit the ledger in place**: show the diff first and get
  the user's approval before writing.
- Prefer `report --json` when you need to read numbers programmatically.
- Show the exact command you ran and its exit code.

## Command outline

When working inside this repository, prefix commands with `uv run`.

| Command | Purpose |
| --- | --- |
| `check LEDGER` | Validate and check the invariant. Exits non-zero on any error. |
| `report LEDGER [--month YYYY-MM] [--months N] [--json]` | One table spanning one to three months: a row per envelope (opening once, then allocated / activity / closing per month) plus pool and cash. |
| `explain LEDGER [--month YYYY-MM] [--envelope REGEX] [--json]` | How each planned envelope is evaluated for a month: the target's formula (the resolved `monthly`, an `avgN`'s averaged months, the `up-to` ceiling, or the goal schedule), the funded amount, the under/on/over verdict and the suggested top-up. |
| `pool LEDGER [--all\|--month YYYY-MM\|--date YYYY-MM-DD] [--json]` | The pool's per-transaction ledger with the running balance after each line; a bare run opens on the pool's lowest month (or the latest when it never went negative). |
| `history LEDGER --envelope REGEX [--json]` | One envelope's whole history as a vertical ledger, grouped by month: its `open` and plan directives, allocations, spending and `close`, each with the running balance. |
| `suggest LEDGER` | The monthly amount each goal envelope (`goal` + `by`) needs. |
| `fund LEDGER --from YYYY-MM --to YYYY-MM [--mode actuals\|overspend\|copy] [--envelopes REGEX] [--force]` | Print `allocate` directives to backfill or roll a range forward. |
| `fund LEDGER --mode topup [--date YYYY-MM-DD] [--reserve AMOUNT] [--strict]` | Print `allocate` directives funding envelopes to their target, in priority order. |
| `fund LEDGER --mode fully-fund\|fund-available [--date YYYY-MM-DD] [--reserve AMOUNT] [--strict]` | Print `allocate` directives funding every envelope to its plan or its overspend, whichever is greater; `fund-available` caps the run by the pool. |
| `pin LEDGER --account REGEX [--through YYYY-MM-DD] [--write]` | Freeze the resolved envelope onto existing postings so an account can be remapped without changing history. |
| `consolidate LEDGER [--envelope REGEX] [--month YYYY-MM] [--write]` | Merge an envelope-month's several `allocate` directives into one, dated month-end (today, in the current month). Prints a diff; the net amount, the pool and the invariant are unchanged. |
| `schema [--json]` | The directive, metadata and reserved-value schema. |
| `completion SHELL` | Tab-completion snippet for bash or zsh. |

`fund` modes:

- `actuals` (default) — allocate each month's net spending, so closings equal
  openings. The backfill mode.
- `overspend` — allocate only enough to bring a negative envelope back to zero.
- `copy` — repeat the previous month's allocation (roll one month forward).
- `topup` — fund each envelope to its target, in priority order, from the pool.
  A `monthly` envelope is a flow: it funds what is left of the month's amount
  after the month's allocations. A goal envelope's target is the on-track
  contribution for the run month. An `up-to` envelope funds what is left of the
  month's `monthly` allowance, never past the ceiling.
- `fully-fund` — the month-level action: fund every envelope to its plan target
  or its overspend, whichever is greater, in priority order. Unlike `topup`,
  envelopes with no plan are still funded their overspend.
- `fund-available` — the same as `fully-fund`, but stop before the pool would go
  negative; anything left unfunded prints as `; UNFUNDED`.

The three range modes skip any envelope-month that already has an allocation;
`--force` overrides. `topup`, `fully-fund` and `fund-available` never skip,
because they measure from current balances — rerun them after each paycheck and
append the new output.

An envelope's plan is its `monthly`, `up-to`, `priority`, `goal` and `by`
metadata, set on `open` or changed from a date with `plan`
(`custom "envelope" "plan" "<name>"` plus the same metadata), and cleared with
`unplan`. `monthly` may be a number or `"avgN"`, the average of the last N
completed months of spending. `up-to` pairs with `monthly` to cap the balance: it
requires `monthly` and cannot be combined with `goal`.

**`topup` needs a plan.** With no `monthly` (and no `goal` + `by`) it has no
target to fund to and prints nothing. Use `fully-fund` for a whole month instead:
it funds every envelope to its plan or its overspend, whichever is greater, and
`fund-available` does the same while keeping the pool non-negative. To top spent
envelopes back up without any plan directives over a range, use `overspend` —
e.g. the current month: `fund LEDGER --from YYYY-MM --to YYYY-MM --mode
overspend` brings negative envelopes back to zero. `actuals` funds each month's
net spending, so months close flat.

## Which workflow?

- **New ledger** → set up accounts and directives, then `check`. See
  `references/workflows.md#first-time-setup`.
- **After a paycheck** → `fund --mode topup`, review, append, `check`. See
  `references/workflows.md#business-as-usual`.
- **Existing history** → `fund --mode actuals` over the range, include the output,
  `check`. See `references/workflows.md#backfill-history`.
- **Moving an account to a new envelope** → `pin`, then change the metadata. See
  `references/workflows.md#remap-an-account`.
- **Tidying up duplicate allocations** → `consolidate`, review the diff, then
  `--write`. See `references/workflows.md#tidy-up-allocations`.
- **Reviewing one envelope's history** → `history LEDGER --envelope NAME`. See
  `references/workflows.md#review-an-envelope`.

## Pitfalls

- `E002` means a non-cash account moved cash without an envelope. Give it an
  `envelope:` (or `envelope: "pool"` for taxes and pre-tax deductions).
- `E001` means a directive or posting names an envelope that was never declared.
- `W001` means the pool closed a month negative — usually allocating before the
  income that funds it landed. Only month ends are judged, so a dip that recovers
  by month end is not reported; run `pool LEDGER` to see the pool's per-transaction
  ledger either way.
- Beancount runs `beancount.ops.pad` after every user plugin, so a plugin that
  runs earlier would miss `pad`'s synthesized transactions; the library applies
  `pad` before computing to stay in step with the report. `plugin_processing_mode
  "raw"` opts out.
- The invariant `E007` compares the pool plus envelopes against cash at each month
  end; a mismatch means a posting was missed.
- `topup` funds each envelope to its target at the run date — for a `monthly`
  envelope what is left of the month's amount, for an `up-to` or `goal` envelope
  what is on hand — so run it after the month's last bill and paycheck, not
  mid-month.
