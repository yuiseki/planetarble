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

        canonical_size: Optional[Tuple[int, int]] = None
        updates = []
        for z, x, y, bdata in conn.execute(
            "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"
        ):
            base_img = Image.open(io.BytesIO(bdata)).convert("RGBA")
            if canonical_size is None:
                canonical_size = base_img.size
            key = (z, x, y)
            odata = overlay_tiles.pop(key, None)
            if odata is not None:
                ov_img = Image.open(io.BytesIO(odata)).convert("RGBA")
                # tile sizes can differ (e.g. 512 base vs 256 overlay); match the base
                if ov_img.size != base_img.size:
                    ov_img = ov_img.resize(base_img.size, Image.Resampling.LANCZOS)
                out_img = Image.alpha_composite(base_img, ov_img)
            else:
                out_img = base_img
            updates.append((z, x, y, _encode(out_img)))

        # overlay-only tiles (deeper zooms with no base) inserted re-encoded,
        # normalized to the planet's canonical tile size
        for (z, x, y), odata in overlay_tiles.items():
            ov_img = Image.open(io.BytesIO(odata)).convert("RGBA")
            if canonical_size is not None and ov_img.size != canonical_size:
                ov_img = ov_img.resize(canonical_size, Image.Resampling.LANCZOS)
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


def _xyz_tile(lon: float, lat: float, z: int) -> Tuple[int, int]:
    import math

    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    lat = max(min(lat, 85.0511287798066), -85.0511287798066)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return max(0, min(x, n - 1)), max(0, min(y, n - 1))


def fetch_tile_overzoom(conn, z: int, x: int, y: int, *, tile_size: int = 256):
    """Return tile (z,x,y) as an RGBA image, upscaling an ancestor if absent.

    Assumes XYZ tile rows (planetarble tiles with ``--convention xyz``). Returns
    None when no ancestor has data, so callers can leave a hole for the viewer
    to overzoom instead of baking a transparent tile.
    """
    from PIL import Image

    for zz in range(z, -1, -1):
        d = z - zz
        ax, ay = x >> d, y >> d
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (zz, ax, ay),
        ).fetchone()
        if row is None:
            continue
        img = Image.open(io.BytesIO(row[0])).convert("RGBA")
        if d == 0:
            return img if img.size == (tile_size, tile_size) else img.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
        factor = 1 << d
        w, h = img.size
        sub_w, sub_h = w // factor, h // factor
        ox = (x - (ax << d)) * sub_w
        oy = (y - (ay << d)) * sub_h
        crop = img.crop((ox, oy, ox + sub_w, oy + sub_h))
        return crop.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
    return None


def composite_overzoom(
    sources,
    destination: Path,
    *,
    aoi_bbox: Tuple[float, float, float, float],
    min_zoom: int,
    max_zoom: int,
    tile_format: str = "webp",
    quality: int = 85,
    tile_size: int = 256,
) -> Path:
    """Build a stacked planet over an AOI, filling lower sources by overzoom.

    ``sources`` are mbtiles paths ordered bottom to top (e.g. BMNG, HLS, OAM).
    For every output tile in the AOI at each zoom, each source contributes its
    tile or an upscaled ancestor, composited in order, so the finest source is
    on top and lower sources fill underneath (no holes). High zooms are bounded
    to the AOI bbox to keep tile counts feasible.
    """
    from PIL import Image

    pil_format = _PIL_SAVE_FORMAT.get(tile_format.lower())
    if pil_format is None:
        raise ValueError(f"Unsupported tile_format: {tile_format}")
    conns = [sqlite3.connect(str(p)) for p in sources]
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        out = sqlite3.connect(str(destination))
        out.execute("CREATE TABLE metadata (name text, value text)")
        out.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob)")
        out.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")

        minx, miny, maxx, maxy = aoi_bbox
        written = 0
        for z in range(min_zoom, max_zoom + 1):
            x0, y0 = _xyz_tile(minx, maxy, z)  # NW
            x1, y1 = _xyz_tile(maxx, miny, z)  # SE
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    composed = None
                    for conn in conns:
                        layer = fetch_tile_overzoom(conn, z, x, y, tile_size=tile_size)
                        if layer is None:
                            continue
                        composed = layer if composed is None else Image.alpha_composite(composed, layer)
                    if composed is None or composed.getextrema()[3][1] == 0:
                        continue  # no data / fully transparent -> leave hole for overzoom
                    buf = io.BytesIO()
                    save_img = composed.convert("RGB") if pil_format == "JPEG" else composed
                    save_img.save(buf, format=pil_format, quality=quality)
                    out.execute(
                        "INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?,?,?,?)",
                        (z, x, y, buf.getvalue()),
                    )
                    written += 1
        for key, value in (("format", tile_format), ("minzoom", min_zoom), ("maxzoom", max_zoom)):
            out.execute("INSERT INTO metadata (name, value) VALUES (?, ?)", (key, str(value)))
        out.commit()
        out.close()
    finally:
        for conn in conns:
            conn.close()
    return destination
