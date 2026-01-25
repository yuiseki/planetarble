"""Utilities for manipulating MBTiles archives."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Optional


def merge_mbtiles(
    base_path: Path,
    overlay_path: Path,
    *,
    destination: Optional[Path] = None,
) -> Path:
    """Copy base MBTiles and overlay tiles from another MBTiles archive."""

    if not base_path.exists():
        raise FileNotFoundError(f"Base MBTiles not found: {base_path}")
    if not overlay_path.exists():
        raise FileNotFoundError(f"Overlay MBTiles not found: {overlay_path}")
    output_path = destination or base_path.with_name(f"{base_path.stem}_merged{base_path.suffix}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    shutil.copy2(base_path, output_path)

    with sqlite3.connect(str(output_path)) as conn:
        conn.execute(f"ATTACH DATABASE '{overlay_path}' AS overlay")
        conn.execute(
            """
            DELETE FROM tiles
            WHERE (zoom_level, tile_column, tile_row) IN (
                SELECT zoom_level, tile_column, tile_row FROM overlay.tiles
            )
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data)
            SELECT zoom_level, tile_column, tile_row, tile_data
            FROM overlay.tiles
            """
        )
        min_zoom = conn.execute("SELECT MIN(zoom_level) FROM tiles").fetchone()[0]
        max_zoom = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0]
        meta_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
        ).fetchone()
        if meta_exists:
            for key, value in (("minzoom", min_zoom), ("maxzoom", max_zoom)):
                if value is None:
                    continue
                updated = conn.execute(
                    "UPDATE metadata SET value=? WHERE name=?",
                    (str(value), key),
                ).rowcount
                if updated == 0:
                    conn.execute(
                        "INSERT INTO metadata (name, value) VALUES (?, ?)",
                        (key, str(value)),
                    )
        conn.commit()
        conn.execute("DETACH DATABASE overlay")
    return output_path
