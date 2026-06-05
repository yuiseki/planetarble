"""Test the GDAL-backed Fmask dilation that buffers the cloud mask (fix D)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


def test_dilate_fmask_buffers_cloud_bits(tmp_path: Path) -> None:
    gdal = pytest.importorskip("osgeo.gdal")
    from planetarble.processing.manager import _dilate_fmask

    # 5x5 Fmask: one cloud pixel (bit 2 = cloud) at center, rest clear
    qa = np.zeros((5, 5), dtype=np.uint16)
    qa[2, 2] = 2
    qa_path = tmp_path / "Fmask.tif"
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(str(qa_path), 5, 5, 1, gdal.GDT_UInt16)
    ds.SetGeoTransform((139.0, 0.001, 0.0, 35.8, 0.0, -0.001))
    ds.GetRasterBand(1).WriteArray(qa)
    ds.FlushCache()
    ds = None

    out = _dilate_fmask(qa_path, tmp_path / "dilated.tif", mask_value=2, iterations=1)
    res = gdal.Open(str(out))
    arr = res.GetRasterBand(1).ReadAsArray()
    res = None

    # cloud grown to a 4-connected plus; result is a 0/1 mask
    assert arr[2, 2] == 1
    assert arr[1, 2] == 1 and arr[3, 2] == 1 and arr[2, 1] == 1 and arr[2, 3] == 1
    assert arr[0, 0] == 0
    assert set(np.unique(arr)).issubset({0, 1})
