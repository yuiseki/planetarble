"""Tests for the MBTiles sink and direct download-to-mbtiles path."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from planetarble.tiling.mbtiles import MbtilesSink, download_xyz_to_mbtiles


class _Resp:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


def test_sink_writes_tms_rows_and_metadata(tmp_path: Path) -> None:
    out = tmp_path / "s.mbtiles"
    with MbtilesSink(out, tile_format="jpg", batch_size=1, metadata={"name": "x"}) as sink:
        sink(3, 1, 1, b"a")  # TMS row = (1<<3)-1-1 = 6
        sink(4, 0, 0, b"b")  # TMS row = (1<<4)-1-0 = 15

    conn = sqlite3.connect(str(out))
    try:
        assert conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=3 AND tile_column=1 AND tile_row=6"
        ).fetchone()[0] == b"a"
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
        assert meta["format"] == "jpg"
        assert meta["minzoom"] == "3" and meta["maxzoom"] == "4"
        assert meta["name"] == "x"
    finally:
        conn.close()


def test_sink_contains_reflects_existing(tmp_path: Path) -> None:
    out = tmp_path / "c.mbtiles"
    with MbtilesSink(out) as sink:
        sink(5, 2, 2, b"d")
    # re-open: previously written tile is now "contained"
    with MbtilesSink(out) as sink2:
        assert sink2.contains(5, 2, 2) is True
        assert sink2.contains(5, 2, 3) is False


def test_download_to_mbtiles_skips_cached_and_counts(tmp_path: Path) -> None:
    out = tmp_path / "d.mbtiles"
    served = {"3/1/1.jpg": b"t1", "3/2/2.jpg": b"t2"}

    def fake_get(url: str, timeout: int):
        for k, v in served.items():
            if url.endswith(k):
                return _Resp(200, v)
        return _Resp(404)

    stats = download_xyz_to_mbtiles(
        [(3, 1, 1), (3, 2, 2), (3, 9, 9)],
        mbtiles_path=out, template="http://x/{z}/{x}/{y}.jpg", ext="jpg",
        workers=2, batch_size=1, http_get=fake_get, sleep=lambda s: None,
    )
    assert stats.ok == 2 and stats.http_404 == 1

    # second run: both downloaded tiles are now cached
    stats2 = download_xyz_to_mbtiles(
        [(3, 1, 1), (3, 2, 2)],
        mbtiles_path=out, template="http://x/{z}/{x}/{y}.jpg", ext="jpg",
        workers=2, batch_size=1, http_get=fake_get, sleep=lambda s: None,
    )
    assert stats2.cached == 2 and stats2.ok == 0
