"""The manifest builder must search wide and keep a deep, diverse stack (fix E)."""

import json
from datetime import date, timedelta
from pathlib import Path

from planetarble.acquisition.hls import HLSMosaicTask, HLSScene
from planetarble.core.models import HLSConfig
from planetarble.processing.hls import HLSSceneManifestBuilder


class WideStubClient:
    def __init__(self, scenes):
        self._scenes = scenes
        self.max_items_seen = None

    def fetch_scenes(self, task, *, max_items=200, include_fallback=True):
        self.max_items_seen = max_items
        return {"primary": list(self._scenes)}


def _task() -> HLSMosaicTask:
    return HLSMosaicTask(
        z=1, x=1, y=0, bbox=(0.0, 0.0, 180.0, 85.0),
        start_date=date(2024, 4, 1), end_date=date(2024, 10, 31),
        season_name="north", hemisphere="north",
        collections=("HLSS30",), fallback_collections=("HLSL30",),
        max_cloud=40.0, fallback_max_cloud=50.0,
    )


def test_builder_searches_wide_and_keeps_deep_stack(tmp_path: Path) -> None:
    # 30 scenes spread across Apr-Oct
    base = date(2024, 4, 1)
    scenes = [
        HLSScene(
            collection_id="HLSS30",
            item_id=f"S{i:02d}",
            acquisition_date=base + timedelta(days=i * 6),
            cloud_cover=float((i * 7) % 40),
            bbox=(0.0, 0.0, 1.0, 1.0),
            bands={"B02": "u", "B03": "u", "B04": "u"},
            qa_asset="qa",
        )
        for i in range(30)
    ]
    client = WideStubClient(scenes)
    builder = HLSSceneManifestBuilder(HLSConfig(target_zoom=1), client=client)

    plan_path = tmp_path / "plan.ndjson"
    plan_path.write_text(json.dumps(_task().to_mapping()), encoding="utf-8")

    manifest = builder.build(
        plan_path, max_scenes_per_tile=12, search_limit=100, progress_interval=1
    )
    # the STAC search is decoupled from the keep count and goes wide
    assert client.max_items_seen == 100
    # a deep stack is kept (far above the old hard cap of 3)
    assert len(manifest.scenes) == 12
    # and it spans the season rather than clustering
    dates = sorted(s.acquisition_date for s in manifest.scenes)
    assert (dates[-1] - dates[0]).days > 120
