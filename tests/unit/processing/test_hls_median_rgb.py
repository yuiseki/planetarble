"""Tests for wiring the temporal median composite into the HLS mosaic.

The pure median statistics are covered in test_composite.py; here we cover the
strategy predicate (pure) and the GDAL-backed helper that warps per-scene band
rasters onto a common grid, medians them, and writes a 3-band RGB GeoTIFF.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from planetarble.processing.manager import _build_hls_median_rgb, _use_median_strategy


def test_use_median_strategy_predicate() -> None:
    assert _use_median_strategy("best_pixel") is True
    assert _use_median_strategy("median") is True
    assert _use_median_strategy("Median") is True
    assert _use_median_strategy("mosaic") is False
    assert _use_median_strategy("first") is False
    assert _use_median_strategy("") is False


def _write_band_tif(path: Path, array: np.ndarray, nodata: int = 0) -> None:
    gdal = pytest.importorskip("osgeo.gdal")
    h, w = array.shape
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(path), w, h, 1, gdal.GDT_UInt16)
    # a plain north-up grid in EPSG:4326 around Tokyo
    ds.SetGeoTransform((139.0, 0.001, 0.0, 35.8, 0.0, -0.001))
    srs = gdal.osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(array.astype(np.uint16))
    band.SetNoDataValue(nodata)
    ds.FlushCache()
    ds = None


def test_build_hls_median_rgb_medians_and_fills(tmp_path: Path) -> None:
    pytest.importorskip("osgeo.gdal")
    from osgeo import gdal

    # three scenes, same grid; band B04(red) carries a cloud hole (0) per scene
    # pixel layout (2x2). red values per scene:
    #   s0: [[10, 0  ],[ 5, 5]]
    #   s1: [[20, 100],[ 5, 0]]
    #   s2: [[30, 200],[ 0, 5]]
    # medians ignoring 0: (0,0)=20  (0,1)=150  (1,0)=5  (1,1)=5
    reds = [
        np.array([[10, 0], [5, 5]]),
        np.array([[20, 100], [5, 0]]),
        np.array([[30, 200], [0, 5]]),
    ]
    band_lists = {}
    for label, base in (("red", 1000), ("green", 2000), ("blue", 3000)):
        srcs = []
        for i in range(3):
            arr = reds[i] if label == "red" else np.full((2, 2), base + i, dtype=np.uint16)
            p = tmp_path / f"{label}_{i}.tif"
            _write_band_tif(p, arr)
            srcs.append(str(p))
        lst = tmp_path / f"{label}_sources.txt"
        lst.write_text("\n".join(srcs), encoding="utf-8")
        band_lists[label] = lst

    out = _build_hls_median_rgb(band_lists, tmp_path)
    ds = gdal.Open(str(out))
    assert ds.RasterCount == 3
    red = ds.GetRasterBand(1).ReadAsArray()
    assert red.tolist() == [[20, 150], [5, 5]]
    assert ds.GetRasterBand(1).GetNoDataValue() == 0
    ds = None
