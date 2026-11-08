"""GSI orthophoto clips: tile math, tile download/cache, and the GDAL steps.

HTTP and GDAL are faked; nothing is fetched from cyberjapandata.gsi.go.jp.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, List

import pytest

from planetarble.acquisition import gsi
from planetarble.acquisition.gsi import (
    ORIGIN_SHIFT,
    GSIError,
    _format_duration,
    _normalize_bbox,
    _tile_bounds_mercator,
    _tiles_for_bbox,
    fetch_gsi_ortho_clip,
)

TEMPLATE = "https://tiles.invalid/{z}/{x}/{y}.jpg"


def _merc_x(lon: float) -> float:
    return lon * ORIGIN_SHIFT / 180.0


def _merc_y(lat: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * ORIGIN_SHIFT / math.pi


# --- tile math ----------------------------------------------------------------


def test_zoom_zero_tile_covers_the_whole_mercator_plane() -> None:
    assert _tile_bounds_mercator(0, 0, 0) == pytest.approx((-ORIGIN_SHIFT, -ORIGIN_SHIFT, ORIGIN_SHIFT, ORIGIN_SHIFT))


def test_quadrants_at_zoom_one() -> None:
    assert _tiles_for_bbox((-10, 10, -5, 20), 1) == [(1, 0, 0)]  # north-west
    assert _tiles_for_bbox((5, -20, 10, -10), 1) == [(1, 1, 1)]  # south-east


@pytest.mark.parametrize(("lon", "lat", "zoom"), [(139.7671, 35.6812, 18), (-122.4194, 37.7749, 16), (174.7762, -41.2865, 19)])
def test_the_tile_found_for_a_point_contains_it(lon: float, lat: float, zoom: int) -> None:
    ((z, x, y),) = _tiles_for_bbox((lon, lat, lon, lat), zoom)
    minx, miny, maxx, maxy = _tile_bounds_mercator(x, y, z)
    assert minx <= _merc_x(lon) < maxx
    assert miny < _merc_y(lat) <= maxy


def test_bbox_tiles_form_a_full_grid() -> None:
    tiles = _tiles_for_bbox((139.70, 35.65, 139.80, 35.70), 15)
    xs = sorted({t[1] for t in tiles})
    ys = sorted({t[2] for t in tiles})
    assert len(tiles) == len(xs) * len(ys)
    assert xs == list(range(xs[0], xs[-1] + 1)) and ys == list(range(ys[0], ys[-1] + 1))


def test_bbox_is_clamped_to_web_mercator() -> None:
    tiles = _tiles_for_bbox((-200, -89, 200, 89), 1)
    assert sorted(tiles) == [(1, 0, 0), (1, 0, 1), (1, 1, 0), (1, 1, 1)]


def test_reversed_bbox_yields_no_tiles() -> None:
    assert _tiles_for_bbox((10, 0, 5, 1), 5) == []


def test_normalize_bbox_orders_corners() -> None:
    assert _normalize_bbox((3, 4, 1, 2)) == (1, 2, 3, 4)


@pytest.mark.parametrize(("seconds", "text"), [(0, "00:00"), (65.9, "01:05"), (3725, "01:02:05"), (-3, "00:00")])
def test_format_duration(seconds: float, text: str) -> None:
    assert _format_duration(seconds) == text


# --- download -----------------------------------------------------------------


class FakeSession:
    requested: List[str] = []
    status = 200

    def get(self, url, timeout):
        FakeSession.requested.append(url)
        return type("R", (), {"status_code": FakeSession.status, "content": b"JPEG" + url.encode()})()


@pytest.fixture
def fake_http(monkeypatch) -> List[float]:
    FakeSession.requested = []
    FakeSession.status = 200
    sleeps: List[float] = []
    monkeypatch.setattr(gsi.requests, "Session", FakeSession)
    monkeypatch.setattr(gsi.time, "sleep", sleeps.append)
    return sleeps


def test_tiles_are_stored_as_z_x_y_and_rate_limited(tmp_path: Path, fake_http: List[float]) -> None:
    tiles = gsi._download_tiles(
        tmp_dir=tmp_path, tile_template=TEMPLATE, tile_triplets=[(18, 1, 2), (18, 1, 3)], timeout=5,
        rate_limit_seconds=0.25,
    )
    assert [t.path for t in tiles] == [tmp_path / "18/1/2.jpg", tmp_path / "18/1/3.jpg"]
    assert [t.vrt_path for t in tiles] == [tmp_path / "18/1/2.vrt", tmp_path / "18/1/3.vrt"]
    assert (tmp_path / "18/1/2.jpg").read_bytes() == b"JPEG" + TEMPLATE.format(z=18, x=1, y=2).encode()
    assert fake_http == [0.25, 0.25]


def test_cached_tiles_are_not_requested_again(tmp_path: Path, fake_http: List[float]) -> None:
    cached = tmp_path / "18/1/2.jpg"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"old")
    (tmp_path / "18/1/3.jpg").write_bytes(b"")  # an empty file is a failed earlier download

    gsi._download_tiles(
        tmp_dir=tmp_path, tile_template=TEMPLATE, tile_triplets=[(18, 1, 2), (18, 1, 3)], timeout=5,
        rate_limit_seconds=0.25,
    )

    assert FakeSession.requested == [TEMPLATE.format(z=18, x=1, y=3)]
    assert cached.read_bytes() == b"old"
    assert fake_http == [0.25]


def test_http_error_stops_the_download(tmp_path: Path, fake_http) -> None:
    FakeSession.status = 404
    with pytest.raises(GSIError, match="404"):
        gsi._download_tiles(
            tmp_dir=tmp_path, tile_template=TEMPLATE, tile_triplets=[(18, 1, 2)], timeout=5, rate_limit_seconds=0
        )


# --- the whole clip -----------------------------------------------------------


@pytest.fixture
def fake_gdal(monkeypatch) -> List[List[str]]:
    commands: List[List[str]] = []
    monkeypatch.setattr(gsi, "_run", lambda command, description: commands.append(list(command)))
    return commands


def test_dry_run_lists_tile_urls_without_downloading(tmp_path: Path, fake_http, fake_gdal) -> None:
    bbox = (139.765, 35.680, 139.770, 35.683)
    result = fetch_gsi_ortho_clip(
        lat=0, lon=0, width_m=1, height_m=1, bbox=bbox, output_path=tmp_path / "o.tif", zoom=17,
        tile_template=TEMPLATE, dry_run=True,
    )
    expected = [TEMPLATE.format(z=z, x=x, y=y) for z, x, y in _tiles_for_bbox(bbox, 17)]
    assert result["urls"] == expected
    assert result["tiles"] == len(expected)
    assert FakeSession.requested == [] and fake_gdal == []


def test_clip_georeferences_mosaics_and_warps_into_the_cache(tmp_path: Path, fake_http, fake_gdal) -> None:
    bbox = (139.765, 35.680, 139.770, 35.683)
    cache = tmp_path / "cache"
    out = tmp_path / "out" / "clip.tif"

    result = fetch_gsi_ortho_clip(
        lat=0, lon=0, width_m=1, height_m=1, bbox=bbox, output_path=out, zoom=17, tile_template=TEMPLATE,
        cache_dir=cache, rate_limit_seconds=0,
    )

    n = len(_tiles_for_bbox(bbox, 17))
    assert result == {"output": str(out.resolve()), "bbox": bbox, "zoom": 17, "tiles": n}
    translates, (buildvrt, warp) = fake_gdal[:n], fake_gdal[n:]
    assert all(c[0] == "gdal_translate" and "-a_ullr" in c and c[c.index("-a_srs") + 1] == "EPSG:3857"
               for c in translates)
    assert buildvrt == ["gdalbuildvrt", "-input_file_list", str(cache / "tiles.txt"), str(cache / "mosaic.vrt")]
    assert len((cache / "tiles.txt").read_text().splitlines()) == n
    assert warp[0] == "gdalwarp"
    assert warp[warp.index("-te") + 1 : warp.index("-te") + 5] == [str(v) for v in bbox]
    assert warp[-2:] == [str(cache / "mosaic.vrt"), str(out.resolve())]


def test_georeference_uses_the_tile_corners(tmp_path: Path, fake_gdal) -> None:
    tile = gsi.GSITile(z=1, x=1, y=0, url="u", path=tmp_path / "a.jpg", vrt_path=tmp_path / "a.vrt")
    gsi._georeference_tiles(tiles=[tile], gdal_translate="gt")
    (cmd,) = fake_gdal
    i = cmd.index("-a_ullr")
    ulx, uly, lrx, lry = map(float, cmd[i + 1 : i + 5])
    assert (ulx, uly, lrx, lry) == pytest.approx((0.0, ORIGIN_SHIFT, ORIGIN_SHIFT, 0.0))


def test_clip_without_cache_uses_a_temporary_directory(tmp_path: Path, fake_http, fake_gdal) -> None:
    fetch_gsi_ortho_clip(
        lat=35.681, lon=139.767, width_m=50, height_m=50, output_path=tmp_path / "o.tif", zoom=16,
        tile_template=TEMPLATE, rate_limit_seconds=0,
    )
    buildvrt: Any = fake_gdal[-2]
    assert "planetarble_gsi_" in buildvrt[-1]
    assert not Path(buildvrt[-1]).exists()  # cleaned up afterwards


@pytest.mark.parametrize("zoom", [0, 1, 5, 18])
@pytest.mark.parametrize("lat", [-85.0511, 85.0511])
def test_tile_indices_stay_inside_the_grid_at_the_edges(zoom: int, lat: float) -> None:
    n = 1 << zoom
    # a tiny box in the corner of the grid: the antimeridian and a pole limit
    tiles = _tiles_for_bbox((179.9999, lat - 0.0001, 180.0, lat + 0.0001), zoom)
    assert tiles
    for _z, x, y in tiles:
        assert 0 <= x < n and 0 <= y < n
