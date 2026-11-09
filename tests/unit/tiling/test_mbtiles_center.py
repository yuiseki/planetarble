"""Every MBTiles the tile stage writes carries a `center` row.

GDAL's MBTiles driver writes `bounds` but no `center` unless asked, and
`pmtiles convert` (go-pmtiles <= 1.31.2) then derives the center with an int32
overflow for bounds east of ~107.4 E or west of ~107.4 W
(https://github.com/protomaps/go-pmtiles/issues/297). Writing the center
ourselves sidesteps it and gives viewers a sensible default view.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

import planetarble.tiling.manager as tm
from planetarble.core.models import ProcessingConfig
from planetarble.tiling.mbtiles import default_center, ensure_center

TOKYO_LAND = (138.9228178, 24.2120253, 153.9857603, 35.8788697)


def _mbtiles(path: Path, **meta: str) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name text, value text)")
    conn.executemany("INSERT INTO metadata VALUES (?, ?)", meta.items())
    conn.commit()
    conn.close()
    return path


def _meta(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT name, value FROM metadata"))
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("bounds", "minzoom", "maxzoom", "expected"),
    [
        (TOKYO_LAND, 11, 11, (146.454289, 30.0454475, 11)),  # z2 raised to the only zoom
        ((-180.0, -85.0511, 180.0, 85.0511), 0, 8, (0.0, 0.0, 2)),  # global keeps z2
        ((-180.0, -85.0511, 180.0, 85.0511), 0, 1, (0.0, 0.0, 1)),  # z2 capped at maxzoom
    ],
)
def test_default_center_is_the_middle_of_the_bounds(bounds, minzoom, maxzoom, expected) -> None:
    lon, lat, zoom = default_center(bounds, minzoom, maxzoom)
    assert (lon, lat) == pytest.approx(expected[:2])
    assert zoom == expected[2]


def test_ensure_center_writes_it_from_the_bounds(tmp_path: Path) -> None:
    path = _mbtiles(tmp_path / "t.mbtiles", bounds=",".join(map(str, TOKYO_LAND)), minzoom="11", maxzoom="11")
    ensure_center(path)
    assert _meta(path)["center"] == "146.4542890,30.0454475,11"


def test_ensure_center_keeps_a_declared_center(tmp_path: Path) -> None:
    path = _mbtiles(tmp_path / "t.mbtiles", bounds="139,35,140,36", minzoom="5", maxzoom="12", center="139.7,35.6,10")
    ensure_center(path)
    assert _meta(path)["center"] == "139.7,35.6,10"


def test_ensure_center_without_bounds_or_zooms_does_nothing(tmp_path: Path) -> None:
    no_bounds = _mbtiles(tmp_path / "a.mbtiles", minzoom="5", maxzoom="5")
    no_zooms = _mbtiles(tmp_path / "b.mbtiles", bounds="139,35,140,36")
    ensure_center(no_bounds)
    ensure_center(no_zooms)
    assert "center" not in _meta(no_bounds)
    assert "center" not in _meta(no_zooms)


def test_ensure_center_on_a_missing_file_creates_nothing(tmp_path: Path) -> None:
    ensure_center(tmp_path / "missing.mbtiles")
    assert not (tmp_path / "missing.mbtiles").exists()


@pytest.mark.parametrize(("gdal2mbtiles", "expected_step"), [
    (False, "generate MBTiles pyramid via gdal_translate"),
    (True, "generate MBTiles pyramid via gdal2mbtiles"),
])
def test_create_mbtiles_adds_the_center(tmp_path: Path, monkeypatch, gdal2mbtiles: bool, expected_step: str) -> None:
    # both tilers end with ensure_center on the archive they wrote
    seen: list = []
    steps: list = []
    monkeypatch.setattr(tm, "ensure_center", seen.append)
    monkeypatch.setattr(tm.TileRunner, "run", lambda self, command, *, description: steps.append(description))
    monkeypatch.setattr(tm.TilingManager, "_resolve_gdal2mbtiles",
                        lambda self: ["gdal2mbtiles"] if gdal2mbtiles else None)
    monkeypatch.setattr(tm.TilingManager, "_libvips_healthy", lambda self: True)
    # auto mode uses gdal2mbtiles for JPEG only
    manager = tm.TilingManager(ProcessingConfig(tile_format="JPEG"), temp_dir=tmp_path / "tmp",
                               output_dir=tmp_path / "out")
    out = manager.create_mbtiles(tmp_path / "in.tif", destination=tmp_path / "out" / "t.mbtiles")
    assert expected_step in steps
    assert seen == [out]


def test_dry_run_does_not_touch_the_archive(tmp_path: Path, monkeypatch) -> None:
    seen: list = []
    monkeypatch.setattr(tm, "ensure_center", seen.append)
    monkeypatch.setattr(tm.TilingManager, "_resolve_gdal2mbtiles", lambda self: None)
    manager = tm.TilingManager(ProcessingConfig(), temp_dir=tmp_path / "tmp", output_dir=tmp_path / "out",
                               dry_run=True)
    manager.create_mbtiles(tmp_path / "in.tif")
    assert seen == []


@pytest.mark.skipif(shutil.which("gdal_translate") is None or shutil.which("pmtiles") is None,
                    reason="needs gdal_translate and pmtiles")
def test_a_gdal_mbtiles_of_japan_converts_with_a_center_inside_its_bounds(tmp_path: Path) -> None:
    gdal = pytest.importorskip("osgeo.gdal")
    gdal.UseExceptions()
    src = gdal.GetDriverByName("GTiff").Create(str(tmp_path / "in.tif"), 256, 256, 3)
    src.SetGeoTransform((139.0, 1 / 256, 0, 36.0, 0, -1 / 256))
    src.SetProjection("EPSG:4326")
    src = None
    mbtiles = tmp_path / "out.mbtiles"
    subprocess.run(["gdal_translate", "-q", "-of", "MBTiles", str(tmp_path / "in.tif"), str(mbtiles)], check=True)
    assert "center" not in _meta(mbtiles)  # GDAL leaves it out

    ensure_center(mbtiles)

    pmtiles = tmp_path / "out.pmtiles"
    subprocess.run(["pmtiles", "convert", "-q", str(mbtiles), str(pmtiles)], check=True, capture_output=True)
    shown = subprocess.run(["pmtiles", "show", str(pmtiles)], check=True, capture_output=True, text=True).stdout
    center_line = next(line for line in shown.splitlines() if line.startswith("center:"))
    lon = float(center_line.split("long:")[1].split(",")[0])
    assert 139.0 <= lon <= 140.0
