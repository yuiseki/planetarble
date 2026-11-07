"""The tile subcommand: build MBTiles from the processed raster."""

from __future__ import annotations

import argparse
from pathlib import Path

from planetarble.config import load_config
from planetarble.logging import get_logger, log_skip
from planetarble.tiling import TilingManager

from .checks import _is_valid_mbtiles, _resolve_config_path, _resolve_copernicus_cog

LOGGER = get_logger(__name__)


def _handle_tile(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    cfg = load_config(config_path)

    if args.tile_format is not None:
        cfg.processing.tile_format = args.tile_format
    if args.quality is not None:
        cfg.processing.tile_quality = args.quality
    if args.min_zoom is not None:
        cfg.processing.min_zoom = args.min_zoom
    if args.max_zoom is not None:
        cfg.processing.max_zoom = args.max_zoom

    manager = TilingManager(
        cfg.processing,
        temp_dir=cfg.temp_dir,
        output_dir=cfg.output_dir,
        dry_run=args.dry_run,
    )

    processing_dir = (cfg.output_dir / "processing").resolve()
    if not processing_dir.exists():
        raise SystemExit(f"Processing directory not found: {processing_dir}")

    source_candidates = sorted(processing_dir.glob("*_normalized_cog.tif"))
    if not source_candidates:
        raise SystemExit("No normalized COG raster found; run the process stage first")
    source_raster = source_candidates[0]

    tile_source = (cfg.processing.tile_source or cfg.modis.tile_source or "bmng").lower()

    if tile_source == "modis":
        modis_candidates = sorted(processing_dir.glob("modis_*_rgb_cog.tif"))
        if not modis_candidates:
            raise SystemExit("MODIS tile source selected but no modis_*_rgb_cog.tif found; run process stage")
        source_raster = modis_candidates[0]
    elif tile_source == "viirs":
        viirs_candidates = sorted(processing_dir.glob("viirs_*_rgb_cog.tif"))
        if not viirs_candidates:
            raise SystemExit("VIIRS tile source selected but no viirs_*_rgb_cog.tif found; run process stage")
        source_raster = viirs_candidates[0]
    elif tile_source == "copernicus":
        copernicus_candidates = _resolve_copernicus_cog(processing_dir, cfg.copernicus.layers)
        if not copernicus_candidates:
            raise SystemExit(
                "Copernicus tile source selected but no copernicus_*_cog.tif found; run process stage"
            )
        source_raster = copernicus_candidates[0]
    elif tile_source == "sentinel2":
        plan_region = args.plan_region or cfg.sentinel2.plan_region
        if cfg.sentinel2.plan_regions and not plan_region:
            raise SystemExit("sentinel2.plan_regions is set; pass --plan-region or set sentinel2.plan_region")
        filename = (
            f"sentinel2_mosaic_{plan_region}_cog.tif"
            if plan_region
            else "sentinel2_mosaic_cog.tif"
        )
        sentinel2_candidate = (cfg.output_dir / "processing" / filename).resolve()
        if not sentinel2_candidate.exists():
            raise SystemExit(
                "Sentinel-2 tile source selected but no sentinel2_mosaic_cog.tif found; run process stage"
            )
        source_raster = sentinel2_candidate
    elif tile_source == "gsi_orthophotos":
        gsi_candidate = (cfg.output_dir / "processing" / f"{cfg.gsi_orthophotos.output_basename}.tif").resolve()
        if not gsi_candidate.exists():
            raise SystemExit(
                "GSI tile source selected but no GSI orthophoto COG found; run process stage"
            )
        source_raster = gsi_candidate
    elif tile_source == "blend":
        raise SystemExit("tile_source=blend is not implemented yet")
    elif tile_source == "hls":
        region = args.plan_region or cfg.hls.plan_region
        if not region:
            raise SystemExit("tile_source=hls requires --plan-region or hls.plan_region in config")
        source_raster = (cfg.output_dir / "processing" / f"hls_mosaic_{region}_cog.tif").resolve()
        if not source_raster.exists():
            raise SystemExit(f"HLS mosaic COG not found: {source_raster}")
    elif tile_source != "bmng":
        raise SystemExit(f"Unsupported tile_source value: {cfg.processing.tile_source}")

    mbtiles_destination = None
    if tile_source == "hls":
        region = args.plan_region or cfg.hls.plan_region or "region"
        mbtiles_destination = (
            cfg.output_dir
            / "tiling"
            / f"planet_hls_{region}_{cfg.processing.max_zoom}z.mbtiles"
        )
    if mbtiles_destination is None:
        mbtiles_destination = (
            cfg.output_dir
            / "tiling"
            / f"planet_{cfg.processing.gebco_year}_{cfg.processing.max_zoom}z.mbtiles"
        )
    if _is_valid_mbtiles(mbtiles_destination) and not args.force:
        log_skip(LOGGER, phase="tile", reason="valid MBTiles", path=str(mbtiles_destination))
        return 0
    if args.force and mbtiles_destination.exists() and not args.dry_run:
        mbtiles_destination.unlink()
    mbtiles_path = manager.create_mbtiles(source_raster, destination=mbtiles_destination)

    LOGGER.info("tiling outputs", extra={
        "source": str(source_raster),
        "mbtiles": str(mbtiles_path),
    })
    return 0


def register_tile(subcommands: argparse._SubParsersAction) -> None:
    tile = subcommands.add_parser("tile", help="Generate MBTiles output")
    tile.set_defaults(handler=_handle_tile)
    tile.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON)",
    )
    tile.add_argument(
        "--plan-region",
        default=None,
        help="Named plan region to tile when tile_source is hls or sentinel2",
    )
    tile.add_argument(
        "--dry-run",
        action="store_true",
        help="Print tiling commands without executing",
    )
    tile.add_argument(
        "--min-zoom",
        type=int,
        default=None,
        help="Override minimum zoom level",
    )
    tile.add_argument(
        "--max-zoom",
        type=int,
        default=None,
        help="Override maximum zoom level",
    )
    tile.add_argument(
        "--tile-format",
        choices=["PNG", "JPEG", "WEBP"],
        default=None,
        help="Override tile image format",
    )
    tile.add_argument(
        "--quality",
        type=int,
        default=None,
        help="Override tile encoding quality",
    )
    tile.add_argument(
        "--force",
        action="store_true",
        help="Regenerate tiles even if output exists",
    )
