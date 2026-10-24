"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from beancount import loader

from beancount_envelope_budget.core import BudgetResult, compute

if TYPE_CHECKING:
    from collections.abc import Callable

_LEDGERS = Path(__file__).parent / "ledgers"


@pytest.fixture
def load_budget() -> Callable[[str], BudgetResult]:
    """Return a loader that computes the budget for a fixture ledger.

    Returns:
        A callable mapping a ledger file name to its :class:`BudgetResult`.
    """

    def _load(name: str) -> BudgetResult:
        entries, _errors, options = loader.load_file(_LEDGERS / name)
        return compute(entries, options)

    return _load
