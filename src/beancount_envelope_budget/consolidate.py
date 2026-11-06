"""Pure planning for editing a ledger's ``allocate`` directives.

Two operations share this module because they share the same scan.

As a ledger grows, an envelope is often funded more than once in a month, which
scatters its history across many ``allocate`` directives. Consolidating merges
those into a single directive per envelope-month, dated the last day of the
month (or today, for the current month), so the history reads cleanly. The net
amount is unchanged, so every balance, the pool and the invariant are unchanged.

Clearing is the inverse: deleting every ``allocate`` directive in a month (or
matching an envelope), so the month can be funded again from scratch. The funds
return to the pool.

This module decides *what* to change and performs no I/O: it scans the ledger's
``allocate`` directives, groups them, and returns the source edits — a
replacement, a deletion, or both. The CLI and the Fava report apply those edits
through their own writers, so the planning is shared and testable on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from beancount.core.amount import Amount
from beancount.core.data import Custom

from beancount_envelope_budget import actions
from beancount_envelope_budget.core import month_of

if TYPE_CHECKING:
    import re
    from collections.abc import Sequence

    from beancount.core.data import Directive

__all__ = [
    "ClearPlan",
    "ConsolidationGroup",
    "ConsolidationPlan",
    "DirectiveEdit",
    "directive_day",
    "plan_clear",
    "plan_consolidation",
]

#: The number of values an ``allocate`` directive carries, verb included.
_ALLOCATE_ARGS = 3

#: An envelope-month needs at least this many directives to be worth merging.
_MIN_DIRECTIVES = 2
_DECEMBER = 12


@dataclass(frozen=True, slots=True)
class DirectiveEdit:
    """One source change for a consolidation or a clear.

    Attributes:
        filename: The file the directive lives in.
        lineno: The directive's one-based line number.
        entry: The directive to write in its place, or ``None`` to delete it.
    """

    filename: str
    lineno: int
    entry: Custom | None


@dataclass(frozen=True, slots=True)
class ConsolidationGroup:
    """One envelope-month that was merged.

    Attributes:
        envelope: The envelope name.
        month: The first day of the month.
        amount: The net amount, the sum of the merged directives.
        directives: How many directives became this one (or, for a zero net,
            how many were removed).
    """

    envelope: str
    month: date
    amount: Decimal
    directives: int


@dataclass(frozen=True, slots=True)
class ConsolidationPlan:
    """The edits a consolidation would make.

    Attributes:
        edits: The source changes, in no particular order.
        groups: The envelope-months merged.
        skipped: A description of each envelope-month left alone because one of
            its directives has no source location to edit.
    """

    edits: tuple[DirectiveEdit, ...]
    groups: tuple[ConsolidationGroup, ...]
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClearPlan:
    """The edits a clear would make.

    Attributes:
        edits: The source deletions, one per matching directive.
        envelopes: The distinct envelopes cleared, in first-seen order.
        skipped: A description of each directive left alone because it has no
            source location to edit.
    """

    edits: tuple[DirectiveEdit, ...]
    envelopes: tuple[str, ...]
    skipped: tuple[str, ...] = ()


def directive_day(month: date, today: date) -> date:
    """Return the date a consolidated directive for a month is dated.

    The current month dates today, so a directive is not pushed into the future;
    any other month dates its last day.

    Args:
        month: Any date within the month.
        today: The current date.

    Returns:
        The date to use: today for the current month, else the month's last day.

    Examples:
        >>> directive_day(date(2024, 1, 15), date(2024, 1, 10))
        datetime.date(2024, 1, 10)
        >>> directive_day(date(2024, 1, 15), date(2024, 3, 3))
        datetime.date(2024, 1, 31)
    """
    first = month_of(month)
    if first == month_of(today):
        return today
    if first.month == _DECEMBER:
        return date(first.year, _DECEMBER, 31)
    return date(first.year, first.month + 1, 1) - timedelta(days=1)


def plan_consolidation(
    entries: Sequence[Directive],
    *,
    currency: str,
    today: date,
    envelope: re.Pattern[str] | None = None,
    month: date | None = None,
) -> ConsolidationPlan:
    r"""Plan the consolidation of ``allocate`` directives.

    Only envelope-months with **two or more** directives are merged; a month
    already holding a single directive is left untouched. Within a group the
    latest-located directive is rewritten with the net amount and re-dated by
    :func:`directive_day`; the others are deleted. A group whose net is zero
    keeps no directive at all — it is deleted rather than written as a no-op.

    Args:
        entries: The ledger entries.
        currency: The operating currency, for the merged amount.
        today: The current date, so the current month dates correctly.
        envelope: An optional regex; only matching envelopes are consolidated.
        month: An optional month; only that month is consolidated.

    Returns:
        The :class:`ConsolidationPlan`.

    Examples:
        >>> from beancount.parser import parser
        >>> source = (
        ...     '2024-01-05 custom "envelope" "allocate" "Groceries" 100.00 USD\n'
        ...     '2024-01-20 custom "envelope" "allocate" "Groceries" 250.00 USD\n'
        ... )
        >>> entries, _errors, _options = parser.parse_string(source)
        >>> plan = plan_consolidation(entries, currency="USD", today=date(2024, 2, 1))
        >>> [(g.envelope, g.month.isoformat(), str(g.amount)) for g in plan.groups]
        [('Groceries', '2024-01-01', '350.00')]
        >>> [edited.entry is not None for edited in plan.edits]
        [True, False]
    """
    groups: dict[tuple[str, date], list[_Located]] = {}
    for located in _allocate_directives(entries):
        if envelope is not None and not envelope.search(located.envelope):
            continue
        if month is not None and located.month != month_of(month):
            continue
        groups.setdefault((located.envelope, located.month), []).append(located)

    edits: list[DirectiveEdit] = []
    merged: list[ConsolidationGroup] = []
    skipped: list[str] = []
    for (name, when), members in groups.items():
        if len(members) < _MIN_DIRECTIVES:
            continue
        locatable = [member for member in members if member.located]
        if len(locatable) != len(members):
            skipped.append(
                f"{name} {when:%Y-%m}: {len(members) - len(locatable)} of "
                f"{len(members)} directives have no source location"
            )
            continue
        net = sum((member.amount for member in members), Decimal(0))
        survivor = max(
            members,
            key=lambda member: (member.entry.date, member.filename, member.lineno),
        )
        day = directive_day(when, today)
        if net == 0:
            edits.extend(
                DirectiveEdit(member.filename, member.lineno, None)
                for member in members
            )
        else:
            replacement = actions.allocate(name, net, day, currency)
            edits.append(DirectiveEdit(survivor.filename, survivor.lineno, replacement))
            edits.extend(
                DirectiveEdit(member.filename, member.lineno, None)
                for member in members
                if member is not survivor
            )
        merged.append(ConsolidationGroup(name, when, net, len(members)))
    return ConsolidationPlan(tuple(edits), tuple(merged), tuple(skipped))


def plan_clear(
    entries: Sequence[Directive],
    *,
    envelope: re.Pattern[str] | None = None,
    month: date | None = None,
) -> ClearPlan:
    r"""Plan the deletion of ``allocate`` directives.

    Every matching directive with a source location is deleted; one without a
    location is reported in ``skipped`` and left alone. With neither filter the
    whole ledger is cleared.

    Args:
        entries: The ledger entries.
        envelope: An optional regex; only matching envelopes are cleared.
        month: An optional month; only that month is cleared.

    Returns:
        The :class:`ClearPlan`.

    Examples:
        >>> from beancount.parser import parser
        >>> source = (
        ...     '2024-01-05 custom "envelope" "allocate" "Groceries" 100.00 USD\n'
        ...     '2024-02-05 custom "envelope" "allocate" "Groceries" 50.00 USD\n'
        ... )
        >>> entries, _errors, _options = parser.parse_string(source)
        >>> plan = plan_clear(entries, month=date(2024, 1, 1))
        >>> [(edit.lineno, edit.entry) for edit in plan.edits]
        [(1, None)]
        >>> plan.envelopes
        ('Groceries',)
    """
    edits: list[DirectiveEdit] = []
    envelopes: list[str] = []
    skipped: list[str] = []
    for located in _allocate_directives(entries):
        if envelope is not None and not envelope.search(located.envelope):
            continue
        if month is not None and located.month != month_of(month):
            continue
        if not located.located:
            skipped.append(
                f"{located.envelope} {located.month:%Y-%m}: directive has no "
                "source location"
            )
            continue
        edits.append(DirectiveEdit(located.filename, located.lineno, None))
        if located.envelope not in envelopes:
            envelopes.append(located.envelope)
    return ClearPlan(tuple(edits), tuple(envelopes), tuple(skipped))


@dataclass(frozen=True, slots=True)
class _Located:
    """One located ``allocate`` directive, for grouping.

    Attributes:
        envelope: The funded envelope.
        month: The first day of the directive's month.
        amount: The amount allocated.
        entry: The directive itself.
        filename: The source file, or empty when there is none.
        lineno: The source line, or ``0`` when there is none.
    """

    envelope: str
    month: date
    amount: Decimal
    entry: Custom
    filename: str
    lineno: int

    @property
    def located(self) -> bool:
        """Whether this directive has a real source location to edit.

        Returns:
            True when both a filename and a line number are present.
        """
        return bool(self.filename) and self.lineno > 0


def _allocate_directives(entries: Sequence[Directive]) -> list[_Located]:
    """Collect the ledger's ``allocate`` directives with their source locations.

    Args:
        entries: The ledger entries.

    Returns:
        One record per valid ``allocate`` directive.
    """
    located: list[_Located] = []
    for entry in entries:
        if not isinstance(entry, Custom) or entry.type != "envelope":
            continue
        values = [_unwrap(value) for value in entry.values]
        if len(values) < _ALLOCATE_ARGS or values[0] != "allocate":
            continue
        name, amount = values[1], values[2]
        if not isinstance(name, str) or not isinstance(amount, Amount):
            continue
        number = amount.number
        if not isinstance(number, Decimal):
            continue
        meta = entry.meta or {}
        filename = meta.get("filename")
        lineno = meta.get("lineno")
        located.append(
            _Located(
                envelope=name,
                month=month_of(entry.date),
                amount=number,
                entry=entry,
                filename=filename if isinstance(filename, str) else "",
                lineno=lineno if isinstance(lineno, int) and lineno > 0 else 0,
            )
        )
    return located


def _unwrap(value: object) -> object:
    """Unwrap a Beancount ``ValueType`` into its value.

    Args:
        value: A custom-directive value, possibly a ``ValueType``.

    Returns:
        The underlying value.
    """
    return getattr(value, "value", value)
