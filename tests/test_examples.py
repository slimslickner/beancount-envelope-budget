"""The example ledger runs as an integration test, so the quickstart cannot rot."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader

from beancount_envelope_budget.core import BudgetResult, compute
from beancount_envelope_budget.report import build_view

D = Decimal

_EXAMPLE = Path(__file__).parents[1] / "examples" / "household.bean"


def _result() -> BudgetResult:
    """Load and compute the household example."""
    entries, errors, options = loader.load_file(_EXAMPLE)
    assert not errors, [error.message for error in errors]
    result = compute(entries, options)
    assert result.diagnostics == ()
    return result


def _view(name: str):
    """Return one envelope's three-month view, asserting it exists."""
    view = build_view(_result(), date(2024, 1, 1), 3)
    found = next((item for item in view.envelopes if item.name == name), None)
    assert found is not None, name
    return found


def test_household_example() -> None:
    """The example loads cleanly, computes without diagnostics and balances."""
    result = _result()
    for pool_row, cash_row in zip(result.pool, result.cash, strict=True):
        total = pool_row.closing + sum(
            (report.closing_at(pool_row.month) for report in result.envelopes),
            D(0),
        )
        assert total == cash_row.balance


def test_household_monthly_is_full_each_month() -> None:
    """A ``monthly`` envelope funded to its plan reads on plan every month."""
    rent = _view("Rent")
    assert [cell.funding for cell in rent.cells] == ["on", "on", "on"]
    assert all(cell.fill == D("1") for cell in rent.cells)


def test_household_monthly_flow_and_over_funding() -> None:
    """A carried balance does not fund a month; an over-allocation reads over."""
    subscriptions = _view("Subscriptions")
    assert [cell.funding for cell in subscriptions.cells] == ["on", "under", "under"]
    assert subscriptions.cells[1].fill == D("0.5")
    # March opens with February's 5 but allocates nothing: the flow is zero.
    assert subscriptions.cells[2].opening == D("5")
    assert subscriptions.cells[2].fill == D("0")

    mortgage = _view("Mortgage")
    assert [cell.funding for cell in mortgage.cells] == ["on", "on", "over"]
    assert mortgage.cells[2].closing == D("500")


def test_household_up_to_and_overspend() -> None:
    """An ``up-to`` envelope caps its target; an overspent one closes negative."""
    utilities = _view("Utilities")
    assert utilities.cells[0].up_to == D("600")
    assert utilities.cells[0].funding == "under"
    assert utilities.cells[1].funding == "on"

    # Dining is funded to plan but spends more, so it closes negative and reads
    # on plan at once: the balance's red is the signal, the flow is satisfied.
    dining = _view("Dining")
    assert dining.cells[0].funding == "on"
    assert dining.cells[0].closing == D("-100")


def test_household_goal_is_funded_then_spent() -> None:
    """A goal stays funded after it is spent; another is under its pace."""
    vacation = _view("Vacation")
    assert vacation.cells[1].closing == D("3000")
    assert vacation.cells[2].closing == D("0")
    # Spending does not reduce the cumulative contributions.
    assert vacation.cells[2].explanation is not None
    assert vacation.cells[2].explanation.funded == D("3000")

    car = _view("Car")
    assert car.cells[2].funding == "under"
