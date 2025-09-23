"""Protocol definitions for data acquisition components."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from planetarble.core.models import AssetManifest


class DataAcquisition(Protocol):
    """Interface for downloading and validating source datasets."""

    def download_bmng(self, resolution: str = "500m", force: bool = False) -> Path:
        """Download NASA BMNG imagery at the requested resolution."""

    def download_gebco(self, year: int = 2025, force: bool = False) -> Path:
        """Download the GEBCO bathymetry grid for the specified year."""

    def download_natural_earth(self, scale: str = "10m", force: bool = False) -> Path:
        """Download Natural Earth coastline and mask data at the given scale."""

    def verify_checksums(self, manifest: AssetManifest) -> bool:
        """Return True when downloaded files match the manifest checksums."""

    def generate_manifest(self) -> AssetManifest:
        """Produce an asset manifest describing acquired datasets."""
