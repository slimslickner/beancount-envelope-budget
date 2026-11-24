"""Regenerate the generated documentation.

The committed files under ``docs/`` (``cli.md``, ``schema.md`` and
``errors.md``) are produced from the live parser and schema registry by this
script::

    uv run python scripts/docs.py

Run with ``--check`` to verify the committed files are current; the gate does
this, the same way ``scripts/sample_ledger.py --check`` guards the sample
ledger. Edit the parser or the registry, then regenerate; never edit a
generated file by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

from beancount_envelope_budget.cli import render_docs


def _docs_dir() -> Path:
    """Return the directory that holds the generated documentation.

    Returns:
        The ``docs`` directory at the repository root.
    """
    return Path(__file__).resolve().parents[1] / "docs"


def _read(path: Path) -> str | None:
    """Read a text file if it exists.

    Args:
        path: The file to read.

    Returns:
        The file's contents, or ``None`` when it does not exist.
    """
    return path.read_text(encoding="utf-8") if path.exists() else None


def main(argv: list[str] | None = None) -> int:
    """Write the generated documentation, or check it is current.

    Args:
        argv: Command line arguments after the program name. Pass ``--check`` to
            write nothing and exit non-zero when any file is stale. Defaults to
            :data:`sys.argv`.

    Returns:
        The process exit status.
    """
    arguments = sys.argv[1:] if argv is None else argv
    docs_dir = _docs_dir()
    generated = render_docs()
    if "--check" in arguments:
        stale = [
            name
            for name, content in generated.items()
            if _read(docs_dir / name) != content
        ]
        for name in stale:
            print(f"out of date: docs/{name}", file=sys.stderr)
        if stale:
            print(
                "run `uv run python scripts/docs.py` to regenerate",
                file=sys.stderr,
            )
            return 1
        print("generated docs are up to date")
        return 0

    docs_dir.mkdir(parents=True, exist_ok=True)
    for name, content in generated.items():
        (docs_dir / name).write_text(content, encoding="utf-8")
        print(f"wrote docs/{name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
