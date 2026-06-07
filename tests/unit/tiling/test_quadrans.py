"""Tests for the Quadrans 4-way tile classification."""

from __future__ import annotations

import math

from planetarble.tiling.quadrans import QUADRANS, quadrans_of_tile


def _lonlat_to_tile(lon: float, lat: float, z: int) -> "tuple[int, int]":
    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


CITIES = {
    "tokyo": (139.69, 35.69, "east"),     # lon >= 138
    "osaka": (135.50, 34.69, "west"),     # 133..138
    "nagoya": (136.91, 35.18, "west"),    # 133..138
    "sapporo": (141.35, 43.06, "north"),  # lat_code 64 >= 62
    "fukuoka": (130.40, 33.59, "south"),  # lon_code 30 <= 32
    "hiroshima": (132.46, 34.40, "south"),  # lon 132 -> lon_code 32 <= 32
    "sendai": (140.87, 38.27, "east"),    # lon >= 138
}


def test_known_cities_classify_as_expected() -> None:
    for name, (lon, lat, expected) in CITIES.items():
        for z in (12, 16, 18):
            x, y = _lonlat_to_tile(lon, lat, z)
            assert quadrans_of_tile(z, x, y) == expected, f"{name}@z{z}"


def test_always_returns_a_valid_quadrans() -> None:
    # sweep a coarse grid of tiles at z6 over Japan-ish range; every tile maps to one region
    for x in range(54, 60):       # ~ lon 116..150 at z6
        for y in range(22, 28):   # ~ lat range over Japan
            q = quadrans_of_tile(6, x, y)
            assert q in QUADRANS


def test_boundaries_are_tunable() -> None:
    # Tokyo is 'east' by default; raise the east boundary so 138 no longer qualifies
    x, y = _lonlat_to_tile(139.69, 35.69, 14)
    assert quadrans_of_tile(14, x, y) == "east"
    assert quadrans_of_tile(14, x, y, lon_east=41) == "west"
