"""Tests for the envelope history: the pure model and the CLI ``history``."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader

from beancount_envelope_budget import cli
from beancount_envelope_budget.core import compute
from beancount_envelope_budget.report import build_envelope_history, plan_line_text

_LEDGERS = Path(__file__).parent / "ledgers"

D = Decimal

_LEDGER = """\
option "operating_currency" "USD"
2020-01-01 open Assets:Checking USD
  on-budget: TRUE
2020-01-01 open Equity:Opening-Balances USD
2020-01-01 open Expenses:Groceries USD
  envelope: "Groceries"
2023-12-31 * "Opening"
  Assets:Checking           1000.00 USD
  Equity:Opening-Balances -1000.00 USD
2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Groceries"
  monthly: 500.00
2024-01-05 custom "envelope" "allocate" "Groceries" 500.00 USD
2024-01-10 * "Shop" "Supermarket"
  Expenses:Groceries   120.00 USD
  Assets:Checking     -120.00 USD
2024-02-01 custom "envelope" "plan" "Groceries"
  monthly: 600.00
2024-02-05 custom "envelope" "allocate" "Groceries" 600.00 USD
2024-03-01 custom "envelope" "unplan" "Groceries"
"""


def _history(source: str = _LEDGER, name: str = "Groceries"):
    """Compute a ledger and build one envelope's history.

    Args:
        source: The Beancount source.
        name: The envelope name.

    Returns:
        The :class:`~beancount_envelope_budget.report.EnvelopeHistory`.
    """
    entries, _errors, options = loader.load_string(source)
    result = compute(entries, options, collect_activity=True)
    return build_envelope_history(result, name)


def test_history_groups_lines_by_month_with_a_running_balance() -> None:
    """The history lists directives and movements, each carrying the balance."""
    history = _history()
    assert history.found is True
    assert [month.month for month in history.months] == [
        date(2024, 1, 1),
        date(2024, 2, 1),
        date(2024, 3, 1),
    ]
    january = history.months[0]
    assert (january.opening, january.allocated, january.activity, january.closing) == (
        D("0"),
        D("500"),
        D("-120"),
        D("380"),
    )
    assert [(line.kind, line.balance) for line in january.lines] == [
        ("open", D("0")),
        ("allocate", D("500")),
        ("activity", D("380")),
    ]
    assert january.lines[2].description == "Shop - Supermarket"
    assert january.lines[0].description == "open: monthly 500.00"
    assert all(line.entry_hash for line in january.lines)


def test_history_lists_plan_changes() -> None:
    """A plan change and an unplan appear on their dates, past the rows."""
    history = _history()
    assert [line.kind for line in history.months[1].lines] == ["plan", "allocate"]
    assert history.months[1].lines[0].description == "monthly 600.00"
    # The unplan is dated in March, a month the budget's rows do not compute, so
    # the history extends to it and carries the balance in.
    assert [line.kind for line in history.months[2].lines] == ["unplan"]
    assert history.months[2].opening == history.months[2].closing == D("980")
    assert history.months[2].lines[0].description == "clear plan"
    assert len(history.plans) == 3


def test_plan_line_text() -> None:
    """A plan line reads as its fields."""
    history = _history()
    assert plan_line_text(history.plans[0]) == "open: monthly 500.00"
    assert plan_line_text(history.plans[-1]) == "clear plan"


def test_history_includes_close(load_budget) -> None:
    """A closed envelope's history ends with its close line."""
    result = load_budget("closed_envelope.bean")
    history = build_envelope_history(result, "Old")
    kinds = [line.kind for month in history.months for line in month.lines]
    assert kinds == ["open", "close"]
    close = history.months[-1].lines[-1]
    assert close.date == date(2024, 2, 7)
    assert close.entry_hash


def test_history_unknown_envelope(load_budget) -> None:
    """An unknown envelope yields a not-found history."""
    history = build_envelope_history(load_budget("basic.bean"), "Nope")
    assert history.found is False
    assert history.months == ()


def _ledger(name: str) -> str:
    """Return the path to a fixture ledger.

    Args:
        name: The fixture file name.

    Returns:
        The path as a string.
    """
    return str(_LEDGERS / name)


def test_cli_history_prints_a_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI renders the history as a headed table."""
    path = tmp_path / "main.bean"
    path.write_text(_LEDGER, encoding="utf-8")
    code = cli.main(["history", str(path), "--envelope", "Groceries"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Envelope history: Groceries" in out
    assert "2024-01  opening 0.00" in out
    assert "open: monthly 500" in out
    assert "Shop - Supermarket" in out
    assert "closing 380.00" in out


def test_cli_history_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--json emits the months and their lines."""
    path = tmp_path / "main.bean"
    path.write_text(_LEDGER, encoding="utf-8")
    assert cli.main(["history", str(path), "--envelope", "Groceries", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["envelope"] == "Groceries"
    assert payload["months"][0]["closing"] == "380.00"
    assert payload["months"][0]["lines"][1]["kind"] == "allocate"


def test_cli_history_no_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An --envelope that matches nothing reports it and exits zero."""
    path = tmp_path / "main.bean"
    path.write_text(_LEDGER, encoding="utf-8")
    assert cli.main(["history", str(path), "--envelope", "Nope"]) == 0
    assert "no envelope matches" in capsys.readouterr().err


def test_cli_history_requires_an_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    """--envelope is required."""
    with pytest.raises(SystemExit):
        cli.main(["history", _ledger("basic.bean")])
    assert "--envelope" in capsys.readouterr().err
