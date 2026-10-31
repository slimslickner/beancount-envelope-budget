"""Plugin tests: diagnostics surfaced to Beancount and optional annotations."""

from __future__ import annotations

from pathlib import Path

from beancount import loader
from beancount.core import data, flags

from beancount_envelope_budget import plugin
from beancount_envelope_budget.core import compute

_PLUGIN = 'plugin "beancount_envelope_budget.plugin"'
_PLUGIN_RESOLVED = 'plugin "beancount_envelope_budget.plugin" "resolved"'

_LEDGERS = Path(__file__).parent / "ledgers"


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


def test_plugin_applies_padding_before_computing() -> None:
    """The plugin pads before computing, so it matches the report on a pad ledger."""
    entries, _errors, options = loader.load_file(_LEDGERS / "padded_cash.bean")
    pre = [
        entry
        for entry in entries
        if not (
            isinstance(entry, data.Transaction) and entry.flag == flags.FLAG_PADDING
        )
    ]
    # The finished ledger, and a report built from it, has no pool warning.
    assert [
        d.code for d in compute(entries, options).diagnostics if d.code == "W001"
    ] == []
    # Computing on the pre-pad entries would warn ...
    assert "W001" in [d.code for d in compute(pre, options).diagnostics]
    # ... but the plugin re-applies pad first, so it agrees with the report.
    new_entries, errors = plugin.plugin(pre, options)
    assert errors == []
    assert new_entries == pre


def test_plugin_padding_skipped_in_raw_mode() -> None:
    """In raw mode the plugin leaves plugin ordering to the ledger."""
    ledger = f"""option "operating_currency" "USD"
option "plugin_processing_mode" "raw"
{_PLUGIN}

2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Equity:Opening-Balances USD
2020-01-01 open Expenses:Groceries USD
  envelope: "Groceries"

2020-01-01 pad Assets:Checking Equity:Opening-Balances
2020-01-02 balance Assets:Checking 1000.00 USD

2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Groceries"
2024-01-01 custom "envelope" "allocate" "Groceries" 500.00 USD
"""
    entries, _errors, options = loader.load_string(ledger)
    # Beancount does not run pad in raw mode either, so the plugin and a direct
    # compute agree: both see the pre-pad ledger and warn.
    assert "W001" in [d.code for d in compute(entries, options).diagnostics]
    _new_entries, errors = plugin.plugin(entries, options)
    assert any("pool is negative" in error.message for error in errors)


def test_plugin_leaves_a_reusable_result() -> None:
    """The plugin caches its budget for a matching consumer, then consumes it."""
    _entries, _errors, options = loader.load_string(_ledger(_PLUGIN, enveloped=True))
    cached = plugin.cached_result(
        options,
        collect_resolved=False,
        collect_activity=False,
        collect_hashes=False,
    )
    assert cached is not None
    assert cached.months
    # The cache is consumed, so a second reader must compute for itself.
    assert (
        plugin.cached_result(
            options,
            collect_resolved=False,
            collect_activity=False,
            collect_hashes=False,
        )
        is None
    )


def test_plugin_cache_is_not_reused_for_a_different_collection() -> None:
    """A consumer wanting activity detail does not get the validation-only budget."""
    _entries, _errors, options = loader.load_string(_ledger(_PLUGIN, enveloped=True))
    assert (
        plugin.cached_result(
            options,
            collect_resolved=False,
            collect_activity=True,
            collect_hashes=True,
        )
        is None
    )


def test_cached_result_absent_without_the_plugin() -> None:
    """A ledger that does not load the plugin leaves nothing to reuse."""
    _entries, _errors, options = loader.load_string(_ledger("", enveloped=True))
    assert (
        plugin.cached_result(
            options,
            collect_resolved=False,
            collect_activity=False,
            collect_hashes=False,
        )
        is None
    )


def test_collect_hashes_gates_the_hashes() -> None:
    """Hashes are computed only when asked for, and empty otherwise."""
    entries, _errors, options = loader.load_string(_ledger("", enveloped=True))
    without = compute(entries, options, collect_activity=True)
    with_hashes = compute(entries, options, collect_activity=True, collect_hashes=True)
    assert [event.entry_hash for event in without.activity] == [""]
    assert all(len(event.entry_hash) == 32 for event in with_hashes.activity)
    assert without.envelopes[0].envelope.source_hash == ""
    assert len(with_hashes.envelopes[0].envelope.source_hash) == 32


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
