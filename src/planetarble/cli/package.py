"""The package subcommand: PMTiles, TileJSON and the distribution bundle."""

from __future__ import annotations

import argparse
from pathlib import Path

from planetarble.config import load_config
from planetarble.core.models import TileMetadata
from planetarble.logging import get_logger, log_skip
from planetarble.packaging import PackagingManager
from planetarble.tiling.mbtiles import default_center

from .checks import (
    _is_valid_pmtiles,
    _is_valid_tilejson,
    _read_mbtiles_metadata,
    _resolve_config_path,
)

LOGGER = get_logger(__name__)


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
    elif tile_source == "hls":
        imagery_label = "Harmonized Landsat and Sentinel-2 (HLS) v2"
        imagery_attribution = "Imagery: HLS v2.0 (NASA/USGS)."
        if "landsat-c2-l2" in cfg.hls.fallback_collections:
            # the mosaic fills cloud gaps from the Landsat fallback collection
            imagery_attribution += " Landsat imagery courtesy of the U.S. Geological Survey."
    else:
        imagery_label = "NASA Blue Marble Next Generation (2004)"
        imagery_attribution = "Imagery: NASA Blue Marble (2004)."

    mbtiles_meta = _read_mbtiles_metadata(mbtiles_path)
    bounds = tuple(mbtiles_meta.get("bounds") or (-180.0, -85.0511, 180.0, 85.0511))
    minzoom = int(mbtiles_meta.get("minzoom") or 0)
    maxzoom = int(mbtiles_meta.get("maxzoom") or cfg.processing.max_zoom)
    if mbtiles_meta.get("center"):
        center = tuple(mbtiles_meta["center"])
    else:
        # MBTiles from before the tile stage wrote a center: open on the middle
        # of the bounds, at a zoom that has tiles (z2 for a global build).
        center = default_center(bounds, minzoom, maxzoom)
    tile_format = str(mbtiles_meta.get("format") or cfg.processing.tile_format)

    # Only the BMNG pipeline composites GEBCO bathymetry and Natural Earth
    # masks into the tiled raster; every other source is tiled on its own.
    bmng_composite = tile_source not in {"modis", "viirs", "copernicus", "sentinel2", "gsi_orthophotos", "hls"}
    if bmng_composite:
        description = f"Global basemap composed from {imagery_label} and GEBCO bathymetry."
        attribution = f"{imagery_attribution} Bathymetry: GEBCO 2024. Masks: Natural Earth 10m."
    else:
        description = f"Basemap composed from {imagery_label}."
        attribution = imagery_attribution

    packaging.set_header_center(pmtiles_path, center)

    metadata = TileMetadata(
        name=f"Planetarble {cfg.processing.gebco_year}",
        description=description,
        version=str(cfg.processing.gebco_year),
        bounds=bounds,
        center=center,
        minzoom=minzoom,
        maxzoom=maxzoom,
        attribution=attribution,
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
        "hls": "- NASA/USGS Harmonized Landsat and Sentinel-2 (HLS) v2.0 via Microsoft Planetary Computer.",
    }.get(tile_source, "- NASA Blue Marble Next Generation (2004).")
    if tile_source == "hls" and "landsat-c2-l2" in cfg.hls.fallback_collections:
        imagery_line += "\n- USGS Landsat Collection 2 Level-2 surface reflectance (cloud-gap fallback)."

    if bmng_composite:
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
    else:
        license_text = (
            "Planetarble Distribution\n\n"
            "Data Sources:\n"
            f"{imagery_line}\n\n"
            "Attribution:\n"
            f"{imagery_attribution}"
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


def register_package(subcommands: argparse._SubParsersAction) -> None:
    package = subcommands.add_parser("package", help="Create PMTiles distribution")
    package.set_defaults(handler=_handle_package)
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
