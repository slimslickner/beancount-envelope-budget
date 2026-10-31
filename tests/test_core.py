"""Core computation tests, one per fixture ledger in ``tests/ledgers``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader

from beancount_envelope_budget.core import (
    BudgetResult,
    Envelope,
    EnvelopeReport,
    MonthRow,
    Plan,
    compute,
)

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


def test_transaction_override_does_not_redirect_pool() -> None:
    """A transaction-level override leaves an explicitly pool account in the pool."""
    ledger = """
option "operating_currency" "USD"
2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Expenses:Taxes USD
  envelope: "pool"
2020-01-01 open Equity:Opening-Balances USD
2023-12-31 * "Opening"
  Assets:Checking            1000.00 USD
  Equity:Opening-Balances   -1000.00 USD
2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Groceries"
2024-01-05 * "taxes plus groceries"
  envelope: "Groceries"
  Assets:Checking  -100.00 USD
  Expenses:Taxes    100.00 USD
"""
    entries, _errors, options = loader.load_string(ledger)
    result = compute(entries, options)
    assert result.diagnostics == ()
    assert _envelope(result, "Groceries").closing == D("0")
    assert result.pool[-1].closing == D("900")


def test_posting_before_open_is_reported() -> None:
    """A posting dated before its envelope's open is E003 and lands in the pool."""
    ledger = """
option "operating_currency" "USD"
2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Expenses:Late USD
  envelope: "Late"
2020-01-01 open Equity:Opening-Balances USD
2023-12-31 * "Opening"
  Assets:Checking            1000.00 USD
  Equity:Opening-Balances   -1000.00 USD
2024-01-01 custom "envelope" "start"
2024-03-15 * "spend before the envelope is declared"
  Expenses:Late   20.00 USD
  Assets:Checking -20.00 USD
2024-07-01 custom "envelope" "open" "Late"
2024-07-15 * "spend after"
  Expenses:Late   30.00 USD
  Assets:Checking -30.00 USD
"""
    entries, _errors, options = loader.load_string(ledger)
    result = compute(entries, options)
    assert _codes(result) == ["E003"]
    assert _envelope(result, "Late").closing == D("-30")
    # The pre-open money is redirected to the pool, not dropped or backdated.
    assert result.pool[-1].closing == D("980")


def test_open_mid_budget_is_clean(load_budget) -> None:
    """An envelope opened and first used mid-budget produces no diagnostics."""
    result = load_budget("open_late.bean")
    assert result.diagnostics == ()
    assert _envelope(result, "Late").closing == D("150")


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
    assert plan.goal == D("15000")
    assert plan.goal_currency == "USD"
    assert plan.by == date(2028, 6, 1)


def test_goal_progress_survives_spending(load_budget) -> None:
    """A goal is funded by contributions, so spending before its deadline does
    not un-fund it (the Disney 2028 case)."""
    trip = _envelope(load_budget("disney_2028.bean"), "Disney 2028")
    # The goal is met once the total is contributed, and stays met as the trip
    # is paid for: only the balance moves, never the progress.
    assert trip.funded_at(date(2028, 1, 31)) == D("10000")
    assert trip.closing_at(date(2028, 1, 31)) == D("8000")
    assert trip.funded_at(date(2028, 3, 31)) == D("10000")
    assert trip.closing_at(date(2028, 3, 31)) == D("5000")
    assert trip.funded_at(date(2028, 7, 31)) == D("10000")
    assert trip.closing_at(date(2028, 7, 31)) == D("0")
    assert trip.envelope.funding_level(date(2028, 6, 15)) == D("10000")


def test_goal_progress_seeds_with_existing_balance() -> None:
    """Adding a goal to an already-funded envelope credits the money there."""
    rows = (
        MonthRow(date(2026, 1, 1), D("0"), D("4000"), D("0")),
        MonthRow(date(2026, 2, 1), D("4000"), D("0"), D("0")),
    )
    report = EnvelopeReport(
        Envelope(
            "Car",
            date(2026, 2, 1),
            None,
            Plan(goal=D("15000"), goal_currency="USD", by=date(2028, 6, 1)),
            (),
        ),
        rows,
    )
    # The goal's clock starts in its open month; the $4000 built beforehand
    # seeds the count, so only $11000 remains to save.
    assert report.funded_at(date(2026, 2, 28)) == D("4000")


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


def test_pool_floor_names_the_low_date() -> None:
    """W001 names the day of the lowest balance, not the first negative day."""
    ledger = """
option "operating_currency" "USD"
2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2020-01-01 open Income:Salary USD
2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "A"
2024-01-01 custom "envelope" "open" "B"
2024-01-02 custom "envelope" "allocate" "A" 100.00 USD
2024-01-05 custom "envelope" "allocate" "B" 500.00 USD
2024-01-10 * "income"
  Assets:Checking   200.00 USD
  Income:Salary    -200.00 USD
"""
    entries, _errors, options = loader.load_string(ledger)
    result = compute(entries, options)
    warnings = [d for d in result.diagnostics if d.code == "W001"]
    assert len(warnings) == 1
    message = warnings[0].message
    assert "2024-01-05" in message
    assert "2024-01-02" not in message
    assert "-600.00" in message
    assert "-100.00" in message


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


def test_tolerated_residual_is_absorbed(load_budget) -> None:
    """A residual within Beancount's tolerance is folded into the pool."""
    result = load_budget("rounding_residual.bean")
    assert result.diagnostics == ()
    for pool_row, cash_row in zip(result.pool, result.cash, strict=True):
        total = pool_row.closing + sum(
            (report.closing_at(pool_row.month) for report in result.envelopes), D(0)
        )
        assert total == cash_row.balance


def test_rounding_plug_reconciles(load_budget) -> None:
    """Booking the residual to Equity:Rounding-Errors keeps it clean."""
    result = load_budget("rounding_plugged.bean")
    assert result.diagnostics == ()


def test_investment_amounts_use_the_display_precision(load_budget) -> None:
    """A sub-cent cost basis is valued at Beancount's display precision."""
    result = load_budget("investment_rounding.bean")
    taxable = _envelope(result, "Taxable Investments")
    # Without rounding, the 1.680168 USD weight would leave the envelope at
    # -0.00 instead of a clean zero, and colour it red.
    assert taxable.closing_at(date(2026, 5, 31)) == D("0")
    assert taxable.closing_at(date(2026, 6, 30)) == D("0")
    assert result.pool[-1].closing == D("5000")
    assert result.diagnostics == ()


def test_material_residual_fails_the_invariant() -> None:
    """A residual beyond the inferred tolerance still fails the invariant."""
    ledger = """
option "operating_currency" "USD"
2020-01-01 open Assets:Brokerage:VTI VTI
  envelope: "Taxable Investments"
2020-01-01 open Assets:Brokerage:Cash USD
  cash-account: TRUE
2020-01-01 open Income:Gains USD
2020-01-01 open Equity:Opening-Balances USD
2025-12-31 * "Opening balance"
  Assets:Brokerage:Cash       5000.00 USD
  Equity:Opening-Balances    -5000.00 USD
2026-01-01 custom "envelope" "start"
2026-01-01 custom "envelope" "open" "Taxable Investments"
2026-01-01 custom "envelope" "allocate" "Taxable Investments" 5000.00 USD
2026-01-05 * "Sell VTI (imported)"
  Assets:Brokerage:VTI          -3 VTI {200.00 USD}
  Assets:Brokerage:Cash        632.11 USD
  Income:Gains                 -32.10 USD
"""
    entries, errors, options = loader.load_string(ledger)
    assert errors, "Beancount itself reports the one-cent imbalance"
    assert "E007" in _codes(compute(entries, options))


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
    assert len([code for code in _codes(result) if code == "E008"]) == 3
    groceries = _envelope(result, "Groceries")
    assert groceries.envelope.plan.monthly == "avg3"
    assert groceries.envelope.plan.priority == D("1")


def test_plan_with_no_effect_is_reported(load_budget) -> None:
    """A plan whose metadata has no effect is reported as E010."""
    result = load_budget("bad_plan.bean")
    messages = [d.message for d in result.diagnostics if d.code == "E010"]
    assert len(messages) == 3
    assert any("goal requires by" in message for message in messages)
    assert any("by requires goal" in message for message in messages)
    assert any("mutually exclusive" in message for message in messages)


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
        monthly=D("200"), priority=D("2")
    )


def test_plan_merges_only_named_fields(load_budget) -> None:
    """A plan changes only the fields it names, keeping the rest."""
    envelope = _envelope(load_budget("plan_merge.bean"), "Car").envelope
    before = envelope.effective(date(2024, 1, 1))
    assert before == Plan(
        priority=D("2"),
        goal=D("15000"),
        goal_currency="USD",
        by=date(2028, 6, 1),
    )
    after = envelope.effective(date(2024, 6, 1))
    assert after == Plan(
        priority=D("1"),
        goal=D("15000"),
        goal_currency="USD",
        by=date(2028, 6, 1),
    )


def test_unplan_clears_from_its_date(load_budget) -> None:
    """unplan stops funding without removing the envelope or its balance."""
    result = load_budget("unplan.bean")
    envelope = _envelope(result, "Groceries").envelope
    assert envelope.effective(date(2024, 1, 15)).monthly == D("500")
    assert envelope.effective(date(2024, 2, 1)).monthly is None
    assert _envelope(result, "Groceries").closing == D("-220")


def test_parent_groups(load_budget) -> None:
    """A parent label is parsed, and grouping does not touch the numbers."""
    result = load_budget("parent_groups.bean")
    parents = {report.name: report.envelope.parent for report in result.envelopes}
    assert parents == {
        "Groceries": "Living",
        "Rent": "Living",
        "Car": "Transport",
        "Misc": None,
    }
    assert _envelope(result, "Groceries").closing == D("380")
    assert result.pool[-1].closing == D("2300")
    assert "E007" not in _codes(result)


def test_fixtures_are_valid_beancount() -> None:
    """Every fixture ledger parses cleanly under Beancount itself."""
    for path in sorted((Path(__file__).parent / "ledgers").glob("*.bean")):
        _entries, errors, _options = loader.load_file(path)
        assert not errors, f"{path.name}: {[e.message for e in errors]}"


def test_up_to_parses_as_a_capped_monthly(load_budget) -> None:
    """``up-to`` parses as a positive number alongside ``monthly``."""
    envelope = _envelope(load_budget("up_to.bean"), "Car").envelope
    plan = envelope.effective(date(2024, 2, 15))
    assert plan.monthly == D("250")
    assert plan.up_to == D("600")


def test_up_to_plan_merges_and_clears() -> None:
    """A ``plan`` changes ``up-to`` alone and ``unplan`` clears it (#22)."""
    ledger = """
option "operating_currency" "USD"
2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "Car"
  monthly: 250.00
  up-to: 600.00
2024-06-01 custom "envelope" "plan" "Car"
  up-to: 900.00
2024-09-01 custom "envelope" "unplan" "Car"
"""
    entries, _errors, options = loader.load_string(ledger)
    result = compute(entries, options)
    report = _envelope(result, "Car")
    assert report.envelope.effective(date(2024, 3, 1)).up_to == D("600")
    assert report.envelope.effective(date(2024, 7, 1)).up_to == D("900")
    assert report.envelope.effective(date(2024, 7, 1)).monthly == D("250")
    assert report.envelope.effective(date(2024, 10, 1)).up_to is None


def test_up_to_plan_errors() -> None:
    """``up-to`` without ``monthly``, with a target, or <= 0 is reported (#22)."""
    ledger = """
option "operating_currency" "USD"
2020-01-01 open Assets:Checking USD
  cash-account: TRUE
2024-01-01 custom "envelope" "start"
2024-01-01 custom "envelope" "open" "NeedsMonthly"
  up-to: 600.00
2024-01-01 custom "envelope" "open" "Conflicts"
  up-to: 600.00
  goal: 5000.00 USD
  by: 2025-01-01
2024-01-01 custom "envelope" "open" "Negative"
  monthly: 250.00
  up-to: -5.00
"""
    entries, _errors, options = loader.load_string(ledger)
    result = compute(entries, options)
    messages = [d.message for d in result.diagnostics]
    assert any("up-to requires monthly" in message for message in messages)
    assert any(
        "up-to and goal are mutually exclusive" in message for message in messages
    )
    assert any(
        "up-to for Negative must be a positive number" in message
        for message in messages
    )
    assert any(d.code == "E008" for d in result.diagnostics)
    assert any(d.code == "E010" for d in result.diagnostics)
