"""Web Mercator tile math shared by Copernicus acquisition and processing."""

from __future__ import annotations

import pytest

from planetarble.acquisition.copernicus import (
    ORIGIN_SHIFT,
    _tile_range,
    tile_bounds_3857,
)

EDGE = ORIGIN_SHIFT


@pytest.mark.parametrize(
    ("x", "y", "z", "bounds"),
    [
        (0, 0, 0, (-EDGE, -EDGE, EDGE, EDGE)),
        (1, 0, 1, (0.0, 0.0, EDGE, EDGE)),
        (0, 1, 1, (-EDGE, -EDGE, 0.0, 0.0)),
    ],
)
def test_tile_bounds_3857(x: int, y: int, z: int, bounds) -> None:
    assert tile_bounds_3857(x, y, z) == pytest.approx(bounds)


def test_tile_range_tokyo() -> None:
    # Independently: x = (lon + 180) / 360 * 2^14 -> 14552.63 .. 14553.09,
    # y = (1 - asinh(tan(lat)) / pi) / 2 * 2^14 -> 6451.17 (35.69N) .. 6452.29 (35.67N)
    assert _tile_range((139.76, 35.67, 139.77, 35.69), 14) == (14552, 14553, 6451, 6452)


def test_tile_range_normalises_and_clamps() -> None:
    assert _tile_range((180.0, 90.0, -180.0, -90.0), 1) == (0, 1, 0, 1)
    assert _tile_range((-200.0, -10.0, -170.0, 10.0), 2) == (0, 0, 1, 2)
