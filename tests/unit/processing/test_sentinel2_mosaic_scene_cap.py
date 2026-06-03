"""Tests for capping the number of scenes used in a Sentinel-2 mosaic."""

from __future__ import annotations

from typing import Dict, Optional

from planetarble.core.models import Sentinel2Config
from planetarble.processing.manager import _select_mosaic_scenes


def _scene(item_id: str, cloud: Optional[float]) -> Dict[str, object]:
    scene: Dict[str, object] = {
        "item_id": item_id,
        "collection_id": "sentinel-2-l2a",
        "bbox": [139.0, 35.0, 140.0, 36.0],
        "assets": {"visual": "https://example.com/tci.tif"},
    }
    if cloud is not None:
        scene["cloud_cover"] = cloud
    return scene


def test_select_mosaic_scenes_takes_lowest_cloud() -> None:
    # Regression: the mosaic step downloaded the full asset for every covering
    # scene in the manifest (25 scenes x ~350MiB for a tiny bbox). Only the
    # lowest-cloud scenes are worth fetching.
    scenes = [_scene("b", 5.0), _scene("a", 0.1), _scene("c", None), _scene("d", 2.0)]

    selected = _select_mosaic_scenes(scenes, 2)

    assert [scene["item_id"] for scene in selected] == ["a", "d"]


def test_select_mosaic_scenes_zero_or_negative_disables_cap() -> None:
    scenes = [_scene("a", 1.0), _scene("b", 2.0)]

    assert _select_mosaic_scenes(scenes, 0) == scenes
    assert _select_mosaic_scenes(scenes, -1) == scenes


def test_select_mosaic_scenes_unknown_cloud_sorts_last() -> None:
    scenes = [_scene("x", None), _scene("y", 9.0)]

    selected = _select_mosaic_scenes(scenes, 1)

    assert selected[0]["item_id"] == "y"


def test_sentinel2_config_default_mosaic_max_scenes() -> None:
    assert Sentinel2Config().mosaic_max_scenes == 3
