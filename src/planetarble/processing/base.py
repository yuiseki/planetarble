"""Protocol definitions for raster processing components."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class DataProcessor(Protocol):
    """Interface for transforming raw datasets into tile-ready rasters."""

    def normalize_bmng(self, input_path: Path) -> Path:
        """Return a normalized BMNG raster prepared for blending."""

    def generate_hillshade(self, gebco_path: Path) -> Path:
        """Return a hillshade raster derived from GEBCO bathymetry."""

    def create_masks(self, natural_earth_path: Path) -> Path:
        """Return a mask dataset delineating land and ocean regions."""

    def create_cog(self, raster_path: Path) -> Path:
        """Convert an input raster to a Cloud Optimized GeoTIFF."""

    def blend_layers(self, base: Path, overlay: Path, opacity: float) -> Path:
        """Blend overlay content onto the base raster using an opacity factor."""
