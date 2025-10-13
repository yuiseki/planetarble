from pathlib import Path

from planetarble.acquisition.hls import HLSMosaicPlanner
from planetarble.core.models import HLSConfig


def test_hls_planner_writes_plan(tmp_path: Path) -> None:
    config = HLSConfig(target_zoom=2, land_buffer_km=0.0)
    planner = HLSMosaicPlanner(config)
    summary = planner.write_plan(tmp_path / "plan.ndjson")

    assert summary.path.exists()
    assert summary.tile_count > 0

    first_line = summary.path.read_text(encoding="utf-8").splitlines()[0]
    assert "\"z\":" in first_line
