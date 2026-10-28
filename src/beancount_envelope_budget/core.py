"""Pure envelope-budget computation over a parsed Beancount ledger.

This module performs no I/O and imports neither the CLI nor Fava. It converts
Beancount entries into the core's own typed records, applies the resolution
rules, folds the results into per-month rows for every envelope plus the pool
and the cash balance, and reports diagnostics.

The one piece of Beancount-specific knowledge is confined to the adapter
helpers at the top of the module (``_unwrap``, ``_scan_accounts`` and friends);
everything below them works on plain records.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from beancount.core import compare, convert, interpolate
from beancount.core.amount import Amount
from beancount.core.data import Custom, Directive, Open, Posting, Transaction

from beancount_envelope_budget.schema import Severity, get_diagnostic, render_message

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "POOL",
    "ActivityEvent",
    "BudgetResult",
    "CashRow",
    "Diagnostic",
    "Envelope",
    "EnvelopeReport",
    "MonthRow",
    "Plan",
    "PlanChange",
    "Source",
    "compute",
    "existing_allocations",
    "format_amount",
    "iter_months",
    "month_of",
    "row_for",
]

#: The reserved name of the unassigned pool.
POOL = "pool"

#: A planned monthly amount: a number, or the literal ``"avgN"``.
Monthly = Decimal | str

_ROOT_INCOME = "Income"
_ROOT_EQUITY = "Equity"
_INCOME_OR_EQUITY = frozenset({_ROOT_INCOME, _ROOT_EQUITY})
_CASH_ROOTS = frozenset({"Assets", "Liabilities"})
_MONTHS_PER_YEAR = 12
_MOVE_ARGS = 3
_ALLOCATE_ARGS = 3

#: The shape of an average-of-N-months monthly amount, e.g. ``"avg3"``.
_AVG_PATTERN = re.compile(r"^avg(\d+)$")


# --- Records -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Source:
    """A location in the ledger.

    Attributes:
        filename: The ledger file name.
        lineno: The one-based line number.
    """

    filename: str
    lineno: int


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A validation error or warning produced by the core.

    Attributes:
        code: The registered code, such as ``E001`` or ``W001``.
        severity: Whether the code is an error or a warning.
        message: The rendered, human-readable message.
        entry: The Beancount directive the diagnostic refers to, if any.
        source: A ledger location, when no directive is available.
    """

    code: str
    severity: Severity
    message: str
    entry: Directive | None = None
    source: Source | None = None


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """The effect of one posting on an envelope or the pool.

    Attributes:
        destination: An envelope name or :data:`POOL`.
        date: The transaction date.
        amount: The signed effect on the destination.
        entry_hash: The Beancount hash of the transaction, for linking back to
            it. It is the same value Beancount's ``compare.hash_entry`` yields,
            so Fava can resolve it to the entry.
        payee: The transaction's payee, if any.
        transaction_narration: The transaction's narration, if any.
        posting_narration: The posting's ``narration`` metadata, if any. This is
            the convention used by the ``autobean.narration`` plugin; it is
            read, not validated.
    """

    destination: str
    date: date
    amount: Decimal
    entry_hash: str
    payee: str | None = None
    transaction_narration: str | None = None
    posting_narration: str | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """An envelope's funding parameters at one point in time.

    The same record is used for an envelope's base parameters, for the result of
    :meth:`Envelope.effective`, and as the fields an override may change.

    Attributes:
        monthly: The planned monthly amount, a number or ``"avgN"``, or ``None``.
        priority: The funding priority, lower first, or ``None``.
        target: A goal balance, if any.
        target_currency: The currency of the target, if any.
        by: The date the target should be reached by, if any.

    Examples:
        >>> Plan()
        Plan(monthly=None, priority=None, target=None, target_currency=None, by=None)
    """

    monthly: Monthly | None = None
    priority: Decimal | None = None
    target: Decimal | None = None
    target_currency: str | None = None
    by: date | None = None


@dataclass(frozen=True, slots=True)
class PlanChange:
    """A dated change to an envelope's plan.

    Every field is ``None`` when the change does not name it, so applying a
    change touches only the fields it sets. A clearing change (from ``unplan``)
    resets the accumulated plan to defaults.

    Attributes:
        date: The date the change takes effect.
        monthly: The new monthly amount, or ``None`` when unchanged.
        priority: The new priority, or ``None`` when unchanged.
        target: The new goal balance, or ``None`` when unchanged.
        target_currency: The currency of the new target, or ``None``.
        by: The new deadline, or ``None`` when unchanged.
        clears: Whether applying the change resets the plan to defaults.
    """

    date: date
    monthly: Monthly | None = None
    priority: Decimal | None = None
    target: Decimal | None = None
    target_currency: str | None = None
    by: date | None = None
    clears: bool = False


@dataclass(frozen=True, slots=True)
class Envelope:
    """A declared envelope and its funding parameters.

    Attributes:
        name: The envelope's name.
        opened: The date the envelope was declared.
        closed: The date the envelope was retired, if any.
        plan: The base plan, from the ``open`` metadata.
        plans: Dated overrides, sorted by date.
        parent: A grouping label set on ``open``, or ``None``.
    """

    name: str
    opened: date
    closed: date | None
    plan: Plan
    plans: tuple[PlanChange, ...]
    parent: str | None = None

    def effective(self, on: date) -> Plan:
        """Return the plan in force on a date.

        Args:
            on: The date to evaluate.

        Returns:
            The base plan with every override dated on or before ``on`` applied
            in order; a clearing override resets it to a default :class:`Plan`.
        """
        plan = self.plan
        for change in self.plans:
            if change.date > on:
                break
            if change.clears:
                plan = Plan()
                continue
            plan = replace(
                plan,
                monthly=(
                    change.monthly if change.monthly is not None else plan.monthly
                ),
                priority=(
                    change.priority if change.priority is not None else plan.priority
                ),
                target=change.target if change.target is not None else plan.target,
                target_currency=(
                    change.target_currency
                    if change.target_currency is not None
                    else plan.target_currency
                ),
                by=change.by if change.by is not None else plan.by,
            )
        return plan

    def funding_level(self, on: date) -> Decimal | None:
        """Return the on-track balance for a goal envelope on a date.

        A goal envelope is one funded by a ``target``. Its level is the balance
        it should hold at ``on`` to be on schedule: ``target`` grown linearly
        from the month the target first took effect to its ``by`` month, both
        ends inclusive, so the level is ``target`` once ``by`` arrives and a
        fraction of it before.

        Args:
            on: The date to evaluate.

        Returns:
            The level, or ``None`` when the envelope has no target (or no
            ``by``).

        Examples:
            >>> emergency = Envelope(
            ...     "Emergency",
            ...     date(2026, 1, 1),
            ...     None,
            ...     Plan(
            ...         target=Decimal("6000"),
            ...         target_currency="USD",
            ...         by=date(2026, 12, 31),
            ...     ),
            ...     (),
            ... )
            >>> emergency.funding_level(date(2026, 6, 15))
            Decimal('3000')
            >>> emergency.funding_level(date(2026, 12, 31))
            Decimal('6000')
            >>> Envelope("Misc", date(2026, 1, 1), None, Plan(), ()).funding_level(
            ...     date(2026, 6, 15)
            ... ) is None
            True
        """
        plan = self.effective(on)
        if plan.target is None or plan.by is None:
            return None
        start = _goal_start(self, on)
        if start is None:
            return None
        total = _months_inclusive(start, plan.by)
        if total <= 0:
            return plan.target
        elapsed = max(0, min(_months_inclusive(start, on), total))
        return plan.target * elapsed / total


def _goal_start(envelope: Envelope, on: date) -> date | None:
    """Return the date an envelope's current target first took effect.

    Args:
        envelope: The envelope.
        on: The date to evaluate.

    Returns:
        The ``open`` date when the base plan sets a target, the date of the
        first ``plan`` on or before ``on`` that introduced one otherwise, or
        ``None`` when no target is in effect. A clearing ``unplan`` drops the
        target, so a later plan that sets one starts the clock again; changing
        ``target`` or ``by`` does not.
    """
    start = envelope.opened if envelope.plan.target is not None else None
    for change in envelope.plans:
        if change.date > on:
            break
        if change.clears:
            start = None
        elif change.target is not None and start is None:
            start = change.date
    return start


def _months_inclusive(start: date, end: date) -> int:
    """Count whole months from one month to another, counting both ends.

    Args:
        start: Any date; only its month matters.
        end: Any date; only its month matters.

    Returns:
        The number of months, one when both dates fall in the same month, and
        negative when ``end``'s month is before ``start``'s.

    Examples:
        >>> _months_inclusive(date(2024, 1, 15), date(2024, 1, 31))
        1
        >>> _months_inclusive(date(2024, 1, 1), date(2024, 6, 10))
        6
    """
    return (end.year - start.year) * _MONTHS_PER_YEAR + (end.month - start.month) + 1


@dataclass(frozen=True, slots=True)
class MonthRow:
    """One month of an envelope's or the pool's ledger.

    Attributes:
        month: The first day of the month.
        opening: The balance carried in.
        allocated: Money assigned (or, for the pool, removed) this month.
        activity: The summed effects of postings this month.
    """

    month: date
    opening: Decimal
    allocated: Decimal
    activity: Decimal

    @property
    def closing(self) -> Decimal:
        """The balance carried out, opening plus allocated plus activity."""
        return self.opening + self.allocated + self.activity


@dataclass(frozen=True, slots=True)
class EnvelopeReport:
    """An envelope and its monthly rows.

    Attributes:
        envelope: The declared envelope.
        rows: One :class:`MonthRow` per month in the result.
    """

    envelope: Envelope
    rows: tuple[MonthRow, ...]

    @property
    def name(self) -> str:
        """The envelope's name."""
        return self.envelope.name

    @property
    def closing(self) -> Decimal:
        """The balance at the end of the final month, or zero."""
        return self.rows[-1].closing if self.rows else Decimal(0)

    def closing_at(self, month: date) -> Decimal:
        """Return the closing balance for a month.

        Args:
            month: The first day of the month, or any date within it.

        Returns:
            The closing balance for that month, or zero when the month is out
            of range.
        """
        target = month_of(month)
        for row in self.rows:
            if row.month == target:
                return row.closing
        return Decimal(0)

    def funded_at(self, on: date) -> Decimal:
        """Return the amount credited toward the envelope's plan on a date.

        A goal envelope — one with a ``target`` and no ``monthly`` — is funded by
        its closing balance, because a ``target`` is a balance to hold and
        spending draws it down. A ``monthly`` envelope is funded by the money
        made available that month: the opening balance plus the month's
        allocation (its closing balance less the month's activity), so receiving
        the planned amount reads as funded even after the month's spending. The
        precedence matches :func:`~beancount_envelope_budget.report.plan_level`:
        a plan with a ``monthly`` is measured as a monthly plan.

        Args:
            on: A date in the month to evaluate.

        Returns:
            The amount credited toward the plan.

        Examples:
            >>> row = MonthRow(
            ...     date(2026, 10, 1), Decimal(0), Decimal(550), Decimal("-403.69")
            ... )
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
            >>> report.funded_at(date(2026, 10, 31))
            Decimal('550')
            >>> report.closing_at(date(2026, 10, 31))
            Decimal('146.31')
        """
        plan = self.envelope.effective(on)
        row = row_for(self.rows, on)
        if plan.monthly is None and plan.target is not None:
            return row.closing
        return row.opening + row.allocated


@dataclass(frozen=True, slots=True)
class CashRow:
    """The cash balance at one month end.

    Attributes:
        month: The first day of the month.
        balance: The ledger's cash balance on the last day of that month.
    """

    month: date
    balance: Decimal


@dataclass(frozen=True, slots=True)
class BudgetResult:
    """The full outcome of a computation.

    Attributes:
        start: The start date, or ``None`` when tracking is not configured.
        operating_currency: The single operating currency, or ``""``.
        months: Every month in range, as first days.
        envelopes: Per-envelope reports, in declaration order.
        pool: Monthly rows for the unassigned pool.
        cash: The cash balance at each month end.
        diagnostics: Every error and warning, sorted deterministically.
        resolved: ``(id(posting), envelope)`` pairs for non-cash postings,
            when requested by ``collect_resolved``.
        activity: Per-posting activity detail, when requested by
            ``collect_activity``.
        pre_start_cash: The cash held on the day before the start date; the
            pool's opening balance and the cash carried into the first month.
    """

    start: date | None
    operating_currency: str
    months: tuple[date, ...]
    envelopes: tuple[EnvelopeReport, ...]
    pool: tuple[MonthRow, ...]
    cash: tuple[CashRow, ...]
    diagnostics: tuple[Diagnostic, ...]
    resolved: tuple[tuple[int, str], ...] = ()
    activity: tuple[ActivityEvent, ...] = ()
    pre_start_cash: Decimal = Decimal(0)

    def envelope(self, name: str) -> EnvelopeReport | None:
        """Return the report for an envelope by name.

        Args:
            name: The envelope's name.

        Returns:
            The matching report, or ``None``.
        """
        return next((report for report in self.envelopes if report.name == name), None)


# --- Adapter helpers (the only Beancount-aware section) ------------------------


@dataclass(frozen=True, slots=True)
class AccountInfo:
    """The envelope-relevant metadata of an account.

    Attributes:
        is_cash: Whether the account is marked ``cash-account: TRUE``.
        envelope: The account's ``envelope`` metadata, if any.
    """

    is_cash: bool
    envelope: str | None


@dataclass(frozen=True, slots=True)
class _RawDirective:
    """A parsed ``custom "envelope"`` directive.

    Attributes:
        date: The directive's date.
        verb: The first value after the ``envelope`` type.
        args: The remaining values, unwrapped from ``ValueType``.
        meta: The directive's metadata.
        entry: The source directive.
    """

    date: date
    verb: str
    args: tuple[object, ...]
    meta: dict[str, object]
    entry: Custom


@dataclass
class _Draft:
    """A mutable envelope under construction.

    Args:
        name: The envelope's name.
        opened: The declaration date.
    """

    name: str
    opened: date
    plan: Plan = field(default_factory=Plan)
    plans: list[PlanChange] = field(default_factory=list)
    closed: date | None = None
    parent: str | None = None

    def freeze(self) -> Envelope:
        """Return the immutable envelope.

        Returns:
            The frozen :class:`Envelope`.
        """
        return Envelope(
            name=self.name,
            opened=self.opened,
            closed=self.closed,
            plan=self.plan,
            plans=tuple(sorted(self.plans, key=lambda change: change.date)),
            parent=self.parent,
        )

    def effective(self, on: date) -> Plan:
        """Return the plan in force on a date, before the draft is frozen.

        Args:
            on: The date to evaluate.

        Returns:
            The plan as :meth:`Envelope.effective` would resolve it.
        """
        return self.freeze().effective(on)


def _unwrap(value: object) -> object:
    """Unwrap a Beancount ``ValueType`` into its value.

    Args:
        value: A custom-directive value, possibly a ``ValueType``.

    Returns:
        The underlying value.
    """
    return getattr(value, "value", value)


def _meta(entry: Directive) -> dict[str, object]:
    """Return an entry's user metadata.

    Args:
        entry: Any Beancount directive.

    Returns:
        The metadata without Beancount's ``filename`` and ``lineno`` keys.
    """
    return {
        key: value
        for key, value in (entry.meta or {}).items()
        if key not in {"filename", "lineno"}
    }


def _source(entry: Directive | None) -> Source | None:
    """Return the ledger location of an entry.

    Args:
        entry: A directive, or ``None``.

    Returns:
        The :class:`Source`, or ``None`` when unavailable.
    """
    if entry is None or entry.meta is None:
        return None
    filename = entry.meta.get("filename")
    lineno = entry.meta.get("lineno")
    if filename is None or lineno is None:
        return None
    return Source(str(filename), int(lineno))


def _diag(
    code: str,
    *,
    entry: Directive | None = None,
    **fields: object,
) -> Diagnostic:
    """Build a diagnostic from the registry.

    Args:
        code: The registered diagnostic code.
        entry: The directive the diagnostic refers to, if any.
        **fields: Values for the message template.

    Returns:
        The constructed :class:`Diagnostic`.
    """
    return Diagnostic(
        code=code,
        severity=get_diagnostic(code).severity,
        message=render_message(code, **fields),
        entry=entry,
        source=_source(entry),
    )


def _root(account: str) -> str:
    """Return an account's top-level component.

    Args:
        account: A full account name.

    Returns:
        The first colon-separated component.
    """
    return account.split(":", 1)[0]


def _amount_number(units: Amount | None) -> Decimal | None:
    """Return the numeric part of an amount, when present.

    Beancount types an amount's number as optional; a booked posting always
    carries one, but the type checker cannot know that.

    Args:
        units: A posting's units, or ``None``.

    Returns:
        The number, or ``None`` when there is no amount or no number.
    """
    if units is None or units.number is None:
        return None
    return units.number


def _operating_value(posting: Posting, operating_currency: str) -> Decimal | None:
    """Return a posting's signed value in the operating currency.

    A posting is valued at its Beancount weight, the same value Beancount
    balances a transaction by, so a commodity posting is converted through its
    cost or price: ``5 VTI {200.00 USD}`` counts as ``1000.00 USD``, and
    ``2 VTI @ 210.00 USD`` as ``420.00 USD``. A posting already in the operating
    currency is normally its own weight, but a cost or price on it is honoured
    too. A posting with no units, or whose weight is in a different currency, has
    no operating value.

    Args:
        posting: The posting.
        operating_currency: The single operating currency, or ``""`` when none.

    Returns:
        The signed amount in the operating currency, or ``None`` when the
        posting has no amount or cannot be converted.
    """
    units = posting.units
    if units is None:
        return None
    if not operating_currency:
        return units.number if isinstance(units.number, Decimal) else None
    weight = convert.get_weight(posting)
    if not isinstance(weight.number, Decimal):
        return None
    if weight.currency != operating_currency:
        return None
    return weight.number


# --- Small public helpers ------------------------------------------------------


def month_of(value: date) -> date:
    """Return the first day of the month containing a date.

    Args:
        value: Any date.

    Returns:
        The first day of that month.

    Examples:
        >>> month_of(date(2024, 2, 15))
        datetime.date(2024, 2, 1)
    """
    return value.replace(day=1)


def existing_allocations(
    entries: list[Directive],
) -> dict[tuple[str, date], Decimal]:
    r"""Sum existing ``allocate`` directives by envelope and month.

    ``Custom`` directives have no natural key, so "does this envelope-month
    already have an allocation?" is a semantic, summed question over the
    ledger, not a lookup. The CLI's ``fund`` and the Fava report both ask it.

    Args:
        entries: The ledger entries.

    Returns:
        A mapping from ``(envelope, month)`` to the summed allocation.

    Examples:
        A pool allocation is keyed by the month of its date:

        >>> from beancount.parser import parser
        >>> entries, _errors, _options = parser.parse_string(
        ...     '2024-01-31 custom "envelope" "allocate" "Groceries" 600.00 USD\n'
        ... )
        >>> existing_allocations(entries)
        {('Groceries', datetime.date(2024, 1, 1)): Decimal('600.00')}
    """
    totals: dict[tuple[str, date], Decimal] = {}
    for entry in entries:
        if not isinstance(entry, Custom) or entry.type != "envelope":
            continue
        values = [_unwrap(value) for value in entry.values]
        if len(values) < _ALLOCATE_ARGS or values[0] != "allocate":
            continue
        name, amount = values[1], values[2]
        if not isinstance(name, str) or not isinstance(amount, Amount):
            continue
        key = (name, month_of(entry.date))
        totals[key] = totals.get(key, Decimal(0)) + (amount.number or Decimal(0))
    return totals


def iter_months(start: date, end: date) -> list[date]:
    """List the first day of every month from ``start`` to ``end`` inclusive.

    Args:
        start: Any date; only its month matters.
        end: Any date; only its month matters.

    Returns:
        The first day of each month in range, ascending.

    Examples:
        >>> [day.isoformat() for day in iter_months(date(2024, 1, 15), date(2024, 3, 2))]
        ['2024-01-01', '2024-02-01', '2024-03-01']
    """
    months: list[date] = []
    cursor = month_of(start)
    last = month_of(end)
    while cursor <= last:
        months.append(cursor)
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == _MONTHS_PER_YEAR
            else date(cursor.year, cursor.month + 1, 1)
        )
    return months


def row_for(rows: Sequence[MonthRow], month: date) -> MonthRow:
    """Return a monthly row for any month, projecting outside the range.

    Args:
        rows: The computed rows, ascending by month.
        month: The month to evaluate.

    Returns:
        The row for that month; before the range it is all zeros, and after the
        range it carries the final closing balance forward with no activity.
    """
    target = month_of(month)
    if not rows or target < rows[0].month:
        return MonthRow(target, Decimal(0), Decimal(0), Decimal(0))
    for index, row in enumerate(rows):
        if row.month == target:
            return row
        if row.month > target:
            previous = rows[index - 1]
            return MonthRow(target, previous.closing, Decimal(0), Decimal(0))
    last = rows[-1]
    return MonthRow(target, last.closing, Decimal(0), Decimal(0))


def format_amount(value: Decimal, currency: str = "") -> str:
    """Format an amount for a diagnostic message.

    Args:
        value: The amount.
        currency: The currency code, appended when non-empty.

    Returns:
        The amount with two decimal places, and the currency if given.

    Examples:
        >>> format_amount(Decimal("-500"), "USD")
        '-500.00 USD'
        >>> format_amount(Decimal("0"))
        '0.00'
    """
    text = f"{value:.2f}"
    return f"{text} {currency}" if currency else text


# --- Scanning ------------------------------------------------------------------


def _operating_currency(
    options: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> str:
    """Read the single operating currency from the ledger options.

    Args:
        options: The Beancount options map.
        diagnostics: The list to append a diagnostic to on failure.

    Returns:
        The operating currency, or ``""`` when it is missing or ambiguous.
    """
    value = options.get("operating_currency")
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple) and len(value) == 1:
        return str(value[0])
    diagnostics.append(_diag("E009"))
    return ""


def _scan_accounts(
    entries: list[Directive],
    diagnostics: list[Diagnostic],
) -> dict[str, AccountInfo]:
    """Collect the envelope-relevant metadata of every opened account.

    Args:
        entries: The ledger entries.
        diagnostics: The list to append diagnostics to.

    Returns:
        A mapping from account name to :class:`AccountInfo`.
    """
    accounts: dict[str, AccountInfo] = {}
    for entry in entries:
        if not isinstance(entry, Open):
            continue
        meta = _meta(entry)
        is_cash = False
        marker = meta.get("cash-account")
        if marker is True:
            if _root(entry.account) in _CASH_ROOTS:
                is_cash = True
            else:
                diagnostics.append(_diag("E004", entry=entry, account=entry.account))
        elif marker is not None and marker is not False:
            diagnostics.append(_diag("E004", entry=entry, account=entry.account))
        envelope = meta.get("envelope")
        if envelope is not None and not isinstance(envelope, str):
            diagnostics.append(
                _diag(
                    "E008",
                    entry=entry,
                    detail=f"envelope on {entry.account} must be a string",
                )
            )
            envelope = None
        accounts[entry.account] = AccountInfo(is_cash=is_cash, envelope=envelope)
    return accounts


def _start_date(
    entries: list[Directive],
    diagnostics: list[Diagnostic],
) -> date | None:
    """Find the start date.

    Args:
        entries: The ledger entries.
        diagnostics: The list to append diagnostics to.

    Returns:
        The earliest start date, or ``None`` when there is no start directive.
    """
    starts = [
        entry
        for entry in entries
        if isinstance(entry, Custom) and entry.type == "envelope" and _is_start(entry)
    ]
    if not starts:
        diagnostics.append(_diag("W003"))
        return None
    starts.sort(key=lambda entry: entry.date)
    diagnostics.extend(
        _diag("E008", entry=duplicate, detail="duplicate start directive")
        for duplicate in starts[1:]
    )
    return starts[0].date


def _is_start(entry: Custom) -> bool:
    """Report whether a custom directive is the ``start`` verb.

    Args:
        entry: A custom directive.

    Returns:
        ``True`` when the first value is ``start``.
    """
    return bool(entry.values) and _unwrap(entry.values[0]) == "start"


def _custom_directives(
    entries: list[Directive],
    diagnostics: list[Diagnostic],
) -> list[_RawDirective]:
    """Parse every ``custom "envelope"`` directive, in order.

    Args:
        entries: The ledger entries.
        diagnostics: The list to append diagnostics to.

    Returns:
        The parsed directives, sorted by date (stable for equal dates).
    """
    raw: list[_RawDirective] = []
    for entry in entries:
        if not isinstance(entry, Custom) or entry.type != "envelope":
            continue
        if _is_start(entry):
            continue
        values = [_unwrap(value) for value in entry.values]
        verb = values[0] if values else None
        if not isinstance(verb, str) or not verb:
            diagnostics.append(
                _diag("E008", entry=entry, detail="missing envelope verb")
            )
            continue
        raw.append(
            _RawDirective(
                date=entry.date,
                verb=verb,
                args=tuple(values[1:]),
                meta=_meta(entry),
                entry=entry,
            )
        )
    return raw


# --- Metadata parsing ----------------------------------------------------------


def _as_decimal(value: object) -> Decimal | None:
    """Coerce a metadata value to :class:`~decimal.Decimal`.

    Args:
        value: A metadata value.

    Returns:
        The decimal value, or ``None`` when it is not numeric.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return None


def _parse_monthly(
    value: object,
    name: str,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> Monthly | None:
    """Parse a ``monthly`` metadata value.

    Args:
        value: The raw metadata value.
        name: The envelope or directive name, for the diagnostic.
        entry: The source directive.
        diagnostics: The list to append diagnostics to.

    Returns:
        The parsed value, or ``None`` on error.
    """
    if value is None:
        return None
    if isinstance(value, str):
        match = _AVG_PATTERN.match(value)
        if match is not None and int(match.group(1)) >= 1:
            return value
        diagnostics.append(
            _diag(
                "E008",
                entry=entry,
                detail=f'monthly for {name} must be a number or "avgN"',
            )
        )
        return None
    number = _as_decimal(value)
    if number is None:
        diagnostics.append(
            _diag(
                "E008",
                entry=entry,
                detail=f'monthly for {name} must be a number or "avgN"',
            )
        )
    return number


def _parse_priority(
    value: object,
    name: str,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> Decimal | None:
    """Parse a ``priority`` metadata value.

    Args:
        value: The raw metadata value.
        name: The envelope or directive name, for the diagnostic.
        entry: The source directive.
        diagnostics: The list to append diagnostics to.

    Returns:
        The parsed number, or ``None`` on error.
    """
    if value is None:
        return None
    number = _as_decimal(value)
    if number is None:
        diagnostics.append(
            _diag(
                "E008",
                entry=entry,
                detail=f"priority for {name} must be a number",
            )
        )
    return number


def _parse_target(
    value: object,
    name: str,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> tuple[Decimal, str] | None:
    """Parse a ``target`` metadata value.

    Args:
        value: The raw metadata value, expected to be an amount.
        name: The envelope name, for the diagnostic.
        entry: The source directive.
        diagnostics: The list to append diagnostics to.

    Returns:
        A ``(amount, currency)`` pair, or ``None`` on error.
    """
    if value is None:
        return None
    if isinstance(value, Amount):
        number = _amount_number(value)
        if number is not None:
            return number, value.currency
    diagnostics.append(
        _diag("E008", entry=entry, detail=f"target for {name} must be an amount")
    )
    return None


def _parse_by(
    value: object,
    name: str,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> date | None:
    """Parse a ``by`` metadata value.

    Args:
        value: The raw metadata value, expected to be a date.
        name: The envelope name, for the diagnostic.
        entry: The source directive.
        diagnostics: The list to append diagnostics to.

    Returns:
        The parsed date, or ``None`` on error.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    diagnostics.append(
        _diag("E008", entry=entry, detail=f"by for {name} must be a date")
    )
    return None


# --- Declaration processing ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Declarations:
    """The outcome of processing envelope directives.

    Attributes:
        envelopes: Declared envelopes, in declaration order.
        allocate: ``(date, name, amount)`` allocation events.
        move: ``(date, source, target, amount)`` move events.
    """

    envelopes: tuple[Envelope, ...]
    allocate: tuple[tuple[date, str, Decimal], ...]
    move: tuple[tuple[date, str, str, Decimal], ...]


def _validate_plan(
    plan: Plan,
    name: str,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> None:
    """Report a plan whose metadata has no effect or conflicts.

    Args:
        plan: The resulting plan.
        name: The envelope name.
        entry: The source directive.
        diagnostics: The list to append diagnostics to.
    """
    detail: str | None = None
    if plan.monthly is not None and plan.target is not None:
        detail = "monthly and target are mutually exclusive"
    elif plan.target is not None and plan.by is None:
        detail = "target requires by"
    elif plan.by is not None and plan.target is None:
        detail = "by requires target"
    if detail is not None:
        diagnostics.append(_diag("E010", entry=entry, name=name, detail=detail))


def _handle_open(
    raw: _RawDirective,
    drafts: dict[str, _Draft],
    order: list[str],
    diagnostics: list[Diagnostic],
) -> None:
    """Handle an ``open`` directive.

    Args:
        raw: The parsed directive.
        drafts: The in-progress envelopes, keyed by name.
        order: Declaration order, extended in place.
        diagnostics: The list to append diagnostics to.
    """
    name = raw.args[0] if raw.args else None
    if not isinstance(name, str) or not name:
        diagnostics.append(
            _diag("E008", entry=raw.entry, detail="envelope name must be a string")
        )
        return
    if name == POOL:
        diagnostics.append(
            _diag("E008", entry=raw.entry, detail=f'"{POOL}" is a reserved name')
        )
        return
    if name in drafts:
        diagnostics.append(_diag("E003", entry=raw.entry, name=name))
        return
    target = _parse_target(raw.meta.get("target"), name, raw.entry, diagnostics)
    parent = raw.meta.get("parent")
    if parent is not None and not isinstance(parent, str):
        diagnostics.append(
            _diag(
                "E008",
                entry=raw.entry,
                detail=f"parent for {name} must be a string",
            )
        )
        parent = None
    plan = Plan(
        monthly=_parse_monthly(raw.meta.get("monthly"), name, raw.entry, diagnostics),
        priority=_parse_priority(
            raw.meta.get("priority"), name, raw.entry, diagnostics
        ),
        target=target[0] if target is not None else None,
        target_currency=target[1] if target is not None else None,
        by=_parse_by(raw.meta.get("by"), name, raw.entry, diagnostics),
    )
    _validate_plan(plan, name, raw.entry, diagnostics)
    drafts[name] = _Draft(
        name=name,
        opened=raw.date,
        plan=plan,
        parent=parent,
    )
    order.append(name)


def _handle_plan(
    raw: _RawDirective,
    drafts: dict[str, _Draft],
    diagnostics: list[Diagnostic],
) -> None:
    """Handle a ``plan`` directive.

    Args:
        raw: The parsed directive.
        drafts: The in-progress envelopes, keyed by name.
        diagnostics: The list to append diagnostics to.
    """
    name = raw.args[0] if raw.args else None
    if not isinstance(name, str) or name not in drafts:
        diagnostics.append(_diag("E001", entry=raw.entry, name=name))
        return
    target = _parse_target(raw.meta.get("target"), name, raw.entry, diagnostics)
    drafts[name].plans.append(
        PlanChange(
            date=raw.date,
            monthly=_parse_monthly(
                raw.meta.get("monthly"), name, raw.entry, diagnostics
            ),
            priority=_parse_priority(
                raw.meta.get("priority"), name, raw.entry, diagnostics
            ),
            target=target[0] if target is not None else None,
            target_currency=target[1] if target is not None else None,
            by=_parse_by(raw.meta.get("by"), name, raw.entry, diagnostics),
        )
    )
    _validate_plan(drafts[name].effective(raw.date), name, raw.entry, diagnostics)


def _handle_unplan(
    raw: _RawDirective,
    drafts: dict[str, _Draft],
    diagnostics: list[Diagnostic],
) -> None:
    """Handle an ``unplan`` directive.

    Clears the envelope's plan from the directive's date on, without touching
    its balance or removing it from reports.

    Args:
        raw: The parsed directive.
        drafts: The in-progress envelopes, keyed by name.
        diagnostics: The list to append diagnostics to.
    """
    name = raw.args[0] if raw.args else None
    if not isinstance(name, str) or name not in drafts:
        diagnostics.append(_diag("E001", entry=raw.entry, name=name))
        return
    drafts[name].plans.append(PlanChange(date=raw.date, clears=True))


def _parse_amount(
    value: object,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> Amount | None:
    """Parse a positional amount argument.

    Args:
        value: The raw positional value.
        entry: The source directive.
        diagnostics: The list to append diagnostics to.

    Returns:
        The amount, or ``None`` on error.
    """
    if isinstance(value, Amount):
        return value
    diagnostics.append(
        _diag("E008", entry=entry, detail="amount must be a number and currency")
    )
    return None


def _valid_target(
    name: object,
    drafts: dict[str, _Draft],
    on: date,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> str | None:
    """Validate that an envelope reference names a live envelope.

    Args:
        name: The referenced envelope name.
        drafts: The declared envelopes, keyed by name.
        on: The date of the reference.
        entry: The source directive.
        diagnostics: The list to append diagnostics to.

    Returns:
        The valid name, or ``None`` when the reference is invalid.
    """
    if not isinstance(name, str) or name not in drafts:
        diagnostics.append(_diag("E001", entry=entry, name=name))
        return None
    closed = drafts[name].closed
    if closed is not None and closed < on:
        diagnostics.append(_diag("E003", entry=entry, name=name))
        return None
    return name


def _process_declarations(
    raw_directives: list[_RawDirective],
    start: date,
    operating_currency: str,
    diagnostics: list[Diagnostic],
) -> _Declarations:
    """Process every envelope directive on or after the start date.

    Args:
        raw_directives: The parsed directives, in order.
        start: The start date.
        operating_currency: The single operating currency.
        diagnostics: The list to append diagnostics to.

    Returns:
        The declared envelopes and the allocation and move events.
    """
    drafts: dict[str, _Draft] = {}
    order: list[str] = []
    allocate: list[tuple[date, str, Decimal]] = []
    move: list[tuple[date, str, str, Decimal]] = []
    for raw in raw_directives:
        if raw.date < start and raw.verb in {"open", "allocate", "move"}:
            diagnostics.append(
                _diag(
                    "W002",
                    entry=raw.entry,
                    kind=f'"{raw.verb}" directive',
                    name=_label(raw),
                )
            )
            continue
        if not _directive_currency_ok(raw, operating_currency, diagnostics):
            continue
        if raw.verb == "open":
            _handle_open(raw, drafts, order, diagnostics)
        elif raw.verb == "plan":
            _handle_plan(raw, drafts, diagnostics)
        elif raw.verb == "unplan":
            _handle_unplan(raw, drafts, diagnostics)
        elif raw.verb == "allocate":
            _handle_allocate(raw, drafts, allocate, diagnostics)
        elif raw.verb == "move":
            _handle_move(raw, drafts, move, diagnostics)
        elif raw.verb == "close":
            _handle_close(raw, drafts, diagnostics)
        else:
            diagnostics.append(
                _diag("E008", entry=raw.entry, detail=f"unknown verb {raw.verb!r}")
            )
    return _Declarations(
        envelopes=tuple(drafts[name].freeze() for name in order),
        allocate=tuple(allocate),
        move=tuple(move),
    )


def _check_currency(
    amount: Amount,
    entry: Directive,
    operating_currency: str,
    diagnostics: list[Diagnostic],
) -> bool:
    """Check a directive amount against the operating currency.

    Args:
        amount: The amount to check.
        entry: The source directive.
        operating_currency: The single operating currency.
        diagnostics: The list to append diagnostics to.

    Returns:
        ``True`` when the amount is in the operating currency (or there is
        none).
    """
    if not operating_currency or amount.currency == operating_currency:
        return True
    diagnostics.append(
        _diag(
            "E006",
            entry=entry,
            currency=amount.currency,
            date=entry.date.isoformat(),
            operating_currency=operating_currency,
        )
    )
    return False


def _directive_currency_ok(
    raw: _RawDirective,
    operating_currency: str,
    diagnostics: list[Diagnostic],
) -> bool:
    """Check every amount in a directive against the operating currency.

    Args:
        raw: The parsed directive.
        operating_currency: The single operating currency.
        diagnostics: The list to append diagnostics to.

    Returns:
        ``True`` when every amount is in the operating currency.
    """
    amounts = [arg for arg in raw.args if isinstance(arg, Amount)]
    target = raw.meta.get("target")
    if isinstance(target, Amount):
        amounts.append(target)
    return all(
        _check_currency(amount, raw.entry, operating_currency, diagnostics)
        for amount in amounts
    )


def _label(raw: _RawDirective) -> str:
    """Return a human label for a directive, for diagnostics.

    Args:
        raw: The parsed directive.

    Returns:
        The first argument when it is a string, else the verb.
    """
    first = raw.args[0] if raw.args else None
    return first if isinstance(first, str) else raw.verb


def _handle_allocate(
    raw: _RawDirective,
    drafts: dict[str, _Draft],
    allocate: list[tuple[date, str, Decimal]],
    diagnostics: list[Diagnostic],
) -> None:
    """Handle an ``allocate`` directive.

    Args:
        raw: The parsed directive.
        drafts: The declared envelopes, keyed by name.
        allocate: The allocation events, extended in place.
        diagnostics: The list to append diagnostics to.
    """
    name = raw.args[0] if raw.args else None
    amount = _parse_amount(
        raw.args[1] if len(raw.args) > 1 else None, raw.entry, diagnostics
    )
    target = _valid_target(name, drafts, raw.date, raw.entry, diagnostics)
    number = _amount_number(amount)
    if target is None or number is None:
        return
    allocate.append((raw.date, target, number))


def _handle_move(
    raw: _RawDirective,
    drafts: dict[str, _Draft],
    move: list[tuple[date, str, str, Decimal]],
    diagnostics: list[Diagnostic],
) -> None:
    """Handle a ``move`` directive.

    Args:
        raw: The parsed directive.
        drafts: The declared envelopes, keyed by name.
        move: The move events, extended in place.
        diagnostics: The list to append diagnostics to.
    """
    source = raw.args[0] if raw.args else None
    dest = raw.args[1] if len(raw.args) > 1 else None
    amount = _parse_amount(
        raw.args[2] if len(raw.args) >= _MOVE_ARGS else None, raw.entry, diagnostics
    )
    from_name = _valid_target(source, drafts, raw.date, raw.entry, diagnostics)
    to_name = _valid_target(dest, drafts, raw.date, raw.entry, diagnostics)
    number = _amount_number(amount)
    if from_name is None or to_name is None or number is None:
        return
    move.append((raw.date, from_name, to_name, number))


def _handle_close(
    raw: _RawDirective,
    drafts: dict[str, _Draft],
    diagnostics: list[Diagnostic],
) -> None:
    """Handle a ``close`` directive.

    Args:
        raw: The parsed directive.
        drafts: The declared envelopes, keyed by name.
        diagnostics: The list to append diagnostics to.
    """
    name = raw.args[0] if raw.args else None
    if not isinstance(name, str) or name not in drafts:
        diagnostics.append(_diag("E001", entry=raw.entry, name=name))
        return
    if drafts[name].closed is not None:
        diagnostics.append(_diag("E003", entry=raw.entry, name=name))
        return
    drafts[name].closed = raw.date


# --- Transaction processing ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Effects:
    """The effects gathered from transactions.

    Attributes:
        activity: ``(envelope or pool, month, amount)`` activity events.
        cash: ``(date, amount)`` cash changes.
        resolved: ``(id(posting), envelope)`` resolutions, when requested.
    """

    activity: tuple[tuple[str, date, Decimal], ...]
    cash: tuple[tuple[date, Decimal], ...]
    resolved: tuple[tuple[int, str], ...] = ()
    details: tuple[ActivityEvent, ...] = ()


def _resolve_envelope(
    posting_meta: dict[str, object],
    transaction_meta: dict[str, object],
    account: str,
    info: AccountInfo | None,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> str:
    """Resolve a non-cash posting's destination envelope or the pool.

    A transaction-level override applies only to postings whose account names a
    real envelope; an account set to the reserved value ``pool`` stays in the
    pool regardless, so an override never redirects a posting that goes to the
    pool.

    Args:
        posting_meta: The posting's metadata.
        transaction_meta: The transaction's metadata.
        account: The posting's account.
        info: The account's :class:`AccountInfo`, if opened.
        entry: The source transaction.
        diagnostics: The list to append diagnostics to.

    Returns:
        An envelope name or :data:`POOL`.
    """
    posting_env = posting_meta.get("envelope")
    if posting_env is not None:
        if isinstance(posting_env, str):
            return posting_env
        diagnostics.append(
            _diag(
                "E008",
                entry=entry,
                detail=f"envelope on posting {account} must be a string",
            )
        )
        return POOL
    if info is None or info.envelope is None:
        return _default_destination(account, entry, diagnostics)
    if info.envelope == POOL:
        return POOL
    transaction_env = transaction_meta.get("envelope")
    if isinstance(transaction_env, str):
        return transaction_env
    return info.envelope


def _default_destination(
    account: str,
    entry: Directive,
    diagnostics: list[Diagnostic],
) -> str:
    """Return the pool for an account with no envelope of its own.

    Income and Equity accounts resolve to the pool; any other account is an
    error (``E002``) and also resolves to the pool.

    Args:
        account: The posting's account.
        entry: The source transaction.
        diagnostics: The list to append diagnostics to.

    Returns:
        The pool.
    """
    if _root(account) in _INCOME_OR_EQUITY:
        return POOL
    diagnostics.append(_diag("E002", entry=entry, account=account))
    return POOL


@dataclass(frozen=True, slots=True)
class _Valuation:
    """The operating currency and the options used to value and tolerate.

    Attributes:
        currency: The single operating currency, or ``""`` when none.
        options: The ledger options map, for the inferred rounding tolerance.
    """

    currency: str
    options: Mapping[str, object]


def _process_transactions(
    entries: list[Directive],
    accounts: dict[str, AccountInfo],
    declarations: _Declarations,
    start: date,
    valuation: _Valuation,
    diagnostics: list[Diagnostic],
    *,
    collect_resolved: bool = False,
    collect_activity: bool = False,
) -> _Effects:
    """Gather envelope effects and cash changes from cash-moving transactions.

    Args:
        entries: The ledger entries.
        accounts: The account metadata.
        declarations: The declared envelopes.
        start: The start date.
        valuation: The operating currency and tolerance options.
        diagnostics: The list to append diagnostics to.
        collect_resolved: Whether to record each posting's resolved envelope.
        collect_activity: Whether to record per-posting activity detail.

    Returns:
        The gathered :class:`_Effects`.
    """
    operating_currency = valuation.currency
    activity: list[tuple[str, date, Decimal]] = []
    cash: list[tuple[date, Decimal]] = []
    resolved: list[tuple[int, str]] = []
    details: list[ActivityEvent] = []
    for entry in entries:
        if not isinstance(entry, Transaction) or entry.date < start:
            continue
        cash_postings = _cash_postings(entry, accounts)
        if not cash_postings:
            continue
        if _has_foreign_currency(entry, operating_currency, diagnostics):
            continue
        cash_total = Decimal(0)
        for posting in cash_postings:
            cash_total += _operating_value(posting, operating_currency) or Decimal(0)
        cash.append((entry.date, cash_total))
        _absorb_rounding(entry, valuation, activity)
        cash_ids = {id(posting) for posting in cash_postings}
        txn_meta = _meta(entry)
        payee, narration = _txn_parts(entry)
        for posting in entry.postings:
            number = _operating_value(posting, operating_currency)
            if number is None or id(posting) in cash_ids:
                continue
            dest = _resolve_destination(
                posting, txn_meta, accounts, declarations, entry, diagnostics
            )
            if collect_resolved:
                resolved.append((id(posting), dest))
            amount = -number
            if collect_activity:
                details.append(
                    ActivityEvent(
                        dest,
                        entry.date,
                        amount,
                        compare.hash_entry(entry),
                        payee,
                        narration,
                        _posting_narration(posting),
                    )
                )
            activity.append((dest, entry.date, amount))
    return _Effects(
        activity=tuple(activity),
        cash=tuple(cash),
        resolved=tuple(resolved),
        details=tuple(details),
    )


def _txn_parts(entry: Transaction) -> tuple[str | None, str | None]:
    """Return a transaction's payee and narration, trimmed.

    Args:
        entry: The transaction.

    Returns:
        The payee and narration, each ``None`` when it is empty.

    Examples:
        >>> _txn_parts(
        ...     Transaction(
        ...         None, date(2024, 1, 1), "*", "Amazon", "Order",
        ...         frozenset(), frozenset(), (),
        ...     )
        ... )
        ('Amazon', 'Order')
        >>> _txn_parts(
        ...     Transaction(
        ...         None, date(2024, 1, 1), "*", None, "  ",
        ...         frozenset(), frozenset(), (),
        ...     )
        ... )
        (None, None)
    """
    payee = (entry.payee or "").strip() or None
    narration = (entry.narration or "").strip() or None
    return payee, narration


def _posting_narration(posting: Posting) -> str | None:
    """Return a posting's narration metadata, when it has one.

    This surfaces the ``narration`` metadata convention used by the
    ``autobean.narration`` plugin: a line item's own description, which can
    differ from its transaction's narration. It is read, not validated.

    Args:
        posting: The posting.

    Returns:
        The trimmed narration, or ``None`` when it is absent, empty or not a
        string.

    Examples:
        >>> _posting_narration(Posting("Expenses:E", None, None, None, None, {"narration": "Escrow"}))
        'Escrow'
        >>> _posting_narration(Posting("Expenses:E", None, None, None, None, {"narration": "  "})) is None
        True
        >>> _posting_narration(Posting("Expenses:E", None, None, None, None, None)) is None
        True
    """
    meta = posting.meta
    value = meta.get("narration") if meta else None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _transaction_residual(entry: Transaction, operating_currency: str) -> Decimal:
    """Return the sum of a transaction's postings in the operating currency.

    Every posting is valued at its Beancount weight, so a transaction Beancount
    considers balanced sums to zero, up to Beancount's inferred tolerance.

    Args:
        entry: The transaction.
        operating_currency: The single operating currency.

    Returns:
        The signed residual: zero when the postings balance exactly.
    """
    residual = Decimal(0)
    for posting in entry.postings:
        value = _operating_value(posting, operating_currency)
        if value is not None:
            residual += value
    return residual


def _tolerance_options(options: Mapping[str, object]) -> Mapping[str, object]:
    """Return the option subset Beancount needs to infer a tolerance.

    :func:`beancount.core.interpolate.infer_tolerances` reads three options
    directly; a hand-built options map may omit them, so they are defaulted here.

    Args:
        options: The ledger options map.

    Returns:
        A mapping with the tolerance options always present.
    """
    return {
        "infer_tolerance_from_cost": options.get("infer_tolerance_from_cost", False),
        "tolerance_multiplier": options.get("tolerance_multiplier", Decimal("0.5")),
        "inferred_tolerance_default": options.get("inferred_tolerance_default", {}),
    }


def _absorb_rounding(
    entry: Transaction,
    valuation: _Valuation,
    activity: list[tuple[str, date, Decimal]],
) -> None:
    """Fold a sub-tolerance residual into the pool so the invariant stays exact.

    Beancount accepts a small imbalance, sized by ``inferred_tolerance_default``
    and the precision of the numbers involved. The budget cannot ignore it, so a
    residual within that tolerance is treated as rounding and routed to the pool —
    the same place an ``Equity:Rounding-Errors`` posting would land. A residual
    beyond the tolerance is left alone: Beancount reports the imbalance and the
    month-end invariant (``E007``) reflects it until it is booked.

    Args:
        entry: The transaction.
        valuation: The operating currency and tolerance options.
        activity: The activity events, extended in place.
    """
    operating_currency = valuation.currency
    if not operating_currency:
        return
    residual = _transaction_residual(entry, operating_currency)
    if residual == 0:
        return
    tolerance = interpolate.infer_tolerances(
        entry.postings, _tolerance_options(valuation.options)
    ).get(operating_currency, Decimal(0))
    if abs(residual) <= tolerance:
        activity.append((POOL, entry.date, residual))


def _cash_postings(
    entry: Transaction,
    accounts: dict[str, AccountInfo],
) -> list[Posting]:
    """Return a transaction's postings to cash accounts.

    Detection is currency-agnostic: a cash posting in a foreign currency is
    still returned so the transaction is checked and reported rather than
    silently skipped.

    Args:
        entry: The transaction.
        accounts: The account metadata.

    Returns:
        The postings to cash accounts, in ledger order. Auto-balanced postings
        with no amount are excluded.
    """
    found: list[Posting] = []
    for posting in entry.postings:
        info = accounts.get(posting.account)
        number = _amount_number(posting.units)
        if info is not None and info.is_cash and number is not None:
            found.append(posting)
    return found


def _has_foreign_currency(
    entry: Transaction,
    operating_currency: str,
    diagnostics: list[Diagnostic],
) -> bool:
    """Check a transaction for an unconvertible foreign currency.

    A posting in a foreign currency is accepted when its cost or price converts
    it to the operating currency — a security bought with USD is an
    operating-currency posting at cost, not a foreign one. Any posting whose
    value in the operating currency cannot be determined is reported, including
    an operating-currency posting whose price or cost is in another currency.

    Args:
        entry: The transaction.
        operating_currency: The single operating currency.
        diagnostics: The list to append diagnostics to.

    Returns:
        ``True`` when the transaction uses an unconvertible foreign currency.
    """
    if not operating_currency:
        return False
    for posting in entry.postings:
        units = posting.units
        if units is None or units.number is None:
            continue
        if _operating_value(posting, operating_currency) is not None:
            continue
        weight = convert.get_weight(posting)
        diagnostics.append(
            _diag(
                "E006",
                entry=entry,
                currency=weight.currency,
                date=entry.date.isoformat(),
                operating_currency=operating_currency,
            )
        )
        return True
    return False


def _resolve_destination(
    posting: Posting,
    txn_meta: dict[str, object],
    accounts: dict[str, AccountInfo],
    declarations: _Declarations,
    entry: Transaction,
    diagnostics: list[Diagnostic],
) -> str:
    """Resolve a posting to a live envelope name or the pool.

    Effects that name an unknown envelope, or one used before it was opened or
    after it was closed, are redirected to the pool and reported.

    Args:
        posting: The posting.
        txn_meta: The transaction's metadata.
        accounts: The account metadata.
        declarations: The declared envelopes.
        entry: The source transaction.
        diagnostics: The list to append diagnostics to.

    Returns:
        A live envelope name or :data:`POOL`.
    """
    info = accounts.get(posting.account)
    name = _resolve_envelope(
        posting.meta or {}, txn_meta, posting.account, info, entry, diagnostics
    )
    if name == POOL:
        return POOL
    live = {envelope.name: envelope for envelope in declarations.envelopes}
    envelope = live.get(name)
    if envelope is None:
        diagnostics.append(_diag("E001", entry=entry, name=name))
        return POOL
    if entry.date < envelope.opened:
        diagnostics.append(_diag("E003", entry=entry, name=name))
        return POOL
    if envelope.closed is not None and entry.date > envelope.closed:
        diagnostics.append(_diag("E003", entry=entry, name=name))
        return POOL
    return name


# --- Aggregation ---------------------------------------------------------------


def _by_month(events: list[tuple[date, Decimal]]) -> dict[date, Decimal]:
    """Sum ``(date, amount)`` events by month.

    Args:
        events: The dated events.

    Returns:
        A mapping from month to the summed amount.
    """
    totals: dict[date, Decimal] = defaultdict(Decimal)
    for when, amount in events:
        totals[month_of(when)] += amount
    return totals


def _rows(
    months: list[date],
    allocated: dict[date, Decimal],
    activity: dict[date, Decimal],
    opening: Decimal = Decimal(0),
) -> tuple[MonthRow, ...]:
    """Build monthly rows with carryover.

    Args:
        months: The months in range.
        allocated: Allocated amounts by month.
        activity: Activity amounts by month.
        opening: The balance carried into the first month.

    Returns:
        One :class:`MonthRow` per month.
    """
    rows: list[MonthRow] = []
    carried = opening
    for month in months:
        row = MonthRow(
            month=month,
            opening=carried,
            allocated=allocated.get(month, Decimal(0)),
            activity=activity.get(month, Decimal(0)),
        )
        rows.append(row)
        carried = row.closing
    return tuple(rows)


# --- Entry point ---------------------------------------------------------------


def compute(
    entries: Sequence[Directive],
    options: Mapping[str, object],
    *,
    collect_resolved: bool = False,
    collect_activity: bool = False,
) -> BudgetResult:
    """Compute envelope balances, the pool and the cash balance.

    Args:
        entries: The ledger entries.
        options: The Beancount options map, used for ``operating_currency``.
        collect_resolved: Whether to record each non-cash posting's resolved
            envelope in :attr:`BudgetResult.resolved`.
        collect_activity: Whether to record per-posting activity detail in
            :attr:`BudgetResult.activity`.

    Returns:
        The :class:`BudgetResult`, including every diagnostic.

    Examples:
        >>> [d.code for d in compute([], {}).diagnostics]
        ['E009', 'W003']
    """
    diagnostics: list[Diagnostic] = []
    entries = list(entries)
    operating_currency = _operating_currency(options, diagnostics)
    accounts = _scan_accounts(entries, diagnostics)
    start = _start_date(entries, diagnostics)
    if start is None:
        return BudgetResult(
            start=None,
            operating_currency=operating_currency,
            months=(),
            envelopes=(),
            pool=(),
            cash=(),
            diagnostics=tuple(_sorted(diagnostics)),
        )

    raw = _custom_directives(entries, diagnostics)
    declarations = _process_declarations(raw, start, operating_currency, diagnostics)
    effects = _process_transactions(
        entries,
        accounts,
        declarations,
        start,
        _Valuation(operating_currency, options),
        diagnostics,
        collect_resolved=collect_resolved,
        collect_activity=collect_activity,
    )
    pre_start_cash = _pre_start_cash(entries, accounts, start, operating_currency)
    return _build_result(
        start=start,
        operating_currency=operating_currency,
        declarations=declarations,
        effects=effects,
        pre_start_cash=pre_start_cash,
        diagnostics=diagnostics,
    )


def _pre_start_cash(
    entries: list[Directive],
    accounts: dict[str, AccountInfo],
    start: date,
    operating_currency: str,
) -> Decimal:
    """Sum the cash balance on the day before the start date.

    Args:
        entries: The ledger entries.
        accounts: The account metadata.
        start: The start date.
        operating_currency: The single operating currency.

    Returns:
        The cash held before the start date.
    """
    total = Decimal(0)
    for entry in entries:
        if not isinstance(entry, Transaction) or entry.date >= start:
            continue
        for posting in entry.postings:
            info = accounts.get(posting.account)
            if info is None or not info.is_cash:
                continue
            value = _operating_value(posting, operating_currency)
            if value is None:
                continue
            total += value
    return total


def _build_result(
    *,
    start: date,
    operating_currency: str,
    declarations: _Declarations,
    effects: _Effects,
    pre_start_cash: Decimal,
    diagnostics: list[Diagnostic],
) -> BudgetResult:
    """Assemble the result from the gathered events.

    Args:
        start: The start date.
        operating_currency: The single operating currency.
        declarations: The declared envelopes and allocation/move events.
        effects: The activity, cash, resolution and detail events.
        pre_start_cash: The cash held before the start date.
        diagnostics: The list to append diagnostics to.

    Returns:
        The assembled :class:`BudgetResult`.
    """
    activity = effects.activity
    cash = effects.cash
    allocation_events: list[tuple[date, Decimal]] = [
        (when, amount) for when, _name, amount in declarations.allocate
    ]
    pool_alloc: list[tuple[date, Decimal]] = list(allocation_events)
    env_alloc: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for when, name, amount in declarations.allocate:
        env_alloc[name].append((when, amount))
    for when, source, dest, amount in declarations.move:
        env_alloc[source].append((when, -amount))
        env_alloc[dest].append((when, amount))

    env_activity: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    pool_activity: list[tuple[date, Decimal]] = []
    for dest, when, amount in activity:
        if dest == POOL:
            pool_activity.append((when, amount))
        else:
            env_activity[dest].append((when, amount))

    months = _month_range(start, declarations, activity, cash)
    pool_rows = _rows(
        months,
        allocated={m: -v for m, v in _by_month(pool_alloc).items()},
        activity=_by_month(pool_activity),
        opening=pre_start_cash,
    )
    envelope_reports = tuple(
        EnvelopeReport(
            envelope=envelope,
            rows=_rows(
                months,
                allocated=_by_month(env_alloc.get(envelope.name, [])),
                activity=_by_month(env_activity.get(envelope.name, [])),
            ),
        )
        for envelope in declarations.envelopes
    )

    cash_rows = _cash_rows(months, cash, pre_start_cash)
    _check_invariant(
        pool_rows, envelope_reports, cash_rows, operating_currency, diagnostics
    )
    _check_envelope_closes(
        declarations, env_activity, env_alloc, operating_currency, diagnostics
    )
    _check_pool_floor(
        start,
        pre_start_cash,
        pool_activity,
        pool_alloc,
        operating_currency,
        diagnostics,
    )
    return BudgetResult(
        start=start,
        operating_currency=operating_currency,
        months=tuple(months),
        envelopes=envelope_reports,
        pool=pool_rows,
        cash=cash_rows,
        diagnostics=tuple(_sorted(diagnostics)),
        resolved=effects.resolved,
        activity=effects.details,
        pre_start_cash=pre_start_cash,
    )


def _month_range(
    start: date,
    declarations: _Declarations,
    activity: tuple[tuple[str, date, Decimal], ...],
    cash: tuple[tuple[date, Decimal], ...],
) -> list[date]:
    """Determine the span of months to report.

    Args:
        start: The start date.
        declarations: The declared envelopes and allocation/move events.
        activity: The validated activity events.
        cash: The cash changes.

    Returns:
        The months in range, ascending.
    """
    dates = [start]
    dates.extend(when for _dest, when, _amount in activity)
    dates.extend(when for when, _name, _amount in declarations.allocate)
    dates.extend(when for when, _source, _dest, _amount in declarations.move)
    dates.extend(when for when, _amount in cash)
    dates.extend(
        envelope.closed for envelope in declarations.envelopes if envelope.closed
    )
    return iter_months(start, max(dates))


def _cash_rows(
    months: list[date],
    cash: tuple[tuple[date, Decimal], ...],
    pre_start_cash: Decimal,
) -> tuple[CashRow, ...]:
    """Build the month-end cash balance rows.

    Args:
        months: The months in range.
        cash: The ``(date, amount)`` cash changes.
        pre_start_cash: The cash held before the start date.

    Returns:
        One :class:`CashRow` per month.
    """
    by_month = _by_month(list(cash))
    rows: list[CashRow] = []
    balance = pre_start_cash
    for month in months:
        balance += by_month.get(month, Decimal(0))
        rows.append(CashRow(month=month, balance=balance))
    return tuple(rows)


def _check_invariant(
    pool_rows: tuple[MonthRow, ...],
    envelopes: tuple[EnvelopeReport, ...],
    cash_rows: tuple[CashRow, ...],
    operating_currency: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Check that the pool plus envelopes equals cash at each month end.

    Args:
        pool_rows: The pool's monthly rows.
        envelopes: The envelope reports.
        cash_rows: The month-end cash balances.
        operating_currency: The currency, for the message.
        diagnostics: The list to append diagnostics to.
    """
    for pool_row, cash_row in zip(pool_rows, cash_rows, strict=True):
        actual = pool_row.closing + sum(
            (report.closing_at(pool_row.month) for report in envelopes), Decimal(0)
        )
        if actual != cash_row.balance:
            diagnostics.append(
                _diag(
                    "E007",
                    month=pool_row.month.strftime("%Y-%m"),
                    actual=format_amount(actual, operating_currency),
                    expected=format_amount(cash_row.balance, operating_currency),
                    difference=format_amount(
                        actual - cash_row.balance, operating_currency
                    ),
                )
            )


def _check_envelope_closes(
    declarations: _Declarations,
    env_activity: dict[str, list[tuple[date, Decimal]]],
    env_alloc: dict[str, list[tuple[date, Decimal]]],
    operating_currency: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Check that every closed envelope holds a zero balance.

    Args:
        declarations: The declared envelopes.
        env_activity: Activity events by envelope.
        env_alloc: Allocation and move events by envelope.
        operating_currency: The currency, for the message.
        diagnostics: The list to append diagnostics to.
    """
    for envelope in declarations.envelopes:
        if envelope.closed is None:
            continue
        balance = sum(
            (
                amount
                for when, amount in (
                    env_activity.get(envelope.name, [])
                    + env_alloc.get(envelope.name, [])
                )
                if when <= envelope.closed
            ),
            Decimal(0),
        )
        if balance != 0:
            diagnostics.append(
                _diag(
                    "E005",
                    name=envelope.name,
                    balance=format_amount(balance, operating_currency),
                )
            )


def _check_pool_floor(
    start: date,
    pre_start_cash: Decimal,
    pool_activity: list[tuple[date, Decimal]],
    pool_alloc: list[tuple[date, Decimal]],
    operating_currency: str,
    diagnostics: list[Diagnostic],
) -> None:
    """Warn when the pool goes negative on any day.

    Args:
        start: The start date.
        pre_start_cash: The pool's opening balance.
        pool_activity: Pool activity events.
        pool_alloc: Allocation events, which leave the pool.
        operating_currency: The currency, for the message.
        diagnostics: The list to append diagnostics to.
    """
    activity_by_date: dict[date, Decimal] = defaultdict(Decimal)
    for when, amount in pool_activity:
        activity_by_date[when] += amount
    alloc_by_date: dict[date, Decimal] = defaultdict(Decimal)
    for when, amount in pool_alloc:
        alloc_by_date[when] += amount

    dates = sorted({start, *activity_by_date, *alloc_by_date})
    balance = pre_start_cash
    first_negative: date | None = None
    lowest = balance
    for when in dates:
        balance += activity_by_date.get(when, Decimal(0))
        if balance < 0 and first_negative is None:
            first_negative, lowest = when, balance
        elif balance < 0:
            lowest = min(lowest, balance)
        balance -= alloc_by_date.get(when, Decimal(0))
        if balance < 0 and first_negative is None:
            first_negative, lowest = when, balance
        elif balance < 0:
            lowest = min(lowest, balance)
    if first_negative is not None:
        diagnostics.append(
            _diag(
                "W001",
                date=first_negative.isoformat(),
                balance=format_amount(lowest, operating_currency),
            )
        )


def _sorted(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """Sort diagnostics deterministically.

    Args:
        diagnostics: The diagnostics to sort.

    Returns:
        The diagnostics, errors before warnings, then by code and location.
    """
    return sorted(
        diagnostics,
        key=lambda diagnostic: (
            diagnostic.severity != Severity.ERROR,
            diagnostic.code,
            diagnostic.source.filename if diagnostic.source else "",
            diagnostic.source.lineno if diagnostic.source else 0,
        ),
    )
