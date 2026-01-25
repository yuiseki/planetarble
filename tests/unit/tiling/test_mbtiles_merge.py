import sqlite3
from pathlib import Path

from planetarble.tiling.mbtiles import merge_mbtiles


def _create_mbtiles(path: Path, tiles: list[tuple[int, int, int, bytes]]) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)"
        )
        conn.executemany(
            "INSERT INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)",
            tiles,
        )
        conn.commit()


def test_merge_mbtiles_overlays_tiles(tmp_path: Path) -> None:
    base = tmp_path / "base.mbtiles"
    overlay = tmp_path / "overlay.mbtiles"
    out = tmp_path / "merged.mbtiles"
    _create_mbtiles(base, [(10, 1, 2, b"base"), (11, 3, 4, b"keep")])
    _create_mbtiles(overlay, [(10, 1, 2, b"overlay")])

    merged = merge_mbtiles(base, overlay, destination=out)

    with sqlite3.connect(str(merged)) as conn:
        rows = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=10 AND tile_column=1 AND tile_row=2"
        ).fetchone()
        assert rows is not None
        assert rows[0] == b"overlay"
        rows = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=11 AND tile_column=3 AND tile_row=4"
        ).fetchone()
        assert rows is not None
        assert rows[0] == b"keep"
