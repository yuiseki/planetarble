"""Source registry and the pluggable source-adapter protocol (ADR 0001).

Each imagery source planetarble can read is described by a ``SourceInfo`` in
``SOURCE_REGISTRY`` (its advertised Web Mercator zoom ceiling, kept in sync with
SOURCE.md) and, eventually, backed by a concrete ``SourceAdapter``. Step 1 ships
the registry and protocol so the declarative pipeline spec can be parsed and
validated; the concrete adapters are wired in later steps without changing the
orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Protocol, runtime_checkable


@dataclass(frozen=True)
class SourceInfo:
    """Static description of an imagery source."""

    name: str
    native_max_zoom: int
    resolution_varies: bool = False
    note: str = ""


# Zoom ceilings mirror SOURCE.md ("Max zoom"); these are the data-justified
# maxima used to reject silent oversampling, independent of operational choices
# (e.g. HLS is computed z12 but often served at z11).
SOURCE_REGISTRY: Dict[str, SourceInfo] = {
    "bmng": SourceInfo("bmng", 8, note="500m panels; 2km single frame supports z6"),
    "hls": SourceInfo("hls", 12, note="HLS v2 S30/L30, 30m"),
    "landsat-c2-l2": SourceInfo("landsat-c2-l2", 12, note="Landsat C2 L2 SR, 30m (HLS fallback)"),
    "sentinel2": SourceInfo("sentinel2", 14, note="Sentinel-2 L2A, 10m"),
    "copernicus": SourceInfo("copernicus", 14, note="Copernicus WMS, Sentinel-2 derived, 10m"),
    "gsi_orthophotos": SourceInfo("gsi_orthophotos", 18, note="GSI aerial photos (Japan only)"),
    "modis": SourceInfo("modis", 8, note="MODIS MCD43A4, 500m"),
    "viirs": SourceInfo("viirs", 8, note="VIIRS VNP09GA, 500m/1km"),
    "openaerialmap": SourceInfo(
        "openaerialmap", 22, resolution_varies=True, note="OAM orthophotos, sub-meter, varies per scene"
    ),
}


def known_sources() -> List[str]:
    return sorted(SOURCE_REGISTRY)


@runtime_checkable
class SourceAdapter(Protocol):
    """Protocol every imagery source implements behind the orchestrator.

    Concrete adapters arrive in later steps; existing per-source code (HLS
    planner/manifest builder, Sentinel-2 builder, GSI fetch, BMNG mosaic) is
    refactored to sit behind this rather than rewritten.
    """

    name: str

    def native_max_zoom(self, aoi: "object") -> int:
        """Advertised resolution ceiling for the given AOI."""

    def plan(self, aoi: "object", zoom_range: "object") -> "object":
        """Enumerate the work needed to cover the AOI (tiles/scenes/footprints)."""

    def build_raster(self, plan: "object", workspace: "object") -> "object":
        """Produce a COG or MBTiles for the AOI from a plan."""
