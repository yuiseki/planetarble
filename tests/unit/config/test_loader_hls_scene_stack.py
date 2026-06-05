"""Tests for HLS scene-stack depth config (fix E).

The temporal median is only as good as the stack feeding it. The old
hard-coded cap of 3 scenes per tile (which also limited the STAC search to 3
results) left the median powerless. These knobs let the search go wide and keep
a deep, sun-angle-diverse stack.
"""

from pathlib import Path

from planetarble.config import load_config
from planetarble.core.models import HLSConfig


def test_hls_scene_stack_defaults_are_deep() -> None:
    cfg = HLSConfig()
    # default must be far above the old hard cap of 3 so the median has votes
    assert cfg.scenes_per_tile >= 10
    # the STAC search must be decoupled from (and wider than) the keep count
    assert cfg.scene_search_limit > cfg.scenes_per_tile


def test_load_config_parses_hls_scene_stack(tmp_path: Path) -> None:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text(
        "\n".join(
            [
                "data_dir: data",
                "output_dir: output",
                "hls:",
                "  scenes_per_tile: 20",
                "  scene_search_limit: 150",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.hls.scenes_per_tile == 20
    assert isinstance(cfg.hls.scenes_per_tile, int)
    assert cfg.hls.scene_search_limit == 150
    assert isinstance(cfg.hls.scene_search_limit, int)
