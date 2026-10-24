"""The read-only Fava report for envelope balances.

Load with::

    2024-01-01 custom "fava-extension" "beancount_envelope_budget.fava_ext"

The extension recomputes the budget with :func:`beancount_envelope_budget.core.compute`
in ``after_load_file`` and renders a Jinja template. It is the only module that
imports Fava.
"""

from __future__ import annotations

__all__: list[str] = []
