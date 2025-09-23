"""Protocol definitions for PMTiles packaging components."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from planetarble.core.models import TileMetadata


class PackagingManager(Protocol):
    """Interface for converting tiles into distribution artifacts."""

    def convert_to_pmtiles(self, mbtiles_path: Path) -> Path:
        """Return the generated PMTiles archive path."""

    def generate_tilejson(self, pmtiles_path: Path, metadata: TileMetadata) -> Path:
        """Return the TileJSON metadata file associated with the PMTiles archive."""

    def create_distribution_package(self, pmtiles_path: Path) -> Path:
        """Return a bundled distribution directory or archive for deployment."""
