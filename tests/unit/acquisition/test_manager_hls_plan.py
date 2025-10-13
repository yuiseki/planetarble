from pathlib import Path
from time import sleep

from planetarble.acquisition import AcquisitionManager
from planetarble.core.models import HLSConfig


def test_build_hls_plan_reuses_existing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manager = AcquisitionManager(data_dir)

    plan_path = data_dir / "plans" / "hls_z2_plan.ndjson"
    config = HLSConfig(target_zoom=2, land_buffer_km=0.0)

    summary_first = manager.build_hls_plan(config, destination=plan_path, force=True)
    assert summary_first is not None
    initial_mtime = plan_path.stat().st_mtime

    sleep(0.01)
    summary_second = manager.build_hls_plan(config, destination=plan_path, force=False)
    assert summary_second is not None
    assert plan_path.stat().st_mtime == initial_mtime
    assert summary_second.tile_count == summary_first.tile_count
