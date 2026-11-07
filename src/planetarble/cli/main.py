"""CLI entry point for Planetarble."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

from planetarble.acquisition import (
    CopernicusCredentialsMissing,
    GSIError,
    MPCError,
    fetch_gsi_ortho_clip,
    fetch_true_color_tile,
    get_available_layers,
    split_plan_by_miniplanet,
)
from planetarble.config import load_config
from planetarble.core.models import ProcessingConfig, TileMetadata
from planetarble.logging import configure_logging, get_logger, log_skip
from planetarble.packaging import PackagingManager
from planetarble.tiling import PmtilesTilingManager, TilingManager
from planetarble.tiling.mbtiles import merge_mbtiles, union_mbtiles

from .acquire import (
    _handle_acquire,
    register_acquire,
)
from .checks import (
    _is_valid_mbtiles,
    _is_valid_pmtiles,
    _is_valid_tilejson,
    _read_mbtiles_metadata,
    _resolve_config_path,
    _resolve_copernicus_cog,
    _resolve_hls_plan_path,
)
from .process import (
    _handle_process,
    register_process,
)
from .serve import (
    _handle_serve,
    register_serve,
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
    tiling_stitch.add_argument("--shard-dir", type=Path, default=None,
                               help="Dir for worker shards (default: next to --out; put on a separate disk to split shards/output)")

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

    register_serve(subcommands)

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
    from planetarble.prefetch import (
        PrefetchPacing,
        prefetch_planet,
        prefetch_wait_seconds,
    )

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
                  workers=args.workers, shard_dir=args.shard_dir, on_progress=_progress)
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


def _handle_copernicus_layers(args: argparse.Namespace) -> int:
    from planetarble.acquisition import CopernicusAccessError, CopernicusAuthError

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
        fetch_mokuroku,
        iter_mokuroku_lines,
        mokuroku_url,
        read_mokuroku_gz,
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
