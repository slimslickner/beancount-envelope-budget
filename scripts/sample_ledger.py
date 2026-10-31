"""Generate the all-features sample ledger.

The committed ledger at ``tests/ledgers/sample.bean`` is produced by this
script, so a new feature is demonstrated by extending the generator and
regenerating::

    uv run python scripts/sample_ledger.py

Run with ``--check`` to verify the committed file is current; CI does this, the
same way it checks the generated docs. ``--months`` and ``--per-month`` scale
the generated ledger, which the benchmark uses to time a large one.

The sample loads with no diagnostics: it is a reference for what a healthy
budget looks like, not a catalogue of errors (those have their own fixtures).
It exercises, in one ledger:

* a no-plan envelope, a ``monthly`` envelope, a ``monthly`` + ``up-to``
  envelope and an ``avgN`` envelope;
* goal envelopes that are not yet met, met then spent out of, and met, spent
  and then closed;
* an envelope opened on day one, one opened later and one closed after a couple
  of months;
* parent groups, and ``allocate`` back to the pool;
* account-, transaction- and posting-level ``envelope`` overrides, and the
  reserved ``pool`` value;
* income with its own envelope, a brokerage posting valued at cost, and
  ``unplan``;
* pre-start history, so the opening pool is non-zero.

The structural directives (accounts, then the start and declarations) come
first, and the dated activity that follows is sorted by date, so the file reads
the way a hand-kept ledger does.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import MutableSequence

#: The day tracking starts; the timeline is anchored here.
START = date(2024, 1, 1)

#: The repository root, for the default output path.
_ROOT = Path(__file__).resolve().parents[1]

#: Where the committed sample lives.
DEFAULT_OUT = _ROOT / "tests" / "ledgers" / "sample.bean"

#: How many months the committed sample spans.
DEFAULT_MONTHS = 6

#: How many ordinary purchases the committed sample adds per month.
DEFAULT_PER_MONTH = 4

#: The month offset each life-cycle event lands on, from :data:`START`.
_GYM_OPEN = 2
_MOVING_CLOSE = 2
_SUBSCRIPTIONS_UNPLAN = 2
_LAPTOP_SPEND = 3
_LAPTOP_CLOSE = 4
_VACATION_SPEND = 5

#: The last month offset, exclusive, that an envelope is funded.
_VACATION_FUND_UNTIL = 3
_LAPTOP_FUND_UNTIL = 3
_SUBSCRIPTIONS_FUND_UNTIL = 2

#: Each envelope's account. One account may map to an envelope of another name
#: (``Costco``) or to the pool (``Misc``); those are added by hand.
_ACCOUNT = {
    "Groceries": "Expenses:Groceries",
    "Rent": "Expenses:Rent",
    "Utilities": "Expenses:Utilities",
    "Dining": "Expenses:Dining",
    "Emergency": "Expenses:Emergency",
    "Vacation": "Expenses:Vacation",
    "Laptop": "Expenses:Laptop",
    "Moving": "Expenses:Moving",
    "Gym": "Expenses:Gym",
    "Subscriptions": "Expenses:Subscriptions",
    "Gifts": "Expenses:Gifts",
    "Fun": "Expenses:Fun",
    "Bonus": "Income:Bonus",
    "Investments": "Assets:Brokerage:VTI",
}

#: A dated block of ledger lines: the date to sort by, and the lines to emit.
_Block = tuple[date, list[str]]


def _month(start: date, offset: int) -> date:
    """Return the first day of the month ``offset`` months after ``start``.

    Args:
        start: Any date; only its month matters.
        offset: The signed number of months to shift.

    Returns:
        The first day of the shifted month.
    """
    index = start.year * 12 + (start.month - 1) + offset
    return date(index // 12, index % 12 + 1, 1)


def _last_day(month: date) -> date:
    """Return the last calendar day of a month.

    Args:
        month: Any date within the month.

    Returns:
        The last day of that month.
    """
    return _month(month, 1) - timedelta(days=1)


def _day(month: date, offset: int) -> date:
    """Return a day within a month, for spacing a month's transactions.

    Args:
        month: The first day of the month.
        offset: The number of days after the first.

    Returns:
        The shifted date.
    """
    return date.fromordinal(month.toordinal() + offset)


def _allocate(
    blocks: MutableSequence[_Block],
    month: date,
    envelope: str,
    amount: str,
) -> None:
    """Add an ``allocate`` directive for an envelope-month.

    Args:
        blocks: The dated blocks, extended in place.
        month: The month to fund.
        envelope: The envelope to fund.
        amount: The amount, as a Beancount number string; negative moves money
            back to the pool.
    """
    when = _last_day(month)
    blocks.append(
        (
            when,
            [
                (
                    f'{when.isoformat()} custom "envelope" "allocate" '
                    f'"{envelope}" {amount} USD'
                ),
            ],
        )
    )


def _purchase(
    blocks: MutableSequence[_Block],
    when: date,
    account: str,
    amount: str,
    *,
    meta: dict[str, str] | None = None,
) -> None:
    """Add an expense transaction.

    Args:
        blocks: The dated blocks, extended in place.
        when: The transaction date.
        account: The expense account to debit.
        amount: The amount, as a Beancount number string.
        meta: Optional transaction metadata, keyed by key, written above the
            postings (the transaction-level ``envelope`` override).
    """
    lines = [f'{when.isoformat()} * "Store"']
    for key, value in (meta or {}).items():
        lines.append(f'  {key}: "{value}"')
    lines.append(f"  {account} {amount} USD")
    lines.append(f"  Liabilities:CreditCard -{amount} USD")
    blocks.append((when, lines))


def _header() -> list[str]:
    """Return the operating-currency option and the intro comment.

    Returns:
        The header lines.
    """
    return [
        'option "operating_currency" "USD"',
        "",
        "; A hand-sized budget that shows every feature in one place. It is",
        "; generated by scripts/sample_ledger.py; regenerate it after a change.",
        "",
    ]


def _accounts() -> list[str]:
    """Return the account opens and the pre-start history.

    Returns:
        The account lines.
    """
    out = [
        "; --- Accounts ------------------------------------------------",
        "",
        "2023-12-01 open Assets:Checking USD",
        "  on-budget: TRUE",
        "2023-12-01 open Assets:Savings USD",
        "  on-budget: TRUE",
        "2023-12-01 open Liabilities:CreditCard USD",
        "  on-budget: TRUE",
        "2023-12-01 open Assets:Brokerage:Cash USD",
        "  on-budget: TRUE",
        "2023-12-01 open Assets:Brokerage:VTI VTI",
        '  envelope: "Investments"',
        "2023-12-01 open Income:Salary USD",
        "2023-12-01 open Income:Bonus USD",
        '  envelope: "Bonus"',
        "2023-12-01 open Equity:Opening-Balances USD",
    ]
    for envelope, account in _ACCOUNT.items():
        if account.startswith("Expenses:"):
            out += [f"2023-12-01 open {account} USD", f'  envelope: "{envelope}"']
    # The Costco account counts against Groceries: an account-level override.
    out += ["2023-12-01 open Expenses:Costco USD", '  envelope: "Groceries"']
    # The reserved value sends an account's effects to the pool.
    out += ["2023-12-01 open Expenses:Misc USD", '  envelope: "pool"']
    out += [
        "",
        "; --- Pre-start history: the pool opens non-zero ---------------",
        "",
        '2023-12-31 * "Opening balances"',
        "  Assets:Checking 5000.00 USD",
        "  Assets:Savings 20000.00 USD",
        "  Equity:Opening-Balances -25000.00 USD",
        "",
    ]
    return out


def _declarations() -> list[str]:
    """Return the start directive and every envelope declaration.

    Returns:
        The declaration lines.
    """
    return [
        f'{START.isoformat()} custom "envelope" "start"',
        "",
        "; --- Envelope declarations ------------------------------------",
        "",
        f'{START.isoformat()} custom "envelope" "open" "Groceries"',
        f'{START.isoformat()} custom "envelope" "open" "Dining"',
        '  monthly: "avg3"',
        f'{START.isoformat()} custom "envelope" "open" "Rent"',
        "  monthly: 2000.00",
        '  parent: "Home"',
        f'{START.isoformat()} custom "envelope" "open" "Utilities"',
        "  monthly: 200.00",
        "  up-to: 600.00",
        '  parent: "Home"',
        f'{START.isoformat()} custom "envelope" "open" "Emergency"',
        "  goal: 6000.00 USD",
        f"  by: {_last_day(_month(START, 11)).isoformat()}",
        f'{START.isoformat()} custom "envelope" "open" "Vacation"',
        "  goal: 3000.00 USD",
        f"  by: {_last_day(_month(START, 5)).isoformat()}",
        f'{START.isoformat()} custom "envelope" "open" "Laptop"',
        "  goal: 1500.00 USD",
        f"  by: {_last_day(_month(START, 4)).isoformat()}",
        f'{START.isoformat()} custom "envelope" "open" "Moving"',
        f'{START.isoformat()} custom "envelope" "open" "Subscriptions"',
        "  monthly: 30.00",
        f'{START.isoformat()} custom "envelope" "open" "Gifts"',
        f'{START.isoformat()} custom "envelope" "open" "Fun"',
        f'{START.isoformat()} custom "envelope" "open" "Bonus"',
        f'{START.isoformat()} custom "envelope" "open" "Investments"',
        "",
        "; An envelope opened partway through is hidden before its open month.",
        f'{_month(START, _GYM_OPEN).isoformat()} custom "envelope" "open" "Gym"',
        "  monthly: 50.00",
        "",
    ]


def _allocations(blocks: MutableSequence[_Block], month: date, offset: int) -> None:
    """Add a month's allocations.

    Args:
        blocks: The dated blocks, extended in place.
        month: The month to fund.
        offset: The month's offset from :data:`START`.
    """
    # The no-plan envelopes are funded by hand; the planned ones per their plan.
    for envelope, amount in (
        ("Groceries", "100.00"),
        ("Gifts", "100.00"),
        ("Fun", "100.00"),
        ("Dining", "60.00"),
        ("Rent", "2000.00"),
        ("Utilities", "200.00"),
        ("Emergency", "200.00"),
    ):
        _allocate(blocks, month, envelope, amount)
    if offset < _VACATION_FUND_UNTIL:
        _allocate(blocks, month, "Vacation", "1000.00")
    if offset < _LAPTOP_FUND_UNTIL:
        _allocate(blocks, month, "Laptop", "500.00")
    if offset < _SUBSCRIPTIONS_FUND_UNTIL:
        _allocate(blocks, month, "Subscriptions", "30.00")
    if offset >= _GYM_OPEN:
        _allocate(blocks, month, "Gym", "50.00")
    if offset == 0:
        _allocate(blocks, month, "Moving", "200.00")
        _allocate(blocks, month, "Investments", "1000.00")
    if offset == _VACATION_SPEND:
        # Emergency is trimmed back to the pool, a negative allocation.
        _allocate(blocks, month, "Emergency", "-200.00")


def _income(blocks: MutableSequence[_Block], month: date, offset: int) -> None:
    """Add a month's payroll, and once a bonus with its own envelope.

    Args:
        blocks: The dated blocks, extended in place.
        month: The month to add income to.
        offset: The month's offset from :data:`START`.
    """
    blocks.append(
        (
            _last_day(month),
            [
                f'{_last_day(month).isoformat()} * "Employer" "Payroll"',
                "  Assets:Checking 4000.00 USD",
                "  Income:Salary -4000.00 USD",
            ],
        )
    )
    if offset == 1:
        blocks.append(
            (
                month,
                [
                    f'{month.isoformat()} * "Employer" "Bonus"',
                    "  Assets:Checking 1000.00 USD",
                    "  Income:Bonus -1000.00 USD",
                ],
            )
        )


def _spending(
    blocks: MutableSequence[_Block],
    month: date,
    offset: int,
    per_month: int,
) -> None:
    """Add a month's ordinary spending.

    Args:
        blocks: The dated blocks, extended in place.
        month: The month to spend in.
        offset: The month's offset from :data:`START`.
        per_month: How many ordinary purchases to add.
    """
    envelopes = ("Groceries", "Dining", "Gifts", "Fun")
    for index in range(per_month):
        envelope = envelopes[index % len(envelopes)]
        _purchase(
            blocks, _day(month, index * 3), _ACCOUNT[envelope], f"{40 + 10 * index}.00"
        )
    _purchase(blocks, _day(month, 4), _ACCOUNT["Rent"], "2000.00")
    _purchase(blocks, _day(month, 5), _ACCOUNT["Utilities"], "150.00")
    if offset >= _GYM_OPEN:
        _purchase(blocks, _day(month, 6), _ACCOUNT["Gym"], "50.00")


def _overrides(blocks: MutableSequence[_Block], month: date) -> None:
    """Add the override and reserved-value examples.

    Args:
        blocks: The dated blocks, extended in place.
        month: The month to add them to.
    """
    _purchase(blocks, _day(month, 8), "Expenses:Costco", "30.00")
    _purchase(
        blocks,
        _day(month, 9),
        _ACCOUNT["Groceries"],
        "25.00",
        meta={"envelope": "Gifts"},
    )
    blocks.append(
        (
            _day(month, 10),
            [
                f'{_day(month, 10).isoformat()} * "Arcade"',
                f"  {_ACCOUNT['Dining']} 20.00 USD",
                '    envelope: "Fun"',
                "  Liabilities:CreditCard -20.00 USD",
            ],
        )
    )
    _purchase(blocks, _day(month, 11), "Expenses:Misc", "10.00")


def _lifecycle(blocks: MutableSequence[_Block], month: date, offset: int) -> None:
    """Add a month's life-cycle events: opens, closes and spend-outs.

    Args:
        blocks: The dated blocks, extended in place.
        month: The month the events land in.
        offset: The month's offset from :data:`START`.
    """
    if offset == 0:
        blocks.append(
            (
                month,
                [
                    f'{month.isoformat()} * "Brokerage" "Buy VTI at cost"',
                    "  Assets:Brokerage:VTI 5 VTI {200.00 USD}",
                    "  Assets:Brokerage:Cash -1000.00 USD",
                ],
            )
        )
        # Moving is spent out at once, so it closes at zero.
        _purchase(blocks, _day(month, 14), _ACCOUNT["Moving"], "200.00")
    if offset == _MOVING_CLOSE:
        blocks.append(
            (month, [f'{month.isoformat()} custom "envelope" "close" "Moving"'])
        )
    if offset == _SUBSCRIPTIONS_UNPLAN:
        blocks.append(
            (month, [f'{month.isoformat()} custom "envelope" "unplan" "Subscriptions"'])
        )
    if offset == _LAPTOP_SPEND:
        _purchase(blocks, _day(month, 12), _ACCOUNT["Laptop"], "1500.00")
    if offset == _LAPTOP_CLOSE:
        blocks.append(
            (month, [f'{month.isoformat()} custom "envelope" "close" "Laptop"'])
        )
    if offset == _VACATION_SPEND:
        # Vacation was met over three months; now it is spent out of, without
        # closing, so its progress stays met.
        _purchase(blocks, _day(month, 13), _ACCOUNT["Vacation"], "3000.00")


def build(
    months: int = DEFAULT_MONTHS,
    per_month: int = DEFAULT_PER_MONTH,
) -> str:
    """Build the sample ledger source.

    Args:
        months: How many months of activity to generate. The committed sample
            uses :data:`DEFAULT_MONTHS`, enough for every life-cycle event.
        per_month: How many ordinary purchases to add per month.

    Returns:
        The ledger source, ending in a newline.
    """
    out: list[str] = [*_header(), *_accounts(), *_declarations()]
    blocks: list[_Block] = []
    for offset in range(months):
        month = _month(START, offset)
        _allocations(blocks, month, offset)
        _income(blocks, month, offset)
        _spending(blocks, month, offset, per_month)
        _overrides(blocks, month)
        _lifecycle(blocks, month, offset)

    # A stable sort keeps a day's own order, so the file reads chronologically
    # with a heading per month.
    blocks.sort(key=lambda block: block[0])
    current: tuple[int, int] | None = None
    for when, lines in blocks:
        key = (when.year, when.month)
        if key != current:
            current = key
            out.append(
                f"; --- {when:%B %Y} -----------------------------------------------"
            )
            out.append("")
        out.extend(lines)
        out.append("")
    return "\n".join(out) + "\n"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command-line arguments.

    Args:
        argv: The arguments, or ``None`` for ``sys.argv``.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="where to write the ledger (default: the committed sample)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the file is current instead of writing it",
    )
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    parser.add_argument("--per-month", type=int, default=DEFAULT_PER_MONTH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Write the sample ledger, or check it is current.

    Args:
        argv: The arguments, or ``None`` for ``sys.argv``.

    Returns:
        The process exit status: non-zero when ``--check`` finds the file stale.
    """
    args = _parse_args(argv)
    source = build(args.months, args.per_month)
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else None
        if current != source:
            print(f"out of date: {args.out}", file=sys.stderr)
            print("run `uv run python scripts/sample_ledger.py`", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.write_text(source, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
