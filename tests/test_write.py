"""Tests for the CLI's opt-in ledger writer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from beancount_envelope_budget import actions, cli, writer

_LEDGER = """\
option "operating_currency" "USD"
2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Equity:Opening-Balances USD
2020-01-01 open Expenses:Groceries USD
  envelope: "Groceries"
2023-12-31 * "Opening"
  Assets:Checking           1000.00 USD
  Equity:Opening-Balances -1000.00 USD
2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Groceries"
2024-01-01 custom "envelope" "open" "Gifts"
"""


def _ledger(tmp_path: Path, text: str = _LEDGER) -> Path:
    path = tmp_path / "main.bean"
    path.write_text(text, encoding="utf-8")
    return path


def _lineno_of(text: str, needle: str) -> int:
    for index, line in enumerate(text.splitlines(), start=1):
        if line.startswith(needle):
            return index
    msg = f"{needle!r} not found"
    raise AssertionError(msg)


def test_resolve_target_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the ledger directory"):
        writer.resolve_target(Path("../evil.bean"), tmp_path)


def test_resolve_target_rejects_non_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Beancount source file"):
        writer.resolve_target(Path("Envelope.txt"), tmp_path)


def test_resolve_target_accepts_relative(tmp_path: Path) -> None:
    assert (
        writer.resolve_target(Path("beans/Env.bean"), tmp_path)
        == (tmp_path / "beans/Env.bean").resolve()
    )


def test_directive_line_count_includes_metadata() -> None:
    lines = [
        '2024-01-31 custom "envelope" "plan" "Car"\n',
        "  goal: 15000.00 USD\n",
        "  by: 2028-06-01\n",
        "\n",
        '2024-02-01 custom "envelope" "start"\n',
    ]
    assert writer.directive_line_count(lines, 1) == 3
    assert writer.directive_line_count(lines, 5) == 1


def test_append_adds_a_blank_line_and_preserves_crlf(tmp_path: Path) -> None:
    path = _ledger(tmp_path, _LEDGER.replace("\n", "\r\n"))
    entry = actions.allocate("Groceries", Decimal(250), date(2024, 2, 10), "USD")
    writer.append(path, entry)
    raw = path.read_bytes()
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert raw.decode().endswith(
        '\r\n2024-02-10 custom "envelope" "allocate" "Groceries" 250.00 USD\r\n'
    )


def test_append_keeps_the_inode(tmp_path: Path) -> None:
    path = _ledger(tmp_path)
    before = path.stat().st_ino
    writer.append(
        path, actions.allocate("Groceries", Decimal(1), date(2024, 2, 1), "USD")
    )
    assert path.stat().st_ino == before


def test_replace_swaps_a_directive(tmp_path: Path) -> None:
    text = (
        _LEDGER
        + '2024-01-31 custom "envelope" "plan" "Groceries"\n'
        + "  monthly: 500.00\n"
    )
    path = _ledger(tmp_path, text)
    lineno = _lineno_of(text, '2024-01-31 custom "envelope" "plan"')
    writer.replace(
        path, lineno, actions.plan("Groceries", date(2024, 1, 31), monthly="avg3")
    )
    assert path.read_text(encoding="utf-8").endswith(
        '2024-01-31 custom "envelope" "plan" "Groceries"\n  monthly: "avg3"\n'
    )


def test_delete_removes_a_directive_and_its_metadata(tmp_path: Path) -> None:
    text = (
        _LEDGER
        + '2024-01-31 custom "envelope" "plan" "Groceries"\n'
        + "  monthly: 500.00\n"
    )
    path = _ledger(tmp_path, text)
    lineno = _lineno_of(text, '2024-01-31 custom "envelope" "plan"')
    writer.delete(path, lineno)
    assert "plan" not in path.read_text(encoding="utf-8")


def test_cli_allocate_write_appends(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _ledger(tmp_path)
    code = cli.main(
        [
            "allocate",
            str(path),
            "Groceries",
            "250",
            "--date",
            "2024-02-10",
            "--write",
            str(path),
        ]
    )
    assert code == 0
    written = path.read_text(encoding="utf-8")
    assert "appended 1 directive(s)" in capsys.readouterr().out
    assert written.endswith(
        '2024-02-10 custom "envelope" "allocate" "Groceries" 250.00 USD\n'
    )


def test_cli_move_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _ledger(tmp_path)
    code = cli.main(
        ["move", str(path), "Groceries", "Gifts", "25", "--write", str(path)]
    )
    assert code == 0
    assert 'custom "envelope" "move" "Groceries" "Gifts" 25.00 USD' in (
        path.read_text(encoding="utf-8")
    )
    capsys.readouterr()


def test_cli_write_recheck_reports_unknown_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _ledger(tmp_path)
    code = cli.main(["allocate", str(path), "Nope", "10", "--write", str(path)])
    assert code == 1
    assert "E001" in capsys.readouterr().out


def test_cli_write_refuses_escape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _ledger(tmp_path)
    code = cli.main(
        ["allocate", str(path), "Groceries", "10", "--write", "../evil.bean"]
    )
    assert code == 2
    assert "outside the ledger directory" in capsys.readouterr().err


def test_cli_plan_needs_an_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _ledger(tmp_path)
    assert cli.main(["plan", str(path), "Groceries"]) == 2
    assert "at least one" in capsys.readouterr().out


def test_cli_unplan_and_plan_print(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _ledger(tmp_path)
    assert cli.main(["unplan", str(path), "Groceries", "--date", "2024-02-01"]) == 0
    assert (
        cli.main(
            [
                "plan",
                str(path),
                "Groceries",
                "--monthly",
                "avg3",
                "--date",
                "2024-02-01",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert 'custom "envelope" "unplan" "Groceries"' in out
    assert 'monthly: "avg3"' in out


def test_fund_write_appends_and_rechecks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _ledger(
        tmp_path,
        _LEDGER
        + '2024-01-15 * "Shop"\n'
        + "  Expenses:Groceries   100.00 USD\n"
        + "  Assets:Checking     -100.00 USD\n",
    )
    code = cli.main(
        [
            "fund",
            str(path),
            "--from",
            "2024-01",
            "--to",
            "2024-01",
            "--write",
            str(path),
        ]
    )
    assert code == 0
    capsys.readouterr()
    assert 'custom "envelope" "allocate" "Groceries" 100.00 USD' in path.read_text(
        encoding="utf-8"
    )
