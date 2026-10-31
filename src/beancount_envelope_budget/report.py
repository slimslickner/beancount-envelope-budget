"""A neutral, read-only view of a budget result.

Both the CLI and the Fava report render from :func:`build_view`, so the terminal
and the web page cannot disagree about the numbers. This module is pure: no I/O
and no Fava or CLI imports. The one exception is :func:`today_month`, which reads
the wall clock to pick a default month — a display concern that the CLI and the
Fava report share from here rather than each reimplement.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from beancount_envelope_budget.core import (
    POOL,
    ActivityEvent,
    AllocationEvent,
    BudgetResult,
    Diagnostic,
    Envelope,
    EnvelopeReport,
    MonthRow,
    Plan,
    entry_hash,
    month_of,
    row_for,
    row_index,
)
from beancount_envelope_budget.schema import Severity

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "TOP_N",
    "ActivityLine",
    "AllocationLine",
    "BudgetView",
    "Cell",
    "EnvelopeGroup",
    "EnvelopeView",
    "FundingExplanation",
    "MonthReport",
    "PlanLine",
    "PoolEntry",
    "PoolLedger",
    "ReportEntry",
    "ReportRow",
    "Reports",
    "ViewIndex",
    "active_plan",
    "build_reports",
    "build_view",
    "envelope_need",
    "funding_explanation",
    "funding_explanation_lines",
    "month_funding",
    "overspend_cover",
    "plan_level",
    "plan_top_up",
    "pool_entry_text",
    "pool_ledger",
    "pool_ledger_json",
    "prepare_view",
    "to_jsonable",
    "today_month",
]

#: The most activity detail lines shown for a single cell.
MAX_DETAIL = 20

#: The maximum number of months a view may span.
MAX_MONTHS = 3

#: How many envelopes a report ranking lists for each month.
TOP_N = 10

#: The quantum every funding level is rounded to, matching the whole cents the
#: ledger holds and the report displays.
_CENT = Decimal("0.01")


def _round_to_cent(value: Decimal) -> Decimal:
    """Round a funding level to the money the ledger displays.

    A goal's level is ``goal * elapsed / total``, which can carry a sub-cent
    tail (``1000 / 7 = 142.857...``). Balances are whole cents, so comparing the
    raw level with a balance reports a spurious over- or under-funding — the CLI
    can even print an excess of ``0.00``. Rounding the level to the cent keeps
    every comparison at one precision.

    Args:
        value: The resolved level.

    Returns:
        The level rounded to the cent.
    """
    return value.quantize(_CENT)


def today_month() -> date:
    """Return the first day of the current month.

    The current month is a wall-clock, display concern, so it is resolved here
    rather than inside the pure :func:`build_view`. The CLI and the Fava report
    both call it to choose the month to show by default, so a future-dated
    directive cannot drag the default window into the future.

    Returns:
        The first day of the local current month.

    Examples:
        >>> today_month().day
        1
    """
    return date.today().replace(day=1)  # noqa: DTZ011 - local today is intended


#: The kind of a pool-ledger line: pool activity, or an allocation leaving it.
POOL_ACTIVITY = "activity"
POOL_ALLOCATION = "allocation"


@dataclass(frozen=True, slots=True)
class PoolEntry:
    """One line in the pool's ledger.

    The pool's balance moves for two reasons: pool activity (income, spending
    charged to the pool, a security's gain, a folded rounding residual) and
    allocations leaving it for an envelope. Both appear here in date order, with
    the balance carried after each line, so the pool's lows are visible.

    Attributes:
        date: The line's date.
        kind: :data:`POOL_ACTIVITY` or :data:`POOL_ALLOCATION`.
        amount: The signed effect on the pool.
        balance: The running pool balance after this line.
        entry_hash: The Beancount hash of the source transaction or directive,
            for linking back to it. It is the same value Beancount's
            ``compare.hash_entry`` yields.
        payee: The transaction's payee, for an activity line.
        transaction_narration: The transaction's narration, for an activity line.
        posting_narration: The posting's ``narration`` metadata, for an activity
            line.
        envelope: The destination envelope, for an allocation line.
    """

    date: date
    kind: str
    amount: Decimal
    balance: Decimal
    entry_hash: str
    payee: str | None = None
    transaction_narration: str | None = None
    posting_narration: str | None = None
    envelope: str | None = None


@dataclass(frozen=True, slots=True)
class PoolLedger:
    """The pool's per-transaction ledger over a window.

    Attributes:
        opening: The pool balance carried into the window.
        entries: The pool's lines within the window, in date order, each
            carrying the running balance from :attr:`opening`.
    """

    opening: Decimal
    entries: tuple[PoolEntry, ...]

    @property
    def low(self) -> Decimal | None:
        """The lowest running balance in the window, or ``None`` when empty.

        Examples:
            >>> from decimal import Decimal
            >>> PoolLedger(
            ...     Decimal(0),
            ...     (
            ...         PoolEntry(date(2024, 1, 2), POOL_ACTIVITY, Decimal(5), Decimal(5), ""),
            ...         PoolEntry(
            ...             date(2024, 1, 3), POOL_ALLOCATION, Decimal(-8), Decimal(-3), ""
            ...         ),
            ...     ),
            ... ).low
            Decimal('-3')
        """
        return min((entry.balance for entry in self.entries), default=None)

    @property
    def low_date(self) -> date | None:
        """The date of the lowest running balance, or ``None`` when empty."""
        if not self.entries:
            return None
        return min(self.entries, key=lambda entry: entry.balance).date


@dataclass(frozen=True, slots=True)
class ActivityLine:
    """One contributing posting behind an activity cell.

    Attributes:
        date: The transaction date.
        amount: The signed effect on the envelope.
        entry_hash: The Beancount hash of the transaction, for linking back to
            it in Fava.
        payee: The transaction's payee, if any.
        transaction_narration: The transaction's narration, if any.
        posting_narration: The posting's own ``narration`` metadata, if any —
            the ``autobean.narration`` convention for describing a line item.
    """

    date: date
    amount: Decimal
    entry_hash: str
    payee: str | None = None
    transaction_narration: str | None = None
    posting_narration: str | None = None


@dataclass(frozen=True, slots=True)
class AllocationLine:
    """One ``allocate`` directive behind a cell's allocated figure.

    Attributes:
        date: The directive's date.
        amount: The amount allocated.
        entry_hash: The Beancount hash of the directive, for linking back to it
            in Fava.
    """

    date: date
    amount: Decimal
    entry_hash: str


@dataclass(frozen=True, slots=True)
class PlanLine:
    """One plan directive for an envelope.

    An envelope's plan is a base, set on its ``open`` directive, plus dated
    overrides from ``plan`` (and clearing ``unplan``) directives. This records
    each so the Fava report can list them all and link back to their source.

    Attributes:
        date: The directive's date.
        entry_hash: The Beancount hash of the directive, for linking back to it
            in Fava.
        monthly: The planned monthly amount, a number or ``"avgN"``, or ``None``.
        priority: The funding priority, lower first, or ``None``.
        goal: The goal total, or ``None``.
        goal_currency: The currency of the goal, or ``None``.
        by: The date the goal should be reached by, or ``None``.
        up_to: A balance ceiling paired with ``monthly``, or ``None``.
        clears: Whether this is an ``unplan`` that resets the plan to defaults.
        base: Whether this is the envelope's ``open`` directive.
    """

    date: date
    entry_hash: str
    monthly: Decimal | str | None = None
    priority: Decimal | None = None
    goal: Decimal | None = None
    goal_currency: str | None = None
    by: date | None = None
    up_to: Decimal | None = None
    clears: bool = False
    base: bool = False


#: How each funding status reads in an explanation.
_FUNDING_TEXT = {"under": "underfunded", "on": "on plan", "over": "over plan"}


@dataclass(frozen=True, slots=True)
class FundingExplanation:
    """Why an envelope reads under, on or over its plan in a month.

    The report computes an envelope's funding level, the amount credited toward
    it, and the top-up still needed, and classifies it. This records the
    arithmetic behind those figures — the resolved ``monthly`` and its samples,
    the ``up-to`` ceiling, or the goal schedule — so the CLI and Fava can show
    *how* a plan was evaluated, not just the verdict.

    Attributes:
        reference: What the plan is measured against: ``"monthly"``,
            ``"up-to"`` or ``"goal"``.
        level: The figure the envelope should reach this month, or ``None``.
        funded: The amount credited toward the plan.
        top_up: The amount ``fund`` or the prefill would still allocate, never
            negative.
        funding: ``"under"``, ``"on"`` or ``"over"`` the plan, or ``None``.
        opening: The month's opening balance, or ``None``.
        allocated: The money assigned this month, or ``None``.
        closing: The month's closing balance, or ``None``.
        monthly: The resolved ``monthly`` amount — a number, or an ``avgN``
            average — or ``None``.
        up_to: The ``up-to`` balance ceiling, or ``None``.
        goal: The goal total, or ``None``.
        by: The goal deadline, or ``None``.
        months_elapsed: The goal months counted so far, or ``None``.
        months_total: The goal months in total, or ``None``.
        average_months: The ``(month, spending)`` samples an ``avgN`` monthly
            amount averaged, in date order; empty for a plain ``monthly``.
    """

    reference: str
    level: Decimal | None
    funded: Decimal
    top_up: Decimal
    funding: str | None = None
    opening: Decimal | None = None
    allocated: Decimal | None = None
    closing: Decimal | None = None
    monthly: Decimal | None = None
    up_to: Decimal | None = None
    goal: Decimal | None = None
    by: date | None = None
    months_elapsed: int | None = None
    months_total: int | None = None
    average_months: tuple[tuple[date, Decimal], ...] = ()


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
        visible: Whether the row should be shown this month. It is ``False``
            outside the envelope's life — before it opens or after it closes —
            so only the months between ``open`` and ``close`` are shown.
        level: The balance the envelope should hold this month under its plan,
            or ``None`` when it has no funding plan.
        funding: Whether the envelope is ``"under"``, ``"on"`` or ``"over"`` its
            level this month, or ``None`` when there is no level.
        goal: The envelope's goal total this month, or ``None`` when it has
            no goal.
        up_to: The envelope's balance ceiling this month, or ``None`` when it has
            no ``up-to``.
        fill: How full the envelope is toward its goal, clamped to ``0`` to
            ``1`` — the amount credited toward the plan this month (see
            :meth:`~beancount_envelope_budget.core.EnvelopeReport.funded_at`)
            divided by its ``goal``, or by its ``level`` when it has no goal.
            ``None`` when it has neither.
        allocations: The ``allocate`` directives dated in this month, each
            linking back to its source directive.
        pool: For the pool row, the pool's ledger lines in this month, each on
            its own date with the running balance; empty for envelopes.
        explanation: The arithmetic behind the month's plan verdict — its level,
            funded amount and top-up — or ``None`` when the envelope has no
            plan.
    """

    month: date
    projected: bool
    opening: Decimal
    allocated: Decimal
    activity: Decimal
    closing: Decimal
    detail: tuple[ActivityLine, ...]
    detail_total: int
    visible: bool = True
    level: Decimal | None = None
    funding: str | None = None
    goal: Decimal | None = None
    up_to: Decimal | None = None
    fill: Decimal | None = None
    allocations: tuple[AllocationLine, ...] = ()
    pool: tuple[PoolEntry, ...] = ()
    explanation: FundingExplanation | None = None


@dataclass(frozen=True, slots=True)
class EnvelopeView:
    """An envelope and its cells across the view's months.

    Attributes:
        name: The envelope's name.
        plan: The envelope's effective plan as of the view's first month.
        cells: One :class:`Cell` per month, aligned with the view's months.
        parent: The envelope's grouping label, or ``None``.
        plans: Every plan directive for the envelope — its ``open`` base first,
            then each dated ``plan``/``unplan`` change in order — for the Fava
            plan popover.
    """

    name: str
    plan: Plan
    cells: tuple[Cell, ...]
    parent: str | None = None
    plans: tuple[PlanLine, ...] = ()

    @property
    def visible(self) -> bool:
        """Whether the envelope is shown in any month of the view.

        A unified table gives each envelope one row spanning the whole window,
        so the row exists when the envelope is visible in at least one of the
        view's months — an envelope opened partway through still spans it.
        The individual :class:`Cell` flags then blank the months outside its
        life.

        Examples:
            >>> cell = Cell(
            ...     month=date(2024, 1, 1),
            ...     projected=False,
            ...     opening=Decimal(0),
            ...     allocated=Decimal(0),
            ...     activity=Decimal(0),
            ...     closing=Decimal(0),
            ...     detail=(),
            ...     detail_total=0,
            ...     visible=False,
            ... )
            >>> EnvelopeView("Groceries", Plan(), (cell,)).visible
            False
        """
        return any(cell.visible for cell in self.cells)


@dataclass(frozen=True, slots=True)
class EnvelopeGroup:
    """Envelopes sharing a parent category, with their combined subtotal.

    Attributes:
        parent: The grouping label, or ``None`` for envelopes with no parent.
        envelopes: The member envelopes, in alphabetical order.
        subtotal: One summed :class:`Cell` per month, aligned with the view's
            months. Its detail is always empty.
    """

    parent: str | None
    envelopes: tuple[EnvelopeView, ...]
    subtotal: tuple[Cell, ...]

    @property
    def visible(self) -> bool:
        """Whether the group is shown in any month of the view.

        A named group keeps its heading row while any member is visible
        somewhere in the window; its :class:`Cell` flags then blank the months
        where no member is open.

        Examples:
            >>> EnvelopeGroup(None, (), ()).visible
            False
        """
        return any(cell.visible for cell in self.subtotal)


@dataclass(frozen=True, slots=True)
class BudgetView:
    """The rendered-ready view of a budget over one to three months.

    Attributes:
        has_start: Whether the ledger has a start directive.
        currency: The operating currency.
        months: The months shown, ascending.
        projected: Whether each month is beyond the computed range.
        envelopes: The envelopes, in alphabetical order.
        groups: The envelopes grouped by parent category, with the parent
            headings in alphabetical order. Envelopes with no parent form a
            headingless group placed last.
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
    groups: tuple[EnvelopeGroup, ...]
    pool: tuple[Cell, ...]
    cash: tuple[Cell, ...]
    warnings: tuple[Diagnostic, ...]
    errors: tuple[Diagnostic, ...]
    nav: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReportEntry:
    """One name and amount in a month's report column.

    Attributes:
        name: The entry's label — an envelope name, or ``Cash``/``Pool``.
        amount: The figure for the month.
    """

    name: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class MonthReport:
    """One month's column of a report row.

    Attributes:
        month: The first day of the month.
        entries: The entries shown for the month, in display order.
    """

    month: date
    entries: tuple[ReportEntry, ...]


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One row of the report: a small table per month, side by side.

    Attributes:
        title: The row's label, such as ``Top spending`` or ``Cash and pool``.
        months: One :class:`MonthReport` per report month, ascending.
    """

    title: str
    months: tuple[MonthReport, ...]


@dataclass(frozen=True, slots=True)
class Reports:
    """A full-history, table-only view of a budget, for the Fava report.

    Where :class:`BudgetView` is a ledger-style grid over one to three months,
    the reports view spans every computed month as rows of month columns: the
    cash and pool balances first, then the per-month top spending, allocated and
    ending balance. It is data only; the Fava report renders it as tables.

    Attributes:
        has_start: Whether the ledger has a start directive.
        currency: The operating currency.
        rows: The report rows, in display order.
        name_chars: The length of the longest entry name across the rows, so
            every table's name column can be the same width.
        amount_chars: The length of the longest formatted amount across the
            rows, so every table's amount column can fit it.
    """

    has_start: bool
    currency: str
    rows: tuple[ReportRow, ...]
    name_chars: int = 0
    amount_chars: int = 0


#: The titles of the report's ranking rows, in display order.
RANKING_TITLES = ("Top spending", "Top allocated", "Top ending balance")

#: The title of the cash-and-pool balance row.
BALANCE_TITLE = "Cash and pool"


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


def _last_day(month: date) -> date:
    """Return the last calendar day of a month.

    Args:
        month: Any date within the month.

    Returns:
        The last day of that month.
    """
    return _shift(month, 1) - timedelta(days=1)


def _maybe_row(rows: tuple[MonthRow, ...], month: date) -> MonthRow | None:
    """Return a monthly row for a month, or ``None`` when out of range.

    Args:
        rows: The rows to search.
        month: The month to find.

    Returns:
        The matching row, or ``None``.
    """
    index = row_index(rows, month)
    return None if index is None else rows[index]


def _avg_window(monthly: str) -> int:
    """Return the window size of an ``avgN`` monthly amount.

    Args:
        monthly: An ``avgN`` string, already validated by the core.

    Returns:
        The window size, ``N``.

    Examples:
        >>> _avg_window("avg6")
        6
    """
    return int(monthly[3:])


def _average_samples(
    report: EnvelopeReport, on: date, window: int
) -> tuple[tuple[date, Decimal], ...]:
    """Collect an envelope's spending over the last ``window`` completed months.

    Each sample is a completed month and the spending charged to the envelope in
    it, oldest first, so an ``avgN`` plan's arithmetic can be shown as well as
    evaluated.

    Args:
        report: The envelope's report.
        on: The date to evaluate.
        window: How many completed months to collect.

    Returns:
        The ``(month, spending)`` samples in date order, omitting months with no
        row.
    """
    anchor = month_of(on)
    samples: list[tuple[date, Decimal]] = []
    for offset in range(1, window + 1):
        row = _maybe_row(report.rows, _shift(anchor, -offset))
        if row is None:
            continue
        samples.append((row.month, -row.activity))
    samples.reverse()
    return tuple(samples)


def _average_spend(report: EnvelopeReport, on: date, window: int) -> Decimal | None:
    """Average an envelope's spending over the last ``window`` completed months.

    Args:
        report: The envelope's report.
        on: The date to evaluate.
        window: How many completed months to average.

    Returns:
        The average spending, or ``None`` when there is no history.
    """
    samples = _average_samples(report, on, window)
    if not samples:
        return None
    return sum((spend for _month, spend in samples), Decimal(0)) / len(samples)


def _monthly_amount(report: EnvelopeReport, on: date) -> Decimal | None:
    """Resolve an envelope's planned monthly amount on a date.

    Args:
        report: The envelope's report.
        on: The date to evaluate.

    Returns:
        The monthly amount — a number, or the average of the last ``avgN``
        completed months of spending, rounded to the cent — or ``None`` when the
        envelope has no ``monthly`` plan (or an ``avgN`` with no history).
    """
    plan = report.envelope.effective(on)
    if isinstance(plan.monthly, Decimal):
        return _round_to_cent(plan.monthly)
    if isinstance(plan.monthly, str):
        average = _average_spend(report, on, _avg_window(plan.monthly))
        return None if average is None else _round_to_cent(average)
    return None


def plan_level(report: EnvelopeReport, on: date) -> Decimal | None:
    """Resolve an envelope's funding level on a date.

    An envelope with a ``monthly`` plan holds that amount (a number, or the
    average of its last ``avgN`` completed months of spending). An envelope with
    an ``up-to`` ceiling holds ``min(up_to, opening + monthly)`` — the balance it
    should reach this month, contributing the month's amount but never climbing
    past the ceiling. A goal envelope with a ``goal`` holds the on-track
    contribution level from
    :meth:`~beancount_envelope_budget.core.Envelope.funding_level`, compared
    against its cumulative contributions (see
    :meth:`~beancount_envelope_budget.core.EnvelopeReport.funded_at`) rather than
    its balance. The resolved level is rounded to the cent, because a goal's
    prorated total can carry a sub-cent tail that a whole-cent figure would
    otherwise read as over- or under-funded.

    Args:
        report: The envelope's report.
        on: The date to evaluate.

    Returns:
        The figure the envelope should reach, or ``None`` when it has no plan.

    Examples:
        >>> from decimal import Decimal
        >>> from beancount_envelope_budget.core import Envelope, EnvelopeReport, Plan
        >>> goal = EnvelopeReport(
        ...     Envelope(
        ...         "Emergency",
        ...         date(2026, 1, 1),
        ...         None,
        ...         Plan(
        ...             goal=Decimal("6000"),
        ...             goal_currency="USD",
        ...             by=date(2026, 12, 31),
        ...         ),
        ...         (),
        ...     ),
        ...     (),
        ... )
        >>> plan_level(goal, date(2026, 6, 15))
        Decimal('3000.00')
    """
    plan = report.envelope.effective(on)
    if plan.up_to is not None:
        monthly = _monthly_amount(report, on)
        if monthly is None:
            return _round_to_cent(plan.up_to)
        opening = row_for(report.rows, on).opening
        return _round_to_cent(min(plan.up_to, opening + monthly))
    monthly = _monthly_amount(report, on)
    if monthly is not None:
        return monthly
    level = report.envelope.funding_level(on)
    return None if level is None else _round_to_cent(level)


def active_plan(plans: tuple[PlanLine, ...], month: date) -> PlanLine | None:
    """Return the plan directive in force in a month.

    An envelope's plan is a base, set on its ``open`` directive, plus dated
    ``plan``/``unplan`` overrides. This picks the one that governs a month — the
    latest dated on or before the month's last day — matching the date the rest
    of the view evaluates an envelope's plan at, so the directive shown agrees
    with the funding level beside it.

    Args:
        plans: The envelope's plan directives, base first.
        month: Any date within the month.

    Returns:
        The governing :class:`PlanLine`, or ``None`` when the plan has never
        been set.

    Examples:
        >>> from decimal import Decimal
        >>> plans = (
        ...     PlanLine(date(2024, 1, 1), "base", monthly=Decimal("100"), base=True),
        ...     PlanLine(date(2024, 6, 1), "change", priority=Decimal("1")),
        ... )
        >>> active_plan(plans, date(2024, 1, 1)).entry_hash
        'base'
        >>> active_plan(plans, date(2024, 6, 15)).entry_hash
        'change'
        >>> active_plan(plans, date(2023, 12, 1)) is None
        True
    """
    on = _last_day(month)
    active: PlanLine | None = None
    for line in plans:
        if line.date <= on and (active is None or line.date >= active.date):
            active = line
    return active


def plan_top_up(report: EnvelopeReport, on: date) -> Decimal:
    """Amount to allocate to fund an envelope to its plan level.

    This is the "set the month's allocation to the plan" intent. For a plain
    envelope it is the gap between the envelope's funding level and the amount
    already credited toward the plan (see
    :meth:`~beancount_envelope_budget.core.EnvelopeReport.funded_at`) — cumulative
    contributions for a goal envelope. For an
    envelope with an ``up-to`` ceiling, ``monthly`` is a per-month contribution
    allowance: the amount is what is left of that allowance after this month's
    allocations, capped by how far the closing balance still sits below the
    ceiling, so once the envelope is full the amount is zero and spending only
    re-opens room within the allowance. An envelope with no plan has no level, so
    it needs nothing here; use :func:`overspend_cover` for its spending.

    Args:
        report: The envelope's report.
        on: A date in the month to evaluate.

    Returns:
        The amount to allocate, never negative; zero when the plan is met.

    Examples:
        >>> row = MonthRow(date(2026, 10, 1), Decimal(0), Decimal(0), Decimal(0))
        >>> report = EnvelopeReport(
        ...     Envelope(
        ...         "Groceries",
        ...         date(2026, 10, 1),
        ...         None,
        ...         Plan(monthly=Decimal("550")),
        ...         (),
        ...     ),
        ...     (row,),
        ... )
        >>> plan_top_up(report, date(2026, 10, 31))
        Decimal('550.00')
        >>> capped = EnvelopeReport(
        ...     Envelope(
        ...         "Car",
        ...         date(2026, 10, 1),
        ...         None,
        ...         Plan(monthly=Decimal("250"), up_to=Decimal("600")),
        ...         (),
        ...     ),
        ...     (MonthRow(date(2026, 10, 1), Decimal("500"), Decimal(0), Decimal(0)),),
        ... )
        >>> plan_top_up(capped, date(2026, 10, 31))
        Decimal('100.00')
    """
    plan = report.envelope.effective(on)
    if plan.up_to is not None:
        monthly = _monthly_amount(report, on)
        if monthly is None:
            return Decimal(0)
        row = row_for(report.rows, on)
        allowance_left = max(Decimal(0), monthly - row.allocated)
        room = max(Decimal(0), plan.up_to - row.closing)
        return _round_to_cent(min(allowance_left, room))
    level = plan_level(report, on)
    if level is None:
        return Decimal(0)
    return max(Decimal(0), level - report.funded_at(on))


def overspend_cover(report: EnvelopeReport, on: date) -> Decimal:
    """Amount to allocate to bring a negative closing balance to zero.

    This is the "cover overspending on the balance" intent: how far the month
    closed below zero. An envelope that ends the month positive — or that has no
    spending — needs nothing.

    Args:
        report: The envelope's report.
        on: A date in the month to evaluate.

    Returns:
        The amount to allocate, never negative.

    Examples:
        >>> row = MonthRow(
        ...     date(2026, 10, 1), Decimal("0.00"), Decimal("50.00"), Decimal("-100.00")
        ... )
        >>> report = EnvelopeReport(
        ...     Envelope("Groceries", date(2026, 10, 1), None, Plan(), ()),
        ...     (row,),
        ... )
        >>> overspend_cover(report, date(2026, 10, 31))
        Decimal('50.00')
    """
    return max(Decimal(0), -row_for(report.rows, on).closing)


def envelope_need(report: EnvelopeReport, on: date) -> Decimal:
    """Amount to allocate to fully fund one envelope for a month.

    The greater of its plan top-up (:func:`plan_top_up`) and its overspend cover
    (:func:`overspend_cover`), so funding it satisfies both the plan and a
    negative balance. This backs the "fully fund" month action; the per-envelope
    "add allocation" form offers the two intents separately instead.

    Args:
        report: The envelope's report.
        on: A date in the month to evaluate.

    Returns:
        The amount to allocate, never negative.

    Examples:
        >>> row = MonthRow(date(2026, 10, 1), Decimal(0), Decimal(0), Decimal(-100))
        >>> report = EnvelopeReport(
        ...     Envelope(
        ...         "Groceries",
        ...         date(2026, 10, 1),
        ...         None,
        ...         Plan(monthly=Decimal("550")),
        ...         (),
        ...     ),
        ...     (row,),
        ... )
        >>> envelope_need(report, date(2026, 10, 31))
        Decimal('550.00')
    """
    return max(plan_top_up(report, on), overspend_cover(report, on))


def funding_explanation(
    report: EnvelopeReport,
    on: date,
) -> FundingExplanation | None:
    """Explain how an envelope's plan is evaluated in a month.

    Gathers the arithmetic behind the month's verdict: the resolved
    ``monthly`` amount (and the spending an ``avgN`` averaged), the ``up-to``
    ceiling or the goal schedule, the funding level, the amount credited toward
    it, and the top-up still needed. Pure, so the CLI and Fava render one
    explanation. Returns ``None`` when the envelope has no plan.

    Args:
        report: The envelope's report.
        on: A date in the month to evaluate.

    Returns:
        The :class:`FundingExplanation`, or ``None`` when the envelope has no
        ``monthly``, ``up-to`` or ``goal`` plan.

    Examples:
        >>> row = MonthRow(date(2026, 10, 1), Decimal(0), Decimal(300), Decimal("-100"))
        >>> report = EnvelopeReport(
        ...     Envelope(
        ...         "Groceries",
        ...         date(2026, 10, 1),
        ...         None,
        ...         Plan(monthly=Decimal("550")),
        ...         (),
        ...     ),
        ...     (row,),
        ... )
        >>> explanation = funding_explanation(report, date(2026, 10, 31))
        >>> explanation.reference, explanation.funded, explanation.top_up
        ('monthly', Decimal('300'), Decimal('250.00'))
    """
    plan = report.envelope.effective(on)
    if plan.up_to is not None:
        reference = "up-to"
    elif plan.monthly is not None:
        reference = "monthly"
    elif plan.goal is not None:
        reference = "goal"
    else:
        return None
    row = row_for(report.rows, on)
    level = plan_level(report, on)
    funded = report.funded_at(on)
    top_up = plan_top_up(report, on)
    if reference == "up-to":
        funding = "on" if top_up == 0 else "under"
    else:
        funding = _funding(level, funded)
    schedule = report.envelope.goal_schedule(on) if reference == "goal" else None
    return FundingExplanation(
        reference=reference,
        level=level,
        funded=funded,
        top_up=top_up,
        funding=funding,
        opening=row.opening,
        allocated=row.allocated,
        closing=row.closing,
        monthly=_monthly_amount(report, on),
        up_to=plan.up_to,
        goal=plan.goal,
        by=plan.by,
        months_elapsed=schedule.elapsed if schedule is not None else None,
        months_total=schedule.total if schedule is not None else None,
        average_months=(
            _average_samples(report, on, _avg_window(plan.monthly))
            if isinstance(plan.monthly, str)
            else ()
        ),
    )


def funding_explanation_lines(
    explanation: FundingExplanation,
    currency: str = "",
) -> tuple[str, ...]:
    """Render a funding explanation as labelled derivation lines.

    The lines spell out the level's formula, what the funded amount is measured
    from, whether the envelope is under, on or over plan, and the top-up the
    plan still needs. Shared by the CLI's ``explain`` command and the Fava
    closing popover, so the two cannot disagree.

    Args:
        explanation: The explanation to render.
        currency: The operating currency, appended to each amount when given.

    Returns:
        The derivation lines.

    Examples:
        >>> from decimal import Decimal
        >>> explanation = FundingExplanation(
        ...     reference="monthly",
        ...     level=Decimal("550"),
        ...     funded=Decimal("500"),
        ...     top_up=Decimal("50"),
        ...     funding="under",
        ...     opening=Decimal(0),
        ...     allocated=Decimal("500"),
        ...     monthly=Decimal("550"),
        ... )
        >>> funding_explanation_lines(explanation)
        ('monthly 550.00 = plan', 'level 550.00 = monthly 550.00', 'funded 500.00 = opening 0.00 + allocated 500.00', 'underfunded by 50.00', 'top up 50.00')
    """

    def money(value: Decimal | None) -> str:
        suffix = f" {currency}" if currency else ""
        return "-" if value is None else f"{value:.2f}{suffix}"

    lines: list[str] = []
    if explanation.reference == "goal":
        window = ""
        if explanation.months_elapsed is not None and explanation.months_total:
            window = (
                f" ({explanation.months_elapsed}/{explanation.months_total} months)"
            )
        deadline = f" to {explanation.by}" if explanation.by is not None else ""
        lines.append(
            f"level {money(explanation.level)} = goal {money(explanation.goal)}"
            f"{window}{deadline}"
        )
        lines.append(f"funded {money(explanation.funded)} = cumulative contributions")
    elif explanation.reference == "up-to":
        lines.append(
            f"level {money(explanation.level)} = min(ceiling "
            f"{money(explanation.up_to)}, opening {money(explanation.opening)}"
            f" + monthly {money(explanation.monthly)})"
        )
        lines.append(f"funded {money(explanation.funded)} = closing balance")
    else:
        if explanation.average_months:
            samples = ", ".join(
                f"{month:%Y-%m} {spend:.2f}"
                for month, spend in explanation.average_months
            )
            lines.append(f"monthly {money(explanation.monthly)} = average of {samples}")
        else:
            lines.append(f"monthly {money(explanation.monthly)} = plan")
        lines.append(
            f"level {money(explanation.level)} = monthly {money(explanation.monthly)}"
        )
        lines.append(
            f"funded {money(explanation.funded)} = opening "
            f"{money(explanation.opening)} + allocated {money(explanation.allocated)}"
        )

    if explanation.funding is not None:
        label = _FUNDING_TEXT[explanation.funding]
        difference = (
            None
            if explanation.level is None
            else explanation.funded - explanation.level
        )
        if explanation.funding == "under" and difference is not None:
            lines.append(f"{label} by {money(-difference)}")
        elif explanation.funding == "over" and difference is not None:
            lines.append(f"{label} by {money(difference)}")
        else:
            lines.append(label)

    if explanation.top_up > 0:
        lines.append(f"top up {money(explanation.top_up)}")
    return tuple(lines)


def month_funding(
    result: BudgetResult,
    on: date,
    *,
    available: bool = False,
    reserve: Decimal = Decimal(0),
) -> tuple[list[tuple[str, Decimal]], list[tuple[str, Decimal]]]:
    """Plan a month's funding across every envelope.

    Backs the month-level "fully fund" and "fund with available" actions. Each
    envelope's amount is its :func:`envelope_need`, so a planned envelope is
    funded to its plan and an overspent one is covered. Envelopes are ordered by
    plan ``priority`` (lower first, unplanned last), which matters when the pool
    caps the run.

    Args:
        result: The computed budget.
        on: A date in the month to fund.
        available: Whether to cap the run by the pool, leaving it no lower than
            ``reserve``. When ``False``, every envelope is funded in full.
        reserve: Cash to keep unallocated in the pool, for ``available``.

    Returns:
        The allocations ``(name, amount)``, and — only when ``available`` — the
        shortfalls ``(name, amount)`` that the pool could not cover.

    Examples:
        >>> pool = (MonthRow(date(2026, 1, 1), Decimal(0), Decimal(0), Decimal("250.00")),)
        >>> groceries = EnvelopeReport(
        ...     Envelope(
        ...         "Groceries",
        ...         date(2026, 1, 1),
        ...         None,
        ...         Plan(monthly=Decimal("300")),
        ...         (),
        ...     ),
        ...     (MonthRow(date(2026, 1, 1), Decimal(0), Decimal(0), Decimal(0)),),
        ... )
        >>> result = BudgetResult(
        ...     date(2026, 1, 1),
        ...     "USD",
        ...     (date(2026, 1, 1),),
        ...     (groceries,),
        ...     pool,
        ...     (),
        ...     (),
        ... )
        >>> month_funding(result, date(2026, 1, 31))
        ([('Groceries', Decimal('300.00'))], [])
        >>> month_funding(result, date(2026, 1, 31), available=True)
        ([('Groceries', Decimal('250.00'))], [('Groceries', Decimal('50.00'))])
    """
    remaining = row_for(result.pool, month_of(on)).closing - reserve
    candidates: list[tuple[bool, Decimal, int, str, Decimal]] = []
    for index, report in enumerate(result.envelopes):
        need = envelope_need(report, on)
        if need <= 0:
            continue
        plan = report.envelope.effective(on)
        rank = plan.priority if plan.priority is not None else Decimal(0)
        candidates.append((plan.priority is None, rank, index, report.name, need))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))

    allocations: list[tuple[str, Decimal]] = []
    unfunded: list[tuple[str, Decimal]] = []
    for _unset, _rank, _index, name, need in candidates:
        if not available:
            allocations.append((name, need))
            continue
        if remaining <= 0:
            unfunded.append((name, need))
            continue
        amount = min(need, remaining)
        remaining -= amount
        allocations.append((name, amount))
        if amount < need:
            unfunded.append((name, need - amount))
    return allocations, unfunded


def _funding(level: Decimal | None, funded: Decimal) -> str | None:
    """Classify an envelope's funded amount against its funding level.

    Args:
        level: The month's funding level, or ``None``.
        funded: The amount credited toward the plan, from
            :meth:`~beancount_envelope_budget.core.EnvelopeReport.funded_at`.

    Returns:
        ``"under"``, ``"on"`` or ``"over"``, or ``None`` when there is no level.
    """
    if level is None:
        return None
    if funded < level:
        return "under"
    if funded > level:
        return "over"
    return "on"


def _fill(reference: Decimal | None, funded: Decimal) -> Decimal | None:
    """Return how full an envelope is toward a reference figure.

    Args:
        reference: The figure to measure against, or ``None``.
        funded: The amount credited toward the plan, from
            :meth:`~beancount_envelope_budget.core.EnvelopeReport.funded_at`.

    Returns:
        ``funded`` divided by ``reference``, clamped to ``0`` to ``1``, or
        ``None`` when there is no positive reference.

    Examples:
        >>> _fill(Decimal("200"), Decimal("50"))
        Decimal('0.25')
        >>> _fill(Decimal("200"), Decimal("500"))
        Decimal('1')
        >>> _fill(Decimal("0"), Decimal("50")) is None
        True
        >>> _fill(None, Decimal("50")) is None
        True
    """
    if reference is None or reference <= 0:
        return None
    return min(max(funded / reference, Decimal(0)), Decimal(1))


def _sort_key(name: str) -> str:
    """Return the sort key for an envelope or parent name.

    Names sort alphabetically, case-insensitively. A leading number is compared
    as text, not as a value, so a zero-padded prefix (``01``, ``02`` … ``10``)
    controls the order while a bare ``2`` would sort after ``10``.

    Args:
        name: The envelope or parent name.

    Returns:
        The key to sort by.

    Examples:
        >>> sorted(("Groceries", "car", "Rent"), key=_sort_key)
        ['car', 'Groceries', 'Rent']
    """
    return name.casefold()


def pool_ledger(
    result: BudgetResult,
    *,
    start: date | None = None,
    end: date | None = None,
) -> PoolLedger:
    """Build the pool's ledger of transactions and allocations, in a date window.

    The pool's balance moves for two reasons, and both are included: each
    transaction's net pooled effect (income, spending charged to the pool, a
    security's gain; a folded rounding residual is its own line) and allocations
    leaving the pool for an envelope. A transaction is one line, so its internal
    posting order never reads as a dip. Lines are ordered by date, with a day's
    activity before its allocations, so the running balance walks the same path
    the ``W001`` pool-floor warning measures. Each line carries the balance after
    it, so the pool's lows are visible.

    ``result`` must have been computed with ``collect_activity=True``, or the
    pool's activity lines are absent and only allocations remain.

    Args:
        result: The computed budget.
        start: The first date to include, or ``None`` for the beginning.
        end: The last date to include, or ``None`` for the end.

    Returns:
        The :class:`PoolLedger`; the opening balance is the pool balance carried
        into the window, and each entry keeps its absolute running balance.
    """
    if result.start is None:
        return PoolLedger(result.pre_start_cash, ())
    # Each line as plain fields, so the running balance is filled in one pass
    # without rebuilding a PoolEntry per line.
    staged: list[
        tuple[date, str, Decimal, str, str | None, str | None, str | None, str | None]
    ] = [
        (
            event.date,
            POOL_ACTIVITY,
            event.amount,
            entry_hash(event.entry) if event.entry is not None else "",
            event.payee,
            event.transaction_narration,
            event.posting_narration,
            None,
        )
        for event in result.activity
        if event.destination == POOL
    ]
    staged += [
        (
            event.date,
            POOL_ALLOCATION,
            -event.amount,
            entry_hash(event.entry) if event.entry is not None else "",
            None,
            None,
            None,
            event.envelope,
        )
        for event in result.allocations
    ]
    # Sort by date and kind, keeping each list's own order within a day; that
    # puts a day's activity before its allocations, as the warning does.
    staged.sort(key=lambda item: (item[0], item[1]))

    balance = result.pre_start_cash
    built: list[PoolEntry] = []
    for when, kind, amount, digest, payee, txn, post, envelope in staged:
        balance += amount
        built.append(
            PoolEntry(
                date=when,
                kind=kind,
                amount=amount,
                balance=balance,
                entry_hash=digest,
                payee=payee,
                transaction_narration=txn,
                posting_narration=post,
                envelope=envelope,
            )
        )

    opening = result.pre_start_cash
    if start is not None:
        opening += sum(
            (entry.amount for entry in built if entry.date < start), Decimal(0)
        )
        built = [entry for entry in built if entry.date >= start]
    if end is not None:
        built = [entry for entry in built if entry.date <= end]
    return PoolLedger(opening, tuple(built))


def pool_entry_text(entry: PoolEntry) -> str:
    """Describe one pool-ledger line, for the CLI and the Fava popover.

    Args:
        entry: The pool entry.

    Returns:
        ``allocate <envelope>`` for an allocation, else ``payee - narration``
        (preferring the posting's narration), or ``(no description)``.

    Examples:
        >>> pool_entry_text(
        ...     PoolEntry(
        ...         date(2024, 1, 5),
        ...         POOL_ALLOCATION,
        ...         Decimal("-500"),
        ...         Decimal("-500"),
        ...         "",
        ...         envelope="Groceries",
        ...     )
        ... )
        'allocate Groceries'
    """
    if entry.kind == POOL_ALLOCATION:
        return f"allocate {entry.envelope}" if entry.envelope else "allocation"
    narration = entry.posting_narration or entry.transaction_narration
    parts = [part for part in (entry.payee, narration) if part]
    return " - ".join(parts) or "(no description)"


def pool_ledger_json(ledger: PoolLedger) -> dict[str, object]:
    """Serialise a pool ledger to plain JSON-compatible data.

    Args:
        ledger: The pool ledger.

    Returns:
        A JSON-serialisable dictionary with fixed two-decimal money strings and
        ISO dates.
    """
    return {
        "opening": f"{ledger.opening:.2f}",
        "entries": [_pool_entry_json(entry) for entry in ledger.entries],
        "low": _money_json(ledger.low),
        "low_date": ledger.low_date.isoformat() if ledger.low_date else None,
    }


@dataclass(frozen=True, slots=True)
class ViewIndex:
    """The history-wide lookups a view slices, built once per ledger load.

    :func:`build_view` derives these from the whole budget on every call — the
    activity detail grouped by envelope and month, the allocations likewise, and
    the pool's full ledger. A caller that renders the view repeatedly for one
    loaded ledger, such as the Fava report, builds a :class:`ViewIndex` once with
    :func:`prepare_view` and passes it back, so a request only slices the months
    it shows.

    Attributes:
        details: The activity events, by ``(destination, month)``.
        allocations: The allocation events, by ``(envelope, month)``.
        ledger: The pool's full ledger.
    """

    details: Mapping[tuple[str, date], list[ActivityEvent]]
    allocations: Mapping[tuple[str, date], list[AllocationEvent]]
    ledger: PoolLedger


def prepare_view(result: BudgetResult) -> ViewIndex:
    """Build the lookups :func:`build_view` slices a view from.

    Args:
        result: The computed budget.

    Returns:
        The :class:`ViewIndex`, ready to pass to :func:`build_view`.
    """
    return ViewIndex(
        details=_detail_index(result.activity),
        allocations=_allocation_index(result.allocations),
        ledger=pool_ledger(result),
    )


def build_view(
    result: BudgetResult,
    month: date | None = None,
    count: int = 1,
    *,
    index: ViewIndex | None = None,
) -> BudgetView:
    """Build a view over one to three months.

    Args:
        result: The computed budget.
        month: The first month to show; defaults to the latest month computed.
            Callers that want the current month — the CLI and the Fava report —
            pass :func:`today_month`, so a future-dated directive does not move
            the default window forward.
        count: The number of months to show, clamped to ``1..MAX_MONTHS``.
        index: The precomputed :func:`prepare_view`, or ``None`` to build one
            here. Pass one to avoid rebuilding the history-wide lookups on every
            call.

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
            groups=(),
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
    if index is None:
        index = prepare_view(result)
    details = index.details
    allocations = index.allocations
    ledger = index.ledger

    envelopes = tuple(
        _envelope_view(report, months, available, details, allocations)
        for report in sorted(
            result.envelopes, key=lambda report: _sort_key(report.name)
        )
    )
    pool = tuple(
        _cell(
            row_for(result.pool, month),
            details.get((POOL, month), []),
            projected=month not in available,
            pool=_pool_entries(ledger, month),
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
        groups=_group(envelopes),
        pool=pool,
        cash=cash,
        warnings=tuple(d for d in result.diagnostics if d.severity is Severity.WARNING),
        errors=tuple(d for d in result.diagnostics if d.severity is Severity.ERROR),
        nav=_nav(start, count),
    )


def build_reports(
    result: BudgetResult,
    *,
    start: date | None = None,
    end: date | None = None,
) -> Reports:
    """Build a table-only reports view of a budget over a month range.

    Unlike :func:`build_view`, which spans one to three months for the ledger
    grid, this spans a run of computed months as report rows of month columns:
    the cash and pool balances, then the per-month top spending, allocated and
    ending-balance envelopes. It is pure and data only, so the Fava report and
    any future caller share the same numbers.

    ``start`` and ``end`` narrow the months shown, for the Fava window's date
    filter. They select whole months: a month is kept when its first day is on
    or after ``start`` and on or before ``end``. The cash and pool balances are
    cumulative, so they are still computed from the whole budget and only
    *displayed* for the months in range, never recomputed from a truncated
    history. The CLI, which has no window, passes neither and spans full
    history.

    Args:
        result: The computed budget.
        start: The first month to show, or ``None`` for the beginning.
        end: The last month to show, or ``None`` for the end.

    Returns:
        The :class:`Reports`. When tracking is not configured it has
        ``has_start = False`` and no rows; when the range excludes every month
        it has ``has_start = True`` and no rows.

    Examples:
        >>> from beancount_envelope_budget.core import BudgetResult
        >>> empty = BudgetResult(None, "USD", (), (), (), (), ())
        >>> build_reports(empty).has_start
        False
    """
    if result.start is None or not result.months:
        return Reports(
            has_start=False,
            currency=result.operating_currency,
            rows=(),
        )
    months = tuple(
        month
        for month in result.months
        if (start is None or month >= start) and (end is None or month <= end)
    )
    if not months:
        return Reports(
            has_start=True,
            currency=result.operating_currency,
            rows=(),
        )
    rows = (_balance_row(result, months), *_ranking_rows(result, months))
    return Reports(
        has_start=True,
        currency=result.operating_currency,
        rows=rows,
        name_chars=_report_name_chars(rows),
        amount_chars=_report_amount_chars(rows),
    )


def _balance_row(result: BudgetResult, months: tuple[date, ...]) -> ReportRow:
    """Build the cash-and-pool balance row.

    Each month's column carries the cash balance and the pool balance.

    Args:
        result: The computed budget.
        months: The months to show.

    Returns:
        The :class:`ReportRow`.
    """
    return ReportRow(
        BALANCE_TITLE,
        tuple(
            MonthReport(
                month,
                (
                    ReportEntry("Cash", _cash_balance(result, month)),
                    ReportEntry("Pool", row_for(result.pool, month).closing),
                ),
            )
            for month in months
        ),
    )


def _ranking_rows(
    result: BudgetResult,
    months: tuple[date, ...],
) -> tuple[ReportRow, ...]:
    """Rank envelopes by spending, allocation and ending balance each month.

    Only envelopes open in a month are ranked, and only positive figures — an
    envelope that did not spend, was not allocated to, or closed non-positive is
    omitted.

    Args:
        result: The computed budget.
        months: The months to rank.

    Returns:
        The spending, allocation and ending-balance :class:`ReportRow`s.
    """
    spend: list[MonthReport] = []
    allocated: list[MonthReport] = []
    closing: list[MonthReport] = []
    for index, month in enumerate(months):
        spend_entries: list[ReportEntry] = []
        allocated_entries: list[ReportEntry] = []
        closing_entries: list[ReportEntry] = []
        for report in result.envelopes:
            if not _open_in(report.envelope, month):
                continue
            row = report.rows[index]
            if -row.activity > 0:
                spend_entries.append(ReportEntry(report.name, -row.activity))
            if row.allocated > 0:
                allocated_entries.append(ReportEntry(report.name, row.allocated))
            if row.closing > 0:
                closing_entries.append(ReportEntry(report.name, row.closing))
        spend.append(MonthReport(month, _top_entries(spend_entries)))
        allocated.append(MonthReport(month, _top_entries(allocated_entries)))
        closing.append(MonthReport(month, _top_entries(closing_entries)))
    return (
        ReportRow(RANKING_TITLES[0], tuple(spend)),
        ReportRow(RANKING_TITLES[1], tuple(allocated)),
        ReportRow(RANKING_TITLES[2], tuple(closing)),
    )


def _top_entries(entries: list[ReportEntry]) -> tuple[ReportEntry, ...]:
    """Sort ranked entries by amount, largest first, and cap them at ``TOP_N``.

    Args:
        entries: The ranked entries.

    Returns:
        The top entries, ties broken by name.
    """
    ordered = sorted(entries, key=lambda entry: (-entry.amount, _sort_key(entry.name)))
    return tuple(ordered[:TOP_N])


def _report_name_chars(rows: tuple[ReportRow, ...]) -> int:
    """Return the longest entry name across every report row.

    The Fava report uses it to give every column the same width.

    Args:
        rows: The report rows.

    Returns:
        The length of the longest name, or ``0`` when there is none.

    Examples:
        >>> row = ReportRow(
        ...     "Top",
        ...     (MonthReport(date(2024, 1, 1), (ReportEntry("Rent", Decimal(1)),)),),
        ... )
        >>> _report_name_chars((row,))
        4
    """
    return max(
        (
            len(entry.name)
            for row in rows
            for month in row.months
            for entry in month.entries
        ),
        default=0,
    )


def _report_amount_chars(rows: tuple[ReportRow, ...]) -> int:
    """Return the length of the longest formatted amount across every row.

    The Fava report uses it to size every table's amount column to fit its
    widest figure, in the ``f"{amount:.2f}"`` form the report displays.

    Args:
        rows: The report rows.

    Returns:
        The length of the longest amount, or ``0`` when there is none.

    Examples:
        >>> row = ReportRow(
        ...     "Top",
        ...     (MonthReport(date(2024, 1, 1), (ReportEntry("Rent", Decimal("1234.5")),)),),
        ... )
        >>> _report_amount_chars((row,))
        7
    """
    return max(
        (
            len(f"{entry.amount:.2f}")
            for row in rows
            for month in row.months
            for entry in month.entries
        ),
        default=0,
    )


def _open_in(envelope: Envelope, month: date) -> bool:
    """Whether an envelope is open in a month.

    Args:
        envelope: The declared envelope.
        month: The month to test.

    Returns:
        True when the month is on or after the envelope's ``open`` and on or
        before its ``close`` (when it has one).
    """
    return month >= month_of(envelope.opened) and (
        envelope.closed is None or month <= month_of(envelope.closed)
    )


def _pool_entries(ledger: PoolLedger, month: date) -> tuple[PoolEntry, ...]:
    """Return a pool ledger's lines dated within a month.

    Args:
        ledger: The pool ledger.
        month: The month to select.

    Returns:
        The month's pool entries, in order.
    """
    goal = month_of(month)
    return tuple(entry for entry in ledger.entries if month_of(entry.date) == goal)


def _detail_index(
    events: tuple[ActivityEvent, ...],
) -> dict[tuple[str, date], list[ActivityEvent]]:
    """Group activity events by destination and month.

    The events keep their source directive; a cell derives the hash of just the
    lines it shows, so hashing the whole ledger is avoided.

    Args:
        events: The per-posting activity events.

    Returns:
        A mapping from ``(destination, month)`` to its detail events.
    """
    index: dict[tuple[str, date], list[ActivityEvent]] = {}
    for event in events:
        index.setdefault((event.destination, month_of(event.date)), []).append(event)
    return index


def _allocation_index(
    events: tuple[AllocationEvent, ...],
) -> dict[tuple[str, date], list[AllocationEvent]]:
    """Group ``allocate`` directives by envelope and month.

    The events keep their source directive, for the same reason as
    :func:`_detail_index`.

    Args:
        events: The allocation events.

    Returns:
        A mapping from ``(envelope, month)`` to its allocation events.
    """
    index: dict[tuple[str, date], list[AllocationEvent]] = {}
    for event in events:
        index.setdefault((event.envelope, month_of(event.date)), []).append(event)
    return index


def _activity_line(event: ActivityEvent) -> ActivityLine:
    """Build an activity detail line, hashing its source transaction.

    Args:
        event: The activity event, carrying its source transaction.

    Returns:
        The :class:`ActivityLine`.
    """
    return ActivityLine(
        date=event.date,
        amount=event.amount,
        entry_hash=entry_hash(event.entry) if event.entry is not None else "",
        payee=event.payee,
        transaction_narration=event.transaction_narration,
        posting_narration=event.posting_narration,
    )


def _allocation_line(event: AllocationEvent) -> AllocationLine:
    """Build an allocation line, hashing its source directive.

    Args:
        event: The allocation event, carrying its source directive.

    Returns:
        The :class:`AllocationLine`.
    """
    return AllocationLine(
        date=event.date,
        amount=event.amount,
        entry_hash=entry_hash(event.entry) if event.entry is not None else "",
    )


def _plan_lines(envelope: Envelope) -> tuple[PlanLine, ...]:
    """List every plan directive for an envelope, in order.

    Args:
        envelope: The declared envelope.

    Returns:
        The ``open`` base plan first, then each dated ``plan``/``unplan`` change.
    """
    base = PlanLine(
        date=envelope.opened,
        entry_hash=envelope.source_hash,
        monthly=envelope.plan.monthly,
        priority=envelope.plan.priority,
        goal=envelope.plan.goal,
        goal_currency=envelope.plan.goal_currency,
        by=envelope.plan.by,
        up_to=envelope.plan.up_to,
        base=True,
    )
    history = tuple(
        PlanLine(
            date=change.date,
            entry_hash=change.entry_hash,
            monthly=change.monthly,
            priority=change.priority,
            goal=change.goal,
            goal_currency=change.goal_currency,
            by=change.by,
            up_to=change.up_to,
            clears=change.clears,
        )
        for change in envelope.plans
    )
    return (base, *history)


def _cell(  # noqa: PLR0913 - the cell's many optional figures share one builder
    row: MonthRow,
    detail: Sequence[ActivityEvent],
    *,
    projected: bool,
    visible: bool = True,
    level: Decimal | None = None,
    funding: str | None = None,
    goal: Decimal | None = None,
    up_to: Decimal | None = None,
    fill: Decimal | None = None,
    allocations: Sequence[AllocationEvent] = (),
    pool: tuple[PoolEntry, ...] = (),
    explanation: FundingExplanation | None = None,
) -> Cell:
    """Build a cell from a monthly row and its detail.

    Args:
        row: The monthly row.
        detail: The contributing postings.
        projected: Whether the month is beyond the computed range.
        visible: Whether the row should be shown for the month.
        level: The month's funding level, or ``None``.
        funding: The month's funding status, or ``None``.
        goal: The month's goal total, or ``None``.
        up_to: The month's balance ceiling, or ``None``.
        fill: How full the envelope is toward its goal, or ``None``.
        allocations: The month's ``allocate`` directives.
        pool: The pool row's ledger lines for the month, if any.
        explanation: The arithmetic behind the month's plan verdict, or ``None``.

    Returns:
        The :class:`Cell`. Its detail lines and allocations are built, and their
        source hashes derived, here — for the ``MAX_DETAIL`` lines it keeps, not
        the whole index.
    """
    return Cell(
        month=row.month,
        projected=projected,
        opening=row.opening,
        allocated=row.allocated,
        activity=row.activity,
        closing=row.closing,
        detail=tuple(_activity_line(event) for event in detail[:MAX_DETAIL]),
        detail_total=len(detail),
        visible=visible,
        level=level,
        funding=funding,
        goal=goal,
        up_to=up_to,
        fill=fill,
        allocations=tuple(_allocation_line(event) for event in allocations),
        pool=pool,
        explanation=explanation,
    )


def _envelope_view(
    report: EnvelopeReport,
    months: tuple[date, ...],
    available: set[date],
    details: Mapping[tuple[str, date], list[ActivityEvent]],
    allocations: Mapping[tuple[str, date], list[AllocationEvent]],
) -> EnvelopeView:
    """Build an envelope view.

    Args:
        report: The envelope's report.
        months: The months shown.
        available: The computed months.
        details: The activity detail index.
        allocations: The allocation index.

    Returns:
        The :class:`EnvelopeView`.
    """
    envelope = report.envelope
    opened_month = month_of(envelope.opened)
    closed_month = month_of(envelope.closed) if envelope.closed is not None else None
    cells: list[Cell] = []
    for month in months:
        row = row_for(report.rows, month)
        on = _last_day(month)
        plan = envelope.effective(on)
        level = plan_level(report, on)
        funded = report.funded_at(on)
        if plan.up_to is not None:
            up_to = plan.up_to
            reference = _round_to_cent(plan.up_to)
            funding = "on" if plan_top_up(report, on) == 0 else "under"
        else:
            up_to = None
            reference = plan.goal if plan.goal is not None else level
            funding = _funding(level, funded)
        cells.append(
            _cell(
                row,
                details.get((report.name, month), []),
                projected=month not in available,
                visible=month >= opened_month
                and (closed_month is None or month <= closed_month),
                level=level,
                funding=funding,
                goal=plan.goal,
                up_to=up_to,
                fill=_fill(reference, funded),
                allocations=allocations.get((report.name, month), []),
                explanation=funding_explanation(report, on),
            )
        )
    return EnvelopeView(
        name=report.name,
        plan=envelope.effective(_last_day(months[0])),
        cells=tuple(cells),
        parent=envelope.parent,
        plans=_plan_lines(envelope),
    )


def _group(envelopes: tuple[EnvelopeView, ...]) -> tuple[EnvelopeGroup, ...]:
    """Group envelopes by parent category.

    Args:
        envelopes: The envelopes, in alphabetical order.

    Returns:
        One :class:`EnvelopeGroup` per distinct parent, with the parent
        headings in alphabetical order; envelopes with no parent form a
        headingless group placed last. Each group's members keep the order of
        ``envelopes``.
    """
    buckets: dict[str | None, list[EnvelopeView]] = {}
    for envelope in envelopes:
        buckets.setdefault(envelope.parent, []).append(envelope)
    ordered_parents = sorted(
        (parent for parent in buckets if parent is not None), key=_sort_key
    )
    headings: list[str | None] = [*ordered_parents]
    if None in buckets:
        headings.append(None)
    return tuple(
        EnvelopeGroup(
            parent=parent,
            envelopes=tuple(buckets[parent]),
            subtotal=_subtotal(buckets[parent]),
        )
        for parent in headings
    )


def _subtotal(envelopes: list[EnvelopeView]) -> tuple[Cell, ...]:
    """Sum a group's member cells, month by month.

    Args:
        envelopes: The group's member envelopes.

    Returns:
        One summed :class:`Cell` per month, with no detail.
    """
    if not envelopes:
        return ()
    cells: list[Cell] = []
    for index in range(len(envelopes[0].cells)):
        reference = envelopes[0].cells[index]
        members = [
            envelope.cells[index]
            for envelope in envelopes
            if envelope.cells[index].visible
        ]
        cells.append(
            Cell(
                month=reference.month,
                projected=reference.projected,
                opening=sum((cell.opening for cell in members), Decimal(0)),
                allocated=sum((cell.allocated for cell in members), Decimal(0)),
                activity=sum((cell.activity for cell in members), Decimal(0)),
                closing=sum((cell.closing for cell in members), Decimal(0)),
                detail=(),
                detail_total=0,
                visible=bool(members),
            )
        )
    return tuple(cells)


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
        The balance for that month, the pre-start cash before the range, or the
        final balance after it.
    """
    goal = month_of(month)
    if not result.cash or goal < result.months[0]:
        return result.pre_start_cash
    index = bisect_left(result.cash, goal, key=lambda row: row.month)
    if index < len(result.cash) and result.cash[index].month == goal:
        return result.cash[index].balance
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
                "parent": envelope.parent,
                "plan": _plan_json(envelope.plan),
                "plans": [_plan_line_json(line) for line in envelope.plans],
                "cells": [_cell_json(cell) for cell in envelope.cells],
            }
            for envelope in view.envelopes
        ],
        "groups": [
            {
                "parent": group.parent,
                "envelopes": [envelope.name for envelope in group.envelopes],
                "subtotal": [_cell_json(cell) for cell in group.subtotal],
            }
            for group in view.groups
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


def _ratio_json(value: Decimal | None) -> str | None:
    """Format an optional ratio for JSON.

    Args:
        value: The ratio, or ``None``.

    Returns:
        A four-decimal string, or ``None``.
    """
    return None if value is None else f"{value:.4f}"


def _plan_json(plan: Plan) -> dict[str, object]:
    """Serialise an envelope's effective plan.

    Args:
        plan: The plan.

    Returns:
        A JSON-compatible dictionary.
    """
    monthly: str | None = None
    if isinstance(plan.monthly, Decimal):
        monthly = f"{plan.monthly:.2f}"
    elif isinstance(plan.monthly, str):
        monthly = plan.monthly
    return {
        "monthly": monthly,
        "priority": _money_json(plan.priority),
        "goal": _money_json(plan.goal),
        "goal_currency": plan.goal_currency,
        "by": plan.by.isoformat() if plan.by is not None else None,
        "up_to": _money_json(plan.up_to),
    }


def _pool_entry_json(entry: PoolEntry) -> dict[str, object]:
    """Serialise one pool-ledger line.

    Args:
        entry: The pool entry.

    Returns:
        A JSON-compatible dictionary with a fixed two-decimal money string and
        an ISO date.
    """
    return {
        "date": entry.date.isoformat(),
        "kind": entry.kind,
        "amount": f"{entry.amount:.2f}",
        "balance": f"{entry.balance:.2f}",
        "entry_hash": entry.entry_hash,
        "payee": entry.payee,
        "transaction_narration": entry.transaction_narration,
        "posting_narration": entry.posting_narration,
        "envelope": entry.envelope,
    }


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
        "visible": cell.visible,
        "opening": f"{cell.opening:.2f}",
        "allocated": f"{cell.allocated:.2f}",
        "activity": f"{cell.activity:.2f}",
        "closing": f"{cell.closing:.2f}",
        "level": _money_json(cell.level),
        "funding": cell.funding,
        "goal": _money_json(cell.goal),
        "up_to": _money_json(cell.up_to),
        "fill": _ratio_json(cell.fill),
        "detail": [
            {
                "date": line.date.isoformat(),
                "amount": f"{line.amount:.2f}",
                "entry_hash": line.entry_hash,
                "payee": line.payee,
                "transaction_narration": line.transaction_narration,
                "posting_narration": line.posting_narration,
            }
            for line in cell.detail
        ],
        "detail_total": cell.detail_total,
        "allocations": [
            {
                "date": line.date.isoformat(),
                "amount": f"{line.amount:.2f}",
                "entry_hash": line.entry_hash,
            }
            for line in cell.allocations
        ],
        "pool": [_pool_entry_json(entry) for entry in cell.pool],
        "explanation": _explanation_json(cell.explanation),
    }


def _explanation_json(
    explanation: FundingExplanation | None,
) -> dict[str, object] | None:
    """Serialise a funding explanation.

    Args:
        explanation: The explanation, or ``None``.

    Returns:
        A JSON-compatible dictionary, or ``None``.
    """
    if explanation is None:
        return None
    return {
        "reference": explanation.reference,
        "level": _money_json(explanation.level),
        "funded": f"{explanation.funded:.2f}",
        "top_up": f"{explanation.top_up:.2f}",
        "funding": explanation.funding,
        "opening": _money_json(explanation.opening),
        "allocated": _money_json(explanation.allocated),
        "closing": _money_json(explanation.closing),
        "monthly": _money_json(explanation.monthly),
        "up_to": _money_json(explanation.up_to),
        "goal": _money_json(explanation.goal),
        "by": explanation.by.isoformat() if explanation.by is not None else None,
        "months_elapsed": explanation.months_elapsed,
        "months_total": explanation.months_total,
        "average_months": [
            {"month": month.isoformat(), "spending": f"{spend:.2f}"}
            for month, spend in explanation.average_months
        ],
    }


def _plan_line_json(line: PlanLine) -> dict[str, object]:
    """Serialise one plan directive.

    Args:
        line: The plan line.

    Returns:
        A JSON-compatible dictionary.
    """
    monthly: str | None = None
    if isinstance(line.monthly, Decimal):
        monthly = f"{line.monthly:.2f}"
    elif isinstance(line.monthly, str):
        monthly = line.monthly
    return {
        "date": line.date.isoformat(),
        "entry_hash": line.entry_hash,
        "monthly": monthly,
        "priority": _money_json(line.priority),
        "goal": _money_json(line.goal),
        "goal_currency": line.goal_currency,
        "by": line.by.isoformat() if line.by is not None else None,
        "up_to": _money_json(line.up_to),
        "clears": line.clears,
        "base": line.base,
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
    }
