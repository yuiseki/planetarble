"""Concrete data processing utilities leveraging GDAL commands."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import time
import zipfile
from urllib.request import urlopen
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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
from planetarble.logging import get_logger, log_progress, log_step, log_skip

from .base import DataProcessor
from .hls import HLSSceneManifestBuilder
from .ocean import OceanRenderer
from planetarble.acquisition.hls import load_region_geometry, _geom_intersects_bbox
from planetarble.acquisition.mpc import append_sas_token, fetch_sas_token
from planetarble.acquisition.sentinel_2 import Sentinel2SceneManifestBuilder

LOGGER = get_logger(__name__)

ORIGIN_SHIFT = 20037508.342789244


class CommandExecutionError(RuntimeError):
    """Raised when an external processing command fails."""


class CommandRunner:
    """Execute external commands with optional dry-run support."""

    def __init__(self, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    def run(self, command: Sequence[str], *, description: str) -> None:
        log_step(LOGGER, phase="process", step=description, command=list(command))
        if self._dry_run:
            return
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - requires GDAL runtime
            raise CommandExecutionError(f"Command failed: {' '.join(command)}") from exc


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
        max_scenes_per_tile: int = 3,
    ) -> Optional[Path]:
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
        output_name = f"hls_mosaic_{region.name}" if region else "hls_mosaic"
        output_base = destination or (self._processing_dir / f"{output_name}.tif")
        if self._dry_run:
            LOGGER.info(
                "dry-run: would build HLS mosaic",
                extra={"scene_manifest": str(scene_manifest_path), "destination": str(output_base)},
            )
            return output_base
        band_lists = _write_hls_band_lists(self._temp_dir, scenes)
        band_vrts = _build_hls_band_vrts(self._runner, band_lists, self._temp_dir)
        rgb_vrt = _build_hls_rgb_vrt(self._runner, band_vrts, self._temp_dir)
        cropped = _translate_hls_rgb(
            self._runner,
            rgb_vrt,
            output_base,
            region_geometry=region_geometry,
        )
        return self.create_cog(cropped)

    def prepare_sentinel2_scene_manifest(
        self,
        *,
        destination: Optional[Path] = None,
        max_items: Optional[int] = None,
        force_refresh: bool = False,
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
        builder = Sentinel2SceneManifestBuilder(
            self._sentinel2,
            cache_dir=self._data_dir / "cache" / "sentinel2",
            cache_ttl_days=self._sentinel2.cache_ttl_days,
        )
        manifest = builder.build(
            bbox=self._sentinel2.bbox,
            max_items=max_items,
            force_refresh=force_refresh,
        )
        manifest.write(dest)
        return dest

    def build_sentinel2_mosaic(
        self,
        scene_manifest_path: Path,
        *,
        destination: Optional[Path] = None,
        force: bool = False,
    ) -> Optional[Path]:
        if not self._sentinel2.enabled:
            LOGGER.info("Sentinel-2 processing disabled; skipping mosaic generation")
            return None
        scenes = _load_sentinel2_scene_manifest(scene_manifest_path)
        if not scenes:
            raise ValueError(f"No Sentinel-2 scenes found in {scene_manifest_path}")
        scenes = _refresh_sentinel2_scene_urls(scenes, self._sentinel2)
        scenes = _cache_sentinel2_assets(
            scenes,
            cache_dir=self._data_dir / "cache" / "sentinel2" / "assets",
            timeout=self._sentinel2.request_timeout_seconds,
            config=self._sentinel2,
        )
        output_path = destination or (self._processing_dir / "sentinel2_mosaic_cog.tif")
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
                bbox=self._sentinel2.bbox,
                scale_to_byte=False,
            )

        band_lists = _write_sentinel2_band_lists(self._temp_dir, scenes, assets)
        band_vrts = _build_sentinel2_band_vrts(self._runner, band_lists, self._temp_dir)
        rgb_vrt = _build_sentinel2_rgb_vrt(self._runner, band_vrts, self._temp_dir)
        return _translate_sentinel2_rgb(
            self._runner,
            rgb_vrt,
            output_path,
            bbox=self._sentinel2.bbox,
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
        command = [
            "gdal_translate",
            "-of",
            "COG",
            "-co",
            "COMPRESS=JPEG",
            "-co",
            "QUALITY=95",
            str(raster_path),
            str(output),
        ]
        self._runner.run(command, description="create Cloud Optimized GeoTIFF")
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


def _load_hls_scene_manifest(path: Path) -> List[Dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenes = payload.get("scenes") if isinstance(payload, dict) else None
    if not isinstance(scenes, list):
        return []
    return [scene for scene in scenes if isinstance(scene, dict)]


def _refresh_hls_scene_urls(
    scenes: Sequence[Dict[str, object]],
    config: HLSConfig,
) -> List[Dict[str, object]]:
    refreshed: List[Dict[str, object]] = []
    tokens: Dict[str, str] = {}
    for scene in scenes:
        collection = scene.get("collection_id")
        if not isinstance(collection, str) or not collection:
            continue
        token = tokens.get(collection)
        if token is None:
            token = fetch_sas_token(collection, timeout=config.request_timeout_seconds)
            tokens[collection] = token
        bands = scene.get("bands")
        if isinstance(bands, dict):
            updated_bands = {}
            for band, href in bands.items():
                if not isinstance(href, str) or not href:
                    continue
                updated_bands[band] = append_sas_token(_strip_query(href), token)
            scene["bands"] = updated_bands
        qa = scene.get("qa_asset")
        if isinstance(qa, str) and qa:
            scene["qa_asset"] = append_sas_token(_strip_query(qa), token)
        refreshed.append(scene)
    return refreshed


def _strip_query(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _cache_hls_assets(
    scenes: Sequence[Dict[str, object]],
    *,
    cache_dir: Path,
    timeout: int,
) -> List[Dict[str, object]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    total_assets = 0
    for scene in scenes:
        bands = scene.get("bands")
        if isinstance(bands, dict):
            total_assets += len([href for href in bands.values() if isinstance(href, str) and href])
    start_time = time.monotonic()
    completed = 0
    progress_interval = 10
    cache_hits = 0
    downloads = 0
    redownloads = 0
    updated: List[Dict[str, object]] = []
    for scene in scenes:
        collection = scene.get("collection_id")
        item_id = scene.get("item_id")
        if not isinstance(collection, str) or not isinstance(item_id, str):
            continue
        bands = scene.get("bands")
        if not isinstance(bands, dict):
            continue
        local_bands: Dict[str, str] = {}
        for band, href in bands.items():
            if not isinstance(href, str) or not href:
                continue
            local_path, status = _cache_hls_asset(
                href,
                cache_dir=cache_dir / collection / item_id,
                timeout=timeout,
            )
            local_bands[band] = str(local_path)
            completed += 1
            if status == "hit":
                cache_hits += 1
            elif status == "download":
                downloads += 1
            elif status == "redownload":
                redownloads += 1
            if total_assets and (completed % progress_interval == 0 or completed == total_assets):
                elapsed = max(time.monotonic() - start_time, 0.001)
                rate = completed / elapsed
                remaining = max(total_assets - completed, 0)
                eta = remaining / rate if rate > 0 else 0.0
                percent = (completed / total_assets) * 100.0
                log_progress(
                    LOGGER,
                    phase="process",
                    step="hls asset cache",
                    current=completed,
                    total=total_assets,
                    percent=round(percent, 1),
                    elapsed=_format_duration(elapsed),
                    eta=_format_duration(eta),
                    extra={"progress_bar": _format_progress_bar(completed, total_assets)},
                )
        scene["bands"] = local_bands
        updated.append(scene)
    if total_assets:
        hit_rate = cache_hits / total_assets
        redownload_rate = redownloads / total_assets
        LOGGER.info(
            "hls asset cache summary",
            extra={
                "assets": total_assets,
                "hits": cache_hits,
                "downloads": downloads,
                "redownloads": redownloads,
                "hit_rate": round(hit_rate, 4),
                "redownload_rate": round(redownload_rate, 4),
            },
        )
    return updated


def _cache_sentinel2_assets(
    scenes: Sequence[Dict[str, object]],
    *,
    cache_dir: Path,
    timeout: int,
    config: Sentinel2Config,
) -> List[Dict[str, object]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    total_assets = 0
    for scene in scenes:
        assets = scene.get("assets")
        if isinstance(assets, dict):
            total_assets += len([href for href in assets.values() if isinstance(href, str) and href])
    start_time = time.monotonic()
    completed = 0
    progress_interval = 10
    cache_hits = 0
    downloads = 0
    redownloads = 0
    failures = 0
    tokens: Dict[str, str] = {}
    updated: List[Dict[str, object]] = []
    for scene in scenes:
        collection = scene.get("collection_id") or config.collection
        item_id = scene.get("item_id")
        if not isinstance(collection, str) or not isinstance(item_id, str):
            continue
        assets = scene.get("assets")
        if not isinstance(assets, dict):
            continue
        token = tokens.get(collection)
        if token is None:
            token = fetch_sas_token(collection, timeout=timeout)
            tokens[collection] = token
        local_assets: Dict[str, str] = {}
        for asset_name, href in assets.items():
            if not isinstance(href, str) or not href:
                continue
            unsigned = _strip_query(href)
            signed = append_sas_token(unsigned, token)
            local_path, status = _cache_sentinel2_asset(
                signed,
                cache_dir=cache_dir / collection / item_id,
                timeout=timeout,
                asset_name=str(asset_name),
            )
            if status == "failed":
                token = fetch_sas_token(collection, timeout=timeout)
                tokens[collection] = token
                signed = append_sas_token(unsigned, token)
                local_path, status = _cache_sentinel2_asset(
                    signed,
                    cache_dir=cache_dir / collection / item_id,
                    timeout=timeout,
                    asset_name=str(asset_name),
                )
            completed += 1
            if status == "hit":
                cache_hits += 1
            elif status == "download":
                downloads += 1
            elif status == "redownload":
                redownloads += 1
            elif status == "failed":
                failures += 1
            if local_path is not None:
                local_assets[asset_name] = str(local_path)
            if total_assets and (completed % progress_interval == 0 or completed == total_assets):
                elapsed = max(time.monotonic() - start_time, 0.001)
                rate = completed / elapsed
                remaining = max(total_assets - completed, 0)
                eta = remaining / rate if rate > 0 else 0.0
                percent = (completed / total_assets) * 100.0
                log_progress(
                    LOGGER,
                    phase="process",
                    step="sentinel2 asset cache",
                    current=completed,
                    total=total_assets,
                    percent=round(percent, 1),
                    elapsed=_format_duration(elapsed),
                    eta=_format_duration(eta),
                    extra={"progress_bar": _format_progress_bar(completed, total_assets)},
                )
        if local_assets:
            scene["assets"] = local_assets
            updated.append(scene)
    if total_assets:
        hit_rate = cache_hits / total_assets
        redownload_rate = redownloads / total_assets
        failure_rate = failures / total_assets
        LOGGER.info(
            "sentinel2 asset cache summary",
            extra={
                "assets": total_assets,
                "hits": cache_hits,
                "downloads": downloads,
                "redownloads": redownloads,
                "failures": failures,
                "hit_rate": round(hit_rate, 4),
                "redownload_rate": round(redownload_rate, 4),
                "failure_rate": round(failure_rate, 4),
            },
        )
    return updated


def _format_progress_bar(current: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return "[" + ("-" * width) + "]"
    ratio = min(max(current / total, 0.0), 1.0)
    filled = int(round(ratio * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"


def _format_duration(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _cache_hls_asset(url: str, *, cache_dir: Path, timeout: int) -> tuple[Path, str]:
    from urllib.parse import urlsplit

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = urlsplit(url).path
    filename = Path(path).name or "asset.tif"
    destination = cache_dir / filename
    redownloaded = False
    if destination.exists():
        if _is_valid_hls_asset(destination):
            LOGGER.info(
                "hls asset cache hit",
                extra={"url": url, "path": str(destination)},
            )
            return destination, "hit"
        quarantined = _quarantine_hls_asset(destination, reason="invalid cache")
        LOGGER.warning(
            "hls asset cache invalid; quarantined",
            extra={"url": url, "path": str(destination), "quarantine": str(quarantined)},
        )
        redownloaded = True
    LOGGER.info("downloading hls asset", extra={"url": url, "path": str(destination)})
    temp_path = destination.with_suffix(destination.suffix + ".part")
    with urlopen(url, timeout=timeout) as response, temp_path.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    if not _is_valid_hls_asset(temp_path):
        quarantined = _quarantine_hls_asset(temp_path, reason="invalid download")
        LOGGER.warning(
            "hls asset download invalid; quarantined",
            extra={"url": url, "path": str(destination), "quarantine": str(quarantined)},
        )
        raise RuntimeError(f"Downloaded HLS asset is invalid: {destination}")
    temp_path.replace(destination)
    status = "redownload" if redownloaded else "download"
    return destination, status


def _cache_sentinel2_asset(
    url: str,
    *,
    cache_dir: Path,
    timeout: int,
    asset_name: str,
) -> tuple[Optional[Path], str]:
    from urllib.error import HTTPError
    from urllib.parse import urlsplit

    cache_dir.mkdir(parents=True, exist_ok=True)
    path = urlsplit(url).path
    filename = Path(path).name or f"{asset_name}.tif"
    destination = cache_dir / filename
    redownloaded = False
    if destination.exists():
        if _is_valid_sentinel2_asset(destination):
            LOGGER.info(
                "sentinel2 asset cache hit",
                extra={"url": url, "path": str(destination)},
            )
            return destination, "hit"
        elif _aria2c_available() and destination.with_suffix(destination.suffix + ".aria2").exists():
            LOGGER.info(
                "sentinel2 asset resume pending",
                extra={"path": str(destination)},
            )
        else:
            quarantined = _quarantine_hls_asset(destination, reason="invalid cache")
            LOGGER.warning(
                "sentinel2 asset cache invalid; quarantined",
                extra={"url": url, "path": str(destination), "quarantine": str(quarantined)},
            )
            redownloaded = True
    LOGGER.info("downloading sentinel2 asset", extra={"url": url, "path": str(destination)})
    temp_path = destination.with_suffix(destination.suffix + ".part")
    temp_path.unlink(missing_ok=True)
    use_aria2c = _aria2c_available()
    try:
        if use_aria2c:
            try:
                _download_with_aria2(url, destination, timeout=timeout)
            except RuntimeError as exc:
                LOGGER.warning(
                    "sentinel2 asset download failed",
                    extra={"url": url, "path": str(destination), "error": str(exc)},
                )
                return None, "failed"
        else:
            temp_path.unlink(missing_ok=True)
            with urlopen(url, timeout=timeout) as response, temp_path.open("wb") as handle:
                total_size = None
                try:
                    header_value = response.getheader("Content-Length")
                    if header_value:
                        total_size = int(header_value)
                except (ValueError, TypeError):
                    total_size = None
                downloaded = 0
                log_threshold = 10 * 1024 * 1024
                next_log = log_threshold
                start_time = time.monotonic()
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if downloaded >= next_log:
                        elapsed = max(time.monotonic() - start_time, 0.001)
                        rate = downloaded / elapsed
                        percent = None
                        if total_size:
                            percent = (downloaded / total_size) * 100.0
                        log_progress(
                            LOGGER,
                            phase="process",
                            step="sentinel2 asset download",
                            current=downloaded,
                            total=total_size,
                            percent=round(percent, 1) if percent is not None else None,
                            elapsed=_format_duration(elapsed),
                            eta=_format_duration((total_size - downloaded) / rate) if total_size and rate > 0 else None,
                            extra={"path": str(destination), "bytes_per_sec": round(rate, 1)},
                        )
                        next_log += log_threshold
    except HTTPError as exc:
        LOGGER.warning(
            "sentinel2 asset download failed",
            extra={"url": url, "path": str(destination), "status": exc.code},
        )
        if temp_path.exists():
            temp_path.unlink()
        return None, "failed"
    except OSError as exc:
        LOGGER.warning(
            "sentinel2 asset download failed",
            extra={"url": url, "path": str(destination), "error": str(exc)},
        )
        if temp_path.exists():
            temp_path.unlink()
        return None, "failed"
    downloaded_path = destination if use_aria2c else temp_path
    if not _is_valid_sentinel2_asset(downloaded_path):
        quarantined = _quarantine_hls_asset(downloaded_path, reason="invalid download")
        LOGGER.warning(
            "sentinel2 asset download invalid; quarantined",
            extra={"url": url, "path": str(destination), "quarantine": str(quarantined)},
        )
        return None, "failed"
    if not use_aria2c:
        temp_path.replace(destination)
    status = "redownload" if redownloaded else "download"
    return destination, status


def _aria2c_available() -> bool:
    return shutil.which("aria2c") is not None


def _download_with_aria2(url: str, destination: Path, *, timeout: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "aria2c",
        "--continue=true",
        "--allow-overwrite=false",
        "--auto-file-renaming=false",
        "--file-allocation=none",
        "--remove-control-file=false",
        "--auto-save-interval=5",
        "--summary-interval=10",
        "--console-log-level=warn",
        f"--timeout={timeout}",
        f"--connect-timeout={timeout}",
        "--dir",
        str(destination.parent),
        "--out",
        destination.name,
        url,
    ]
    LOGGER.info("sentinel2 asset download via aria2c", extra={"command": " ".join(command)})
    try:
        subprocess.run(command, check=True)  # pragma: no cover - requires aria2c
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"aria2c failed for {url}") from exc


def _quarantine_hls_asset(path: Path, *, reason: str) -> Path:
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    quarantined = path.with_name(f"{path.name}.corrupt-{timestamp}")
    try:
        path.rename(quarantined)
    except FileNotFoundError:
        return path
    LOGGER.info(
        "hls asset quarantined",
        extra={"path": str(path), "quarantine": str(quarantined), "reason": reason},
    )
    return quarantined


def _is_valid_hls_asset(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        from osgeo import gdal
    except Exception:
        # If GDAL isn't available, skip validation instead of blocking.
        return True
    gdal.ErrorReset()
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        return False
    band = dataset.GetRasterBand(1)
    if band is None:
        return False
    block_x, block_y = band.GetBlockSize()
    xsize = band.XSize
    ysize = band.YSize
    if block_x <= 0 or block_y <= 0 or xsize <= 0 or ysize <= 0:
        return False
    tiles_x = math.ceil(xsize / block_x)
    tiles_y = math.ceil(ysize / block_y)
    sample_tiles = {
        (0, 0),
        (tiles_x // 2, 0),
        (0, tiles_y // 2),
        (tiles_x // 2, tiles_y // 2),
        (tiles_x - 1, tiles_y - 1),
    }
    for tx, ty in sample_tiles:
        xoff = min(max(tx * block_x, 0), xsize - 1)
        yoff = min(max(ty * block_y, 0), ysize - 1)
        width = min(block_x, xsize - xoff)
        height = min(block_y, ysize - yoff)
        gdal.ErrorReset()
        sample = band.ReadRaster(xoff, yoff, width, height)
        if sample is None:
            return False
        if gdal.GetLastErrorType() != 0:
            return False
    return True


def _is_valid_sentinel2_asset(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        from osgeo import gdal
    except Exception:
        return True
    gdal.ErrorReset()
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        return False
    band = dataset.GetRasterBand(1)
    if band is None:
        return False
    block_x, block_y = band.GetBlockSize()
    xsize = band.XSize
    ysize = band.YSize
    if block_x <= 0 or block_y <= 0 or xsize <= 0 or ysize <= 0:
        return False
    tiles_x = math.ceil(xsize / block_x)
    tiles_y = math.ceil(ysize / block_y)
    sample_tiles = {
        (0, 0),
        (tiles_x // 2, 0),
        (0, tiles_y // 2),
        (tiles_x // 2, tiles_y // 2),
        (tiles_x - 1, tiles_y - 1),
    }
    for tx, ty in sample_tiles:
        xoff = min(max(tx * block_x, 0), xsize - 1)
        yoff = min(max(ty * block_y, 0), ysize - 1)
        width = min(block_x, xsize - xoff)
        height = min(block_y, ysize - yoff)
        gdal.ErrorReset()
        sample = band.ReadRaster(xoff, yoff, width, height)
        if sample is None:
            return False
        if gdal.GetLastErrorType() != 0:
            return False
    return True


def _is_valid_raster(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        from osgeo import gdal  # type: ignore
    except Exception:
        return True
    gdal.ErrorReset()
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        return False
    band = dataset.GetRasterBand(1)
    if band is None:
        return False
    return True


def _write_hls_band_lists(temp_dir: Path, scenes: Sequence[Dict[str, object]]) -> Dict[str, Path]:
    band_keys = {"B02": "blue", "B03": "green", "B04": "red"}
    lists: Dict[str, List[str]] = {label: [] for label in band_keys.values()}
    for scene in scenes:
        bands = scene.get("bands")
        if not isinstance(bands, dict):
            continue
        for band, label in band_keys.items():
            url = bands.get(band)
            if isinstance(url, str) and url:
                lists[label].append(url)
    paths: Dict[str, Path] = {}
    temp_dir.mkdir(parents=True, exist_ok=True)
    for label, urls in lists.items():
        if not urls:
            raise ValueError(f"No HLS sources found for {label} band")
        list_path = temp_dir / f"hls_{label}_sources.txt"
        list_path.write_text("\n".join(urls), encoding="utf-8")
        paths[label] = list_path
    return paths


def _build_hls_band_vrts(
    runner: CommandRunner,
    band_lists: Dict[str, Path],
    temp_dir: Path,
) -> Dict[str, Path]:
    vrts: Dict[str, Path] = {}
    for label, list_path in band_lists.items():
        vrt_path = temp_dir / f"hls_{label}_mosaic.vrt"
        command = [
            "gdalbuildvrt",
            "-allow_projection_difference",
            "-input_file_list",
            str(list_path),
            str(vrt_path),
        ]
        runner.run(command, description=f"mosaic HLS {label} band")
        vrts[label] = vrt_path
    return vrts


def _build_hls_rgb_vrt(
    runner: CommandRunner,
    band_vrts: Dict[str, Path],
    temp_dir: Path,
) -> Path:
    rgb_vrt = temp_dir / "hls_rgb.vrt"
    command = [
        "gdalbuildvrt",
        "-separate",
        str(rgb_vrt),
        str(band_vrts["red"]),
        str(band_vrts["green"]),
        str(band_vrts["blue"]),
    ]
    runner.run(command, description="combine HLS bands into RGB VRT")
    return rgb_vrt


def _translate_hls_rgb(
    runner: CommandRunner,
    rgb_vrt: Path,
    output_path: Path,
    *,
    region_geometry: Optional["ogr.Geometry"] = None,
) -> Path:
    translate_input = rgb_vrt
    if region_geometry is not None:
        cutline = output_path.with_suffix(".geojson")
        _write_geometry_geojson(region_geometry, cutline)
        cropped = output_path.with_suffix(".cropped.tif")
        if cropped.exists():
            cropped.unlink()
        command = [
            "gdalwarp",
            "-cutline",
            str(cutline),
            "-cutline_srs",
            "EPSG:4326",
            "-t_srs",
            "EPSG:4326",
            "-crop_to_cutline",
            "-overwrite",
            "-of",
            "GTiff",
            str(rgb_vrt),
            str(cropped),
        ]
        runner.run(command, description="crop HLS mosaic to region")
        translate_input = cropped
    if output_path.exists():
        output_path.unlink()
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
        "0",
        "10000",
        "0",
        "255",
        "-a_nodata",
        "0",
        str(translate_input),
        str(output_path),
    ]
    runner.run(command, description="convert HLS RGB mosaic to GeoTIFF")
    return output_path


def _load_sentinel2_scene_manifest(path: Path) -> List[Dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenes = payload.get("scenes") if isinstance(payload, dict) else None
    if not isinstance(scenes, list):
        return []
    return [scene for scene in scenes if isinstance(scene, dict)]


def _refresh_sentinel2_scene_urls(
    scenes: Sequence[Dict[str, object]],
    config: Sentinel2Config,
) -> List[Dict[str, object]]:
    refreshed: List[Dict[str, object]] = []
    tokens: Dict[str, str] = {}
    for scene in scenes:
        collection = scene.get("collection_id") or config.collection
        if not isinstance(collection, str) or not collection:
            continue
        token = tokens.get(collection)
        if token is None:
            token = fetch_sas_token(collection, timeout=config.request_timeout_seconds)
            tokens[collection] = token
        assets = scene.get("assets")
        if isinstance(assets, dict):
            updated_assets = {}
            for asset_name, href in assets.items():
                if not isinstance(href, str) or not href:
                    continue
                updated_assets[asset_name] = append_sas_token(_strip_query(href), token)
            scene["assets"] = updated_assets
        refreshed.append(scene)
    return refreshed


def _select_sentinel2_asset_mode(assets: Sequence[str]) -> str:
    normalized = [asset.strip() for asset in assets if asset]
    if not normalized:
        raise ValueError("sentinel2.assets must contain at least one asset name")
    if len(normalized) == 1 and normalized[0].lower() == "visual":
        return "visual"
    band_set = {asset.upper() for asset in normalized}
    if band_set == {"B02", "B03", "B04"}:
        return "bands"
    raise ValueError("sentinel2.assets must be ['visual'] or ['B02','B03','B04']")


def _write_sentinel2_visual_list(
    temp_dir: Path,
    scenes: Sequence[Dict[str, object]],
    asset_name: str,
) -> Path:
    urls: List[str] = []
    for scene in scenes:
        assets = scene.get("assets")
        if not isinstance(assets, dict):
            continue
        url = assets.get(asset_name)
        if isinstance(url, str) and url:
            urls.append(url)
    if not urls:
        raise ValueError("No Sentinel-2 visual assets found in manifest")
    temp_dir.mkdir(parents=True, exist_ok=True)
    list_path = temp_dir / "sentinel2_visual_sources.txt"
    list_path.write_text("\n".join(urls), encoding="utf-8")
    return list_path


def _build_sentinel2_visual_vrt(
    runner: CommandRunner,
    list_path: Path,
    temp_dir: Path,
) -> Path:
    vrt_path = temp_dir / "sentinel2_visual_mosaic.vrt"
    command = [
        "gdalbuildvrt",
        "-allow_projection_difference",
        "-resolution",
        "highest",
        "-input_file_list",
        str(list_path),
        str(vrt_path),
    ]
    runner.run(command, description="mosaic Sentinel-2 visual assets")
    return vrt_path


def _write_sentinel2_band_lists(
    temp_dir: Path,
    scenes: Sequence[Dict[str, object]],
    assets: Sequence[str],
) -> Dict[str, Path]:
    band_keys = {"B02": "blue", "B03": "green", "B04": "red"}
    lists: Dict[str, List[str]] = {label: [] for label in band_keys.values()}
    asset_set = {asset.upper() for asset in assets}
    for scene in scenes:
        bands = scene.get("assets")
        if not isinstance(bands, dict):
            continue
        for band, label in band_keys.items():
            if band not in asset_set:
                continue
            url = bands.get(band)
            if isinstance(url, str) and url:
                lists[label].append(url)
    paths: Dict[str, Path] = {}
    temp_dir.mkdir(parents=True, exist_ok=True)
    for label, urls in lists.items():
        if not urls:
            raise ValueError(f"No Sentinel-2 sources found for {label} band")
        list_path = temp_dir / f"sentinel2_{label}_sources.txt"
        list_path.write_text("\n".join(urls), encoding="utf-8")
        paths[label] = list_path
    return paths


def _build_sentinel2_band_vrts(
    runner: CommandRunner,
    band_lists: Dict[str, Path],
    temp_dir: Path,
) -> Dict[str, Path]:
    vrts: Dict[str, Path] = {}
    for label, list_path in band_lists.items():
        vrt_path = temp_dir / f"sentinel2_{label}_mosaic.vrt"
        command = [
            "gdalbuildvrt",
            "-allow_projection_difference",
            "-resolution",
            "highest",
            "-input_file_list",
            str(list_path),
            str(vrt_path),
        ]
        runner.run(command, description=f"mosaic Sentinel-2 {label} band")
        vrts[label] = vrt_path
    return vrts


def _build_sentinel2_rgb_vrt(
    runner: CommandRunner,
    band_vrts: Dict[str, Path],
    temp_dir: Path,
) -> Path:
    rgb_vrt = temp_dir / "sentinel2_rgb.vrt"
    command = [
        "gdalbuildvrt",
        "-separate",
        str(rgb_vrt),
        str(band_vrts["red"]),
        str(band_vrts["green"]),
        str(band_vrts["blue"]),
    ]
    runner.run(command, description="combine Sentinel-2 bands into RGB VRT")
    return rgb_vrt


def _translate_sentinel2_rgb(
    runner: CommandRunner,
    rgb_vrt: Path,
    output_path: Path,
    *,
    bbox: Tuple[float, float, float, float],
    scale_to_byte: bool,
) -> Path:
    translate_input = rgb_vrt
    if bbox:
        cropped = output_path.with_suffix(".cropped.tif")
        if cropped.exists():
            cropped.unlink()
        minx, miny, maxx, maxy = (str(value) for value in bbox)
        command = [
            "gdalwarp",
            "-te_srs",
            "EPSG:4326",
            "-te",
            minx,
            miny,
            maxx,
            maxy,
            "-overwrite",
            "-of",
            "GTiff",
            str(rgb_vrt),
            str(cropped),
        ]
        runner.run(command, description="crop Sentinel-2 mosaic to bbox")
        translate_input = cropped
    if output_path.exists():
        output_path.unlink()
    command = [
        "gdal_translate",
        "-of",
        "COG",
        "-co",
        "COMPRESS=JPEG",
        "-co",
        "QUALITY=90",
        "-co",
        "BLOCKSIZE=512",
        "-co",
        "NUM_THREADS=ALL_CPUS",
        "-co",
        "PHOTOMETRIC=RGB",
    ]
    if scale_to_byte:
        command.extend(
            [
                "-ot",
                "Byte",
                "-scale",
                "0",
                "10000",
                "0",
                "255",
                "-a_nodata",
                "0",
            ]
        )
    command.extend([str(translate_input), str(output_path)])
    runner.run(command, description="convert Sentinel-2 mosaic to COG")
    return output_path


def _write_geometry_geojson(geometry: "ogr.Geometry", path: Path) -> None:
    try:
        from osgeo import ogr  # type: ignore
        from osgeo import osr  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on GDAL availability
        raise RuntimeError("GDAL (osgeo.ogr) is required for region clipping") from exc
    driver = ogr.GetDriverByName("GeoJSON")
    if driver is None:
        raise RuntimeError("GeoJSON driver not available in GDAL")
    if path.exists():
        path.unlink()
    dataset = driver.CreateDataSource(str(path))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    layer = dataset.CreateLayer("region", srs=srs, geom_type=ogr.wkbMultiPolygon)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry)
    layer.CreateFeature(feature)
    feature = None
    dataset = None

def _collect_copernicus_tiles(
    layer_dir: Path,
    config: CopernicusConfig,
) -> List[Tuple[Path, int, int, int]]:
    records: List[Tuple[Path, int, int, int]] = []
    valid_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    available_zooms = []
    for zoom in range(config.min_zoom, config.max_zoom + 1):
        zoom_dir = layer_dir / str(zoom)
        if not zoom_dir.exists():
            continue
        available_zooms.append(zoom)
    if not available_zooms:
        return records
    target_zoom = config.max_zoom if config.max_zoom in available_zooms else max(available_zooms)
    if target_zoom != config.max_zoom:
        LOGGER.warning(
            "copernicus max zoom tiles missing; using highest available zoom",
            extra={"requested": config.max_zoom, "available": target_zoom},
        )
    for zoom in [target_zoom]:
        zoom_dir = layer_dir / str(zoom)
        for x_dir in sorted(p for p in zoom_dir.iterdir() if p.is_dir()):
            try:
                x = int(x_dir.name)
            except ValueError:
                continue
            for tile_file in sorted(x_dir.iterdir()):
                if not tile_file.is_file():
                    continue
                if tile_file.suffix.lower() not in valid_suffixes:
                    continue
                try:
                    y = int(tile_file.stem)
                except ValueError:
                    continue
                records.append((tile_file, zoom, x, y))
    return records


def _extension_for_format(fmt: str) -> str:
    normalized = fmt.lower()
    if normalized in {"image/jpeg", "image/jpg"}:
        return "jpg"
    if normalized == "image/png":
        return "png"
    if normalized == "image/webp":
        return "webp"
    return normalized.split("/")[-1] or "bin"


def _compression_for_format(fmt: str) -> str:
    normalized = fmt.lower()
    if normalized in {"image/png", "image/webp"}:
        return "DEFLATE"
    if normalized in {"image/jpeg", "image/jpg"}:
        return "JPEG"
    return "DEFLATE"


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "layer"


def _tile_bounds(x: int, y: int, zoom: int) -> Tuple[float, float, float, float]:
    n = 2**zoom
    tile_size = (ORIGIN_SHIFT * 2) / n
    minx = -ORIGIN_SHIFT + x * tile_size
    maxx = minx + tile_size
    maxy = ORIGIN_SHIFT - y * tile_size
    miny = maxy - tile_size
    return minx, miny, maxx, maxy


def _tile_range(bbox: Tuple[float, float, float, float], zoom: int) -> Tuple[int, int, int, int]:
    min_lon, min_lat, max_lon, max_lat = bbox
    min_lon = max(-180.0, min(180.0, min_lon))
    max_lon = max(-180.0, min(180.0, max_lon))
    min_lat = max(-85.05112878, min(85.05112878, min_lat))
    max_lat = max(-85.05112878, min(85.05112878, max_lat))
    if max_lon < min_lon:
        min_lon, max_lon = max_lon, min_lon
    if max_lat < min_lat:
        min_lat, max_lat = max_lat, min_lat

    epsilon = 1e-9
    x_min = int(math.floor(_lon_to_tile(min_lon, zoom)))
    x_max = int(math.floor(_lon_to_tile(max_lon - epsilon, zoom)))
    y_min = int(math.floor(_lat_to_tile(max_lat - epsilon, zoom)))
    y_max = int(math.floor(_lat_to_tile(min_lat, zoom)))

    n = 2**zoom
    x_min = max(0, min(x_min, n - 1))
    x_max = max(0, min(x_max, n - 1))
    y_min = max(0, min(y_min, n - 1))
    y_max = max(0, min(y_max, n - 1))
    return x_min, x_max, y_min, y_max


def _lon_to_tile(lon: float, zoom: int) -> float:
    n = 2**zoom
    return (lon + 180.0) / 360.0 * n


def _lat_to_tile(lat: float, zoom: int) -> float:
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat)
    n = 2**zoom
    return (1 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2 * n

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
