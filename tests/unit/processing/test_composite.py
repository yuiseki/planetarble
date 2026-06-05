"""Tests for the pure temporal-median composite core."""

from __future__ import annotations

import numpy as np

from planetarble.processing.composite import median_composite


def test_median_ignores_nodata() -> None:
    # pixel (0,0): values 10, 20, 30 -> median 20
    # pixel (0,1): 100, nodata, 200 -> median ignoring nodata = 150
    stack = np.array(
        [
            [[10, 100]],
            [[20, 0]],
            [[30, 200]],
        ],
        dtype=np.uint16,
    )
    out = median_composite(stack, nodata=0)
    assert out.tolist() == [[20, 150]]
    assert out.dtype == np.uint16


def test_all_nodata_pixel_stays_nodata() -> None:
    stack = np.array([[[0, 5]], [[0, 7]]], dtype=np.uint16)
    out = median_composite(stack, nodata=0)
    # first pixel: every scene masked -> stays nodata (no clear observation)
    assert out[0, 0] == 0
    assert out[0, 1] == 6


def test_single_scene_returns_itself() -> None:
    stack = np.array([[[3, 0, 9]]], dtype=np.uint16)
    out = median_composite(stack, nodata=0)
    assert out.tolist() == [[3, 0, 9]]


def test_requires_3d_stack() -> None:
    try:
        median_composite(np.zeros((2, 2), dtype=np.uint16))
    except ValueError:
        return
    raise AssertionError("expected ValueError for non-3D stack")
