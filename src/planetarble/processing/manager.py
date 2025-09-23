"""Concrete data processing utilities leveraging GDAL commands."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence
import zipfile

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
        self._runner = CommandRunner(dry_run=dry_run)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def normalize_bmng(self, input_path: Path) -> Path:
        output = self._temp_dir / f"{input_path.stem}_normalized.tif"
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
        output = self._temp_dir / f"{gebco_path.stem}_hillshade.tif"
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
        output = self._output_dir / f"{raster_path.stem}_cog.tif"
        command = [
            "gdal_translate",
            "-of",
            "COG",
            "-co",
            "COMPRESS=DEFLATE",
            str(raster_path),
            str(output),
        ]
        self._runner.run(command, description="create Cloud Optimized GeoTIFF")
        return output

    def blend_layers(self, base: Path, overlay: Path, opacity: float) -> Path:
        opacity = max(0.0, min(opacity, 1.0))
        output = self._temp_dir / f"{base.stem}_blended.tif"
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
