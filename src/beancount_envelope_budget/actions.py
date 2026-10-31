"""Pure generation of ``custom "envelope"`` directives from budget actions.

The CLI and the Fava report both change a budget by appending, editing or
removing a single ``custom "envelope"`` directive. This module turns an action —
``allocate``, ``move``, ``plan``, ``unplan``, ``open`` or ``close`` — into the
Beancount directive that expresses it, and renders it with Beancount's own
printer, so the CLI and the web report emit identical text. It performs no I/O
and imports neither the CLI nor Fava.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 - used by doctests at runtime
from decimal import Decimal
from typing import TYPE_CHECKING

from beancount.core.amount import Amount
from beancount.core.data import Custom
from beancount.parser import printer
from beancount.parser.grammar import ValueType

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "allocate",
    "close",
    "move",
    "open_envelope",
    "plan",
    "render",
    "unplan",
]

#: The Beancount custom directive type every action uses.
TYPE = "envelope"

#: The quantum every monetary amount is rounded to before it is written.
_CENT = Decimal("0.01")


def _amount(number: Decimal, currency: str) -> Amount:
    """Round an amount to the cent and attach its currency.

    Args:
        number: The amount.
        currency: The operating currency.

    Returns:
        The amount, rounded to two decimal places.
    """
    return Amount(number.quantize(_CENT), currency)


def _custom(
    day: date,
    values: Sequence[object],
    meta: Mapping[str, object] | None = None,
) -> Custom:
    """Build a ``custom "envelope"`` directive.

    Args:
        day: The directive's date.
        values: The values after the ``envelope`` type, starting with the verb.
        meta: The directive's metadata, or ``None``.

    Returns:
        The directive.
    """
    return Custom(
        meta=dict(meta) if meta else {},
        date=day,
        type=TYPE,
        values=[ValueType(value, type(value)) for value in values],
    )


def allocate(envelope: str, amount: Decimal, day: date, currency: str) -> Custom:
    """Build an ``allocate`` directive.

    Args:
        envelope: The envelope to fund; the reserved pool name moves money back.
        amount: The amount to allocate; negative moves it back to the pool.
        day: The directive's date.
        currency: The operating currency.

    Returns:
        The directive.

    Examples:
        >>> render(allocate("Groceries", Decimal("600"), date(2024, 1, 31), "USD"))
        '2024-01-31 custom "envelope" "allocate" "Groceries" 600.00 USD'
    """
    return _custom(day, ["allocate", envelope, _amount(amount, currency)])


def move(
    source: str,
    destination: str,
    amount: Decimal,
    day: date,
    currency: str,
) -> Custom:
    """Build a ``move`` directive.

    Args:
        source: The envelope to take money from.
        destination: The envelope to give money to.
        amount: The amount to move.
        day: The directive's date.
        currency: The operating currency.

    Returns:
        The directive.

    Examples:
        >>> render(move("Groceries", "Gifts", Decimal("50"), date(2024, 2, 1), "USD"))
        '2024-02-01 custom "envelope" "move" "Groceries" "Gifts" 50.00 USD'
    """
    return _custom(day, ["move", source, destination, _amount(amount, currency)])


def _plan_meta(
    monthly: Decimal | str | None,
    priority: Decimal | None,
    goal: Decimal | None,
    goal_currency: str | None,
    by: date | None,
    up_to: Decimal | None,
) -> dict[str, object]:
    """Build the shared ``open``/``plan`` metadata.

    Args:
        monthly: The planned monthly amount, a number or ``"avgN"``.
        priority: The funding priority, lower first.
        goal: The goal total.
        goal_currency: The currency of the goal.
        by: The date the goal should be reached by.
        up_to: A balance ceiling paired with ``monthly``.

    Returns:
        The metadata, omitting the fields that are ``None``.

    Raises:
        ValueError: If a goal is given without its currency.
    """
    meta: dict[str, object] = {}
    if monthly is not None:
        meta["monthly"] = monthly
    if up_to is not None:
        meta["up-to"] = up_to
    if priority is not None:
        meta["priority"] = priority
    if goal is not None:
        if goal_currency is None:
            message = "a goal needs a goal_currency"
            raise ValueError(message)
        meta["goal"] = _amount(goal, goal_currency)
    if by is not None:
        meta["by"] = by
    return meta


def plan(
    envelope: str,
    day: date,
    *,
    monthly: Decimal | str | None = None,
    priority: Decimal | None = None,
    goal: Decimal | None = None,
    goal_currency: str | None = None,
    by: date | None = None,
    up_to: Decimal | None = None,
) -> Custom:
    r"""Build a ``plan`` directive.

    Args:
        envelope: The envelope the plan applies to.
        day: The directive's date.
        monthly: The planned monthly amount, a number or ``"avgN"``.
        priority: The funding priority, lower first.
        goal: The goal total.
        goal_currency: The currency of the goal.
        by: The date the goal should be reached by.
        up_to: A balance ceiling paired with ``monthly``.

    Returns:
        The directive.

    Examples:
        >>> print(render(plan("Car", date(2024, 1, 31), monthly=Decimal("500"))))
        2024-01-31 custom "envelope" "plan" "Car"
          monthly: 500
    """
    meta = _plan_meta(monthly, priority, goal, goal_currency, by, up_to)
    return _custom(day, ["plan", envelope], meta)


def unplan(envelope: str, day: date) -> Custom:
    """Build an ``unplan`` directive.

    Args:
        envelope: The envelope to clear the plan from.
        day: The directive's date.

    Returns:
        The directive.

    Examples:
        >>> render(unplan("Car", date(2024, 2, 1)))
        '2024-02-01 custom "envelope" "unplan" "Car"'
    """
    return _custom(day, ["unplan", envelope])


def open_envelope(  # noqa: PLR0913 - the plan fields share one builder
    envelope: str,
    day: date,
    *,
    monthly: Decimal | str | None = None,
    priority: Decimal | None = None,
    goal: Decimal | None = None,
    goal_currency: str | None = None,
    by: date | None = None,
    up_to: Decimal | None = None,
    parent: str | None = None,
) -> Custom:
    """Build an ``open`` directive.

    Args:
        envelope: The envelope to declare.
        day: The directive's date.
        monthly: The planned monthly amount, a number or ``"avgN"``.
        priority: The funding priority, lower first.
        goal: The goal total.
        goal_currency: The currency of the goal.
        by: The date the goal should be reached by.
        up_to: A balance ceiling paired with ``monthly``.
        parent: A grouping label.

    Returns:
        The directive.

    Examples:
        >>> render(open_envelope("Groceries", date(2024, 1, 1)))
        '2024-01-01 custom "envelope" "open" "Groceries"'
    """
    meta = _plan_meta(monthly, priority, goal, goal_currency, by, up_to)
    if parent is not None:
        meta["parent"] = parent
    return _custom(day, ["open", envelope], meta)


def close(envelope: str, day: date) -> Custom:
    """Build a ``close`` directive.

    Args:
        envelope: The envelope to retire.
        day: The directive's date.

    Returns:
        The directive.

    Examples:
        >>> render(close("Car", date(2024, 6, 1)))
        '2024-06-01 custom "envelope" "close" "Car"'
    """
    return _custom(day, ["close", envelope])


def render(entry: Custom) -> str:
    """Render a directive as it should appear in a ledger.

    Delegates to Beancount's own printer so the CLI and the Fava report never
    disagree on spelling.

    Args:
        entry: The directive.

    Returns:
        The directive's text, without a trailing newline.
    """
    return printer.format_entry(entry).rstrip("\n")
