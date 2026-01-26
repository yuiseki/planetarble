from datetime import datetime, timezone

from pystac import Asset, Item

from planetarble.acquisition.sentinel_2 import _build_scene


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
