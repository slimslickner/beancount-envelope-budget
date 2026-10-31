"""Time the loader and the envelope budget on a synthetic year of transactions.

This is a development aid, not part of the test suite or the CI gate. It builds
a ledger with one month of plans and a configurable number of monthly
household transactions, then reports the wall-clock cost of each phase:

* parsing the ledger without booking or plugins,
* loading it end to end with the plugin enabled,
* computing the budget in each collection mode (validation only vs. the full
  activity-and-hash collection the report uses).

Run it with::

    uv run python scripts/benchmark.py

Options let you scale the ledger and repeat the timings::

    uv run python scripts/benchmark.py --months 24 --per-month 40 --repeat 15
"""

from __future__ import annotations

import argparse
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from beancount import loader
from beancount.parser import parser

from beancount_envelope_budget import __version__
from beancount_envelope_budget.core import compute

if TYPE_CHECKING:
    from collections.abc import Callable

_ENVELOPES = ("Groceries", "Dining", "Utilities", "Transport", "Health", "Fun")
_MONTH_LENGTH = 30


def build_ledger(months: int, per_month: int) -> str:
    """Build a synthetic ledger: a year of a household budget.

    Args:
        months: How many months of transactions to generate.
        per_month: How many expense transactions to generate per month.

    Returns:
        The ledger source, with the plugin enabled and one month of plans.
    """
    lines = [
        'option "operating_currency" "USD"',
        'plugin "beancount_envelope_budget.plugin"',
        "",
    ]
    lines += [
        "2020-01-01 open Assets:Checking USD",
        "  cash-account: TRUE",
        "2020-01-01 open Liabilities:CreditCard:Visa USD",
        "  cash-account: TRUE",
        "2020-01-01 open Income:Salary USD",
        "2020-01-01 open Equity:Opening-Balances USD",
    ]
    for envelope in _ENVELOPES:
        lines += [
            f"2020-01-01 open Expenses:{envelope} USD",
            f'  envelope: "{envelope}"',
        ]
    lines += [
        "",
        '2023-12-31 * "Opening"',
        "  Assets:Checking 4000.00 USD",
        "  Equity:Opening-Balances -4000.00 USD",
        "",
        '2024-01-01 custom "envelope" "start"',
    ]
    for envelope in _ENVELOPES:
        lines.append(f'2024-01-01 custom "envelope" "open" "{envelope}"')
        lines.append(f'2024-01-01 custom "envelope" "plan" "{envelope}"')
        lines.append("  monthly: 200.00")
    lines.append("")

    day = date(2024, 1, 1)
    for _ in range(months):
        for index in range(per_month):
            when = day + timedelta(days=index % _MONTH_LENGTH)
            envelope = _ENVELOPES[index % len(_ENVELOPES)]
            amount = 20 + index
            lines += [
                f'{when.isoformat()} * "Store" "Purchase"',
                f"  Expenses:{envelope} {amount}.00 USD",
                f"  Liabilities:CreditCard:Visa -{amount}.00 USD",
            ]
        lines += [
            f'{day.isoformat()} * "Employer" "Payroll"',
            "  Assets:Checking 3500.00 USD",
            "  Income:Salary -3500.00 USD",
            "",
        ]
        day = (day + timedelta(days=_MONTH_LENGTH + 1)).replace(day=1)
    return "\n".join(lines) + "\n"


def _time(call: Callable[[], object], repeat: int) -> float:
    """Return the best wall-clock milliseconds over several repeats.

    Args:
        call: The callable to time.
        repeat: How many times to run it.

    Returns:
        The fastest run's duration, in milliseconds.
    """
    timings: list[float] = []
    for _ in range(repeat):
        start = time.perf_counter()
        call()
        timings.append((time.perf_counter() - start) * 1000)
    return min(timings)


def main() -> None:
    """Build a synthetic ledger and print the phase timings."""
    args = _parse_args()
    source = build_ledger(args.months, args.per_month)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "benchmark.bean"
        path.write_text(source, encoding="utf-8")

        entries, _errors, options = loader.load_file(path)
        transactions = sum(1 for entry in entries if hasattr(entry, "postings"))

        rows = [
            (
                "parse (no booking/plugins)",
                _time(lambda: parser.parse_file(str(path)), args.repeat),
            ),
            ("load (with plugin)", _time(lambda: loader.load_file(path), args.repeat)),
            (
                "compute (validation only)",
                _time(lambda: compute(entries, options), args.repeat),
            ),
            (
                "compute (activity + hashes)",
                _time(
                    lambda: compute(
                        entries, options, collect_activity=True, collect_hashes=True
                    ),
                    args.repeat,
                ),
            ),
        ]

    print(f"beancount-envelope-budget {__version__} benchmark")
    print(
        f"{args.months} months, {args.per_month} expenses/month "
        f"({transactions} transactions, {len(entries)} directives)"
    )
    width = max(len(label) for label, _ in rows)
    for label, milliseconds in rows:
        print(f"{label:<{width}}  {milliseconds:8.1f} ms")


def _parse_args() -> argparse.Namespace:
    """Parse the command-line arguments.

    Returns:
        The parsed arguments.
    """
    parser_ = argparse.ArgumentParser(description=__doc__)
    parser_.add_argument("--months", type=int, default=12, help="months to generate")
    parser_.add_argument(
        "--per-month", type=int, default=30, help="transactions per month"
    )
    parser_.add_argument("--repeat", type=int, default=9, help="timing repeats")
    return parser_.parse_args()


if __name__ == "__main__":
    main()
