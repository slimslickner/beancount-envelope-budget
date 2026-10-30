"""The Beancount plugin entry point.

Load with::

    plugin "beancount_envelope_budget.plugin"

The plugin calls :func:`beancount_envelope_budget.core.compute`, returns the
entries unchanged and appends the validation diagnostics as ordinary Beancount
errors, so Fava lists them on its errors page. Errors never stop the ledger
from loading.

Beancount runs ``beancount.ops.pad`` after every user plugin, so a plugin never
sees the transactions ``pad`` synthesizes. To report the same pool the CLI and
the Fava report do — they recompute on the finished ledger — the plugin applies
``pad`` to a working copy before computing. Set
``option "plugin_processing_mode" "raw"`` to keep the ordering in the ledger
instead.

Pass the config string ``"resolved"`` to also write each non-cash posting's
resolved envelope into posting metadata under ``envelope-resolved``, which BQL
can then read with ``meta('envelope-resolved')``::

    plugin "beancount_envelope_budget.plugin" "resolved"
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from beancount.core import data
from beancount.ops import pad as pad_ops

from beancount_envelope_budget.core import Diagnostic, compute

if TYPE_CHECKING:
    from collections.abc import Mapping

    from beancount.core.data import Directive, Transaction

__all__ = ["__plugins__", "plugin"]

#: The plugin functions for Beancount to run, discovered via ``__plugins__``.
__plugins__ = ["plugin"]

#: The config value that switches on ``envelope-resolved`` annotations.
_CONFIG_RESOLVED = "resolved"

#: The metadata key written when ``envelope-resolved`` is enabled.
RESOLVED_KEY = "envelope-resolved"


class EnvelopeBudgetError(NamedTuple):
    """A Beancount-compatible error.

    Attributes:
        source: The ledger location, a ``filename``/``lineno`` mapping.
        message: The rendered message.
        entry: The directive the error refers to, if any.
    """

    source: Mapping[str, object]
    message: str
    entry: Directive | None


def plugin(
    entries: list[Directive],
    options_map: Mapping[str, object],
    config: str | None = None,
) -> tuple[list[Directive], list[EnvelopeBudgetError]]:
    """Validate the ledger and return its entries plus any diagnostics.

    Args:
        entries: The ledger entries.
        options_map: The Beancount options map.
        config: The plugin config string. ``"resolved"`` enables the
            ``envelope-resolved`` posting metadata.

    Returns:
        The entries, unchanged unless ``envelope-resolved`` is enabled, and the
        list of errors and warnings.
    """
    annotate = _resolved_requested(config)
    result = compute(
        _with_padding(entries, options_map),
        options_map,
        collect_resolved=annotate,
    )
    errors = [_to_error(diagnostic, options_map) for diagnostic in result.diagnostics]
    if annotate:
        entries = _annotate(entries, dict(result.resolved))
    return entries, errors


def _with_padding(
    entries: list[Directive],
    options_map: Mapping[str, object],
) -> list[Directive]:
    """Return the entries as Beancount's post-plugins will leave them.

    Beancount appends ``beancount.ops.pad`` (and ``balance``) to the plugin
    chain, after every user plugin, and ``pad`` synthesizes transactions from
    ``pad`` directives. A plugin therefore never sees the finished ledger, and a
    pool computed from the entries it is handed can disagree with the CLI and
    the Fava report, which recompute on the final entries. Applying ``pad`` to a
    working copy first keeps the two in step. The original entries are still
    returned, so Beancount's own ``pad`` pass runs normally and nothing is
    padded twice.

    Args:
        entries: The ledger entries.
        options_map: The Beancount options map. Its ``plugin_processing_mode``
            gates whether Beancount runs its own post-plugins.

    Returns:
        The padded entries, or ``entries`` unchanged when there are no ``pad``
        directives, when the mode leaves ordering to the ledger, or when
        ``pad`` fails (Beancount reports that itself).
    """
    if options_map.get("plugin_processing_mode") != "default":
        return entries
    if not any(isinstance(entry, data.Pad) for entry in entries):
        return entries
    try:
        padded, _errors = pad_ops.pad(entries, options_map)
    except Exception:  # noqa: BLE001 - fall back; Beancount reports the failure
        return entries
    return padded


def _resolved_requested(config: str | None) -> bool:
    """Report whether the config asks for ``envelope-resolved`` annotations.

    Args:
        config: The plugin config string, if any.

    Returns:
        ``True`` when the config equals ``"resolved"``, ignoring case and
        surrounding whitespace.
    """
    return isinstance(config, str) and config.strip().lower() == _CONFIG_RESOLVED


def _to_error(
    diagnostic: Diagnostic,
    options_map: Mapping[str, object],
) -> EnvelopeBudgetError:
    """Convert a core diagnostic into a Beancount error.

    Args:
        diagnostic: The core diagnostic.
        options_map: The Beancount options map, for a fallback file name.

    Returns:
        The Beancount-compatible error.
    """
    if diagnostic.entry is not None:
        return EnvelopeBudgetError(
            diagnostic.entry.meta, diagnostic.message, diagnostic.entry
        )
    filename = str(options_map.get("filename", "<unknown>"))
    lineno = diagnostic.source.lineno if diagnostic.source is not None else 0
    return EnvelopeBudgetError(
        data.new_metadata(filename, lineno), diagnostic.message, None
    )


def _annotate(
    entries: list[Directive],
    resolved: dict[int, str],
) -> list[Directive]:
    """Return entries with ``envelope-resolved`` added to non-cash postings.

    Args:
        entries: The ledger entries.
        resolved: ``id(posting)`` to resolved envelope.

    Returns:
        A new entry list; transactions without a resolution are reused as-is.
    """
    annotated: list[Directive] = []
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            annotated.append(entry)
            continue
        postings, changed = _annotate_postings(entry, resolved)
        annotated.append(entry._replace(postings=postings) if changed else entry)
    return annotated


def _annotate_postings(
    entry: Transaction,
    resolved: dict[int, str],
) -> tuple[list[data.Posting], bool]:
    """Add ``envelope-resolved`` to a transaction's postings.

    Args:
        entry: The transaction.
        resolved: ``id(posting)`` to resolved envelope.

    Returns:
        The new posting list and whether anything changed.
    """
    postings: list[data.Posting] = []
    changed = False
    for posting in entry.postings:
        meta = posting.meta or {}
        name = resolved.get(id(posting))
        if name is None or RESOLVED_KEY in meta:
            postings.append(posting)
            continue
        postings.append(posting._replace(meta={**meta, RESOLVED_KEY: name}))
        changed = True
    return postings, changed
