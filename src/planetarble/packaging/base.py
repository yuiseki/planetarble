"""Protocol definitions for PMTiles packaging components."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol, Tuple

from planetarble.core.models import TileMetadata


class PackagingManager(Protocol):
    """Interface for converting tiles into distribution artifacts."""

    def convert_to_pmtiles(self, mbtiles_path: Path) -> Path:
        """Return the generated PMTiles archive path."""

    def set_header_center(self, pmtiles_path: Path, center: Tuple[float, float, int]) -> None:
        """Write ``center`` (lon, lat, zoom) into the PMTiles header."""

    def generate_tilejson(self, pmtiles_path: Path, metadata: TileMetadata) -> Path:
        """Return the TileJSON metadata file associated with the PMTiles archive."""

    def create_distribution_package(
        self,
        pmtiles_path: Path,
        *,
        tilejson_path: Path,
        manifest_path: Path,
        license_text: str,
        destination: Optional[Path] = None,
    ) -> Path:
        """Return a bundled distribution directory or archive for deployment."""
