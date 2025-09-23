"""Web Mercator tiling utilities built on GDAL."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

from planetarble.core.models import ProcessingConfig
from planetarble.logging import get_logger

from .base import TileGenerator

LOGGER = get_logger(__name__)


class TileCommandError(RuntimeError):
    """Raised when a tiling command exits with a non-zero code."""


class TileRunner:
    """Execute external commands and propagate failures with context."""

    def __init__(self, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    def run(self, command: List[str], *, description: str) -> None:
        LOGGER.info("tiling step", extra={"description": description, "command": " ".join(command)})
        if self._dry_run:
            return
        try:
            subprocess.run(command, check=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - depends on GDAL runtime
            raise TileCommandError(f"Command failed: {' '.join(command)}") from exc


class TilingManager(TileGenerator):
    """Generate MBTiles from processed rasters."""

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
        self._tiling_dir = self._output_dir / "tiling"
        self._dry_run = dry_run
        self._runner = TileRunner(dry_run=dry_run)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._tiling_dir.mkdir(parents=True, exist_ok=True)

    def reproject_to_webmercator(self, input_path: Path) -> Path:
        output = self._temp_dir / f"{input_path.stem}_3857.vrt"
        if output.exists() and not self._dry_run:
            output.unlink()
        command = [
            "gdalwarp",
            "-t_srs",
            "EPSG:3857",
            "-r",
            "bilinear",
            "-multi",
            "-dstalpha",
            "-te",
            "-20037508.342789244",
            "-20037508.342789244",
            "20037508.342789244",
            "20037508.342789244",
            "-te_srs",
            "EPSG:3857",
            "-overwrite",
            "-of",
            "VRT",
            str(input_path),
            str(output),
        ]
        self._runner.run(command, description="reproject raster to EPSG:3857")
        return output

    def generate_pyramid(self, input_path: Path, max_zoom: int | None = None) -> Path:
        # For MBTiles generation we defer to gdal_translate output path naming
        return input_path

    def create_mbtiles(self, pyramid_path: Path, format: str | None = None, quality: int | None = None) -> Path:
        tile_format = (format or self._config.tile_format).upper()
        quality_value = str(quality or self._config.tile_quality)
        max_zoom = str(self._config.max_zoom)
        mbtiles_path = self._tiling_dir / f"world_{self._config.max_zoom}z.mbtiles"
        command = [
            "gdal_translate",
            "-of",
            "MBTILES",
            "-co",
            f"TILE_FORMAT={tile_format}",
            "-co",
            f"QUALITY={quality_value}",
            "-co",
            "MINZOOM=0",
            "-co",
            f"MAXZOOM={max_zoom}",
            str(pyramid_path),
            str(mbtiles_path),
        ]
        self._runner.run(command, description="generate MBTiles pyramid")
        self.optimize_overviews(mbtiles_path)
        return mbtiles_path

    def optimize_overviews(self, mbtiles_path: Path) -> None:
        # Build overviews for better rendering performance at low zooms
        overview_levels = ["2", "4", "6", "8", "10", "12", "16", "32", "64"]
        command = ["gdaladdo", "-r", "average", str(mbtiles_path), *overview_levels]
        try:
            self._runner.run(command, description="build MBTiles overviews")
        except TileCommandError:
            LOGGER.warning("gdaladdo overviews skipped (command failed)")
