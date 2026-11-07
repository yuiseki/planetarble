"""Pin the structure of the CLI argument parser.

Moving argument definitions out of cli/main.py must not change any option,
default, type, choice, help text or the order subcommands are listed in. The
structure (not the rendered --help, which varies across Python versions) is
compared with golden/parser.json. Regenerate with
PLANETARBLE_UPDATE_GOLDEN=1 only for an intended interface change.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden" / "parser.json"
cli = importlib.import_module("planetarble.cli.main")


def _describe_action(action: argparse.Action) -> dict:
    kind = type(action).__name__
    entry = {
        "kind": kind,
        "options": list(action.option_strings),
        "dest": action.dest,
        "required": bool(action.required),
        "nargs": action.nargs if not isinstance(action.nargs, int) else action.nargs,
        "default": repr(action.default),
        "type": getattr(action.type, "__name__", repr(action.type)) if action.type else None,
        "choices": sorted(map(str, action.choices)) if action.choices and kind != "_SubParsersAction" else None,
        "metavar": action.metavar if isinstance(action.metavar, (str, type(None))) else list(action.metavar),
        "help": action.help,
    }
    return entry


def _describe(parser: argparse.ArgumentParser) -> dict:
    out: dict = {"description": parser.description, "arguments": [], "subcommands": []}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            helps = {choice.dest: choice.help for choice in action._choices_actions}
            out["subcommand_dest"] = action.dest
            out["subcommand_required"] = action.required
            for name, sub in action.choices.items():
                out["subcommands"].append({"name": name, "help": helps.get(name), **_describe(sub)})
        else:
            out["arguments"].append(_describe_action(action))
    return out


def test_parser_structure_matches_golden() -> None:
    actual = _describe(cli.build_parser())
    if os.environ.get("PLANETARBLE_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    assert GOLDEN.exists(), f"golden file missing: {GOLDEN} (run with PLANETARBLE_UPDATE_GOLDEN=1)"
    assert actual == json.loads(GOLDEN.read_text(encoding="utf-8"))
