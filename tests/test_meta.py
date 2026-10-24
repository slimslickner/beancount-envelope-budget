"""Tests for the repository guide, style guide and the moved design spec."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).parents[1]
_GATE_PARTS = (
    "ruff format --check",
    "ruff check",
    "ty check",
    "pytest",
    "docs --check",
)


def _read(relative: str) -> str:
    """Read a file relative to the repository root.

    Args:
        relative: The repository-relative path.

    Returns:
        The file contents.
    """
    return (_REPO / relative).read_text(encoding="utf-8")


def test_repo_guide_files_exist() -> None:
    """The guide, style guide and pointer files are present and non-empty."""
    for relative in ("AGENTS.md", "CLAUDE.md", "docs/style-guide.md"):
        text = _read(relative)
        assert text.strip(), relative


def test_claude_points_at_agents() -> None:
    """CLAUDE.md points at AGENTS.md rather than duplicating it."""
    assert "AGENTS.md" in _read("CLAUDE.md")


def test_agents_references_key_docs() -> None:
    """AGENTS.md links the docs an agent needs."""
    text = _read("AGENTS.md")
    for link in (
        "README.md",
        "docs/concepts.md",
        "docs/style-guide.md",
        "docs/original_design.md",
    ):
        assert link in text, link


def test_design_spec_moved() -> None:
    """The design spec now lives in docs and the old root path is gone."""
    assert (_REPO / "docs" / "original_design.md").exists()
    assert not (_REPO / "Envelope Budgeting for Beancount Spec.md").exists()
    assert "concepts.md" in _read("docs/original_design.md")


def test_gate_is_consistent() -> None:
    """The gate is documented the same way in the README, AGENTS.md and CI."""
    sources = {
        "README.md": _read("README.md"),
        "AGENTS.md": _read("AGENTS.md"),
        ".github/workflows/ci.yml": _read(".github/workflows/ci.yml"),
    }
    for name, text in sources.items():
        for part in _GATE_PARTS:
            assert part in text, f"{name} is missing {part!r}"
