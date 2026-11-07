"""OceanRenderer: ETOPO depth colour relief and hillshade commands."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from planetarble.core.models import OceanConfig
from planetarble.processing.ocean import OceanRenderer


class Recorder:
    def __init__(self) -> None:
        self.commands: list[tuple[str, list[str]]] = []

    def run(self, command, *, description: str) -> None:
        self.commands.append((description, [str(c) for c in command]))


def _renderer(tmp_path: Path, **overrides) -> tuple[OceanRenderer, Recorder]:
    ramp = tmp_path / "ramp.json"
    ramp.write_text(
        json.dumps([{"depth": -6000, "color": [8, 24, 64]}, {"depth": 0, "color": [120, 180, 220, 255]}]),
        encoding="utf-8",
    )
    config = replace(OceanConfig(enabled=True, depth_color_ramp=str(ramp)), **overrides)
    runner = Recorder()
    return OceanRenderer(config, runner, temp_dir=tmp_path / "tmp", output_dir=tmp_path / "out"), runner


def test_render_with_hillshade(tmp_path: Path) -> None:
    renderer, runner = _renderer(tmp_path)
    etopo = tmp_path / "etopo.tif"
    out = renderer.render(etopo)

    assert out == {"color": tmp_path / "out" / "etopo_depth_color.tif", "hillshade": tmp_path / "out" / "etopo_hillshade.tif"}
    ramp_table = tmp_path / "tmp" / "etopo_depth_ramp.txt"
    assert ramp_table.read_text(encoding="utf-8") == "-6000.0 8 24 64\n0.0 120 180 220\n"
    assert runner.commands == [
        ("apply color ramp to ETOPO depths",
         ["gdaldem", "color-relief", str(etopo), str(ramp_table), str(out["color"]), "-alpha"]),
        ("generate ocean hillshade",
         ["gdaldem", "hillshade", str(etopo), str(out["hillshade"]), "-az", "315.0", "-alt", "45.0", "-compute_edges"]),
    ]


def test_render_without_hillshade(tmp_path: Path) -> None:
    renderer, runner = _renderer(tmp_path, apply_hillshade=False)
    out = renderer.render(tmp_path / "etopo.tif")
    assert out["hillshade"] == Path()
    assert [d for d, _ in runner.commands] == ["apply color ramp to ETOPO depths"]
