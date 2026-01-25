from pathlib import Path

import pytest

from planetarble.acquisition import AcquisitionManager
from planetarble.core.models import HLSConfig, HLSPlanRegion


def test_build_hls_plans_filters_by_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manager = AcquisitionManager(data_dir)
    config = HLSConfig(
        target_zoom=2,
        land_buffer_km=0.0,
        plan_regions=(
            HLSPlanRegion(name="tokyo"),
            HLSPlanRegion(name="osaka"),
        ),
    )

    called: list[str] = []

    def stub_build(self, cfg, region, force):  # type: ignore[no-untyped-def]
        called.append(region.name)
        plan_path = data_dir / "plans" / f"hls_z{cfg.target_zoom}_plan_{region.name}.ndjson"
        return type("Summary", (), {"path": plan_path, "zoom": cfg.target_zoom, "tile_count": 1, "season_counts": {}})()

    monkeypatch.setattr(AcquisitionManager, "_build_hls_plan_for_region", stub_build)

    summaries = manager.build_hls_plans(config, selected_region="tokyo", force=True)

    assert list(summaries.keys()) == ["tokyo"]
    assert called == ["tokyo"]


def test_build_hls_plans_unknown_region_raises(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manager = AcquisitionManager(data_dir)
    config = HLSConfig(plan_regions=(HLSPlanRegion(name="tokyo"),))

    with pytest.raises(ValueError, match="Unknown HLS plan region"):
        manager.build_hls_plans(config, selected_region="osaka")
