"""Protocol definitions for tile generation components."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class TileGenerator(Protocol):
    """Interface for creating Web Mercator tile pyramids."""

    def reproject_to_webmercator(self, input_path: Path) -> Path:
        """Return a raster transformed to EPSG:3857 with polar clipping."""

    def generate_pyramid(self, input_path: Path, max_zoom: int) -> Path:
        """Return a path to generated tile pyramid artifacts up to max_zoom."""

    def create_mbtiles(self, pyramid_path: Path, format: str, quality: int) -> Path:
        """Return an MBTiles archive generated from the tile pyramid."""

    def optimize_overviews(self, mbtiles_path: Path) -> None:
        """Optimize MBTiles overviews in-place for efficient access."""
