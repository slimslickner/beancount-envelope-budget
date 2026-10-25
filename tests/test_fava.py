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


def test_envelope_target_tooltip() -> None:
    """The envelope tooltip shows the target and deadline."""
    view = _extension("savings_goal.bean").report("2024-02", "1")
    car = next(e for e in view.envelopes if e.name == "Car")
    title = _extension("savings_goal.bean").envelope_title(car)
    assert "15000.00" in title
    assert "2028-06-01" in title


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


def test_template_renders_groups() -> None:
    """The template brackets each parent category and totals on its heading."""
    html = _render(
        _extension("parent_groups.bean"), {"month": "2024-01", "months": "2"}
    )
    assert "envelope-months" in html
    assert "envelope-group" in html
    assert "Living" in html
    assert "Transport" in html
    assert "envelope-subtotal" not in html
    assert "380.00" in html


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


def test_template_without_start() -> None:
    """A ledger without a start directive explains itself."""
    html = _render(_extension("no_start.bean"), {})
    assert "nothing to show" in html
