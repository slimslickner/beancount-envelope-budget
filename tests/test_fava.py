"""Tests for the read-only Fava extension and its template."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import jinja2
import pytest
from beancount import loader
from fava.ext import ExtensionConfigError, find_extensions

from beancount_envelope_budget import actions, fava_ext
from beancount_envelope_budget.fava_ext import Envelopes
from beancount_envelope_budget.report import ActivityLine, to_jsonable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fava.core import FavaLedger

D = Decimal

_LEDGERS = Path(__file__).parent / "ledgers"
_TEMPLATES = Path(fava_ext.__file__).parent / "templates"


def _extension(
    name: str,
    *,
    load: bool = True,
    config: str | None = None,
    ledger_path: Path | None = None,
) -> Envelopes:
    """Build an extension over a fixture ledger.

    Args:
        name: The fixture file name.
        load: Whether to run ``after_load_file``.
        config: An optional extension config string.
        ledger_path: An optional path to hang off the fake ledger as
            ``beancount_file_path``, so a ``write`` config can resolve against
            it.

    Returns:
        The extension.
    """
    entries, _errors, options = loader.load_file(_LEDGERS / name)
    ledger = cast("FavaLedger", SimpleNamespace(all_entries=entries, options=options))
    if ledger_path is not None:
        ledger.beancount_file_path = str(ledger_path)
    extension = Envelopes(ledger, config)
    if load:
        extension.after_load_file()
    return extension


def _render(extension: Envelopes, args: Mapping[str, str]) -> str:
    """Render the extension template standalone.

    Args:
        extension: The extension.
        args: The query parameters.

    Returns:
        The rendered HTML fragment.
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES)),
        autoescape=True,
    )
    template = env.get_template("Envelopes.html")
    request = SimpleNamespace(args=dict(args), path="/ledger/extension/Envelopes/")
    return template.render(
        ledger=extension.ledger, extension=extension, request=request
    )


def test_extension_is_discoverable() -> None:
    """Fava's own discovery finds the class with the expected names."""
    classes, errors = find_extensions(
        Path(fava_ext.__file__).parent, "beancount_envelope_budget.fava_ext"
    )
    assert errors == []
    assert Envelopes in classes
    assert _extension("basic.bean").name == "Envelopes"
    assert Envelopes.report_title == "Envelopes"
    assert (_TEMPLATES / "Envelopes.html").exists()


def test_report_defaults_to_current_month(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default view starts at the current month, spanning the next two."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2024, 1, 1))
    view = _extension("basic.bean").report()
    assert view.has_start is True
    assert view.months == (date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1))
    assert view.projected == (False, True, True)


def test_report_default_ignores_future_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future-dated directive does not move the default month forward."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2024, 1, 1))
    view = _extension("future_directive.bean").report()
    assert view.months[0] == date(2024, 1, 1)


def test_config_sets_default_months() -> None:
    """An extension config sets the default number of months shown."""
    assert len(_extension("basic.bean", config="{'months': 2}").report().months) == 2
    assert len(_extension("basic.bean", config="1").report().months) == 1


def test_config_is_overridden_by_query() -> None:
    """The months query parameter wins over the configured default."""
    extension = _extension("basic.bean", config="{'months': 1}")
    assert len(extension.report(None, "3").months) == 3


def test_config_rejects_bad_months() -> None:
    """A config that is not a valid month count is an extension error."""
    with pytest.raises(ExtensionConfigError):
        _extension("basic.bean", config="{'months': 0}")
    with pytest.raises(ExtensionConfigError):
        _extension("basic.bean", config="{'months': 'lots'}")


def test_report_values() -> None:
    """The view carries the same numbers as the core."""
    view = _extension("basic.bean").report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    assert groceries.cells[0].closing == D("380")
    assert view.pool[0].closing == D("3500")
    assert view.cash[0].closing == D("3880")


def test_report_cash_opening_is_pre_start_cash() -> None:
    """The first month's cash opening is the cash held before the start date."""
    view = _extension("basic.bean").report("2024-01", "2")
    assert view.cash[0].opening == D("1000")
    assert view.cash[0].closing == D("3880")
    # Later months carry the previous month's closing in.
    assert view.cash[1].opening == view.cash[0].closing


def test_report_hides_closed_envelope_after_close() -> None:
    """A closed envelope stays in its close month and drops out afterwards."""
    view = _extension("closed_envelope.bean").report("2024-01", "3")
    old = next(e for e in view.envelopes if e.name == "Old")
    assert [cell.visible for cell in old.cells] == [True, True, False]
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    assert all(cell.visible for cell in groceries.cells)


def test_report_hides_envelope_before_open() -> None:
    """An envelope opened mid-budget is hidden until the month it opens."""
    view = _extension("open_late.bean").report("2024-06", "2")
    late = next(e for e in view.envelopes if e.name == "Late")
    assert [cell.visible for cell in late.cells] == [False, True]


def test_envelope_view_visible_spans_window() -> None:
    """A late-opened envelope's row spans the whole visible window."""
    view = _extension("open_late.bean").report("2024-06", "3")
    late = next(e for e in view.envelopes if e.name == "Late")
    assert [cell.visible for cell in late.cells] == [False, True, True]
    assert late.visible is True


def test_envelope_view_dropped_outside_its_life() -> None:
    """An envelope with no visible month is dropped from the unified table."""
    view = _extension("open_late.bean").report("2024-01", "3")
    late = next(e for e in view.envelopes if e.name == "Late")
    assert late.visible is False


def test_group_view_visible_spans_window() -> None:
    """A group's row spans the window while any member is open in it."""
    view = _extension("parent_close.bean").report("2024-01", "4")
    gone = next(group for group in view.groups if group.parent == "Gone")
    assert gone.visible is True
    assert gone.subtotal[-1].visible is False


def test_template_hides_envelope_before_open() -> None:
    """The template omits a not-yet-open envelope and shows it from open on."""
    before = _render(_extension("open_late.bean"), {"month": "2024-01", "months": "1"})
    assert "Groceries" in before
    assert "Late" not in before
    after = _render(_extension("open_late.bean"), {"month": "2024-07", "months": "1"})
    assert "Late" in after


def test_template_spans_late_envelope_across_months() -> None:
    """The template gives a late-opened envelope one row across the window."""
    html = _render(_extension("open_late.bean"), {"month": "2024-06", "months": "3"})
    # Groceries and Late each get one row, plus Pool and Cash.
    rows = html.split("<tr")
    late = next(row for row in rows if ">Late<" in row)
    # Name + opening + three columns for each of the three visible months.
    assert late.count("<td") == 11


def test_report_clamps_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Malformed parameters fall back and an early month is clamped up."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2024, 1, 1))
    extension = _extension("basic.bean")
    assert extension.report("nonsense", "lots").months[0] == date(2024, 1, 1)
    assert len(extension.report("2024-01", "9").months) == 3
    assert extension.report("2019-01", "1").months[0] == date(2024, 1, 1)
    assert extension.report(None, "1").months == (date(2024, 1, 1),)


def test_activity_tooltip() -> None:
    """The activity tooltip lists the contributing transactions."""
    view = _extension("fund_backfill.bean").report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    cell = groceries.cells[0]
    assert cell.detail_total == 1
    title = _extension("fund_backfill.bean").activity_title(cell)
    assert "-100.00" in title
    assert "January groceries" in title


def test_template_activity_popover_links_to_entry() -> None:
    """An activity figure reveals links to Fava's entry popup on click."""
    extension = _extension("fund_backfill.bean")
    view = extension.report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    entry_hash = groceries.cells[0].detail[0].entry_hash
    html = _render(extension, {"month": "2024-01", "months": "1"})
    assert "envelope-activity envelope-pop-host" in html
    assert 'class="envelope-pop-toggle"' in html
    assert f'href="#context-{entry_hash}"' in html
    assert "January groceries" in html


def test_report_activity_narration() -> None:
    """A posting's narration metadata surfaces on its activity line."""
    view = _extension("posting_narration.bean").report("2024-02", "1")
    housing = next(e for e in view.envelopes if e.name == "Housing")
    lines = {line.posting_narration: line.amount for line in housing.cells[0].detail}
    assert lines == {"Principal": D("-300"), "Escrow": D("-100"), "Interest": D("-400")}
    line = housing.cells[0].detail[0]
    assert line.payee == "Bank"
    assert line.transaction_narration == "Mortgage payment"


def test_report_activity_without_narration() -> None:
    """A posting without narration metadata leaves it unset."""
    view = _extension("fund_backfill.bean").report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    line = groceries.cells[0].detail[0]
    assert line.posting_narration is None
    assert line.transaction_narration == "January groceries"


def test_detail_text_prefers_posting_narration() -> None:
    """The description reads payee - the posting narration, when present."""
    extension = _extension("posting_narration.bean")
    line = ActivityLine(
        date=date(2024, 2, 1),
        amount=D("-100"),
        entry_hash="x",
        payee="Bank",
        transaction_narration="Mortgage payment",
        posting_narration="Escrow",
    )
    assert extension.detail_text(line) == "Bank - Escrow"


def test_detail_text_falls_back_to_transaction_narration() -> None:
    """Without a posting narration the transaction narration is used."""
    extension = _extension("posting_narration.bean")
    line = ActivityLine(
        date=date(2024, 3, 1),
        amount=D("-10"),
        entry_hash="x",
        payee="Bank",
        transaction_narration="Service fee",
        posting_narration=None,
    )
    assert extension.detail_text(line) == "Bank - Service fee"


def test_detail_text_without_a_description() -> None:
    """A line with no payee or narration reads as no description."""
    extension = _extension("posting_narration.bean")
    line = ActivityLine(date=date(2024, 3, 1), amount=D("-10"), entry_hash="x")
    assert extension.detail_text(line) == "(no description)"


def test_template_activity_narration() -> None:
    """The popover shows the payee and the posting narration."""
    html = _render(
        _extension("posting_narration.bean"), {"month": "2024-02", "months": "1"}
    )
    assert "Bank - Escrow" in html
    assert "Bank - Principal" in html
    assert "Bank - Interest" in html


def test_template_activity_narration_fallback() -> None:
    """A posting without narration falls back to the transaction narration."""
    html = _render(
        _extension("posting_narration.bean"), {"month": "2024-03", "months": "1"}
    )
    assert "Bank - Service fee" in html


def test_activity_title_narration() -> None:
    """The text fallback shows the payee and the posting narration."""
    extension = _extension("posting_narration.bean")
    view = extension.report("2024-02", "1")
    housing = next(e for e in view.envelopes if e.name == "Housing")
    title = extension.activity_title(housing.cells[0])
    assert "Bank - Escrow" in title


def test_report_goal_fill() -> None:
    """A goal cell carries its target and how full it is toward it."""
    view = _extension("savings_goal.bean").report("2024-01", "1")
    car = next(e for e in view.envelopes if e.name == "Car")
    cell = car.cells[0]
    assert cell.goal == D("15000")
    assert cell.fill is not None
    assert D("0") < cell.fill < D("1")


def test_report_monthly_fill_clamps() -> None:
    """A monthly envelope fills to its level, clamped at a whole ring."""
    view = _extension("funding_colors.bean").report("2024-01", "1")
    fills = {e.name: e.cells[0].fill for e in view.envelopes}
    assert fills["Under"] == D("0.5")
    assert fills["On"] == D("1")
    assert fills["Over"] == D("1")


def test_report_monthly_funding_after_spending() -> None:
    """A monthly envelope funded to its plan stays funded after spending."""
    view = _extension("funding_spent.bean").report("2026-10", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    cell = groceries.cells[0]
    assert cell.closing == D("146.31")
    assert cell.funding == "on"
    assert cell.fill == D("1")


def test_report_subcent_goal_is_on_track() -> None:
    """A goal funded to a rounded level reads as on track, not over."""
    view = _extension("funding_subcent_goal.bean").report("2024-01", "1")
    goal = next(e for e in view.envelopes if e.name == "Goal")
    cell = goal.cells[0]
    # 1000 / 7 = 142.857142..., which the 142.86 allocation is on track for.
    assert cell.level == D("142.86")
    assert cell.funding == "on"


def test_report_no_fill_without_a_plan() -> None:
    """An envelope with no plan has nothing to fill toward."""
    view = _extension("basic.bean").report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    assert groceries.cells[0].fill is None
    assert groceries.cells[0].goal is None


def test_template_renders_funding_ring() -> None:
    """A planned envelope renders a ring sized to its fill."""
    html = _render(
        _extension("funding_colors.bean"), {"month": "2024-01", "months": "1"}
    )
    assert 'class="envelope-ring"' in html
    assert "--envelope-fill: 50.0%" in html
    assert "--envelope-fill: 100.0%" in html


def test_template_no_ring_without_a_plan() -> None:
    """An unplanned envelope renders no ring."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    assert 'class="envelope-ring"' not in html


def _closing_pop(html: str, marker: str) -> str:
    """Return the closing popover fragment from the row containing a marker.

    Args:
        html: The rendered template.
        marker: A substring unique to the row.

    Returns:
        The row's ``.envelope-closing-pop`` markup onward, so a test reads only
        the closing popover and not the name cell's plan history.
    """
    row = _row(html, marker)
    return row[row.index("envelope-closing-pop") :]


def test_template_closing_popover_shows_plan_status() -> None:
    """A planned envelope's closing figure opens a plan-status popover."""
    html = _render(
        _extension("funding_colors.bean"), {"month": "2024-01", "months": "1"}
    )
    under = _row(html, ">Under<")
    assert "envelope-closing-pop" in under
    assert "envelope-closing-host" in under
    # The status now shows the funding fraction and the full derivation.
    assert "50.0% funded" in under
    assert "underfunded by 50.00" in under
    assert "level 100.00 = monthly 100.00" in under


def test_template_closing_popover_shows_the_active_plan() -> None:
    """The closing popover lists the plan in force for the month, not the history."""
    extension = _extension("plan_merge.bean")
    early = _closing_pop(
        _render(extension, {"month": "2024-01", "months": "1"}), ">Car<"
    )
    assert "open: priority 2" in early
    later = _closing_pop(
        _render(extension, {"month": "2024-06", "months": "1"}), ">Car<"
    )
    assert "priority 1" in later
    assert "open:" not in later


def test_template_closing_popover_links_the_active_plan() -> None:
    """A goal envelope's closing popover names its target and links its plan."""
    html = _render(_extension("savings_goal.bean"), {"month": "2024-02", "months": "1"})
    car = _closing_pop(html, ">Car<")
    assert "goal 15000.00" in car
    assert "2028-06-01" in car
    assert "#context-" in car


def test_template_no_closing_popover_without_a_plan() -> None:
    """An unplanned envelope's closing figure stays plain text."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    groceries = _row(html, ">Groceries<")
    assert "envelope-closing-pop" not in groceries
    assert "envelope-closing-host" not in groceries


def test_report_includes_warnings() -> None:
    """Warnings surface on the view."""
    view = _extension("mid_month_dip.bean").report("2024-01", "1")
    assert [warning.code for warning in view.warnings] == ["W001"]


def test_report_without_start() -> None:
    """A ledger without a start directive yields an empty view."""
    view = _extension("no_start.bean").report()
    assert view.has_start is False
    assert view.months == ()


def test_report_groups_by_parent() -> None:
    """Envelopes group by parent, with a per-month subtotal."""
    view = _extension("parent_groups.bean").report("2024-01", "1")
    assert [group.parent for group in view.groups] == ["Living", "Transport", None]
    living = view.groups[0]
    assert [envelope.name for envelope in living.envelopes] == ["Groceries", "Rent"]
    assert living.subtotal[0].closing == D("380")
    assert view.groups[1].subtotal[0].closing == D("250")
    assert [envelope.name for envelope in view.groups[2].envelopes] == ["Misc"]


def test_report_orders_groups_and_envelopes() -> None:
    """Parents and envelopes sort alphabetically; ungrouped envelopes come last.

    A leading number sorts as text, so ``10 Tax`` precedes ``2 Food``; a
    zero-padded prefix like ``02 Transport`` is how a caller pins the order.
    """
    view = _extension("parent_order.bean").report("2024-01", "1")
    assert [group.parent for group in view.groups] == [
        "01 Living",
        "02 Transport",
        "03 Numbered",
        None,
    ]
    living = view.groups[0]
    assert [envelope.name for envelope in living.envelopes] == ["Groceries", "Rent"]
    assert [envelope.name for envelope in view.groups[2].envelopes] == [
        "10 Tax",
        "2 Food",
    ]
    assert [envelope.name for envelope in view.groups[3].envelopes] == ["Misc"]
    assert [envelope.name for envelope in view.envelopes] == [
        "10 Tax",
        "2 Food",
        "Car",
        "Groceries",
        "Misc",
        "Rent",
    ]


def test_report_group_subtotal_excludes_closed_members() -> None:
    """A group subtotal sums only the members still open in the month."""
    view = _extension("parent_close.bean").report("2024-04", "1")
    living = next(group for group in view.groups if group.parent == "Living")
    assert living.subtotal[0].visible
    assert living.subtotal[0].closing == D("200")


def test_report_hides_group_when_all_members_closed() -> None:
    """A named group with no open members left is hidden for later months."""
    view = _extension("parent_close.bean").report("2024-04", "1")
    gone = next(group for group in view.groups if group.parent == "Gone")
    assert gone.subtotal[0].visible is False
    assert gone.visible is False


def test_report_shows_group_in_close_month() -> None:
    """A group is still shown in the month its members close."""
    view = _extension("parent_close.bean").report("2024-02", "1")
    gone = next(group for group in view.groups if group.parent == "Gone")
    assert gone.subtotal[0].visible is True


def test_template_hides_fully_closed_group() -> None:
    """The template omits a named group once all its members have closed."""
    html = _render(_extension("parent_close.bean"), {"month": "2024-04", "months": "1"})
    assert "Living" in html
    assert "Gone" not in html
    assert "Fun" not in html


def test_template_renders_groups() -> None:
    """The template brackets each parent category and totals on its heading."""
    html = _render(
        _extension("parent_groups.bean"), {"month": "2024-01", "months": "2"}
    )
    assert "envelope-table-scroll" in html
    assert "envelope-group" in html
    assert "Living" in html
    assert "Transport" in html
    assert "envelope-subtotal" not in html
    assert "380.00" in html
    # One table spans both visible months, four columns for the first
    # (Opening plus its three) and three for the second.
    assert html.count("<table") == 1
    assert html.count('colspan="4"') == 1
    assert html.count('colspan="3"') == 1


def test_template_groups_are_collapsible() -> None:
    """Each parent group is its own tbody with a toggle and a label."""
    html = _render(
        _extension("parent_groups.bean"), {"month": "2024-01", "months": "1"}
    )
    assert 'class="envelope-group-body"' in html
    assert 'class="envelope-toggle"' in html
    assert 'type="checkbox"' in html
    assert '<label for="envelope-group-' in html
    assert ":has(.envelope-toggle:checked)" in html


def test_template_renders() -> None:
    """The template renders balances and tooltips from the view."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "3"})
    assert "Envelopes" in html
    assert "Groceries" in html
    assert "380.00" in html
    assert "2024-03" in html
    assert "(projected)" in html


def test_template_renders_target_tooltip() -> None:
    """The template surfaces the envelope target and deadline."""
    html = _render(_extension("savings_goal.bean"), {"month": "2024-02", "months": "1"})
    assert "15000.00" in html
    assert "2028-06-01" in html


def test_template_renders_warning_panel() -> None:
    """Warnings are shown with a link to the errors page."""
    html = _render(
        _extension("mid_month_dip.bean"), {"month": "2024-01", "months": "1"}
    )
    assert "W001" in html
    assert "Warnings" in html
    assert "../../errors/" in html


def test_template_hides_closed_envelope_after_close() -> None:
    """The template drops a closed envelope from months after its close."""
    html = _render(
        _extension("closed_envelope.bean"), {"month": "2024-03", "months": "1"}
    )
    assert "Groceries" in html
    assert "Old" not in html


def test_template_shows_closed_envelope_in_close_month() -> None:
    """The template still shows a closed envelope in the month it was closed."""
    html = _render(
        _extension("closed_envelope.bean"), {"month": "2024-02", "months": "1"}
    )
    assert "Old" in html


def test_template_without_start() -> None:
    """A ledger without a start directive explains itself."""
    html = _render(_extension("no_start.bean"), {})
    assert "nothing to show" in html


def _row(html: str, marker: str) -> str:
    """Return the ``<tr>`` fragment that contains a marker.

    Args:
        html: The rendered template.
        marker: A substring unique to the row.

    Returns:
        The row's HTML, up to the next ``<tr``.
    """
    return next(row for row in html.split("<tr") if marker in row)


def _month_strip(html: str) -> str:
    """Return the selectable month strip fragment from a rendered template.

    Args:
        html: The rendered template.

    Returns:
        The strip's HTML, from its opening span up to the view tabs.
    """
    start = html.index('<span class="envelope-months">')
    end = html.index('<span class="envelope-nav-tabs">')
    return html[start:end]


def test_template_blanks_zero_envelope_values() -> None:
    """A leaf envelope's 0.00 values render blank, while the pool shows them."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "3"})
    groceries = _row(html, ">Groceries<")
    # Opening, and the projected months' zero allocated and activity, are blank.
    assert ">0.00<" not in groceries
    assert groceries.count(">380.00<") == 3
    assert ">0.00<" in _row(html, ">Pool<")


def test_template_pool_popover_shows_ledger() -> None:
    """The pool activity figure opens a ledger with a running balance."""
    html = _render(
        _extension("mid_month_dip.bean"), {"month": "2024-01", "months": "1"}
    )
    pool = _row(html, ">Pool<")
    assert "allocate Groceries" in pool
    assert "Salary" in pool
    assert "&rarr;" in pool
    assert "-500.00" in pool
    assert "2500.00" in pool
    assert 'id="pool-202401"' in pool


def test_template_hides_rounded_investment_zero() -> None:
    """A sub-cent cost basis does not leak a red -0.00 into the report."""
    html = _render(
        _extension("investment_rounding.bean"),
        {"month": "2026-05", "months": "2"},
    )
    taxable = _row(html, ">Taxable Investments<")
    assert "-0.00" not in taxable
    assert "0.00" not in taxable
    assert "envelope-neg" not in taxable
    assert "1.68" in taxable


def test_template_parent_subtotal_keeps_zeros() -> None:
    """A parent subtotal always shows its numbers, including zeros."""
    html = _render(
        _extension("parent_groups.bean"), {"month": "2024-01", "months": "1"}
    )
    assert ">0.00<" in _row(html, ">Living<")


def test_template_colors_overspent_closing() -> None:
    """A negative closing balance is marked red."""
    html = _render(
        _extension("overspend_carry.bean"), {"month": "2024-01", "months": "1"}
    )
    groceries = _row(html, ">Groceries<")
    assert "envelope-neg" in groceries
    assert "-30.00" in groceries


def test_template_colors_funding_status() -> None:
    """Closing balances are green when funded, amber when underfunded, and an
    over-funded balance carries an extra marker distinct from on-plan."""
    html = _render(
        _extension("funding_colors.bean"), {"month": "2024-01", "months": "1"}
    )
    assert "envelope-partial" in _row(html, ">Under<")
    on = _row(html, ">On<")
    assert "envelope-funded" in on
    assert "envelope-over" not in on
    over = _row(html, ">Over<")
    assert "envelope-funded" in over
    assert "envelope-over" in over
    assert "envelope-legend-swatch" in html


def test_template_marks_current_month(monkeypatch: pytest.MonkeyPatch) -> None:
    """The current month is labelled in the header and marked in the strip."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2024, 1, 1))
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "3"})
    assert "(current)" in _row(html, ">Envelope<")
    strip = _month_strip(html)
    assert (
        'class="envelope-month envelope-month-visible envelope-month-current"' in strip
    )
    assert 'href="?month=2024-01&amp;months=3"' in strip
    assert 'aria-label="January 2024"' in strip
    assert ">Jan<" in strip


def test_template_no_current_month_outside_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No month is labelled current when today is outside the view."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2019, 6, 1))
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "3"})
    assert "(current)" not in _row(html, ">Envelope<")
    assert "envelope-month-current" not in _month_strip(html)


def test_month_strip_caps_at_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    """The strip shows at most eight months, not the whole history."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2027, 1, 1))
    html = _render(_extension("disney_2028.bean"), {"month": "2027-03", "months": "2"})
    strip = _month_strip(html)
    assert strip.count('href="?month=') == 8
    for label in ("Jan", "Feb", "Dec"):
        assert f">{label}<" in strip
    # The block spans late 2026 through early 2027, so both years are marked.
    assert f">{2026}<" in strip
    assert f">{2027}<" in strip


def test_month_strip_blocks_around_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The block sits around the window, with one trailing month to step to."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2030, 1, 1))
    extension = _extension("disney_2028.bean")
    links = extension.month_nav(extension.report("2027-06", "2").months)
    assert [link.month for link in links] == [
        date(2027, 1, 1),
        date(2027, 2, 1),
        date(2027, 3, 1),
        date(2027, 4, 1),
        date(2027, 5, 1),
        date(2027, 6, 1),
        date(2027, 7, 1),
        date(2027, 8, 1),
    ]
    assert [link.visible for link in links] == [
        False,
        False,
        False,
        False,
        False,
        True,
        True,
        False,
    ]
    june = next(link for link in links if link.month == date(2027, 6, 1))
    assert june.query == "month=2027-06&months=2"
    assert june.current is False


def test_month_strip_steps_forward_with_single_month(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With one month shown, a trailing month is still there to step to."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2024, 1, 1))
    extension = _extension("basic.bean")
    links = extension.month_nav(extension.report("2024-01", "1").months)
    february = next(link for link in links if link.month == date(2024, 2, 1))
    assert february.query == "month=2024-02&months=1"
    assert february.visible is False


def test_month_strip_marks_visible_window() -> None:
    """Every month in the visible window is marked, and only those."""
    html = _render(_extension("disney_2028.bean"), {"month": "2027-03", "months": "3"})
    strip = _month_strip(html)
    assert strip.count("envelope-month-visible") == 3
    assert 'href="?month=2027-03&amp;months=3"' in strip


def test_month_strip_reaches_into_projected_months() -> None:
    """The strip runs through the visible window, past the last computed month."""
    html = _render(_extension("disney_2028.bean"), {"month": "2028-06", "months": "3"})
    strip = _month_strip(html)
    # The history ends in July 2028; the window's last month, August, is
    # projected and selectable, with September as the trailing step.
    assert 'href="?month=2028-08&amp;months=3"' in strip
    assert 'href="?month=2028-09&amp;months=3"' in strip
    assert strip.count("envelope-month-visible") == 3


def test_template_shows_today_link(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Today link jumps back to the current month, keeping the count."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2030, 1, 1))
    html = _render(_extension("disney_2028.bean"), {"month": "2027-03", "months": "2"})
    assert 'class="envelope-today"' in html
    assert 'href="?month=2030-01&amp;months=2">Today</a>' in html


def test_template_pins_name_column() -> None:
    """The name column is sized to the longest label, so it cannot resize."""
    extension = _extension("parent_groups.bean")
    html = _render(extension, {"month": "2024-01", "months": "1"})
    assert f"calc(max({extension.name_chars()}, 9) * 1ch + 3ch)" in html
    assert extension.name_chars() >= len("Envelope")


def test_template_freezes_header_and_footer() -> None:
    """The table header, name column and pool/cash rows are sticky."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    assert "position: sticky" in html
    assert "border-collapse: separate" in html
    assert "max-height: min(80vh, calc(100vh - 6rem))" in html
    assert "bottom: var(--envelope-total-row-height)" in html


def test_template_has_collapse_all() -> None:
    """One control collapses every parent group, and only when there are some."""
    html = _render(
        _extension("parent_groups.bean"), {"month": "2024-01", "months": "1"}
    )
    assert ":has(.envelope-collapse-all:checked)" in html
    nav = html[html.index('<p class="envelope-nav">') : html.index("</p>")]
    assert 'id="envelope-collapse-all"' in nav
    flat = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    assert 'id="envelope-collapse-all"' not in flat


def test_template_freezes_parent_headings() -> None:
    """A parent heading sticks under the table header while its group scrolls."""
    html = _render(
        _extension("parent_groups.bean"), {"month": "2024-01", "months": "1"}
    )
    assert "tbody.envelope-group-body > tr.envelope-group > *" in html
    assert "top: calc(2 * var(--envelope-header-row-height))" in html


# --- Writing (opt-in) ----------------------------------------------------------


def test_write_is_off_by_default() -> None:
    """Without a write target the report stays read-only."""
    extension = _extension("basic.bean")
    assert extension.writable is False
    assert extension.write_target is None


def test_config_parses_a_write_target(tmp_path: Path) -> None:
    """A write config resolves against the ledger directory."""
    extension = _extension(
        "basic.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    assert extension.writable is True
    assert extension.write_target == (tmp_path / "Envelope.bean").resolve()


def test_config_refuses_a_write_escape(tmp_path: Path) -> None:
    """A write target outside the ledger directory is an extension error."""
    with pytest.raises(ExtensionConfigError):
        _extension(
            "basic.bean",
            config="{'write': '../evil.bean'}",
            ledger_path=tmp_path / "main.bean",
        )


def test_config_refuses_a_non_source_write(tmp_path: Path) -> None:
    """A write target that is not Beancount is an extension error."""
    with pytest.raises(ExtensionConfigError):
        _extension(
            "basic.bean",
            config="{'write': 'notes.txt'}",
            ledger_path=tmp_path / "main.bean",
        )


def test_commit_endpoint_is_registered() -> None:
    """The commit and source endpoints are POST handlers."""
    extension = _extension("basic.bean")
    assert ("commit", "POST") in extension.endpoints
    assert ("source", "POST") in extension.endpoints


def test_perform_appends_through_the_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A submitted allocate form is appended to the configured file."""
    recorded: list[tuple[Path, object]] = []
    monkeypatch.setattr(
        fava_ext, "_insert", lambda _ledger, path, entry: recorded.append((path, entry))
    )
    extension = _extension(
        "basic.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    entries = extension.perform(
        {
            "action": "allocate",
            "envelope": "Groceries",
            "amount": "250",
            "date": "2024-02-10",
        }
    )
    assert len(entries) == 1
    assert actions.render(entries[0]) == (
        '2024-02-10 custom "envelope" "allocate" "Groceries" 250.00 USD'
    )
    assert recorded == [(extension.write_target, entries[0])]


def test_perform_from_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A `source` post is parsed and appended, the JS modal's path."""
    recorded: list[tuple[Path, object]] = []
    monkeypatch.setattr(
        fava_ext, "_insert", lambda _ledger, path, entry: recorded.append((path, entry))
    )
    extension = _extension(
        "basic.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    entries = extension.perform(
        {"source": ('2024-02-10 custom "envelope" "allocate" "Groceries" 250.00 USD\n')}
    )
    assert actions.render(entries[0]) == (
        '2024-02-10 custom "envelope" "allocate" "Groceries" 250.00 USD'
    )
    assert recorded == [(extension.write_target, entries[0])]


def test_perform_rejects_bad_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that does not parse is refused."""
    monkeypatch.setattr(fava_ext, "_insert", lambda *_args: None)
    extension = _extension(
        "basic.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    with pytest.raises(ValueError, match="Invalid token"):
        extension.perform({"source": "this is not beancount\n"})


def test_perform_rejects_non_envelope_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source with no envelope directive is refused."""
    monkeypatch.setattr(fava_ext, "_insert", lambda *_args: None)
    extension = _extension(
        "basic.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    with pytest.raises(ValueError, match="custom"):
        extension.perform({"source": "2024-01-01 open Assets:Checking USD\n"})


def test_render_source_from_fields() -> None:
    """The source endpoint renders the directive the form's fields describe."""
    extension = _extension("basic.bean")
    allocate = extension.render_source(
        {
            "action": "allocate",
            "envelope": "Groceries",
            "amount": "120",
            "date": "2024-01-31",
        }
    )
    assert allocate == (
        '2024-01-31 custom "envelope" "allocate" "Groceries" 120.00 USD'
    )
    unplan = extension.render_source(
        {"action": "unplan", "envelope": "Groceries", "date": "2024-02-01"}
    )
    assert unplan == '2024-02-01 custom "envelope" "unplan" "Groceries"'


def test_fill_date_is_the_month_end_or_today() -> None:
    """A past month's directive is dated its month end."""
    extension = _extension("basic.bean")
    assert extension.fill_date(date(2024, 1, 1)) == "2024-01-31"


def _extension_from_text(text: str) -> Envelopes:
    """Build an extension over inline ledger text.

    Args:
        text: Beancount source.

    Returns:
        The loaded extension.
    """
    entries, _errors, options = loader.load_string(text)
    ledger = cast("FavaLedger", SimpleNamespace(all_entries=entries, options=options))
    extension = Envelopes(ledger, None)
    extension.after_load_file()
    return extension


def _today() -> date:
    """Return the local current date, matching the extension's own clock."""
    return date.today()  # noqa: DTZ011 - local today is intended


def _monthly_ledger() -> str:
    """A monthly envelope funded this month, with room left for next month."""
    this_month = _today().replace(day=1)
    prior = (this_month - timedelta(days=1)).replace(day=1)
    opening = prior - timedelta(days=1)
    return (
        'option "operating_currency" "USD"\n'
        "2020-01-01 open Assets:Checking USD\n"
        "  cash-account: TRUE\n"
        "2020-01-01 open Expenses:Groceries USD\n"
        '  envelope: "Groceries"\n'
        "2020-01-01 open Equity:Opening-Balances USD\n"
        f'{opening} * "Opening"\n'
        "  Assets:Checking           10000.00 USD\n"
        "  Equity:Opening-Balances -10000.00 USD\n"
        f'{prior} custom "envelope" "start"\n'
        f'{prior} custom "envelope" "open" "Groceries"\n'
        "  monthly: 300.00\n"
        f'{this_month} custom "envelope" "allocate" "Groceries" 300.00 USD\n'
        f'{this_month} * "Groceries"\n'
        "  Expenses:Groceries   120.00 USD\n"
        "  Assets:Checking     -120.00 USD\n"
    )


def _next_month() -> date:
    """Return the first day of next month, relative to the real today."""
    return (_today().replace(day=1) + timedelta(days=32)).replace(day=1)


def _month_end(month: date) -> date:
    """Return the last calendar day of a month."""
    first = month.replace(day=1)
    return (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def test_fill_day_dates_future_months_in_that_month() -> None:
    """A future month dates its last day, so a directive counts toward it (#22)."""
    nxt = _next_month()
    assert fava_ext._fill_day(nxt) == _month_end(nxt)
    assert fava_ext._fill_day(_today()) == _today()
    assert fava_ext._fill_day(date(2024, 1, 1)) == date(2024, 1, 31)


def test_fill_date_for_a_future_month() -> None:
    """A future month's form dates a directive inside that month (#22)."""
    extension = _extension("basic.bean")
    nxt = _next_month()
    assert extension.fill_date(nxt) == _month_end(nxt).isoformat()


def test_prefill_interprets_the_actioned_month() -> None:
    """A future month suggests its own plan need, not the current month's (#22)."""
    extension = _extension_from_text(_monthly_ledger())
    this_month = _today().replace(day=1)
    assert extension.prefill("Groceries", this_month) == "0.00"
    assert extension.prefill("Groceries", _next_month()) == "120.00"


def test_month_action_dates_a_future_month() -> None:
    """The month funding action dates its directives inside the future month (#22)."""
    extension = _extension_from_text(_monthly_ledger())
    entries = fava_ext._month_entries(
        "fully_fund", {"month": _next_month().strftime("%Y-%m")}, extension._result()
    )
    assert [entry.date for entry in entries] == [_month_end(_next_month())]


def test_plan_fields_open_on_the_current_plan() -> None:
    """The plan form opens on the plan in force."""
    extension = _extension("topup_plan.bean")
    fields = extension.plan_fields("Mortgage", date(2024, 1, 1))
    assert fields["monthly"] == "2500.00"
    assert fields["priority"] == "1"
    empty = _extension("basic.bean").plan_fields("Groceries", date(2024, 1, 1))
    assert empty == {
        "monthly": "",
        "up_to": "",
        "priority": "",
        "goal": "",
        "by": "",
    }


def test_plan_fields_include_up_to() -> None:
    """The plan form opens on an ``up-to`` ceiling (#22)."""
    fields = _extension("up_to.bean").plan_fields("Car", date(2024, 1, 1))
    assert fields["monthly"] == "250.00"
    assert fields["up_to"] == "600.00"


def test_plan_text_shows_up_to() -> None:
    """The plan popover describes an ``up-to`` ceiling (#22)."""
    extension = _extension("up_to.bean")
    view = extension.report("2024-03", "1")
    car = next(e for e in view.envelopes if e.name == "Car")
    assert extension.plan_text(car.plans[0]) == "open: monthly 250.00 · up to 600.00"


def test_funding_text_shows_up_to() -> None:
    """The closing popover names the ceiling and the plan status (#22)."""
    extension = _extension("up_to.bean")
    view = extension.report("2024-03", "1")
    car = next(e for e in view.envelopes if e.name == "Car")
    text = extension.funding_text(car.cells[0])
    assert "level 600.00 = min(ceiling 600.00, opening 500.00 + monthly 250.00)" in text
    assert "funded 500.00 = closing balance" in text
    assert text.endswith("underfunded by 100.00\ntop up 100.00")


def test_action_entry_plan_includes_up_to() -> None:
    """The plan action writes the ``up-to`` ceiling it names (#22)."""
    extension = _extension("basic.bean")
    entry = fava_ext._action_entry(
        {
            "action": "plan",
            "envelope": "Groceries",
            "monthly": "250",
            "up_to": "600",
            "date": "2024-02-01",
        },
        extension._result(),
    )
    assert actions.render(entry) == (
        '2024-02-01 custom "envelope" "plan" "Groceries"\n  monthly: 250\n  up-to: 600'
    )


def test_template_renders_up_to_tooltip() -> None:
    """The template surfaces the ``up-to`` ceiling in the closing popover (#22)."""
    html = _render(_extension("up_to.bean"), {"month": "2024-03", "months": "1"})
    assert "up to 600.00" in html


def test_js_module_is_shipped() -> None:
    """The extension advertises and ships its JavaScript module."""
    assert Envelopes.has_js_module is True
    assert (Path(fava_ext.__file__).parent / "Envelopes.js").exists()


def test_js_module_remembers_scroll() -> None:
    """The JavaScript module restores the table's scroll position on reload.

    There is no JavaScript runtime in this project, so this only pins the
    contract: the hook restores the scroll box from sessionStorage (#28).
    """
    source = (Path(fava_ext.__file__).parent / "Envelopes.js").read_text()
    assert "envelope-table-scroll" in source
    assert "sessionStorage" in source
    assert "track_scroll()" in source.split("onExtensionPageLoad(ctx)")[1]


def test_js_module_refreshes_after_save() -> None:
    """The JavaScript module refreshes the report after it saves an edit.

    It clicks Fava's own reload button, so it does not wait for the watcher's
    poll or the yellow reload indicator (#28).
    """
    source = (Path(fava_ext.__file__).parent / "Envelopes.js").read_text()
    assert "button.reload-page" in source
    assert "\n  refresh();" in source


def test_js_module_places_popovers() -> None:
    """The module sizes and places popovers so they stay inside the viewport.

    There is no JavaScript runtime in this project, so this only pins the
    contract: on open the popover is measured and switched to a fixed overlay
    that flips side to fit (#43).
    """
    source = (Path(fava_ext.__file__).parent / "Envelopes.js").read_text()
    assert "setup_popovers()" in source.split("onExtensionPageLoad(ctx)")[1]
    assert "function place_popover(" in source
    assert "function reset_popover(" in source
    assert "function float_popover(" in source
    assert "getBoundingClientRect" in source
    assert "envelope-pop-floating" in source
    assert 'window.addEventListener("scroll", place_open_popover, true)' in source


def test_template_popovers_size_to_content() -> None:
    """The stylesheet sizes ledger popovers to their widest line (#43)."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    assert "envelope-pop-floating" in html
    assert "width: max-content" in html


def test_perform_requires_a_write_target() -> None:
    """Applying an edit without a write target is refused."""
    extension = _extension("basic.bean")
    with pytest.raises(RuntimeError, match="not configured"):
        extension.perform(
            {"action": "allocate", "envelope": "Groceries", "amount": "1"}
        )


def test_perform_unknown_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown action is a value error."""
    monkeypatch.setattr(fava_ext, "_insert", lambda *_args: None)
    extension = _extension(
        "basic.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    with pytest.raises(ValueError, match="unknown action"):
        extension.perform({"action": "bogus"})


def test_perform_fully_funds_a_month(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fully-fund action appends one allocation per envelope that needs it."""
    recorded: list[object] = []
    monkeypatch.setattr(
        fava_ext, "_insert", lambda _ledger, _path, entry: recorded.append(entry)
    )
    extension = _extension(
        "prefill.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    entries = extension.perform({"action": "fully_fund", "month": "2024-01"})
    rendered = [actions.render(entry) for entry in entries]
    assert any(line.endswith('"Groceries" 50.00 USD') for line in rendered)
    assert any(line.endswith('"Rent" 2200.00 USD') for line in rendered)
    assert any(line.endswith('"Car" 823.08 USD') for line in rendered)
    assert any(line.endswith('"Utilities" 200.00 USD') for line in rendered)
    assert len(recorded) == len(entries)


def test_render_source_expands_a_month_action() -> None:
    """A month action renders one allocate directive per envelope."""
    extension = _extension("prefill.bean")
    source = extension.render_source({"action": "fund_available", "month": "2024-01"})
    lines = source.splitlines()
    assert any('"Rent"' in line and "2200.00 USD" in line for line in lines)
    assert any('"Groceries"' in line and "50.00 USD" in line for line in lines)


def test_perform_rejects_an_empty_month_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A month with nothing to fund is refused."""
    monkeypatch.setattr(fava_ext, "_insert", lambda *_args: None)
    extension = _extension(
        "basic.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    with pytest.raises(ValueError, match="nothing to fund"):
        extension.perform({"action": "fully_fund", "month": "2024-01"})


def test_action_entry_covers_every_verb() -> None:
    """Each action builds the directive it names."""
    extension = _extension("basic.bean")
    result = extension._result()
    allocate = fava_ext._action_entry(
        {
            "action": "allocate",
            "envelope": "Groceries",
            "amount": "10",
            "date": "2024-02-01",
        },
        result,
    )
    plan = fava_ext._action_entry(
        {
            "action": "plan",
            "envelope": "Groceries",
            "monthly": "avg3",
            "priority": "2",
            "date": "2024-02-01",
        },
        result,
    )
    unplan = fava_ext._action_entry(
        {"action": "unplan", "envelope": "Groceries", "date": "2024-02-01"}, result
    )
    assert actions.render(allocate).endswith('"Groceries" 10.00 USD')
    assert actions.render(plan) == (
        '2024-02-01 custom "envelope" "plan" "Groceries"\n'
        '  monthly: "avg3"\n'
        "  priority: 2"
    )
    assert actions.render(unplan).endswith('"unplan" "Groceries"')


def test_prefill_covers_uncovered_spending() -> None:
    """An unplanned envelope prefills only the spending it has not covered."""
    extension = _extension("fund_backfill.bean")
    assert extension.prefill("Groceries", date(2024, 1, 1)) == "100.00"
    # The March refund still leaves a -50 balance, which May carries forward.
    assert extension.prefill("Groceries", date(2024, 5, 1)) == "50.00"
    assert extension.prefill("Nope", date(2024, 1, 1)) == "0.00"


def test_prefill_funds_the_plan_level() -> None:
    """A planned envelope is prefilled with its plan top-up, not its spending."""
    extension = _extension("topup_plan.bean")
    assert extension.prefill("Mortgage", date(2024, 1, 1)) == "2500.00"


def test_prefill_accounts_for_existing_allocations() -> None:
    """A partly funded envelope prefills only the remaining gap (#35)."""
    extension = _extension("prefill.bean")
    assert extension.prefill("Groceries", date(2024, 1, 1)) == "50.00"
    assert extension.prefill("Rent", date(2024, 1, 1)) == "2200.00"
    assert extension.prefill("Car", date(2024, 1, 1)) == "823.08"


def test_cover_prefill_suggests_overspending() -> None:
    """The cover quick-fill is the amount that brings the month back to zero."""
    extension = _extension("prefill.bean")
    assert extension.cover_prefill("Groceries", date(2024, 1, 1)) == "50.00"
    assert extension.cover_prefill("Rent", date(2024, 1, 1)) == "400.00"
    assert extension.cover_prefill("Car", date(2024, 1, 1)) == "0.00"
    assert extension.cover_prefill("Nope", date(2024, 1, 1)) == "0.00"


def test_prefill_leads_with_cover_when_plan_is_met() -> None:
    """An already-funded but overspent envelope leads with the cover (#35)."""
    extension = _extension("prefill.bean")
    assert extension.prefill("Utilities", date(2024, 1, 1)) == "200.00"
    assert extension.cover_prefill("Utilities", date(2024, 1, 1)) == "200.00"


def test_report_exposes_allocations() -> None:
    """Each allocation figure carries the directives behind it and their hash."""
    view = _extension("basic.bean").report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    allocations = groceries.cells[0].allocations
    assert len(allocations) == 1
    line = allocations[0]
    assert line.amount == D("500")
    assert line.date == date(2024, 1, 1)
    assert len(line.entry_hash) == 32


def test_report_exposes_plan_history() -> None:
    """Every plan directive, the open base first, is on the envelope view."""
    view = _extension("topup_plan.bean").report("2024-01", "1")
    mortgage = next(e for e in view.envelopes if e.name == "Mortgage")
    assert mortgage.plans[0].base is True
    assert mortgage.plans[0].monthly == D("2500")
    changes = mortgage.plans[1:]
    assert changes
    assert all(line.entry_hash for line in mortgage.plans)


def test_report_plan_history_marks_clears() -> None:
    """An unplan change is recorded as a clearing plan line."""
    view = _extension("unplan.bean").report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    assert any(line.clears for line in groceries.plans)


def test_json_includes_allocations_and_plans() -> None:
    """The JSON view carries the new linkage data too."""
    view = _extension("basic.bean").report("2024-01", "1")
    data = to_jsonable(view)
    envelopes = cast("list[dict[str, object]]", data["envelopes"])
    groceries = next(e for e in envelopes if e["name"] == "Groceries")
    assert groceries["plans"]
    cells = cast("list[dict[str, object]]", groceries["cells"])
    allocations = cast("list[dict[str, object]]", cells[0]["allocations"])
    assert allocations[0]["entry_hash"]


def test_template_lifts_open_popover_above_sticky_layers() -> None:
    """An open popover's host is raised above the sticky header and headings."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    assert "td.envelope-pop-host:has(.envelope-pop-toggle:checked)" in html
    assert "z-index: 6" in html


def test_template_read_only_shows_no_forms() -> None:
    """A read-only report shows the figures but no create forms."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    assert 'name="action"' not in html
    assert "<form" not in html


def test_template_shows_edit_popovers_when_writable(tmp_path: Path) -> None:
    """A writable report shows the in-table edit popovers."""
    extension = _extension(
        "basic.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    html = _render(extension, {"month": "2024-01", "months": "1"})
    assert "envelope-alloc-pop" in html
    assert "envelope-plan-pop" in html
    assert '<div class="envelope-pop' in html
    assert "data-create" in html
    assert 'action="/ledger/extension/Envelopes/commit"' in html
    assert 'name="action" value="allocate"' in html
    assert 'name="action" value="plan"' in html
    assert 'name="action" value="unplan"' in html
    assert 'name="action" value="fully_fund"' in html
    assert 'name="action" value="fund_available"' in html
    assert "envelope-month-pop" in html
    assert "title=" not in html


def test_template_plan_popover_shows_read_only_info() -> None:
    """A planned envelope shows its plan on click even without write access."""
    html = _render(_extension("savings_goal.bean"), {"month": "2024-02", "months": "1"})
    assert "envelope-plan-pop" in html
    assert "15000.00" in html
    assert "2028-06-01" in html
    assert "<form" not in html


# --- Reports ---------------------------------------------------------------


def test_reports_tab_is_rendered() -> None:
    """The reports view offers the balance row and every ranking table."""
    html = _render(_extension("parent_groups.bean"), {"view": "reports"})
    assert "Reports" in html
    assert "Cash and pool" in html
    assert "Top spending" in html
    assert "Top allocated" in html
    assert "Top ending balance" in html
    assert "<svg" not in html
    # The balance row carries the cash and pool balances for the month.
    assert ">Cash<" in html
    assert ">Pool<" in html
    assert "3030.00" in html
    assert "2300.00" in html


def test_reports_balance_row_is_first() -> None:
    """The cash and pool balance row comes before the ranking rows."""
    html = _render(_extension("parent_groups.bean"), {"view": "reports"})
    assert html.index(">Cash and pool<") < html.index(">Top spending<")


def test_reports_is_not_shown_by_default() -> None:
    """The default report view is the ledger grid, not the reports."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    assert "Cash and pool" not in html
    assert "<svg" not in html
    assert 'href="?view=reports"' in html


def test_template_puts_tabs_on_the_nav_row() -> None:
    """The view tabs share the month-nav row, to save vertical space."""
    html = _render(
        _extension("parent_groups.bean"), {"month": "2024-01", "months": "1"}
    )
    nav = html[html.index('<p class="envelope-nav">') : html.index("</p>")]
    assert "envelope-months" in nav
    assert 'class="envelope-month ' in nav
    assert "envelope-nav-tabs" in nav
    assert ">Envelopes<" in nav
    assert ">Reports<" in nav
    assert "envelope-tabs" not in html


def test_reports_tabs_share_the_row() -> None:
    """On the reports the tabs sit in the same row, with no month strip."""
    html = _render(_extension("basic.bean"), {"view": "reports"})
    nav = html[html.index('<p class="envelope-nav">') : html.index("</p>")]
    assert ">Reports<" in nav
    assert "envelope-months" not in nav


def test_reports_nav_is_not_sticky() -> None:
    """The nav only sticks on the ledger grid; on the reports it scrolls away."""
    html = _render(_extension("basic.bean"), {"view": "reports"})
    assert ".envelope-report:not(:has(.envelope-reports)) .envelope-nav" in html


def test_reports_rows_share_one_scroll() -> None:
    """Every report row sits in one scroll, with fixed aligned month columns."""
    html = _render(_extension("parent_groups.bean"), {"view": "reports"})
    assert html.count('class="envelope-reports-strip"') == 1
    assert html.count('class="envelope-reports-row"') == 4
    assert "envelope-reports-months" in html
    # The columns share widths derived from the longest name and amount.
    assert "--envelope-reports-name-chars: 9" in html
    assert "--envelope-reports-amount-chars: 7" in html
    assert "table-layout: fixed" in html
    assert "envelope-reports-name-col" in html
    assert "--envelope-reports-name-width" in html
    assert "--envelope-reports-amount-width" in html
    # Each row's title stays pinned while its months scroll horizontally.
    assert ".envelope-reports-row h3" in html
    assert "width: max-content" in html


def test_reports_without_start() -> None:
    """A ledger with no start still has nothing to show on the reports."""
    html = _render(_extension("no_start.bean"), {"view": "reports"})
    assert "nothing to show" in html


def test_filter_range_without_a_request_is_none() -> None:
    """Outside a Flask request there is no active filter."""
    assert _extension("basic.bean")._filter_range() is None


def test_reports_span_full_history_without_a_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no date filter the reports span every computed month."""
    extension = _extension("fund_backfill.bean")
    monkeypatch.setattr(Envelopes, "_filter_range", lambda _self: None)
    reports = extension.reports()
    assert [month.month for month in reports.rows[0].months] == [
        date(2024, 1, 1),
        date(2024, 2, 1),
        date(2024, 3, 1),
    ]


def test_reports_respect_the_date_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """A window date filter narrows the reports to the months in range."""
    extension = _extension("fund_backfill.bean")
    monkeypatch.setattr(
        Envelopes,
        "_filter_range",
        lambda _self: (date(2024, 2, 1), date(2024, 2, 28)),
    )
    reports = extension.reports()
    assert [month.month for month in reports.rows[0].months] == [date(2024, 2, 1)]


def test_template_reports_hide_months_outside_the_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reports tab renders only the months the date filter selects."""
    extension = _extension("fund_backfill.bean")
    monkeypatch.setattr(
        Envelopes,
        "_filter_range",
        lambda _self: (date(2024, 2, 1), date(2024, 2, 28)),
    )
    html = _render(extension, {"view": "reports"})
    assert "2024-02" in html
    assert "2024-01" not in html
    assert "2024-03" not in html


def test_template_reports_show_a_note_when_the_filter_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filter that excludes every month says so instead of blank tables."""
    extension = _extension("fund_backfill.bean")
    monkeypatch.setattr(
        Envelopes,
        "_filter_range",
        lambda _self: (date(2030, 1, 1), date(2030, 1, 31)),
    )
    html = _render(extension, {"view": "reports"})
    assert "No months in the selected date range." in html
    assert "Top spending" not in html


def test_prefill_reason_explains_the_suggestion() -> None:
    """The add-allocation note spells out the prefilled amount's derivation."""
    extension = _extension("up_to.bean")
    lines = extension.prefill_reason("Car", date(2024, 3, 1))
    assert (
        lines[0]
        == "level 600.00 = min(ceiling 600.00, opening 500.00 + monthly 250.00)"
    )
    assert lines[-1] == "top up 100.00"
    assert extension.prefill_reason("Nope", date(2024, 3, 1)) == ()


def test_template_shows_the_prefill_reason(
    tmp_path: Path,
) -> None:
    """The allocation popover shows why the suggested amount is suggested."""
    extension = _extension(
        "up_to.bean",
        config="{'write': 'Envelope.bean'}",
        ledger_path=tmp_path / "main.bean",
    )
    html = _render(extension, {"month": "2024-03", "months": "1"})
    assert "envelope-prefill-reason" in html
    assert "top up 100.00" in html
