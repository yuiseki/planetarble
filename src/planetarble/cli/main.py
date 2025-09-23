"""CLI entry point for Planetarble."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from planetarble.acquisition import AcquisitionManager
from planetarble.config import load_config
from planetarble.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Planetarble command-line interface")
    parser.add_argument("--log-json", action="store_true", help="Emit logs in JSON format")
    parser.add_argument("--log-level", default="INFO", help="Logging level (default: INFO)")
    subcommands = parser.add_subparsers(dest="command", required=True)

    acquire = subcommands.add_parser("acquire", help="Download source datasets and emit manifest")
    acquire.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON)",
    )
    acquire.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Explicit manifest output path (defaults to output_dir/MANIFEST.json)",
    )
    acquire.add_argument(
        "--bmng-resolution",
        choices=["500m", "2km"],
        default="500m",
        help="Preferred BMNG resolution (default: 500m)",
    )
    acquire.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files already exist",
    )
    acquire.add_argument(
        "--no-aria2",
        action="store_true",
        help="Disable aria2c integration and use built-in downloader",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    configure_logging(level=args.log_level, json_logs=args.log_json)

    if args.command == "acquire":
        return _handle_acquire(args)
    parser.error("Unknown command")
    return 1


def _handle_acquire(args: argparse.Namespace) -> int:
    if args.config:
        cfg = load_config(args.config)
    else:
        default_cfg = Path("configs/base/pipeline.yaml")
        if not default_cfg.exists():
            raise SystemExit("No configuration file found; supply --config or create configs/base/pipeline.yaml")
        cfg = load_config(default_cfg)

    manifest_path = args.manifest or (cfg.output_dir / "MANIFEST.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manager = AcquisitionManager(
        cfg.data_dir,
        manifest_path=manifest_path,
        use_aria2=not args.no_aria2,
    )
    manager.download_bmng(args.bmng_resolution, force=args.force)
    manager.download_gebco(force=args.force)
    manager.download_natural_earth(force=args.force)
    manager.generate_manifest(
        generation_params={"bmng_resolution": args.bmng_resolution},
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
