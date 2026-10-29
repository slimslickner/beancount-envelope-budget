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
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, cast

from beancount.core.data import Custom
from beancount.parser import parser
from fava.core import file as fava_file
from fava.ext import ExtensionConfigError, FavaExtensionBase, extension_endpoint
from flask import jsonify, make_response, redirect, request

from beancount_envelope_budget import actions, writer
from beancount_envelope_budget.core import BudgetResult, Plan, compute
from beancount_envelope_budget.report import (
    MAX_MONTHS,
    ActivityLine,
    BudgetView,
    Cell,
    PlanLine,
    build_view,
    month_funding,
    overspend_cover,
    plan_top_up,
    today_month,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from beancount.core.data import Directive
    from fava.beans.abc import Directive as FavaDirective
    from fava.core import FavaLedger
    from flask import Response

__all__ = ["Envelopes"]

_DEFAULT_MONTHS = 3
_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")


class Envelopes(FavaExtensionBase):
    """A report of envelope balances that can edit the budget when configured."""

    report_title = "Envelopes"

    has_js_module = True

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

        The suggestion leads with the envelope's plan top-up; when there is
        nothing left to fund, it falls back to covering the month's overspend,
        and to zero when the month is neither underfunded nor overspent. It is
        used to prefill the "add allocation" form and modal.

        Args:
            envelope: The envelope name.
            month: The month to fill.

        Returns:
            The suggested amount, formatted.
        """
        report = self._result().envelope(envelope)
        if report is None:
            return "0.00"
        on = _fill_day(month)
        top_up = plan_top_up(report, on)
        if top_up > 0:
            return f"{top_up:.2f}"
        return f"{overspend_cover(report, on):.2f}"

    def cover_prefill(self, envelope: str, month: date) -> str:
        """Suggest the amount that covers an envelope-month's overspending.

        Offered beside the prefill as a labelled quick-fill, so a partly funded
        envelope can be topped up to plan or brought back to zero directly.

        Args:
            envelope: The envelope name.
            month: The month to fill.

        Returns:
            The overspend cover, formatted; ``0.00`` when the month is not
            overspent.
        """
        report = self._result().envelope(envelope)
        if report is None:
            return "0.00"
        return f"{overspend_cover(report, _fill_day(month)):.2f}"

    def envelope_names(self) -> list[str]:
        """Return every envelope's name, for a move form's target picker.

        Returns:
            The envelope names.
        """
        return [report.name for report in self._result().envelopes]

    def plan_text(self, line: PlanLine) -> str:
        """Describe one plan directive for the plan popover.

        Args:
            line: The plan line.

        Returns:
            A short description, such as ``target 15000.00 USD · by 2028-06-01``.
        """
        if line.clears:
            return "clear plan"
        parts: list[str] = []
        if line.monthly is not None:
            parts.append(f"monthly {line.monthly}")
        if line.priority is not None:
            parts.append(f"priority {line.priority}")
        if line.target is not None:
            currency = f" {line.target_currency}" if line.target_currency else ""
            parts.append(f"target {line.target:.2f}{currency}")
        if line.by is not None:
            parts.append(f"by {line.by.isoformat()}")
        label = " · ".join(parts) if parts else "no plan"
        return f"open: {label}" if line.base else label

    def perform(self, params: Mapping[str, str]) -> list[Custom]:
        """Apply a submitted edit form, appending its directive(s).

        With a ``source`` field — what the JavaScript modal posts — the
        Beancount text is parsed and every ``custom "envelope"`` directive it
        holds is appended. Otherwise the individual form fields are turned into
        one directive, the no-JS fallback.

        Args:
            params: The submitted fields. ``source`` holds Beancount text; any
                other shape is the ``action``/arguments of the plain forms.

        Returns:
            The directives that were appended.

        Raises:
            RuntimeError: If writing is not configured.
            ValueError: If the source does not parse or the form is malformed.
        """
        if self.write_target is None:
            message = "writing is not configured"
            raise RuntimeError(message)
        entries = self._entries_for(params)
        for entry in entries:
            _insert(self.ledger, self.write_target, entry)
        return entries

    def _entries_for(self, params: Mapping[str, str]) -> list[Custom]:
        """Turn submitted fields into the directives they describe.

        A ``source`` field — what the JavaScript modal posts — is parsed; any
        other shape is the individual form fields, the no-JS fallback. A
        month-level action expands to one allocation per envelope.

        Args:
            params: The submitted fields.

        Returns:
            The directives.

        Raises:
            ValueError: If the source does not parse or the fields are malformed.
        """
        source = params.get("source")
        if source is not None and source.strip():
            return _parse_source(source)
        return _action_entries(params, self._result())

    def fill_date(self, month: date) -> str:
        """Return the date a new directive in a month is dated, as text.

        The form's date inputs and the rendered source both use this, so the
        modal opens on the date the form shows.

        Args:
            month: Any date within the month.

        Returns:
            The ISO date: the last day of the month, or today when earlier.
        """
        return _fill_day(month).isoformat()

    def plan_fields(self, envelope: str, month: date) -> dict[str, str]:
        """Return the plan in force as form field values, for the plan form.

        Args:
            envelope: The envelope name.
            month: The month the plan is being set in.

        Returns:
            ``monthly``, ``priority``, ``target`` and ``by`` as strings, each
            empty when the plan does not set it.
        """
        report = self._result().envelope(envelope)
        plan = report.envelope.effective(_fill_day(month)) if report else Plan()
        return {
            "monthly": "" if plan.monthly is None else str(plan.monthly),
            "priority": "" if plan.priority is None else str(plan.priority),
            "target": "" if plan.target is None else f"{plan.target}",
            "by": "" if plan.by is None else plan.by.isoformat(),
        }

    def render_source(self, params: Mapping[str, str]) -> str:
        """Render the directive a create form's current fields describe.

        Used to seed the JavaScript modal, so what it opens with matches what
        the form shows.

        Args:
            params: The submitted form fields.

        Returns:
            The Beancount text.

        Raises:
            ValueError: If the fields do not describe a valid directive.
        """
        return "\n".join(actions.render(entry) for entry in self._entries_for(params))

    @extension_endpoint("source", ["POST"])
    def source(self) -> Response:
        """Render the directive a create form describes, without writing it.

        Returns:
            ``{"source": ...}``, or ``{"error": ...}`` when the fields are bad.
        """
        params = request.form.to_dict()
        try:
            text = self.render_source(params)
        except (ValueError, KeyError) as error:
            return make_response(jsonify({"error": str(error)}), 400)
        return make_response(jsonify({"source": text}))

    @extension_endpoint("commit", ["POST"])
    def commit(self) -> Response:
        """Append the directive a submitted form describes, then return.

        The JavaScript modal posts ``format=json`` and gets JSON back; the
        plain form gets a redirect to the report.

        Returns:
            JSON, or a redirect back to the report.
        """
        params = request.form.to_dict()
        as_json = params.get("format") == "json"
        try:
            self.perform(params)
        except (ValueError, RuntimeError) as error:
            if as_json:
                return make_response(jsonify({"error": str(error)}), 400)
            raise
        if as_json:
            return make_response(jsonify({"status": "ok"}))
        report = request.path.rpartition("/")[0] + "/"
        return cast("Response", redirect(request.referrer or report))

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


def _fill_day(month: date) -> date:
    """Return the date a new directive in a month is dated.

    Args:
        month: Any date within the month.

    Returns:
        The last day of the month, or today when today is earlier.
    """
    first = month.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return min(last, date.today())  # noqa: DTZ011 - local today is intended


def _parse_source(source: str) -> list[Custom]:
    """Parse submitted Beancount source into envelope directives.

    Args:
        source: The Beancount text from the JavaScript modal.

    Returns:
        The ``custom "envelope"`` directives it holds.

    Raises:
        ValueError: If the source does not parse, or holds no envelope directive.
    """
    entries, errors, _options = parser.parse_string(source)
    if errors:
        message = "; ".join(str(getattr(error, "message", error)) for error in errors)
        raise ValueError(message)
    directives = [
        entry
        for entry in entries
        if isinstance(entry, Custom) and entry.type == actions.TYPE
    ]
    if not directives:
        message = 'the source has no custom "envelope" directive'
        raise ValueError(message)
    return directives


def _action_entries(params: Mapping[str, str], result: BudgetResult) -> list[Custom]:
    """Turn submitted form fields into the directive(s) they describe.

    A month-level action expands to one ``allocate`` per envelope; every other
    action describes exactly one directive.

    Args:
        params: The submitted form fields.
        result: The computed budget.

    Returns:
        The directives.

    Raises:
        ValueError: If the action is unknown or a required field is missing.
    """
    action = params.get("action")
    if action in ("fully_fund", "fund_available"):
        return _month_entries(action, params, result)
    return [_action_entry(params, result)]


def _month_entries(
    action: str,
    params: Mapping[str, str],
    result: BudgetResult,
) -> list[Custom]:
    """Build the allocations a month-level action funds.

    Args:
        action: ``fully_fund`` or ``fund_available``.
        params: The submitted form fields; ``month`` selects the month.
        result: The computed budget.

    Returns:
        One ``allocate`` directive per envelope that needs funding.

    Raises:
        ValueError: If the month is missing, or nothing needs funding.
    """
    month = _parse_month(params.get("month"))
    if month is None:
        message = "a month is required, as YYYY-MM"
        raise ValueError(message)
    on = _fill_day(month)
    allocations, _unfunded = month_funding(
        result,
        on,
        available=action == "fund_available",
    )
    if not allocations:
        message = "nothing to fund for this month"
        raise ValueError(message)
    currency = result.operating_currency
    return [
        actions.allocate(name, amount, on, currency) for name, amount in allocations
    ]


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
            params["envelope"],
            _form_decimal(params.get("amount") or "0"),
            day,
            currency,
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
