#!/usr/bin/env python3
"""Generate a rebalanced 18-way miniplanet partition for planetarble.

Unlike ``@geolonia/osm-miniplanets`` (whose subdivisions are balanced for OSM
``planet.osm.pbf`` byte size), planetarble's workload is dominated by the number
of *land* ZL10 composite tiles it plans (one STAC search + composite per tile).
This script therefore weights each z6 cell by the count of land z10 tiles it
contains -- a faithful proxy for HLS raster volume -- and partitions the z6 grid
into 18 axis-aligned, mutually-exclusive, globally-exhaustive rectangles with
roughly equal land weight via recursive coordinate bisection (RCB).

The partitioning algorithm (``compute_subdivisions``) lives in
``planetarble.acquisition.miniplanets``. This tool only builds the land-weight
grid and prints the resulting ``SUBDIVISIONS`` table for pasting into that module.

The land predicate mirrors ``planetarble.acquisition.hls`` so the weighting
matches what ``HLSMosaicPlanner`` actually plans. By default it uses the coarse
``LAND_APPROX_BBOXES`` heuristic (no GDAL / no data download required); pass a
finer per-tile predicate if a precise land mask is available.

Usage:
    PYTHONPATH=src python3 tools/gen_miniplanets.py [--base-zoom 6] [--target-zoom 10]
"""

from __future__ import annotations

import argparse
import math
from typing import List, Tuple

# --- land predicate (mirrors planetarble.acquisition.hls; planetarble's own code) ---

WEBMERCATOR_MIN_LAT = -85.0511287798066
WEBMERCATOR_MAX_LAT = 85.0511287798066

LAND_APPROX_BBOXES: Tuple[Tuple[float, float, float, float], ...] = (
    (-170.0, -60.0, -30.0, 72.0),   # Americas
    (-30.0, -40.0, 60.0, 75.0),     # Europe + Africa
    (60.0, -10.0, 150.0, 80.0),     # Asia
    (110.0, -60.0, 180.0, -10.0),   # Australia
    (-45.0, -55.0, -10.0, -30.0),   # Southern South America
    (20.0, -75.0, 160.0, -60.0),    # Antarctica
    (-90.0, 10.0, -60.0, 30.0),     # Caribbean / Central America
    (-170.0, -25.0, -140.0, 25.0),  # Central Pacific archipelagos
    (150.0, -50.0, 180.0, -30.0),   # New Zealand
)


def _tile_bounds(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    n = 1 << z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_min = _tile_latitude(y + 1, n)
    lat_max = _tile_latitude(y, n)
    lat_min = max(lat_min, WEBMERCATOR_MIN_LAT)
    lat_max = min(lat_max, WEBMERCATOR_MAX_LAT)
    return (lon_min, lat_min, lon_max, lat_max)


def _tile_latitude(y: int, n: int) -> float:
    merc_y = math.pi * (1 - 2 * y / n)
    lat_rad = math.atan(math.sinh(merc_y))
    return math.degrees(lat_rad)


def _boxes_intersect(a, b, buffer_deg: float) -> bool:
    a_min_lon, a_min_lat, a_max_lon, a_max_lat = a
    b_min_lon, b_min_lat, b_max_lon, b_max_lat = b
    if a_max_lon < b_min_lon - buffer_deg:
        return False
    if a_min_lon > b_max_lon + buffer_deg:
        return False
    if a_max_lat < b_min_lat - buffer_deg:
        return False
    if a_min_lat > b_max_lat + buffer_deg:
        return False
    return True


def _bbox_intersects_land(bbox, buffer_deg: float) -> bool:
    for land_bbox in LAND_APPROX_BBOXES:
        if _boxes_intersect(bbox, land_bbox, buffer_deg):
            return True
    _, min_lat, _, max_lat = bbox
    if max_lat > 75.0 or min_lat < -75.0:
        return True
    return False


def build_land_weight_grid(base_zoom: int, target_zoom: int, land_buffer_km: float) -> List[List[float]]:
    """Return an NxN grid (N=2**base_zoom) of land target-zoom tile counts."""
    if target_zoom < base_zoom:
        raise ValueError("target_zoom must be >= base_zoom")
    buffer_deg = max(0.0, land_buffer_km) / 111.32
    n_base = 1 << base_zoom
    step = 1 << (target_zoom - base_zoom)  # children per axis
    grid = [[0.0] * n_base for _ in range(n_base)]
    for bx in range(n_base):
        for by in range(n_base):
            count = 0
            x0 = bx * step
            y0 = by * step
            for cx in range(x0, x0 + step):
                for cy in range(y0, y0 + step):
                    bounds = _tile_bounds(target_zoom, cx, cy)
                    if _bbox_intersects_land(bounds, buffer_deg):
                        count += 1
            grid[bx][by] = float(count)
    return grid


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-zoom", type=int, default=6)
    parser.add_argument("--target-zoom", type=int, default=10)
    parser.add_argument("--land-buffer-km", type=float, default=20.0)
    parser.add_argument("--regions", type=int, default=18)
    args = parser.parse_args()

    # Import here so the heavy land-grid build can stand alone if the package import fails.
    from planetarble.acquisition.miniplanets import compute_subdivisions

    grid = build_land_weight_grid(args.base_zoom, args.target_zoom, args.land_buffer_km)
    total = sum(sum(col) for col in grid)
    n_base = 1 << args.base_zoom
    subs = compute_subdivisions(grid, args.regions, extent=(0, 0, n_base - 1, n_base - 1))

    print(f"# base_zoom={args.base_zoom} target_zoom={args.target_zoom} "
          f"regions={args.regions} total_land_tiles={int(total)}")
    print("SUBDIVISIONS = (")
    for (x0, y0, x1, y1) in subs:
        w = sum(
            grid[x][y]
            for x in range(x0, x1 + 1)
            for y in range(y0, y1 + 1)
        )
        pct = 100.0 * w / total if total else 0.0
        print(f"    ({x0}, {y0}, {x1}, {y1}),  # land={int(w)} ({pct:.1f}%)")
    print(")")


if __name__ == "__main__":
    main()
