"""Tests for the read-only Fava extension and its template."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import jinja2
import pytest
from beancount import loader
from fava.ext import ExtensionConfigError, find_extensions

from beancount_envelope_budget import fava_ext
from beancount_envelope_budget.fava_ext import Envelopes

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
) -> Envelopes:
    """Build an extension over a fixture ledger.

    Args:
        name: The fixture file name.
        load: Whether to run ``after_load_file``.
        config: An optional extension config string.

    Returns:
        The extension.
    """
    entries, _errors, options = loader.load_file(_LEDGERS / name)
    ledger = cast("FavaLedger", SimpleNamespace(all_entries=entries, options=options))
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
    request = SimpleNamespace(args=dict(args))
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


def test_report_defaults_to_three_months() -> None:
    """The default view spans the latest month plus the next two."""
    view = _extension("basic.bean").report()
    assert view.has_start is True
    assert view.months == (date(2024, 1, 1), date(2024, 2, 1), date(2024, 3, 1))
    assert view.projected == (False, True, True)


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


def test_report_clamps_and_parses() -> None:
    """Malformed parameters fall back and an early month is clamped up."""
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
    """An activity figure reveals links to Fava's entry popup."""
    extension = _extension("fund_backfill.bean")
    view = extension.report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    entry_hash = groceries.cells[0].detail[0].entry_hash
    html = _render(extension, {"month": "2024-01", "months": "1"})
    assert 'class="num envelope-activity" tabindex="0"' in html
    assert f'href="#context-{entry_hash}"' in html
    assert "January groceries" in html


def test_envelope_target_tooltip() -> None:
    """The envelope tooltip shows the target and deadline."""
    view = _extension("savings_goal.bean").report("2024-02", "1")
    car = next(e for e in view.envelopes if e.name == "Car")
    title = _extension("savings_goal.bean").envelope_title(car)
    assert "15000.00" in title
    assert "2028-06-01" in title


def test_report_goal_fill() -> None:
    """A goal cell carries its target and how full it is toward it."""
    view = _extension("savings_goal.bean").report("2024-01", "1")
    car = next(e for e in view.envelopes if e.name == "Car")
    cell = car.cells[0]
    assert cell.target == D("15000")
    assert cell.fill is not None
    assert D("0") < cell.fill < D("1")


def test_report_monthly_fill_clamps() -> None:
    """A monthly envelope fills to its level, clamped at a whole ring."""
    view = _extension("funding_colors.bean").report("2024-01", "1")
    fills = {e.name: e.cells[0].fill for e in view.envelopes}
    assert fills["Under"] == D("0.5")
    assert fills["On"] == D("1")
    assert fills["Over"] == D("1")


def test_report_no_fill_without_a_plan() -> None:
    """An envelope with no plan has nothing to fill toward."""
    view = _extension("basic.bean").report("2024-01", "1")
    groceries = next(e for e in view.envelopes if e.name == "Groceries")
    assert groceries.cells[0].fill is None
    assert groceries.cells[0].target is None


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
    assert "Envelope report" in html
    assert "Groceries" in html
    assert "380.00" in html
    assert "2024-03" in html
    assert "(projected)" in html
    assert 'title="' in html


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


def test_template_blanks_zero_envelope_values() -> None:
    """A leaf envelope's 0.00 values render blank, while the pool shows them."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "3"})
    groceries = _row(html, ">Groceries<")
    # Opening, and the projected months' zero allocated and activity, are blank.
    assert ">0.00<" not in groceries
    assert groceries.count(">380.00<") == 3
    assert ">0.00<" in _row(html, ">Pool<")


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
    """Closing balances are green when funded and amber when underfunded."""
    html = _render(
        _extension("funding_colors.bean"), {"month": "2024-01", "months": "1"}
    )
    assert "envelope-partial" in _row(html, ">Under<")
    assert "envelope-funded" in _row(html, ">On<")
    assert "envelope-funded" in _row(html, ">Over<")


def test_template_marks_current_month(monkeypatch: pytest.MonkeyPatch) -> None:
    """The current month is labelled in the header and linked from the nav."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2024, 1, 1))
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "3"})
    assert "(current)" in _row(html, ">Envelope<")
    assert "month=2024-01&amp;months=3" in html
    # The this-month link sits between previous and next.
    nav = html.split("<tr")[0]
    assert (
        nav.index("previous month") < nav.index("this month") < nav.index("next month")
    )
    assert "last 3 months" not in nav
    assert "next 3 months" not in nav


def test_template_no_current_month_outside_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No month is labelled current when today is outside the view."""
    monkeypatch.setattr(Envelopes, "today_month", lambda _self: date(2019, 6, 1))
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "3"})
    assert "(current)" not in _row(html, ">Envelope<")


def test_template_freezes_header_and_footer() -> None:
    """The table header, name column and pool/cash rows are sticky."""
    html = _render(_extension("basic.bean"), {"month": "2024-01", "months": "1"})
    assert "position: sticky" in html
    assert "border-collapse: separate" in html
    assert "max-height: 80vh" in html
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
