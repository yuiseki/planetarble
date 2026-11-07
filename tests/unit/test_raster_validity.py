"""The CLI and the processing manager must agree on what a valid raster is.

Both decide whether to skip rebuilding an existing mosaic. A truncated or
corrupt GeoTIFF left behind by an interrupted run must count as invalid (so it
is rebuilt) and must never crash the check, whether or not GDAL exceptions are
enabled at the time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.cli import process as cli_process
from planetarble.processing import assets
from planetarble.processing import manager as processing_manager

try:
    from osgeo import gdal
except ImportError:  # collection-only hosts
    gdal = None

needs_gdal = pytest.mark.skipif(gdal is None, reason="GDAL Python bindings not installed")

CHECKS = [assets._is_valid_raster]


def test_cli_and_processing_share_one_implementation() -> None:
    assert cli_process._is_valid_raster is assets._is_valid_raster
    assert processing_manager._is_valid_raster is assets._is_valid_raster


def _write_tif(path: Path) -> Path:
    ds = gdal.GetDriverByName("GTiff").Create(str(path), 4, 4, 3, gdal.GDT_Byte)
    ds.SetGeoTransform((0, 1, 0, 0, 0, -1))
    ds.FlushCache()
    ds = None
    return path


@pytest.fixture(params=[True, False], ids=["exceptions", "no-exceptions"])
def gdal_exceptions(request):
    previous = gdal.GetUseExceptions()
    if request.param:
        gdal.UseExceptions()
    else:
        gdal.DontUseExceptions()
    yield request.param
    if previous:
        gdal.UseExceptions()
    else:
        gdal.DontUseExceptions()


@needs_gdal
@pytest.mark.parametrize("check", CHECKS)
def test_valid_raster(tmp_path, gdal_exceptions, check) -> None:
    assert check(_write_tif(tmp_path / "ok.tif")) is True


@needs_gdal
@pytest.mark.parametrize("check", CHECKS)
def test_missing_or_empty_is_invalid(tmp_path, gdal_exceptions, check) -> None:
    assert check(tmp_path / "missing.tif") is False
    (tmp_path / "empty.tif").write_bytes(b"")
    assert check(tmp_path / "empty.tif") is False


@needs_gdal
@pytest.mark.parametrize("check", CHECKS)
def test_corrupt_is_invalid_without_raising(tmp_path, gdal_exceptions, check) -> None:
    bad = tmp_path / "bad.tif"
    bad.write_bytes(b"II*\x00" + b"\x00" * 64)  # TIFF magic, garbage body
    assert check(bad) is False


@needs_gdal
@pytest.mark.parametrize("check", CHECKS)
def test_check_does_not_change_gdal_exception_mode(tmp_path, gdal_exceptions, check) -> None:
    check(_write_tif(tmp_path / "ok.tif"))
    assert bool(gdal.GetUseExceptions()) is gdal_exceptions
