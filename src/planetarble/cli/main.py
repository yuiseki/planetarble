"""CLI entry point for Planetarble."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

from planetarble.logging import configure_logging, get_logger

from .acquire import (
    register_acquire,
)
from .gsi import (
    register_gsi_collect,
    register_gsi_fetch,
    register_gsi_pack,
)
from .overlay import (
    register_build,
    register_prefetch,
    register_split_plan,
)
from .package import (
    register_package,
)
from .process import (
    register_process,
)
from .serve import (
    register_serve,
)
from .sources import (
    register_copernicus_layers,
    register_mpc_fetch,
)
from .tile import (
    register_tile,
)
from .tiling import (
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

    return args.handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
