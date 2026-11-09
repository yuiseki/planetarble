import io
import sqlite3
from pathlib import Path

import pytest

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


def test_set_mbtiles_metadata_updates_and_inserts(tmp_path) -> None:
    import sqlite3

    from planetarble.tiling.mbtiles import set_mbtiles_metadata

    path = tmp_path / "m.mbtiles"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.execute("INSERT INTO metadata VALUES ('name', 'old')")
    conn.commit()
    conn.close()

    set_mbtiles_metadata(path, {"name": "planet", "attribution": "A; B"})

    rows = dict(sqlite3.connect(path).execute("SELECT name, value FROM metadata").fetchall())
    assert rows == {"name": "planet", "attribution": "A; B"}


# --- overlays in another format, or with transparency -------------------------
# The 2026-01 planet_2024_12z_*_hls archives were BMNG (JPEG) with HLS tiles
# (transparent WebP) copied over them blob for blob: one archive held two
# formats, and the HLS nodata punched holes through the BMNG beneath.

Image = pytest.importorskip("PIL.Image")

RED, BLUE = (200, 30, 30), (30, 30, 200)


def _encode(img, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _half_blue_half_clear() -> "Image.Image":
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (4, 8), (*BLUE, 255)), (0, 0))
    return img


def _tile(path: Path, z: int, x: int, y: int) -> bytes:
    with sqlite3.connect(str(path)) as conn:
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?", (z, x, y)
        ).fetchone()
    assert row is not None
    return row[0]


def _near(pixel, expected, tol: int = 40) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(pixel[:3], expected, strict=True))


@pytest.fixture
def jpeg_base_webp_overlay(tmp_path: Path):
    base, overlay = tmp_path / "base.mbtiles", tmp_path / "overlay.mbtiles"
    red_jpeg = _encode(Image.new("RGB", (8, 8), RED), "JPEG", quality=95)
    _create_mbtiles(base, [(11, 1, 2, red_jpeg), (11, 5, 5, red_jpeg)])
    clear_webp = _encode(_half_blue_half_clear(), "WEBP", lossless=True)
    _create_mbtiles(overlay, [(11, 1, 2, clear_webp), (12, 2, 4, clear_webp)])
    out = merge_mbtiles(base, overlay, destination=tmp_path / "merged.mbtiles")
    return out, red_jpeg


def test_merge_writes_overlay_tiles_in_the_base_format(jpeg_base_webp_overlay) -> None:
    out, _ = jpeg_base_webp_overlay
    for z, x, y in ((11, 1, 2), (12, 2, 4)):
        assert Image.open(io.BytesIO(_tile(out, z, x, y))).format == "JPEG"


def test_merge_lets_the_base_show_through_the_overlay_nodata(jpeg_base_webp_overlay) -> None:
    out, _ = jpeg_base_webp_overlay
    img = Image.open(io.BytesIO(_tile(out, 11, 1, 2))).convert("RGB")
    assert _near(img.getpixel((1, 4)), BLUE)
    assert _near(img.getpixel((6, 4)), RED)


def test_merge_leaves_base_tiles_outside_the_overlay_untouched(jpeg_base_webp_overlay) -> None:
    out, red_jpeg = jpeg_base_webp_overlay
    assert _tile(out, 11, 5, 5) == red_jpeg


def test_merge_copies_an_opaque_overlay_of_the_same_format_as_is(tmp_path: Path) -> None:
    base, overlay = tmp_path / "base.mbtiles", tmp_path / "overlay.mbtiles"
    _create_mbtiles(base, [(11, 1, 2, _encode(Image.new("RGB", (8, 8), RED), "JPEG"))])
    blue_jpeg = _encode(Image.new("RGB", (8, 8), BLUE), "JPEG", quality=70)
    _create_mbtiles(overlay, [(11, 1, 2, blue_jpeg)])
    out = merge_mbtiles(base, overlay, destination=tmp_path / "merged.mbtiles")
    assert _tile(out, 11, 1, 2) == blue_jpeg
