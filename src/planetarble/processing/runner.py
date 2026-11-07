"""Run external processing commands, with dry-run support."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Sequence

from planetarble.logging import get_logger, log_step

LOGGER = get_logger(__name__)


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


def _cog_command(
    raster_path: Path,
    output: Path,
    *,
    compress: str = "DEFLATE",
    gdal_translate: str = "gdal_translate",
) -> List[str]:
    """gdal_translate command to produce a Cloud Optimized GeoTIFF.

    Uses a lossless compressor so the source nodata=0 (Fmask-masked cloud holes
    over the HLS mosaic) survives exactly and the tiler can turn those holes
    transparent. A lossy JPEG COG both drops the nodata mask (opaque black holes
    over the BMNG floor) and adds 8x8 block artefacts over high-contrast urban
    scenes, which is the bulk of the "dirty" look. The COG here is an
    intermediate; final tiles are re-encoded to WebP at tiling time.
    """
    return [
        gdal_translate,
        "-of",
        "COG",
        "-co",
        f"COMPRESS={compress}",
        str(raster_path),
        str(output),
    ]
