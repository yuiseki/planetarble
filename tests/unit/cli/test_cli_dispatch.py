"""Characterise the CLI surface: every subcommand parses, has --help, and
main() routes it to the expected handler (bound with set_defaults(handler=...)
by each command module's register_* function).

This pins the command -> handler mapping so that splitting cli/main.py into
modules cannot silently drop or misroute a subcommand.
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pytest

# planetarble.cli re-exports the main() function under the same name as the
# module, so `from planetarble.cli import main` would give the function.
cli = importlib.import_module("planetarble.cli.main")

# (argv prefix, handler name in planetarble.cli.main)
ROUTES: list[tuple[tuple[str, ...], str]] = [
    (("acquire",), "_handle_acquire"),
    (("process",), "_handle_process"),
    (("tile",), "_handle_tile"),
    (("tiling", "pmtiles"), "_handle_tiling_pmtiles"),
    (("tiling", "merge-mbtiles"), "_handle_merge_mbtiles"),
    (("tiling", "union-mbtiles"), "_handle_union_mbtiles"),
    (("tiling", "stitch-512"), "_handle_stitch_512"),
    (("build",), "_handle_build"),
    (("prefetch",), "_handle_prefetch"),
    (("split-plan",), "_handle_split_plan"),
    (("mpc-fetch",), "_handle_mpc_fetch"),
    (("gsi-collect",), "_handle_gsi_collect"),
    (("gsi-pack",), "_handle_gsi_pack"),
    (("gsi-fetch",), "_handle_gsi_fetch"),
    (("package",), "_handle_package"),
    (("serve",), "_handle_serve"),
    (("copernicus-layers",), "_handle_copernicus_layers"),
]


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _leaf_parser(path: tuple[str, ...]) -> argparse.ArgumentParser:
    parser = cli.build_parser()
    for name in path:
        parser = _subparsers(parser)[name]
    return parser


def _dummy_value(action: argparse.Action, tmp_path: Path) -> str:
    if action.choices:
        return str(next(iter(action.choices)))
    if action.type in (int, float):
        return "1"
    if action.type is Path:
        return str(tmp_path / f"{action.dest}.dummy")
    return "dummy"


def _minimal_argv(path: tuple[str, ...], tmp_path: Path) -> list[str]:
    """argv for `path` with every required option filled with a dummy value."""
    argv = list(path)
    for action in _leaf_parser(path)._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if not action.option_strings:
            if action.nargs not in ("?", "*"):
                argv.append(_dummy_value(action, tmp_path))
            continue
        if action.required:
            argv.append(action.option_strings[0])
            if action.nargs != 0:
                argv.append(_dummy_value(action, tmp_path))
    return argv


def test_routes_cover_every_subcommand() -> None:
    parser = cli.build_parser()
    declared: set[tuple[str, ...]] = set()
    for name, sub in _subparsers(parser).items():
        nested = _subparsers(sub)
        if nested:
            declared.update((name, child) for child in nested)
        else:
            declared.add((name,))
    assert declared == {path for path, _ in ROUTES}


@pytest.mark.parametrize("path", [path for path, _ in ROUTES], ids=" ".join)
def test_help_exits_cleanly(path: tuple[str, ...], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([*path, "--help"])
    assert excinfo.value.code == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.parametrize(("path", "handler"), ROUTES, ids=[" ".join(p) for p, _ in ROUTES])
def test_main_routes_to_handler(
    path: tuple[str, ...],
    handler: str,
    tmp_path: Path,
    patch_cli,
) -> None:
    calls: list[tuple[str, argparse.Namespace]] = []
    for _, name in ROUTES:
        patch_cli(name, lambda args, _name=name: calls.append((_name, args)) or 7)
    patch_cli("_load_env", lambda: None)

    assert cli.main(_minimal_argv(path, tmp_path)) == 7
    assert [name for name, _ in calls] == [handler]


@pytest.mark.parametrize(("path", "handler"), ROUTES, ids=[" ".join(p) for p, _ in ROUTES])
def test_parsed_args_carry_their_handler(path: tuple[str, ...], handler: str, tmp_path: Path) -> None:
    args = cli.build_parser().parse_args(_minimal_argv(path, tmp_path))
    assert args.handler.__name__ == handler
    assert args.handler.__module__.startswith("planetarble.cli.")
