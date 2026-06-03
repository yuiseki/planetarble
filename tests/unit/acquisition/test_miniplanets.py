"""Tests for the miniplanet subdivision scheme."""

from __future__ import annotations

from typing import List, Tuple

import pytest

from planetarble.acquisition.miniplanets import (
    BASE_ZOOM,
    SUBDIVISIONS,
    compute_subdivisions,
    miniplanet_geo_bbox,
    miniplanet_ids,
    subdivision_z6_bbox,
    tile_to_miniplanet_id,
)

BBox = Tuple[int, int, int, int]


def _assert_exact_partition(boxes: List[BBox], extent: BBox) -> None:
    """Every cell in extent is covered by exactly one box (no overlaps, no gaps)."""
    x0, y0, x1, y1 = extent
    owner = {}
    for index, (bx0, by0, bx1, by1) in enumerate(boxes):
        assert bx0 <= bx1 and by0 <= by1, f"degenerate box {index}: {boxes[index]}"
        for x in range(bx0, bx1 + 1):
            for y in range(by0, by1 + 1):
                assert (x, y) not in owner, f"overlap at {(x, y)} (boxes {owner.get((x, y))}, {index})"
                owner[(x, y)] = index
    expected = (x1 - x0 + 1) * (y1 - y0 + 1)
    assert len(owner) == expected, "coverage gap: some cells unassigned"
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            assert (x, y) in owner, f"missing cell {(x, y)}"


def test_subdivisions_count_is_18() -> None:
    assert len(SUBDIVISIONS) == 18


def test_subdivisions_are_exact_partition_of_z6_grid() -> None:
    n = 1 << BASE_ZOOM
    _assert_exact_partition(list(SUBDIVISIONS), (0, 0, n - 1, n - 1))


def test_miniplanet_ids_are_zero_padded_sequence() -> None:
    assert miniplanet_ids() == [str(i).zfill(2) for i in range(18)]


def test_tile_to_miniplanet_id_below_base_zoom_is_none() -> None:
    assert tile_to_miniplanet_id(5, 0, 0) is None
    assert tile_to_miniplanet_id(0, 0, 0) is None


def test_tile_to_miniplanet_id_matches_subdivision_membership_at_base_zoom() -> None:
    n = 1 << BASE_ZOOM
    for x in range(n):
        for y in range(n):
            mp_id = tile_to_miniplanet_id(BASE_ZOOM, x, y)
            assert mp_id is not None
            minx, miny, maxx, maxy = subdivision_z6_bbox(mp_id)
            assert minx <= x <= maxx and miny <= y <= maxy


def test_tile_to_miniplanet_id_uses_ancestor_for_deeper_zoom() -> None:
    # A z10 tile must resolve to the same miniplanet as its z6 ancestor.
    z = 10
    shift = z - BASE_ZOOM
    for (mp_index, (minx, miny, _maxx, _maxy)) in enumerate(SUBDIVISIONS):
        x10 = (minx << shift) + 3
        y10 = (miny << shift) + 7
        assert tile_to_miniplanet_id(z, x10, y10) == str(mp_index).zfill(2)


def test_miniplanet_geo_bbox_is_well_formed() -> None:
    for mp_id in miniplanet_ids():
        west, south, east, north = miniplanet_geo_bbox(mp_id)
        assert -180.0 <= west < east <= 180.0
        assert south < north
        assert -90.0 <= south and north <= 90.0


def test_subdivision_z6_bbox_rejects_unknown_id() -> None:
    with pytest.raises(KeyError):
        subdivision_z6_bbox("99")
    with pytest.raises(KeyError):
        subdivision_z6_bbox("abc")


def test_compute_subdivisions_partitions_uniform_grid() -> None:
    size = 32
    weight = [[1.0] * size for _ in range(size)]
    boxes = compute_subdivisions(weight, 18)
    assert len(boxes) == 18
    _assert_exact_partition(boxes, (0, 0, size - 1, size - 1))


def test_compute_subdivisions_balances_uniform_weight() -> None:
    size = 64
    weight = [[1.0] * size for _ in range(size)]
    boxes = compute_subdivisions(weight, 8)
    cell_total = size * size
    counts = [(x1 - x0 + 1) * (y1 - y0 + 1) for (x0, y0, x1, y1) in boxes]
    assert sum(counts) == cell_total
    ideal = cell_total / 8
    # Each region within 25% of the ideal equal share.
    for count in counts:
        assert abs(count - ideal) <= ideal * 0.25


def test_compute_subdivisions_is_deterministic() -> None:
    size = 16
    weight = [[float((x + y) % 3) for y in range(size)] for x in range(size)]
    first = compute_subdivisions(weight, 7)
    second = compute_subdivisions(weight, 7)
    assert first == second
    _assert_exact_partition(first, (0, 0, size - 1, size - 1))


def test_compute_subdivisions_rejects_oversplit() -> None:
    weight = [[1.0, 1.0], [1.0, 1.0]]  # 2x2 = 4 cells
    with pytest.raises(ValueError):
        compute_subdivisions(weight, 5)


def test_load_region_geometry_uses_miniplanet_bbox() -> None:
    pytest.importorskip("osgeo")  # GDAL only required for geometry construction
    from pathlib import Path

    from planetarble.acquisition.hls import load_region_geometry
    from planetarble.core.models import HLSPlanRegion

    region = HLSPlanRegion(name="mp_00", miniplanet="00", land_only=True)
    geometry = load_region_geometry(region, data_dir=Path("data"))
    assert geometry is not None
    west, south, east, north = miniplanet_geo_bbox("00")
    env_minx, env_maxx, env_miny, env_maxy = geometry.GetEnvelope()
    assert env_minx == pytest.approx(west)
    assert env_maxx == pytest.approx(east)
    assert env_miny == pytest.approx(south)
    assert env_maxy == pytest.approx(north)
