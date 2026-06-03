"""Tests for miniplanet tagging and sharding of HLS plans."""

from __future__ import annotations

from pathlib import Path

from planetarble.acquisition.hls import (
    UNASSIGNED_MINIPLANET,
    HLSMosaicPlanner,
    HLSMosaicTask,
    iter_plan,
    split_plan_by_miniplanet,
    task_miniplanet_id,
)
from planetarble.acquisition.miniplanets import tile_to_miniplanet_id
from planetarble.core.models import HLSConfig


def test_planner_tags_tasks_with_miniplanet(tmp_path: Path) -> None:
    config = HLSConfig(target_zoom=10, land_buffer_km=0.0)
    planner = HLSMosaicPlanner(config)
    for task in planner.iter_tasks():
        assert task.miniplanet == tile_to_miniplanet_id(task.z, task.x, task.y)
        assert task.miniplanet is not None  # z10 always resolves
        break


def test_task_mapping_round_trips_miniplanet() -> None:
    config = HLSConfig(target_zoom=10, land_buffer_km=0.0)
    planner = HLSMosaicPlanner(config)
    task = next(iter(planner.iter_tasks()))
    restored = HLSMosaicTask.from_mapping(task.to_mapping())
    assert restored.miniplanet == task.miniplanet


def test_to_mapping_omits_miniplanet_when_absent() -> None:
    config = HLSConfig(target_zoom=2, land_buffer_km=0.0)  # below BASE_ZOOM -> None
    planner = HLSMosaicPlanner(config)
    task = next(iter(planner.iter_tasks()))
    assert task.miniplanet is None
    assert "miniplanet" not in task.to_mapping()


def test_task_miniplanet_id_recomputes_when_untagged() -> None:
    task = HLSMosaicTask.from_mapping(
        {
            "z": 10,
            "x": 909,
            "y": 403,
            "bbox": [0, 0, 1, 1],
            "start_date": "2024-04-01",
            "end_date": "2024-10-31",
        }
    )
    assert task.miniplanet is None
    assert task_miniplanet_id(task) == tile_to_miniplanet_id(10, 909, 403)


def test_split_plan_by_miniplanet(tmp_path: Path) -> None:
    config = HLSConfig(target_zoom=10, land_buffer_km=0.0)
    planner = HLSMosaicPlanner(config)
    plan_path = tmp_path / "hls_z10_plan.ndjson"
    # Write a small synthetic plan spanning two distinct miniplanets.
    tiles = [(10, 909, 403), (10, 160, 395), (10, 909, 404)]
    with plan_path.open("w", encoding="utf-8") as handle:
        import json

        for (z, x, y) in tiles:
            handle.write(
                json.dumps(
                    {
                        "z": z,
                        "x": x,
                        "y": y,
                        "bbox": [0, 0, 1, 1],
                        "start_date": "2024-04-01",
                        "end_date": "2024-10-31",
                        "miniplanet": tile_to_miniplanet_id(z, x, y),
                    },
                    sort_keys=True,
                )
            )
            handle.write("\n")

    shards = split_plan_by_miniplanet(plan_path, tmp_path / "shards")

    expected_keys = {tile_to_miniplanet_id(*t) for t in tiles}
    assert set(shards.keys()) == expected_keys
    # Every shard file exists, lives in the shard dir, and round-trips.
    total = 0
    for key, shard_path in shards.items():
        assert shard_path.exists()
        assert shard_path.parent == (tmp_path / "shards")
        tasks = list(iter_plan(shard_path))
        assert tasks
        assert all(task_miniplanet_id(t) == key for t in tasks)
        total += len(tasks)
    assert total == len(tiles)


def test_split_plan_groups_untagged_under_unassigned(tmp_path: Path) -> None:
    import json

    plan_path = tmp_path / "plan.ndjson"
    # z5 is below BASE_ZOOM, so it cannot resolve to a miniplanet.
    with plan_path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "z": 5,
                    "x": 0,
                    "y": 0,
                    "bbox": [0, 0, 1, 1],
                    "start_date": "2024-04-01",
                    "end_date": "2024-10-31",
                },
                sort_keys=True,
            )
        )
        handle.write("\n")

    shards = split_plan_by_miniplanet(plan_path, tmp_path / "shards")
    assert set(shards.keys()) == {UNASSIGNED_MINIPLANET}
