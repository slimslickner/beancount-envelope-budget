"""Core computation tests, one per fixture ledger in ``tests/ledgers``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader

from beancount_envelope_budget.core import BudgetResult, EnvelopeReport, Plan

D = Decimal


def _envelope(result: BudgetResult, name: str) -> EnvelopeReport:
    """Return an envelope report, failing if it does not exist.

    Args:
        result: The computed result.
        name: The envelope name.

    Returns:
        The matching envelope report.
    """
    report = result.envelope(name)
    assert report is not None, f"envelope {name!r} is missing"
    return report


def _codes(result: BudgetResult) -> list[str]:
    """Return the diagnostic codes.

    Args:
        result: The computed result.

    Returns:
        The codes in order.
    """
    return [diagnostic.code for diagnostic in result.diagnostics]


def test_basic(load_budget) -> None:
    """The worked example: Groceries 380, pool 3500, invariant holds."""
    result = load_budget("basic.bean")
    assert result.diagnostics == ()
    assert _envelope(result, "Groceries").closing == D("380")
    assert result.pool[-1].closing == D("3500")
    assert result.cash[-1].balance == D("3880")


def test_credit_card(load_budget) -> None:
    """A card purchase hits its envelope; the payment changes nothing."""
    result = load_budget("credit_card.bean")
    assert result.diagnostics == ()
    assert _envelope(result, "Groceries").closing == D("420")
    assert result.pool[-1].closing == D("500")
    assert result.cash[-1].balance == D("920")


def test_overspend_carry(load_budget) -> None:
    """A negative balance carries into the next month."""
    groceries = _envelope(load_budget("overspend_carry.bean"), "Groceries")
    assert groceries.closing_at(date(2024, 1, 1)) == D("-30")
    assert groceries.closing_at(date(2024, 2, 1)) == D("70")


def test_posting_override(load_budget) -> None:
    """A posting-level envelope override redirects a split purchase."""
    result = load_budget("posting_override.bean")
    assert _envelope(result, "Groceries").closing == D("-80")
    assert _envelope(result, "Gifts").closing == D("-40")


def test_transaction_override(load_budget) -> None:
    """A transaction-level override redirects an enveloped account."""
    result = load_budget("transaction_override.bean")
    assert _envelope(result, "Gifts").closing == D("-40")
    assert _envelope(result, "Groceries").closing == D("0")


def test_payroll(load_budget) -> None:
    """A payroll deduction with a pool override charges the pool."""
    result = load_budget("payroll.bean")
    assert result.diagnostics == ()
    assert _envelope(result, "Retirement").closing == D("0")
    assert result.pool[-1].closing == D("3500")


def test_retirement_transfer(load_budget) -> None:
    """Contributions lower the Retirement envelope; withdrawals raise it."""
    result = load_budget("retirement_transfer.bean")
    assert _envelope(result, "Retirement").closing == D("-300")
    assert result.pool[-1].closing == D("1000")
    assert result.cash[-1].balance == D("700")


def test_brokerage(load_budget) -> None:
    """A security is off-budget but enveloped, valued at its cost or price."""
    result = load_budget("brokerage.bean")
    assert result.diagnostics == ()
    # The buys (at cost and at price) lower the goal; the sale refunds the cost.
    assert _envelope(result, "Taxable Investments").closing == D("1580")
    # The realized gain of 100 falls to the pool.
    assert result.pool[-1].closing == D("3000")
    # Opening cash is net of the pre-start purchase, valued at cost.
    assert result.cash[-1].balance == D("4580")


def test_brokerage_unenveloped(load_budget) -> None:
    """An unenveloped security is E002, not a spurious currency error."""
    result = load_budget("brokerage_unenveloped.bean")
    assert _codes(result) == ["E002"]
    assert result.pool[-1].closing == D("4000")
    assert result.cash[-1].balance == D("4000")


def test_envelope_income(load_budget) -> None:
    """Income with its own envelope raises that envelope, not the pool."""
    result = load_budget("envelope_income.bean")
    assert _envelope(result, "Bonus").closing == D("1000")
    assert result.pool[-1].closing == D("1000")
    assert result.cash[-1].balance == D("2000")


def test_savings_goal(load_budget) -> None:
    """A goal envelope's target and deadline may come from a plan."""
    car = _envelope(load_budget("savings_goal.bean"), "Car")
    assert car.closing == D("1000")
    plan = car.envelope.effective(date(2024, 2, 1))
    assert plan.target == D("15000")
    assert plan.target_currency == "USD"
    assert plan.by == date(2028, 6, 1)


def test_cash_only_move(load_budget) -> None:
    """A move between two cash accounts changes nothing."""
    result = load_budget("cash_only_move.bean")
    assert result.diagnostics == ()
    assert result.pool[-1].closing == D("1000")
    assert result.cash[-1].balance == D("1000")


def test_pre_start_history(load_budget) -> None:
    """History before the start date is ignored and warned about."""
    result = load_budget("pre_start_history.bean")
    assert len([code for code in _codes(result) if code == "W002"]) == 2
    assert _envelope(result, "Groceries").closing == D("-30")
    assert result.pool[-1].closing == D("950")
    assert result.cash[-1].balance == D("920")


def test_bad_ledger(load_budget) -> None:
    """The four bad-ledger cases are reported, without a spurious invariant."""
    result = load_budget("bad_ledger.bean")
    found = set(_codes(result))
    assert {"E001", "E002", "E005", "E006"} <= found
    assert "E007" not in found


def test_mid_month_dip(load_budget) -> None:
    """A pool dip is warned about once, naming the first date and the low."""
    result = load_budget("mid_month_dip.bean")
    warnings = [d for d in result.diagnostics if d.code == "W001"]
    assert len(warnings) == 1
    assert "2024-01-01" in warnings[0].message
    assert "-500.00" in warnings[0].message
    assert "E007" not in _codes(result)
    assert result.pool[-1].closing == D("2500")


def test_invariant_holds(load_budget) -> None:
    """Pool plus envelopes equals cash at every month end for clean ledgers."""
    for name in (
        "basic.bean",
        "credit_card.bean",
        "overspend_carry.bean",
        "brokerage.bean",
    ):
        result = load_budget(name)
        for pool_row, cash_row in zip(result.pool, result.cash, strict=True):
            total = pool_row.closing + sum(
                (report.closing_at(pool_row.month) for report in result.envelopes),
                D(0),
            )
            assert total == cash_row.balance, name


def test_no_start(load_budget) -> None:
    """Without a start directive the plugin does nothing and warns."""
    result = load_budget("no_start.bean")
    assert result.start is None
    assert result.months == ()
    assert _codes(result) == ["W003"]


def test_bad_metadata(load_budget) -> None:
    """Invalid cash-account and tuning metadata are reported with their codes."""
    result = load_budget("bad_metadata.bean")
    cash_errors = [d for d in result.diagnostics if d.code == "E004"]
    assert {d.entry.account for d in cash_errors if d.entry is not None} == {
        "Income:Salary",
        "Equity:Opening-Balances",
    }
    # cash-account: FALSE is the same as omitting the key, so Assets:401k is
    # absent from the diagnostics above.
    assert len([code for code in _codes(result) if code == "E008"]) == 4
    groceries = _envelope(result, "Groceries")
    assert groceries.envelope.plan.monthly == "avg3"
    assert groceries.envelope.plan.ahead == 2
    assert groceries.envelope.plan.priority == D("1")


def test_envelope_lifecycle(load_budget) -> None:
    """Moves, plans, closes and their error cases behave as specified."""
    result = load_budget("envelope_lifecycle.bean")
    found = set(_codes(result))
    assert {"E001", "E003", "E005"} <= found
    assert "E007" not in found
    assert _envelope(result, "A").closing == D("80")
    assert _envelope(result, "B").closing == D("30")
    assert result.pool[-1].closing == D("890")


def test_plan_changes_effective_parameters(load_budget) -> None:
    """A plan directive overrides the base plan from its date."""
    envelope = _envelope(load_budget("envelope_lifecycle.bean"), "A").envelope
    assert envelope.effective(date(2024, 1, 1)) == Plan()
    assert envelope.effective(date(2024, 2, 1)) == Plan(
        monthly=D("200"), ahead=3, priority=D("2")
    )


def test_plan_merges_only_named_fields(load_budget) -> None:
    """A plan changes only the fields it names, keeping the rest."""
    envelope = _envelope(load_budget("plan_merge.bean"), "Car").envelope
    before = envelope.effective(date(2024, 1, 1))
    assert before == Plan(
        monthly=D("500"),
        ahead=1,
        priority=D("2"),
        target=D("15000"),
        target_currency="USD",
        by=date(2028, 6, 1),
    )
    after = envelope.effective(date(2024, 6, 1))
    assert after == Plan(
        monthly=D("500"),
        ahead=1,
        priority=D("1"),
        target=D("15000"),
        target_currency="USD",
        by=date(2028, 6, 1),
    )


def test_unplan_clears_from_its_date(load_budget) -> None:
    """unplan stops funding without removing the envelope or its balance."""
    result = load_budget("unplan.bean")
    envelope = _envelope(result, "Groceries").envelope
    assert envelope.effective(date(2024, 1, 15)).monthly == D("500")
    assert envelope.effective(date(2024, 2, 1)).monthly is None
    assert _envelope(result, "Groceries").closing == D("-220")


def test_fixtures_are_valid_beancount() -> None:
    """Every fixture ledger parses cleanly under Beancount itself."""
    for path in sorted((Path(__file__).parent / "ledgers").glob("*.bean")):
        _entries, errors, _options = loader.load_file(path)
        assert not errors, f"{path.name}: {[e.message for e in errors]}"
