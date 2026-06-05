"""Asset validity checks must report invalid, never crash, on corrupt cache.

With gdal.UseExceptions() active (the real build enables it), Open/ReadRaster on
a truncated or corrupt asset raises (e.g. TIFFReadEncodedTile failed). The
validity check must catch that and return False so the caller can resume the
download (aria2c --continue) or re-fetch, instead of crashing the whole build.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _make_tiled_tif(path: Path, gdal) -> None:
    import numpy as np

    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(
        str(path), 512, 512, 1, gdal.GDT_Byte,
        options=["TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256", "COMPRESS=DEFLATE"],
    )
    ds.GetRasterBand(1).WriteArray(np.full((512, 512), 7, np.uint8))
    ds.FlushCache()
    ds = None


def test_valid_tif_is_valid(tmp_path: Path) -> None:
    gdal = pytest.importorskip("osgeo.gdal")
    gdal.UseExceptions()
    from planetarble.processing.manager import _is_valid_sentinel2_asset, _is_valid_hls_asset

    p = tmp_path / "ok.tif"
    _make_tiled_tif(p, gdal)
    assert _is_valid_sentinel2_asset(p) is True
    assert _is_valid_hls_asset(p) is True


def test_garbage_is_invalid_not_raise(tmp_path: Path) -> None:
    gdal = pytest.importorskip("osgeo.gdal")
    gdal.UseExceptions()  # mirror the build: Open on junk raises rather than None
    from planetarble.processing.manager import (
        _is_valid_sentinel2_asset, _is_valid_hls_asset, _is_valid_raster,
    )

    p = tmp_path / "bad.tif"
    p.write_bytes(b"not a tiff at all" * 1000)
    # must return False, never propagate a GDAL RuntimeError
    assert _is_valid_sentinel2_asset(p) is False
    assert _is_valid_hls_asset(p) is False
    assert _is_valid_raster(p) is False


def test_truncated_tif_is_invalid_not_raise(tmp_path: Path) -> None:
    gdal = pytest.importorskip("osgeo.gdal")
    gdal.UseExceptions()
    from planetarble.processing.manager import _is_valid_sentinel2_asset

    p = tmp_path / "trunc.tif"
    _make_tiled_tif(p, gdal)
    data = p.read_bytes()
    p.write_bytes(data[: len(data) // 3])  # chop -> Open/ReadRaster fail
    # the truncated-cache case that used to crash the build
    assert _is_valid_sentinel2_asset(p) is False
