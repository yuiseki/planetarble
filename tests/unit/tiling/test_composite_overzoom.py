"""Tests for overzoom-fill stacked compositing (OAM>HLS>BMNG at every zoom)."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from planetarble.tiling.mbtiles import composite_overzoom, fetch_tile_overzoom  # noqa: E402


def _mbtiles(path: Path, tiles: dict) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE metadata (name text, value text)")
    conn.execute("CREATE TABLE tiles (zoom_level int, tile_column int, tile_row int, tile_data blob)")
    for (z, x, y), img in tiles.items():
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        conn.execute("INSERT INTO tiles VALUES (?,?,?,?)", (z, x, y, buf.getvalue()))
    conn.commit()
    conn.close()


def _read(path: Path, z: int, x: int, y: int):
    conn = sqlite3.connect(str(path))
    row = conn.execute(
        "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
        (z, x, y),
    ).fetchone()
    conn.close()
    return Image.open(io.BytesIO(row[0])).convert("RGBA") if row else None


def _near(actual, expected, tol: int = 50) -> None:
    for a, e in zip(actual[:3], expected):
        assert abs(a - e) <= tol, f"{actual[:3]} not near {expected}"


def test_fetch_tile_overzoom_upscales_ancestor(tmp_path: Path) -> None:
    src = tmp_path / "s.mbtiles"
    _mbtiles(src, {(2, 1, 1): Image.new("RGBA", (4, 4), (0, 200, 0, 255))})
    conn = sqlite3.connect(str(src))
    # a z4 child of (2,1,1): (4, 1*4.., ...) -> any child resolves to the parent color
    img = fetch_tile_overzoom(conn, 4, 1 * 4 + 1, 1 * 4 + 1, tile_size=4)
    conn.close()
    assert img is not None and img.size == (4, 4)
    _near(img.getpixel((0, 0)), (0, 200, 0))


def test_fetch_tile_overzoom_none_when_no_ancestor(tmp_path: Path) -> None:
    src = tmp_path / "s.mbtiles"
    _mbtiles(src, {(2, 1, 1): Image.new("RGBA", (4, 4), (0, 0, 0, 255))})
    conn = sqlite3.connect(str(src))
    # a tile under a different z2 parent -> no data
    assert fetch_tile_overzoom(conn, 4, 30, 30, tile_size=4) is None
    conn.close()


def test_composite_overzoom_fills_lower_under_finer(tmp_path: Path) -> None:
    # BMNG floor at z1 only; OAM patch at z3. At z3 over the AOI, the BMNG
    # ancestor must fill under, so output z3 tiles are opaque (no holes).
    bmng = tmp_path / "bmng.mbtiles"
    oam = tmp_path / "oam.mbtiles"
    dest = tmp_path / "planet.mbtiles"
    _mbtiles(bmng, {(1, 1, 1): Image.new("RGBA", (4, 4), (100, 60, 20, 255))})
    # OAM only covers one z3 tile within that z1 parent
    _mbtiles(oam, {(3, 5, 5): Image.new("RGBA", (4, 4), (0, 0, 255, 255))})

    # AOI bbox covering the z3 tile (5,5) region; build z3 only for the test
    # bbox chosen to cover z3 tiles x in [4,5], y in [4,5] (tile (5,5) is ~lon45,lat-41)
    composite_overzoom(
        [bmng, oam], dest, aoi_bbox=(0.0, -50.0, 50.0, -35.0), min_zoom=3, max_zoom=3,
        tile_format="webp", tile_size=4,
    )

    # the OAM tile is painted, opaque
    out = _read(dest, 3, 5, 5)
    assert out is not None
    _near(out.getpixel((0, 0)), (0, 0, 255))
    # a neighboring z3 tile with no OAM still has BMNG filled (no hole)
    neighbor = _read(dest, 3, 4, 5)
    assert neighbor is not None
    _near(neighbor.getpixel((0, 0)), (100, 60, 20))
