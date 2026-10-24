"""A neutral, read-only view of a budget result.

Both the CLI and the Fava report render from :func:`build_view`, so the terminal
and the web page cannot disagree about the numbers. This module is pure: no I/O
and no Fava or CLI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from beancount_envelope_budget.core import (
    POOL,
    ActivityEvent,
    BudgetResult,
    Diagnostic,
    EnvelopeReport,
    MonthRow,
    month_of,
    row_for,
)
from beancount_envelope_budget.schema import Severity

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ActivityLine",
    "BudgetView",
    "Cell",
    "EnvelopeView",
    "build_view",
    "to_jsonable",
]

#: The most activity detail lines shown for a single cell.
MAX_DETAIL = 20

#: The maximum number of months a view may span.
MAX_MONTHS = 3


@dataclass(frozen=True, slots=True)
class ActivityLine:
    """One contributing posting behind an activity cell.

    Attributes:
        date: The transaction date.
        label: A short description of the transaction.
        amount: The signed effect on the envelope.
    """

    date: date
    label: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class Cell:
    """One envelope's or the pool's figures for one month.

    Attributes:
        month: The first day of the month.
        projected: Whether the month lies beyond the computed range.
        opening: The balance carried in.
        allocated: Money assigned this month.
        activity: The summed effects of postings this month.
        closing: The balance carried out.
        detail: The contributing postings, capped at :data:`MAX_DETAIL`.
        detail_total: The full number of contributing postings.
    """

    month: date
    projected: bool
    opening: Decimal
    allocated: Decimal
    activity: Decimal
    closing: Decimal
    detail: tuple[ActivityLine, ...]
    detail_total: int


@dataclass(frozen=True, slots=True)
class EnvelopeView:
    """An envelope and its cells across the view's months.

    Attributes:
        name: The envelope's name.
        target: A goal balance, if any.
        target_currency: The currency of the target, if any.
        by: The date the target should be reached by, if any.
        cells: One :class:`Cell` per month, aligned with the view's months.
    """

    name: str
    target: Decimal | None
    target_currency: str | None
    by: date | None
    cells: tuple[Cell, ...]


@dataclass(frozen=True, slots=True)
class BudgetView:
    """The rendered-ready view of a budget over one to three months.

    Attributes:
        has_start: Whether the ledger has a start directive.
        currency: The operating currency.
        months: The months shown, ascending.
        projected: Whether each month is beyond the computed range.
        envelopes: The envelopes, in declaration order.
        pool: The pool's cells.
        cash: The cash balance cells.
        warnings: The warnings from the computation.
        errors: The errors from the computation.
        nav: Query strings for navigation links, keyed by label.
    """

    has_start: bool
    currency: str
    months: tuple[date, ...]
    projected: tuple[bool, ...]
    envelopes: tuple[EnvelopeView, ...]
    pool: tuple[Cell, ...]
    cash: tuple[Cell, ...]
    warnings: tuple[Diagnostic, ...]
    errors: tuple[Diagnostic, ...]
    nav: Mapping[str, str]


def _shift(month: date, delta: int) -> date:
    """Shift a month by a signed number of months.

    Args:
        month: Any date within the month.
        delta: The number of months to shift.

    Returns:
        The first day of the shifted month.
    """
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def build_view(
    result: BudgetResult,
    month: date | None = None,
    count: int = 1,
) -> BudgetView:
    """Build a view over one to three months.

    Args:
        result: The computed budget.
        month: The first month to show; defaults to the latest month computed.
        count: The number of months to show, clamped to ``1..MAX_MONTHS``.

    Returns:
        The :class:`BudgetView`. When tracking is not configured, it has
        ``has_start = False`` and no months.

    Examples:
        >>> from decimal import Decimal
        >>> from beancount_envelope_budget.core import BudgetResult
        >>> empty = BudgetResult(None, "USD", (), (), (), (), ())
        >>> build_view(empty).has_start
        False
    """
    count = max(1, min(count, MAX_MONTHS))
    if result.start is None or not result.months:
        return BudgetView(
            has_start=False,
            currency=result.operating_currency,
            months=(),
            projected=(),
            envelopes=(),
            pool=(),
            cash=(),
            warnings=(),
            errors=(),
            nav={},
        )

    first = result.months[0]
    start = month_of(month) if month is not None else result.months[-1]
    start = max(start, first)
    months = tuple(_shift(start, offset) for offset in range(count))
    available = set(result.months)
    projected = tuple(month not in available for month in months)
    details = _detail_index(result.activity)

    envelopes = tuple(
        _envelope_view(report, months, available, details)
        for report in result.envelopes
    )
    pool = tuple(
        _cell(
            row_for(result.pool, month),
            details.get((POOL, month), []),
            projected=month not in available,
        )
        for month in months
    )
    cash = tuple(_cash_cell(result, month, available) for month in months)
    return BudgetView(
        has_start=True,
        currency=result.operating_currency,
        months=months,
        projected=projected,
        envelopes=envelopes,
        pool=pool,
        cash=cash,
        warnings=tuple(d for d in result.diagnostics if d.severity is Severity.WARNING),
        errors=tuple(d for d in result.diagnostics if d.severity is Severity.ERROR),
        nav=_nav(start, count),
    )


def _detail_index(
    events: tuple[ActivityEvent, ...],
) -> dict[tuple[str, date], list[ActivityLine]]:
    """Group activity events by destination and month.

    Args:
        events: The per-posting activity events.

    Returns:
        A mapping from ``(destination, month)`` to its detail lines.
    """
    index: dict[tuple[str, date], list[ActivityLine]] = {}
    for event in events:
        key = (event.destination, month_of(event.date))
        index.setdefault(key, []).append(
            ActivityLine(event.date, event.label, event.amount)
        )
    return index


def _cell(
    row: MonthRow,
    detail: list[ActivityLine],
    *,
    projected: bool,
) -> Cell:
    """Build a cell from a monthly row and its detail.

    Args:
        row: The monthly row.
        detail: The contributing postings.
        projected: Whether the month is beyond the computed range.

    Returns:
        The :class:`Cell`.
    """
    return Cell(
        month=row.month,
        projected=projected,
        opening=row.opening,
        allocated=row.allocated,
        activity=row.activity,
        closing=row.closing,
        detail=tuple(detail[:MAX_DETAIL]),
        detail_total=len(detail),
    )


def _envelope_view(
    report: EnvelopeReport,
    months: tuple[date, ...],
    available: set[date],
    details: dict[tuple[str, date], list[ActivityLine]],
) -> EnvelopeView:
    """Build an envelope view.

    Args:
        report: The envelope's report.
        months: The months shown.
        available: The computed months.
        details: The activity detail index.

    Returns:
        The :class:`EnvelopeView`.
    """
    envelope = report.envelope
    cells = tuple(
        _cell(
            row_for(report.rows, month),
            details.get((report.name, month), []),
            projected=month not in available,
        )
        for month in months
    )
    return EnvelopeView(
        name=report.name,
        target=envelope.target,
        target_currency=envelope.target_currency,
        by=envelope.by,
        cells=cells,
    )


def _cash_cell(result: BudgetResult, month: date, available: set[date]) -> Cell:
    """Build the cash cell for a month.

    Args:
        result: The computed budget.
        month: The month to show.
        available: The computed months.

    Returns:
        The :class:`Cell`.
    """
    closing = _cash_balance(result, month)
    opening = _cash_balance(result, _shift(month, -1))
    return Cell(
        month=month,
        projected=month not in available,
        opening=opening,
        allocated=Decimal(0),
        activity=closing - opening,
        closing=closing,
        detail=(),
        detail_total=0,
    )


def _cash_balance(result: BudgetResult, month: date) -> Decimal:
    """Return the cash balance carried into or out of a month.

    Args:
        result: The computed budget.
        month: The month to evaluate.

    Returns:
        The balance for that month, zero before the range, or the final balance
        after it.
    """
    target = month_of(month)
    if not result.cash or target < result.months[0]:
        return Decimal(0)
    for row in result.cash:
        if row.month == target:
            return row.balance
    return result.cash[-1].balance


def to_jsonable(view: BudgetView) -> dict[str, object]:
    """Serialise a view to plain JSON-compatible data.

    Money is emitted as fixed two-decimal strings and dates as ISO strings, so
    the output is stable and free of floating-point surprises.

    Args:
        view: The view to serialise.

    Returns:
        A JSON-serialisable dictionary.

    Examples:
        >>> from beancount_envelope_budget.core import BudgetResult
        >>> to_jsonable(build_view(BudgetResult(None, "USD", (), (), (), (), ())))["has_start"]
        False
    """
    return {
        "has_start": view.has_start,
        "currency": view.currency,
        "months": [month.isoformat() for month in view.months],
        "projected": list(view.projected),
        "envelopes": [
            {
                "name": envelope.name,
                "target": _money_json(envelope.target),
                "target_currency": envelope.target_currency,
                "by": envelope.by.isoformat() if envelope.by is not None else None,
                "cells": [_cell_json(cell) for cell in envelope.cells],
            }
            for envelope in view.envelopes
        ],
        "pool": [_cell_json(cell) for cell in view.pool],
        "cash": [_cell_json(cell) for cell in view.cash],
        "warnings": [_diagnostic_json(d) for d in view.warnings],
        "errors": [_diagnostic_json(d) for d in view.errors],
        "nav": dict(view.nav),
    }


def _money_json(value: Decimal | None) -> str | None:
    """Format an optional amount for JSON.

    Args:
        value: The amount, or ``None``.

    Returns:
        A two-decimal string, or ``None``.
    """
    return None if value is None else f"{value:.2f}"


def _cell_json(cell: Cell) -> dict[str, object]:
    """Serialise a cell.

    Args:
        cell: The cell.

    Returns:
        A JSON-compatible dictionary.
    """
    return {
        "month": cell.month.isoformat(),
        "projected": cell.projected,
        "opening": f"{cell.opening:.2f}",
        "allocated": f"{cell.allocated:.2f}",
        "activity": f"{cell.activity:.2f}",
        "closing": f"{cell.closing:.2f}",
        "detail": [
            {
                "date": line.date.isoformat(),
                "label": line.label,
                "amount": f"{line.amount:.2f}",
            }
            for line in cell.detail
        ],
        "detail_total": cell.detail_total,
    }


def _diagnostic_json(diagnostic: Diagnostic) -> dict[str, object]:
    """Serialise a diagnostic.

    Args:
        diagnostic: The diagnostic.

    Returns:
        A JSON-compatible dictionary.
    """
    return {
        "code": diagnostic.code,
        "severity": diagnostic.severity.value,
        "message": diagnostic.message,
    }


def _nav(start: date, count: int) -> dict[str, str]:
    """Build navigation query strings.

    Args:
        start: The first month shown.
        count: The number of months shown.

    Returns:
        A mapping from a link label to its query string.
    """

    def query(month: date) -> str:
        return f"month={month:%Y-%m}&months={count}"

    return {
        "previous": query(_shift(start, -1)),
        "next": query(_shift(start, 1)),
        "last3": f"month={_shift(start, -2):%Y-%m}&months=3",
        "forward3": f"month={start:%Y-%m}&months=3",
    }
