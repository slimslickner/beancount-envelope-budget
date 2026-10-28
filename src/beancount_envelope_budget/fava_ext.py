"""The Fava report for envelope balances, read-only by default.

Load with::

    2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext"

Set the default number of months shown (1 to
:data:`~beancount_envelope_budget.report.MAX_MONTHS`) with an optional config::

    2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext" "{'months': 2}"

The ``months`` query parameter overrides it.

The report is **read-only unless a write target is configured**. Add a ``write``
key naming a Beancount file, relative to the ledger's directory, to let the
report append ``custom "envelope"`` directives::

    2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext" "{'write': 'beans/Envelope.bean'}"

A path that escapes the ledger directory or is not a Beancount source file is
refused. Writes go through Fava's own file API (its lock, watcher and printer),
so Fava's editor and this report stay consistent; the directives themselves come
from :mod:`beancount_envelope_budget.actions`, the same module the CLI uses.

The extension recomputes the budget in :meth:`Envelopes.after_load_file` and
renders a Jinja template. It is the only module that imports Fava. The view it
renders is built by :func:`beancount_envelope_budget.report.build_view`, the
same function the CLI uses, so the two cannot disagree.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fava.core import file as fava_file
from fava.ext import ExtensionConfigError, FavaExtensionBase, extension_endpoint
from flask import redirect, request

from beancount_envelope_budget import actions, writer
from beancount_envelope_budget.core import BudgetResult, compute, row_for
from beancount_envelope_budget.report import (
    MAX_MONTHS,
    ActivityLine,
    BudgetView,
    Cell,
    EnvelopeView,
    build_view,
    today_month,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from beancount.core.data import Custom, Directive
    from fava.beans.abc import Directive as FavaDirective
    from fava.core import FavaLedger
    from flask import Response

__all__ = ["Envelopes"]

_DEFAULT_MONTHS = 3
_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")


class Envelopes(FavaExtensionBase):
    """A report of envelope balances that can edit the budget when configured."""

    report_title = "Envelopes"

    def __init__(self, ledger: FavaLedger, config: str | None = None) -> None:
        """Initialise the extension.

        Args:
            ledger: The Fava ledger.
            config: The optional extension config. A mapping may set a ``months``
                key (1 to :data:`~beancount_envelope_budget.report.MAX_MONTHS`,
                the default number of months shown) and a ``write`` key (the
                Beancount file to append directives to, relative to the ledger
                directory; writing stays off when it is absent). A whole number
                sets the month count. The ``months`` query parameter overrides
                the month count.

        Raises:
            ExtensionConfigError: If the config is not valid, or the write target
                escapes the ledger directory or is not a Beancount source file.
        """
        super().__init__(ledger, config)
        self.result: BudgetResult | None = None
        try:
            self.default_months = _default_months(self.config)
            self.write_target = self._resolve_write(_write_setting(self.config))
        except (TypeError, ValueError) as error:
            raise ExtensionConfigError(SyntaxError(str(error)), config or "") from error

    def _resolve_write(self, value: str | None) -> Path | None:
        """Resolve the configured write target, refusing an unsafe path.

        Args:
            value: The configured ``write`` path, or ``None``.

        Returns:
            The absolute target path, or ``None`` when writing is not configured.
        """
        if value is None:
            return None
        base = Path(self.ledger.beancount_file_path).parent
        return writer.resolve_target(Path(value), base)

    @property
    def writable(self) -> bool:
        """Whether a write target is configured, so the report can edit.

        Returns:
            True when the report may append directives.
        """
        return self.write_target is not None

    def _result(self) -> BudgetResult:
        """Return the computed budget, computing it once on first use.

        Returns:
            The :class:`BudgetResult`.
        """
        if self.result is None:
            self.result = compute(
                self._entries(),
                self.ledger.options,
                collect_activity=True,
            )
        return self.result

    def prefill(self, envelope: str, month: date) -> str:
        """Suggest an allocation for an envelope-month, as ``fund`` would.

        The suggestion is the month's net spending (the ``actuals`` mode of the
        CLI's ``fund``), so the "add allocation" form opens with a sensible
        amount. It is empty when the envelope has no allocation to suggest.

        Args:
            envelope: The envelope name.
            month: The month to fill.

        Returns:
            The suggested amount, formatted, or an empty string.
        """
        report = self._result().envelope(envelope)
        if report is None:
            return ""
        amount = -row_for(report.rows, month).activity
        return f"{amount:.2f}" if amount != 0 else ""

    def envelope_names(self) -> list[str]:
        """Return every envelope's name, for a move form's target picker.

        Returns:
            The envelope names.
        """
        return [report.name for report in self._result().envelopes]

    def perform(self, params: Mapping[str, str]) -> Custom:
        """Apply a submitted edit form, appending one directive.

        Args:
            params: The submitted form fields; ``action`` selects the directive
                and the remaining keys are its arguments.

        Returns:
            The directive that was appended.

        Raises:
            RuntimeError: If writing is not configured.
            ValueError: If the form is missing or malforms an argument.
        """
        if self.write_target is None:
            message = "writing is not configured"
            raise RuntimeError(message)
        entry = _action_entry(params, self._result())
        _insert(self.ledger, self.write_target, entry)
        return entry

    @extension_endpoint("commit", ["POST"])
    def commit(self) -> Response:
        """Append the directive a submitted form describes, then return.

        Returns:
            A redirect back to the report.
        """
        self.perform(request.form.to_dict())
        return cast("Response", redirect(request.referrer or ".."))

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
                current month.
            months: The number of months to show (1-3), or ``None`` for the
                default.

        Returns:
            The :class:`BudgetView`.
        """
        return build_view(
            self._result(),
            _parse_month(month) or self.today_month(),
            _parse_count(months, self.default_months),
        )

    def money(self, value: Decimal) -> str:
        """Format a money amount for display.

        Args:
            value: The amount.

        Returns:
            The amount with two decimal places.
        """
        return f"{value:.2f}"

    def fill_percent(self, cell: Cell) -> str:
        """Format a cell's goal fill as a CSS percentage.

        Args:
            cell: The view cell.

        Returns:
            The fill from ``0%`` to ``100%``, or ``0%`` when the cell has no
            goal or level to measure against.
        """
        return f"{cell.fill * 100:.1f}%" if cell.fill is not None else "0%"

    def today_month(self) -> date:
        """Return the first day of the current month.

        Delegates to :func:`~beancount_envelope_budget.report.today_month`, the
        single implementation shared with the CLI, so the two never disagree.
        The template highlights the current month's column and links to it.

        Returns:
            The first day of the local current month.
        """
        return today_month()

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
        """Build the plain-text listing of an activity cell's transactions.

        This is the text-only fallback for the activity drill-down; the report
        renders the same lines as links into Fava's entry popup.

        Args:
            cell: The view cell.

        Returns:
            One line per contributing posting, with a remainder note when the
            list was capped.
        """
        lines = [
            f"{line.date.isoformat()}  {self.detail_text(line)}  {line.amount:.2f}"
            for line in cell.detail
        ]
        remaining = cell.detail_total - len(cell.detail)
        if remaining > 0:
            lines.append(f"and {remaining} more")
        return "\n".join(lines)

    def detail_text(self, line: ActivityLine) -> str:
        """Describe one activity detail line.

        The line reads ``payee - narration``: the transaction's payee, then the
        posting's ``narration`` metadata (the ``autobean.narration`` convention),
        falling back to the transaction's narration. The posting's own narration
        wins, so a transaction whose narration ``autobean.narration`` synthesised
        from its postings does not repeat it.

        Args:
            line: The activity detail line.

        Returns:
            The description, or ``(no description)`` when there is nothing to
            show.
        """
        narration = line.posting_narration or line.transaction_narration
        parts = [part for part in (line.payee, narration) if part]
        return " - ".join(parts) or "(no description)"

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


def _write_setting(config: object) -> str | None:
    """Read the optional ``write`` path from an extension config.

    Args:
        config: The parsed extension config.

    Returns:
        The configured path, or ``None``.

    Raises:
        TypeError: If ``write`` is present but not a string.
    """
    if not isinstance(config, dict):
        return None
    value = config.get("write")
    if value is None:
        return None
    if not isinstance(value, str):
        message = "write must be a path string"
        raise TypeError(message)
    return value


def _form_decimal(value: str) -> Decimal:
    """Parse a required decimal form field.

    Args:
        value: The raw field.

    Returns:
        The parsed decimal.
    """
    return Decimal(value)


def _form_optional_decimal(value: str | None) -> Decimal | None:
    """Parse an optional decimal form field.

    Args:
        value: The raw field, or ``None``.

    Returns:
        The parsed decimal, or ``None`` when blank.
    """
    return None if value is None or not value.strip() else Decimal(value)


def _form_monthly(value: str | None) -> Decimal | str | None:
    """Parse a ``monthly`` form field: a number or ``avgN``.

    Args:
        value: The raw field, or ``None``.

    Returns:
        The decimal or ``avgN`` string, or ``None`` when blank.
    """
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value)
    except ArithmeticError:
        return value


def _form_day(value: str | None) -> date:
    """Parse a form date, defaulting to today.

    Args:
        value: The raw field, or ``None``.

    Returns:
        The parsed date, or the local current date when blank.
    """
    if value:
        return date.fromisoformat(value)
    return date.today()  # noqa: DTZ011 - local today is intended


def _form_optional_day(value: str | None) -> date | None:
    """Parse an optional form date.

    Args:
        value: The raw field, or ``None``.

    Returns:
        The parsed date, or ``None`` when blank.
    """
    return date.fromisoformat(value) if value else None


def _action_entry(params: Mapping[str, str], result: BudgetResult) -> Custom:
    """Turn submitted form fields into the directive they describe.

    Args:
        params: The submitted form fields.
        result: The computed budget, for the operating currency.

    Returns:
        The directive.

    Raises:
        ValueError: If the action is unknown or a required field is missing.
    """
    action = params.get("action")
    day = _form_day(params.get("date"))
    currency = result.operating_currency
    if action == "allocate":
        return actions.allocate(
            params["envelope"], _form_decimal(params["amount"]), day, currency
        )
    if action == "move":
        return actions.move(
            params["source"],
            params["target"],
            _form_decimal(params["amount"]),
            day,
            currency,
        )
    if action == "plan":
        return actions.plan(
            params["envelope"],
            day,
            monthly=_form_monthly(params.get("monthly")),
            priority=_form_optional_decimal(params.get("priority")),
            target=_form_optional_decimal(params.get("target")),
            target_currency=currency,
            by=_form_optional_day(params.get("by")),
        )
    if action == "unplan":
        return actions.unplan(params["envelope"], day)
    message = f"unknown action: {action!r}"
    raise ValueError(message)


def _insert(ledger: FavaLedger, path: Path, entry: Custom) -> None:
    """Append a directive through Fava's own file API.

    Reuses Fava's lock, watcher and printer, so its editor and this report stay
    consistent. The target is the configured file, not Fava's default.

    Args:
        ledger: The Fava ledger.
        path: The file to append to.
        entry: The directive.
    """
    options = ledger.fava_options
    with ledger.file._lock:  # noqa: SLF001 - Fava's own lock is the point
        fava_entry = cast("FavaDirective", entry)
        written, _ = fava_file.insert_entry(
            fava_entry,
            str(path),
            [],
            options.currency_column,
            options.indent,
        )
        ledger.watcher.notify(written)
        ledger.extensions.after_insert_entry(fava_entry)
