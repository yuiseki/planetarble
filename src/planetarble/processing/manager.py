"""Concrete data processing utilities leveraging GDAL commands."""

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Sequence

from planetarble.core.models import ProcessingConfig
from planetarble.logging import get_logger

from .base import DataProcessor

LOGGER = get_logger(__name__)


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
        dry_run: bool = False,
    ) -> None:
        self._config = config
        self._temp_dir = temp_dir
        self._output_dir = output_dir
        self._processing_dir = self._output_dir / "processing"
        self._runner = CommandRunner(dry_run=dry_run)
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

    def normalize_bmng(self, input_path: Path) -> Path:
        output = self._processing_dir / f"{input_path.stem}_normalized.tif"
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
        return output

    def generate_hillshade(self, gebco_path: Path) -> Path:
        output = self._processing_dir / f"{gebco_path.stem}_hillshade.tif"
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
        return output

    def create_masks(self, natural_earth_path: Path) -> Path:
        destination = self._temp_dir / "natural_earth"
        destination.mkdir(parents=True, exist_ok=True)
        if natural_earth_path.is_file() and natural_earth_path.suffix == ".zip":
            self._extract_zip(natural_earth_path, destination)
            return destination
        if natural_earth_path.is_dir():
            for archive in natural_earth_path.glob("*.zip"):
                self._extract_zip(archive, destination / archive.stem)
        else:  # pragma: no cover - input guard
            raise ValueError(f"Unsupported Natural Earth input: {natural_earth_path}")
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

    def prepare_modis_rgb(
        self,
        modis_root: Path,
        *,
        tiles: Sequence[str],
        date_code: str,
    ) -> Path:
        if not tiles:
            raise ValueError("No MODIS tiles provided for processing")

        band_map: Dict[str, str] = {
            "red": "Nadir_Reflectance_Band1",
            "green": "Nadir_Reflectance_Band4",
            "blue": "Nadir_Reflectance_Band3",
        }

        band_files: Dict[str, List[Path]] = {key: [] for key in band_map}

        for tile in tiles:
            tile_dir = modis_root / tile
            if not tile_dir.exists():
                raise FileNotFoundError(f"MODIS tile directory not found: {tile_dir}")
            candidates = sorted(p for p in tile_dir.glob("MCD43A4.061_*") if p.is_dir())
            if not candidates:
                raise FileNotFoundError(f"No MODIS data bundle found under {tile_dir}")
            data_dir = candidates[0]

            for key, band_name in band_map.items():
                pattern = f"*{band_name}_doy{date_code}*.tif"
                matches = list((data_dir if data_dir.is_dir() else tile_dir).glob(pattern))
                if not matches:
                    raise FileNotFoundError(
                        f"Band {band_name} not found in {data_dir} for tile {tile}"
                    )
                band_files[key].extend(matches)

        vrt_paths: Dict[str, Path] = {}
        for key, files in band_files.items():
            list_path = self._temp_dir / f"modis_{key}_tiles.txt"
            list_path.write_text("\n".join(str(path) for path in files), encoding="utf-8")
            vrt_path = self._temp_dir / f"modis_{key}_mosaic.vrt"
            command = [
                "gdalbuildvrt",
                "-input_file_list",
                str(list_path),
                str(vrt_path),
            ]
            self._runner.run(command, description=f"mosaic MODIS {key} band")
            vrt_paths[key] = vrt_path

        rgb_vrt = self._temp_dir / "modis_rgb.vrt"
        command = [
            "gdalbuildvrt",
            "-separate",
            str(rgb_vrt),
            str(vrt_paths["red"]),
            str(vrt_paths["green"]),
            str(vrt_paths["blue"]),
        ]
        self._runner.run(command, description="combine MODIS bands into RGB VRT")

        rgb_tif = self._processing_dir / f"modis_{date_code}_rgb.tif"
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
            str(rgb_vrt),
            str(rgb_tif),
        ]
        self._runner.run(command, description="convert MODIS RGB mosaic to GeoTIFF")

        cog_path = self.create_cog(rgb_tif)
        return cog_path
