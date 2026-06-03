import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pystac import Asset, Item

from planetarble.acquisition.sentinel_2 import (
    Sentinel2SceneManifestBuilder,
    _build_scene,
    _search_timeout,
)


def _make_item(assets: dict[str, str]) -> Item:
    item = Item(
        id="sentinel-item",
        geometry=None,
        bbox=[139.7, 35.6, 139.9, 35.8],
        datetime=datetime(2024, 6, 1, tzinfo=timezone.utc),
        properties={"eo:cloud_cover": 5.0},
    )
    for name, href in assets.items():
        item.assets[name] = Asset(href=href)
    return item


def test_build_scene_signs_assets() -> None:
    item = _make_item(
        {
            "B02": "https://example.com/B02.tif",
            "B03": "https://example.com/B03.tif",
            "B04": "https://example.com/B04.tif",
        }
    )
    scene = _build_scene(item, collection="sentinel-2-l2a", token="sig=abc", assets=("B02", "B03", "B04"))
    assert scene is not None
    assert scene.item_id == "sentinel-item"
    assert scene.cloud_cover == 5.0
    assert scene.assets["B02"].endswith("B02.tif")
    assert scene.assets["B03"].endswith("B03.tif")
    assert scene.assets["B04"].endswith("B04.tif")


def test_build_scene_requires_assets() -> None:
    item = _make_item({"B02": "https://example.com/B02.tif"})
    scene = _build_scene(item, collection="sentinel-2-l2a", token="sig=abc", assets=("B02", "B03", "B04"))
    assert scene is None


def test_store_cache_items_is_a_builder_method() -> None:
    # Regression: the search-timeout helpers were inserted mid-class, orphaning
    # _store_cache_items inside _search_timeout and dropping it from the class.
    assert callable(getattr(Sentinel2SceneManifestBuilder, "_store_cache_items", None))


def test_cache_store_load_round_trip(tmp_path: Path) -> None:
    builder = object.__new__(Sentinel2SceneManifestBuilder)
    builder._cache_dir = tmp_path
    builder._cache_ttl_days = 30
    item = _make_item({"B02": "https://example.com/B02.tif"})

    builder._store_cache_items("key1", [item])
    loaded = builder._load_cache_items("key1")

    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].id == "sentinel-item"


def test_search_timeout_raises_on_overrun() -> None:
    with pytest.raises(TimeoutError):
        with _search_timeout(1):
            time.sleep(2)


def test_search_timeout_noop_when_zero() -> None:
    with _search_timeout(0):
        pass  # zero/negative timeout must not arm a signal or raise
