"""Tests for wiring the temporal median composite into the HLS mosaic.

The pure median statistics are covered in test_composite.py; here we cover the
strategy predicate (pure) and the GDAL-backed helper that warps per-scene band
rasters onto a common grid, medians them, and writes a 3-band RGB GeoTIFF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # provided by the system alongside GDAL

from planetarble.processing.hls_mosaic import (
    _build_hls_median_rgb,
    _use_median_strategy,
)


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


# --- memory bound -------------------------------------------------------------
# tokyo_land reaches Ogasawara: its grid was 13,660 x 46,994 px with 148 scenes,
# and 512-row blocks of that stack (uint16, its float32 copy, the NaN mask and
# nanmedian's own copy) ran past the 8 GB cap on 2026-11-11.


def test_median_block_rows_keep_the_tokyo_stack_inside_the_budget() -> None:
    from planetarble.processing.hls_mosaic import (
        MEDIAN_BYTES_PER_SAMPLE,
        _median_block_rows,
    )

    budget = 1 << 30
    rows = _median_block_rows(148, 13660, memory_budget=budget)

    assert 1 <= rows < 512
    assert rows * 148 * 13660 * MEDIAN_BYTES_PER_SAMPLE <= budget


def test_median_block_rows_are_capped_and_never_zero() -> None:
    from planetarble.processing.hls_mosaic import _median_block_rows

    assert _median_block_rows(3, 100, memory_budget=1 << 30) == 512
    assert _median_block_rows(10_000, 100_000, memory_budget=1) == 1


def test_build_hls_median_rgb_is_the_same_in_one_row_blocks(tmp_path: Path) -> None:
    pytest.importorskip("osgeo.gdal")
    from osgeo import gdal

    reds = [np.array([[10, 0], [5, 5]]), np.array([[20, 100], [5, 0]]), np.array([[30, 200], [0, 5]])]
    band_lists = {}
    for label in ("red", "green", "blue"):
        srcs = []
        for i, arr in enumerate(reds):
            p = tmp_path / f"{label}_{i}.tif"
            _write_band_tif(p, arr)
            srcs.append(str(p))
        lst = tmp_path / f"{label}_sources.txt"
        lst.write_text("\n".join(srcs), encoding="utf-8")
        band_lists[label] = lst

    out = _build_hls_median_rgb(band_lists, tmp_path, memory_budget=1)  # one row per block

    ds = gdal.Open(str(out))
    assert ds.GetRasterBand(1).ReadAsArray().tolist() == [[20, 150], [5, 5]]
    ds = None
