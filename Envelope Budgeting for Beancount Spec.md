# Envelope Budgeting for Beancount: Spec

Oct 24, 2026 · @Tim

## Goals and non-goals

This adds envelope budgeting on top of an ordinary Beancount ledger: every dollar in your cash accounts is either assigned to an envelope or sits in an unassigned pool, and balances (positive or negative) carry from month to month.

Goals:

- Assign money to envelopes with dated `custom` directives.
- Derive spending from postings, using explicit metadata on accounts, with per-transaction and per-posting overrides.
- Keep the ledger the only source of truth. No second database.
- Validate the setup at load time, so mistakes show up on Fava's errors page.
- Provide a CLI to backfill history and to roll allocations forward.
- Provide a read-only Fava report of envelope balances.

Non-goals for the first version:

- Multiple currencies. One operating currency only.
- Replacing Fava's built-in `custom "budget"`. The two coexist and ignore each other.
- Editing allocations from the Fava UI.
- Forecasting, scheduled transactions, bank sync.

## Concepts

Six terms carry the whole model. Months are calendar months, and everything is attributed by its date.

| Term | Meaning |
| --- | --- |
| Envelope | A named bucket with a running balance, declared once with a directive. |
| Cash account | An Assets or Liabilities account whose balance counts as spendable money: checking, savings, credit cards. Marked `cash-account: TRUE` on its `open`. |
| Pool | Cash not assigned to any envelope ("to be budgeted"). |
| Allocation | A dated amount moved from the pool into an envelope. |
| Activity | The effect of postings on an envelope: spending lowers it, refunds and envelope-level income raise it. |
| Carryover | An envelope's closing balance, positive or negative, becomes next month's opening balance. |

## Ledger syntax

Everything lives in the ledger: account metadata on `open` directives, and `custom "envelope"` directives for declarations and money movement. No new parser is needed, because Beancount already accepts both.

**Account metadata.** Accounts opt in explicitly. Nothing is inherited from parent accounts.

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

The reserved value `"pool"` sends an account's postings to the pool instead of an envelope. Use it for taxes and other deductions that never reach an envelope. Income and Equity accounts need no `envelope` and flow to the pool by default.

**Directives.** One directive name, `"envelope"`, with a verb as the first argument.

| Directive | Meaning |
| --- | --- |
| `2024-01-01 custom "envelope" "start"` | Start of tracking. Earlier postings and allocations are ignored. The pool starts at the cash balance just before this date. |
| `2024-01-01 custom "envelope" "open" "Groceries"` | Declares an envelope. Optional metadata: `target: 15000.00 USD`, `by: 2028-06-01`, and for auto-fund `monthly`, `ahead` and `priority` (see the CLI section). |
| `2024-01-31 custom "envelope" "allocate" "Groceries" 600.00 USD` | Moves 600 from the pool into the envelope. Negative amounts move money back. The month is the directive's own. |
| `2024-02-15 custom "envelope" "move" "Groceries" "Gifts" 50.00 USD` | Moves money between two envelopes, e.g. to cover an overspend. |
| `2027-12-31 custom "envelope" "close" "Old"` | Retires an envelope. Its balance must be zero, and it disappears from reports afterwards. |
| `2028-01-01 custom "envelope" "plan" "Mortgage" 2600.00 USD` | Sets the planned monthly amount from that date. The latest plan on or before the run date wins. Optional metadata `ahead` and `priority` override the values on `open`. |

**Overrides.** The same `envelope` key works on a transaction or a single posting.

```beancount
2027-03-02 * "Costco" "Groceries and a birthday gift"
  Assets:Checking          -120.00 USD
  Expenses:Groceries         80.00 USD
  Expenses:Groceries         40.00 USD
    envelope: "Gifts"
```

## Resolution rules

Every transaction on or after the start date is processed in four steps. The result is a set of signed effects on envelopes and on the pool.

1. **Filter.** A transaction with no posting to a cash account is ignored, since no cash moved. Examples: an employer 401k match, a trade inside a brokerage.
2. **Classify postings.** A posting is cash if its account has `cash-account: TRUE`, otherwise non-cash.
3. **Resolve the envelope** of each non-cash posting, first match wins:
   1. `envelope` metadata on the posting.
   2. `envelope` metadata on the transaction, but only for postings whose account has an envelope. It never redirects a posting that goes to the pool.
   3. `envelope` metadata on the account's `open`.
   4. Income and Equity accounts with none of these resolve to the pool. Any other account is a validation error.
4. **Apply effects.** For a non-cash posting with signed units `u`, the resolved envelope (or the pool) changes by `-u`.

Cash-to-cash transactions are neutral, which is why a credit card payment from checking changes nothing. Spending hits its envelope when the purchase hits the card.

The sign convention covers the cases you described:

- A purchase (`Expenses +50`) lowers its envelope by 50.
- A refund (`Expenses -20`) raises it by 20.
- A transfer to a retirement account (`Assets:401k +500`, envelope Retirement) lowers Retirement by 500.
- A withdrawal from it (`Assets:401k -200`) raises Retirement by 200.
- Income with its own envelope (`Income:Bonus -1000`, envelope Bonus) raises Bonus by 1000.
- Salary with no envelope (`Income:Salary -5000`) raises the pool by 5000, and an untagged tax posting (`+1000`, `pool`) lowers it by 1000.

Payroll deferrals need one override. If `Assets:401k` has `envelope: "Retirement"`, a payroll deduction would lower Retirement without ever passing through the pool. Put `envelope: "pool"` on the 401k posting in the payroll transaction to charge the pool instead.

## Computation

For each envelope and month, the closing balance is the opening balance plus allocations plus activity. Overspending needs no special rule: a negative balance just carries forward until later allocations cover it.

```latex
closing(e, m) = closing(e, m-1) + allocated(e, m) + activity(e, m)
```

- **Opening.** The first month starts every envelope at 0. Later months start at the previous closing.
- **Allocated.** Allocations dated in the month, plus moves in, minus moves out.
- **Activity.** The summed envelope effects from the resolution rules, dated by transaction date.
- **Pool.** Pool closing = pool opening + pool effects from postings − allocations dated in the month. Moves between envelopes do not touch it.
- **Invariant.** At every month end, the pool plus all envelope balances must equal the ledger's cash balance. The pool and envelopes are built from flows, and the cash balance is read straight from the ledger, so the check is a real cross-check, not a tautology. A mismatch means a posting was missed or unsupported.

Worked example. Checking holds 1000 at the start date, so the pool opens at 1000. Salary of 3000 arrives on Jan 1, then you allocate 500 to Groceries and spend 120 there.

| Step | Pool | Groceries | Cash |
| --- | --- | --- | --- |
| Start | 1000 | 0 | 1000 |
| Salary +3000 | 4000 | 0 | 4000 |
| Allocate 500 | 3500 | 500 | 4000 |
| Spend 120 | 3500 | 380 | 3880 |

Pool plus Groceries is 3880, which matches cash. A pool that goes negative on any day means more was allocated than the cash available at that point, and is reported as a warning. Allocating before a mid-month paycheck lands is flagged this way.

## Validation

The Beancount plugin runs these checks at load time and returns them as ordinary Beancount errors, so Fava lists them on its errors page. Errors never stop the ledger from loading.

| Code | Severity | Condition |
| --- | --- | --- |
| E001 | Error | An `envelope` value, in metadata or a directive, names an envelope that was never declared. Reserved value `pool` is exempt. |
| E002 | Error | An Expenses, non-cash Assets or non-cash Liabilities account has no `envelope` metadata and posts in a cash-moving transaction. |
| E003 | Error | An envelope is declared twice, or used after it was closed. |
| E004 | Error | `cash-account` is set on an account outside Assets and Liabilities, or is not boolean `TRUE`. |
| E005 | Error | An envelope is closed with a non-zero balance. |
| E006 | Error | A cash-moving transaction contains a currency other than the operating currency. |
| E007 | Error | The invariant fails: pool plus envelopes does not equal cash at a month end. The message names the month and the difference. |
| W001 | Warning | The pool is negative on any day. Postings count before allocations dated the same day. The message gives the first date it goes negative and the lowest balance. |
| W002 | Warning | An allocation, move or `open` directive is dated before the `start` directive, so it is ignored. |
| W003 | Warning | There is no `start` directive. The plugin then does nothing. |
| E008 | Error | An envelope's monthly is neither a number nor "avg3", or its ahead is not a whole number, or its priority is not a number. |

A transaction-level `envelope` override on a transaction with no cash posting is silently ignored, since the whole transaction is skipped.

## Components

Four pieces, all depending on one core library. Only the Fava extension imports Fava, so Fava's changing internals can break at most that file.

| Component | File | Responsibility |
| --- | --- | --- |
| Core | `core.py` | Pure functions. `compute(entries, options)` returns per-month rows for every envelope, the pool, the cash balance and a list of errors. No I/O and no Fava or CLI imports. |
| Plugin | `plugin.py` | Beancount plugin. Calls the core and returns the entries unchanged plus the validation errors. Optionally writes the resolved envelope onto each posting under `envelope-resolved` (off by default). |
| CLI | `cli.py` | The `beancount-envelope-budget` command. Reads a ledger, calls the core, prints reports or generates directives. |
| Fava extension | `fava_ext.py` | Subclass of `FavaExtensionBase` with `report_title = "Envelopes"`. Recomputes in `after_load_file` and renders a Jinja template. Read-only in the first version. |

Loading it takes two lines in the ledger: `plugin "beancount_envelope_budget.plugin"` and a `custom "fava-extension"` line for the report.

Tests use pytest, with small ledger fixtures under `tests/ledgers/`, one per case in the test list below. Pin a Fava version in the project and test against it, because extension internals change between releases.

## Project and tooling

The project is one uv-managed Python package. The distribution name and the CLI command are `beancount-envelope-budget`, and the import name is `beancount_envelope_budget`. The plugin loads by module path, so it needs no entry point.

```
beancount-envelope-budget/
  pyproject.toml
  uv.lock
  .python-version
  README.md
  CHANGELOG.md
  docs/
    concepts.md
    cli.md        (generated)
    schema.md     (generated)
    errors.md     (generated)
  examples/
    household.bean
  src/beancount_envelope_budget/
    __init__.py
    schema.py
    core.py
    plugin.py
    cli.py
    fava_ext.py
    py.typed
  tests/
    ledgers/
    test_core.py
    test_plugin.py
    test_cli.py
    test_docs.py
```

Three tools manage and check it, all configured in `pyproject.toml`:

| Tool | Role | Commands |
| --- | --- | --- |
| uv | Python version, virtualenv, dependencies, lockfile, build | `uv sync`, `uv run ...`, `uv build` |
| ruff | Linting and formatting, in one tool | `uv run ruff format`, `uv run ruff check` |
| ty | Type checking | `uv run ty check` |

The strict pattern:

- **One gate.** A change is done when this passes, locally and in CI: `uv run ruff format --check && uv run ruff check && uv run ty check && uv run pytest` && uv run beancount-envelope-budget docs --check.
- **Lockfile committed.** `uv.lock` is checked in, CI installs with `uv sync --locked`, and `requires-python` plus `.python-version` pin the interpreter.
- **Ruff.** `select = ["ALL"]` with a short ignore list, each entry commented with its reason: the rules that conflict with the formatter, and relaxations that apply only under `tests/` (such as plain `assert`).
- **Types.** Every function is fully annotated. ty reports no errors, and a suppression needs a comment saying why.
- **Money is `Decimal`.** Never `float`. Core data is frozen dataclasses with slots.
- **Dependency boundaries.** The core imports only `beancount`. Fava is an optional extra (`beancount-envelope-budget[fava]`), imported only in `fava_ext.py`. Dev tools live in the `dev` dependency group, and the CLI uses stdlib `argparse` plus argcomplete for shell completion, the only runtime dependency besides Beancount.
- **Tests.** pytest, one fixture ledger per case in the test list, and a coverage floor on `core.py`.
- **Hooks.** A pre-commit hook runs the ruff checks and ty before a commit.

Beancount and Fava may ship incomplete type information, so ty may need suppressions at the boundary. Keep them in one thin adapter module that converts Beancount entries into the core's own typed records, so the rest of the code stays fully checked. This assumes Beancount 3, and ty is the newest of the three tools, so pin its version in the lockfile and expect configuration changes between releases.

## Documentation requirements

These are requirements from the first commit, enforced by the gate. They are not cleanup for later.

**Docstrings**

- Every public module, class, function and method has a Google-style docstring, with Args, Returns and Raises where they apply. Public means no leading underscore.
- Ruff's `D` rules are on with `convention = "google"`, so a missing or malformed docstring fails the lint. Tests are exempt. Enable ruff's pydoclint (`DOC`) rules as well if your ruff version has them stable.
- Examples in docstrings are doctests, run by pytest with `--doctest-modules`, so they cannot go stale.

**CLI**

- Every subcommand and flag has help text, and `--help` is the source of truth. A test walks the argparse tree and fails on any missing help.
- `docs/cli.md` is generated from that same tree.

**Schema**

- One registry in `schema.py` lists every directive verb, metadata key and reserved value, with its type, whether it is required, its default, where it applies and a one-line description. That covers `envelope`, `cash-account`, `monthly`, `ahead`, `priority`, `target`, `by` and `pool`.
- Parsing and validation read the same registry, so the documentation cannot disagree with behavior. `docs/schema.md` is generated from it, and the `schema` command prints it.
- Error and warning codes live in a second registry: code, severity, message template, and a longer explanation with the fix. A test fails if the code emits an unregistered code, or a registered code has no explanation. `docs/errors.md` is generated from it.

**Hand-written docs**

- `README.md` covers installing with uv, a five-minute quickstart, and the plugin and Fava extension lines.
- `docs/concepts.md` is this spec's concepts, resolution rules and computation, rewritten for users.
- `examples/household.bean` is a complete example ledger covering the test-case scenarios. It also runs as an integration test, so the quickstart cannot rot.
- `CHANGELOG.md` follows Keep a Changelog and is updated with every user-visible change.

**Definition of done.** A change is finished when the gate passes, including `docs --check`, and any new directive, metadata key, flag or error code has its registry entry, docstring and generated documentation.

## CLI

The CLI never edits your ledger, except for pin with --write. Commands that generate directives print them to stdout, and you redirect them into a file you include.

| Command | What it does |
| --- | --- |
| `beancount-envelope-budget check LEDGER` | Runs validation and the invariant. Exits non-zero if any error is found. |
| `beancount-envelope-budget report LEDGER [--month 2027-03]` | Prints opening, allocated, activity and closing per envelope, plus the pool and cash rows. |
| `beancount-envelope-budget fund LEDGER --from 2024-01 --to 2025-12 [--mode MODE] [--envelopes REGEX]` | Prints `allocate` directives, dated the last day of each month, for the range. |
| `beancount-envelope-budget suggest LEDGER` | For envelopes with `target` and `by`, prints the monthly amount needed to reach the target on time. |
| `beancount-envelope-budget pin LEDGER --account REGEX [--through DATE] [--write]` | Freezes the current envelope onto existing postings by writing it as posting metadata, so you can then change the account's `envelope` without altering history. Prints a diff by default. `--write` edits the files in place. Skips postings that already have `envelope` metadata, and cannot pin transactions generated by plugins or importers. |
| beancount-envelope-budget schema \[--json\] | Prints every directive, metadata key and reserved value the plugin understands, from the schema registry. --json prints it machine-readable. |
| beancount-envelope-budget docs \[--check\] | Regenerates docs/cli.md, docs/schema.md and docs/errors.md. With --check it writes nothing and exits non-zero if any of them is out of date. |
| beancount-envelope-budget completion SHELL | Prints the snippet that enables tab completion for bash, zsh or fish. |

`fund` modes:

- **`actuals`** (default). Allocation = that month's net spending, so every envelope closes each month at its opening balance. This is the backfill mode, and it leaves the pool absorbing all the surplus.
- **`overspend`**. Allocates only enough to bring a negative envelope back to zero. Envelopes with money left get nothing.
- **`copy`**. Repeats the previous month's allocation for each envelope. Run it for a single month (`--from 2027-04 --to 2027-04`) to roll the budget forward.

Rerunning the backfill modes is safe. `actuals`, `overspend` and `copy` skip any envelope and month that already has an `allocate` directive, so reruns never double-fund. A `--force` flag prints them anyway.

### Auto-fund with topup

`topup` is a fourth mode. It funds each envelope up to a level, using the pool's cash, in priority order. It is meant to be run after a paycheck lands, with `beancount-envelope-budget fund LEDGER --mode topup [--date 2027-03-01] [--reserve 500] [--strict]`. It takes a date instead of a month range.

Three inputs per envelope tell it how much to fund:

| Input | Where | Meaning |
| --- | --- | --- |
| `monthly` | `open` metadata, or a dated `plan` directive | The planned monthly amount: a number, or `"avg3"` for the average of actual spending in the last three completed months. Goal envelopes with `target` and `by` default to the `suggest` amount. |
| `ahead` | `open` or `plan` metadata | Whole months to hold in reserve beyond the current one. Default 0. |
| `priority` | `open` or `plan` metadata | Lower numbers fund first. Ties fund in declaration order. Envelopes with none fund last. |

The algorithm:

1. Read each envelope's balance and the pool balance as of `--date` (default today). Subtract `--reserve` from the pool.
2. The level of an envelope is `monthly × (1 + ahead)`, using the latest `plan` on or before the date.
3. The need is the level minus the current balance, or 0 if the balance is already at or above it.
4. Walk envelopes in priority order and allocate the smaller of the need and the pool left. The envelope that runs the pool dry gets a partial amount, and later ones get nothing.
5. Print `allocate` directives dated `--date`. Shortfalls print as comments, such as `; UNFUNDED Car: needs 400`. With `--strict` the command exits non-zero when anything is unfunded.

Rerunning `topup` is safe and overwrites nothing, because it only prints. It measures the shortfall from the ledger's current balances, so a second run after another paycheck prints just what is still missing, as extra `allocate` directives that add to the earlier ones. A run with nothing left to fund prints nothing. This is why `topup` never skips by month.

The one requirement is that the first run's output is already included in the ledger when you rerun. If it isn't, the second run sees the same shortfall and prints it again, which doubles the funding.

Limit: `topup` measures what is on hand at the run date and does not predict later spending in the month. Run it after the month's last bill and last paycheck, not mid-month.

### Shell completion

The CLI completes subcommands, flags and values in bash, zsh and fish.

- **Mechanism.** `argcomplete` on top of argparse. `cli.py` carries the `# PYTHON_ARGCOMPLETE_OK` marker and calls `argcomplete.autocomplete(parser)` before parsing. Users enable it with one line, such as `eval "$(register-python-argcomplete beancount-envelope-budget)"` for bash and zsh, and the `completion SHELL` command prints the right snippet for each shell.
- **Static values.** Subcommands, flags and the fixed choices for `--mode` (`actuals`, `overspend`, `copy`, `topup`).
- **Values from the ledger.** Envelope names for `--envelopes`, account names for `pin --account`, and `.bean` and `.beancount` files for the ledger argument. The completer loads the ledger named earlier on the command line. If there isn't one yet, or it fails to load, it returns no candidates and prints nothing.
- **Fast.** Loading a ledger on every Tab press is slow on large ledgers, so completers cache envelope and account names, keyed on the ledger files' modification times.
- **Tested.** Tests call the completer functions directly against the fixture ledgers, covering subcommands, choices, ledger-derived values, and the no-ledger and broken-ledger cases. The README and `docs/cli.md` document the setup line for each shell.

I have not checked which shells argcomplete supports in its current release, so verify bash, zsh and fish before promising all three.

Backfilling to your start date is then one command, with the output going to its own file:

```
beancount-envelope-budget fund main.bean --from 2024-01 --to 2025-12 > envelope_backfill.bean
```

## BQL recipes

BQL can report what you spent per envelope, but it cannot see allocations, because those are `custom` directives. Balances, carryover and the pool come from the CLI or the Fava report.

The effective envelope of a posting is its own metadata, falling back to the account's:

```sql
COALESCE(any_meta('envelope'), open_meta(account, 'envelope'))
```

`COALESCE` is confirmed to work in your setup. `any_meta` (posting metadata, then transaction metadata) is from memory and needs a test on the first fixture ledger.

Spending per envelope for a month:

```sql
SELECT COALESCE(any_meta('envelope'), open_meta(account, 'envelope')) AS env,
       sum(position) AS amount
WHERE account ~ '^Expenses' AND date >= 2027-03-01 AND date < 2027-04-01
GROUP BY env
```

Expense accounts with no envelope at all, which is what E002 reports:

```sql
SELECT account, sum(position)
WHERE account ~ '^Expenses'
  AND COALESCE(any_meta('envelope'), open_meta(account, 'envelope')) IS NULL
GROUP BY account
```

One known difference from the core: BQL applies a transaction-level override to every posting without its own, while the core applies it only to postings whose account already has an envelope. They only differ on transactions that mix pool postings with an override, such as payroll. Treat the CLI report as authoritative. If the BQL expression gets clumsy, the plugin can write the resolved envelope to each posting as `envelope-resolved`, and BQL then needs only `meta('envelope-resolved')`.

## Test cases

Each case below is one small fixture ledger plus an expected result. Build the core against these before touching the plugin or Fava.

| Case | Setup | Expected |
| --- | --- | --- |
| Basic | Worked example from Computation | Groceries 380, pool 3500, invariant holds |
| Credit card | Visa is a cash account. Buy 80 on Visa, then pay Visa 80 from checking | Envelope drops 80 at purchase. The payment changes nothing |
| Overspend carry | Allocate 100, spend 130 in Jan. Allocate 100, spend 0 in Feb | Jan closes at -30. Feb opens at -30 and closes at 70 |
| Posting override | Costco split: 80 Groceries, 40 with `envelope: "Gifts"` | Groceries -80, Gifts -40 |
| Transaction override | One Groceries posting of 40 with `envelope: "Gifts"` on the transaction | Gifts -40, Groceries unchanged |
| Payroll | Gross 5000, federal tax 1000 (`pool`), 401k 500 with posting override `pool`, checking +3500 | Pool +3500, Retirement unchanged |
| Retirement transfer | Checking to `Assets:401k` 500, then 401k to checking 200 | Retirement -500, then +200 |
| Envelope income | `Income:Bonus` with `envelope: "Bonus"`, 1000 | Bonus +1000, pool unchanged |
| Savings goal | Envelope Car with allocations only, `target` 15000 by 2028-06-01 | Balance accumulates. `suggest` prints the monthly need |
| Cash-only move | Checking to savings, both cash accounts | No envelope or pool change |
| Pre-start history | Postings and allocations before `start` | Ignored. W002 for the allocations. Pool starts at the prior cash balance |
| Bad ledger | Unenveloped expense, undeclared envelope, close with balance, foreign currency | E002, E001, E005 and E006 respectively |
| Backfill modes | Two years of fixture spending | `actuals` leaves every closing equal to its opening. `overspend` leaves none negative. `copy` repeats the prior month |
| Mid-month dip | Pool is 0 on the 1st. Allocate 500 that day, salary of 3000 arrives on the 15th | W001 naming the 1st and a lowest balance of -500. No warning at month end |
| Topup level | Mortgage with monthly 2500 and ahead 2. Balance 5000, enough pool | Allocates 2500 (level 7500 minus balance 5000) |
| Topup shortage | Pool 3000. Mortgage needs 2500 (priority 1), Groceries needs 620 (priority 2), Car needs 400 (priority 3) | Mortgage 2500, Groceries 500, a comment that Groceries is short 120 and Car is unfunded. --strict exits non-zero |
| Topup rerun | Run after paycheck one and include the output. Add paycheck two and run again. Run a third time | The second run prints only the remaining shortfall. The third run prints nothing |
| Topup plan change | A plan directive raises Mortgage to 2600 from 2028-01-01 | Runs dated before 2028-01-01 use 2500. Runs on or after use 2600 |
| Topup avg3 | Groceries monthly is "avg3" with spending of 500, 540 and 520 in the last three completed months | Monthly resolves to 520 |

Acceptance for the first release:

- All cases pass in pytest.
- `beancount-envelope-budget check` on your real ledger exits 0 after a `fund` backfill from 2024-01.
- The invariant holds for every month in that range.
- The pool at the end of 2025 equals your cash accounts minus the sum of envelope balances.

## Open questions

None of these block the core. They need an answer before the plugin or the Fava report ships.

- [x] Does `any_meta` exist in your beanquery version? If not, use the `envelope-resolved` option.
- [ ] The Fava docs I read do not say how an extension's templates and JS are served. Check Fava's bundled extensions before writing `fava_ext.py`.
- [ ] To remap an account, first run pin to freeze its old envelope onto existing postings (see CLI), then change the account's metadata. A dated `custom "envelope" "map"` directive is the alternative, but pinning needs no new syntax.
- [ ] Plugin load time on a multi-year ledger. Measure it on your real ledger once the core runs.
- [ ] Daily pool warnings will be noisy if you allocate on the 1st but are paid mid-month. Decide whether to add a setting for month-end-only checking.

Out of scope for now: multiple currencies, editing allocations in Fava, mobile access, forecasting, and migrating data from `fava-envelope`.
