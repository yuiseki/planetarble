"""Concrete data processing utilities leveraging GDAL commands."""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from planetarble.acquisition.hls import (
    _geom_intersects_bbox,
    load_land_geometry,
    load_region_geometry,
)
from planetarble.acquisition.mpc import append_sas_token, fetch_sas_token
from planetarble.acquisition.sentinel_2 import Sentinel2SceneManifestBuilder
from planetarble.core.models import (
    CopernicusConfig,
    CopernicusLayerConfig,
    HLSConfig,
    HLSPlanRegion,
    ModisConfig,
    OceanConfig,
    ProcessingConfig,
    Sentinel2Config,
    ViirsConfig,
)
from planetarble.logging import get_logger, log_skip

from .assets import (
    _cache_hls_assets,
    _cache_sentinel2_asset,
    _cache_sentinel2_assets,
    _is_valid_raster,
    _strip_query,
)
from .base import DataProcessor
from .copernicus import (
    _collect_copernicus_tiles,
    _compression_for_format,
    _slugify,
)
from .geometry import (
    _tile_bounds,
    bbox_from_geometry,
    clip_land_to_region,
)
from .hls import HLSSceneManifestBuilder
from .hls_mosaic import (
    _build_hls_band_vrts,
    _build_hls_median_rgb,
    _build_hls_rgb_vrt,
    _load_hls_scene_manifest,
    _mask_hls_scene_bands,
    _qa_mask_value,
    _refresh_hls_scene_urls,
    _translate_hls_rgb,
    _use_median_strategy,
    _write_hls_band_lists,
)
from .ocean import OceanRenderer
from .runner import (
    CommandRunner,
    _cog_command,
)
from .sentinel2_mosaic import (
    _build_sentinel2_band_vrts,
    _build_sentinel2_rgb_vrt,
    _build_sentinel2_visual_vrt,
    _load_sentinel2_scene_manifest,
    _refresh_sentinel2_scene_urls,
    _select_mosaic_scenes,
    _select_sentinel2_asset_mode,
    _sentinel2_mosaic_filename,
    _translate_sentinel2_rgb,
    _write_sentinel2_band_lists,
    _write_sentinel2_visual_list,
)

LOGGER = get_logger(__name__)

class ProcessingManager(DataProcessor):
    """Implement data processing steps using GDAL tooling."""

    def __init__(
        self,
        config: ProcessingConfig,
        *,
        temp_dir: Path,
        output_dir: Path,
        data_dir: Path,
        copernicus: Optional[CopernicusConfig] = None,
        sentinel2: Optional[Sentinel2Config] = None,
        modis: Optional[ModisConfig] = None,
        viirs: Optional[ViirsConfig] = None,
        hls: Optional[HLSConfig] = None,
        ocean: Optional[OceanConfig] = None,
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._modis = modis or ModisConfig()
        self._viirs = viirs or ViirsConfig()
        self._temp_dir = temp_dir
        self._output_dir = output_dir
        self._data_dir = data_dir
        self._processing_dir = self._output_dir / "processing"
        self._copernicus = copernicus
        self._sentinel2 = sentinel2 or Sentinel2Config(enabled=False)
        self._hls = hls or HLSConfig(enabled=False)
        self._ocean = ocean or OceanConfig(enabled=False)
        self._runner = CommandRunner(dry_run=dry_run)
        self._dry_run = dry_run
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._processing_dir.mkdir(parents=True, exist_ok=True)

    def prepare_hls_scene_manifest(
        self,
        plan_path: Path,
        *,
        destination: Optional[Path] = None,
        max_tiles: Optional[int] = None,
        max_scenes_per_tile: Optional[int] = None,
    ) -> Optional[Path]:
        if max_scenes_per_tile is None:
            max_scenes_per_tile = self._hls.scenes_per_tile
        if not self._hls.enabled:
            LOGGER.info("HLS processing disabled; skipping scene manifest generation")
            return None
        dest = destination or (self._processing_dir / "hls_scene_manifest.json")
        if self._dry_run:
            LOGGER.info(
                "dry-run: would build HLS scene manifest",
                extra={"plan_path": str(plan_path), "destination": str(dest)},
            )
            return dest
        builder = HLSSceneManifestBuilder(
            self._hls,
            cache_dir=self._data_dir / "cache" / "hls",
            cache_ttl_days=self._hls.cache_ttl_days,
        )
        manifest = builder.build(
            plan_path,
            max_tiles=max_tiles,
            max_scenes_per_tile=max_scenes_per_tile,
            search_limit=self._hls.scene_search_limit,
            progress_interval=100,
        )
        manifest.write(dest)
        return dest

    def build_hls_mosaic(
        self,
        scene_manifest_path: Path,
        *,
        plan_region: Optional[str] = None,
        destination: Optional[Path] = None,
    ) -> Optional[Path]:
        if not self._hls.enabled:
            LOGGER.info("HLS processing disabled; skipping mosaic generation")
            return None
        region = self._resolve_hls_region(plan_region)
        region_geometry = None
        if region is not None:
            region_geometry = load_region_geometry(region, data_dir=self._data_dir)
        scenes = _load_hls_scene_manifest(scene_manifest_path)
        if region_geometry is not None:
            filtered: List[Dict[str, object]] = []
            for scene in scenes:
                bbox = scene.get("bbox")
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    continue
                if _geom_intersects_bbox(region_geometry, tuple(float(v) for v in bbox)):
                    filtered.append(scene)
            scenes = filtered
        scenes = _refresh_hls_scene_urls(scenes, self._hls)
        scenes = _cache_hls_assets(
            scenes,
            cache_dir=self._data_dir / "cache" / "hls" / "assets",
            timeout=self._hls.request_timeout_seconds,
        )
        if not scenes:
            LOGGER.warning("no HLS scenes available for mosaic", extra={"path": str(scene_manifest_path)})
            return None
        mask_value = _qa_mask_value(self._hls.qa_mask_flags)
        if mask_value > 0:
            masked_count = sum(1 for s in scenes if s.get("qa_local"))
            LOGGER.info(
                "masking HLS clouds via Fmask",
                extra={"mask_value": mask_value, "scenes_with_qa": masked_count, "total": len(scenes)},
            )
            scenes = _mask_hls_scene_bands(
                self._runner, scenes, temp_dir=self._temp_dir, mask_value=mask_value,
                dilation=self._hls.cloud_mask_dilation,
            )
        output_name = f"hls_mosaic_{region.name}" if region else "hls_mosaic"
        output_base = destination or (self._processing_dir / f"{output_name}.tif")
        if self._dry_run:
            LOGGER.info(
                "dry-run: would build HLS mosaic",
                extra={"scene_manifest": str(scene_manifest_path), "destination": str(output_base)},
            )
            return output_base
        band_lists = _write_hls_band_lists(self._temp_dir, scenes)
        if _use_median_strategy(self._hls.mosaic_strategy):
            LOGGER.info(
                "compositing HLS via temporal median",
                extra={"strategy": self._hls.mosaic_strategy, "scenes": len(scenes)},
            )
            rgb_source = _build_hls_median_rgb(band_lists, self._temp_dir)
        else:
            band_vrts = _build_hls_band_vrts(self._runner, band_lists, self._temp_dir)
            rgb_source = _build_hls_rgb_vrt(self._runner, band_vrts, self._temp_dir)
        cropped = _translate_hls_rgb(
            self._runner,
            rgb_source,
            output_base,
            region_geometry=region_geometry,
            scale_max=self._hls.display_scale_max,
            gamma=self._hls.display_gamma,
        )
        return self.create_cog(cropped)

    def prepare_sentinel2_scene_manifest(
        self,
        *,
        destination: Optional[Path] = None,
        max_items: Optional[int] = None,
        force_refresh: bool = False,
        plan_region: Optional[str] = None,
    ) -> Optional[Path]:
        if not self._sentinel2.enabled:
            LOGGER.info("Sentinel-2 processing disabled; skipping scene manifest generation")
            return None
        dest = destination or (self._processing_dir / "sentinel2_scene_manifest.json")
        if self._dry_run:
            LOGGER.info(
                "dry-run: would build Sentinel-2 scene manifest",
                extra={"destination": str(dest)},
            )
            return dest
        region = self._resolve_sentinel2_region(plan_region)
        region_geometry = None
        region_bbox = self._sentinel2.bbox
        if region is not None:
            if region.bbox:
                region_bbox = region.bbox
                region_geometry = load_region_geometry(region, data_dir=self._data_dir) if region.land_only else None
            elif region.natural_earth:
                region_geometry = load_region_geometry(region, data_dir=self._data_dir)
                if region_geometry is not None:
                    region_bbox = bbox_from_geometry(region_geometry)
            if region.land_only:
                land_geometry = load_land_geometry(
                    land_mask_path=self._hls.land_mask_path,
                    data_dir=self._data_dir,
                    region_geometry=region_geometry,
                )
                land_geometry = clip_land_to_region(land_geometry, region_geometry)
                region_geometry = land_geometry
                region_bbox = bbox_from_geometry(land_geometry)
        builder = Sentinel2SceneManifestBuilder(
            self._sentinel2,
            cache_dir=self._data_dir / "cache" / "sentinel2",
            cache_ttl_days=self._sentinel2.cache_ttl_days,
        )
        manifest = builder.build(
            bbox=region_bbox,
            max_items=max_items,
            force_refresh=force_refresh,
        )
        manifest.write(dest)
        return dest

    def prefetch_sentinel2_assets(self, scene_manifest_path: Path, *, plan_region: Optional[str] = None):
        """Download (cache) the mosaic scenes' assets for an AOI without tiling.

        Warms ``data/cache/sentinel2/assets`` so a later build is download-free.
        Selects the same ``mosaic_max_scenes`` lowest-cloud scenes the mosaic
        would use, so the cache matches. Returns a PrefetchStats with the bytes
        actually downloaded (cache hits contribute 0) for throttle-aware pacing.
        """
        from planetarble.prefetch import PrefetchStats

        label = plan_region or "sentinel2"
        if not self._sentinel2.enabled:
            return PrefetchStats(overlay=label)
        scenes = _load_sentinel2_scene_manifest(scene_manifest_path)
        if not scenes:
            return PrefetchStats(overlay=label)
        selected = _select_mosaic_scenes(scenes, self._sentinel2.mosaic_max_scenes)
        selected = _refresh_sentinel2_scene_urls(selected, self._sentinel2)
        cache_dir = self._data_dir / "cache" / "sentinel2" / "assets"
        cache_dir.mkdir(parents=True, exist_ok=True)
        timeout = self._sentinel2.request_timeout_seconds
        tokens: Dict[str, str] = {}
        dl_count = dl_bytes = hit_count = 0
        start = time.monotonic()
        for scene in selected:
            collection = scene.get("collection_id") or self._sentinel2.collection
            item_id = scene.get("item_id")
            assets = scene.get("assets")
            if not isinstance(collection, str) or not isinstance(item_id, str) or not isinstance(assets, dict):
                continue
            token = tokens.get(collection)
            if token is None:
                token = fetch_sas_token(collection, timeout=timeout)
                tokens[collection] = token
            for asset_name, href in assets.items():
                if not isinstance(href, str) or not href:
                    continue
                unsigned = _strip_query(href)
                local, status = _cache_sentinel2_asset(
                    append_sas_token(unsigned, token),
                    cache_dir=cache_dir / collection / item_id,
                    timeout=timeout, asset_name=str(asset_name),
                )
                if status == "failed":
                    token = fetch_sas_token(collection, timeout=timeout)
                    tokens[collection] = token
                    local, status = _cache_sentinel2_asset(
                        append_sas_token(unsigned, token),
                        cache_dir=cache_dir / collection / item_id,
                        timeout=timeout, asset_name=str(asset_name),
                    )
                if status in ("download", "redownload") and local is not None:
                    dl_count += 1
                    try:
                        dl_bytes += local.stat().st_size
                    except OSError:
                        pass
                elif status == "hit":
                    hit_count += 1
        elapsed = time.monotonic() - start
        LOGGER.info(
            "prefetched sentinel-2 assets",
            extra={"region": label, "downloaded": dl_count, "bytes": dl_bytes,
                   "hits": hit_count, "elapsed_s": round(elapsed, 1)},
        )
        return PrefetchStats(
            overlay=label, downloaded_count=dl_count, downloaded_bytes=dl_bytes,
            hit_count=hit_count, elapsed_seconds=elapsed,
        )

    def build_sentinel2_mosaic(
        self,
        scene_manifest_path: Path,
        *,
        destination: Optional[Path] = None,
        force: bool = False,
        plan_region: Optional[str] = None,
    ) -> Optional[Path]:
        if not self._sentinel2.enabled:
            LOGGER.info("Sentinel-2 processing disabled; skipping mosaic generation")
            return None
        region = self._resolve_sentinel2_region(plan_region)
        region_geometry = None
        region_bbox = self._sentinel2.bbox
        if region is not None:
            if region.bbox:
                region_bbox = region.bbox
                region_geometry = load_region_geometry(region, data_dir=self._data_dir) if region.land_only else None
            elif region.natural_earth:
                region_geometry = load_region_geometry(region, data_dir=self._data_dir)
                if region_geometry is not None:
                    region_bbox = bbox_from_geometry(region_geometry)
            if region.land_only:
                land_geometry = load_land_geometry(
                    land_mask_path=self._hls.land_mask_path,
                    data_dir=self._data_dir,
                    region_geometry=region_geometry,
                )
                land_geometry = clip_land_to_region(land_geometry, region_geometry)
                region_geometry = land_geometry
                region_bbox = bbox_from_geometry(land_geometry)
        scenes = _load_sentinel2_scene_manifest(scene_manifest_path)
        if not scenes:
            raise ValueError(f"No Sentinel-2 scenes found in {scene_manifest_path}")
        selected = _select_mosaic_scenes(scenes, self._sentinel2.mosaic_max_scenes)
        if len(selected) < len(scenes):
            LOGGER.info(
                "capped sentinel-2 mosaic scenes",
                extra={
                    "total": len(scenes),
                    "selected": len(selected),
                    "mosaic_max_scenes": self._sentinel2.mosaic_max_scenes,
                },
            )
        scenes = selected
        scenes = _refresh_sentinel2_scene_urls(scenes, self._sentinel2)
        scenes = _cache_sentinel2_assets(
            scenes,
            cache_dir=self._data_dir / "cache" / "sentinel2" / "assets",
            timeout=self._sentinel2.request_timeout_seconds,
            config=self._sentinel2,
        )
        if destination:
            output_path = destination
        else:
            output_name = _sentinel2_mosaic_filename(region.name if region else None, region_bbox)
            output_path = self._processing_dir / output_name
        if _is_valid_raster(output_path) and not self._dry_run and not force:
            log_skip(LOGGER, phase="process", reason="valid Sentinel-2 mosaic", path=str(output_path))
            return output_path
        if force and output_path.exists() and not self._dry_run:
            output_path.unlink()

        assets = tuple(self._sentinel2.assets or ())
        mode = _select_sentinel2_asset_mode(assets)
        if mode == "visual":
            list_path = _write_sentinel2_visual_list(self._temp_dir, scenes, assets[0])
            visual_vrt = _build_sentinel2_visual_vrt(self._runner, list_path, self._temp_dir)
            return _translate_sentinel2_rgb(
                self._runner,
                visual_vrt,
                output_path,
                bbox=region_bbox,
                region_geometry=region_geometry,
                scale_to_byte=False,
            )

        band_lists = _write_sentinel2_band_lists(self._temp_dir, scenes, assets)
        band_vrts = _build_sentinel2_band_vrts(self._runner, band_lists, self._temp_dir)
        rgb_vrt = _build_sentinel2_rgb_vrt(self._runner, band_vrts, self._temp_dir)
        return _translate_sentinel2_rgb(
            self._runner,
            rgb_vrt,
            output_path,
            bbox=region_bbox,
            region_geometry=region_geometry,
            scale_to_byte=True,
        )

    def render_ocean(self, etopo_path: Path) -> Dict[str, Path]:
        if not self._ocean.enabled:
            LOGGER.info("Ocean rendering disabled in configuration")
            return {}
        renderer = OceanRenderer(
            self._ocean,
            self._runner,
            temp_dir=self._temp_dir / "ocean",
            output_dir=self._processing_dir / "ocean",
        )
        if self._dry_run:
            LOGGER.info(
                "dry-run: would render ocean shading",
                extra={"etopo_path": str(etopo_path)},
            )
            ocean_dir = self._processing_dir / "ocean"
            return {
                "color": ocean_dir / "etopo_depth_color.tif",
                "hillshade": ocean_dir / "etopo_hillshade.tif",
            }
        return renderer.render(etopo_path)

    def compose_bmng_panels(self, panel_dir: Path) -> Path:
        """Build a single raster from BMNG panels if multiple files are present."""

        tif_files = sorted(panel_dir.glob("*.tif"))
        if not tif_files:
            raise FileNotFoundError(f"No TIFF panels found in {panel_dir}")
        if len(tif_files) == 1:
            LOGGER.info("single BMNG panel detected; skipping mosaic")
            return tif_files[0]

        panel_list = self._temp_dir / "bmng_panels.txt"
        panel_list.write_text("\n".join(str(path) for path in tif_files), encoding="utf-8")
        vrt_path = self._temp_dir / "bmng_panels.vrt"
        self._runner.run(
            [
                "gdalbuildvrt",
                "-input_file_list",
                str(panel_list),
                str(vrt_path),
            ],
            description="assemble BMNG panels into VRT",
        )

        mosaic_path = self._processing_dir / "bmng_mosaic.tif"
        self._runner.run(
            [
                "gdal_translate",
                str(vrt_path),
                str(mosaic_path),
                "-co",
                "TILED=YES",
                "-co",
                "COMPRESS=DEFLATE",
            ],
            description="convert BMNG VRT mosaic to GeoTIFF",
        )
        return mosaic_path

    def normalize_bmng(self, input_path: Path, *, source_files: Sequence[Path] | None = None) -> Path:
        output = self._processing_dir / f"{input_path.stem}_normalized.tif"
        meta_path = self._metadata_path_for_output(output)
        sources = self._format_sources(source_files, default_key="bmng_panel", fallback=input_path)
        if self._can_reuse_output(output, meta_path, sources):
            LOGGER.info(
                "reusing normalized BMNG raster",
                extra={"output": str(output)},
            )
            return output
        command = [
            "gdal_translate",
            "-of",
            "GTiff",
            "-a_srs",
            "EPSG:4326",
            "-co",
            "TILED=YES",
            "-co",
            "COMPRESS=DEFLATE",
            str(input_path),
            str(output),
        ]
        self._runner.run(command, description="normalize BMNG raster")
        self._record_source_hashes(meta_path, sources)
        return output

    def generate_hillshade(self, gebco_path: Path) -> Path:
        output = self._processing_dir / f"{gebco_path.stem}_hillshade.tif"
        meta_path = self._metadata_path_for_output(output)
        sources = {"gebco": gebco_path}
        if self._can_reuse_output(output, meta_path, sources):
            LOGGER.info(
                "reusing GEBCO hillshade",
                extra={"output": str(output)},
            )
            return output
        command = [
            "gdaldem",
            "hillshade",
            "-az",
            "315",
            "-alt",
            "45",
            "-compute_edges",
            str(gebco_path),
            str(output),
        ]
        self._runner.run(command, description="generate GEBCO hillshade")
        self._record_source_hashes(meta_path, sources)
        return output

    def create_masks(self, natural_earth_path: Path) -> Path:
        destination = self._temp_dir / "natural_earth"
        destination.mkdir(parents=True, exist_ok=True)
        meta_path = self._metadata_path_for_output(destination)
        sources = self._collect_natural_earth_sources(natural_earth_path)
        if self._can_reuse_output(destination, meta_path, sources):
            LOGGER.info(
                "reusing Natural Earth masks",
                extra={"destination": str(destination)},
            )
            return destination
        if natural_earth_path.is_file() and natural_earth_path.suffix == ".zip":
            self._extract_zip(natural_earth_path, destination)
        elif natural_earth_path.is_dir():
            for archive in natural_earth_path.glob("*.zip"):
                self._extract_zip(archive, destination / archive.stem)
        else:  # pragma: no cover - input guard
            raise ValueError(f"Unsupported Natural Earth input: {natural_earth_path}")
        self._record_source_hashes(meta_path, sources)
        return destination

    def create_cog(self, raster_path: Path) -> Path:
        output = self._processing_dir / f"{raster_path.stem}_cog.tif"
        self._runner.run(
            _cog_command(raster_path, output),
            description="create Cloud Optimized GeoTIFF",
        )
        return output

    def blend_layers(self, base: Path, overlay: Path, opacity: float) -> Path:
        opacity = max(0.0, min(opacity, 1.0))
        output = self._processing_dir / f"{base.stem}_blended.tif"
        command = [
            "gdal_calc.py",
            "-A",
            str(base),
            "-B",
            str(overlay),
            "--A_band=1",
            "--B_band=1",
            "--calc",
            f"A*(1-{opacity})+B*({opacity})",
            "--format",
            "GTiff",
            "--outfile",
            str(output),
        ]
        self._runner.run(command, description="blend base and overlay rasters")
        return output

    def _extract_zip(self, archive: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        LOGGER.info(
            "extracting archive",
            extra={"archive": str(archive), "destination": str(destination)},
        )
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(destination)

    def _collect_natural_earth_sources(self, natural_earth_path: Path) -> Dict[str, Path]:
        if natural_earth_path.is_file():
            if natural_earth_path.suffix.lower() != ".zip":
                raise ValueError(f"Unsupported Natural Earth archive: {natural_earth_path}")
            return {natural_earth_path.name: natural_earth_path.resolve()}
        if natural_earth_path.is_dir():
            archives = sorted(natural_earth_path.glob("*.zip"))
            if not archives:
                raise FileNotFoundError(
                    f"No Natural Earth ZIP archives found under {natural_earth_path}"
                )
            return {archive.name: archive.resolve() for archive in archives}
        raise ValueError(f"Unsupported Natural Earth input: {natural_earth_path}")

    def _format_sources(
        self,
        source_files: Sequence[Path] | None,
        *,
        default_key: str,
        fallback: Path,
    ) -> Dict[str, Path]:
        if source_files:
            mapping: Dict[str, Path] = {}
            for index, path in enumerate(sorted(set(Path(p).resolve() for p in source_files))):
                mapping[f"{default_key}_{index + 1:02d}"] = path
            if mapping:
                return mapping
        return {default_key: fallback.resolve()}

    def _metadata_path_for_output(self, output: Path) -> Path:
        if output.suffix:
            return output.with_suffix(output.suffix + ".hash.json")
        return output / ".hash.json"

    def _can_reuse_output(
        self,
        output: Path,
        meta_path: Path,
        sources: Dict[str, Path],
    ) -> bool:
        if self._dry_run:
            return False
        if not output.exists() or not meta_path.exists():
            return False
        try:
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        recorded = cached.get("sources", {})
        for key, source_path in sources.items():
            if not source_path.exists():
                return False
            current_hash = self._hash_file(source_path)
            cached_entry = recorded.get(key, {})
            if cached_entry.get("md5") != current_hash:
                return False
        if len(recorded) != len(sources):
            return False
        return True

    def _record_source_hashes(self, meta_path: Path, sources: Dict[str, Path]) -> None:
        if self._dry_run:
            return
        payload = {"sources": {}}
        for key, source_path in sources.items():
            resolved = source_path.resolve()
            if not resolved.exists():
                continue
            payload["sources"][key] = {
                "path": str(resolved),
                "md5": self._hash_file(resolved),
            }
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            LOGGER.warning(
                "failed to persist hash metadata",
                extra={"path": str(meta_path), "error": str(exc)},
            )

    @staticmethod
    def _hash_file(path: Path) -> str:
        checksum = hashlib.md5()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                checksum.update(chunk)
        return checksum.hexdigest()

    def prepare_copernicus_layers(self, *, force: bool = False) -> List[Path]:
        config = self._copernicus
        if not config or not config.enabled:
            return []

        tiles_root = (self._data_dir / "copernicus" / "tiles").resolve()
        if not tiles_root.exists():
            raise FileNotFoundError(f"Copernicus tiles directory not found: {tiles_root}")

        outputs: List[Path] = []
        for layer_config in config.layers:
            slug = _slugify(layer_config.output or layer_config.name)
            layer_dir = tiles_root / slug
            if not layer_dir.exists():
                LOGGER.warning("copernicus layer tiles missing", extra={"layer": layer_config.name, "path": str(layer_dir)})
                continue
            try:
                cog = self._build_copernicus_cog(
                    layer_dir=layer_dir,
                    layer_config=layer_config,
                    config=config,
                    force=force,
                )
            except Exception as exc:
                LOGGER.warning("copernicus layer processing failed", extra={"layer": layer_config.name, "error": str(exc)})
                continue
            outputs.append(cog)
        return outputs

    def _build_copernicus_cog(
        self,
        *,
        layer_dir: Path,
        layer_config: CopernicusLayerConfig,
        config: CopernicusConfig,
        force: bool,
    ) -> Path:
        tile_records = _collect_copernicus_tiles(layer_dir, config)
        if not tile_records:
            raise FileNotFoundError(f"No tiles found under {layer_dir}")

        slug = _slugify(layer_config.output or layer_config.name)
        vrt_dir = self._temp_dir / "copernicus_vrts" / slug
        vrt_dir.mkdir(parents=True, exist_ok=True)

        vrt_paths: List[Path] = []
        for tile_path, zoom, x, y in tile_records:
            vrt_path = vrt_dir / f"{zoom}_{x}_{y}.vrt"
            if force or not vrt_path.exists():
                minx, miny, maxx, maxy = _tile_bounds(x, y, zoom)
                command = [
                    "gdal_translate",
                    "-of",
                    "VRT",
                    "-a_srs",
                    "EPSG:3857",
                    "-a_ullr",
                    str(minx),
                    str(maxy),
                    str(maxx),
                    str(miny),
                    str(tile_path),
                    str(vrt_path),
                ]
                self._runner.run(command, description="georeference copernicus tile")
            vrt_paths.append(vrt_path)

        list_path = vrt_dir / "inputs.txt"
        list_path.write_text("\n".join(str(path) for path in vrt_paths), encoding="utf-8")

        mosaic_vrt = self._temp_dir / f"copernicus_{slug}_mosaic.vrt"
        if mosaic_vrt.exists() and not self._dry_run:
            mosaic_vrt.unlink()

        command = [
            "gdalbuildvrt",
            "-resolution",
            "highest",
            "-input_file_list",
            str(list_path),
            str(mosaic_vrt),
        ]
        self._runner.run(command, description="build Copernicus mosaic VRT")

        cog_path = self._processing_dir / f"copernicus_{slug}_cog.tif"
        if cog_path.exists() and force and not self._dry_run:
            cog_path.unlink()

        translate_cmd = [
            "gdal_translate",
            "-of",
            "COG",
            "-co",
            "COMPRESS=" + _compression_for_format(layer_config.format),
            "-co",
            "BLOCKSIZE=512",
            "-co",
            "NUM_THREADS=ALL_CPUS",
            str(mosaic_vrt),
            str(cog_path),
        ]
        if layer_config.format.lower() == "image/jpeg":
            translate_cmd.extend(["-co", "QUALITY=90"])
        self._runner.run(translate_cmd, description="convert Copernicus mosaic to COG")
        return cog_path

    def prepare_modis_rgb(
        self,
        modis_root: Path,
        *,
        tiles: Sequence[str],
        date_code: str,
    ) -> Path:
        band_map: Dict[str, str] = {
            "red": "Nadir_Reflectance_Band1",
            "green": "Nadir_Reflectance_Band4",
            "blue": "Nadir_Reflectance_Band3",
        }
        return self._prepare_rgb_product(
            product_root=modis_root,
            tiles=tiles,
            date_code=date_code,
            band_map=band_map,
            pattern_template="*{band}_doy{date_code}*.tif",
            product_slug="modis",
            scale_min=self._modis.scale_min,
            scale_max=self._modis.scale_max,
            gamma=self._modis.gamma,
        )

    def prepare_viirs_rgb(
        self,
        viirs_root: Path,
        *,
        tiles: Sequence[str],
        date_code: str,
    ) -> Path:
        product = (self._viirs.product or "").strip()
        if product.endswith((".002", ".003")):
            band_map: Dict[str, str] = {
                "red": "SurfReflect_I1_1",
                "green": "SurfReflect_I2_1",
                "blue": "SurfReflect_I3_1",
            }
        else:
            band_map = {
                "red": "SurfReflect_I1",
                "green": "SurfReflect_I2",
                "blue": "SurfReflect_I3",
            }
        return self._prepare_rgb_product(
            product_root=viirs_root,
            tiles=tiles,
            date_code=date_code,
            band_map=band_map,
            pattern_template="*{band}_doy{date_code}*.tif",
            product_slug="viirs",
            scale_min=self._viirs.scale_min,
            scale_max=self._viirs.scale_max,
            gamma=self._viirs.gamma,
        )

    def _prepare_rgb_product(
        self,
        *,
        product_root: Path,
        tiles: Sequence[str],
        date_code: str,
        band_map: Dict[str, str],
        pattern_template: str,
        product_slug: str,
        scale_min: float,
        scale_max: float,
        gamma: float,
    ) -> Path:
        if not tiles:
            raise ValueError(f"No {product_slug} tiles provided for processing")

        band_files: Dict[str, List[Path]] = {key: [] for key in band_map}

        for tile in tiles:
            tile_dir = product_root / tile
            if not tile_dir.exists():
                raise FileNotFoundError(f"{product_slug.upper()} tile directory not found: {tile_dir}")
            data_dir = self._resolve_tile_data_dir(tile_dir)

            for key, band_name in band_map.items():
                pattern = pattern_template.format(band=band_name, date_code=date_code)
                matches = list(data_dir.glob(pattern))
                if not matches:
                    raise FileNotFoundError(
                        f"Band {band_name} not found in {data_dir} for tile {tile}"
                    )
                band_files[key].extend(matches)

        vrt_paths: Dict[str, Path] = {}
        for key, files in band_files.items():
            list_path = self._temp_dir / f"{product_slug}_{key}_tiles.txt"
            list_path.write_text("\n".join(str(path) for path in files), encoding="utf-8")
            vrt_path = self._temp_dir / f"{product_slug}_{key}_mosaic.vrt"
            command = [
                "gdalbuildvrt",
                "-input_file_list",
                str(list_path),
                str(vrt_path),
            ]
            self._runner.run(command, description=f"mosaic {product_slug.upper()} {key} band")
            vrt_paths[key] = vrt_path

        rgb_vrt = self._temp_dir / f"{product_slug}_rgb.vrt"
        command = [
            "gdalbuildvrt",
            "-separate",
            str(rgb_vrt),
            str(vrt_paths["red"]),
            str(vrt_paths["green"]),
            str(vrt_paths["blue"]),
        ]
        self._runner.run(command, description=f"combine {product_slug.upper()} bands into RGB VRT")

        rgb_tif = self._processing_dir / f"{product_slug}_{date_code}_rgb.tif"

        command = [
            "gdal_translate",
            "-of",
            "GTiff",
            "-co",
            "TILED=YES",
            "-co",
            "COMPRESS=DEFLATE",
            "-co",
            "PHOTOMETRIC=RGB",
            "-ot",
            "Byte",
            "-scale",
            str(scale_min),
            str(scale_max),
            "0",
            "255",
        ]
        if gamma and gamma != 1.0:
            command.extend(["-exponent", str(gamma)])
        command.extend([
            "-a_nodata",
            "0",
            str(rgb_vrt),
            str(rgb_tif),
        ])
        self._runner.run(command, description=f"convert {product_slug.upper()} RGB mosaic to GeoTIFF")

        cog_path = self.create_cog(rgb_tif)
        return cog_path

    def _resolve_tile_data_dir(self, tile_dir: Path) -> Path:
        candidates = sorted(p for p in tile_dir.iterdir() if p.is_dir())
        if candidates:
            return candidates[0]
        return tile_dir

    def _resolve_hls_region(self, plan_region: Optional[str]) -> Optional[HLSPlanRegion]:
        region_name = plan_region or self._hls.plan_region
        if not region_name:
            return None
        for region in self._hls.plan_regions:
            if region.name == region_name:
                return region
        raise ValueError(f"Unknown HLS plan region: {region_name}")

    def _resolve_sentinel2_region(self, plan_region: Optional[str]) -> Optional[HLSPlanRegion]:
        region_name = plan_region or self._sentinel2.plan_region
        if not region_name:
            return None
        for region in self._sentinel2.plan_regions:
            if region.name == region_name:
                return region
        raise ValueError(f"Unknown Sentinel-2 plan region: {region_name}")
