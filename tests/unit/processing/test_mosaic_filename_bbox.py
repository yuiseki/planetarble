"""The Sentinel-2 mosaic filename must change when the AOI bbox changes.

Otherwise widening an overlay's AOI silently reuses the old, smaller-footprint
cached COG (the mosaic skip is keyed on a valid existing file), so the expansion
never takes effect.
"""

from __future__ import annotations

from planetarble.processing.manager import _sentinel2_mosaic_filename


def test_same_bbox_same_name() -> None:
    a = _sentinel2_mosaic_filename("tokyo_s2", (138.8, 35.16, 140.0, 36.13))
    b = _sentinel2_mosaic_filename("tokyo_s2", (138.8, 35.16, 140.0, 36.13))
    assert a == b
    assert a.startswith("sentinel2_mosaic_tokyo_s2_")
    assert a.endswith("_cog.tif")


def test_changed_bbox_changes_name() -> None:
    small = _sentinel2_mosaic_filename("hiroshima_s2", (132.41, 34.36, 132.49, 34.43))
    big = _sentinel2_mosaic_filename("hiroshima_s2", (131.72, 34.22, 132.92, 35.22))
    assert small != big  # widened AOI -> fresh name -> regenerates


def test_no_bbox_unhashed() -> None:
    assert _sentinel2_mosaic_filename("x", None) == "sentinel2_mosaic_x_cog.tif"
    assert _sentinel2_mosaic_filename(None, None) == "sentinel2_mosaic_cog.tif"
