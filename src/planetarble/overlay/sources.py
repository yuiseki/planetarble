"""Source registry and the pluggable source-adapter protocol (ADR 0001).

Each imagery source planetarble can read is described by a ``SourceInfo`` in
``SOURCE_REGISTRY`` (its advertised Web Mercator zoom ceiling, kept in sync with
docs/sources/) and, eventually, backed by a concrete ``SourceAdapter``. Step 1 ships
the registry and protocol so the declarative pipeline spec can be parsed and
validated; the concrete adapters are wired in later steps without changing the
orchestrator.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Protocol, Tuple, runtime_checkable


@dataclass(frozen=True)
class SourceInfo:
    """Static description of an imagery source."""

    name: str
    native_max_zoom: int
    resolution_varies: bool = False
    note: str = ""
    # Credit line; may contain {placeholders} filled from the overlay's
    # source_options, falling back to attribution_defaults.
    attribution: str = ""
    attribution_defaults: Tuple[Tuple[str, str], ...] = ()

    def credit(self, options: Mapping[str, Any]) -> str:
        values = dict(self.attribution_defaults)
        values.update({k: str(v) for k, v in options.items()})
        return string.Formatter().vformat(self.attribution, (), _KeepMissing(values))


class _KeepMissing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# Zoom ceilings mirror the max_zoom of each docs/sources/ page (tested); these are the data-justified
# maxima used to reject silent oversampling, independent of operational choices
# (e.g. HLS is computed z12 but often served at z11).
SOURCE_REGISTRY: Dict[str, SourceInfo] = {
    "bmng": SourceInfo("bmng", 8, note="500m panels; 2km single frame supports z6", attribution="Image courtesy NASA Earth Observatory"),
    "hls": SourceInfo("hls", 12, note="HLS v2 S30/L30, 30m", attribution="HLS v2.0 (NASA/USGS)"),
    "landsat-c2-l2": SourceInfo("landsat-c2-l2", 12, note="Landsat C2 L2 SR, 30m (HLS fallback)", attribution="Landsat imagery courtesy of the U.S. Geological Survey"),
    "sentinel2": SourceInfo("sentinel2", 14, note="Sentinel-2 L2A, 10m", attribution="Contains modified Copernicus Sentinel data [year]"),
    "copernicus": SourceInfo("copernicus", 14, note="Copernicus WMS, Sentinel-2 derived, 10m", attribution="Contains modified Copernicus Sentinel data [year]"),
    "gsi_orthophotos": SourceInfo("gsi_orthophotos", 18, note="GSI aerial photos (Japan only)", attribution="Imagery: Geospatial Information Authority of Japan (GSI) Seamless Orthophotography"),
    "modis": SourceInfo("modis", 8, note="MODIS MCD43A4, 500m", attribution="Imagery: NASA MODIS MCD43A4 (LP DAAC)"),
    "viirs": SourceInfo("viirs", 8, note="VIIRS VNP09GA, 500m/1km", attribution="Imagery: NASA VIIRS Corrected Reflectance (LP DAAC)"),
    "openaerialmap": SourceInfo(
        "openaerialmap", 22, resolution_varies=True, note="OAM orthophotos, sub-meter, varies per scene"
    , attribution="OpenAerialMap contributors (CC BY 4.0)"),
    "esa-worldcover": SourceInfo(
        "esa-worldcover",
        14,
        note="ESA WorldCover Sentinel-2 RGBNIR annual median composite, 10m, 2020 or 2021",
        attribution=(
            "© ESA WorldCover project {year} / Contains modified Copernicus Sentinel data ({year}) "
            "processed by ESA WorldCover consortium"
        ),
        attribution_defaults=(("year", "2021"),),
    ),
    "naip": SourceInfo(
        "naip",
        18,
        resolution_varies=True,
        note="USDA NAIP aerial imagery via Planetary Computer, 0.3 to 1m, contiguous US",
        attribution="NAIP imagery courtesy of USDA Farm Service Agency",
    ),
    "nz-imagery": SourceInfo(
        "nz-imagery",
        21,
        resolution_varies=True,
        note="New Zealand aerial and satellite imagery from LINZ, 0.05 to 10m, per survey",
        attribution="Imagery: Toitū Te Whenua Land Information New Zealand and the survey licensors (CC BY 4.0)",
    ),
    # Generic: COG URLs given in the spec. No default credit; the overlay
    # must state one (validated). The ceiling is only an upper guard.
    "cog": SourceInfo("cog", 22, resolution_varies=True, note="any openly licensed COG(s) named by URL"),
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
