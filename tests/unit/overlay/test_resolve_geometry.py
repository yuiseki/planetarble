"""AOI resolution through GDAL: GeoJSON, Natural Earth, buffering, land clip.

Uses tiny GeoJSON fixtures written to tmp_path; skipped without GDAL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import pytest

pytest.importorskip("osgeo")

from planetarble.overlay import AOI
from planetarble.overlay.resolve import resolve_aoi
from planetarble.processing.geometry import write_geometry_geojson

Box = Tuple[float, float, float, float]


def _square(box: Box) -> List[List[List[float]]]:
    minx, miny, maxx, maxy = box
    return [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]]


def _feature_collection(path: Path, *features: Tuple[Box, dict]) -> Path:
    path.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": _square(box)}}
            for box, props in features
        ],
    }))
    return path


def test_geojson_aoi_is_the_union_of_its_features(tmp_path: Path) -> None:
    path = _feature_collection(tmp_path / "aoi.geojson", ((0, 0, 1, 1), {}), ((2, 2, 3, 4), {}))
    resolved = resolve_aoi(AOI.from_mapping({"geojson": str(path)}), data_dir=tmp_path)
    assert resolved.bbox == pytest.approx((0, 0, 3, 4))
    assert resolved.geometry is not None
    assert resolved.geometry.Area() == pytest.approx(1 + 2)  # type: ignore[attr-defined]


def test_geojson_aoi_buffer_grows_the_geometry(tmp_path: Path) -> None:
    path = _feature_collection(tmp_path / "aoi.geojson", ((10, 10, 11, 11), {}))
    resolved = resolve_aoi(AOI.from_mapping({"geojson": str(path), "buffer_km": 111.32}), data_dir=tmp_path)
    # buffered by about one degree on every side
    assert resolved.bbox == pytest.approx((9, 9, 12, 12), abs=0.01)


def test_missing_geojson_is_reported(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="nope.geojson"):
        resolve_aoi(AOI.from_mapping({"geojson": str(tmp_path / "nope.geojson")}), data_dir=tmp_path)


def test_geojson_without_geometry_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": None}]}))
    with pytest.raises(ValueError, match="no geometry"):
        resolve_aoi(AOI.from_mapping({"geojson": str(path)}), data_dir=tmp_path)


def test_land_only_clips_the_aoi_to_the_land_mask(tmp_path: Path) -> None:
    land = _feature_collection(tmp_path / "ne_10m_land.geojson", ((1, 1, 3, 3), {}))
    aoi = AOI.from_mapping({"bbox": [0, 0, 2, 2], "land_only": True})
    resolved = resolve_aoi(aoi, data_dir=tmp_path, land_mask_path=str(land))
    assert resolved.bbox == pytest.approx((1, 1, 2, 2))


def test_land_only_default_mask_is_the_zipped_natural_earth_download(tmp_path: Path) -> None:
    (tmp_path / "natural_earth").mkdir()
    _feature_collection(tmp_path / "natural_earth" / "ne_10m_land.geojson", ((1, 1, 3, 3), {}))
    aoi = AOI.from_mapping({"bbox": [0, 0, 2, 2], "land_only": True})
    with pytest.raises(FileNotFoundError, match="ne_10m_land.zip"):
        # the default is the zipped Natural Earth download, not a loose GeoJSON
        resolve_aoi(aoi, data_dir=tmp_path)


def test_natural_earth_where_selects_features(tmp_path: Path) -> None:
    states = _feature_collection(
        tmp_path / "ne_10m_admin_1_states_provinces.geojson",
        ((0, 0, 1, 1), {"name": "A"}), ((5, 5, 7, 6), {"name": "B"}),
    )
    aoi = AOI.from_mapping({"natural_earth": {"dataset": "admin_1", "where": "name = 'B'", "path": str(states)}})
    assert resolve_aoi(aoi, data_dir=tmp_path).bbox == pytest.approx((5, 5, 7, 6))


def test_natural_earth_where_without_matches_is_an_error(tmp_path: Path) -> None:
    states = _feature_collection(tmp_path / "ne_10m_admin_1_states_provinces.geojson", ((0, 0, 1, 1), {"name": "A"}))
    aoi = AOI.from_mapping({"natural_earth": {"dataset": "admin_1", "where": "name = 'Z'", "path": str(states)}})
    with pytest.raises(RuntimeError, match="name = 'Z'"):
        resolve_aoi(aoi, data_dir=tmp_path)


def test_write_geometry_geojson_round_trips_and_overwrites(tmp_path: Path) -> None:
    from osgeo import ogr

    out = tmp_path / "region.geojson"
    out.write_text("stale")
    geometry = ogr.CreateGeometryFromWkt("POLYGON ((0 0, 2 0, 2 1, 0 1, 0 0))")

    write_geometry_geojson(geometry, out)

    data = json.loads(out.read_text())
    (feature,) = data["features"]
    assert feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    reread = ogr.CreateGeometryFromJson(json.dumps(feature["geometry"]))
    assert reread.Area() == pytest.approx(2.0)
