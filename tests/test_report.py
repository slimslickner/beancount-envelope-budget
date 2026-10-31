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
    ReportEntry,
    _top_entries,
    build_reports,
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
    return compute(
        entries, options, collect_activity=collect_activity, collect_hashes=True
    )


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


def test_pool_ledger_nets_each_transaction() -> None:
    """A transaction's pool postings net into one line, so payroll is not a dip."""
    result = _compute("payroll.bean")
    ledger = pool_ledger(result)
    pool_lines = [e for e in ledger.entries if e.kind == POOL_ACTIVITY]
    assert [e.amount for e in pool_lines] == [D("3500")]
    assert ledger.low == D("3500")
    assert "W001" not in [d.code for d in result.diagnostics]


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


def test_reports_balances_row_spans_every_month() -> None:
    """The first report row is the cash and pool balance, per month."""
    reports = build_reports(_compute("parent_groups.bean"))
    assert reports.has_start is True
    assert reports.currency == "USD"
    assert [row.months[0].month for row in reports.rows] == [date(2024, 1, 1)] * 4
    balance = reports.rows[0]
    assert balance.title == "Cash and pool"
    assert [(e.name, e.amount) for e in balance.months[0].entries] == [
        ("Cash", D("3030")),
        ("Pool", D("2300")),
    ]


def test_reports_row_titles() -> None:
    """The rows are the balance, then the three top-ten rankings."""
    reports = build_reports(_compute("parent_groups.bean"))
    assert [row.title for row in reports.rows] == [
        "Cash and pool",
        "Top spending",
        "Top allocated",
        "Top ending balance",
    ]


def test_reports_column_widths() -> None:
    """The report exposes the longest name and amount, for uniform columns."""
    reports = build_reports(_compute("parent_groups.bean"))
    assert reports.name_chars == 9
    assert reports.amount_chars == len("3030.00")
    empty = build_reports(_compute("no_start.bean"))
    assert empty.name_chars == 0
    assert empty.amount_chars == 0


def test_reports_rankings_order_and_filter() -> None:
    """Each ranking is envelope-level, ordered high to low, positive only."""
    reports = build_reports(_compute("parent_groups.bean"))
    _balance, spending, allocated, closing = reports.rows
    assert [e.name for e in spending.months[0].entries] == ["Rent", "Groceries", "Car"]
    assert [e.name for e in allocated.months[0].entries] == [
        "Rent",
        "Groceries",
        "Car",
        "Misc",
    ]
    # Rent closes at zero, so it is not a "top ending balance".
    assert [e.name for e in closing.months[0].entries] == [
        "Groceries",
        "Car",
        "Misc",
    ]
    assert closing.months[0].entries[0].amount == D("380")


def test_reports_without_start() -> None:
    """A ledger with no start has an empty reports view."""
    reports = build_reports(_compute("no_start.bean"))
    assert reports.has_start is False
    assert reports.rows == ()


def test_reports_filter_to_a_month_range() -> None:
    """A start/end range narrows the months shown."""
    reports = build_reports(
        _compute("fund_backfill.bean"),
        start=date(2024, 2, 1),
        end=date(2024, 2, 28),
    )
    assert [month.month for month in reports.rows[0].months] == [date(2024, 2, 1)]


def test_reports_balances_stay_cumulative_when_filtered() -> None:
    """A narrowed range still shows balances from the whole history.

    The pool holds money that earlier months moved through, so a balance
    recomputed from only the visible month would be wrong; it must be drawn
    from the whole budget.
    """
    reports = build_reports(
        _compute("fund_backfill.bean"),
        start=date(2024, 2, 1),
        end=date(2024, 2, 28),
    )
    balance = reports.rows[0].months[0]
    assert [(entry.name, entry.amount) for entry in balance.entries] == [
        ("Cash", D("4750")),
        ("Pool", D("5000")),
    ]


def test_reports_empty_range_has_no_rows() -> None:
    """A range with no computed months leaves the rows empty but started."""
    reports = build_reports(_compute("fund_backfill.bean"), start=date(2030, 1, 1))
    assert reports.has_start is True
    assert reports.rows == ()


def test_top_entries_caps_and_breaks_ties() -> None:
    """Ranked entries cap at TOP_N and break ties by name."""
    entries = [ReportEntry(f"env{index:02d}", D(1)) for index in range(12)]
    top = _top_entries(entries)
    assert len(top) == 10
    assert [entry.name for entry in top] == [f"env{index:02d}" for index in range(10)]
