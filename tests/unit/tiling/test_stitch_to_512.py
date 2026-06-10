"""Tests for stitch_to_512 (build a 512px pyramid from a 256px source).

Output tile (z,x,y) is the 2x2 mosaic of source (z+1, 2x|2x+1, 2y|2y+1).
MBTiles store TMS rows (row = 2**z - 1 - y_xyz); helpers convert at the SQL
boundary. Fixtures use a tiny src_tile_size so output tiles are small and exact
(PNG, lossless) for pixel assertions.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from planetarble.tiling.mbtiles import stitch_to_512  # noqa: E402

SUB = 2  # source tile size in tests -> output tiles are 4x4


def _tms(z: int, y: int) -> int:
    return (1 << z) - 1 - y


def _mbtiles(path: Path, tiles: dict, *, meta: dict | None = None) -> None:
    """tiles keyed by XYZ (z, x, y) -> RGB tuple; stored as solid PNG, TMS rows."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE metadata (name text, value text)")
    conn.execute("CREATE TABLE tiles (zoom_level int, tile_column int, tile_row int, tile_data blob)")
    conn.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
    for (z, x, y), color in tiles.items():
        buf = io.BytesIO()
        Image.new("RGB", (SUB, SUB), color).save(buf, format="PNG")
        conn.execute("INSERT INTO tiles VALUES (?,?,?,?)", (z, x, _tms(z, y), buf.getvalue()))
    for k, v in (meta or {}).items():
        conn.execute("INSERT INTO metadata VALUES (?,?)", (k, v))
    conn.commit()
    conn.close()


def _read_xyz(path: Path, z: int, x: int, y: int):
    conn = sqlite3.connect(str(path))
    row = conn.execute(
        "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
        (z, x, _tms(z, y)),
    ).fetchone()
    conn.close()
    return Image.open(io.BytesIO(row[0])).convert("RGB") if row else None


def _meta(path: Path) -> dict:
    conn = sqlite3.connect(str(path))
    m = dict(conn.execute("SELECT name, value FROM metadata"))
    conn.close()
    return m


def test_quadrants_placed_north_up_west_left(tmp_path: Path) -> None:
    # source zoom 1 (the four children of output (0,0,0)), distinct colours
    NW, NE, SW, SE = (10, 0, 0), (0, 20, 0), (0, 0, 30), (40, 40, 40)
    src = tmp_path / "s.mbtiles"
    _mbtiles(src, {
        (1, 0, 0): NW,  # x even (west), y even (north) -> top-left
        (1, 1, 0): NE,  # east, north -> top-right
        (1, 0, 1): SW,  # west, south -> bottom-left
        (1, 1, 1): SE,  # east, south -> bottom-right
    })
    out = tmp_path / "o.mbtiles"
    stitch_to_512(src, out, tile_format="png", src_tile_size=SUB)

    tile = _read_xyz(out, 0, 0, 0)
    assert tile is not None and tile.size == (2 * SUB, 2 * SUB)
    assert tile.getpixel((0, 0)) == NW            # top-left
    assert tile.getpixel((2 * SUB - 1, 0)) == NE  # top-right
    assert tile.getpixel((0, 2 * SUB - 1)) == SW  # bottom-left
    assert tile.getpixel((2 * SUB - 1, 2 * SUB - 1)) == SE  # bottom-right


def test_zoom_shift_and_range(tmp_path: Path) -> None:
    # source spanning z2..z3 -> output z1..z2
    src = tmp_path / "s.mbtiles"
    _mbtiles(src, {
        (2, 0, 0): (1, 1, 1), (2, 1, 0): (2, 2, 2), (2, 0, 1): (3, 3, 3), (2, 1, 1): (4, 4, 4),
        (3, 4, 5): (9, 9, 9),
    })
    out = tmp_path / "o.mbtiles"
    stitch_to_512(src, out, tile_format="png", src_tile_size=SUB)

    m = _meta(out)
    assert m["minzoom"] == "1" and m["maxzoom"] == "2"
    # z2 children (0,0),(1,0),(0,1),(1,1) -> output z1 tile (0,0)
    assert _read_xyz(out, 1, 0, 0) is not None
    # z3 (4,5) -> output z2 parent (2,2)
    assert _read_xyz(out, 2, 2, 2) is not None


def test_partial_coverage_fills_background(tmp_path: Path) -> None:
    # only the NW child present; other quadrants -> background
    src = tmp_path / "s.mbtiles"
    _mbtiles(src, {(1, 0, 0): (200, 100, 50)})
    out = tmp_path / "o.mbtiles"
    stitch_to_512(src, out, tile_format="png", src_tile_size=SUB, background=(7, 8, 9))

    tile = _read_xyz(out, 0, 0, 0)
    assert tile.getpixel((0, 0)) == (200, 100, 50)       # NW child
    assert tile.getpixel((2 * SUB - 1, 2 * SUB - 1)) == (7, 8, 9)  # SE missing -> background


def test_metadata_carried_and_format(tmp_path: Path) -> None:
    src = tmp_path / "s.mbtiles"
    _mbtiles(
        src,
        {(1, 0, 0): (1, 2, 3)},
        meta={"name": "GSI", "attribution": "GSI CC BY 4.0", "bounds": "1,2,3,4", "format": "jpg"},
    )
    out = tmp_path / "o.mbtiles"
    stitch_to_512(src, out, src_tile_size=SUB)  # default tile_format jpg

    m = _meta(out)
    assert m["format"] == "jpg"
    assert m["attribution"] == "GSI CC BY 4.0" and m["bounds"] == "1,2,3,4"
    assert m["name"] == "GSI"


def test_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        stitch_to_512(tmp_path / "nope.mbtiles", tmp_path / "o.mbtiles")
