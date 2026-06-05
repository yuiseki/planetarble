"""Sentinel-2 visual mosaic must put the cleanest scene on top (fix clouds).

gdalbuildvrt paints the last source on top with no nodata handling, so the
cloudiest scene used to cover everything. Order cloudiest-first / cleanest-last
and treat 0 as nodata so the lowest-cloud scene wins and its gaps fall through.
"""

from __future__ import annotations

from pathlib import Path

from planetarble.processing.manager import (
    _write_sentinel2_visual_list,
    _build_sentinel2_visual_vrt,
)


class _Runner:
    def __init__(self) -> None:
        self.commands: list = []

    def run(self, command, description=""):  # noqa: ANN001
        self.commands.append([str(c) for c in command])


def _scene(cloud, url):
    return {"cloud_cover": cloud, "assets": {"visual": url}}


def test_visual_list_orders_cleanest_last(tmp_path: Path) -> None:
    scenes = [_scene(0.5, "clean"), _scene(20.0, "mid"), _scene(80.0, "cloudy")]
    list_path = _write_sentinel2_visual_list(tmp_path, scenes, "visual")
    lines = list_path.read_text(encoding="utf-8").splitlines()
    # cloudiest first (bottom), cleanest last (top, painted last by gdalbuildvrt)
    assert lines == ["cloudy", "mid", "clean"]


def test_visual_vrt_treats_zero_as_nodata(tmp_path: Path) -> None:
    runner = _Runner()
    (tmp_path / "list.txt").write_text("x", encoding="utf-8")
    _build_sentinel2_visual_vrt(runner, tmp_path / "list.txt", tmp_path)
    cmd = runner.commands[0]
    assert "-srcnodata" in cmd and cmd[cmd.index("-srcnodata") + 1] == "0"
    assert "-vrtnodata" in cmd and cmd[cmd.index("-vrtnodata") + 1] == "0"
