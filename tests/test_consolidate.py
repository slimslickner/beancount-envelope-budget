"""Tests for consolidating ``allocate`` directives: planner, writer and CLI."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount.core.data import Custom
from beancount.parser import parser

from beancount_envelope_budget import actions, cli, consolidate, writer

D = Decimal

_ALLOCATE_VERB = 'custom "envelope" "allocate"'


def _parse(source: str) -> list[Custom]:
    """Parse a ledger string into its ``custom "envelope"`` entries.

    Args:
        source: The Beancount source.

    Returns:
        The parsed custom directives.
    """
    entries, _errors, _options = parser.parse_string(source)
    return [entry for entry in entries if isinstance(entry, Custom)]


def _allocate(envelope: str, amount: str, day: str = "2024-01-05") -> str:
    """Build one ``allocate`` source line.

    Args:
        envelope: The envelope name.
        amount: The amount, as text.
        day: The date.

    Returns:
        The source line, with a trailing newline.
    """
    return f'{day} {_ALLOCATE_VERB} "{envelope}" {amount} USD\n'


def test_directive_day_uses_today_for_the_current_month() -> None:
    """The current month dates today; any other dates its last day."""
    assert consolidate.directive_day(date(2024, 1, 15), date(2024, 1, 10)) == date(
        2024, 1, 10
    )
    assert consolidate.directive_day(date(2024, 1, 15), date(2024, 3, 3)) == date(
        2024, 1, 31
    )
    assert consolidate.directive_day(date(2023, 2, 15), date(2024, 3, 3)) == date(
        2023, 2, 28
    )


def test_plan_merges_a_month_into_one_directive() -> None:
    """Two January directives become one, dated month-end, at the later site."""
    entries = _parse(
        _allocate("Groceries", "100.00") + _allocate("Groceries", "250.00")
    )
    plan = consolidate.plan_consolidation(
        entries, currency="USD", today=date(2024, 2, 1)
    )
    assert len(plan.groups) == 1
    group = plan.groups[0]
    assert (group.envelope, group.month, group.amount, group.directives) == (
        "Groceries",
        date(2024, 1, 1),
        D("350.00"),
        2,
    )
    # The replacement keeps the later directive's line (2); line 1 is deleted.
    assert [(edit.lineno, edit.entry is None) for edit in plan.edits] == [
        (2, False),
        (1, True),
    ]
    replacement = plan.edits[0].entry
    assert replacement is not None
    assert actions.render(replacement).startswith("2024-01-31")


def test_plan_leaves_a_single_directive_alone() -> None:
    """A month with one directive is not a candidate."""
    entries = _parse(_allocate("Groceries", "100.00"))
    plan = consolidate.plan_consolidation(
        entries, currency="USD", today=date(2024, 2, 1)
    )
    assert plan.edits == ()
    assert plan.groups == ()


def test_plan_deletes_a_net_zero_month() -> None:
    """A month whose allocations cancel keeps no directive."""
    entries = _parse(
        _allocate("Groceries", "100.00", "2024-01-05")
        + _allocate("Groceries", "-100.00", "2024-01-20")
    )
    plan = consolidate.plan_consolidation(
        entries, currency="USD", today=date(2024, 2, 1)
    )
    assert all(edit.entry is None for edit in plan.edits)
    assert len(plan.edits) == 2
    assert plan.groups[0].amount == D("0.00")


def test_plan_filters_by_envelope_and_month() -> None:
    """The envelope and month filters narrow which groups are merged."""
    source = (
        _allocate("Groceries", "100.00", "2024-01-05")
        + _allocate("Groceries", "100.00", "2024-02-05")
        + _allocate("Gifts", "100.00", "2024-01-05")
        + _allocate("Gifts", "100.00", "2024-01-20")
    )
    entries = _parse(source)
    by_envelope = consolidate.plan_consolidation(
        entries,
        currency="USD",
        today=date(2024, 3, 1),
        envelope=re.compile("Gifts"),
    )
    assert [group.envelope for group in by_envelope.groups] == ["Gifts"]
    by_month = consolidate.plan_consolidation(
        entries, currency="USD", today=date(2024, 3, 1), month=date(2024, 2, 1)
    )
    assert by_month.edits == ()


def test_plan_skips_a_group_with_no_source() -> None:
    """A directive without a source location blocks its whole group."""
    entries = _parse(_allocate("Groceries", "100.00") + _allocate("Groceries", "50.00"))
    entries[1] = entries[1]._replace(meta={})
    plan = consolidate.plan_consolidation(
        entries, currency="USD", today=date(2024, 2, 1)
    )
    assert plan.edits == ()
    assert plan.groups == ()
    assert len(plan.skipped) == 1
    assert "Groceries 2024-01" in plan.skipped[0]


def test_apply_edits_replaces_and_deletes_from_the_bottom() -> None:
    """Edits apply bottom-up so each line number still names its directive."""
    lines = [
        f"{_allocate('A', '100.00', '2024-01-05')}",
        f"{_allocate('A', '250.00', '2024-01-20')}",
    ]
    replacement = _parse(_allocate("A", "350.00", "2024-01-31"))[0]
    updated = writer.apply_edits(lines, [(2, replacement), (1, None)])
    assert updated == ['2024-01-31 custom "envelope" "allocate" "A" 350.00 USD\n']


def test_apply_edits_preserves_crlf() -> None:
    """A CRLF file stays CRLF after an edit."""
    lines = ['2024-01-05 custom "envelope" "allocate" "A" 100.00 USD\r\n']
    updated = writer.apply_edits(lines, [(1, None)])
    assert updated == []


def _write_ledger(tmp_path: Path) -> Path:
    """Write a small ledger with two allocations in one month.

    Args:
        tmp_path: The pytest temporary directory.

    Returns:
        The ledger path.
    """
    path = tmp_path / "main.bean"
    path.write_text(
        'option "operating_currency" "USD"\n'
        "2020-01-01 open Assets:Checking USD\n"
        "  on-budget: TRUE\n"
        "2020-01-01 open Equity:Opening-Balances USD\n"
        "2020-01-01 open Expenses:Groceries USD\n"
        '  envelope: "Groceries"\n'
        '2023-12-31 * "Opening"\n'
        "  Assets:Checking           1000.00 USD\n"
        "  Equity:Opening-Balances -1000.00 USD\n"
        '2024-01-01 custom "envelope" "start"\n'
        '2024-01-01 custom "envelope" "open" "Groceries"\n'
        f"{_allocate('Groceries', '400.00', '2024-01-05')}"
        f"{_allocate('Groceries', '250.00', '2024-01-20')}",
        encoding="utf-8",
    )
    return path


def test_cli_consolidate_prints_a_diff(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bare run prints the diff and changes nothing."""
    path = _write_ledger(tmp_path)
    before = path.read_text(encoding="utf-8")
    code = cli.main(["consolidate", str(path)])
    assert code == 0
    out = capsys.readouterr().out
    assert '+2024-01-31 custom "envelope" "allocate" "Groceries" 650.00 USD' in out
    assert '-2024-01-05 custom "envelope" "allocate" "Groceries" 400.00 USD' in out
    assert path.read_text(encoding="utf-8") == before


def test_cli_consolidate_writes_and_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--write merges in place; a second run has nothing to do."""
    path = _write_ledger(tmp_path)
    assert cli.main(["consolidate", str(path), "--write"]) == 0
    assert "consolidated 1 envelope-month(s)" in capsys.readouterr().out
    written = path.read_text(encoding="utf-8")
    assert '2024-01-31 custom "envelope" "allocate" "Groceries" 650.00 USD' in written
    assert "2024-01-05" not in written
    # Idempotent, and the ledger still checks clean.
    assert cli.main(["consolidate", str(path)]) == 0
    assert "nothing to consolidate" in capsys.readouterr().err
    assert cli.main(["check", str(path)]) == 0


def test_cli_consolidate_reports_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ledger with no merges reports nothing and exits zero."""
    path = tmp_path / "main.bean"
    path.write_text(
        'option "operating_currency" "USD"\n'
        "2020-01-01 open Assets:Checking USD\n"
        "  on-budget: TRUE\n"
        '2024-01-01 custom "envelope" "start"\n'
        '2024-01-01 custom "envelope" "open" "A"\n'
        f"{_allocate('A', '10.00')}",
        encoding="utf-8",
    )
    assert cli.main(["consolidate", str(path)]) == 0
    assert "nothing to consolidate" in capsys.readouterr().err


def test_cli_consolidate_no_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ledger with no start directive has nothing to consolidate."""
    path = tmp_path / "main.bean"
    path.write_text('option "operating_currency" "USD"\n', encoding="utf-8")
    assert cli.main(["consolidate", str(path)]) == 0
    assert "no start directive" in capsys.readouterr().err


def test_cli_consolidate_bad_envelope_regex(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed --envelope pattern is a usage error."""
    path = _write_ledger(tmp_path)
    assert cli.main(["consolidate", str(path), "--envelope", "("]) == 2
    assert "invalid --envelope pattern" in capsys.readouterr().err
