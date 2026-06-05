"""Tests for the HLS display stretch (brighten dark surface-reflectance bands)."""

from __future__ import annotations

from pathlib import Path

from planetarble.processing.manager import _translate_hls_rgb


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


def test_default_stretch_uses_3000_and_gamma(tmp_path: Path) -> None:
    runner = _Runner()
    _translate_hls_rgb(runner, tmp_path / "rgb.vrt", tmp_path / "out.tif")
    cmd = _translate_cmd(runner.commands)
    i = cmd.index("-scale")
    # surface reflectance 0..3000 -> 0..255 (not the washed-out 0..10000)
    assert cmd[i + 1 : i + 5] == ["0", "3000", "0", "255"]
    assert "-exponent" in cmd
    assert cmd[cmd.index("-exponent") + 1] == "0.8"


def test_custom_scale_max_and_gamma(tmp_path: Path) -> None:
    runner = _Runner()
    _translate_hls_rgb(
        runner, tmp_path / "rgb.vrt", tmp_path / "out.tif", scale_max=2500, gamma=1.0
    )
    cmd = _translate_cmd(runner.commands)
    i = cmd.index("-scale")
    assert cmd[i + 1 : i + 5] == ["0", "2500", "0", "255"]
    # gamma 1.0 is a no-op, so no -exponent
    assert "-exponent" not in cmd
