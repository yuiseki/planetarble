"""Geometry and Web Mercator tile helpers for processing."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:  # GDAL is optional at runtime; imported lazily where used
    from osgeo import ogr


ORIGIN_SHIFT = 20037508.342789244


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


def _tile_bounds(x: int, y: int, zoom: int) -> Tuple[float, float, float, float]:
    n = 2**zoom
    tile_size = (ORIGIN_SHIFT * 2) / n
    minx = -ORIGIN_SHIFT + x * tile_size
    maxx = minx + tile_size
    maxy = ORIGIN_SHIFT - y * tile_size
    miny = maxy - tile_size
    return minx, miny, maxx, maxy


def _tile_range(bbox: Tuple[float, float, float, float], zoom: int) -> Tuple[int, int, int, int]:
    min_lon, min_lat, max_lon, max_lat = bbox
    min_lon = max(-180.0, min(180.0, min_lon))
    max_lon = max(-180.0, min(180.0, max_lon))
    min_lat = max(-85.05112878, min(85.05112878, min_lat))
    max_lat = max(-85.05112878, min(85.05112878, max_lat))
    if max_lon < min_lon:
        min_lon, max_lon = max_lon, min_lon
    if max_lat < min_lat:
        min_lat, max_lat = max_lat, min_lat

    epsilon = 1e-9
    x_min = int(math.floor(_lon_to_tile(min_lon, zoom)))
    x_max = int(math.floor(_lon_to_tile(max_lon - epsilon, zoom)))
    y_min = int(math.floor(_lat_to_tile(max_lat - epsilon, zoom)))
    y_max = int(math.floor(_lat_to_tile(min_lat, zoom)))

    n = 2**zoom
    x_min = max(0, min(x_min, n - 1))
    x_max = max(0, min(x_max, n - 1))
    y_min = max(0, min(y_min, n - 1))
    y_max = max(0, min(y_max, n - 1))
    return x_min, x_max, y_min, y_max


def _lon_to_tile(lon: float, zoom: int) -> float:
    n = 2**zoom
    return (lon + 180.0) / 360.0 * n


def _lat_to_tile(lat: float, zoom: int) -> float:
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat)
    n = 2**zoom
    return (1 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2 * n
