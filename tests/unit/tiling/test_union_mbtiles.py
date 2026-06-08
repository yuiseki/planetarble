"""Tests for union_mbtiles (combine disjoint Quadrans pieces)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from planetarble.tiling.mbtiles import ingest_xyz_dir, union_mbtiles


def _make_mbtiles(tmp: Path, name: str, tiles) -> Path:
    src = tmp / f"{name}_dir"
    for z, x, y, data in tiles:
        p = src / str(z) / str(x) / f"{y}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    out = tmp / f"{name}.mbtiles"
    ingest_xyz_dir(src, out, tile_format="jpg",
                   metadata={"name": "GSI", "attribution": "GSI", "bounds": "1,2,3,4"})
    return out


def test_union_combines_disjoint_pieces(tmp_path: Path) -> None:
    a = _make_mbtiles(tmp_path, "north", [(16, 1, 1, b"n"), (17, 2, 2, b"n2")])
    b = _make_mbtiles(tmp_path, "east", [(16, 5, 5, b"e"), (18, 9, 9, b"e3")])
    out = tmp_path / "all.mbtiles"

    union_mbtiles([a, b], out)

    conn = sqlite3.connect(str(out))
    try:
        assert conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0] == 4
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
        assert meta["minzoom"] == "16" and meta["maxzoom"] == "18"
        assert meta["format"] == "jpg"
        assert meta["attribution"] == "GSI" and meta["bounds"] == "1,2,3,4"
    finally:
        conn.close()


def test_union_overlap_first_input_wins(tmp_path: Path) -> None:
    a = _make_mbtiles(tmp_path, "first", [(16, 1, 1, b"AAA")])
    b = _make_mbtiles(tmp_path, "second", [(16, 1, 1, b"BBB")])
    out = tmp_path / "u.mbtiles"

    union_mbtiles([a, b], out)

    conn = sqlite3.connect(str(out))
    try:
        rows = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=16 AND tile_column=1"
        ).fetchall()
        assert len(rows) == 1 and rows[0][0] == b"AAA"  # first input wins (OR IGNORE)
    finally:
        conn.close()


def test_union_requires_inputs(tmp_path: Path) -> None:
    try:
        union_mbtiles([], tmp_path / "x.mbtiles")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_union_chunked_keeps_all_tiles(tmp_path: Path) -> None:
    # base is the first input (copied verbatim); the rest are appended in tiny
    # chunks. With chunk_size=1 every row is its own committed transaction, which
    # exercises the chunk loop and the separate-read-connection path.
    base = _make_mbtiles(tmp_path, "base", [(16, 1, 1, b"b1"), (16, 1, 2, b"b2")])
    b = _make_mbtiles(tmp_path, "b", [(17, 3, 3, b"x3"), (17, 3, 4, b"x4"), (18, 9, 9, b"x9")])
    c = _make_mbtiles(tmp_path, "c", [(16, 5, 5, b"c5")])
    out = tmp_path / "all.mbtiles"

    seen = []
    union_mbtiles([base, b, c], out, chunk_size=1, on_progress=seen.append)

    conn = sqlite3.connect(str(out))
    try:
        assert conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0] == 6
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
        assert meta["minzoom"] == "16" and meta["maxzoom"] == "18"
    finally:
        conn.close()
    # 4 non-base rows appended one per chunk -> running counts 1,2,3,4
    assert seen == [1, 2, 3, 4]


def test_union_chunked_first_input_wins_on_overlap(tmp_path: Path) -> None:
    a = _make_mbtiles(tmp_path, "first", [(16, 1, 1, b"AAA")])
    b = _make_mbtiles(tmp_path, "second", [(16, 1, 1, b"BBB"), (16, 2, 2, b"NEW")])
    out = tmp_path / "u.mbtiles"

    union_mbtiles([a, b], out, chunk_size=1)

    conn = sqlite3.connect(str(out))
    try:
        assert conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=16 AND tile_column=1"
        ).fetchone()[0] == b"AAA"  # base (first input) wins
        assert conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0] == 2  # NEW added
    finally:
        conn.close()
