"""Time the loader and the envelope budget on the sample ledger.

This is a development aid and a smoke test, not a CI timing gate. It generates a
ledger from :mod:`sample_ledger` — the same feature mix as the committed sample,
scaled up — and reports the wall-clock cost of each phase:

* parsing the ledger without booking or plugins,
* loading it end to end with the plugin enabled,
* computing the budget in each collection mode (validation only vs. the full
  activity-and-hash collection the report uses).

Run it with::

    uv run python scripts/benchmark.py

Options scale the ledger and repeat the timings::

    uv run python scripts/benchmark.py --months 120 --per-month 400 --repeat 5

The generator keeps the sample and the benchmark in step: when a feature is
added to ``sample_ledger.py``, both grow it.
"""

from __future__ import annotations

import argparse
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from beancount import loader
from beancount.parser import parser
from sample_ledger import build

from beancount_envelope_budget import __version__
from beancount_envelope_budget.core import compute

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

#: The plugin line, so the benchmark measures the plugin too.
_PLUGIN = 'plugin "beancount_envelope_budget.plugin"'


@dataclass(frozen=True, slots=True)
class Measurement:
    """The phase timings for one generated ledger.

    Attributes:
        months: The months generated.
        per_month: The purchases generated per month.
        transactions: The transactions the loaded ledger holds.
        directives: The directives the loaded ledger holds.
        rows: The ``(phase, milliseconds)`` timings, in display order.
    """

    months: int
    per_month: int
    transactions: int
    directives: int
    rows: tuple[tuple[str, float], ...]


def _with_plugin(source: str) -> str:
    """Return the source with the plugin enabled, for the load phase.

    Args:
        source: The generated ledger source.

    Returns:
        The source with a ``plugin`` line after the operating-currency option.
    """
    marker = 'option "operating_currency" "USD"'
    return source.replace(marker, f"{marker}\n{_PLUGIN}", 1)


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


def measure(months: int = 12, per_month: int = 30, repeat: int = 9) -> Measurement:
    """Generate a ledger and time each phase over it.

    Args:
        months: How many months of activity to generate.
        per_month: How many purchases to generate per month.
        repeat: How many times to run each phase, keeping the fastest.

    Returns:
        The :class:`Measurement`.
    """
    source = _with_plugin(build(months, per_month))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "benchmark.bean"
        path.write_text(source, encoding="utf-8")
        entries, _errors, options = loader.load_file(path)
        rows = (
            (
                "parse (no booking/plugins)",
                _time(lambda: parser.parse_file(str(path)), repeat),
            ),
            ("load (with plugin)", _time(lambda: loader.load_file(path), repeat)),
            (
                "compute (validation only)",
                _time(lambda: compute(entries, options), repeat),
            ),
            (
                "compute (activity + hashes)",
                _time(
                    lambda: compute(
                        entries, options, collect_activity=True, collect_hashes=True
                    ),
                    repeat,
                ),
            ),
        )
    return Measurement(
        months=months,
        per_month=per_month,
        transactions=sum(1 for entry in entries if hasattr(entry, "postings")),
        directives=len(entries),
        rows=rows,
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments.

    Args:
        argv: The arguments, or ``None`` for ``sys.argv``.

    Returns:
        The parsed arguments.
    """
    parser_ = argparse.ArgumentParser(description=__doc__)
    parser_.add_argument("--months", type=int, default=12, help="months to generate")
    parser_.add_argument(
        "--per-month", type=int, default=30, help="transactions per month"
    )
    parser_.add_argument("--repeat", type=int, default=9, help="timing repeats")
    return parser_.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Build the sample at scale and print the phase timings.

    Args:
        argv: The arguments, or ``None`` for ``sys.argv``.

    Returns:
        The process exit status, always zero.
    """
    args = _parse_args(argv)
    result = measure(args.months, args.per_month, args.repeat)
    print(f"beancount-envelope-budget {__version__} benchmark")
    print(
        f"{result.months} months, {result.per_month} expenses/month "
        f"({result.transactions} transactions, {result.directives} directives)"
    )
    width = max(len(label) for label, _ in result.rows)
    for label, milliseconds in result.rows:
        print(f"{label:<{width}}  {milliseconds:8.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
