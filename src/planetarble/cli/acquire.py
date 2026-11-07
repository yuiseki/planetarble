"""The acquire subcommand: download source datasets and write the manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from planetarble.acquisition import (
    AcquisitionManager,
    CopernicusAccessError,
    CopernicusAuthError,
    CopernicusCredentialsMissing,
)
from planetarble.config import load_config
from planetarble.logging import get_logger

from .checks import _resolve_config_path

LOGGER = get_logger(__name__)


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
    generation_params: dict[str, object] = {}
    etopo_path: Path | None = None
    if cfg.ocean.enabled:
        try:
            etopo_path = manager.download_etopo(source_id=cfg.ocean.source_id, force=args.force)
            generation_params["etopo_path"] = str(etopo_path)
        except Exception as exc:
            LOGGER.warning("etopo acquisition skipped: %s", exc)

    if cfg.hls.enabled and cfg.processing.tile_source.lower() == "hls":
        if args.bmng_resolution != "500m":
            LOGGER.info(
                "--bmng-resolution is ignored when tile_source is 'hls'",
                extra={"bmng_resolution": args.bmng_resolution},
            )
        plan_region = args.plan_region or cfg.hls.plan_region
        if cfg.hls.plan_regions:
            needs_admin = any(region.natural_earth for region in cfg.hls.plan_regions)
            needs_land = any(region.land_only for region in cfg.hls.plan_regions)
            if needs_admin or needs_land:
                try:
                    manager.download_natural_earth(force=args.force, include_admin=needs_admin)
                except Exception as exc:
                    LOGGER.warning("natural earth acquisition skipped: %s", exc)
            summaries = manager.build_hls_plans(cfg.hls, force=args.force, selected_region=plan_region)
            if summaries:
                generation_params["hls_plan_regions"] = [
                    {
                        "region": name,
                        "path": str(summary.path),
                        "zoom": summary.zoom,
                        "tiles": summary.tile_count,
                        "season_counts": summary.season_counts,
                    }
                    for name, summary in summaries.items()
                ]
            if cfg.hls.plan_include_global:
                plan_path = cfg.data_dir / "plans" / f"hls_z{cfg.hls.target_zoom}_plan.ndjson"
                summary = manager.build_hls_plan(cfg.hls, destination=plan_path, force=args.force)
                if summary:
                    generation_params["hls_plan"] = {
                        "path": str(summary.path),
                        "zoom": summary.zoom,
                        "tiles": summary.tile_count,
                        "season_counts": summary.season_counts,
                    }
        else:
            if plan_region:
                raise SystemExit("hls.plan_regions is empty; remove --plan-region or define regions in config")
            plan_path = cfg.data_dir / "plans" / f"hls_z{cfg.hls.target_zoom}_plan.ndjson"
            summary = manager.build_hls_plan(cfg.hls, destination=plan_path, force=args.force)
            if summary:
                generation_params["hls_plan"] = {
                    "path": str(summary.path),
                    "zoom": summary.zoom,
                    "tiles": summary.tile_count,
                    "season_counts": summary.season_counts,
                }
    elif cfg.sentinel2.enabled and cfg.processing.tile_source.lower() == "sentinel2":
        plan_region = args.plan_region or cfg.sentinel2.plan_region
        if cfg.sentinel2.plan_regions:
            needs_admin = any(region.natural_earth for region in cfg.sentinel2.plan_regions)
            needs_land = any(region.land_only for region in cfg.sentinel2.plan_regions)
            if needs_admin or needs_land:
                try:
                    manager.download_natural_earth(force=args.force, include_admin=needs_admin)
                except Exception as exc:
                    LOGGER.warning("natural earth acquisition skipped: %s", exc)
            if plan_region and not any(region.name == plan_region for region in cfg.sentinel2.plan_regions):
                raise SystemExit(f"Unknown Sentinel-2 plan region: {plan_region}")
        LOGGER.info(
            "Sentinel-2 acquisition does not require downloads",
            extra={"plan_region": plan_region},
        )
    else:
        LOGGER.info(
            "HLS acquisition disabled or tile_source != 'hls'; running legacy Blue Marble pipeline",
            extra={"tile_source": cfg.processing.tile_source, "hls_enabled": cfg.hls.enabled},
        )
        manager.download_bmng(args.bmng_resolution, force=args.force)
        manager.download_gebco(force=args.force)
        manager.download_natural_earth(force=args.force)
        try:
            manager.download_modis_mcd43a4(force=args.force)
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            LOGGER.warning("modis acquisition skipped: %s", exc)
        try:
            manager.download_viirs_corrected_reflectance(
                force=args.force,
                product=cfg.viirs.product,
            )
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            LOGGER.warning("viirs acquisition skipped: %s", exc)
        try:
            manager.check_copernicus_connection()
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            LOGGER.warning("copernicus verification failed: %s", exc)

        copernicus_summary: list[dict[str, object]] = []
        try:
            copernicus_summary = manager.download_copernicus_tiles(cfg.copernicus, force=args.force)
        except (SystemExit, KeyboardInterrupt):
            raise
        except (CopernicusCredentialsMissing, CopernicusAuthError, CopernicusAccessError) as exc:
            LOGGER.warning("copernicus tiles skipped: %s", exc)

        generation_params["bmng_resolution"] = args.bmng_resolution
        if copernicus_summary:
            generation_params["copernicus_layers"] = copernicus_summary

    manager.generate_manifest(generation_params=generation_params or None)
    return 0


def register_acquire(subcommands: argparse._SubParsersAction) -> None:
    acquire = subcommands.add_parser("acquire", help="Download source datasets and emit manifest")
    acquire.set_defaults(handler=_handle_acquire)
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
        "--plan-region",
        default=None,
        help="Named HLS/Sentinel-2 plan region to generate (matches hls.plan_regions entries)",
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
