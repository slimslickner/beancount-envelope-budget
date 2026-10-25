"""Plugin tests: diagnostics surfaced to Beancount and optional annotations."""

from __future__ import annotations

from beancount import loader
from beancount.core import data

from beancount_envelope_budget import plugin

_PLUGIN = 'plugin "beancount_envelope_budget.plugin"'
_PLUGIN_RESOLVED = 'plugin "beancount_envelope_budget.plugin" "resolved"'


def _ledger(plugin_line: str, *, enveloped: bool) -> str:
    """Build a small ledger with or without an enveloped expense account.

    Args:
        plugin_line: The ``plugin`` directive to insert, or an empty string.
        enveloped: Whether the expense account carries an envelope.

    Returns:
        The ledger source.
    """
    envelope_meta = '  envelope: "Groceries"' if enveloped else ""
    return f"""option "operating_currency" "USD"
{plugin_line}

2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Expenses:Groceries USD
{envelope_meta}
2020-01-01 open Equity:Opening-Balances USD

2023-12-31 * "Opening"
  Assets:Checking            1000.00 USD
  Equity:Opening-Balances   -1000.00 USD

2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Groceries"

2024-01-05 * "Spend"
  Expenses:Groceries    10.00 USD
  Assets:Checking      -10.00 USD
"""


def test_module_exposes_plugins() -> None:
    """The module advertises its plugin function and it is callable."""
    assert plugin.__plugins__ == ["plugin"]
    assert callable(plugin.plugin)


def test_plugin_reports_errors() -> None:
    """An unenveloped expense surfaces as a Beancount error."""
    _entries, errors, _options = loader.load_string(_ledger(_PLUGIN, enveloped=False))
    assert errors
    assert any("has no envelope" in error.message for error in errors)
    assert all(hasattr(error, "source") for error in errors)
    assert all(hasattr(error, "entry") for error in errors)


def test_plugin_clean_ledger() -> None:
    """A fully enveloped ledger produces no errors."""
    _entries, errors, _options = loader.load_string(_ledger(_PLUGIN, enveloped=True))
    assert errors == []


def test_plugin_accepts_security_at_cost() -> None:
    """A security bought with operating-currency cash is not a currency error."""
    ledger = f"""option "operating_currency" "USD"
{_PLUGIN}

2020-01-01 open Assets:Brokerage:Cash USD
  cash-account: TRUE
2020-01-01 open Assets:Brokerage:VTI VTI
  envelope: "Taxable Investments"
2020-01-01 open Equity:Opening-Balances USD

2025-12-31 * "Opening"
  Assets:Brokerage:Cash   5000.00 USD
  Equity:Opening-Balances -5000.00 USD

2026-01-01 custom "envelope" "start"
2026-01-01 custom "envelope" "open" "Taxable Investments"

2026-01-05 * "Buy"
  Assets:Brokerage:VTI           5 VTI {{200.00 USD}}
  Assets:Brokerage:Cash  -1000.00 USD
"""
    _entries, errors, _options = loader.load_string(ledger)
    assert errors == []


def test_plugin_direct_call_returns_entries_unchanged() -> None:
    """Calling the plugin without config leaves the entries untouched."""
    entries, _errors, options = loader.load_string(_ledger("", enveloped=True))
    new_entries, plugin_errors = plugin.plugin(entries, options)
    assert new_entries == entries
    assert plugin_errors == []


def test_envelope_resolved_annotation() -> None:
    """The ``resolved`` config writes the resolved envelope onto postings."""
    entries, errors, _options = loader.load_string(
        _ledger(_PLUGIN_RESOLVED, enveloped=True)
    )
    assert errors == []
    transaction = next(
        entry
        for entry in entries
        if isinstance(entry, data.Transaction) and entry.narration == "Spend"
    )
    groceries = next(
        posting
        for posting in transaction.postings
        if posting.account == "Expenses:Groceries"
    )
    checking = next(
        posting
        for posting in transaction.postings
        if posting.account == "Assets:Checking"
    )
    assert (groceries.meta or {})[plugin.RESOLVED_KEY] == "Groceries"
    assert plugin.RESOLVED_KEY not in (checking.meta or {})
