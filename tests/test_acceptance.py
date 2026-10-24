"""An end-to-end acceptance test: backfill a multi-year ledger and check it.

This mirrors the release acceptance criteria — run ``fund actuals`` over a range,
include its output, and confirm ``check`` passes, the invariant holds for every
month, and the pool at the end equals cash minus the envelope balances.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from beancount import loader

from beancount_envelope_budget import cli
from beancount_envelope_budget.core import compute

D = Decimal


def _ledger_text() -> str:
    """Build a two-year ledger with spending but no allocations.

    Returns:
        The ledger source.
    """
    lines = [
        'option "operating_currency" "USD"',
        "",
        "2020-01-01 open Assets:Checking USD",
        "  cash-account: TRUE",
    ]
    for account, envelope in (
        ("Expenses:Rent", "Rent"),
        ("Expenses:Groceries", "Groceries"),
        ("Expenses:Fun", "Fun"),
    ):
        lines += [f"2020-01-01 open {account} USD", f'  envelope: "{envelope}"']
    lines += [
        "2020-01-01 open Income:Salary USD",
        "2020-01-01 open Equity:Opening-Balances USD",
        "",
        '2023-12-31 * "Opening"',
        "  Assets:Checking            5000.00 USD",
        "  Equity:Opening-Balances   -5000.00 USD",
        "",
        '2024-01-01 custom "envelope" "start"',
    ]
    for envelope in ("Rent", "Groceries", "Fun"):
        lines.append(f'2024-01-01 custom "envelope" "open" "{envelope}"')
    lines.append("")
    for year in (2024, 2025):
        for month in range(1, 13):
            day = f"{year}-{month:02d}"
            groceries = D(400 + month * 3)
            fun = D(100 + month * 2)
            lines += [
                f'{day}-01 * "Salary"',
                "  Assets:Checking            3000.00 USD",
                "  Income:Salary             -3000.00 USD",
                f'{day}-02 * "Rent"',
                "  Expenses:Rent              1200.00 USD",
                "  Assets:Checking           -1200.00 USD",
                f'{day}-05 * "Groceries"',
                f"  Expenses:Groceries          {groceries:.2f} USD",
                f"  Assets:Checking           -{groceries:.2f} USD",
                f'{day}-10 * "Fun"',
                f"  Expenses:Fun                {fun:.2f} USD",
                f"  Assets:Checking           -{fun:.2f} USD",
            ]
    return "\n".join(lines) + "\n"


def test_backfill_then_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Backfill a two-year ledger and confirm the ledger then checks out."""
    source = tmp_path / "main.bean"
    source.write_text(_ledger_text(), encoding="utf-8")

    code = cli.main(
        [
            "fund",
            str(source),
            "--from",
            "2024-01",
            "--to",
            "2025-12",
            "--mode",
            "actuals",
        ]
    )
    assert code == 0
    directives = capsys.readouterr().out

    combined = tmp_path / "combined.bean"
    combined.write_text(f'include "{source}"\n{directives}', encoding="utf-8")

    assert cli.main(["check", str(combined)]) == 0
    criteria_output = capsys.readouterr().out
    assert "0 error(s)" in criteria_output

    entries, errors, options = loader.load_file(combined)
    assert not errors
    result = compute(entries, options)

    for report in result.envelopes:
        for row in report.rows:
            assert row.closing == row.opening, report.name

    for pool_row, cash_row in zip(result.pool, result.cash, strict=True):
        total = pool_row.closing + sum(
            (report.closing_at(pool_row.month) for report in result.envelopes), D(0)
        )
        assert total == cash_row.balance, pool_row.month

    envelopes_total = sum((report.closing for report in result.envelopes), D(0))
    assert result.pool[-1].closing == result.cash[-1].balance - envelopes_total
