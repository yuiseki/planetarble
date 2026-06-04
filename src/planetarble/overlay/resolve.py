"""Resolve an AOI into a geographic bbox and (when needed) an OGR geometry.

This is the shared foundation every source adapter and the orchestrator use to
turn a declarative AOI (bbox / natural_earth / miniplanet / geojson, with
optional buffer_km and land_only) into something a planner can clip tiles
against. Pure selectors (bbox, miniplanet) with an optional buffer need no GDAL;
natural_earth, geojson, and land_only resolve through the existing GDAL helpers
in ``planetarble.acquisition.hls`` and ``planetarble.processing.manager``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from planetarble.acquisition.miniplanets import miniplanet_geo_bbox

from .spec import AOI

BBox = Tuple[float, float, float, float]

_KM_PER_DEG_LAT = 111.32


@dataclass
class ResolvedAOI:
    """A resolved area of interest.

    ``bbox`` is always populated (the search/enumeration extent). ``geometry``
    is an ``ogr.Geometry`` when a precise clip is required (natural_earth,
    geojson, or land_only) and ``None`` for a plain rectangular AOI.
    """

    bbox: BBox
    geometry: Optional[object] = None


def _buffer_bbox(bbox: BBox, buffer_km: float) -> BBox:
    if buffer_km <= 0:
        return bbox
    minx, miny, maxx, maxy = bbox
    dlat = buffer_km / _KM_PER_DEG_LAT
    mid_lat = math.radians((miny + maxy) / 2.0)
    dlon = dlat / max(math.cos(mid_lat), 0.01)
    return (
        max(minx - dlon, -180.0),
        max(miny - dlat, -90.0),
        min(maxx + dlon, 180.0),
        min(maxy + dlat, 90.0),
    )


def _base_bbox(aoi: AOI) -> Optional[BBox]:
    if aoi.bbox is not None:
        return aoi.bbox
    if aoi.miniplanet is not None:
        return miniplanet_geo_bbox(aoi.miniplanet)
    return None


def resolve_aoi(
    aoi: AOI,
    *,
    data_dir: Path,
    land_mask_path: Optional[str] = None,
) -> ResolvedAOI:
    needs_geometry = aoi.natural_earth is not None or aoi.geojson is not None or aoi.land_only

    if not needs_geometry:
        base = _base_bbox(aoi)
        if base is None:  # pragma: no cover - guarded by AOI validation
            raise ValueError("AOI has no resolvable extent")
        return ResolvedAOI(bbox=_buffer_bbox(base, aoi.buffer_km), geometry=None)

    # GDAL path: build a geometry, buffer it, optionally clip to land.
    from planetarble.acquisition.hls import (  # local import keeps the pure path GDAL-free
        _bbox_to_geometry,
        load_land_geometry,
        load_region_geometry,
    )
    from planetarble.core.models import HLSPlanRegion, NaturalEarthRegion
    from planetarble.processing.manager import _bbox_from_geometry, _clip_land_to_region

    if aoi.natural_earth is not None:
        region = HLSPlanRegion(
            name="aoi",
            natural_earth=NaturalEarthRegion(
                dataset=str(aoi.natural_earth.get("dataset", "")),
                where=str(aoi.natural_earth.get("where", "")),
                path=aoi.natural_earth.get("path"),
            ),
        )
        geometry = load_region_geometry(region, data_dir=data_dir)
    elif aoi.geojson is not None:
        geometry = _load_geojson_geometry(Path(aoi.geojson))
    else:
        base = _base_bbox(aoi)
        if base is None:  # pragma: no cover - guarded by AOI validation
            raise ValueError("AOI has no resolvable extent")
        geometry = _bbox_to_geometry(_buffer_bbox(base, aoi.buffer_km))

    if geometry is None:
        raise ValueError("failed to resolve AOI geometry")

    if aoi.buffer_km > 0 and (aoi.natural_earth is not None or aoi.geojson is not None):
        geometry = geometry.Buffer(aoi.buffer_km / _KM_PER_DEG_LAT)

    if aoi.land_only:
        land = load_land_geometry(
            land_mask_path=land_mask_path,
            data_dir=data_dir,
            region_geometry=geometry,
        )
        geometry = _clip_land_to_region(land, geometry)

    return ResolvedAOI(bbox=_bbox_from_geometry(geometry), geometry=geometry)


def _load_geojson_geometry(path: Path) -> object:
    from osgeo import ogr  # type: ignore

    try:
        ogr.UseExceptions()
    except AttributeError:  # pragma: no cover - older GDAL
        pass
    dataset = ogr.Open(path.as_posix(), 0)
    if dataset is None:
        raise FileNotFoundError(f"unable to open GeoJSON AOI: {path}")
    union = None
    for layer in (dataset.GetLayer(i) for i in range(dataset.GetLayerCount())):
        for feature in layer:
            geom = feature.GetGeometryRef()
            if geom is None:
                continue
            clone = geom.Clone()
            union = clone if union is None else union.Union(clone)
    if union is None:
        raise ValueError(f"no geometry found in GeoJSON AOI: {path}")
    return union
