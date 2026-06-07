"""Tests for ingesting a z/x/y tile directory into an MBTiles archive."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from planetarble.tiling.mbtiles import ingest_xyz_dir, iter_xyz_dir


def _write_tile(root: Path, z: int, x: int, y: int, data: bytes, ext: str = "jpg") -> None:
    p = root / str(z) / str(x) / f"{y}.{ext}"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_iter_xyz_dir_yields_tiles_and_skips_non_tiles(tmp_path: Path) -> None:
    _write_tile(tmp_path, 8, 1, 2, b"a")
    _write_tile(tmp_path, 9, 3, 4, b"b")
    # stray files that must be ignored
    (tmp_path / "metadata.json").write_text("{}")
    (tmp_path / "8" / "1" / "notanumber.jpg").write_bytes(b"x")

    got = sorted((z, x, y) for z, x, y, _ext in iter_xyz_dir(tmp_path))
    assert got == [(8, 1, 2), (9, 3, 4)]


def test_ingest_creates_mbtiles_with_tms_rows_and_metadata(tmp_path: Path) -> None:
    src = tmp_path / "tiles"
    _write_tile(src, 2, 0, 0, b"img-200")
    _write_tile(src, 3, 1, 2, b"img-312")
    out = tmp_path / "out.mbtiles"

    n = ingest_xyz_dir(
        src, out, tile_format="jpg",
        metadata={"name": "t", "attribution": "GSI"},
    )
    assert n == 2

    conn = sqlite3.connect(str(out))
    try:
        # z3,x1,y2 -> TMS row = (1<<3)-1-2 = 5
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=3 AND tile_column=1 AND tile_row=5"
        ).fetchone()
        assert row is not None and row[0] == b"img-312"
        # z2,x0,y0 -> TMS row = (1<<2)-1-0 = 3
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=2 AND tile_column=0 AND tile_row=3"
        ).fetchone()
        assert row is not None and row[0] == b"img-200"
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
        assert meta["format"] == "jpg"
        assert meta["minzoom"] == "2" and meta["maxzoom"] == "3"
        assert meta["name"] == "t" and meta["attribution"] == "GSI"
    finally:
        conn.close()


def test_ingest_appends_to_existing_and_updates_maxzoom(tmp_path: Path) -> None:
    out = tmp_path / "all.mbtiles"

    a = tmp_path / "a"
    _write_tile(a, 5, 1, 1, b"z5")
    ingest_xyz_dir(a, out, tile_format="jpg")

    b = tmp_path / "b"
    _write_tile(b, 16, 100, 200, b"z16")
    n = ingest_xyz_dir(b, out, tile_format="jpg")
    assert n == 1

    conn = sqlite3.connect(str(out))
    try:
        count = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
        assert count == 2
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
        assert meta["minzoom"] == "5" and meta["maxzoom"] == "16"
    finally:
        conn.close()


def test_ingest_replaces_duplicate_coordinate(tmp_path: Path) -> None:
    out = tmp_path / "dup.mbtiles"
    a = tmp_path / "a"
    _write_tile(a, 10, 5, 5, b"old")
    ingest_xyz_dir(a, out, tile_format="jpg")

    b = tmp_path / "b"
    _write_tile(b, 10, 5, 5, b"new")
    ingest_xyz_dir(b, out, tile_format="jpg")

    conn = sqlite3.connect(str(out))
    try:
        rows = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=10 AND tile_column=5"
        ).fetchall()
        assert len(rows) == 1 and rows[0][0] == b"new"
    finally:
        conn.close()
