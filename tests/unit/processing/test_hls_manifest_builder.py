import json
from datetime import date
from pathlib import Path

import pytest

from planetarble.acquisition.hls import HLSMosaicTask, HLSScene
from planetarble.core.models import HLSConfig
from planetarble.processing.hls import HLSSceneManifestBuilder, scene_to_mapping


class StubHLSClient:
    def __init__(self, scene: HLSScene) -> None:
        self._scene = scene
        self.calls = 0

    def fetch_scenes(self, task: HLSMosaicTask, *, max_items: int = 200, include_fallback: bool = True):  # type: ignore[no-untyped-def]
        self.calls += 1
        return {"primary": [self._scene]}


def test_manifest_builder_deduplicates_scenes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    scene = HLSScene(
        collection_id="HLSS30",
        item_id="SCENE_001",
        acquisition_date=date(2024, 7, 15),
        cloud_cover=5.0,
        bbox=(0.0, 0.0, 1.0, 1.0),
        bands={"B02": "https://example/B02.tif"},
        qa_asset="https://example/Fmask.tif",
    )
    client = StubHLSClient(scene)
    builder = HLSSceneManifestBuilder(HLSConfig(target_zoom=1), client=client)

    plan_path = tmp_path / "plan.ndjson"
    tasks = [
        HLSMosaicTask(
            z=1,
            x=1,
            y=0,
            bbox=(0.0, 0.0, 180.0, 85.0),
            start_date=date(2024, 4, 1),
            end_date=date(2024, 10, 31),
            season_name="north",
            hemisphere="north",
            collections=("HLSS30",),
            fallback_collections=("HLSL30",),
            max_cloud=40.0,
            fallback_max_cloud=50.0,
        ),
        HLSMosaicTask(
            z=1,
            x=1,
            y=1,
            bbox=(0.0, -85.0, 180.0, 0.0),
            start_date=date(2023, 10, 1),
            end_date=date(2024, 4, 30),
            season_name="south",
            hemisphere="south",
            collections=("HLSS30",),
            fallback_collections=("HLSL30",),
            max_cloud=40.0,
            fallback_max_cloud=50.0,
        ),
    ]
    plan_path.write_text("\n".join(json.dumps(task.to_mapping()) for task in tasks), encoding="utf-8")

    caplog.set_level("INFO")
    manifest = builder.build(plan_path, max_tiles=None, max_scenes_per_tile=2, progress_interval=1)
    assert client.calls == len(tasks)
    assert len(manifest.scenes) == 1
    mapping = scene_to_mapping(manifest.scenes[0])
    assert mapping["collection_id"] == "HLSS30"
    assert any(record.message.startswith("process progress") for record in caplog.records)
