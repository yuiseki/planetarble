"""Quadrans: hfu's 4-way spatial split of Japan, expressed in XYZ tile space.

Per UNopenGIS/7#909 the split is defined on the Japanese primary mesh code
(第1次地域区画, 4 digits): upper two digits = latitude number = floor(lat*1.5),
lower two = longitude number = floor(lon)-100. A region is then::

    lat_code >= 62           -> north   (Hokkaido + northern Tohoku)
    else lon_code >= 38      -> east    (Kanto / eastern Chubu, lon >= 138)
    else lon_code <= 32      -> south   (Kyushu / western Chugoku, lon < 133)
    else                     -> west    (Kinki / Nagoya / Shikoku, 133..138)

The issue discusses this on mesh codes; here we classify an XYZ *tile* by the
primary mesh of its centre point, so the partition is a disjoint, complete cover
of tile space and agrees with the mesh-based assignment. The boundary values
(62/38/32) are the issue's tunable defaults and are exposed as arguments.
"""

from __future__ import annotations

import math

QUADRANS = ("north", "east", "south", "west")


def tile_center_lonlat(z: int, x: int, y: int) -> "tuple[float, float]":
    """Return the (lon, lat) of the centre of XYZ tile ``(z, x, y)`` in degrees."""
    n = 1 << z
    lon = (x + 0.5) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 0.5) / n))))
    return lon, lat


def quadrans_of_tile(
    z: int,
    x: int,
    y: int,
    *,
    lat_north: int = 62,
    lon_east: int = 38,
    lon_south: int = 32,
) -> str:
    """Classify XYZ tile ``(z, x, y)`` into one of ``QUADRANS`` (by tile centre)."""
    lon, lat = tile_center_lonlat(z, x, y)
    lat_code = math.floor(lat * 1.5)
    lon_code = math.floor(lon) - 100
    if lat_code >= lat_north:
        return "north"
    if lon_code >= lon_east:
        return "east"
    if lon_code <= lon_south:
        return "south"
    return "west"
