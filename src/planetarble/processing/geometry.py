"""Geometry and Web Mercator tile helpers for processing."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:  # GDAL is optional at runtime; imported lazily where used
    from osgeo import ogr



def bbox_from_geometry(geometry: "ogr.Geometry") -> Tuple[float, float, float, float]:
    minx, maxx, miny, maxy = geometry.GetEnvelope()
    return (float(minx), float(miny), float(maxx), float(maxy))


def clip_land_to_region(
    land_geometry: "ogr.Geometry",
    region_geometry: Optional["ogr.Geometry"],
) -> "ogr.Geometry":
    """Clip land features to the plan region before deriving a search bbox.

    ne_10m_land features that merely intersect a small region are returned
    whole (continent sized), so their raw envelope would inflate the STAC
    search bbox to near-global coverage.
    """
    if region_geometry is None:
        return land_geometry
    clipped = land_geometry.Intersection(region_geometry)
    if clipped is None or clipped.IsEmpty():
        return land_geometry
    return clipped


def write_geometry_geojson(geometry: "ogr.Geometry", path: Path) -> None:
    try:
        from osgeo import (
            ogr,
            osr,
        )
    except Exception as exc:  # pragma: no cover - depends on GDAL availability
        raise RuntimeError("GDAL (osgeo.ogr) is required for region clipping") from exc
    driver = ogr.GetDriverByName("GeoJSON")
    if driver is None:
        raise RuntimeError("GeoJSON driver not available in GDAL")
    if path.exists():
        path.unlink()
    dataset = driver.CreateDataSource(str(path))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    layer = dataset.CreateLayer("region", srs=srs, geom_type=ogr.wkbMultiPolygon)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry)
    layer.CreateFeature(feature)
    feature = None
    dataset = None
