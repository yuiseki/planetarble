"""Sentinel-2 mosaic COG must be lossless with nodata for overlay compositing.

Like the HLS path, the Sentinel-2 mosaic feeds the overlay tiler, which turns
nodata=0 (Sentinel-2 fill / outside the land cutline) transparent so the BMNG
floor shows through. A lossy JPEG COG drops that nodata mask (opaque black bay /
edges) and adds block artefacts, so the intermediate COG stays lossless.
"""

from __future__ import annotations

from pathlib import Path

from planetarble.processing.manager import _translate_sentinel2_rgb


class _Runner:
    def __init__(self) -> None:
        self.commands: list = []

    def run(self, command, description=""):  # noqa: ANN001
        self.commands.append([str(c) for c in command])


def _translate_cmd(commands):
    for cmd in commands:
        if cmd and cmd[0] == "gdal_translate":
            return cmd
    raise AssertionError("no gdal_translate command issued")


def test_visual_cog_is_lossless_with_nodata(tmp_path: Path) -> None:
    runner = _Runner()
    _translate_sentinel2_rgb(
        runner,
        tmp_path / "rgb.vrt",
        tmp_path / "out.tif",
        bbox=(139.56, 35.53, 139.92, 35.82),
        scale_to_byte=False,
    )
    cmd = _translate_cmd(runner.commands)
    assert cmd[cmd.index("-of") + 1] == "COG"
    assert not any("JPEG" in part.upper() for part in cmd)
    assert any(part == "COMPRESS=DEFLATE" for part in cmd)
    # nodata is set even for the visual (TCI) path so holes composite cleanly
    assert "-a_nodata" in cmd and cmd[cmd.index("-a_nodata") + 1] == "0"
