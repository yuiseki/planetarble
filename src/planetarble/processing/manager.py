"""Concrete data processing utilities leveraging GDAL commands."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from planetarble.core.models import CopernicusConfig, CopernicusLayerConfig, ProcessingConfig
from planetarble.logging import get_logger

from .base import DataProcessor

LOGGER = get_logger(__name__)

ORIGIN_SHIFT = 20037508.342789244


class CommandExecutionError(RuntimeError):
    """Raised when an external processing command fails."""


class CommandRunner:
    """Execute external commands with optional dry-run support."""

    def __init__(self, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    def run(self, command: Sequence[str], *, description: str) -> None:
        LOGGER.info("processing step", extra={"description": description, "command": " ".join(command)})
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
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._temp_dir = temp_dir
        self._output_dir = output_dir
        self._data_dir = data_dir
        self._processing_dir = self._output_dir / "processing"
        self._copernicus = copernicus
        self._runner = CommandRunner(dry_run=dry_run)
        self._dry_run = dry_run
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._processing_dir.mkdir(parents=True, exist_ok=True)

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
        if mosaic_vrt.exists() and force and not self._dry_run:
            mosaic_vrt.unlink()

        if force or not mosaic_vrt.exists():
            command = [
                "gdalbuildvrt",
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
            scale_min=self._config.modis_scale_min,
            scale_max=self._config.modis_scale_max,
            gamma=self._config.modis_gamma,
        )

    def prepare_viirs_rgb(
        self,
        viirs_root: Path,
        *,
        tiles: Sequence[str],
        date_code: str,
    ) -> Path:
        product = (self._config.viirs_product or "").strip()
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
            scale_min=self._config.viirs_scale_min,
            scale_max=self._config.viirs_scale_max,
            gamma=self._config.viirs_gamma,
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

def _collect_copernicus_tiles(
    layer_dir: Path,
    config: CopernicusConfig,
) -> List[Tuple[Path, int, int, int]]:
    records: List[Tuple[Path, int, int, int]] = []
    valid_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    for zoom in range(config.min_zoom, config.max_zoom + 1):
        zoom_dir = layer_dir / str(zoom)
        if not zoom_dir.exists():
            continue
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
            scale_min=self._config.modis_scale_min,
            scale_max=self._config.modis_scale_max,
            gamma=self._config.modis_gamma,
        )

    def prepare_viirs_rgb(
        self,
        viirs_root: Path,
        *,
        tiles: Sequence[str],
        date_code: str,
    ) -> Path:
        product = (self._config.viirs_product or "").strip()
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
            scale_min=self._config.viirs_scale_min,
            scale_max=self._config.viirs_scale_max,
            gamma=self._config.viirs_gamma,
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
