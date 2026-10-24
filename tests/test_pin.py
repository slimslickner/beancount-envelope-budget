"""Tests for ``pin`` (print/diff) and account-name completion."""

from __future__ import annotations

import argparse
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


def test_pin_adds_envelope_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    """pin prints a unified diff adding the resolved envelope."""
    code = cli.main(["pin", _ledger("basic.bean"), "--account", "^Expenses:Groceries$"])
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("--- ")
    assert "+++ " in out
    assert '+    envelope: "Groceries"' in out


def test_pin_skips_postings_that_have_envelopes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A posting that already carries an envelope is left alone."""
    code = cli.main(
        ["pin", _ledger("posting_override.bean"), "--account", "^Expenses:Groceries$"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert '+    envelope: "Groceries"' in out
    assert '+    envelope: "Gifts"' not in out


def test_pin_matches_existing_metadata_indentation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The inserted line matches the indentation of nearby metadata."""
    cli.main(
        ["pin", _ledger("posting_override.bean"), "--account", "^Expenses:Groceries$"]
    )
    assert '+    envelope: "Groceries"' in capsys.readouterr().out


def test_pin_through_excludes_later_postings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--through stops pinning after the given date."""
    code = cli.main(
        [
            "pin",
            _ledger("basic.bean"),
            "--account",
            "^Expenses:Groceries$",
            "--through",
            "2024-01-01",
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nothing to pin" in captured.err


def test_pin_through_is_inclusive(capsys: pytest.CaptureFixture[str]) -> None:
    """A posting dated exactly on --through is pinned."""
    code = cli.main(
        [
            "pin",
            _ledger("basic.bean"),
            "--account",
            "^Expenses:Groceries$",
            "--through",
            "2024-01-05",
        ]
    )
    assert code == 0
    assert '+    envelope: "Groceries"' in capsys.readouterr().out


def test_pin_pool_posting(capsys: pytest.CaptureFixture[str]) -> None:
    """Pool-resolved postings are pinned to the reserved value."""
    code = cli.main(
        ["pin", _ledger("payroll.bean"), "--account", "^Expenses:Taxes:Federal$"]
    )
    assert code == 0
    assert '+    envelope: "pool"' in capsys.readouterr().out


def test_pin_no_match(capsys: pytest.CaptureFixture[str]) -> None:
    """An account filter that matches nothing reports so."""
    code = cli.main(["pin", _ledger("basic.bean"), "--account", "^Nope"])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nothing to pin" in captured.err


def test_pin_invalid_regex(capsys: pytest.CaptureFixture[str]) -> None:
    """An invalid --account regex is a usage error."""
    code = cli.main(["pin", _ledger("basic.bean"), "--account", "("])
    assert code == 2
    assert "invalid --account pattern" in capsys.readouterr().err


def test_pin_requires_account() -> None:
    """--account is required."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["pin", _ledger("basic.bean")])
    assert exit_info.value.code == 2


def test_pin_without_start(capsys: pytest.CaptureFixture[str]) -> None:
    """A ledger without a start directive has nothing to pin."""
    code = cli.main(["pin", _ledger("no_start.bean"), "--account", ".*"])
    assert code == 0
    assert "no start directive" in capsys.readouterr().err


def test_pin_does_not_write_files(capsys: pytest.CaptureFixture[str]) -> None:
    """The default mode never edits the ledger."""
    path = _LEDGERS / "basic.bean"
    before = path.read_text(encoding="utf-8")
    cli.main(["pin", str(path), "--account", "^Expenses:Groceries$"])
    capsys.readouterr()
    assert path.read_text(encoding="utf-8") == before


def test_apply_insertions_preserves_crlf() -> None:
    """Inserted metadata keeps the file's CRLF line endings."""
    lines = [
        '2024-01-05 * "x"\r\n',
        "  Expenses:Groceries    1.00 USD\r\n",
        "  Assets:Checking      -1.00 USD\r\n",
    ]
    updated = cli._apply_insertions(lines, [(2, "Groceries")])
    assert updated[2] == '    envelope: "Groceries"\r\n'


def test_ledger_account_names() -> None:
    """Account names come from the ledger's open directives."""
    cli._NAME_CACHE.clear()
    names = cli._ledger_account_names(_LEDGERS / "basic.bean")
    assert "Expenses:Groceries" in names
    assert "Assets:Checking" in names


def test_complete_accounts_prefix() -> None:
    """The account completer filters by prefix."""
    cli._NAME_CACHE.clear()
    parsed = argparse.Namespace(ledger=_ledger("basic.bean"))
    assert cli._complete_accounts("Exp", parsed_args=parsed) == ["Expenses:Groceries"]
    assert cli._complete_accounts("Exp") == []
