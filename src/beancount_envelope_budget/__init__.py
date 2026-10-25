"""Ledger-driven envelope budgeting for Beancount.

Public entry points live in :mod:`beancount_envelope_budget.core` (pure
computation), :mod:`beancount_envelope_budget.plugin` (the Beancount plugin),
:mod:`beancount_envelope_budget.cli` (the command line interface) and
:mod:`beancount_envelope_budget.fava_ext` (the Fava report).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("beancount-envelope-budget")
except PackageNotFoundError:  # pragma: no cover - only from an uninstalled tree
    __version__ = "0.0.0"

__all__ = ["__version__"]
