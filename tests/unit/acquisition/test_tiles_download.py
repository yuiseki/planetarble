"""Tests for the parallel XYZ tile downloader (injected getter, no network)."""

from __future__ import annotations

from pathlib import Path

from planetarble.acquisition.tiles import download_xyz_tiles, tile_path


class _Resp:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


def test_skips_cached_counts_ok_and_404(tmp_path: Path) -> None:
    # pre-create a cached tile (8,1,3)
    p = tile_path(tmp_path, 8, 1, 3, "jpg")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"cached")

    def fake_get(url: str, timeout: int):
        return _Resp(200, b"img") if url.endswith("8/1/1.jpg") else _Resp(404)

    stats = download_xyz_tiles(
        [(8, 1, 1), (8, 1, 2), (8, 1, 3)],
        out_dir=tmp_path, template="http://x/{z}/{x}/{y}.jpg", ext="jpg",
        workers=3, http_get=fake_get, sleep=lambda s: None,
    )
    assert stats.ok == 1
    assert stats.http_404 == 1
    assert stats.cached == 1
    assert tile_path(tmp_path, 8, 1, 1, "jpg").read_bytes() == b"img"
    assert stats.downloaded_bytes == 3  # len(b"img")


def test_cooldown_then_recovers_on_block(tmp_path: Path) -> None:
    calls = {"n": 0}

    def fake_get(url: str, timeout: int):
        calls["n"] += 1
        if calls["n"] <= 2:  # blocked twice, then succeeds
            return _Resp(429)
        return _Resp(200, b"ok")

    stats = download_xyz_tiles(
        [(10, 5, 5)],
        out_dir=tmp_path, template="http://x/{z}/{x}/{y}.jpg",
        workers=1, retries=5, http_get=fake_get, sleep=lambda s: None,
    )
    assert stats.ok == 1
    assert stats.blocked == 2  # cooled down twice before succeeding
    assert calls["n"] == 3
