"""The ocean depth colour ramp referenced by the default config must ship."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from planetarble.core.models import OceanConfig
from planetarble.processing.ocean import _load_depth_ramp


@pytest.mark.xfail(
    strict=True,
    reason="src/planetarble/data/ocean/depth_ramp.json was never committed (it was "
    "hidden by the data/ rule in .gitignore); restore it from the build host",
)
def test_default_packaged_ramp_loads() -> None:
    entries = _load_depth_ramp(OceanConfig().depth_color_ramp)
    assert entries and all("depth" in e and len(e["color"]) >= 3 for e in entries)


def test_missing_packaged_ramp_has_an_actionable_error() -> None:
    with pytest.raises(FileNotFoundError, match="point ocean.depth_color_ramp at a JSON file"):
        _load_depth_ramp("planetarble:ocean/does_not_exist.json")


def test_ramp_from_a_file_path(tmp_path: Path) -> None:
    ramp = tmp_path / "ramp.json"
    ramp.write_text(json.dumps([{"depth": -100, "color": [0, 0, 255]}]), encoding="utf-8")
    assert _load_depth_ramp(str(ramp)) == [{"depth": -100, "color": [0, 0, 255]}]
