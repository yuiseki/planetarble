"""PMTiles-oriented tiling helpers built on GDAL, mb-util, and go-pmtiles."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from planetarble.core.models import ProcessingConfig
from planetarble.logging import get_logger

from .manager import TileCommandError, TileRunner

LOGGER = get_logger(__name__)


@dataclass
class PmtilesMetadata:
    """Metadata payload written for mb-util consumption."""

    name: str
    format: str
    minzoom: int
    maxzoom: int
    bounds: Tuple[float, float, float, float]
    center: Tuple[float, float, int]
    attribution: str

    def to_json(self) -> Dict[str, str]:
        bounds_values = ",".join(f"{value:.6f}" for value in self.bounds)
        center_values = ",".join(
            [f"{self.center[0]:.6f}", f"{self.center[1]:.6f}", str(self.center[2])]
        )
        return {
            "name": self.name,
            "format": self.format,
            "minzoom": str(self.minzoom),
            "maxzoom": str(self.maxzoom),
            "bounds": bounds_values,
            "center": center_values,
            "attribution": self.attribution,
        }


class PmtilesTilingManager:
    """Orchestrate raster → XYZ → MBTiles → PMTiles conversion."""

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
        self._dry_run = dry_run
        self._runner = TileRunner(dry_run=dry_run)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------------
    # XYZ pyramid generation
    # ---------------------------------------------------------------------
    def build_zxy(
        self,
        source_path: Path,
        *,
        min_zoom: int,
        max_zoom: int,
        tile_format: str,
        quality: int,
        resampling: str,
    ) -> Path:
        """Materialize a WebMercator XYZ tile pyramid via ``gdal raster tile``."""

        zxy_dir = self._temp_dir / f"{source_path.stem}_{min_zoom}-{max_zoom}_zxy"
        if zxy_dir.exists() and not self._dry_run:
            shutil.rmtree(zxy_dir)
        if not self._dry_run:
            zxy_dir.mkdir(parents=True, exist_ok=True)

        gdal_format = _gdal_tile_format(tile_format)
        command = [
            "gdal",
            "raster",
            "tile",
            "-i",
            str(source_path),
            "-o",
            str(zxy_dir),
            "--tiling-scheme",
            "WebMercatorQuad",
            "--convention",
            "xyz",
            "--min-zoom",
            str(min_zoom),
            "--max-zoom",
            str(max_zoom),
            "-f",
            gdal_format,
            "-r",
            resampling,
            "--overview-resampling",
            resampling,
            "--config",
            "GDAL_NUM_THREADS",
            self._config.gdal_num_threads,
            "--config",
            "GDAL_CACHEMAX",
            self._config.gdal_cachemax,
        ]
        if gdal_format in {"JPEG", "WEBP"}:
            command.extend(["--co", f"QUALITY={quality}"])

        start = time.perf_counter()
        try:
            self._runner.run(command, description="build XYZ tiles via gdal raster tile")
        except TileCommandError:
            if resampling != "bilinear":
                LOGGER.warning("retrying gdal raster tile with bilinear resampling")
                fallback = command[:]
                _replace_resampling(fallback, "bilinear")
                self._runner.run(
                    fallback,
                    description="build XYZ tiles via gdal raster tile (bilinear retry)",
                )
            else:
                raise
        duration = time.perf_counter() - start
        LOGGER.debug("gdal raster tile finished", extra={"duration_s": f"{duration:.2f}"})
        return zxy_dir

    # ---------------------------------------------------------------------
    # MBTiles packaging
    # ---------------------------------------------------------------------
    def pack_mbtiles(
        self,
        zxy_dir: Path,
        *,
        source_path: Path,
        tile_format: str,
        min_zoom: int,
        max_zoom: int,
        name: str,
        attribution: str,
        bounds_mode: str = "auto",
    ) -> Path:
        """Pack a directory of XYZ tiles into an MBTiles archive."""

        metadata = self._build_metadata(
            source_path,
            tile_format=tile_format,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            name=name,
            attribution=attribution,
            bounds_mode=bounds_mode,
        )
        metadata_path = zxy_dir / "metadata.json"
        if not self._dry_run:
            metadata_path.write_text(json.dumps(metadata.to_json()), encoding="utf-8")

        image_format = _metadata_format(tile_format)
        mbtiles_path = self._temp_dir / f"{source_path.stem}_{min_zoom}-{max_zoom}.mbtiles"
        if mbtiles_path.exists() and not self._dry_run:
            mbtiles_path.unlink()

        command = [
            "mb-util",
            str(zxy_dir),
            str(mbtiles_path),
            "--scheme=xyz",
            f"--image_format={image_format}",
        ]
        self._runner.run(command, description="package XYZ tiles into MBTiles")
        return mbtiles_path

    # ---------------------------------------------------------------------
    # PMTiles conversion + verification
    # ---------------------------------------------------------------------
    def convert_pmtiles(
        self,
        mbtiles_path: Path,
        *,
        destination: Path | None = None,
        deduplicate: bool = True,
        cluster: bool = False,
    ) -> Path:
        """Convert MBTiles into PMTiles via go-pmtiles CLI."""

        pmtiles_path = destination or (self._output_dir / f"{mbtiles_path.stem}.pmtiles")
        if pmtiles_path.exists() and not self._dry_run:
            pmtiles_path.unlink()

        command = ["pmtiles", "convert", str(mbtiles_path), str(pmtiles_path)]
        if not deduplicate:
            command.append("--no-deduplication")
        self._runner.run(command, description="convert MBTiles to PMTiles")

        if cluster:
            cluster_cmd = ["pmtiles", "cluster", str(pmtiles_path)]
            self._runner.run(cluster_cmd, description="cluster PMTiles directory structure")

        return pmtiles_path

    def verify(self, pmtiles_path: Path) -> None:
        """Run ``pmtiles verify`` on the generated archive."""

        command = ["pmtiles", "verify", str(pmtiles_path)]
        self._runner.run(command, description="verify PMTiles archive")

    def show_header(self, pmtiles_path: Path) -> Dict[str, Any]:
        """Return PMTiles header information using ``pmtiles show``."""

        command = ["pmtiles", "show", str(pmtiles_path), "--header-json"]
        LOGGER.info("tiling step", extra={"description": "inspect PMTiles header", "command": " ".join(command)})
        if self._dry_run:
            return {}
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - external tool errors
            raise TileCommandError(f"Command failed: {' '.join(command)}") from exc
        return json.loads(result.stdout)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _build_metadata(
        self,
        source_path: Path,
        *,
        tile_format: str,
        min_zoom: int,
        max_zoom: int,
        name: str,
        attribution: str,
        bounds_mode: str,
    ) -> PmtilesMetadata:
        bounds = self._determine_bounds(source_path, mode=bounds_mode)
        center_lon = (bounds[0] + bounds[2]) / 2.0
        center_lat = (bounds[1] + bounds[3]) / 2.0
        center_zoom = max(min_zoom, min(max_zoom, min_zoom + (max_zoom - min_zoom) // 2))
        return PmtilesMetadata(
            name=name,
            format=_metadata_format(tile_format),
            minzoom=min_zoom,
            maxzoom=max_zoom,
            bounds=bounds,
            center=(center_lon, center_lat, center_zoom),
            attribution=attribution,
        )

    def _determine_bounds(self, source_path: Path, *, mode: str) -> Tuple[float, float, float, float]:
        if self._dry_run:
            return (-180.0, -85.0511, 180.0, 85.0511)
        mode_normalized = mode.lower()
        if mode_normalized == "global":
            return (-180.0, -85.0511, 180.0, 85.0511)
        if mode_normalized != "auto":
            raise ValueError(f"Unsupported bounds mode: {mode}")
        command = ["gdalinfo", "-json", str(source_path)]
        try:
            result = subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - external tool errors
            raise TileCommandError(f"Command failed: {' '.join(command)}") from exc
        info = json.loads(result.stdout)
        corners = info.get("cornerCoordinates") or {}
        coords = []
        for key in ("upperLeft", "upperRight", "lowerRight", "lowerLeft"):
            value = corners.get(key)
            if isinstance(value, dict) and {"lon", "lat"} <= set(value.keys()):
                coords.append((float(value["lon"]), float(value["lat"])))
        if not coords:
            LOGGER.warning("corner coordinates missing; defaulting to global bounds")
            return (-180.0, -85.0511, 180.0, 85.0511)
        lons = [lon for lon, _ in coords]
        lats = [lat for _, lat in coords]
        return (min(lons), min(lats), max(lons), max(lats))


def _metadata_format(tile_format: str) -> str:
    fmt = tile_format.lower()
    return "jpg" if fmt in {"jpeg", "jpg"} else fmt


def _gdal_tile_format(tile_format: str) -> str:
    fmt = tile_format.upper()
    if fmt in {"JPG", "JPEG"}:
        return "JPEG"
    if fmt == "WEBP":
        return "WEBP"
    if fmt == "PNG":
        return "PNG"
    raise ValueError(f"Unsupported tile format for gdal raster tile: {tile_format}")


def _replace_resampling(command: Iterable[str], resampling: str) -> None:
    mapping = {"-r", "--overview-resampling"}
    items = list(command)
    for index, value in enumerate(items):
        if value in mapping:
            if index + 1 < len(items):
                items[index + 1] = resampling
    # mutate original list in place
    if isinstance(command, list):
        command[:] = items
