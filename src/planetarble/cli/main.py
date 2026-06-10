"""CLI entry point for Planetarble."""

from __future__ import annotations

import argparse
import json
import http.server
import functools
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional

from planetarble.acquisition import (
    AcquisitionManager,
    CopernicusAccessError,
    CopernicusAuthError,
    CopernicusCredentialsMissing,
    GSIError,
    MPCError,
    fetch_gsi_ortho_clip,
    fetch_true_color_tile,
    get_available_layers,
    split_plan_by_miniplanet,
    verify_copernicus_connection,
)
from planetarble.config import PipelineConfig, load_config
from planetarble.core.models import CopernicusLayerConfig, ProcessingConfig, TileMetadata
from planetarble.logging import configure_logging, get_logger, log_skip
from planetarble.packaging import PackagingManager
from planetarble.processing import ProcessingManager
from planetarble.tiling import PmtilesTilingManager, TilingManager
from planetarble.tiling.mbtiles import merge_mbtiles, union_mbtiles

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

    process = subcommands.add_parser("process", help="Run raster preprocessing pipeline")
    process.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON)",
    )
    process.add_argument(
        "--plan-region",
        default=None,
        help="Named HLS/Sentinel-2 plan region to process (matches plan_regions entries)",
    )
    process.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them",
    )
    process.add_argument(
        "--force",
        action="store_true",
        help="Regenerate processing outputs even if cached",
    )

    tile = subcommands.add_parser("tile", help="Generate MBTiles output")
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

    tiling = subcommands.add_parser("tiling", help="Advanced tiling utilities")
    tiling_subcommands = tiling.add_subparsers(dest="tiling_command", required=True)

    tiling_pmtiles = tiling_subcommands.add_parser(
        "pmtiles",
        help="Convert a raster into PMTiles via XYZ and MBTiles",
    )
    tiling_pmtiles.add_argument("--input", type=Path, required=True, help="Source raster path")
    tiling_pmtiles.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Destination directory for PMTiles and intermediate artifacts",
    )
    tiling_pmtiles.add_argument(
        "--min-zoom",
        type=int,
        default=None,
        help="Minimum zoom level (default: config or 0)",
    )
    tiling_pmtiles.add_argument(
        "--max-zoom",
        type=int,
        default=None,
        help="Maximum zoom level",
    )
    tiling_pmtiles.add_argument(
        "--format",
        choices=["png", "jpg", "webp"],
        default=None,
        help="Tile image format",
    )
    tiling_pmtiles.add_argument(
        "--quality",
        type=int,
        default=None,
        help="Compression quality for JPEG/WEBP tiles",
    )
    tiling_pmtiles.add_argument(
        "--resampling",
        default=None,
        help="Resampling kernel for gdal raster tile (default: config value)",
    )
    tiling_pmtiles.add_argument("--name", default=None, help="Human-readable tileset name")
    tiling_pmtiles.add_argument(
        "--attribution",
        default=None,
        help="Attribution string embedded into MBTiles metadata",
    )
    tiling_pmtiles.add_argument(
        "--bounds-mode",
        choices=["auto", "global"],
        default="auto",
        help="Strategy to derive bounds metadata",
    )
    tiling_pmtiles.add_argument(
        "--no-deduplication",
        action="store_true",
        help="Disable PMTiles deduplication during conversion",
    )
    tiling_pmtiles.add_argument(
        "--cluster",
        action="store_true",
        help="Run pmtiles cluster after conversion",
    )
    tiling_pmtiles.add_argument(
        "--temp-dir",
        type=Path,
        default=None,
        help="Temporary workspace directory (defaults to <out>/tmp)",
    )
    tiling_pmtiles.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them",
    )

    tiling_merge = tiling_subcommands.add_parser(
        "merge-mbtiles",
        help="Overlay tiles from one MBTiles archive onto another",
    )
    tiling_merge.add_argument("--base", type=Path, required=True, help="Base MBTiles archive")
    tiling_merge.add_argument("--overlay", type=Path, required=True, help="Overlay MBTiles archive")
    tiling_merge.add_argument("--out", type=Path, required=True, help="Output MBTiles archive")

    tiling_union = tiling_subcommands.add_parser(
        "union-mbtiles",
        help="Union several MBTiles into one in a single pass (for disjoint Quadrans pieces)",
    )
    tiling_union.add_argument("--inputs", type=Path, nargs="+", required=True,
                              help="Input MBTiles archives (pass the LARGEST first — it becomes the copy base)")
    tiling_union.add_argument("--out", type=Path, required=True, help="Output MBTiles archive")
    tiling_union.add_argument("--chunk-size", type=int, default=50_000,
                              help="Rows per commit when appending non-base inputs (bounds journal growth)")

    tiling_stitch = tiling_subcommands.add_parser(
        "stitch-512",
        help="Build a 512px pyramid from a 256px source (output zoom z <- source zoom z+1)",
    )
    tiling_stitch.add_argument("--source", type=Path, required=True, help="256px source MBTiles")
    tiling_stitch.add_argument("--out", type=Path, required=True, help="Output 512px MBTiles")
    tiling_stitch.add_argument("--format", default="jpg", help="Output tile format (default jpg)")
    tiling_stitch.add_argument("--quality", type=int, default=90, help="JPEG/WebP quality (default 90)")
    tiling_stitch.add_argument("--workers", type=int, default=1,
                               help="Parallel processes (CPU-bound; shards are unioned at the end)")

    build = subcommands.add_parser(
        "build",
        help="Build a custom planet from an AOI overlay spec (ADR 0001)",
    )
    build.add_argument("--spec", type=Path, required=True, help="AOI overlay pipeline spec (YAML)")
    build.add_argument("--config", type=Path, default=None, help="Base pipeline config (defaults to configs/base/pipeline.yaml)")
    build.add_argument("--base-mbtiles", type=Path, required=True, help="Prebuilt global base MBTiles (the floor)")
    build.add_argument("--work-dir", type=Path, default=None, help="Scratch dir for intermediates (default: output/build)")
    build.add_argument("--tile-size", type=int, default=512)
    build.add_argument("--no-strict", action="store_true", help="Warn instead of failing on zoom-ceiling violations")

    prefetch = subcommands.add_parser(
        "prefetch",
        help="Download-only: warm the Sentinel-2 asset cache for a spec's AOIs (no tiling)",
    )
    prefetch.add_argument("--spec", type=Path, required=True, help="AOI overlay pipeline spec (YAML)")
    prefetch.add_argument("--config", type=Path, default=None, help="Base pipeline config (defaults to configs/base/pipeline.yaml)")
    prefetch.add_argument("--work-dir", type=Path, default=None, help="Scratch dir (default: output/build)")
    prefetch.add_argument("--pace-min", type=float, default=60.0, help="Min inter-tile wait (s) when throughput was healthy")
    prefetch.add_argument("--pace-max", type=float, default=300.0, help="Max inter-tile wait (s) when throughput was healthy")
    prefetch.add_argument("--throttle-floor", type=float, default=150.0, help="KiB/s below which the last tile counts as throttled")
    prefetch.add_argument("--cooldown-min", type=float, default=600.0, help="Min cooldown (s) after a throttled tile")
    prefetch.add_argument("--cooldown-max", type=float, default=900.0, help="Max cooldown (s) after a throttled tile")
    prefetch.add_argument("--recovery-wait", type=float, default=1800.0, help="Seconds to wait between recovery rounds during a broad MPC outage")
    prefetch.add_argument("--max-recovery-rounds", type=int, default=6, help="Max rounds re-attempting failed overlays (1 = no recovery wait); rides out an MPC STAC outage")
    prefetch.add_argument("--dry-run", action="store_true", help="List the Sentinel-2 overlays that would be prefetched and exit")

    split_plan = subcommands.add_parser(
        "split-plan",
        help="Split a global HLS plan into one ndjson shard per miniplanet",
    )
    split_plan.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON)",
    )
    split_plan.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="Explicit plan ndjson to split (defaults to the global HLS plan)",
    )
    split_plan.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output directory for shards (defaults to data_dir/plans/shards)",
    )

    mpc_fetch = subcommands.add_parser(
        "mpc-fetch",
        help="Download a Sentinel-2 true color clip via Microsoft Planetary Computer",
    )
    mpc_fetch.add_argument("--lat", type=float, required=True, help="Latitude of the target point")
    mpc_fetch.add_argument("--lon", type=float, required=True, help="Longitude of the target point")
    mpc_fetch.add_argument(
        "--width-m",
        type=float,
        default=500.0,
        help="Clip width in meters (default: 500)",
    )
    mpc_fetch.add_argument(
        "--height-m",
        type=float,
        default=500.0,
        help="Clip height in meters (default: 500)",
    )
    mpc_fetch.add_argument(
        "--max-cloud",
        type=float,
        default=None,
        help="Maximum acceptable cloud cover percentage",
    )
    mpc_fetch.add_argument(
        "--start",
        dest="start_datetime",
        default=None,
        help="ISO8601 start datetime filter (inclusive)",
    )
    mpc_fetch.add_argument(
        "--end",
        dest="end_datetime",
        default=None,
        help="ISO8601 end datetime filter (inclusive)",
    )
    mpc_fetch.add_argument(
        "--output",
        type=Path,
        default=Path("mpc_true_color.tif"),
        help="Output GeoTIFF path (default: mpc_true_color.tif)",
    )
    mpc_fetch.add_argument(
        "--gdal-translate",
        default="gdal_translate",
        help="gdal_translate executable name (default: gdal_translate)",
    )
    mpc_fetch.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing GDAL",
    )

    gsi_fetch = subcommands.add_parser(
        "gsi-fetch",
        help="Download a GSI high-resolution orthophoto clip via the XYZ tile service",
    )
    gsi_fetch.add_argument("--lat", type=float, required=True, help="Latitude of the target point")
    gsi_fetch.add_argument("--lon", type=float, required=True, help="Longitude of the target point")
    gsi_fetch.add_argument(
        "--width-m",
        type=float,
        default=400.0,
        help="Clip width in meters (default: 400)",
    )
    gsi_fetch.add_argument(
        "--height-m",
        type=float,
        default=400.0,
        help="Clip height in meters (default: 400)",
    )
    gsi_fetch.add_argument(
        "--zoom",
        type=int,
        default=18,
        help="Tile zoom level (default: 18)",
    )
    gsi_fetch.add_argument(
        "--tile-template",
        default="https://cyberjapandata.gsi.go.jp/xyz/ortho/{z}/{x}/{y}.jpg",
        help="XYZ tile template URL (default: GSI ortho)",
    )
    gsi_fetch.add_argument(
        "--output",
        type=Path,
        default=Path("gsi_ortho.tif"),
        help="Output GeoTIFF path (default: gsi_ortho.tif)",
    )
    gsi_fetch.add_argument(
        "--gdal-translate",
        default="gdal_translate",
        help="gdal_translate executable name (default: gdal_translate)",
    )
    gsi_fetch.add_argument(
        "--gdal-buildvrt",
        default="gdalbuildvrt",
        help="gdalbuildvrt executable name (default: gdalbuildvrt)",
    )
    gsi_fetch.add_argument(
        "--gdal-warp",
        default="gdalwarp",
        help="gdalwarp executable name (default: gdalwarp)",
    )
    gsi_fetch.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing GDAL",
    )

    gsi_collect = subcommands.add_parser(
        "gsi-collect",
        help="Collect a GSI XYZ layer (e.g. seamlessphoto) nationwide into a zxy dir, mokuroku-driven",
    )
    gsi_collect.add_argument("--layer", default="seamlessphoto", help="GSI layer id (default: seamlessphoto)")
    gsi_collect.add_argument("--zoom-min", type=int, default=8)
    gsi_collect.add_argument("--zoom-max", type=int, default=16)
    gsi_collect.add_argument("--out", type=Path, default=None, help="Output zxy tile directory (omit when using --mbtiles)")
    gsi_collect.add_argument("--mbtiles", type=Path, default=None, help="Write tiles directly into this MBTiles (no intermediate z/x/y files; resumable)")
    gsi_collect.add_argument("--attribution", default=None, help="metadata attribution (when --mbtiles)")
    gsi_collect.add_argument("--name", default=None, help="metadata name (when --mbtiles)")
    gsi_collect.add_argument("--workers", type=int, default=10, help="Concurrent downloads (be polite; 403/429 trigger cool-down)")
    gsi_collect.add_argument("--ext", default="jpg")
    gsi_collect.add_argument("--quadrans", choices=["north", "east", "south", "west"], default=None,
                             help="Only collect tiles in this Quadrans region (UNopenGIS/7#909) — for splitting work")
    gsi_collect.add_argument("--mokuroku", default=None, help="mokuroku.csv.gz URL or local path (default: GSI URL for the layer)")
    gsi_collect.add_argument("--config", type=Path, default=None, help="Pipeline config (for cache dir defaults)")
    gsi_collect.add_argument("--dry-run", action="store_true", help="Report tile counts per zoom from mokuroku and exit")

    gsi_pack = subcommands.add_parser(
        "gsi-pack",
        help="Pack a z/x/y tile directory into an MBTiles archive (create or append; faster than mb-util)",
    )
    gsi_pack.add_argument("--tiles", type=Path, required=True, help="Source z/x/y tile directory")
    gsi_pack.add_argument("--out", type=Path, required=True, help="Destination MBTiles (created or appended)")
    gsi_pack.add_argument("--format", default="jpg", help="Tile image format stored in metadata (default: jpg)")
    gsi_pack.add_argument("--name", default=None, help="metadata name")
    gsi_pack.add_argument("--attribution", default=None, help="metadata attribution")
    gsi_pack.add_argument("--bounds", default=None, help="metadata bounds 'minlon,minlat,maxlon,maxlat'")
    gsi_pack.add_argument("--batch-size", type=int, default=10000, help="Insert batch size (default: 10000)")

    package = subcommands.add_parser("package", help="Create PMTiles distribution")
    package.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON)",
    )
    package.add_argument(
        "--pmtiles-name",
        type=str,
        default=None,
        help="Filename for the PMTiles archive (defaults to planet_<year>_<max_zoom_level>z.pmtiles)",
    )
    package.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to an MBTiles archive to package (defaults to the latest tiling output)",
    )
    package.add_argument(
        "--dry-run",
        action="store_true",
        help="Print packaging commands without executing",
    )
    package.add_argument(
        "--force",
        action="store_true",
        help="Regenerate PMTiles even if output exists",
    )

    serve = subcommands.add_parser("serve", help="Serve PMTiles with a simple web viewer")
    serve.add_argument("--pmtiles", type=Path, default=None, help="Path to the PMTiles archive")
    serve.add_argument("--region", type=str, default=None, help="Region name to resolve PMTiles from distribution")
    serve.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON) for region resolution",
    )
    serve.add_argument("--host", default="0.0.0.0", help="Host interface to bind (default: 0.0.0.0)")
    serve.add_argument("--tiles-port", type=int, default=8080, help="Port for pmtiles server")
    serve.add_argument("--ui-port", type=int, default=8081, help="Port for the viewer UI")
    serve.add_argument(
        "--viewer-root",
        type=Path,
        default=Path("src/planetarble/viewer"),
        help="Directory containing viewer assets",
    )
    serve.add_argument("--open", action="store_true", help="Open the viewer URL in a browser")

    copernicus_layers = subcommands.add_parser(
        "copernicus-layers",
        help="List available Copernicus WMS layers for the configured instance",
    )
    copernicus_layers.add_argument(
        "--instance-id",
        default=None,
        help="Override COPERNICUS_INSTANCE_ID for listing layers",
    )
    copernicus_layers.add_argument(
        "--no-credentials",
        action="store_true",
        help="Do not use client credentials when fetching capabilities",
    )
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


def _resolve_copernicus_cog(processing_dir: Path, layers: Iterable[CopernicusLayerConfig]) -> list[Path]:
    ordered: list[Path] = []
    seen: set[Path] = set()

    for layer in layers or []:
        slug = _slugify(layer.output or layer.name)
        candidate = processing_dir / f"copernicus_{slug}_cog.tif"
        if candidate.exists() and candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)

    for candidate in sorted(processing_dir.glob("copernicus_*_cog.tif")):
        if candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)

    return ordered


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "layer"


def _handle_build(args: argparse.Namespace) -> int:
    import yaml

    from planetarble.overlay import parse_pipeline_spec
    from planetarble.overlay.executor import DefaultPlanetExecutor
    from planetarble.overlay.orchestrator import build_planet

    cfg = load_config(_resolve_config_path(args.config))
    spec = parse_pipeline_spec(yaml.safe_load(args.spec.read_text(encoding="utf-8")))
    work_dir = (args.work_dir or (cfg.output_dir / "build")).resolve()
    executor = DefaultPlanetExecutor(
        spec,
        cfg,
        data_dir=cfg.data_dir,
        work_dir=work_dir,
        base_mbtiles=args.base_mbtiles.resolve(),
        tile_size=args.tile_size,
    )
    result = build_planet(spec, executor, data_dir=cfg.data_dir, strict=not args.no_strict)
    print(f"built planet: {result.planet}")
    print(f"  base: {result.base_mbtiles}")
    for s in result.stacks:
        print(f"  stack: {s}")
    return 0


def _handle_prefetch(args: argparse.Namespace) -> int:
    import random
    import time

    import yaml

    from planetarble.overlay import parse_pipeline_spec
    from planetarble.overlay.executor import DefaultPlanetExecutor
    from planetarble.prefetch import PrefetchPacing, prefetch_planet, prefetch_wait_seconds

    cfg = load_config(_resolve_config_path(args.config))
    spec = parse_pipeline_spec(yaml.safe_load(args.spec.read_text(encoding="utf-8")))
    s2_overlays = [o for o in spec.overlays if o.source == "sentinel2"]

    if args.dry_run:
        print(f"prefetch (dry-run): {len(s2_overlays)} sentinel2 overlay(s) would be fetched")
        for o in s2_overlays:
            scenes = (o.source_options or {}).get("mosaic_max_scenes", "config-default")
            print(f"  {o.name}: bbox={tuple(o.aoi.bbox) if o.aoi.bbox else o.aoi} scenes<={scenes}")
        return 0

    pacing = PrefetchPacing(
        throttle_floor_kibps=args.throttle_floor,
        jitter_min_s=args.pace_min, jitter_max_s=args.pace_max,
        cooldown_min_s=args.cooldown_min, cooldown_max_s=args.cooldown_max,
    )
    work_dir = (args.work_dir or (cfg.output_dir / "build")).resolve()
    # base_mbtiles is unused for prefetch (no compositing); pass the spec path as a placeholder
    executor = DefaultPlanetExecutor(
        spec, cfg, data_dir=cfg.data_dir, work_dir=work_dir, base_mbtiles=args.spec.resolve(),
    )

    def pacer(stats) -> None:
        wait = prefetch_wait_seconds(stats.downloaded_bytes, stats.elapsed_seconds, pacing, random.uniform)
        print(
            f"  {stats.overlay}: downloaded={stats.downloaded_count} "
            f"({stats.downloaded_bytes/1e6:.0f}MB) hits={stats.hit_count} "
            f"in {stats.elapsed_seconds:.0f}s -> wait {wait:.0f}s"
        )
        if wait > 0:
            time.sleep(wait)

    def on_error(ov, exc) -> None:
        print(f"  ERROR {ov.name}: {exc}")

    import time as _time

    def on_recovery_wait(round_index, n_failed, wait_s) -> None:
        print(
            f"  MPC appears down: {n_failed} overlay(s) failed in round {round_index}; "
            f"waiting {wait_s:.0f}s for recovery, then retrying (cached ones skip)"
        )

    results = prefetch_planet(
        spec, executor, pacer=pacer,
        on_skip=lambda ov: print(f"  skip {ov.name} (source={ov.source})"),
        on_error=on_error,
        recovery_wait_s=args.recovery_wait,
        max_rounds=args.max_recovery_rounds,
        on_recovery_wait=on_recovery_wait,
        sleeper=_time.sleep,
    )
    total_mb = sum(r.downloaded_bytes for r in results) / 1e6
    done_names = {r.overlay for r in results}
    failed = [o.name for o in s2_overlays if o.name not in done_names]
    print(f"prefetch done: {len(results)}/{len(s2_overlays)} sentinel2 overlay(s) ok, {total_mb:.0f}MB downloaded")
    if failed:
        print(f"  {len(failed)} still failing after {args.max_recovery_rounds} round(s): {', '.join(failed)} -- re-run later (cached ones skip fast)")
    return 0


def _handle_split_plan(args: argparse.Namespace) -> int:
    cfg = load_config(_resolve_config_path(args.config))
    plan_path = (args.plan.resolve() if args.plan else _resolve_hls_plan_path(cfg, None))
    if not plan_path.exists():
        raise SystemExit(f"Plan not found: {plan_path}")
    out_dir = (args.out.resolve() if args.out else (cfg.data_dir / "plans" / "shards"))
    shards = split_plan_by_miniplanet(plan_path, out_dir)
    print(f"split {plan_path} into {len(shards)} miniplanet shard(s) under {out_dir}")
    for key in sorted(shards):
        print(f"  {key}: {shards[key]}")
    return 0


def _resolve_hls_plan_path(cfg: PipelineConfig, plan_region: Optional[str]) -> Path:
    region = plan_region or cfg.hls.plan_region
    if region:
        return cfg.data_dir / "plans" / f"hls_z{cfg.hls.target_zoom}_plan_{region}.ndjson"
    return cfg.data_dir / "plans" / f"hls_z{cfg.hls.target_zoom}_plan.ndjson"


def _resolve_hls_scene_manifest_path(cfg: PipelineConfig, plan_region: Optional[str]) -> Path:
    region = plan_region or cfg.hls.plan_region
    filename = "hls_scene_manifest.json" if not region else f"hls_scene_manifest_{region}.json"
    return (cfg.output_dir / "processing" / filename).resolve()


def _resolve_sentinel2_scene_manifest_path(cfg: PipelineConfig, plan_region: Optional[str]) -> Path:
    region = plan_region or cfg.sentinel2.plan_region
    filename = (
        "sentinel2_scene_manifest.json"
        if not region
        else f"sentinel2_scene_manifest_{region}.json"
    )
    return (cfg.output_dir / "processing" / filename).resolve()


def _is_valid_hls_scene_manifest(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    scenes = data.get("scenes")
    summary = data.get("summary")
    if not isinstance(scenes, list) or not scenes:
        return False
    if not isinstance(summary, dict):
        return False
    return True


def _is_valid_sentinel2_scene_manifest(path: Path, *, required_assets: Optional[Sequence[str]] = None) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    scenes = data.get("scenes")
    summary = data.get("summary")
    if not isinstance(scenes, list) or not scenes:
        return False
    if not isinstance(summary, dict):
        return False
    if required_assets:
        asset_set = {asset.lower() for asset in required_assets if asset}
        if asset_set:
            matched = False
            for scene in scenes:
                assets = scene.get("assets") if isinstance(scene, dict) else None
                if not isinstance(assets, dict):
                    continue
                available = {str(name).lower() for name in assets.keys()}
                if asset_set.issubset(available):
                    matched = True
                    break
            if not matched:
                return False
    return True


def _is_valid_raster(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        from osgeo import gdal  # type: ignore
    except Exception:
        return True
    try:
        gdal.UseExceptions()
    except AttributeError:
        pass
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        return False
    if dataset.RasterXSize <= 0 or dataset.RasterYSize <= 0:
        return False
    return dataset.RasterCount > 0


def _is_valid_mbtiles(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(str(path), timeout=1)
    except sqlite3.Error:
        return False
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tiles'")
        if cur.fetchone() is None:
            return False
        cur.execute("SELECT COUNT(*) FROM tiles")
        count = cur.fetchone()[0]
        return count > 0
    finally:
        conn.close()


def _read_mbtiles_metadata(path: Path) -> dict[str, object]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        conn = sqlite3.connect(str(path), timeout=1)
    except sqlite3.Error:
        return {}
    try:
        cur = conn.cursor()
        meta_exists = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone()
        metadata: dict[str, object] = {}
        if meta_exists:
            for key, value in cur.execute("SELECT name, value FROM metadata"):
                metadata[str(key)] = value
        min_zoom = metadata.get("minzoom")
        max_zoom = metadata.get("maxzoom")
        if min_zoom is None or max_zoom is None:
            row = cur.execute("SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles").fetchone()
            if row:
                if min_zoom is None:
                    min_zoom = row[0]
                if max_zoom is None:
                    max_zoom = row[1]
        if min_zoom is not None:
            metadata["minzoom"] = int(min_zoom)
        if max_zoom is not None:
            metadata["maxzoom"] = int(max_zoom)
        bounds = metadata.get("bounds")
        if isinstance(bounds, str):
            parts = [p.strip() for p in bounds.split(",")]
            if len(parts) == 4:
                try:
                    metadata["bounds"] = [float(part) for part in parts]
                except ValueError:
                    pass
        center = metadata.get("center")
        if isinstance(center, str):
            parts = [p.strip() for p in center.split(",")]
            if len(parts) == 3:
                try:
                    metadata["center"] = [float(parts[0]), float(parts[1]), int(float(parts[2]))]
                except ValueError:
                    pass
        return metadata
    finally:
        conn.close()


def _is_valid_pmtiles(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    pmtiles_cli = shutil.which("pmtiles")
    if pmtiles_cli is None:
        return True
    try:
        subprocess.run([pmtiles_cli, "verify", str(path)], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        return False
    return True


def _is_valid_tilejson(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    required = ("tilejson", "tiles", "minzoom", "maxzoom", "bounds")
    return all(key in data for key in required)


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


def _handle_process(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    cfg = load_config(config_path)

    manager = ProcessingManager(
        cfg.processing,
        temp_dir=cfg.temp_dir,
        output_dir=cfg.output_dir,
        data_dir=cfg.data_dir,
        copernicus=cfg.copernicus,
        sentinel2=cfg.sentinel2,
        modis=cfg.modis,
        viirs=cfg.viirs,
        hls=cfg.hls,
        ocean=cfg.ocean,
        dry_run=args.dry_run,
    )
    tile_source = cfg.processing.tile_source.lower()
    if tile_source == "hls":
        plan_path = _resolve_hls_plan_path(cfg, args.plan_region)
        if not plan_path.exists():
            raise SystemExit(
                f"HLS plan not found at {plan_path}; run 'planetarble acquire' before processing"
            )
        manifest_path = _resolve_hls_scene_manifest_path(cfg, args.plan_region)
        scene_manifest: Optional[Path] = None
        if _is_valid_hls_scene_manifest(manifest_path) and not args.force:
            log_skip(LOGGER, phase="process", reason="valid HLS scene manifest", path=str(manifest_path))
            scene_manifest = manifest_path
        else:
            scene_manifest = manager.prepare_hls_scene_manifest(
                plan_path,
                destination=manifest_path,
            )
        region = args.plan_region or cfg.hls.plan_region
        if scene_manifest and region:
            mosaic_path = (cfg.output_dir / "processing" / f"hls_mosaic_{region}_cog.tif").resolve()
            if _is_valid_raster(mosaic_path) and not args.force:
                log_skip(LOGGER, phase="process", reason="valid HLS mosaic", path=str(mosaic_path))
            else:
                manager.build_hls_mosaic(scene_manifest, plan_region=args.plan_region)

        ocean_outputs: Dict[str, Path] = {}
        if cfg.ocean.enabled:
            etopo_path = (cfg.data_dir / "etopo" / "ETOPO_2022_15s_bed.tif").resolve()
            if not etopo_path.exists():
                LOGGER.warning(
                    "ETOPO raster not found; skipping ocean shading",
                    extra={"path": str(etopo_path)},
                )
            else:
                ocean_dir = (cfg.output_dir / "processing" / "ocean").resolve()
                color_path = ocean_dir / "etopo_depth_color.tif"
                hillshade_path = ocean_dir / "etopo_hillshade.tif"
                color_ok = _is_valid_raster(color_path)
                hillshade_ok = True
                if cfg.ocean.apply_hillshade:
                    hillshade_ok = _is_valid_raster(hillshade_path)
                if color_ok and hillshade_ok:
                    log_skip(
                        LOGGER,
                        phase="process",
                        reason="valid ocean shading outputs",
                        extra={"color": str(color_path), "hillshade": str(hillshade_path)},
                    )
                    ocean_outputs = {
                        "color": color_path,
                        "hillshade": hillshade_path if cfg.ocean.apply_hillshade else Path(),
                    }
                else:
                    ocean_outputs = manager.render_ocean(etopo_path)

        LOGGER.info(
            "HLS preprocessing complete",
            extra={
                "scene_manifest": str(scene_manifest) if scene_manifest else None,
                "ocean_outputs": {key: str(value) for key, value in ocean_outputs.items() if value},
            },
        )
        return 0
    if tile_source == "sentinel2":
        if not cfg.sentinel2.enabled:
            raise SystemExit("Sentinel-2 processing requested but sentinel2.enabled is false")
        plan_region = args.plan_region or cfg.sentinel2.plan_region
        if cfg.sentinel2.plan_regions and not plan_region:
            raise SystemExit("sentinel2.plan_regions is set; pass --plan-region or set sentinel2.plan_region")
        manifest_path = _resolve_sentinel2_scene_manifest_path(cfg, plan_region)
        scene_manifest: Optional[Path] = None
        if _is_valid_sentinel2_scene_manifest(manifest_path, required_assets=cfg.sentinel2.assets) and not args.force:
            log_skip(LOGGER, phase="process", reason="valid Sentinel-2 scene manifest", path=str(manifest_path))
            scene_manifest = manifest_path
        else:
            scene_manifest = manager.prepare_sentinel2_scene_manifest(
                destination=manifest_path,
                force_refresh=args.force,
                plan_region=plan_region,
            )
        if scene_manifest:
            mosaic_name = (
                f"sentinel2_mosaic_{plan_region}_cog.tif"
                if plan_region
                else "sentinel2_mosaic_cog.tif"
            )
            mosaic_path = (cfg.output_dir / "processing" / mosaic_name).resolve()
            if _is_valid_raster(mosaic_path) and not args.force:
                log_skip(LOGGER, phase="process", reason="valid Sentinel-2 mosaic", path=str(mosaic_path))
            else:
                manager.build_sentinel2_mosaic(scene_manifest, force=args.force, plan_region=plan_region)
        LOGGER.info(
            "Sentinel-2 preprocessing complete",
            extra={"scene_manifest": str(scene_manifest) if scene_manifest else None},
        )
        return 0
    if tile_source == "copernicus":
        if not cfg.copernicus.enabled:
            raise SystemExit("Copernicus processing requested but copernicus.enabled is false")
        try:
            copernicus_cogs = manager.prepare_copernicus_layers(force=args.force or args.dry_run)
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            LOGGER.warning("copernicus processing skipped: %s", exc)
            copernicus_cogs = []
        LOGGER.info(
            "Copernicus preprocessing complete",
            extra={"copernicus_cogs": [str(path) for path in copernicus_cogs]},
        )
        return 0
    if tile_source == "gsi_orthophotos":
        if not cfg.gsi_orthophotos.enabled:
            raise SystemExit("GSI processing requested but gsi_orthophotos.enabled is false")
        gsi_output = (cfg.output_dir / "processing" / f"{cfg.gsi_orthophotos.output_basename}.tif").resolve()
        product_slug = "seamlessphoto" if cfg.gsi_orthophotos.product == "seamlessphoto" else "orthophoto"
        gsi_cache_root = cfg.data_dir / "cache" / f"gsi_{product_slug}"
        if not args.dry_run:
            gsi_cache_root.mkdir(parents=True, exist_ok=True)
        LOGGER.info("gsi tile cache", extra={"path": str(gsi_cache_root)})
        try:
            gsi_summary = fetch_gsi_ortho_clip(
                lat=cfg.gsi_orthophotos.lat,
                lon=cfg.gsi_orthophotos.lon,
                width_m=cfg.gsi_orthophotos.width_m,
                height_m=cfg.gsi_orthophotos.height_m,
                bbox=cfg.gsi_orthophotos.bbox,
                zoom=cfg.gsi_orthophotos.zoom,
                tile_template=_resolve_gsi_tile_template(cfg.gsi_orthophotos),
                cache_dir=gsi_cache_root,
                rate_limit_seconds=cfg.gsi_orthophotos.rate_limit_seconds,
                output_path=gsi_output,
                timeout=cfg.gsi_orthophotos.timeout_seconds,
                dry_run=args.dry_run,
            )
        except GSIError as exc:
            raise SystemExit(f"Failed to fetch GSI orthophotos: {exc}") from exc
        LOGGER.info(
            "GSI preprocessing complete",
            extra={"output": gsi_summary.get("output") if gsi_summary else str(gsi_output)},
        )
        return 0

    bmng_dir = (cfg.data_dir / "bmng" / cfg.processing.bmng_resolution).resolve()
    if not bmng_dir.exists():
        raise SystemExit(f"BMNG directory not found: {bmng_dir}")
    bmng_panels = tuple(sorted(bmng_dir.glob("*.tif")))
    bmng_source = manager.compose_bmng_panels(bmng_dir)
    normalized = manager.normalize_bmng(bmng_source, source_files=bmng_panels)

    gebco_path = (cfg.data_dir / "gebco" / f"GEBCO_{cfg.processing.gebco_year}_CF.nc").resolve()
    if not gebco_path.exists():
        raise SystemExit(f"GEBCO file not found: {gebco_path}")
    hillshade = manager.generate_hillshade(gebco_path)

    natural_earth_dir = (cfg.data_dir / "natural_earth").resolve()
    if not natural_earth_dir.exists():
        raise SystemExit(f"Natural Earth directory not found: {natural_earth_dir}")
    masks_dir = manager.create_masks(natural_earth_dir)

    cog_path = manager.create_cog(normalized)

    modis_cog_path: Path | None = None
    if cfg.modis.enabled:
        if not cfg.modis.doy:
            raise SystemExit("modis.doy must be set when modis.enabled is true")
        modis_root = (cfg.data_dir / "modis_mcd43a4" / cfg.modis.doy).resolve()
        if not modis_root.exists():
            raise SystemExit(f"MODIS directory not found: {modis_root}")
        tiles = cfg.modis.tiles or tuple(sorted(p.name for p in modis_root.iterdir() if p.is_dir()))
        if not tiles:
            raise SystemExit(f"No MODIS tiles found under {modis_root}")
        modis_cog_path = manager.prepare_modis_rgb(
            modis_root,
            tiles=tiles,
            date_code=cfg.modis.doy,
        )

    viirs_cog_path: Path | None = None
    if cfg.viirs.enabled:
        if not cfg.viirs.date:
            raise SystemExit("viirs.date must be set when viirs.enabled is true")
        viirs_root = (cfg.data_dir / "viirs_vnp09ga" / cfg.viirs.date).resolve()
        if not viirs_root.exists():
            raise SystemExit(f"VIIRS directory not found: {viirs_root}")
        tiles = cfg.viirs.tiles or tuple(
            sorted(p.name for p in viirs_root.iterdir() if p.is_dir())
        )
        if not tiles:
            raise SystemExit(f"No VIIRS tiles found under {viirs_root}")
        viirs_cog_path = manager.prepare_viirs_rgb(
            viirs_root,
            tiles=tiles,
            date_code=cfg.viirs.date,
        )

    copernicus_cogs: list[Path] = []
    if cfg.copernicus.enabled:
        try:
            copernicus_cogs = manager.prepare_copernicus_layers(force=args.dry_run)
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            LOGGER.warning("copernicus processing skipped: %s", exc)

    gsi_summary: dict[str, object] | None = None
    gsi_cog_path: Path | None = None
    if cfg.gsi_orthophotos.enabled:
        gsi_output = (cfg.output_dir / "processing" / f"{cfg.gsi_orthophotos.output_basename}.tif").resolve()
        product_slug = "seamlessphoto" if cfg.gsi_orthophotos.product == "seamlessphoto" else "orthophoto"
        gsi_cache_root = cfg.data_dir / "cache" / f"gsi_{product_slug}"
        if not args.dry_run:
            gsi_cache_root.mkdir(parents=True, exist_ok=True)
        LOGGER.info("gsi tile cache", extra={"path": str(gsi_cache_root)})
        try:
            gsi_summary = fetch_gsi_ortho_clip(
                lat=cfg.gsi_orthophotos.lat,
                lon=cfg.gsi_orthophotos.lon,
                width_m=cfg.gsi_orthophotos.width_m,
                height_m=cfg.gsi_orthophotos.height_m,
                bbox=cfg.gsi_orthophotos.bbox,
                zoom=cfg.gsi_orthophotos.zoom,
                tile_template=_resolve_gsi_tile_template(cfg.gsi_orthophotos),
                cache_dir=gsi_cache_root,
                rate_limit_seconds=cfg.gsi_orthophotos.rate_limit_seconds,
                output_path=gsi_output,
                timeout=cfg.gsi_orthophotos.timeout_seconds,
                dry_run=args.dry_run,
            )
        except GSIError as exc:
            raise SystemExit(f"Failed to fetch GSI orthophotos: {exc}") from exc
        gsi_output_str = gsi_summary.get("output") if gsi_summary else str(gsi_output)
        gsi_cog_path = Path(gsi_output_str)

    LOGGER.info("processing outputs", extra={
        "bmng_mosaic": str(bmng_source),
        "normalized": str(normalized),
        "hillshade": str(hillshade),
        "masks": str(masks_dir),
        "cog": str(cog_path),
        "modis_cog": str(modis_cog_path) if modis_cog_path else None,
        "viirs_cog": str(viirs_cog_path) if viirs_cog_path else None,
        "copernicus_cogs": [str(path) for path in copernicus_cogs] if copernicus_cogs else None,
        "gsi_cog": str(gsi_cog_path) if gsi_cog_path else None,
        "gsi_summary": gsi_summary,
    })
    return 0


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


def _resolve_gsi_tile_template(config: GSIOrthophotoConfig) -> str:
    template = config.tile_template
    if template:
        return template
    product = (config.product or "seamlessphoto").lower()
    if product == "seamlessphoto":
        return "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
    if product == "orthophoto":
        return "https://cyberjapandata.gsi.go.jp/xyz/ort/{z}/{x}/{y}.jpg"
    raise SystemExit(f"Unsupported gsi_orthophotos.product value: {config.product}")


def _handle_tiling_pmtiles(args: argparse.Namespace) -> int:
    source_path = args.input.resolve()
    if not source_path.exists():
        raise SystemExit(f"Input raster not found: {source_path}")

    output_dir = args.out.resolve()
    temp_dir = (args.temp_dir or (output_dir / "tmp")).resolve()

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)

    config = ProcessingConfig()
    min_zoom = args.min_zoom if args.min_zoom is not None else config.min_zoom
    max_zoom = args.max_zoom if args.max_zoom is not None else config.max_zoom
    if max_zoom < min_zoom:
        raise SystemExit("--max-zoom must be greater than or equal to --min-zoom")

    tile_format = (args.format or config.tile_format).upper()
    tile_quality = args.quality if args.quality is not None else config.tile_quality
    resampling = args.resampling or config.resampling
    name = args.name or config.tile_name
    attribution = args.attribution or config.tile_attribution
    deduplicate = config.pmtiles_dedup and not args.no_deduplication

    manager = PmtilesTilingManager(
        config,
        temp_dir=temp_dir,
        output_dir=output_dir,
        dry_run=args.dry_run,
    )

    zxy_dir = manager.build_zxy(
        source_path,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        tile_format=tile_format,
        quality=tile_quality,
        resampling=resampling,
    )

    mbtiles_path = manager.pack_mbtiles(
        zxy_dir,
        source_path=source_path,
        tile_format=tile_format,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        name=name,
        attribution=attribution,
        bounds_mode=args.bounds_mode,
    )

    pmtiles_destination = output_dir / f"{source_path.stem}_{min_zoom}-{max_zoom}.pmtiles"
    pmtiles_path = manager.convert_pmtiles(
        mbtiles_path,
        destination=pmtiles_destination,
        deduplicate=deduplicate,
        cluster=args.cluster,
    )

    manager.verify(pmtiles_path)
    header = manager.show_header(pmtiles_path)
    if header:
        LOGGER.info("pmtiles header", extra=header)

    if not args.dry_run:
        LOGGER.info(
            "pmtiles build complete",
            extra={
                "pmtiles": str(pmtiles_path),
                "mbtiles": str(mbtiles_path),
                "zxy_dir": str(zxy_dir),
            },
        )
    return 0


def _handle_merge_mbtiles(args: argparse.Namespace) -> int:
    base = args.base.resolve()
    overlay = args.overlay.resolve()
    out = args.out.resolve()
    merged = merge_mbtiles(base, overlay, destination=out)
    LOGGER.info(
        "mbtiles merge complete",
        extra={"base": str(base), "overlay": str(overlay), "output": str(merged)},
    )
    return 0


def _handle_union_mbtiles(args: argparse.Namespace) -> int:
    import time

    inputs = [p.resolve() for p in args.inputs]
    out = args.out.resolve()
    start = time.monotonic()
    last = [start]

    def _progress(inserted: int) -> None:
        now = time.monotonic()
        if now - last[0] < 30:
            return
        last[0] = now
        elapsed = now - start
        LOGGER.info(
            "mbtiles union progress",
            extra={"inserted": inserted,
                   "tiles_per_s": round(inserted / elapsed) if elapsed else 0,
                   "seconds": round(elapsed, 1)},
        )

    union_mbtiles(inputs, out, chunk_size=args.chunk_size, on_progress=_progress)
    LOGGER.info(
        "mbtiles union complete",
        extra={"inputs": [str(p) for p in inputs], "output": str(out),
               "seconds": round(time.monotonic() - start, 1)},
    )
    return 0


def _handle_stitch_512(args: argparse.Namespace) -> int:
    import time

    from planetarble.tiling.mbtiles import stitch_to_512

    source = args.source.resolve()
    out = args.out.resolve()
    start = time.monotonic()
    last = [start]

    def _progress(written: int) -> None:
        now = time.monotonic()
        if now - last[0] < 30:
            return
        last[0] = now
        elapsed = now - start
        LOGGER.info(
            "stitch-512 progress",
            extra={"out_tiles": written,
                   "tiles_per_s": round(written / elapsed) if elapsed else 0,
                   "seconds": round(elapsed, 1)},
        )

    stitch_to_512(source, out, tile_format=args.format, quality=args.quality,
                  workers=args.workers, on_progress=_progress)
    LOGGER.info(
        "stitch-512 complete",
        extra={"source": str(source), "output": str(out),
               "seconds": round(time.monotonic() - start, 1)},
    )
    return 0


def _handle_package(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    cfg = load_config(config_path)

    tiling_dir = (cfg.output_dir / "tiling").resolve()
    if not tiling_dir.exists():
        raise SystemExit(f"Tiling directory not found: {tiling_dir}")
    if args.input is not None:
        mbtiles_path = args.input.resolve()
        if not mbtiles_path.exists():
            raise SystemExit(f"MBTiles archive not found: {mbtiles_path}")
    else:
        mbtiles_candidates = sorted(
            tiling_dir.glob(f"planet_{cfg.processing.gebco_year}_{cfg.processing.max_zoom}z.mbtiles")
        )
        if not mbtiles_candidates:
            raise SystemExit("No MBTiles archive found; run the tile stage first")
        mbtiles_path = mbtiles_candidates[0]

    pmtiles_name = args.pmtiles_name or f"planet_{cfg.processing.gebco_year}_{cfg.processing.max_zoom}z.pmtiles"
    pmtiles_destination = tiling_dir / pmtiles_name
    tilejson_destination = pmtiles_destination.with_suffix(".tilejson.json")
    if _is_valid_pmtiles(pmtiles_destination) and _is_valid_tilejson(tilejson_destination) and not args.force:
        log_skip(
            LOGGER,
            phase="package",
            reason="valid PMTiles and TileJSON",
            extra={"pmtiles": str(pmtiles_destination), "tilejson": str(tilejson_destination)},
        )
        return 0

    packaging = PackagingManager(dry_run=args.dry_run)
    pmtiles_path = packaging.convert_to_pmtiles(mbtiles_path, destination=pmtiles_destination)

    tile_source = (cfg.processing.tile_source or cfg.modis.tile_source or "bmng").lower()

    if tile_source == "modis":
        imagery_label = f"MODIS MCD43A4 ({cfg.modis.doy or 'unknown date'})"
        imagery_attribution = "Imagery: NASA MODIS MCD43A4 (LP DAAC)."
    elif tile_source == "viirs":
        product = cfg.viirs.product or "VNP09GA"
        imagery_label = f"VIIRS Corrected Reflectance ({product} {cfg.viirs.date or 'daily'})"
        imagery_attribution = "Imagery: NASA VIIRS Corrected Reflectance (LP DAAC)."
    elif tile_source == "copernicus":
        imagery_label = "Copernicus Sentinel-2 Level-2A"
        imagery_attribution = "Imagery: Copernicus Sentinel-2 (European Space Agency)."
    elif tile_source == "sentinel2":
        imagery_label = "Sentinel-2 L2A (Microsoft Planetary Computer)"
        imagery_attribution = "Imagery: Copernicus Sentinel-2 (European Space Agency)."
    elif tile_source == "gsi_orthophotos":
        imagery_label = "GSI Seamless Orthophoto"
        imagery_attribution = "Imagery: Geospatial Information Authority of Japan (GSI) Seamless Orthophotography."
    else:
        imagery_label = "NASA Blue Marble Next Generation (2004)"
        imagery_attribution = "Imagery: NASA Blue Marble (2004)."

    mbtiles_meta = _read_mbtiles_metadata(mbtiles_path)
    bounds = tuple(mbtiles_meta.get("bounds") or (-180.0, -85.0511, 180.0, 85.0511))
    center = tuple(mbtiles_meta.get("center") or (0.0, 0.0, 2))
    minzoom = int(mbtiles_meta.get("minzoom") or 0)
    maxzoom = int(mbtiles_meta.get("maxzoom") or cfg.processing.max_zoom)
    tile_format = str(mbtiles_meta.get("format") or cfg.processing.tile_format)

    metadata = TileMetadata(
        name=f"Planetarble {cfg.processing.gebco_year}",
        description=f"Global basemap composed from {imagery_label} and GEBCO bathymetry.",
        version=str(cfg.processing.gebco_year),
        bounds=bounds,
        center=center,
        minzoom=minzoom,
        maxzoom=maxzoom,
        attribution=(
            f"{imagery_attribution} Bathymetry: GEBCO 2024. Masks: Natural Earth 10m."
        ),
        format=tile_format,
    )
    tilejson_path = packaging.generate_tilejson(pmtiles_path, metadata)

    manifest_path = (cfg.output_dir / "MANIFEST.json").resolve()
    imagery_line = {
        "modis": "- MODIS MCD43A4 BRDF-Corrected Reflectance (NASA LP DAAC).",
        "viirs": "- VIIRS Corrected Reflectance (VNP09GA, NASA LP DAAC).",
        "copernicus": "- Copernicus Sentinel-2 Level-2A (European Space Agency).",
        "sentinel2": "- Copernicus Sentinel-2 Level-2A via Microsoft Planetary Computer.",
        "gsi_orthophotos": "- Geospatial Information Authority of Japan Seamless Orthophotography.",
    }.get(tile_source, "- NASA Blue Marble Next Generation (2004).")

    license_text = (
        "Planetarble Distribution\n\n"
        "Data Sources:\n"
        f"{imagery_line}\n"
        "- GEBCO 2024 Global Bathymetry Grid.\n"
        "- Natural Earth 1:10m land/ocean/coastline layers.\n\n"
        "Attribution:\n"
        f"{imagery_attribution} Bathymetry courtesy of GEBCO Compilation Group. "
        "Natural Earth data is in the public domain."
    )
    package_dir = packaging.create_distribution_package(
        pmtiles_path,
        tilejson_path=tilejson_path,
        manifest_path=manifest_path,
        license_text=license_text,
        destination=cfg.output_dir / "distribution",
    )

    LOGGER.info("packaging outputs", extra={
        "mbtiles": str(mbtiles_path),
        "pmtiles": str(pmtiles_path),
        "tilejson": str(tilejson_path),
        "distribution": str(package_dir),
    })
    return 0


def _handle_serve(args: argparse.Namespace) -> int:
    if args.pmtiles is None and not args.region:
        raise SystemExit("--pmtiles or --region must be provided")
    if args.pmtiles is not None and args.region:
        raise SystemExit("--pmtiles and --region are mutually exclusive")

    if args.pmtiles is not None:
        pmtiles_path = args.pmtiles.resolve()
    else:
        cfg = load_config(_resolve_config_path(args.config))
        region = args.region
        distribution_dir = (cfg.output_dir / "distribution").resolve()
        region_variants = [region]
        if region.endswith("_land"):
            region_variants.append(region[: -len("_land")])
        candidates: list[Path] = []
        for variant in region_variants:
            pattern = f"planet_{cfg.processing.gebco_year}_*z_{variant}_hls.pmtiles"
            candidates.extend(distribution_dir.glob(pattern))
        if not candidates:
            pmtiles_name = f"planet_{cfg.processing.gebco_year}_{cfg.processing.max_zoom}z_{region}_hls.pmtiles"
            pmtiles_path = (distribution_dir / pmtiles_name).resolve()
        else:
            def zoom_key(path: Path) -> int:
                match = re.search(r"_(\d+)z_", path.name)
                return int(match.group(1)) if match else -1

            pmtiles_path = max(candidates, key=zoom_key).resolve()
    center = None
    if args.region:
        try:
            from planetarble.acquisition.hls import load_region_geometry
            from osgeo import ogr  # type: ignore
        except Exception:
            center = None
        else:
            cfg = load_config(_resolve_config_path(args.config))
            region_name = args.region
            region_config = None
            for item in cfg.hls.plan_regions:
                if item.name == region_name:
                    region_config = item
                    break
            if region_config is not None:
                geometry = load_region_geometry(region_config, data_dir=cfg.data_dir)
                if geometry is not None:
                    envelope = geometry.GetEnvelope()
                    center = ((envelope[0] + envelope[1]) / 2, (envelope[2] + envelope[3]) / 2)
    if not pmtiles_path.exists():
        raise SystemExit(f"PMTiles file not found: {pmtiles_path}")

    viewer_root = args.viewer_root.resolve()
    if not viewer_root.exists():
        raise SystemExit(f"Viewer directory not found: {viewer_root}")

    tiles_host = args.host
    ui_port = args.ui_port
    tiles_port = args.tiles_port
    if tiles_port != ui_port:
        LOGGER.warning(
            "serve uses a single HTTP server; ignoring tiles-port",
            extra={"tiles_port": tiles_port, "ui_port": ui_port},
        )

    distribution_dir = pmtiles_path.parent
    viewer_target = distribution_dir / "viewer"
    viewer_target.mkdir(parents=True, exist_ok=True)
    source_index = viewer_root / "index.html"
    if not source_index.exists():
        raise SystemExit(f"Viewer index not found: {source_index}")
    shutil.copy2(source_index, viewer_target / "index.html")

    bind_host = tiles_host if tiles_host != "0.0.0.0" else "localhost"
    ui_url = f"http://{bind_host}:{ui_port}/viewer/"
    pmtiles_url = f"http://{bind_host}:{ui_port}/{pmtiles_path.name}"

    LOGGER.info(
        "serve starting",
        extra={"pmtiles": str(pmtiles_path), "viewer": str(viewer_target)},
    )
    LOGGER.info("serve commands", extra={"ui": f"python -m planetarble serve {tiles_host}:{ui_port}"})
    viewer_url = f"{ui_url}?pmtiles={pmtiles_url}"
    if center is not None:
        viewer_url = f"{viewer_url}#8/{center[1]}/{center[0]}"
    LOGGER.info("serve urls", extra={"viewer": viewer_url, "pmtiles": pmtiles_url})
    LOGGER.info("open viewer: %s", viewer_url)

    try:
        if args.open:
            import webbrowser

            webbrowser.open(f"{ui_url}?pmtiles={pmtiles_url}")
        handler = functools.partial(_RangeRequestHandler, directory=str(distribution_dir))
        httpd = http.server.ThreadingHTTPServer((tiles_host, ui_port), handler)
        LOGGER.info("serve http", extra={"address": f"{tiles_host}:{ui_port}"})
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("serve interrupted; shutting down")
    finally:
        try:
            httpd.shutdown()  # type: ignore[name-defined]
        except Exception:
            pass
    return 0


class _RangeRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Serve files with HTTP range support for PMTiles."""

    def send_head(self):  # type: ignore[override]
        path = self.translate_path(self.path)
        if not os.path.exists(path):
            return super().send_head()
        if os.path.isdir(path):
            return super().send_head()

        f = None
        try:
            f = open(path, "rb")
            fs = os.fstat(f.fileno())
            size = fs.st_size
            range_header = self.headers.get("Range")
            if range_header:
                start, end = self._parse_range(range_header, size)
                if start is None:
                    self.send_error(416, "Requested Range Not Satisfiable")
                    return None
                self.send_response(206)
                self.send_header("Content-Type", self.guess_type(path))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                f.seek(start)
                self.wfile.write(f.read(end - start + 1))
                f.close()
                return None

            self.send_response(200)
            self.send_header("Content-Type", self.guess_type(path))
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            return f
        except OSError:
            if f:
                f.close()
            self.send_error(404, "File not found")
            return None

    def _parse_range(self, header: str, size: int) -> tuple[Optional[int], int]:
        if not header.startswith("bytes="):
            return None, 0
        range_spec = header.split("=", 1)[1]
        if "," in range_spec:
            range_spec = range_spec.split(",", 1)[0]
        start_str, end_str = range_spec.split("-", 1)
        if start_str == "":
            length = int(end_str)
            start = max(size - length, 0)
            end = size - 1
            return start, end
        start = int(start_str)
        end = int(end_str) if end_str else size - 1
        if start >= size:
            return None, 0
        end = min(end, size - 1)
        return start, end


def _handle_copernicus_layers(args: argparse.Namespace) -> int:
    from planetarble.acquisition import CopernicusAccessError, CopernicusAuthError, get_available_layers

    try:
        layers = get_available_layers(
            instance_id=args.instance_id,
            use_credentials=not args.no_credentials,
        )
    except CopernicusCredentialsMissing as exc:
        LOGGER.error(str(exc))
        return 1
    except (CopernicusAuthError, CopernicusAccessError) as exc:
        LOGGER.error("Unable to list Copernicus layers: %s", exc)
        return 1

    if not layers:
        LOGGER.warning("No layers found for the specified Copernicus instance")
        return 0

    for name, title in layers:
        print(f"{name}\t{title}")
    return 0


def _handle_mpc_fetch(args: argparse.Namespace) -> int:
    try:
        summary = fetch_true_color_tile(
            lat=args.lat,
            lon=args.lon,
            width_m=args.width_m,
            height_m=args.height_m,
            output_path=args.output,
            max_cloud=args.max_cloud,
            start_datetime=args.start_datetime,
            end_datetime=args.end_datetime,
            gdal_translate=args.gdal_translate,
            dry_run=args.dry_run,
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except MPCError as exc:
        LOGGER.error("MPC fetch failed: %s", exc)
        return 1

    LOGGER.info("mpc fetch complete", extra=summary)
    if args.dry_run:
        print(summary)
    return 0


def _handle_gsi_collect(args: argparse.Namespace) -> int:
    import time

    from planetarble.acquisition.mokuroku import (
        fetch_mokuroku, iter_mokuroku_lines, mokuroku_url, read_mokuroku_gz,
    )
    from planetarble.acquisition.tiles import download_xyz_tiles

    cfg = load_config(_resolve_config_path(args.config))
    layer = args.layer
    template = f"https://cyberjapandata.gsi.go.jp/xyz/{layer}/{{z}}/{{x}}/{{y}}.{args.ext}"

    # resolve the mokuroku catalog (download if a URL / default)
    src = args.mokuroku or mokuroku_url(layer)
    if str(src).startswith(("http://", "https://")):
        cache = (cfg.data_dir / "cache" / "mokuroku" / f"{layer}.csv.gz").resolve()
        LOGGER.info("fetching mokuroku", extra={"url": src, "dest": str(cache)})
        gz = fetch_mokuroku(src, cache)
    else:
        gz = Path(src)

    quad = getattr(args, "quadrans", None)
    if quad is not None:
        from planetarble.tiling.quadrans import quadrans_of_tile

    if args.dry_run:
        counts: dict = {}
        total_bytes = 0
        for e in iter_mokuroku_lines(read_mokuroku_gz(gz), zoom_min=args.zoom_min, zoom_max=args.zoom_max):
            if quad is not None and quadrans_of_tile(e.z, e.x, e.y) != quad:
                continue
            counts[e.z] = counts.get(e.z, 0) + 1
            total_bytes += e.size
        n = sum(counts.values())
        print(f"gsi-collect dry-run: {layer} z{args.zoom_min}-{args.zoom_max} = {n} tiles, {total_bytes/1e9:.1f} GB")
        for z in sorted(counts):
            print(f"  z{z}: {counts[z]} tiles")
        return 0

    if args.out is None and args.mbtiles is None:
        print("gsi-collect: one of --out (zxy dir) or --mbtiles is required")
        return 2

    def triplets():
        for e in iter_mokuroku_lines(read_mokuroku_gz(gz), zoom_min=args.zoom_min, zoom_max=args.zoom_max):
            if quad is not None and quadrans_of_tile(e.z, e.x, e.y) != quad:
                continue
            yield (e.z, e.x, e.y)

    start = time.monotonic()

    def on_progress(stats) -> None:
        el = time.monotonic() - start
        done = stats.ok + stats.cached + stats.http_404 + stats.error + stats.failed
        rate = done / el if el > 0 else 0
        print(
            f"gsi-collect {layer} z{args.zoom_min}-{args.zoom_max}: "
            f"ok={stats.ok} cached={stats.cached} 404={stats.http_404} "
            f"blocked={stats.blocked} err={stats.error} failed={stats.failed} "
            f"{stats.downloaded_bytes/1e9:.1f}GB {rate:.0f} tiles/s {el/60:.1f}m",
            flush=True,
        )

    if args.mbtiles is not None:
        from planetarble.tiling.mbtiles import download_xyz_to_mbtiles

        meta = {}
        if args.name:
            meta["name"] = args.name
        if args.attribution:
            meta["attribution"] = args.attribution
        dest = args.mbtiles.resolve()
        stats = download_xyz_to_mbtiles(
            triplets(), mbtiles_path=dest, template=template, ext=args.ext,
            tile_format=args.ext, workers=args.workers, metadata=meta or None,
            on_progress=on_progress, report_every=30.0,
        )
    else:
        dest = args.out.resolve()
        stats = download_xyz_tiles(
            triplets(), out_dir=dest, template=template, ext=args.ext,
            workers=args.workers, on_progress=on_progress, report_every=30.0,
        )
    el = time.monotonic() - start
    print(
        f"gsi-collect done: {layer} z{args.zoom_min}-{args.zoom_max} -> {dest} "
        f"ok={stats.ok} cached={stats.cached} 404={stats.http_404} blocked={stats.blocked} "
        f"err={stats.error} failed={stats.failed} {stats.downloaded_bytes/1e9:.1f}GB in {el/60:.1f}m"
    )
    return 1 if (stats.failed + stats.error) > 0 else 0


def _handle_gsi_pack(args: argparse.Namespace) -> int:
    import time

    from planetarble.tiling.mbtiles import ingest_xyz_dir

    metadata = {}
    if args.name:
        metadata["name"] = args.name
    if args.attribution:
        metadata["attribution"] = args.attribution
    if args.bounds:
        metadata["bounds"] = args.bounds

    start = time.monotonic()

    def on_progress(n: int) -> None:
        el = time.monotonic() - start
        rate = n / el if el > 0 else 0
        print(f"gsi-pack: {n} tiles packed, {rate:.0f} tiles/s {el/60:.1f}m", flush=True)

    n = ingest_xyz_dir(
        args.tiles.resolve(), args.out.resolve(),
        tile_format=args.format, batch_size=args.batch_size,
        metadata=metadata or None, on_progress=on_progress,
    )
    el = time.monotonic() - start
    print(f"gsi-pack done: {n} tiles -> {args.out} in {el/60:.1f}m")
    return 0


def _handle_gsi_fetch(args: argparse.Namespace) -> int:
    try:
        summary = fetch_gsi_ortho_clip(
            lat=args.lat,
            lon=args.lon,
            width_m=args.width_m,
            height_m=args.height_m,
            zoom=args.zoom,
            tile_template=args.tile_template,
            gdal_translate=args.gdal_translate,
            gdal_buildvrt=args.gdal_buildvrt,
            gdal_warp=args.gdal_warp,
            output_path=args.output,
            dry_run=args.dry_run,
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except GSIError as exc:
        LOGGER.error("GSI fetch failed: %s", exc)
        return 1

    LOGGER.info("gsi fetch complete", extra=summary)
    if args.dry_run:
        print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
