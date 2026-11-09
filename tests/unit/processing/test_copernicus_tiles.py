"""Copernicus WMS tile cache: which zoom is mosaicked, which files count."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.core.models import CopernicusConfig
from planetarble.processing.copernicus import (
    _collect_copernicus_tiles,
    _compression_for_format,
    _extension_for_format,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_only_the_requested_max_zoom_is_collected(tmp_path: Path) -> None:
    _touch(tmp_path / "9" / "1" / "1.jpg")
    tile = _touch(tmp_path / "10" / "5" / "7.jpg")
    records = _collect_copernicus_tiles(tmp_path, CopernicusConfig(min_zoom=8, max_zoom=10))
    assert records == [(tile, 10, 5, 7)]


def test_missing_max_zoom_falls_back_to_the_highest_cached(tmp_path: Path) -> None:
    _touch(tmp_path / "8" / "0" / "0.png")
    tile = _touch(tmp_path / "9" / "2" / "3.png")
    records = _collect_copernicus_tiles(tmp_path, CopernicusConfig(min_zoom=8, max_zoom=12))
    assert records == [(tile, 9, 2, 3)]


def test_zooms_outside_the_configured_range_are_ignored(tmp_path: Path) -> None:
    _touch(tmp_path / "13" / "0" / "0.jpg")
    assert _collect_copernicus_tiles(tmp_path, CopernicusConfig(min_zoom=8, max_zoom=12)) == []


def test_non_tile_entries_are_skipped(tmp_path: Path) -> None:
    keep = _touch(tmp_path / "10" / "4" / "2.JPEG")
    _touch(tmp_path / "10" / "4" / "notes.txt")        # not an image
    _touch(tmp_path / "10" / "4" / "tmp.jpg")          # stem is not a row
    _touch(tmp_path / "10" / "x" / "1.jpg")            # column is not a number
    (tmp_path / "10" / "4" / "5.jpg").mkdir()          # a directory named like a tile
    _touch(tmp_path / "10" / "readme.md")              # a file among the columns
    records = _collect_copernicus_tiles(tmp_path, CopernicusConfig(min_zoom=10, max_zoom=10))
    assert records == [(keep, 10, 4, 2)]


def test_tiles_are_sorted_by_column_then_row(tmp_path: Path) -> None:
    for x, y in [(3, 9), (2, 5), (2, 1)]:
        _touch(tmp_path / "10" / str(x) / f"{y}.jpg")
    records = _collect_copernicus_tiles(tmp_path, CopernicusConfig(min_zoom=10, max_zoom=10))
    assert [(x, y) for _, _, x, y in records] == [(2, 1), (2, 5), (3, 9)]


@pytest.mark.parametrize(
    ("fmt", "ext", "compression"),
    [("image/jpeg", "jpg", "JPEG"), ("IMAGE/JPG", "jpg", "JPEG"), ("image/png", "png", "DEFLATE"),
     ("image/webp", "webp", "DEFLATE"), ("image/tiff", "tiff", "DEFLATE"), ("", "bin", "DEFLATE")],
)
def test_format_helpers(fmt: str, ext: str, compression: str) -> None:
    assert _extension_for_format(fmt) == ext
    assert _compression_for_format(fmt) == compression
