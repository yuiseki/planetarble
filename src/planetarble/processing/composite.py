"""Pure temporal-compositing helpers for cloud-free mosaicking.

The per-pixel median over a stack of co-registered observations is the standard
robust way to remove residual clouds, cloud shadows and haze that a per-scene
QA mask (Fmask) misses: a thin cloud or haze pixel is an outlier in time, and
the median rejects it as long as the majority of observations are clear. It also
fills small gaps left by masking, as long as at least one scene saw the ground.

This module is deliberately free of GDAL so the statistics can be unit tested
directly; the GDAL wiring that warps scenes onto a common grid and streams
blocks through here lives in ``planetarble.processing.manager``.
"""

from __future__ import annotations

import warnings

import numpy as np


def median_composite(stack: np.ndarray, nodata: int = 0) -> np.ndarray:
    """Per-pixel median over a (N, H, W) stack, ignoring ``nodata``.

    Pixels equal to ``nodata`` in a scene are excluded from that pixel's median.
    Where every scene is ``nodata`` (no clear observation), the result stays
    ``nodata``. The output keeps the input dtype (values are rounded to nearest).
    """
    arr = np.asarray(stack)
    if arr.ndim != 3:
        raise ValueError("stack must have shape (N, H, W)")

    work = arr.astype(np.float32)
    work[arr == nodata] = np.nan

    with warnings.catch_warnings():
        # all-nodata columns produce an expected "All-NaN slice" warning
        warnings.simplefilter("ignore", category=RuntimeWarning)
        median = np.nanmedian(work, axis=0)

    filled = np.where(np.isnan(median), float(nodata), np.rint(median))
    return filled.astype(arr.dtype)
