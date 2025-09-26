"""CLI entry point for Planetarble."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable

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
    verify_copernicus_connection,
)
from planetarble.config import load_config
from planetarble.core.models import CopernicusLayerConfig, TileMetadata
from planetarble.logging import configure_logging, get_logger
from planetarble.packaging import PackagingManager
from planetarble.processing import ProcessingManager
from planetarble.tiling import TilingManager

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

    tile = subcommands.add_parser("tile", help="Generate MBTiles output")
    tile.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to pipeline configuration file (YAML or JSON)",
    )
    tile.add_argument(
        "--dry-run",
        action="store_true",
        help="Print tiling commands without executing",
    )
    tile.add_argument(
        "--max-zoom",
        type=int,
        default=None,
        help="Override maximum zoom level",
    )
    tile.add_argument(
        "--tile-format",
        choices=["JPEG", "WEBP"],
        default=None,
        help="Override tile image format",
    )
    tile.add_argument(
        "--quality",
        type=int,
        default=None,
        help="Override tile encoding quality",
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
        help="Filename for the PMTiles archive (defaults to world_<year>.pmtiles)",
    )
    package.add_argument(
        "--dry-run",
        action="store_true",
        help="Print packaging commands without executing",
    )

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
    if args.command == "mpc-fetch":
        return _handle_mpc_fetch(args)
    if args.command == "gsi-fetch":
        return _handle_gsi_fetch(args)
    if args.command == "package":
        return _handle_package(args)
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
    try:
        manager.download_modis_mcd43a4(force=args.force)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:
        LOGGER.warning("modis acquisition skipped: %s", exc)
    try:
        manager.download_viirs_corrected_reflectance(
            force=args.force,
            product=cfg.processing.viirs_product,
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

    generation_params: dict[str, object] = {"bmng_resolution": args.bmng_resolution}
    if copernicus_summary:
        generation_params["copernicus_layers"] = copernicus_summary

    manager.generate_manifest(
        generation_params=generation_params,
    )
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
        dry_run=args.dry_run,
    )

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
    if cfg.processing.modis_enabled:
        if not cfg.processing.modis_doy:
            raise SystemExit("processing.modis_doy must be set when modis_enabled is true")
        modis_root = (cfg.data_dir / "modis_mcd43a4" / cfg.processing.modis_doy).resolve()
        if not modis_root.exists():
            raise SystemExit(f"MODIS directory not found: {modis_root}")
        tiles = cfg.processing.modis_tiles or tuple(sorted(p.name for p in modis_root.iterdir() if p.is_dir()))
        if not tiles:
            raise SystemExit(f"No MODIS tiles found under {modis_root}")
        modis_cog_path = manager.prepare_modis_rgb(
            modis_root,
            tiles=tiles,
            date_code=cfg.processing.modis_doy,
        )

    viirs_cog_path: Path | None = None
    if cfg.processing.viirs_enabled:
        if not cfg.processing.viirs_date:
            raise SystemExit("processing.viirs_date must be set when viirs_enabled is true")
        viirs_root = (cfg.data_dir / "viirs_vnp09ga" / cfg.processing.viirs_date).resolve()
        if not viirs_root.exists():
            raise SystemExit(f"VIIRS directory not found: {viirs_root}")
        tiles = cfg.processing.viirs_tiles or tuple(
            sorted(p.name for p in viirs_root.iterdir() if p.is_dir())
        )
        if not tiles:
            raise SystemExit(f"No VIIRS tiles found under {viirs_root}")
        viirs_cog_path = manager.prepare_viirs_rgb(
            viirs_root,
            tiles=tiles,
            date_code=cfg.processing.viirs_date,
        )

    copernicus_cogs: list[Path] = []
    if cfg.copernicus.enabled:
        try:
            copernicus_cogs = manager.prepare_copernicus_layers(force=args.dry_run)
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as exc:
            LOGGER.warning("copernicus processing skipped: %s", exc)

    LOGGER.info("processing outputs", extra={
        "bmng_mosaic": str(bmng_source),
        "normalized": str(normalized),
        "hillshade": str(hillshade),
        "masks": str(masks_dir),
        "cog": str(cog_path),
        "modis_cog": str(modis_cog_path) if modis_cog_path else None,
        "viirs_cog": str(viirs_cog_path) if viirs_cog_path else None,
        "copernicus_cogs": [str(path) for path in copernicus_cogs] if copernicus_cogs else None,
    })
    return 0


def _handle_tile(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    cfg = load_config(config_path)

    if args.max_zoom is not None:
        cfg.processing.max_zoom = args.max_zoom
    if args.tile_format is not None:
        cfg.processing.tile_format = args.tile_format
    if args.quality is not None:
        cfg.processing.tile_quality = args.quality

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

    tile_source = (cfg.processing.tile_source or cfg.processing.modis_tile_source or "bmng").lower()

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
    elif tile_source == "blend":
        raise SystemExit("tile_source=blend is not implemented yet")
    elif tile_source != "bmng":
        raise SystemExit(f"Unsupported tile_source value: {cfg.processing.tile_source}")

    reprojected = manager.reproject_to_webmercator(source_raster)
    mbtiles_path = manager.create_mbtiles(reprojected)

    LOGGER.info("tiling outputs", extra={
        "source": str(source_raster),
        "reprojected": str(reprojected),
        "mbtiles": str(mbtiles_path),
    })
    return 0


def _handle_package(args: argparse.Namespace) -> int:
    config_path = _resolve_config_path(args.config)
    cfg = load_config(config_path)

    tiling_dir = (cfg.output_dir / "tiling").resolve()
    if not tiling_dir.exists():
        raise SystemExit(f"Tiling directory not found: {tiling_dir}")
    mbtiles_candidates = sorted(tiling_dir.glob("*.mbtiles"))
    if not mbtiles_candidates:
        raise SystemExit("No MBTiles archive found; run the tile stage first")
    mbtiles_path = mbtiles_candidates[0]

    pmtiles_name = args.pmtiles_name or f"world_{cfg.processing.gebco_year}.pmtiles"
    pmtiles_destination = tiling_dir / pmtiles_name

    packaging = PackagingManager(dry_run=args.dry_run)
    pmtiles_path = packaging.convert_to_pmtiles(mbtiles_path, destination=pmtiles_destination)

    tile_source = (cfg.processing.tile_source or cfg.processing.modis_tile_source or "bmng").lower()

    if tile_source == "modis":
        imagery_label = f"MODIS MCD43A4 ({cfg.processing.modis_doy or 'unknown date'})"
        imagery_attribution = "Imagery: NASA MODIS MCD43A4 (LP DAAC)."
    elif tile_source == "viirs":
        product = cfg.processing.viirs_product or "VNP09GA"
        imagery_label = f"VIIRS Corrected Reflectance ({product} {cfg.processing.viirs_date or 'daily'})"
        imagery_attribution = "Imagery: NASA VIIRS Corrected Reflectance (LP DAAC)."
    elif tile_source == "copernicus":
        imagery_label = "Copernicus Sentinel-2 Level-2A"
        imagery_attribution = "Imagery: Copernicus Sentinel-2 (European Space Agency)."
    else:
        imagery_label = "NASA Blue Marble Next Generation (2004)"
        imagery_attribution = "Imagery: NASA Blue Marble (2004)."

    metadata = TileMetadata(
        name=f"Planetarble {cfg.processing.gebco_year}",
        description=f"Global basemap composed from {imagery_label} and GEBCO bathymetry.",
        version=str(cfg.processing.gebco_year),
        bounds=(-180.0, -85.0511, 180.0, 85.0511),
        center=(0.0, 0.0, 2),
        minzoom=0,
        maxzoom=cfg.processing.max_zoom,
        attribution=(
            f"{imagery_attribution} Bathymetry: GEBCO 2024. Masks: Natural Earth 10m."
        ),
        format=cfg.processing.tile_format,
    )
    tilejson_path = packaging.generate_tilejson(pmtiles_path, metadata)

    manifest_path = (cfg.output_dir / "MANIFEST.json").resolve()
    imagery_line = {
        "modis": "- MODIS MCD43A4 BRDF-Corrected Reflectance (NASA LP DAAC).",
        "viirs": "- VIIRS Corrected Reflectance (VNP09GA, NASA LP DAAC).",
        "copernicus": "- Copernicus Sentinel-2 Level-2A (European Space Agency).",
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
