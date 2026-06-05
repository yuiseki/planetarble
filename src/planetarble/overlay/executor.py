"""Concrete PlanetExecutor wiring the overlay build to existing components.

Reproduces the manually proven Atami flow: reuse a cached global base (re-encoded
to webp), build each overlay's COG (OpenAerialMap via its adapter, HLS via the
existing planner/manager), tile to MBTiles, overzoom-composite the stack over the
overlay footprint, merge onto the running planet, and package to PMTiles.

GDAL/tiling/network bound: verified by running ``planetarble build`` on a host
with the toolchain, not in unit tests.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from planetarble.core.models import HLSPlanRegion, ProcessingConfig
from planetarble.tiling.mbtiles import composite_overzoom, merge_mbtiles
from planetarble.tiling.pmtiles import PmtilesTilingManager

from .spec import BaseSpec, Overlay, PipelineSpec


class DefaultPlanetExecutor:
    def __init__(
        self,
        spec: PipelineSpec,
        cfg,
        *,
        data_dir: Path,
        work_dir: Path,
        base_mbtiles: Path,
        tile_size: int = 512,
        tile_format: str = "webp",
        quality: int = 85,
    ) -> None:
        self._spec = spec
        self._cfg = cfg
        self._data_dir = Path(data_dir)
        self._work = Path(work_dir)
        self._work.mkdir(parents=True, exist_ok=True)
        self._base_src = Path(base_mbtiles)
        self._tile_size = tile_size
        self._tile_format = tile_format
        self._quality = quality

    # --- base -------------------------------------------------------------
    def build_base(self, base: BaseSpec) -> Path:
        out = self._work / "base_webp.mbtiles"
        if out.exists():
            return out
        # re-encode the cached global base to the target format (webp) so the
        # whole planet is one tile type
        composite_overzoom(
            [self._base_src], out,
            aoi_bbox=(-180.0, -85.0511, 180.0, 85.0511),
            min_zoom=0, max_zoom=base.max_zoom,
            tile_format=self._tile_format, quality=self._quality, tile_size=self._tile_size,
        )
        return out

    # --- overlays ---------------------------------------------------------
    def build_overlay_source(self, overlay: Overlay, resolved) -> Path:
        cog = self._build_overlay_cog(overlay, resolved)
        return self._tile_to_mbtiles(cog, overlay)

    def _build_overlay_cog(self, overlay: Overlay, resolved) -> Path:
        if overlay.source == "openaerialmap":
            return self._build_oam_cog(overlay, resolved)
        if overlay.source == "hls":
            return self._build_hls_cog(overlay, resolved)
        if overlay.source == "sentinel2":
            return self._build_sentinel2_cog(overlay, resolved)
        raise NotImplementedError(f"overlay source not wired in executor: {overlay.source}")

    def _build_oam_cog(self, overlay: Overlay, resolved) -> Path:
        from .adapters import OpenAerialMapAdapter

        opts = dict(overlay.source_options or {})
        opts.pop("asset", None)
        adapter = OpenAerialMapAdapter(**opts)
        plan = adapter.plan(resolved, (overlay.min_zoom, overlay.max_zoom))
        cog = self._work / f"{overlay.name}_oam_cog.tif"
        adapter.build_raster(plan, cog, cache_dir=self._data_dir / "cache" / "oam", aoi_bbox=resolved.bbox)
        return cog

    def _build_hls_cog(self, overlay: Overlay, resolved) -> Path:
        from planetarble.acquisition.hls import (
            HLSMosaicPlanner,
            _bbox_to_geometry,
            load_land_geometry,
        )
        from planetarble.processing.manager import ProcessingManager

        region = HLSPlanRegion(
            name=overlay.name,
            bbox=tuple(resolved.bbox),
            land_only=bool(getattr(overlay.aoi, "land_only", False)),
        )
        hls_cfg = replace(self._cfg.hls, plan_regions=(region,))

        plan_dir = self._data_dir / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plan_dir / f"hls_z{hls_cfg.target_zoom}_plan_{overlay.name}.ndjson"
        planner = HLSMosaicPlanner(hls_cfg)
        region_geom = resolved.geometry or _bbox_to_geometry(resolved.bbox)
        land_geom = None
        if region.land_only:
            land_geom = load_land_geometry(
                land_mask_path=hls_cfg.land_mask_path, data_dir=self._data_dir, region_geometry=region_geom
            )
        planner.write_plan(plan_path, region_geometry=region_geom, land_geometry=land_geom)

        pmgr = ProcessingManager(
            self._cfg.processing,
            temp_dir=self._work / "tmp",
            output_dir=self._cfg.output_dir,
            data_dir=self._data_dir,
            hls=hls_cfg,
            ocean=replace(self._cfg.ocean, enabled=False),
        )
        manifest_path = (self._cfg.output_dir / "processing" / f"hls_scene_manifest_{overlay.name}.json").resolve()
        manifest = pmgr.prepare_hls_scene_manifest(plan_path, destination=manifest_path)
        cog = pmgr.build_hls_mosaic(manifest, plan_region=overlay.name)
        if cog is None:
            raise RuntimeError(f"HLS mosaic produced no output for {overlay.name}")
        return Path(cog)

    def _sentinel2_manager_and_manifest(self, overlay: Overlay, resolved):
        """Build the overlay's Sentinel-2 config + manager and write its scene
        manifest (the shared front half of build and prefetch). Returns
        ``(ProcessingManager, manifest_path)``."""
        import dataclasses

        from planetarble.core.models import Sentinel2Config
        from planetarble.processing.manager import ProcessingManager

        region = HLSPlanRegion(
            name=overlay.name,
            bbox=tuple(resolved.bbox),
            land_only=bool(getattr(overlay.aoi, "land_only", False)),
        )
        # start from the configured Sentinel-2 block (or defaults) and let the
        # overlay's source_options override known fields (e.g. assets: [visual],
        # max_cloud, date window, mosaic_max_scenes) so the spec is self-contained.
        base_cfg = getattr(self._cfg, "sentinel2", None) or Sentinel2Config()
        fields = {f.name for f in dataclasses.fields(Sentinel2Config)}
        overrides = {}
        for key, value in (overlay.source_options or {}).items():
            if key not in fields:
                continue
            overrides[key] = tuple(value) if key == "assets" and value is not None else value
        s2_cfg = dataclasses.replace(
            base_cfg,
            enabled=True,
            bbox=tuple(resolved.bbox),
            plan_region=overlay.name,
            plan_regions=(region,),
            **overrides,
        )

        pmgr = ProcessingManager(
            self._cfg.processing,
            temp_dir=self._work / "tmp",
            output_dir=self._cfg.output_dir,
            data_dir=self._data_dir,
            hls=self._cfg.hls,
            sentinel2=s2_cfg,
            ocean=replace(self._cfg.ocean, enabled=False),
        )
        manifest_path = (
            self._cfg.output_dir / "processing" / f"sentinel2_scene_manifest_{overlay.name}.json"
        ).resolve()
        pmgr.prepare_sentinel2_scene_manifest(destination=manifest_path, plan_region=overlay.name)
        return pmgr, manifest_path

    def _build_sentinel2_cog(self, overlay: Overlay, resolved) -> Path:
        pmgr, manifest_path = self._sentinel2_manager_and_manifest(overlay, resolved)
        cog = pmgr.build_sentinel2_mosaic(manifest_path, plan_region=overlay.name)
        if cog is None:
            raise RuntimeError(f"Sentinel-2 mosaic produced no output for {overlay.name}")
        return Path(cog)

    def prefetch_overlay(self, overlay: Overlay):
        """Download-only: warm the cache for this overlay's Sentinel-2 assets
        (no mosaic/tiling). Returns a PrefetchStats."""
        from planetarble.overlay.resolve import resolve_aoi
        from planetarble.prefetch import PrefetchStats

        if overlay.source != "sentinel2":
            return PrefetchStats(overlay=overlay.name)
        resolved = resolve_aoi(
            overlay.aoi,
            data_dir=self._data_dir,
            land_mask_path=getattr(self._cfg.hls, "land_mask_path", None),
        )
        pmgr, manifest_path = self._sentinel2_manager_and_manifest(overlay, resolved)
        return pmgr.prefetch_sentinel2_assets(manifest_path, plan_region=overlay.name)

    def _tile_to_mbtiles(self, cog: Path, overlay: Overlay) -> Path:
        import subprocess

        # add an alpha band so nodata is transparent when tiled
        alpha = self._work / f"{overlay.name}_alpha.tif"
        subprocess.run(
            ["gdalwarp", "-overwrite", "-dstalpha", "-of", "COG", "-co", "COMPRESS=WEBP", str(cog), str(alpha)],
            check=True,
        )
        out_dir = self._work / f"tile_{overlay.name}"
        manager = PmtilesTilingManager(
            ProcessingConfig(tile_format=self._tile_format.upper(), tile_quality=self._quality),
            temp_dir=out_dir / "tmp",
            output_dir=out_dir,
        )
        min_zoom = overlay.min_zoom or 0
        fmt = self._tile_format.upper()
        zxy = manager.build_zxy(
            alpha, min_zoom=min_zoom, max_zoom=overlay.max_zoom,
            tile_format=fmt, quality=self._quality, resampling="cubic",
        )
        return manager.pack_mbtiles(
            zxy, source_path=alpha, tile_format=fmt, min_zoom=min_zoom, max_zoom=overlay.max_zoom,
            name=overlay.name, attribution="",
        )

    # --- compose ----------------------------------------------------------
    def stack(self, sources: List[Path], aoi_bbox, min_zoom: int, max_zoom: int) -> Path:
        dest = self._work / f"stack_{min_zoom}_{max_zoom}_{abs(hash(tuple(map(str, sources)))) % 100000}.mbtiles"
        return composite_overzoom(
            [Path(s) for s in sources], dest,
            aoi_bbox=tuple(aoi_bbox), min_zoom=min_zoom, max_zoom=max_zoom,
            tile_format=self._tile_format, quality=self._quality, tile_size=self._tile_size,
        )

    def merge(self, base_mbtiles: Path, overlay_mbtiles: Path) -> Path:
        dest = self._work / f"merged_{Path(overlay_mbtiles).stem}.mbtiles"
        return merge_mbtiles(Path(base_mbtiles), Path(overlay_mbtiles), destination=dest)

    def package(self, mbtiles: Path, output_name: str) -> Path:
        import subprocess

        out = (self._cfg.output_dir / "distribution" / f"{output_name}.pmtiles").resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            out.unlink()
        subprocess.run(["pmtiles", "convert", str(mbtiles), str(out)], check=True)
        return out
