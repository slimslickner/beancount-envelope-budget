"""Tests for the pure directive generators in :mod:`actions`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from beancount_envelope_budget import actions


def test_allocate_rounds_to_cents() -> None:
    entry = actions.allocate("Groceries", Decimal("123.456"), date(2024, 1, 31), "USD")
    assert actions.render(entry) == (
        '2024-01-31 custom "envelope" "allocate" "Groceries" 123.46 USD'
    )


def test_allocate_negative_moves_back_to_pool() -> None:
    entry = actions.allocate("Groceries", Decimal(-40), date(2024, 2, 1), "USD")
    assert actions.render(entry).endswith('"Groceries" -40.00 USD')


def test_move_renders_both_envelopes() -> None:
    entry = actions.move("Groceries", "Gifts", Decimal(50), date(2024, 2, 1), "USD")
    assert actions.render(entry) == (
        '2024-02-01 custom "envelope" "move" "Groceries" "Gifts" 50.00 USD'
    )


def test_plan_omits_unset_metadata() -> None:
    entry = actions.plan("Car", date(2024, 1, 31), monthly=Decimal(500))
    assert actions.render(entry) == (
        '2024-01-31 custom "envelope" "plan" "Car"\n  monthly: 500'
    )


def test_plan_with_target_and_by() -> None:
    entry = actions.plan(
        "Car",
        date(2024, 1, 31),
        target=Decimal(15000),
        target_currency="USD",
        by=date(2028, 6, 1),
    )
    assert actions.render(entry) == (
        '2024-01-31 custom "envelope" "plan" "Car"\n'
        "  target: 15000.00 USD\n"
        "  by: 2028-06-01"
    )


def test_plan_target_needs_currency() -> None:
    with pytest.raises(ValueError, match="target_currency"):
        actions.plan("Car", date(2024, 1, 31), target=Decimal(15000))


def test_plan_with_up_to_ceiling() -> None:
    entry = actions.plan(
        "Car",
        date(2024, 1, 31),
        monthly=Decimal(250),
        up_to=Decimal(600),
    )
    assert actions.render(entry) == (
        '2024-01-31 custom "envelope" "plan" "Car"\n  monthly: 250\n  up-to: 600'
    )


def test_unplan_renders() -> None:
    entry = actions.unplan("Car", date(2024, 2, 1))
    assert actions.render(entry) == '2024-02-01 custom "envelope" "unplan" "Car"'


def test_open_envelope_includes_parent() -> None:
    entry = actions.open_envelope("Car", date(2024, 1, 1), parent="Transport")
    assert actions.render(entry) == (
        '2024-01-01 custom "envelope" "open" "Car"\n  parent: "Transport"'
    )


def test_close_renders() -> None:
    entry = actions.close("Car", date(2024, 6, 1))
    assert actions.render(entry) == '2024-06-01 custom "envelope" "close" "Car"'
