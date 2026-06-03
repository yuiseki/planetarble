"""Tests for clipping land geometry to a Sentinel-2 plan region."""

from __future__ import annotations

import pytest


def _box(minx: float, miny: float, maxx: float, maxy: float):
    from osgeo import ogr

    ring = ogr.Geometry(ogr.wkbLinearRing)
    for x, y in [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]:
        ring.AddPoint(x, y)
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    return poly


def test_clip_land_to_region_bounds_search_bbox() -> None:
    # Regression: ne_10m_land features intersecting a small region are returned
    # whole (continent sized), so taking their raw envelope inflated the
    # Sentinel-2 STAC search bbox to near-global coverage (the search returned
    # Antarctic granules for a Tokyo plan region). Land geometry must be
    # clipped to the region before deriving the bbox.
    pytest.importorskip("osgeo")
    from planetarble.processing.manager import _bbox_from_geometry, _clip_land_to_region

    land = _box(60.0, -10.0, 180.0, 80.0)        # continent-sized land feature
    region = _box(139.55, 35.50, 139.92, 35.82)  # tokyo_bbox plan region

    clipped = _clip_land_to_region(land, region)

    assert _bbox_from_geometry(clipped) == pytest.approx((139.55, 35.50, 139.92, 35.82))


def test_clip_land_to_region_without_region_returns_land() -> None:
    pytest.importorskip("osgeo")
    from planetarble.processing.manager import _bbox_from_geometry, _clip_land_to_region

    land = _box(0.0, 0.0, 10.0, 10.0)

    clipped = _clip_land_to_region(land, None)

    assert _bbox_from_geometry(clipped) == pytest.approx((0.0, 0.0, 10.0, 10.0))


def test_clip_land_to_region_disjoint_falls_back_to_land() -> None:
    # A region that misses land entirely must not produce an empty geometry.
    pytest.importorskip("osgeo")
    from planetarble.processing.manager import _bbox_from_geometry, _clip_land_to_region

    land = _box(0.0, 0.0, 10.0, 10.0)
    region = _box(120.0, 30.0, 121.0, 31.0)

    clipped = _clip_land_to_region(land, region)

    assert _bbox_from_geometry(clipped) == pytest.approx((0.0, 0.0, 10.0, 10.0))
