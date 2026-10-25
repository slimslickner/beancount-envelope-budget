"""CLI tests: check, report, suggest and completion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from beancount_envelope_budget import cli

_LEDGERS = Path(__file__).parent / "ledgers"


def _ledger(name: str) -> str:
    """Return the path to a fixture ledger.

    Args:
        name: The fixture file name.

    Returns:
        The path as a string.
    """
    return str(_LEDGERS / name)


def test_check_clean_ledger(capsys: pytest.CaptureFixture[str]) -> None:
    """A clean ledger exits zero and reports no errors."""
    assert cli.main(["check", _ledger("basic.bean")]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_check_bad_ledger(capsys: pytest.CaptureFixture[str]) -> None:
    """A ledger with validation errors exits non-zero and lists them."""
    assert cli.main(["check", _ledger("bad_ledger.bean")]) == 1
    out = capsys.readouterr().out
    assert "E002" in out
    assert "E001" in out


def test_check_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    """A missing ledger is a usage error (exit 2)."""
    assert cli.main(["check", str(_LEDGERS / "nope.bean")]) == 2
    assert "no such ledger" in capsys.readouterr().err


def test_check_beancount_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Beancount's own load errors also fail the check."""
    path = tmp_path / "broken.bean"
    path.write_text(
        'option "operating_currency" "USD"\n'
        '2024-01-01 * "unbalanced"\n'
        "  Assets:Checking   10.00 USD\n"
        "  Income:Salary     -5.00 USD\n",
        encoding="utf-8",
    )
    assert cli.main(["check", str(path)]) == 1
    assert "BEANCOUNT" in capsys.readouterr().out


def test_report_default_month(capsys: pytest.CaptureFixture[str]) -> None:
    """The report defaults to the latest month and lists the envelopes."""
    assert cli.main(["report", _ledger("basic.bean")]) == 0
    out = capsys.readouterr().out
    assert "2024-01" in out
    assert "Groceries" in out
    assert "380.00" in out
    assert "Amounts in USD" in out


def test_report_explicit_month(capsys: pytest.CaptureFixture[str]) -> None:
    """A requested month inside the range is used verbatim."""
    assert (
        cli.main(["report", _ledger("overspend_carry.bean"), "--month", "2024-01"]) == 0
    )
    out = capsys.readouterr().out
    assert "2024-01" in out
    assert "-30.00" in out


def test_report_projects_future_month(capsys: pytest.CaptureFixture[str]) -> None:
    """A month after the last is projected, carrying balances forward."""
    assert cli.main(["report", _ledger("basic.bean"), "--month", "2030-01"]) == 0
    out = capsys.readouterr().out
    assert "2030-01" in out
    assert "(projected)" in out
    assert "3500.00" in out


def test_report_clamps_before_start(capsys: pytest.CaptureFixture[str]) -> None:
    """A month before the first is clamped up, with a note."""
    assert cli.main(["report", _ledger("basic.bean"), "--month", "2019-01"]) == 0
    captured = capsys.readouterr()
    assert "before the first month" in captured.err
    assert "2024-01" in captured.out


def test_report_multiple_months(capsys: pytest.CaptureFixture[str]) -> None:
    """--months shows a forward slice of up to three months."""
    code = cli.main(
        [
            "report",
            _ledger("fund_backfill.bean"),
            "--month",
            "2024-01",
            "--months",
            "3",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "2024-01" in out
    assert "2024-02" in out
    assert "2024-03" in out


def test_report_groups_by_parent(capsys: pytest.CaptureFixture[str]) -> None:
    """The report totals each parent category on its heading row."""
    assert (
        cli.main(["report", _ledger("parent_groups.bean"), "--month", "2024-01"]) == 0
    )
    out = capsys.readouterr().out
    assert "[Living]" in out
    assert "[Transport]" in out
    assert "subtotal" not in out
    living = next(line for line in out.splitlines() if line.startswith("[Living]"))
    assert living.split() == ["[Living]", "0.00", "1300.00", "-920.00", "380.00"]


def test_report_without_parents_is_flat(capsys: pytest.CaptureFixture[str]) -> None:
    """With no parent labels the report is unchanged and has no brackets."""
    assert cli.main(["report", _ledger("basic.bean"), "--month", "2024-01"]) == 0
    assert "[" not in capsys.readouterr().out


def test_report_json(capsys: pytest.CaptureFixture[str]) -> None:
    """--json prints the view as stable, parseable JSON."""
    code = cli.main(
        ["report", _ledger("fund_backfill.bean"), "--month", "2024-01", "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["currency"] == "USD"
    assert payload["months"] == ["2024-01-01"]
    groceries = next(e for e in payload["envelopes"] if e["name"] == "Groceries")
    assert groceries["cells"][0]["activity"] == "-100.00"
    assert groceries["cells"][0]["detail_total"] == 1


def test_report_rejects_bad_month(capsys: pytest.CaptureFixture[str]) -> None:
    """A malformed month is a usage error."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["report", _ledger("basic.bean"), "--month", "24-1"])
    assert exit_info.value.code == 2
    assert "YYYY-MM" in capsys.readouterr().err


def test_report_without_start(capsys: pytest.CaptureFixture[str]) -> None:
    """A ledger without a start directive reports nothing."""
    assert cli.main(["report", _ledger("no_start.bean")]) == 0
    assert "no start directive" in capsys.readouterr().err


def test_suggest(capsys: pytest.CaptureFixture[str]) -> None:
    """The suggestion spreads the shortfall over the remaining months."""
    assert cli.main(["suggest", _ledger("savings_goal.bean")]) == 0
    out = capsys.readouterr().out
    assert "Car" in out
    assert "269.24" in out


def test_suggest_no_goals(capsys: pytest.CaptureFixture[str]) -> None:
    """A ledger without goal envelopes says so."""
    assert cli.main(["suggest", _ledger("basic.bean")]) == 0
    assert "no goal envelopes" in capsys.readouterr().out


def test_completion_bash(capsys: pytest.CaptureFixture[str]) -> None:
    """The bash snippet registers argcomplete."""
    assert cli.main(["completion", "bash"]) == 0
    assert "register-python-argcomplete" in capsys.readouterr().out


def test_completion_zsh(capsys: pytest.CaptureFixture[str]) -> None:
    """The zsh snippet enables bashcompinit first."""
    assert cli.main(["completion", "zsh"]) == 0
    assert "bashcompinit" in capsys.readouterr().out


def test_completion_rejects_other_shells() -> None:
    """An unsupported shell is a usage error."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["completion", "fish"])
    assert exit_info.value.code == 2


def test_schema_json(capsys: pytest.CaptureFixture[str]) -> None:
    """`schema --json` prints parseable JSON."""
    assert cli.main(["schema", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["directive_verbs"]


def test_check_dedupes_plugin_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A ledger that also loads our plugin reports each error only once."""
    path = tmp_path / "plugin.bean"
    path.write_text(
        'option "operating_currency" "USD"\n'
        'plugin "beancount_envelope_budget.plugin"\n'
        "\n"
        "2020-01-01 open Assets:Checking USD\n"
        "  cash-account: TRUE\n"
        "2020-01-01 open Expenses:Groceries USD\n"
        "2020-01-01 open Equity:Opening-Balances USD\n"
        "\n"
        '2023-12-31 * "Opening"\n'
        "  Assets:Checking            1000.00 USD\n"
        "  Equity:Opening-Balances   -1000.00 USD\n"
        "\n"
        '2024-01-01 custom "envelope" "start"\n'
        "\n"
        '2024-01-05 * "Spend"\n'
        "  Expenses:Groceries    10.00 USD\n"
        "  Assets:Checking      -10.00 USD\n",
        encoding="utf-8",
    )
    assert cli.main(["check", str(path)]) == 1
    assert capsys.readouterr().out.count("has no envelope") == 1


def test_ledger_arguments_are_completable() -> None:
    """Every ledger positional carries a file completer."""
    parser = cli.build_parser()
    seen = 0
    for _path, subparser in cli._walk_commands(parser, ("prog",)):
        for action in subparser._actions:
            if action.dest == "ledger":
                assert getattr(action, "completer", None) is not None
                seen += 1
    assert seen >= 3


def test_parse_month_rejects_garbage() -> None:
    """The month type raises for non-month strings."""
    with pytest.raises(argparse.ArgumentTypeError):
        cli._parse_month("not-a-month")
