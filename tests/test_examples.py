"""The example ledger runs as an integration test, so the quickstart cannot rot."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from beancount import loader

from beancount_envelope_budget.core import compute

D = Decimal

_EXAMPLE = Path(__file__).parents[1] / "examples" / "household.bean"


def test_household_example() -> None:
    """The example loads cleanly, computes without diagnostics and balances."""
    entries, errors, options = loader.load_file(_EXAMPLE)
    assert not errors, [error.message for error in errors]

    result = compute(entries, options)
    assert result.diagnostics == ()
    for pool_row, cash_row in zip(result.pool, result.cash, strict=True):
        total = pool_row.closing + sum(
            (report.closing_at(pool_row.month) for report in result.envelopes),
            D(0),
        )
        assert total == cash_row.balance

    car = result.envelope("Car")
    assert car is not None
    assert car.closing == D("500")
