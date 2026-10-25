---
name: Envelope Budgeting CLI
description: Use when working with a Beancount envelope budget and the beancount-envelope-budget CLI — validating a ledger, reporting envelope balances, funding envelopes after a paycheck, backfilling historical allocations, or remapping an account with pin. Covers the safe workflow and the command outline.
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
- `fund` and `pin` (without `--write`) only **print**. Redirect their output into
  a file you `include`; never paste allocations by hand.
- `pin --write` **edits the ledger in place**. Show the diff first and get the
  user's approval before writing.
- Prefer `report --json` when you need to read numbers programmatically.
- Show the exact command you ran and its exit code.

## Command outline

When working inside this repository, prefix commands with `uv run`.

| Command | Purpose |
| --- | --- |
| `check LEDGER` | Validate and check the invariant. Exits non-zero on any error. |
| `report LEDGER [--month YYYY-MM] [--months N] [--json]` | Opening / allocated / activity / closing per envelope, plus pool and cash, for one to three months. |
| `suggest LEDGER` | The monthly amount each goal envelope (`target` + `by`) needs. |
| `fund LEDGER --from YYYY-MM --to YYYY-MM [--mode actuals\|overspend\|copy] [--envelopes REGEX] [--force]` | Print `allocate` directives to backfill or roll a range forward. |
| `fund LEDGER --mode topup [--date YYYY-MM-DD] [--reserve AMOUNT] [--strict]` | Print `allocate` directives funding envelopes up to their level, in priority order. |
| `pin LEDGER --account REGEX [--through YYYY-MM-DD] [--write]` | Freeze the resolved envelope onto existing postings so an account can be remapped without changing history. |
| `schema [--json]` | The directive, metadata and reserved-value schema. |
| `completion SHELL` | Tab-completion snippet for bash or zsh. |

`fund` modes:

- `actuals` (default) — allocate each month's net spending, so closings equal
  openings. The backfill mode.
- `overspend` — allocate only enough to bring a negative envelope back to zero.
- `copy` — repeat the previous month's allocation (roll one month forward).
- `topup` — fund up to `monthly × (1 + ahead)`, in priority order, from the pool.

The three range modes skip any envelope-month that already has an allocation;
`--force` overrides. `topup` never skips, because it measures from current
balances — rerun it after each paycheck and append the new output.

An envelope's plan is its `monthly`, `ahead`, `priority`, `target` and `by`
metadata, set on `open` or changed from a date with `plan`
(`custom "envelope" "plan" "<name>"` plus the same metadata), and cleared with
`unplan`. `monthly` may be a number or `"avgN"`, the average of the last N
completed months of spending.

**`topup` needs a plan.** With no `monthly` (and no `target` + `by`) it has no
level to fund to and prints nothing. To top spent envelopes back up without any
plan directives, use `overspend` over a range instead — e.g. the current month:
`fund LEDGER --from YYYY-MM --to YYYY-MM --mode overspend` brings negative
envelopes back to zero. `actuals` funds each month's net spending, so months
close flat.

## Which workflow?

- **New ledger** → set up accounts and directives, then `check`. See
  `references/workflows.md#first-time-setup`.
- **After a paycheck** → `fund --mode topup`, review, append, `check`. See
  `references/workflows.md#business-as-usual`.
- **Existing history** → `fund --mode actuals` over the range, include the output,
  `check`. See `references/workflows.md#backfill-history`.
- **Moving an account to a new envelope** → `pin`, then change the metadata. See
  `references/workflows.md#remap-an-account`.

## Pitfalls

- `E002` means a non-cash account moved cash without an envelope. Give it an
  `envelope:` (or `envelope: "pool"` for taxes and pre-tax deductions).
- `E001` means a directive or posting names an envelope that was never declared.
- `W001` means the pool went negative on some day — usually allocating before the
  income that funds it lands.
- The invariant `E007` compares the pool plus envelopes against cash at each month
  end; a mismatch means a posting was missed.
- `topup` measures what is on hand at the run date; run it after the month's last
  bill and paycheck, not mid-month.
