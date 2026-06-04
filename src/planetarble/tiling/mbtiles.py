"""Utilities for manipulating MBTiles archives."""

from __future__ import annotations

import io
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Tuple

_PIL_SAVE_FORMAT = {"webp": "WEBP", "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG"}


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


def composite_mbtiles(
    base_path: Path,
    overlay_path: Path,
    destination: Path,
    *,
    tile_format: str = "webp",
    quality: int = 85,
) -> Path:
    """Alpha-composite an overlay MBTiles over a base, painting finer over coarser.

    Unlike ``merge_mbtiles`` (which replaces whole tile blobs), this decodes each
    tile and composites the overlay over the base per pixel, so an overlay's
    transparent nodata lets the base show through and its imagery paints over
    the base only where it actually has data. Every tile is re-encoded to
    ``tile_format`` so the output is uniform (required for a single PMTiles
    tile type). Overlay tiles with no base counterpart (deeper zooms) are
    inserted as-is.
    """
    from PIL import Image  # local import; Pillow only needed for compositing

    if not base_path.exists():
        raise FileNotFoundError(f"Base MBTiles not found: {base_path}")
    if not overlay_path.exists():
        raise FileNotFoundError(f"Overlay MBTiles not found: {overlay_path}")
    pil_format = _PIL_SAVE_FORMAT.get(tile_format.lower())
    if pil_format is None:
        raise ValueError(f"Unsupported tile_format: {tile_format}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    shutil.copy2(base_path, destination)

    def _encode(image) -> bytes:
        buf = io.BytesIO()
        if pil_format == "JPEG":
            image = image.convert("RGB")
        image.save(buf, format=pil_format, quality=quality)
        return buf.getvalue()

    with sqlite3.connect(str(destination)) as conn:
        conn.execute(f"ATTACH DATABASE '{overlay_path}' AS overlay")
        overlay_tiles: Dict[Tuple[int, int, int], bytes] = {
            (z, x, y): data
            for z, x, y, data in conn.execute(
                "SELECT zoom_level, tile_column, tile_row, tile_data FROM overlay.tiles"
            )
        }

        updates = []
        for z, x, y, bdata in conn.execute(
            "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"
        ):
            base_img = Image.open(io.BytesIO(bdata)).convert("RGBA")
            key = (z, x, y)
            odata = overlay_tiles.pop(key, None)
            if odata is not None:
                ov_img = Image.open(io.BytesIO(odata)).convert("RGBA")
                out_img = Image.alpha_composite(base_img, ov_img)
            else:
                out_img = base_img
            updates.append((z, x, y, _encode(out_img)))

        # overlay-only tiles (deeper zooms with no base) inserted re-encoded
        for (z, x, y), odata in overlay_tiles.items():
            ov_img = Image.open(io.BytesIO(odata)).convert("RGBA")
            updates.append((z, x, y, _encode(ov_img)))

        # ``updates`` is the complete final tile set (every base tile re-encoded
        # plus overlay-only tiles), so rewrite the table rather than relying on
        # a unique constraint for INSERT OR REPLACE.
        conn.execute("DELETE FROM tiles")
        conn.executemany(
            "INSERT INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
            updates,
        )

        min_zoom = conn.execute("SELECT MIN(zoom_level) FROM tiles").fetchone()[0]
        max_zoom = conn.execute("SELECT MAX(zoom_level) FROM tiles").fetchone()[0]
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'").fetchone():
            for key, value in (("format", tile_format), ("minzoom", min_zoom), ("maxzoom", max_zoom)):
                if value is None:
                    continue
                if conn.execute("UPDATE metadata SET value=? WHERE name=?", (str(value), key)).rowcount == 0:
                    conn.execute("INSERT INTO metadata (name, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.execute("DETACH DATABASE overlay")
    return destination
