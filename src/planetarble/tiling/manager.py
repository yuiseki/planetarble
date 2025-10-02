"""Web Mercator tiling utilities built on GDAL."""

from __future__ import annotations

import subprocess
from pathlib import Path
import shutil
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
        self._gdal2mbtiles = shutil.which("gdal2mbtiles")
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._tiling_dir.mkdir(parents=True, exist_ok=True)

    def reproject_to_webmercator(self, input_path: Path) -> Path:
        output = self._temp_dir / f"{input_path.stem}_3857.vrt"
        if output.exists() and not self._dry_run:
            output.unlink()
        tile_dimension = 256 * (2 ** self._config.max_zoom)
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
            "-ts",
            str(tile_dimension),
            str(tile_dimension),
            "-overwrite",
            "-of",
            "VRT",
            str(input_path),
            str(output),
        ]
        self._runner.run(command, description="reproject raster to EPSG:3857")
        return output

    def generate_pyramid(self, input_path: Path) -> Path:
        # For MBTiles generation we defer to gdal_translate output path naming
        return input_path

    def create_mbtiles(
        self,
        pyramid_path: Path,
        format: str | None = None,
        quality: int | None = None,
    ) -> Path:
        tile_format = (format or self._config.tile_format).upper()
        quality_value = str(quality or self._config.tile_quality)
        mbtiles_path = self._tiling_dir / f"planet_{self._config.gebco_year}_{self._config.max_zoom}z.mbtiles"
        tiler_preference = (self._config.mbtiles_tiler or "auto").lower()
        preferred = tiler_preference in {"gdal2mbtiles", "pyvips"}
        auto_mode = tiler_preference == "auto"

        if preferred and not self._gdal2mbtiles:
            raise TileCommandError("gdal2mbtiles requested but not found in PATH")

        if mbtiles_path.exists() and not self._dry_run:
            mbtiles_path.unlink()

        if self._should_use_gdal2mbtiles(tile_format, auto_mode, preferred):
            try:
                self._run_gdal2mbtiles(pyramid_path, mbtiles_path, tile_format)
                LOGGER.info(
                    "generated MBTiles with gdal2mbtiles",
                    extra={
                        "path": str(mbtiles_path),
                        "tile_format": tile_format,
                        "max_zoom": self._config.max_zoom,
                    },
                )
                return mbtiles_path
            except TileCommandError:
                if preferred:
                    raise
                LOGGER.warning(
                    "gdal2mbtiles failed; falling back to gdal_translate",
                    extra={"tile_format": tile_format},
                )

        self._run_gdal_translate(pyramid_path, mbtiles_path, tile_format, quality_value)
        self.optimize_overviews(mbtiles_path)
        return mbtiles_path

    def _should_use_gdal2mbtiles(self, tile_format: str, auto_mode: bool, preferred: bool) -> bool:
        if self._dry_run:
            return False
        if not self._gdal2mbtiles:
            return False
        if preferred:
            return True
        if not auto_mode:
            return False
        # gdal2mbtiles currently handles JPEG fastest; other formats fall back to GDAL.
        return tile_format == "JPEG"

    def _run_gdal2mbtiles(self, pyramid_path: Path, destination: Path, tile_format: str) -> None:
        command = [
            self._gdal2mbtiles or "gdal2mbtiles",
            str(pyramid_path),
            str(destination),
        ]
        # Only pass format hint when gdal2mbtiles supports the requested encoding.
        format_map = {"JPEG": "jpg", "PNG": "png"}
        tile_format_lower = format_map.get(tile_format)
        additional_args = []
        if tile_format_lower:
            additional_args.append(f"--format={tile_format_lower}")
        additional_args.append(f"--max-resolution={self._config.max_zoom}")
        command[1:1] = additional_args
        self._runner.run(command, description="generate MBTiles pyramid via gdal2mbtiles")

    def _run_gdal_translate(
        self,
        pyramid_path: Path,
        destination: Path,
        tile_format: str,
        quality_value: str,
    ) -> None:
        command = [
            "gdal_translate",
            "--config",
            "GDAL_NUM_THREADS",
            "ALL_CPUS",
            "--config",
            "GDAL_CACHEMAX",
            "2048",
            "-of",
            "MBTILES",
            "-co",
            "BLOCKSIZE=512",
            "-co",
            f"TILE_FORMAT={tile_format}",
            "-co",
            f"QUALITY={quality_value}",
            "-co",
            "ZOOM_LEVEL_STRATEGY=LOWER",
            str(pyramid_path),
            str(destination),
        ]
        self._runner.run(command, description="generate MBTiles pyramid via gdal_translate")

    def optimize_overviews(self, mbtiles_path: Path) -> None:
        if self._dry_run:
            return

        resampling = self._config.resampling.lower()
        if resampling not in ("nearest", "average", "gauss", "cubic", "cubicspline", "lanczos", "mode"):
            LOGGER.warning(f"unknown resampling method '{resampling}'; defaulting to 'cubic'")
            resampling = "cubic"

        overview_levels = self._compute_overview_factors()
        if not overview_levels:
            LOGGER.debug("no overview levels requested; skipping gdaladdo")
            return

        LOGGER.info(f"building overviews: {', '.join(overview_levels)}")
        command = ["gdaladdo", "-r", resampling, str(mbtiles_path), *overview_levels]
        self._runner.run(command, description="build MBTiles overviews")

    def _compute_overview_factors(self) -> list[str]:
        # Overviews are powers of two down to zoom level 0.
        max_zoom = max(0, self._config.max_zoom)
        return [str(2**level) for level in range(1, max_zoom + 1)]
