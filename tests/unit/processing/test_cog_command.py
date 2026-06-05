"""Tests for the COG creation command.

The HLS mosaic carries nodata=0 over Fmask-masked (cloud) pixels so the tiler
can turn those holes transparent. A lossy JPEG COG both drops that nodata mask
(opaque black holes over the BMNG floor) and adds 8x8 block artefacts over
high-contrast urban scenes, so the intermediate COG must stay lossless.
"""

from __future__ import annotations

from pathlib import Path

from planetarble.processing.manager import _cog_command


def test_cog_command_is_lossless(tmp_path: Path) -> None:
    cmd = _cog_command(tmp_path / "in.tif", tmp_path / "out_cog.tif")
    assert cmd[0] == "gdal_translate"
    assert "-of" in cmd and cmd[cmd.index("-of") + 1] == "COG"
    # must NOT use lossy JPEG (drops nodata + adds block noise)
    assert not any("JPEG" in str(part).upper() for part in cmd)
    # a lossless compressor is used so exact nodata=0 survives for the tiler
    assert any(str(part) == "COMPRESS=DEFLATE" for part in cmd)
    # source and destination are the last two positional args
    assert cmd[-2:] == [str(tmp_path / "in.tif"), str(tmp_path / "out_cog.tif")]


def test_cog_command_custom_compress(tmp_path: Path) -> None:
    cmd = _cog_command(tmp_path / "in.tif", tmp_path / "out.tif", compress="LZW")
    assert any(str(part) == "COMPRESS=LZW" for part in cmd)
