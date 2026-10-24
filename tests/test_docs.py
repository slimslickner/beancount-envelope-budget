"""Tests for the schema registry, the diagnostics and generated docs.

These tests keep three things honest: every argument and subcommand carries help
text, the registries are internally consistent, and the generated documentation
on disk matches what the code would produce right now.
"""

from __future__ import annotations

import argparse
import json

import pytest

from beancount_envelope_budget import cli, schema

_DOCS_DIR = cli._docs_dir()


def _parsers() -> list[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    """Return every parser in the command tree.

    Returns:
        ``(path, parser)`` pairs, parents before children.
    """
    return cli._walk_commands(cli.build_parser(), ("beancount-envelope-budget",))


def test_every_argument_has_help() -> None:
    """Every flag and positional argument carries non-empty help text."""
    for path, parser in _parsers():
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                continue
            assert action.help, f"{' '.join(path)}: {action.dest} has no help"
            assert action.help.strip()


def test_every_subcommand_has_help() -> None:
    """Every subcommand carries non-empty help text for its listing."""
    for path, parser in _parsers():
        for action in parser._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for pseudo in action._choices_actions:
                assert pseudo.help, f"{' '.join(path)}: {pseudo.dest} has no help"
                assert pseudo.help.strip()


def test_render_docs_covers_expected_files() -> None:
    """The generated set matches the declared generated files."""
    assert set(cli.render_docs()) == set(cli._GENERATED_FILES)


def test_generated_docs_match_disk() -> None:
    """The checked-in generated docs are current."""
    for name, content in cli.render_docs().items():
        path = _DOCS_DIR / name
        assert path.exists(), f"docs/{name} is missing"
        assert path.read_text(encoding="utf-8") == content, f"docs/{name} is stale"


def test_diagnostic_codes_are_unique() -> None:
    """No diagnostic code is registered twice."""
    codes = [code.code for code in schema.DIAGNOSTIC_CODES]
    assert len(set(codes)) == len(codes)


def test_diagnostic_codes_are_complete() -> None:
    """Every code has a message, an explanation and a matching severity."""
    for code in schema.DIAGNOSTIC_CODES:
        assert code.summary
        assert code.explanation
        expected = {"E": schema.Severity.ERROR, "W": schema.Severity.WARNING}.get(
            code.code[:1]
        )
        assert expected is not None, f"{code.code} has an unknown prefix"
        assert code.severity is expected, f"{code.code} has the wrong severity"


def test_render_message_unknown_code_raises() -> None:
    """Rendering an unregistered code is an error."""
    with pytest.raises(KeyError):
        schema.render_message("E999")


def test_schema_json_is_serialisable() -> None:
    """The JSON schema output round-trips through :mod:`json`."""
    assert json.loads(schema.dump_schema_json())["directive_verbs"]
