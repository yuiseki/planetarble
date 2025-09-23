"""CLI entry point for Planetarble."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

from planetarble.acquisition import AcquisitionManager
from planetarble.config import load_config
from planetarble.logging import configure_logging, get_logger
from planetarble.processing import ProcessingManager

LOGGER = get_logger(__name__)


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

    process = subcommands.add_parser("process", help="Run raster preprocessing pipeline")
    process.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON)",
    )
    process.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    configure_logging(level=args.log_level, json_logs=args.log_json)

    if args.command == "acquire":
        return _handle_acquire(args)
    if args.command == "process":
        return _handle_process(args)
    parser.error("Unknown command")
    return 1


def _resolve_config_path(path: Path | None) -> Path:
    if path is not None:
        resolved = path.resolve()
        if not resolved.exists():
            raise SystemExit(f"Configuration file not found: {resolved}")
        return resolved
    default_cfg = Path("configs/base/pipeline.yaml")
    if default_cfg.exists():
        return default_cfg.resolve()
    raise SystemExit("No configuration file found; supply --config or create configs/base/pipeline.yaml")


def _handle_acquire(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    cfg = load_config(config_path)

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


def _handle_process(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    cfg = load_config(config_path)

    manager = ProcessingManager(
        cfg.processing,
        temp_dir=cfg.temp_dir,
        output_dir=cfg.output_dir,
        dry_run=args.dry_run,
    )

    bmng_dir = (cfg.data_dir / "bmng" / cfg.processing.bmng_resolution).resolve()
    if not bmng_dir.exists():
        raise SystemExit(f"BMNG directory not found: {bmng_dir}")
    bmng_source = manager.compose_bmng_panels(bmng_dir)
    normalized = manager.normalize_bmng(bmng_source)

    gebco_path = (cfg.data_dir / "gebco" / f"GEBCO_{cfg.processing.gebco_year}_CF.nc").resolve()
    if not gebco_path.exists():
        raise SystemExit(f"GEBCO file not found: {gebco_path}")
    hillshade = manager.generate_hillshade(gebco_path)

    natural_earth_dir = (cfg.data_dir / "natural_earth").resolve()
    if not natural_earth_dir.exists():
        raise SystemExit(f"Natural Earth directory not found: {natural_earth_dir}")
    masks_dir = manager.create_masks(natural_earth_dir)

    cog_path = manager.create_cog(normalized)

    LOGGER.info("processing outputs", extra={
        "bmng_mosaic": str(bmng_source),
        "normalized": str(normalized),
        "hillshade": str(hillshade),
        "masks": str(masks_dir),
        "cog": str(cog_path),
    })
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
