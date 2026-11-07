"""The tiling subcommands: pmtiles, merge-mbtiles, union-mbtiles, stitch-512."""

from __future__ import annotations

import argparse
from pathlib import Path

from planetarble.core.models import ProcessingConfig
from planetarble.logging import get_logger
from planetarble.tiling import PmtilesTilingManager
from planetarble.tiling.mbtiles import merge_mbtiles, union_mbtiles

LOGGER = get_logger(__name__)


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


def register_tiling(subcommands: argparse._SubParsersAction) -> None:
    tiling = subcommands.add_parser("tiling", help="Advanced tiling utilities")
    tiling_subcommands = tiling.add_subparsers(dest="tiling_command", required=True)

    tiling_pmtiles = tiling_subcommands.add_parser(
        "pmtiles",
        help="Convert a raster into PMTiles via XYZ and MBTiles",
    )
    tiling_pmtiles.set_defaults(handler=_handle_tiling_pmtiles)
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
    tiling_merge.set_defaults(handler=_handle_merge_mbtiles)
    tiling_merge.add_argument("--base", type=Path, required=True, help="Base MBTiles archive")
    tiling_merge.add_argument("--overlay", type=Path, required=True, help="Overlay MBTiles archive")
    tiling_merge.add_argument("--out", type=Path, required=True, help="Output MBTiles archive")

    tiling_union = tiling_subcommands.add_parser(
        "union-mbtiles",
        help="Union several MBTiles into one in a single pass (for disjoint Quadrans pieces)",
    )
    tiling_union.set_defaults(handler=_handle_union_mbtiles)
    tiling_union.add_argument("--inputs", type=Path, nargs="+", required=True,
                              help="Input MBTiles archives (pass the LARGEST first — it becomes the copy base)")
    tiling_union.add_argument("--out", type=Path, required=True, help="Output MBTiles archive")
    tiling_union.add_argument("--chunk-size", type=int, default=50_000,
                              help="Rows per commit when appending non-base inputs (bounds journal growth)")

    tiling_stitch = tiling_subcommands.add_parser(
        "stitch-512",
        help="Build a 512px pyramid from a 256px source (output zoom z <- source zoom z+1)",
    )
    tiling_stitch.set_defaults(handler=_handle_stitch_512)
    tiling_stitch.add_argument("--source", type=Path, required=True, help="256px source MBTiles")
    tiling_stitch.add_argument("--out", type=Path, required=True, help="Output 512px MBTiles")
    tiling_stitch.add_argument("--format", default="jpg", help="Output tile format (default jpg)")
    tiling_stitch.add_argument("--quality", type=int, default=90, help="JPEG/WebP quality (default 90)")
    tiling_stitch.add_argument("--workers", type=int, default=1,
                               help="Parallel processes (CPU-bound; shards are unioned at the end)")
    tiling_stitch.add_argument("--shard-dir", type=Path, default=None,
                               help="Dir for worker shards (default: next to --out; put on a separate disk to split shards/output)")
