"""Tests for alpha-compositing MBTiles merge (paint finer source over coarser)."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from planetarble.tiling.mbtiles import composite_mbtiles  # noqa: E402


def _write_mbtiles(path: Path, tiles: dict, fmt: str = "png") -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE metadata (name text, value text)")
    conn.execute("CREATE TABLE tiles (zoom_level int, tile_column int, tile_row int, tile_data blob)")
    conn.execute("INSERT INTO metadata VALUES ('format', ?)", (fmt,))
    for (z, x, y), img in tiles.items():
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        conn.execute(
            "INSERT INTO tiles VALUES (?,?,?,?)", (z, x, y, buf.getvalue())
        )
    conn.commit()
    conn.close()


def _solid(color) -> Image.Image:
    return Image.new("RGBA", (4, 4), color)


def _assert_near(actual, expected, tol: int = 40) -> None:
    # WEBP is lossy, so colors shift slightly; assert proximity, not equality.
    for a, e in zip(actual[:3], expected):
        assert abs(a - e) <= tol, f"{actual[:3]} not near {expected}"


def _read_tile(path: Path, z: int, x: int, y: int) -> Image.Image:
    conn = sqlite3.connect(str(path))
    row = conn.execute(
        "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
        (z, x, y),
    ).fetchone()
    conn.close()
    assert row is not None, f"tile {z}/{x}/{y} missing"
    return Image.open(io.BytesIO(row[0])).convert("RGBA")


def test_composite_paints_opaque_over_base_keeps_base_where_transparent(tmp_path: Path) -> None:
    base = tmp_path / "base.mbtiles"
    overlay = tmp_path / "overlay.mbtiles"
    dest = tmp_path / "out.mbtiles"

    red = _solid((255, 0, 0, 255))
    # overlay z2 tile: left half opaque blue, right half fully transparent
    ov = Image.new("RGBA", (4, 4), (0, 0, 255, 255))
    for yy in range(4):
        for xx in range(2, 4):
            ov.putpixel((xx, yy), (0, 0, 0, 0))

    _write_mbtiles(base, {(2, 1, 1): red, (2, 2, 2): red})
    _write_mbtiles(overlay, {(2, 1, 1): ov})

    composite_mbtiles(base, overlay, dest, tile_format="webp")

    out = _read_tile(dest, 2, 1, 1)
    _assert_near(out.getpixel((0, 0)), (0, 0, 255))   # overlay opaque wins
    _assert_near(out.getpixel((3, 0)), (255, 0, 0))   # transparent -> base shows
    # base-only tile preserved
    _assert_near(_read_tile(dest, 2, 2, 2).getpixel((0, 0)), (255, 0, 0))


def test_composite_inserts_overlay_only_tiles(tmp_path: Path) -> None:
    base = tmp_path / "base.mbtiles"
    overlay = tmp_path / "overlay.mbtiles"
    dest = tmp_path / "out.mbtiles"
    _write_mbtiles(base, {(0, 0, 0): _solid((10, 10, 10, 255))})
    _write_mbtiles(overlay, {(5, 9, 9): _solid((0, 255, 0, 255))})  # deeper zoom, no base

    composite_mbtiles(base, overlay, dest, tile_format="webp")

    _assert_near(_read_tile(dest, 5, 9, 9).getpixel((0, 0)), (0, 255, 0))
    _assert_near(_read_tile(dest, 0, 0, 0).getpixel((0, 0)), (10, 10, 10))


def test_composite_output_is_uniform_webp(tmp_path: Path) -> None:
    base = tmp_path / "base.mbtiles"
    overlay = tmp_path / "overlay.mbtiles"
    dest = tmp_path / "out.mbtiles"
    _write_mbtiles(base, {(1, 0, 0): _solid((1, 2, 3, 255))}, fmt="jpg")
    _write_mbtiles(overlay, {(1, 1, 1): _solid((4, 5, 6, 255))})

    composite_mbtiles(base, overlay, dest, tile_format="webp")

    conn = sqlite3.connect(str(dest))
    fmt = conn.execute("SELECT value FROM metadata WHERE name='format'").fetchone()[0]
    conn.close()
    assert fmt == "webp"
