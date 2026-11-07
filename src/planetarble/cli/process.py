"""The process subcommand: raster preprocessing per tile source."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Optional

from planetarble.acquisition import GSIError, fetch_gsi_ortho_clip
from planetarble.config import PipelineConfig, load_config
from planetarble.logging import get_logger, log_skip
from planetarble.processing import ProcessingManager
from planetarble.processing.assets import _is_valid_raster

from .checks import (
    _is_valid_hls_scene_manifest,
    _is_valid_sentinel2_scene_manifest,
    _resolve_config_path,
    _resolve_gsi_tile_template,
    _resolve_hls_plan_path,
    _resolve_hls_scene_manifest_path,
    _resolve_sentinel2_scene_manifest_path,
)

LOGGER = get_logger(__name__)


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
        gsi_summary, gsi_output = _fetch_gsi_clip(cfg, dry_run=args.dry_run)
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
        gsi_summary, gsi_output = _fetch_gsi_clip(cfg, dry_run=args.dry_run)
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


def _fetch_gsi_clip(cfg: PipelineConfig, *, dry_run: bool) -> tuple[dict | None, Path]:
    """Fetch the configured GSI orthophoto clip; return (summary, output path)."""
    gsi = cfg.gsi_orthophotos
    gsi_output = (cfg.output_dir / "processing" / f"{gsi.output_basename}.tif").resolve()
    product_slug = "seamlessphoto" if gsi.product == "seamlessphoto" else "orthophoto"
    gsi_cache_root = cfg.data_dir / "cache" / f"gsi_{product_slug}"
    if not dry_run:
        gsi_cache_root.mkdir(parents=True, exist_ok=True)
    LOGGER.info("gsi tile cache", extra={"path": str(gsi_cache_root)})
    try:
        summary = fetch_gsi_ortho_clip(
            lat=gsi.lat,
            lon=gsi.lon,
            width_m=gsi.width_m,
            height_m=gsi.height_m,
            bbox=gsi.bbox,
            zoom=gsi.zoom,
            tile_template=_resolve_gsi_tile_template(gsi),
            cache_dir=gsi_cache_root,
            rate_limit_seconds=gsi.rate_limit_seconds,
            output_path=gsi_output,
            timeout=gsi.timeout_seconds,
            dry_run=dry_run,
        )
    except GSIError as exc:
        raise SystemExit(f"Failed to fetch GSI orthophotos: {exc}") from exc
    return summary, gsi_output


def register_process(subcommands: argparse._SubParsersAction) -> None:
    process = subcommands.add_parser("process", help="Run raster preprocessing pipeline")
    process.set_defaults(handler=_handle_process)
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
