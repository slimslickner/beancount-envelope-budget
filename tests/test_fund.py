"""Tests for the ``fund`` modes, ``topup`` and ledger-derived completion."""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader

from beancount_envelope_budget import cli
from beancount_envelope_budget.core import compute
from beancount_envelope_budget.report import plan_level

_LEDGERS = Path(__file__).parent / "ledgers"

D = Decimal


def _ledger(name: str) -> str:
    """Return the path to a fixture ledger.

    Args:
        name: The fixture file name.

    Returns:
        The path as a string.
    """
    return str(_LEDGERS / name)


def _lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    """Return the captured stdout as non-empty lines.

    Args:
        capsys: The capture fixture.

    Returns:
        The output lines.
    """
    return [line for line in capsys.readouterr().out.splitlines() if line]


def test_fund_actuals(capsys: pytest.CaptureFixture[str]) -> None:
    """actuals funds each month's net spending, so closings equal openings."""
    code = cli.main(
        [
            "fund",
            _ledger("fund_backfill.bean"),
            "--from",
            "2024-01",
            "--to",
            "2024-03",
            "--mode",
            "actuals",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2024-01-31 custom "envelope" "allocate" "Groceries" 100.00 USD',
        '2024-02-29 custom "envelope" "allocate" "Groceries" 150.00 USD',
        '2024-03-31 custom "envelope" "allocate" "Groceries" -200.00 USD',
    ]


def test_fund_overspend(capsys: pytest.CaptureFixture[str]) -> None:
    """overspend only tops negative envelopes back to zero."""
    code = cli.main(
        [
            "fund",
            _ledger("fund_backfill.bean"),
            "--from",
            "2024-01",
            "--to",
            "2024-03",
            "--mode",
            "overspend",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2024-01-31 custom "envelope" "allocate" "Groceries" 100.00 USD',
        '2024-02-29 custom "envelope" "allocate" "Groceries" 150.00 USD',
    ]


def test_fund_copy_rolls_forward(capsys: pytest.CaptureFixture[str]) -> None:
    """copy repeats the previous month's allocation for a single month."""
    code = cli.main(
        [
            "fund",
            _ledger("basic.bean"),
            "--from",
            "2024-02",
            "--to",
            "2024-02",
            "--mode",
            "copy",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2024-02-29 custom "envelope" "allocate" "Groceries" 500.00 USD',
    ]


def test_fund_skips_existing_allocations(capsys: pytest.CaptureFixture[str]) -> None:
    """An envelope-month that already has an allocation is skipped."""
    code = cli.main(
        [
            "fund",
            _ledger("basic.bean"),
            "--from",
            "2024-01",
            "--to",
            "2024-01",
            "--mode",
            "actuals",
        ]
    )
    assert code == 0
    assert _lines(capsys) == []


def test_fund_force_overrides_skip(capsys: pytest.CaptureFixture[str]) -> None:
    """--force prints the allocation regardless."""
    code = cli.main(
        [
            "fund",
            _ledger("basic.bean"),
            "--from",
            "2024-01",
            "--to",
            "2024-01",
            "--force",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2024-01-31 custom "envelope" "allocate" "Groceries" 120.00 USD',
    ]


def test_fund_envelopes_filter(capsys: pytest.CaptureFixture[str]) -> None:
    """--envelopes restricts funding to matching names."""
    args = [
        "fund",
        _ledger("fund_backfill.bean"),
        "--from",
        "2024-01",
        "--to",
        "2024-01",
    ]
    assert cli.main([*args, "--envelopes", "^Gro"]) == 0
    assert len(_lines(capsys)) == 1
    assert cli.main([*args, "--envelopes", "^Nope"]) == 0
    assert _lines(capsys) == []


def test_fund_rejects_bad_pattern(capsys: pytest.CaptureFixture[str]) -> None:
    """An invalid regex is a usage error."""
    code = cli.main(
        [
            "fund",
            _ledger("fund_backfill.bean"),
            "--from",
            "2024-01",
            "--to",
            "2024-01",
            "--envelopes",
            "(",
        ]
    )
    assert code == 2
    assert "invalid --envelopes pattern" in capsys.readouterr().err


def test_fund_requires_range(capsys: pytest.CaptureFixture[str]) -> None:
    """actuals/overspend/copy require --from and --to."""
    assert cli.main(["fund", _ledger("basic.bean")]) == 2
    assert "--from and --to are required" in capsys.readouterr().err


def test_fund_rejects_reversed_range(capsys: pytest.CaptureFixture[str]) -> None:
    """--from after --to is a usage error."""
    code = cli.main(
        [
            "fund",
            _ledger("basic.bean"),
            "--from",
            "2024-03",
            "--to",
            "2024-01",
        ]
    )
    assert code == 2
    assert "--from must not be after --to" in capsys.readouterr().err


def test_topup_level(capsys: pytest.CaptureFixture[str]) -> None:
    """topup funds an envelope up to its level."""
    code = cli.main(
        ["fund", _ledger("topup_level.bean"), "--mode", "topup", "--date", "2024-02-15"]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2024-02-15 custom "envelope" "allocate" "Mortgage" 2500.00 USD',
    ]


def test_topup_up_to_stops_at_the_ceiling(capsys: pytest.CaptureFixture[str]) -> None:
    """topup funds an up-to envelope only as far as its ceiling (#22)."""
    code = cli.main(
        [
            "fund",
            _ledger("up_to.bean"),
            "--mode",
            "topup",
            "--date",
            "2024-03-15",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2024-03-15 custom "envelope" "allocate" "Car" 100.00 USD',
    ]


def test_topup_up_to_uses_the_monthly_allowance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """topup spends the monthly allowance after spending, then stops (#22)."""
    ledger = tmp_path / "main.bean"
    ledger.write_text(
        'option "operating_currency" "USD"\n'
        "2020-01-01 open Assets:Checking USD\n"
        "  cash-account: TRUE\n"
        "2020-01-01 open Expenses:Car USD\n"
        '  envelope: "Car"\n'
        "2020-01-01 open Equity:Opening-Balances USD\n"
        '2023-12-31 * "Opening"\n'
        "  Assets:Checking           10000.00 USD\n"
        "  Equity:Opening-Balances -10000.00 USD\n"
        '2024-01-01 custom "envelope" "start"\n'
        '2024-01-01 custom "envelope" "open" "Car"\n'
        "  monthly: 250.00\n"
        "  up-to: 600.00\n"
        '2024-01-31 custom "envelope" "allocate" "Car" 250.00 USD\n'
        '2024-02-29 custom "envelope" "allocate" "Car" 250.00 USD\n'
        '2024-03-05 custom "envelope" "allocate" "Car" 100.00 USD\n'
        '2024-03-20 * "Repair"\n'
        "  Expenses:Car    300.00 USD\n"
        "  Assets:Checking -300.00 USD\n",
        encoding="utf-8",
    )
    args = ["fund", str(ledger), "--mode", "topup", "--date", "2024-03-25"]
    assert cli.main(args) == 0
    assert _lines(capsys) == [
        '2024-03-25 custom "envelope" "allocate" "Car" 150.00 USD',
    ]
    # The 250 allowance is used up (100 + 150), so a rerun asks for nothing.
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + '2024-03-25 custom "envelope" "allocate" "Car" 150.00 USD\n',
        encoding="utf-8",
    )
    assert cli.main(args) == 0
    assert _lines(capsys) == []


def test_topup_credits_monthly_allocation(capsys: pytest.CaptureFixture[str]) -> None:
    """topup credits a monthly envelope's allocation, so it funds nothing."""
    code = cli.main(
        [
            "fund",
            _ledger("funding_spent.bean"),
            "--mode",
            "topup",
            "--date",
            "2026-10-15",
        ]
    )
    assert code == 0
    assert _lines(capsys) == []


def test_topup_shortage(capsys: pytest.CaptureFixture[str]) -> None:
    """topup funds in priority order and reports shortfalls."""
    code = cli.main(
        [
            "fund",
            _ledger("topup_shortage.bean"),
            "--mode",
            "topup",
            "--date",
            "2024-01-15",
            "--strict",
        ]
    )
    assert code == 1
    lines = _lines(capsys)
    assert '2024-01-15 custom "envelope" "allocate" "Mortgage" 2500.00 USD' in lines
    assert '2024-01-15 custom "envelope" "allocate" "Groceries" 500.00 USD' in lines
    assert "; UNFUNDED Groceries: needs 120.00 USD" in lines
    assert "; UNFUNDED Car: needs 400.00 USD" in lines
    assert not any("Car" in line and "UNFUNDED" not in line for line in lines)


def test_topup_reserve(capsys: pytest.CaptureFixture[str]) -> None:
    """--reserve holds cash back before funding."""
    code = cli.main(
        [
            "fund",
            _ledger("topup_shortage.bean"),
            "--mode",
            "topup",
            "--date",
            "2024-01-15",
            "--reserve",
            "1000",
        ]
    )
    assert code == 0
    lines = _lines(capsys)
    assert '2024-01-15 custom "envelope" "allocate" "Mortgage" 2000.00 USD' in lines
    assert "; UNFUNDED Mortgage: needs 500.00 USD" in lines
    assert "; UNFUNDED Groceries: needs 620.00 USD" in lines


def test_topup_plan_change(capsys: pytest.CaptureFixture[str]) -> None:
    """A plan directive changes the monthly amount from its date."""
    args = ["fund", _ledger("topup_plan.bean"), "--mode", "topup"]
    assert cli.main([*args, "--date", "2027-12-15"]) == 0
    assert _lines(capsys) == [
        '2027-12-15 custom "envelope" "allocate" "Mortgage" 2500.00 USD',
    ]
    assert cli.main([*args, "--date", "2028-01-15"]) == 0
    assert _lines(capsys) == [
        '2028-01-15 custom "envelope" "allocate" "Mortgage" 2600.00 USD',
    ]


def test_topup_goal_proration(capsys: pytest.CaptureFixture[str]) -> None:
    """A goal envelope funds to the on-track level for the run month."""
    args = ["fund", _ledger("topup_goal.bean"), "--mode", "topup"]
    assert cli.main([*args, "--date", "2024-03-15"]) == 0
    assert _lines(capsys) == [
        '2024-03-15 custom "envelope" "allocate" "Emergency" 3000.00 USD',
    ]
    assert cli.main([*args, "--date", "2024-06-15"]) == 0
    assert _lines(capsys) == [
        '2024-06-15 custom "envelope" "allocate" "Emergency" 6000.00 USD',
    ]


def test_topup_goal_spend_is_not_refunded(capsys: pytest.CaptureFixture[str]) -> None:
    """Topup funds contributions, so spending before the deadline is not topped
    up again (the Disney-style goal)."""
    args = ["fund", _ledger("topup_goal_spend.bean"), "--mode", "topup"]
    assert cli.main([*args, "--date", "2024-02-29"]) == 0
    # Contributions are $1000; the level is $2000; the $500 repair is spent,
    # not re-funded, so the gap is the February schedule only.
    assert _lines(capsys) == [
        '2024-02-29 custom "envelope" "allocate" "Emergency" 1000.00 USD',
    ]


def test_topup_goal_rerun(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A goal envelope tops up only the remainder when rerun."""
    ledger = tmp_path / "main.bean"
    ledger.write_text(
        'option "operating_currency" "USD"\n'
        "2020-01-01 open Assets:Checking USD\n"
        "  cash-account: TRUE\n"
        "2020-01-01 open Equity:Opening-Balances USD\n"
        '2023-12-31 * "Opening"\n'
        "  Assets:Checking           100000.00 USD\n"
        "  Equity:Opening-Balances -100000.00 USD\n"
        '2024-01-01 custom "envelope" "start"\n'
        '2024-01-01 custom "envelope" "open" "Emergency"\n'
        "  goal: 6000.00 USD\n"
        "  by: 2024-06-30\n"
        '2024-03-15 custom "envelope" "allocate" "Emergency" 3000.00 USD\n',
        encoding="utf-8",
    )
    args = ["fund", str(ledger), "--mode", "topup"]
    assert cli.main([*args, "--date", "2024-03-20"]) == 0
    assert _lines(capsys) == []
    assert cli.main([*args, "--date", "2024-04-15"]) == 0
    assert _lines(capsys) == [
        '2024-04-15 custom "envelope" "allocate" "Emergency" 1000.00 USD',
    ]


def test_plan_level_rounds_a_subcent_goal() -> None:
    """A prorated goal level is rounded to the cent, not left with a tail."""
    entries, _errors, options = loader.load_file(_ledger("topup_goal_subcent.bean"))
    report = compute(entries, options).envelope("Goal")
    assert report is not None
    # 1000 / 7 = 142.857142..., rounded to the cent the ledger holds.
    assert plan_level(report, date(2024, 1, 15)) == D("142.86")


def test_topup_goal_rerun_subcent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A goal funded to a rounded level is not reported as overfunded."""
    ledger = tmp_path / "main.bean"
    ledger.write_text(
        (_LEDGERS / "topup_goal_subcent.bean").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    args = ["fund", str(ledger), "--mode", "topup"]
    assert cli.main([*args, "--date", "2024-01-15"]) == 0
    assert _lines(capsys) == [
        '2024-01-15 custom "envelope" "allocate" "Goal" 142.86 USD',
    ]

    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + '2024-01-15 custom "envelope" "allocate" "Goal" 142.86 USD\n',
        encoding="utf-8",
    )
    assert cli.main([*args, "--date", "2024-01-15"]) == 0
    assert _lines(capsys) == []


def test_topup_goal_raise_same_month(capsys: pytest.CaptureFixture[str]) -> None:
    """Raising a goal's target mid-month tops up the difference."""
    code = cli.main(
        [
            "fund",
            _ledger("topup_goal_raise.bean"),
            "--mode",
            "topup",
            "--date",
            "2026-10-26",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2026-10-26 custom "envelope" "allocate" "Emergency Fund" 9999.00 USD',
    ]


def test_topup_goal_reduce_reports_overfunded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Lowering a goal's target moves nothing and reports the excess."""
    code = cli.main(
        [
            "fund",
            _ledger("topup_goal_reduce.bean"),
            "--mode",
            "topup",
            "--date",
            "2024-06-15",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        "; OVERFUNDED Emergency: 3000.00 USD above plan",
    ]


def test_topup_avg3() -> None:
    """avg3 resolves to the average of the last three completed months."""
    entries, _errors, options = loader.load_file(_LEDGERS / "topup_avg3.bean")
    result = compute(entries, options)
    report = result.envelope("Groceries")
    assert report is not None
    assert plan_level(report, date(2024, 4, 15)) == D("520")


def test_topup_avg6() -> None:
    """avgN resolves to the average of the last N completed months."""
    entries, _errors, options = loader.load_file(_LEDGERS / "topup_avg6.bean")
    result = compute(entries, options)
    report = result.envelope("Groceries")
    assert report is not None
    assert plan_level(report, date(2024, 7, 15)) == D("150")


def test_topup_skips_unplanned_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    """After unplan, topup no longer funds the envelope."""
    code = cli.main(
        ["fund", _ledger("unplan.bean"), "--mode", "topup", "--date", "2024-02-15"]
    )
    assert code == 0
    assert _lines(capsys) == []


def test_topup_rerun(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Rerunning topup after including its output funds only the remainder."""
    ledger = tmp_path / "main.bean"
    ledger.write_text(
        'option "operating_currency" "USD"\n'
        "2020-01-01 open Assets:Checking USD\n"
        "  cash-account: TRUE\n"
        "2020-01-01 open Income:Salary USD\n"
        '2024-01-01 custom "envelope" "start"\n'
        '2024-01-01 custom "envelope" "open" "Mortgage"\n'
        "  monthly: 2500.00\n"
        "  priority: 1\n"
        '2024-01-05 * "Paycheck one"\n'
        "  Assets:Checking    1000.00 USD\n"
        "  Income:Salary     -1000.00 USD\n",
        encoding="utf-8",
    )
    args = ["fund", str(ledger), "--mode", "topup"]

    assert cli.main([*args, "--date", "2024-01-06"]) == 0
    first = capsys.readouterr().out
    assert '2024-01-06 custom "envelope" "allocate" "Mortgage" 1000.00 USD' in first

    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + '2024-01-10 * "Paycheck two"\n'
        + "  Assets:Checking    2000.00 USD\n"
        + "  Income:Salary     -2000.00 USD\n"
        + first,
        encoding="utf-8",
    )
    assert cli.main([*args, "--date", "2024-01-11"]) == 0
    second = capsys.readouterr().out
    assert '2024-01-11 custom "envelope" "allocate" "Mortgage" 1500.00 USD' in second

    ledger.write_text(ledger.read_text(encoding="utf-8") + second, encoding="utf-8")
    assert cli.main([*args, "--date", "2024-01-12"]) == 0
    assert _lines(capsys) == []


def test_topup_rejects_range(capsys: pytest.CaptureFixture[str]) -> None:
    """topup takes --date, not --from/--to."""
    code = cli.main(
        [
            "fund",
            _ledger("topup_level.bean"),
            "--mode",
            "topup",
            "--from",
            "2024-01",
        ]
    )
    assert code == 2
    assert "--mode topup takes --date" in capsys.readouterr().err


def test_fund_rejects_date_for_range(capsys: pytest.CaptureFixture[str]) -> None:
    """--date is only for the date-based modes."""
    code = cli.main(
        [
            "fund",
            _ledger("basic.bean"),
            "--from",
            "2024-01",
            "--to",
            "2024-01",
            "--date",
            "2024-01-01",
        ]
    )
    assert code == 2
    assert "--date is only valid with --mode topup" in capsys.readouterr().err


def test_fund_fully(capsys: pytest.CaptureFixture[str]) -> None:
    """fully-fund allocates each envelope's need in priority order."""
    code = cli.main(
        [
            "fund",
            _ledger("prefill.bean"),
            "--mode",
            "fully-fund",
            "--date",
            "2024-01-31",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2024-01-31 custom "envelope" "allocate" "Rent" 2200.00 USD',
        '2024-01-31 custom "envelope" "allocate" "Utilities" 200.00 USD',
        '2024-01-31 custom "envelope" "allocate" "Groceries" 50.00 USD',
        '2024-01-31 custom "envelope" "allocate" "Car" 823.08 USD',
    ]


def test_fund_available_caps_by_the_pool(capsys: pytest.CaptureFixture[str]) -> None:
    """fund-available funds in priority order until the pool runs out."""
    code = cli.main(
        [
            "fund",
            _ledger("prefill.bean"),
            "--mode",
            "fund-available",
            "--date",
            "2024-01-31",
            "--reserve",
            "47000",
            "--strict",
        ]
    )
    assert code == 1
    lines = _lines(capsys)
    assert '2024-01-31 custom "envelope" "allocate" "Rent" 1050.00 USD' in lines
    assert "; UNFUNDED Rent: needs 1150.00 USD" in lines
    assert "; UNFUNDED Utilities: needs 200.00 USD" in lines
    assert "; UNFUNDED Groceries: needs 50.00 USD" in lines
    assert "; UNFUNDED Car: needs 823.08 USD" in lines


def test_fund_month_rejects_range(capsys: pytest.CaptureFixture[str]) -> None:
    """fully-fund and fund-available take --date, not --from/--to."""
    code = cli.main(
        [
            "fund",
            _ledger("prefill.bean"),
            "--mode",
            "fully-fund",
            "--from",
            "2024-01",
        ]
    )
    assert code == 2
    assert "--mode fully-fund takes --date" in capsys.readouterr().err


def test_fund_month_envelopes_filter(capsys: pytest.CaptureFixture[str]) -> None:
    """--envelopes restricts a month action to matching names."""
    code = cli.main(
        [
            "fund",
            _ledger("prefill.bean"),
            "--mode",
            "fully-fund",
            "--date",
            "2024-01-31",
            "--envelopes",
            "^Car$",
        ]
    )
    assert code == 0
    assert _lines(capsys) == [
        '2024-01-31 custom "envelope" "allocate" "Car" 823.08 USD',
    ]


def test_fund_rejects_unknown_mode() -> None:
    """An unknown mode is a usage error."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["fund", _ledger("basic.bean"), "--mode", "bogus"])
    assert exit_info.value.code == 2


def test_ledger_envelope_completion() -> None:
    """Envelope names are read from the named ledger."""
    cli._NAME_CACHE.clear()
    names = cli._ledger_envelope_names(_LEDGERS / "topup_shortage.bean")
    assert set(names) == {"Mortgage", "Groceries", "Car"}


def test_complete_envelopes_prefix() -> None:
    """The completer filters by prefix and returns nothing without a ledger."""
    cli._NAME_CACHE.clear()
    parsed = argparse.Namespace(ledger=_ledger("topup_shortage.bean"))
    assert cli._complete_envelopes("Gr", parsed_args=parsed) == ["Groceries"]
    assert cli._complete_envelopes("Z", parsed_args=parsed) == []
    assert cli._complete_envelopes("Gr") == []


def test_complete_envelopes_broken_ledger(tmp_path: Path) -> None:
    """A ledger that will not load yields no candidates."""
    cli._NAME_CACHE.clear()
    broken = tmp_path / "broken.bean"
    broken.write_text("this is not valid beancount\n", encoding="utf-8")
    parsed = argparse.Namespace(ledger=str(broken))
    assert cli._complete_envelopes("", parsed_args=parsed) == []
