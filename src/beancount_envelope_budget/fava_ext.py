"""The read-only Fava report for envelope balances.

Load with::

    2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext"

Set the default number of months shown (1 to
:data:`~beancount_envelope_budget.report.MAX_MONTHS`) with an optional config::

    2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext" "{'months': 2}"

The ``months`` query parameter overrides it.

The extension recomputes the budget in :meth:`Envelopes.after_load_file` and
renders a Jinja template. It is the only module that imports Fava. The view it
renders is built by :func:`beancount_envelope_budget.report.build_view`, the
same function the CLI uses, so the two cannot disagree.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING, cast

from fava.ext import ExtensionConfigError, FavaExtensionBase

from beancount_envelope_budget.core import BudgetResult, compute
from beancount_envelope_budget.report import (
    MAX_MONTHS,
    BudgetView,
    Cell,
    EnvelopeView,
    build_view,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from beancount.core.data import Directive
    from fava.core import FavaLedger

__all__ = ["Envelopes"]

_DEFAULT_MONTHS = 3
_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")


class Envelopes(FavaExtensionBase):
    """A read-only report of envelope balances across one to three months."""

    report_title = "Envelopes"

    def __init__(self, ledger: FavaLedger, config: str | None = None) -> None:
        """Initialise the extension.

        Args:
            ledger: The Fava ledger.
            config: The optional extension config. A mapping with a ``months``
                key, or a whole number, sets the default number of months shown
                (1 to :data:`~beancount_envelope_budget.report.MAX_MONTHS`); the
                ``months`` query parameter overrides it.

        Raises:
            ExtensionConfigError: If the config is not a valid month count.
        """
        super().__init__(ledger, config)
        self.result: BudgetResult | None = None
        try:
            self.default_months = _default_months(self.config)
        except (TypeError, ValueError) as error:
            raise ExtensionConfigError(SyntaxError(str(error)), config or "") from error

    def after_load_file(self) -> None:
        """Recompute the budget once the ledger has been loaded."""
        self.result = compute(
            self._entries(),
            self.ledger.options,
            collect_activity=True,
        )

    def _entries(self) -> list[Directive]:
        """Return the ledger's entries as Beancount directives.

        Fava's type stubs describe its own bean model, but at runtime the
        entries are Beancount's own directive types.

        Returns:
            The entries.
        """
        return cast("list[Directive]", list(self.ledger.all_entries))

    def report(self, month: str | None = None, months: str | None = None) -> BudgetView:
        """Build the report view from query parameters.

        Args:
            month: The first month to show, as ``YYYY-MM``, or ``None`` for the
                latest month in the ledger.
            months: The number of months to show (1-3), or ``None`` for the
                default.

        Returns:
            The :class:`BudgetView`.
        """
        result = self.result
        if result is None:
            result = compute(
                self._entries(),
                self.ledger.options,
                collect_activity=True,
            )
            self.result = result
        return build_view(
            result, _parse_month(month), _parse_count(months, self.default_months)
        )

    def money(self, value: Decimal) -> str:
        """Format a money amount for display.

        Args:
            value: The amount.

        Returns:
            The amount with two decimal places.
        """
        return f"{value:.2f}"

    def today_month(self) -> date:
        """Return the first day of the current month.

        The current month is a display concern, so it is resolved here rather
        than in the pure :func:`~beancount_envelope_budget.report.build_view`.
        The template highlights the current month's column and links to it.

        Returns:
            The first day of the local current month.
        """
        return date.today().replace(day=1)  # noqa: DTZ011 - local today is intended

    def today_nav(self, count: int) -> str:
        """Build the query string linking to the current month.

        Args:
            count: The number of months to keep visible.

        Returns:
            A query string for the report's ``this month`` link.
        """
        return f"month={self.today_month():%Y-%m}&months={count}"

    def envelope_title(self, envelope: EnvelopeView) -> str:
        """Build the hover text for an envelope name.

        Args:
            envelope: The envelope view.

        Returns:
            The target and deadline, or the envelope name.
        """
        plan = envelope.plan
        parts: list[str] = []
        if plan.target is not None:
            currency = f" {plan.target_currency}" if plan.target_currency else ""
            parts.append(f"target {plan.target:.2f}{currency}")
        if plan.by is not None:
            parts.append(f"by {plan.by.isoformat()}")
        return " · ".join(parts)

    def activity_title(self, cell: Cell) -> str:
        """Build the hover text listing an activity cell's transactions.

        Args:
            cell: The view cell.

        Returns:
            One line per contributing posting, with a remainder note when the
            list was capped.
        """
        lines = [
            f"{line.date.isoformat()}  {line.label}  {line.amount:.2f}"
            for line in cell.detail
        ]
        remaining = cell.detail_total - len(cell.detail)
        if remaining > 0:
            lines.append(f"and {remaining} more")
        return "\n".join(lines)

    def errors_url(self) -> str:
        """Return a relative link to Fava's errors page.

        Returns:
            The relative URL, computed from the extension page's depth.
        """
        return "../../errors/"


def _parse_month(value: object) -> date | None:
    """Parse a ``YYYY-MM`` query parameter.

    Args:
        value: The raw parameter.

    Returns:
        The first day of the month, or ``None`` when absent or malformed.
    """
    if not isinstance(value, str) or not _MONTH_PATTERN.match(value):
        return None
    year, month = (int(part) for part in value.split("-", 1))
    try:
        return date(year, month, 1)
    except ValueError:
        return None


def _default_months(config: object) -> int:
    """Resolve the default month count from an extension config.

    Args:
        config: The parsed extension config: a mapping with a ``months`` key, a
            whole number, or ``None`` for the built-in default.

    Returns:
        The default number of months to show, between 1 and ``MAX_MONTHS``.

    Raises:
        ValueError: If the config is not a valid month count.
    """
    if config is None:
        return _DEFAULT_MONTHS
    value = (
        config.get("months", _DEFAULT_MONTHS) if isinstance(config, dict) else config
    )
    if isinstance(value, bool) or not isinstance(value, int):
        message = "months must be a whole number"
        raise TypeError(message)
    if not 1 <= value <= MAX_MONTHS:
        message = f"months must be between 1 and {MAX_MONTHS}"
        raise ValueError(message)
    return value


def _parse_count(value: object, default: int = _DEFAULT_MONTHS) -> int:
    """Parse a month-count query parameter.

    Args:
        value: The raw parameter.
        default: The count to use when the parameter is absent or malformed.

    Returns:
        A count between 1 and ``MAX_MONTHS``.
    """
    count = default
    if isinstance(value, str):
        try:
            count = int(value)
        except ValueError:
            count = default
    return max(1, min(count, MAX_MONTHS))
