"""Tests for AOI resolution (ADR 0001, step 2 keystone)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from planetarble.overlay import AOI
from planetarble.overlay.resolve import ResolvedAOI, resolve_aoi
from planetarble.acquisition.miniplanets import miniplanet_geo_bbox

DATA = Path("data")


def test_bbox_without_buffer_is_passthrough_no_gdal() -> None:
    aoi = AOI.from_mapping({"bbox": [139.0, 35.0, 140.0, 36.0]})
    resolved = resolve_aoi(aoi, data_dir=DATA)
    assert isinstance(resolved, ResolvedAOI)
    assert resolved.bbox == (139.0, 35.0, 140.0, 36.0)
    assert resolved.geometry is None  # pure bbox needs no geometry


def test_bbox_buffer_expands_in_degrees() -> None:
    aoi = AOI.from_mapping({"bbox": [139.0, 35.0, 139.0, 35.0], "buffer_km": 11.132})
    resolved = resolve_aoi(aoi, data_dir=DATA)
    minx, miny, maxx, maxy = resolved.bbox
    # 11.132 km ~= 0.1 deg latitude
    assert miny == pytest.approx(35.0 - 0.1, abs=1e-3)
    assert maxy == pytest.approx(35.0 + 0.1, abs=1e-3)
    # longitude buffer is widened by 1/cos(lat)
    expected_dlon = 0.1 / math.cos(math.radians(35.0))
    assert minx == pytest.approx(139.0 - expected_dlon, abs=1e-3)
    assert maxx == pytest.approx(139.0 + expected_dlon, abs=1e-3)


def test_buffer_clamps_to_valid_lonlat() -> None:
    aoi = AOI.from_mapping({"bbox": [-180.0, -90.0, 180.0, 90.0], "buffer_km": 50})
    resolved = resolve_aoi(aoi, data_dir=DATA)
    assert resolved.bbox == (-180.0, -90.0, 180.0, 90.0)


def test_miniplanet_resolves_to_its_geo_bbox_no_gdal() -> None:
    aoi = AOI.from_mapping({"miniplanet": "12"})
    resolved = resolve_aoi(aoi, data_dir=DATA)
    assert resolved.bbox == pytest.approx(miniplanet_geo_bbox("12"))
    assert resolved.geometry is None


def test_land_only_requires_gdal() -> None:
    pytest.importorskip("osgeo")
    aoi = AOI.from_mapping({"bbox": [139.0, 35.0, 140.0, 36.0], "land_only": True})
    # Without a land mask present this raises FileNotFoundError, not a type error,
    # proving the land-clip path is taken.
    with pytest.raises(FileNotFoundError):
        resolve_aoi(aoi, data_dir=Path("/nonexistent"))


def test_natural_earth_requires_gdal_path() -> None:
    pytest.importorskip("osgeo")
    aoi = AOI.from_mapping({"natural_earth": {"dataset": "admin_1", "where": "name='Tokyo'"}})
    with pytest.raises((FileNotFoundError, RuntimeError, ValueError)):
        resolve_aoi(aoi, data_dir=Path("/nonexistent"))
