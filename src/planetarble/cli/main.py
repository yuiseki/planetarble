"""CLI entry point for Planetarble."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

from planetarble.logging import configure_logging, get_logger

from .acquire import (
    _handle_acquire,
    register_acquire,
)
from .gsi import (
    _handle_gsi_collect,
    _handle_gsi_fetch,
    _handle_gsi_pack,
    register_gsi_collect,
    register_gsi_fetch,
    register_gsi_pack,
)
from .overlay import (
    _handle_build,
    _handle_prefetch,
    _handle_split_plan,
    register_build,
    register_prefetch,
    register_split_plan,
)
from .package import (
    _handle_package,
    register_package,
)
from .process import (
    _handle_process,
    register_process,
)
from .serve import (
    _handle_serve,
    register_serve,
)
from .sources import (
    _handle_copernicus_layers,
    _handle_mpc_fetch,
    register_copernicus_layers,
    register_mpc_fetch,
)
from .tile import (
    _handle_tile,
    register_tile,
)
from .tiling import (
    _handle_merge_mbtiles,
    _handle_stitch_512,
    _handle_tiling_pmtiles,
    _handle_union_mbtiles,
    register_tiling,
)

LOGGER = get_logger(__name__)


def _load_env() -> None:
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"')
            os.environ.setdefault(key, value)
    except OSError as exc:  # pragma: no cover - filesystem errors
        LOGGER.warning("Failed to load .env file", extra={"path": str(env_path), "error": str(exc)})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Planetarble command-line interface")
    parser.add_argument("--log-json", action="store_true", help="Emit logs in JSON format")
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO)")
    subcommands = parser.add_subparsers(dest="command", required=True)

    register_acquire(subcommands)

    register_process(subcommands)

    register_tile(subcommands)

    register_tiling(subcommands)

    register_build(subcommands)

    register_prefetch(subcommands)

    register_split_plan(subcommands)

    register_mpc_fetch(subcommands)

    register_gsi_fetch(subcommands)

    register_gsi_collect(subcommands)

    register_gsi_pack(subcommands)

    register_package(subcommands)

    register_serve(subcommands)

    register_copernicus_layers(subcommands)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    _load_env()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    configure_logging(level=args.log_level, json_logs=args.log_json)

    if args.command == "acquire":
        return _handle_acquire(args)
    if args.command == "process":
        return _handle_process(args)
    if args.command == "tile":
        return _handle_tile(args)
    if args.command == "tiling":
        if args.tiling_command == "pmtiles":
            return _handle_tiling_pmtiles(args)
        if args.tiling_command == "merge-mbtiles":
            return _handle_merge_mbtiles(args)
        if args.tiling_command == "union-mbtiles":
            return _handle_union_mbtiles(args)
        if args.tiling_command == "stitch-512":
            return _handle_stitch_512(args)
        parser.error("Unknown tiling subcommand")
        return 1
    if args.command == "build":
        return _handle_build(args)
    if args.command == "prefetch":
        return _handle_prefetch(args)
    if args.command == "split-plan":
        return _handle_split_plan(args)
    if args.command == "mpc-fetch":
        return _handle_mpc_fetch(args)
    if args.command == "gsi-collect":
        return _handle_gsi_collect(args)
    if args.command == "gsi-pack":
        return _handle_gsi_pack(args)
    if args.command == "gsi-fetch":
        return _handle_gsi_fetch(args)
    if args.command == "package":
        return _handle_package(args)
    if args.command == "serve":
        return _handle_serve(args)
    if args.command == "copernicus-layers":
        return _handle_copernicus_layers(args)
    parser.error("Unknown command")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
