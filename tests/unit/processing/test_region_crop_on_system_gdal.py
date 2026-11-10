"""Crop a mosaic to its plan region with the GDAL the README targets (3.8).

gdalwarp gained -cutline_srs in GDAL 3.9. On Ubuntu 24.04 (GDAL 3.8.4) the
2026-11 Tokyo rebuild failed at the crop, after 2.5 hours of compositing, with
"Unknown option name '-cutline_srs'". These tests run the real commands, so
the CI job on system GDAL catches an option that the installed GDAL lacks.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

gdal = pytest.importorskip("osgeo.gdal")
ogr = pytest.importorskip("osgeo.ogr")
osr = pytest.importorskip("osgeo.osr")

if shutil.which("gdalwarp") is None or shutil.which("gdal_translate") is None:
    pytest.skip("GDAL command-line tools not installed", allow_module_level=True)

from planetarble.processing.hls_mosaic import _translate_hls_rgb  # noqa: E402
from planetarble.processing.runner import CommandRunner  # noqa: E402
from planetarble.processing.sentinel2_mosaic import _translate_sentinel2_rgb  # noqa: E402

# a 1x1 degree raster at 0.01 degrees, west half of the cutline only
WEST, NORTH, SIZE, RES = 139.0, 36.0, 100, 0.01


def _rgb_uint16(path: Path) -> Path:
    ds = gdal.GetDriverByName("GTiff").Create(str(path), SIZE, SIZE, 3, gdal.GDT_UInt16)
    ds.SetGeoTransform((WEST, RES, 0, NORTH, 0, -RES))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    for i in range(1, 4):
        ds.GetRasterBand(i).Fill(1500)
    ds = None
    return path


def _west_half() -> "ogr.Geometry":
    return ogr.CreateGeometryFromWkt(
        f"POLYGON(({WEST} {NORTH - 1}, {WEST + 0.5} {NORTH - 1}, {WEST + 0.5} {NORTH}, "
        f"{WEST} {NORTH}, {WEST} {NORTH - 1}))"
    )


def _width_in_degrees(path: Path) -> float:
    ds = gdal.Open(str(path))
    return ds.RasterXSize * ds.GetGeoTransform()[1]


def test_hls_mosaic_crops_to_the_region(tmp_path: Path) -> None:
    out = _translate_hls_rgb(
        CommandRunner(),
        _rgb_uint16(tmp_path / "rgb.tif"),
        tmp_path / "hls.tif",
        region_geometry=_west_half(),
    )
    assert _width_in_degrees(out) == pytest.approx(0.5, abs=RES)
    # 1500 of 3000 with gamma 0.8: 255 * 0.5 ** 0.8, about 146
    assert gdal.Open(str(out)).GetRasterBand(1).ReadAsArray().max() == pytest.approx(146, abs=2)


def test_sentinel2_mosaic_crops_to_the_region(tmp_path: Path) -> None:
    out = _translate_sentinel2_rgb(
        CommandRunner(),
        _rgb_uint16(tmp_path / "rgb.tif"),
        tmp_path / "s2.tif",
        bbox=(WEST, NORTH - 1, WEST + 1, NORTH),
        region_geometry=_west_half(),
        scale_to_byte=True,
    )
    assert _width_in_degrees(out) == pytest.approx(0.5, abs=RES)
