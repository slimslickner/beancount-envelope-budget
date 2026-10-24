"""The Beancount plugin entry point.

Load with::

    plugin "beancount_envelope_budget.plugin"

The plugin calls :func:`beancount_envelope_budget.core.compute`, returns the
entries unchanged and appends the validation diagnostics as ordinary Beancount
errors so Fava lists them on its errors page.
"""

from __future__ import annotations

__all__: list[str] = []
