"""CLI entry point for Planetarble."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

from planetarble.acquisition import (
    GSIError,
    fetch_gsi_ortho_clip,
)
from planetarble.config import load_config
from planetarble.logging import configure_logging, get_logger

from .acquire import (
    _handle_acquire,
    register_acquire,
)
from .checks import (
    _resolve_config_path,
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
