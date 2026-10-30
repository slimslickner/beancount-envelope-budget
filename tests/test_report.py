"""Tests for the shared report view models, especially the pool ledger."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import cast

from beancount import loader

from beancount_envelope_budget.core import BudgetResult, compute
from beancount_envelope_budget.report import (
    POOL_ACTIVITY,
    POOL_ALLOCATION,
    build_view,
    pool_ledger,
    pool_ledger_json,
)

D = Decimal

_LEDGERS = Path(__file__).parent / "ledgers"


def _compute(name: str, *, collect_activity: bool = True) -> BudgetResult:
    """Compute the budget for a fixture ledger.

    Args:
        name: The fixture file name.
        collect_activity: Whether to collect per-posting activity detail.

    Returns:
        The computed :class:`BudgetResult`.
    """
    entries, _errors, options = loader.load_file(_LEDGERS / name)
    return compute(entries, options, collect_activity=collect_activity)


def test_pool_ledger_matches_the_warning() -> None:
    """The ledger's lines, order and low match W001, and it reports the opening."""
    result = _compute("mid_month_dip.bean")
    ledger = pool_ledger(result)
    assert ledger.opening == D("0")
    assert [(e.date, e.kind, e.amount, e.balance) for e in ledger.entries] == [
        (date(2024, 1, 1), POOL_ALLOCATION, D("-500"), D("-500")),
        (date(2024, 1, 15), POOL_ACTIVITY, D("3000"), D("2500")),
    ]
    warning = next(d for d in result.diagnostics if d.code == "W001")
    assert f"{ledger.low_date}" in warning.message
    assert f"{ledger.low:.2f}" in warning.message
    assert "opened that day at 0.00 USD" in warning.message


def test_pool_ledger_window_carries_opening() -> None:
    """A window starts at the pool balance carried in, and filters the lines."""
    result = _compute("mid_month_dip.bean")
    ledger = pool_ledger(result, start=date(2024, 1, 15), end=date(2024, 1, 31))
    assert ledger.opening == D("-500")
    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    assert entry.kind == POOL_ACTIVITY
    assert entry.balance == D("2500")


def test_pool_ledger_by_day() -> None:
    """A single-day window opens at the balance before that day."""
    result = _compute("mid_month_dip.bean")
    ledger = pool_ledger(result, start=date(2024, 1, 1), end=date(2024, 1, 1))
    assert ledger.opening == D("0")
    assert [e.kind for e in ledger.entries] == [POOL_ALLOCATION]
    assert ledger.low == D("-500")


def test_pool_ledger_includes_rounding() -> None:
    """A folded rounding residual appears as a pool activity line."""
    result = _compute("rounding_residual.bean")
    ledger = pool_ledger(result)
    pool_lines = [e for e in ledger.entries if e.kind == POOL_ACTIVITY]
    assert any(e.amount == D("0.01") for e in pool_lines)


def test_pool_ledger_without_start() -> None:
    """A ledger with no start has an empty pool ledger."""
    result = _compute("no_start.bean")
    ledger = pool_ledger(result)
    assert ledger.entries == ()
    assert ledger.low is None
    assert ledger.low_date is None


def test_pool_ledger_json_is_stable() -> None:
    """The JSON serialisation uses fixed money strings and ISO dates."""
    result = _compute("mid_month_dip.bean")
    payload = pool_ledger_json(pool_ledger(result))
    assert payload["opening"] == "0.00"
    assert payload["low"] == "-500.00"
    assert payload["low_date"] == "2024-01-01"
    entries = cast("list[dict[str, object]]", payload["entries"])
    assert entries[0]["kind"] == POOL_ALLOCATION
    assert entries[0]["envelope"] == "Groceries"
    assert entries[0]["amount"] == "-500.00"


def test_build_view_pool_cell_carries_ledger() -> None:
    """The pool cell for a month carries that month's ledger lines."""
    result = _compute("mid_month_dip.bean")
    view = build_view(result, date(2024, 1, 1))
    assert [e.kind for e in view.pool[0].pool] == [POOL_ALLOCATION, POOL_ACTIVITY]
    assert view.pool[0].pool[0].balance == D("-500")
