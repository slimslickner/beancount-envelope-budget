"""CLI tests: check, report, suggest and completion."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pytest

from beancount_envelope_budget import __version__, cli

_LEDGERS = Path(__file__).parent / "ledgers"


def _ledger(name: str) -> str:
    """Return the path to a fixture ledger.

    Args:
        name: The fixture file name.

    Returns:
        The path as a string.
    """
    return str(_LEDGERS / name)


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """--version prints the version sourced from the installed metadata."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


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


def test_report_default_month(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report defaults to the current month and lists the envelopes."""
    monkeypatch.setattr(cli, "today_month", lambda: date(2024, 1, 1))
    assert cli.main(["report", _ledger("basic.bean")]) == 0
    out = capsys.readouterr().out
    assert "2024-01" in out
    assert "Groceries" in out
    assert "380.00" in out
    assert "Amounts in USD" in out


def test_report_default_ignores_future_directive(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A future-dated directive does not move the default month forward."""
    monkeypatch.setattr(cli, "today_month", lambda: date(2024, 1, 1))
    assert cli.main(["report", _ledger("future_directive.bean")]) == 0
    out = capsys.readouterr().out
    assert "2024-01" in out
    assert "2024-12" not in out


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


def test_report_orders_groups_and_envelopes(capsys: pytest.CaptureFixture[str]) -> None:
    """Parent headings and envelopes sort alphabetically, ungrouped last."""
    assert cli.main(["report", _ledger("parent_order.bean"), "--month", "2024-01"]) == 0
    lines = capsys.readouterr().out.splitlines()
    headings = [line for line in lines if line.startswith("[")]
    assert headings[0].startswith("[01 Living]")
    assert headings[1].startswith("[02 Transport]")
    assert headings[2].startswith("[03 Numbered]")
    tax = next(i for i, line in enumerate(lines) if line.startswith("  10 Tax"))
    food = next(i for i, line in enumerate(lines) if line.startswith("  2 Food"))
    assert tax < food


def test_report_hides_fully_closed_group(capsys: pytest.CaptureFixture[str]) -> None:
    """A named group with no open members left is omitted from the report."""
    code = cli.main(["report", _ledger("parent_close.bean"), "--month", "2024-04"])
    assert code == 0
    out = capsys.readouterr().out
    assert "[Living]" in out
    assert "[Gone]" not in out


def test_report_group_subtotal_excludes_closed_members(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The group heading totals only the members still open in the month."""
    code = cli.main(["report", _ledger("parent_close.bean"), "--month", "2024-04"])
    assert code == 0
    living = next(
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("[Living]")
    )
    assert living.split() == ["[Living]", "200.00", "0.00", "0.00", "200.00"]


def test_report_shows_group_in_close_month(capsys: pytest.CaptureFixture[str]) -> None:
    """A named group is still shown in the month its members close."""
    code = cli.main(["report", _ledger("parent_close.bean"), "--month", "2024-02"])
    assert code == 0
    assert "[Gone]" in capsys.readouterr().out


def test_report_without_parents_is_flat(capsys: pytest.CaptureFixture[str]) -> None:
    """With no parent labels the report is unchanged and has no brackets."""
    assert cli.main(["report", _ledger("basic.bean"), "--month", "2024-01"]) == 0
    assert "[" not in capsys.readouterr().out


def test_report_cash_opening_is_pre_start_cash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The first month's cash opening is the cash held before the start date."""
    code = cli.main(
        [
            "report",
            _ledger("basic.bean"),
            "--month",
            "2024-01",
            "--months",
            "2",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cash"][0]["opening"] == "1000.00"
    assert payload["cash"][0]["activity"] == "2880.00"
    assert payload["cash"][0]["closing"] == "3880.00"
    assert payload["cash"][1]["opening"] == payload["cash"][0]["closing"]


def test_report_hides_closed_envelope_after_close(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A closed envelope is absent from a month after its close date."""
    code = cli.main(["report", _ledger("closed_envelope.bean"), "--month", "2024-03"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Groceries" in out
    assert "Old" not in out


def test_report_shows_closed_envelope_in_close_month(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A closed envelope is still shown in the month it was closed."""
    code = cli.main(["report", _ledger("closed_envelope.bean"), "--month", "2024-02"])
    assert code == 0
    assert "Old" in capsys.readouterr().out


def test_report_hides_envelope_before_open(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An envelope opened mid-budget is absent from months before it opens."""
    code = cli.main(["report", _ledger("open_late.bean"), "--month", "2024-01"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Groceries" in out
    assert "Late" not in out


def test_report_shows_envelope_from_open_month(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An envelope appears from its open month onward."""
    code = cli.main(["report", _ledger("open_late.bean"), "--month", "2024-07"])
    assert code == 0
    assert "Late" in capsys.readouterr().out


def test_report_spans_late_envelope_across_months(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A late-opened envelope gets one row spanning the window, blanked before it opens."""
    code = cli.main(
        ["report", _ledger("open_late.bean"), "--month", "2024-06", "--months", "3"]
    )
    assert code == 0
    out = capsys.readouterr().out
    late = next(line for line in out.splitlines() if line.split()[:1] == ["Late"])
    # Opening and the June columns are blank (not yet open); July and the
    # projected August carry the values.
    assert late.split() == [
        "Late",
        "200.00",
        "-50.00",
        "150.00",
        "0.00",
        "0.00",
        "150.00",
    ]


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
    detail = groceries["cells"][0]["detail"][0]
    assert detail["transaction_narration"] == "January groceries"
    # The detail carries the transaction's identity so Fava can link to it.
    assert len(detail["entry_hash"]) == 32
    # The cell carries its goal figures for the funding ring.
    assert groceries["cells"][0]["goal"] is None
    assert groceries["cells"][0]["fill"] is None


def test_report_json_narration(capsys: pytest.CaptureFixture[str]) -> None:
    """--json carries each posting's narration metadata."""
    code = cli.main(
        ["report", _ledger("posting_narration.bean"), "--month", "2024-02", "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    housing = next(e for e in payload["envelopes"] if e["name"] == "Housing")
    narrations = {line["posting_narration"] for line in housing["cells"][0]["detail"]}
    assert narrations == {"Principal", "Escrow", "Interest"}


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
    assert "no envelopes with a `goal` and a `by` date" in capsys.readouterr().out


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
        "  on-budget: TRUE\n"
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


def test_pool_defaults_to_the_lowest_month(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A bare pool run opens on the pool's lowest month and names its lines."""
    assert cli.main(["pool", _ledger("mid_month_dip.bean")]) == 0
    captured = capsys.readouterr()
    assert "lowest month" in captured.err
    assert "Opening: 0.00" in captured.out
    assert "allocate Groceries" in captured.out
    assert "Salary" in captured.out
    assert "Lowest: -500.00 on 2024-01-01" in captured.out


def test_pool_defaults_to_latest_when_positive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pool that never went negative defaults to the latest month, no notice."""
    assert cli.main(["pool", _ledger("basic.bean")]) == 0
    captured = capsys.readouterr()
    assert "lowest month" not in captured.err
    assert "Pool ledger" in captured.out


def test_pool_all_shows_history(capsys: pytest.CaptureFixture[str]) -> None:
    """--all prints the whole history without the lowest-month notice."""
    assert cli.main(["pool", _ledger("mid_month_dip.bean"), "--all"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "2024-01-15" in captured.out


def test_pool_day_window(capsys: pytest.CaptureFixture[str]) -> None:
    """--date opens at the balance before that day."""
    assert (
        cli.main(["pool", _ledger("mid_month_dip.bean"), "--date", "2024-01-15"]) == 0
    )
    out = capsys.readouterr().out
    assert "Opening: -500.00" in out
    assert "Salary" in out
    assert "allocate Groceries" not in out


def test_pool_json(capsys: pytest.CaptureFixture[str]) -> None:
    """--json prints the pool ledger as stable, parseable JSON."""
    assert cli.main(["pool", _ledger("mid_month_dip.bean"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["low"] == "-500.00"
    assert payload["low_date"] == "2024-01-01"
    assert payload["entries"][0]["kind"] == "allocation"
    assert payload["entries"][0]["envelope"] == "Groceries"


def test_pool_without_start(capsys: pytest.CaptureFixture[str]) -> None:
    """A ledger without a start directive has nothing to show."""
    assert cli.main(["pool", _ledger("no_start.bean")]) == 0
    assert "no start directive" in capsys.readouterr().err


def test_pool_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    """A missing ledger is a usage error (exit 2)."""
    assert cli.main(["pool", str(_LEDGERS / "nope.bean")]) == 2
    assert "no such ledger" in capsys.readouterr().err


def test_explain_prints_the_plan_derivation(capsys: pytest.CaptureFixture[str]) -> None:
    """``explain`` shows how an up-to plan is evaluated for a month."""
    assert cli.main(["explain", _ledger("up_to.bean"), "--month", "2024-03"]) == 0
    out = capsys.readouterr().out
    assert "Plan evaluation for 2024-03" in out
    assert "Amounts in USD" in out
    assert "level 600.00 = min(ceiling 600.00, opening 500.00 + monthly 250.00)" in out
    assert "top up 100.00" in out


def test_explain_json_carries_the_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """``explain --json`` prints each envelope's derivation lines."""
    assert (
        cli.main(
            [
                "explain",
                _ledger("savings_goal.bean"),
                "--month",
                "2024-03",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["month"] == "2024-03-01"
    assert payload["currency"] == "USD"
    car = next(entry for entry in payload["envelopes"] if entry["name"] == "Car")
    assert car["lines"][0] == (
        "level 833.33 = goal 15000.00 (3/54 months) to 2028-06-01"
    )


def test_explain_envelope_filter(capsys: pytest.CaptureFixture[str]) -> None:
    """``--envelope`` limits the envelopes explained."""
    assert (
        cli.main(
            [
                "explain",
                _ledger("up_to.bean"),
                "--month",
                "2024-03",
                "--envelope",
                "^Nope$",
            ]
        )
        == 0
    )
    assert "no envelope has a plan" in capsys.readouterr().err


def test_explain_without_start(capsys: pytest.CaptureFixture[str]) -> None:
    """A ledger without a start directive has nothing to explain."""
    assert cli.main(["explain", _ledger("no_start.bean")]) == 0
    assert "no start directive" in capsys.readouterr().err
