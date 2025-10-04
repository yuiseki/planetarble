"""Web Mercator tiling utilities built on GDAL."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
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
           proc = subprocess.run(command, check=True, text=True, capture_output=True)
           if proc.stdout:
               LOGGER.debug(proc.stdout.strip())
           if proc.stderr:
               LOGGER.debug(proc.stderr.strip())
        except subprocess.CalledProcessError as exc:  # pragma: no cover - depends on GDAL runtime
           msg = f"Command failed: {' '.join(command)}"
           if exc.stdout:
               msg += f"\n--- stdout ---\n{exc.stdout}"
           if exc.stderr:
               msg += f"\n--- stderr ---\n{exc.stderr}"
           raise TileCommandError(msg) from exc


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
        self._gdal2mbtiles_cmd = self._resolve_gdal2mbtiles()
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._tiling_dir.mkdir(parents=True, exist_ok=True)

    def reproject_to_webmercator(self, input_path: Path) -> Path:
        output = self._temp_dir / f"{input_path.stem}_3857.vrt"
        if output.exists() and not self._dry_run:
            output.unlink()
        tile_dimension = 256 * (2 ** self._config.max_zoom)

        LOGGER.info("warp params",
            extra={"dst_srs": "EPSG:3857",
                    "tile_dim": tile_dimension,
                    "world_extent": "[-20037508.342789244, 20037508.342789244]"})

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

    def create_mbtiles(
        self,
        source_path: Path,
        format: str | None = None,
        quality: int | None = None,
    ) -> Path:
        tile_format = (format or self._config.tile_format).upper()
        quality_value = str(quality or self._config.tile_quality)
        mbtiles_path = self._tiling_dir / f"planet_{self._config.gebco_year}_{self._config.max_zoom}z.mbtiles"
        tiler_preference = (self._config.mbtiles_tiler or "auto").lower()
        preferred = tiler_preference in {"gdal2mbtiles", "pyvips"}
        auto_mode = tiler_preference == "auto"

        if preferred and not self._gdal2mbtiles_cmd:
            raise TileCommandError("gdal2mbtiles requested but not available in environment")

        if mbtiles_path.exists() and not self._dry_run:
            mbtiles_path.unlink()

        reprojected_path: Path | None = None

        if self._should_use_gdal2mbtiles(tile_format, auto_mode, preferred):
            try:
                self._run_gdal2mbtiles(source_path, mbtiles_path, tile_format)
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

        if reprojected_path is None:
            reprojected_path = self.reproject_to_webmercator(source_path)
        self._run_gdal_translate(reprojected_path, mbtiles_path, tile_format, quality_value)
        self.optimize_overviews(mbtiles_path)
        return mbtiles_path

    def _should_use_gdal2mbtiles(self, tile_format: str, auto_mode: bool, preferred: bool) -> bool:
        if self._dry_run:
            return False
        if not self._gdal2mbtiles_cmd:
            return False
        if not self._libvips_healthy():
            return False
        if preferred:
            return True
        if not auto_mode:
            return False
        return tile_format == "JPEG"

    def _libvips_healthy(self) -> bool:
        try:
            import pyvips  # noqa
            # 最小動作確認（内部シンボルに触れない安全な操作）
            img = pyvips.Image.black(1, 1)
            _ = (img.width, img.height)
            return True
        except Exception as e:
            LOGGER.warning("pyvips/libvips preflight failed; disable gdal2mbtiles",
                        extra={"error": str(e)})
            return False

    def _run_gdal2mbtiles(self, input_path: Path, destination: Path, tile_format: str) -> None:
        command = list(self._gdal2mbtiles_cmd)
        # Only pass format hint when gdal2mbtiles supports the requested encoding.
        format_map = {"JPEG": "jpg", "PNG": "png"}
        tile_format_lower = format_map.get(tile_format)
        if tile_format_lower:
            command.append(f"--format={tile_format_lower}")
        command.append(f"--max-resolution={self._config.max_zoom}")
        command.extend([str(input_path), str(destination)])
        self._runner.run(command, description="generate MBTiles pyramid via gdal2mbtiles")

    def _resolve_gdal2mbtiles(self) -> list[str] | None:
        candidate = shutil.which("gdal2mbtiles")
        if candidate:
            return [candidate]
        spec = importlib.util.find_spec("gdal2mbtiles.main")
        if spec is not None:
            return [sys.executable, "-m", "gdal2mbtiles"]
        return None

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
            "TILING_SCHEME=GoogleMapsCompatible",
            "-co",
            f"TILE_FORMAT={tile_format}",
            "-co",
            f"QUALITY={quality_value}",
            "-co",
            f"MINZOOM=0",
            "-co",
            f"MAXZOOM={self._config.max_zoom}",
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
