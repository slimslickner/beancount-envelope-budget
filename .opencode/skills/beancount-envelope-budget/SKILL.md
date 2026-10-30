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
| `report LEDGER [--month YYYY-MM] [--months N] [--json]` | One table spanning one to three months: a row per envelope (opening once, then allocated / activity / closing per month) plus pool and cash. |
| `pool LEDGER [--all\|--month YYYY-MM\|--date YYYY-MM-DD] [--json]` | The pool's per-transaction ledger with the running balance after each line; a bare run opens on the pool's lowest month (or the latest when it never went negative). |
| `suggest LEDGER` | The monthly amount each goal envelope (`target` + `by`) needs. |
| `fund LEDGER --from YYYY-MM --to YYYY-MM [--mode actuals\|overspend\|copy] [--envelopes REGEX] [--force]` | Print `allocate` directives to backfill or roll a range forward. |
| `fund LEDGER --mode topup [--date YYYY-MM-DD] [--reserve AMOUNT] [--strict]` | Print `allocate` directives funding envelopes up to their level, in priority order. |
| `fund LEDGER --mode fully-fund\|fund-available [--date YYYY-MM-DD] [--reserve AMOUNT] [--strict]` | Print `allocate` directives funding every envelope to its plan or its overspend, whichever is greater; `fund-available` caps the run by the pool. |
| `pin LEDGER --account REGEX [--through YYYY-MM-DD] [--write]` | Freeze the resolved envelope onto existing postings so an account can be remapped without changing history. |
| `schema [--json]` | The directive, metadata and reserved-value schema. |
| `completion SHELL` | Tab-completion snippet for bash or zsh. |

`fund` modes:

- `actuals` (default) — allocate each month's net spending, so closings equal
  openings. The backfill mode.
- `overspend` — allocate only enough to bring a negative envelope back to zero.
- `copy` — repeat the previous month's allocation (roll one month forward).
- `topup` — fund each envelope up to its level, in priority order, from the
  pool. A `monthly` envelope's level is that amount; a goal envelope's is the
  on-track balance for the run month.
- `fully-fund` — the month-level action: fund every envelope to its plan level or
  its overspend, whichever is greater, in priority order. Unlike `topup`,
  envelopes with no plan are still funded their overspend.
- `fund-available` — the same as `fully-fund`, but stop before the pool would go
  negative; anything left unfunded prints as `; UNFUNDED`.

The three range modes skip any envelope-month that already has an allocation;
`--force` overrides. `topup`, `fully-fund` and `fund-available` never skip,
because they measure from current balances — rerun them after each paycheck and
append the new output.

An envelope's plan is its `monthly`, `priority`, `target` and `by` metadata, set
on `open` or changed from a date with `plan` (`custom "envelope" "plan" "<name>"`
plus the same metadata), and cleared with `unplan`. `monthly` may be a number or
`"avgN"`, the average of the last N completed months of spending.

**`topup` needs a plan.** With no `monthly` (and no `target` + `by`) it has no
level to fund to and prints nothing. Use `fully-fund` for a whole month instead:
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

## Pitfalls

- `E002` means a non-cash account moved cash without an envelope. Give it an
  `envelope:` (or `envelope: "pool"` for taxes and pre-tax deductions).
- `E001` means a directive or posting names an envelope that was never declared.
- `W001` means the pool went negative on some day — usually allocating before the
  income that funds it lands. Run `pool LEDGER` to see the pool's ledger around
  the dip, with a running balance.
- Beancount runs `beancount.ops.pad` after every user plugin, so a plugin that
  runs earlier would miss `pad`'s synthesized transactions; the library applies
  `pad` before computing to stay in step with the report. `plugin_processing_mode
  "raw"` opts out.
- The invariant `E007` compares the pool plus envelopes against cash at each month
  end; a mismatch means a posting was missed.
- `topup` measures what is on hand at the run date; run it after the month's last
  bill and paycheck, not mid-month.
