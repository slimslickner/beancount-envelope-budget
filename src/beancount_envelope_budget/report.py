"""A neutral, read-only view of a budget result.

Both the CLI and the Fava report render from :func:`build_view`, so the terminal
and the web page cannot disagree about the numbers. This module is pure: no I/O
and no Fava or CLI imports. The one exception is :func:`today_month`, which reads
the wall clock to pick a default month — a display concern that the CLI and the
Fava report share from here rather than each reimplement.
"""

from __future__ import annotations

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
    month_of,
    row_for,
)
from beancount_envelope_budget.schema import Severity

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ActivityLine",
    "AllocationLine",
    "BudgetView",
    "Cell",
    "EnvelopeGroup",
    "EnvelopeView",
    "PlanLine",
    "build_view",
    "plan_level",
    "to_jsonable",
    "today_month",
]

#: The most activity detail lines shown for a single cell.
MAX_DETAIL = 20

#: The maximum number of months a view may span.
MAX_MONTHS = 3

#: The quantum every funding level is rounded to, matching the whole cents the
#: ledger holds and the report displays.
_CENT = Decimal("0.01")


def _round_to_cent(value: Decimal) -> Decimal:
    """Round a funding level to the money the ledger displays.

    A goal's level is ``target * elapsed / total``, which can carry a sub-cent
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
        target: A goal balance, or ``None``.
        target_currency: The currency of the goal, or ``None``.
        by: The date the goal should be reached by, or ``None``.
        clears: Whether this is an ``unplan`` that resets the plan to defaults.
        base: Whether this is the envelope's ``open`` directive.
    """

    date: date
    entry_hash: str
    monthly: Decimal | str | None = None
    priority: Decimal | None = None
    target: Decimal | None = None
    target_currency: str | None = None
    by: date | None = None
    clears: bool = False
    base: bool = False


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
        target: The envelope's goal balance this month, or ``None`` when it has
            no target.
        fill: How full the envelope is toward its goal, clamped to ``0`` to
            ``1`` — the amount credited toward the plan this month (see
            :meth:`~beancount_envelope_budget.core.EnvelopeReport.funded_at`)
            divided by its ``target``, or by its ``level`` when it has no target.
            ``None`` when it has neither.
        allocations: The ``allocate`` directives dated in this month, each
            linking back to its source directive.
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
    target: Decimal | None = None
    fill: Decimal | None = None
    allocations: tuple[AllocationLine, ...] = ()


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
    target = month_of(month)
    return next((row for row in rows if row.month == target), None)


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


def _average_spend(report: EnvelopeReport, on: date, window: int) -> Decimal | None:
    """Average an envelope's spending over the last ``window`` completed months.

    Args:
        report: The envelope's report.
        on: The date to evaluate.
        window: How many completed months to average.

    Returns:
        The average spending, or ``None`` when there is no history.
    """
    anchor = month_of(on)
    total = Decimal(0)
    count = 0
    for offset in range(1, window + 1):
        row = _maybe_row(report.rows, _shift(anchor, -offset))
        if row is None:
            continue
        total += -row.activity
        count += 1
    return total / count if count else None


def plan_level(report: EnvelopeReport, on: date) -> Decimal | None:
    """Resolve an envelope's funding level on a date.

    An envelope with a ``monthly`` plan holds that amount (a number, or the
    average of its last ``avgN`` completed months of spending). A goal envelope
    with a ``target`` holds the on-track balance from
    :meth:`~beancount_envelope_budget.core.Envelope.funding_level`. The resolved
    level is rounded to the cent, because a goal's prorated target can carry a
    sub-cent tail that a whole-cent balance would otherwise read as over- or
    under-funded.

    Args:
        report: The envelope's report.
        on: The date to evaluate.

    Returns:
        The balance the envelope should hold, or ``None`` when it has no plan.

    Examples:
        >>> from decimal import Decimal
        >>> from beancount_envelope_budget.core import Envelope, EnvelopeReport, Plan
        >>> goal = EnvelopeReport(
        ...     Envelope(
        ...         "Emergency",
        ...         date(2026, 1, 1),
        ...         None,
        ...         Plan(
        ...             target=Decimal("6000"),
        ...             target_currency="USD",
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
    if isinstance(plan.monthly, Decimal):
        return _round_to_cent(plan.monthly)
    if isinstance(plan.monthly, str):
        average = _average_spend(report, on, _avg_window(plan.monthly))
        return None if average is None else _round_to_cent(average)
    level = report.envelope.funding_level(on)
    return None if level is None else _round_to_cent(level)


def _funding(level: Decimal | None, funded: Decimal) -> str | None:
    """Classify an envelope's funded amount against its funding level.

    Args:
        level: The month's funding level, or ``None``.
        funded: The amount credited toward the plan this month, from
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
    """Return how full an envelope is toward a reference balance.

    Args:
        reference: The balance to measure against, or ``None``.
        funded: The amount credited toward the plan this month, from
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


def build_view(
    result: BudgetResult,
    month: date | None = None,
    count: int = 1,
) -> BudgetView:
    """Build a view over one to three months.

    Args:
        result: The computed budget.
        month: The first month to show; defaults to the latest month computed.
            Callers that want the current month — the CLI and the Fava report —
            pass :func:`today_month`, so a future-dated directive does not move
            the default window forward.
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
    details = _detail_index(result.activity)
    allocations = _allocation_index(result.allocations)

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
            ActivityLine(
                date=event.date,
                amount=event.amount,
                entry_hash=event.entry_hash,
                payee=event.payee,
                transaction_narration=event.transaction_narration,
                posting_narration=event.posting_narration,
            )
        )
    return index


def _allocation_index(
    events: tuple[AllocationEvent, ...],
) -> dict[tuple[str, date], list[AllocationLine]]:
    """Group ``allocate`` directives by envelope and month.

    Args:
        events: The allocation events.

    Returns:
        A mapping from ``(envelope, month)`` to its allocation lines.
    """
    index: dict[tuple[str, date], list[AllocationLine]] = {}
    for event in events:
        key = (event.envelope, month_of(event.date))
        index.setdefault(key, []).append(
            AllocationLine(
                date=event.date,
                amount=event.amount,
                entry_hash=event.entry_hash,
            )
        )
    return index


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
        target=envelope.plan.target,
        target_currency=envelope.plan.target_currency,
        by=envelope.plan.by,
        base=True,
    )
    history = tuple(
        PlanLine(
            date=change.date,
            entry_hash=change.entry_hash,
            monthly=change.monthly,
            priority=change.priority,
            target=change.target,
            target_currency=change.target_currency,
            by=change.by,
            clears=change.clears,
        )
        for change in envelope.plans
    )
    return (base, *history)


def _cell(  # noqa: PLR0913 - the cell's many optional figures share one builder
    row: MonthRow,
    detail: list[ActivityLine],
    *,
    projected: bool,
    visible: bool = True,
    level: Decimal | None = None,
    funding: str | None = None,
    target: Decimal | None = None,
    fill: Decimal | None = None,
    allocations: tuple[AllocationLine, ...] = (),
) -> Cell:
    """Build a cell from a monthly row and its detail.

    Args:
        row: The monthly row.
        detail: The contributing postings.
        projected: Whether the month is beyond the computed range.
        visible: Whether the row should be shown for the month.
        level: The month's funding level, or ``None``.
        funding: The month's funding status, or ``None``.
        target: The month's goal balance, or ``None``.
        fill: How full the envelope is toward its goal, or ``None``.
        allocations: The month's ``allocate`` directives.

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
        visible=visible,
        level=level,
        funding=funding,
        target=target,
        fill=fill,
        allocations=allocations,
    )


def _envelope_view(
    report: EnvelopeReport,
    months: tuple[date, ...],
    available: set[date],
    details: dict[tuple[str, date], list[ActivityLine]],
    allocations: dict[tuple[str, date], list[AllocationLine]],
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
        reference = plan.target if plan.target is not None else level
        funded = report.funded_at(on)
        cells.append(
            _cell(
                row,
                details.get((report.name, month), []),
                projected=month not in available,
                visible=month >= opened_month
                and (closed_month is None or month <= closed_month),
                level=level,
                funding=_funding(level, funded),
                target=plan.target,
                fill=_fill(reference, funded),
                allocations=tuple(allocations.get((report.name, month), [])),
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
    target = month_of(month)
    if not result.cash or target < result.months[0]:
        return result.pre_start_cash
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
        "target": _money_json(plan.target),
        "target_currency": plan.target_currency,
        "by": plan.by.isoformat() if plan.by is not None else None,
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
        "target": _money_json(cell.target),
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
        "target": _money_json(line.target),
        "target_currency": line.target_currency,
        "by": line.by.isoformat() if line.by is not None else None,
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
