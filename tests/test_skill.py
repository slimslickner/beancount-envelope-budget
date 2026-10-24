"""Tests for the CLI skill: it exists and its commands match the real parser."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from beancount_envelope_budget import cli

_REPO = Path(__file__).parents[1]
_SKILL_DIR = _REPO / ".opencode" / "skills" / "beancount-envelope-budget"
_COMMAND = re.compile(r"beancount-envelope-budget\s+([a-z]+)")


def _subcommands() -> set[str]:
    """Return the subcommand names registered in the parser.

    Returns:
        The subcommand names.
    """
    parser = cli.build_parser()
    names: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices)
    return names


def test_skill_files_exist() -> None:
    """The skill and its workflows reference are present."""
    skill = _SKILL_DIR / "SKILL.md"
    workflows = _SKILL_DIR / "references" / "workflows.md"
    assert skill.exists()
    assert workflows.exists()
    assert skill.read_text(encoding="utf-8").strip()
    assert workflows.read_text(encoding="utf-8").strip()


def test_skill_frontmatter() -> None:
    """The skill has the name and description frontmatter the loader needs."""
    text = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    assert "name:" in frontmatter
    assert "description:" in frontmatter


def test_documented_commands_exist() -> None:
    """Every subcommand the skill names is a real subcommand."""
    known = _subcommands()
    text = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    text += (_SKILL_DIR / "references" / "workflows.md").read_text(encoding="utf-8")
    documented = set(_COMMAND.findall(text))
    assert documented
    assert documented <= known, documented - known
