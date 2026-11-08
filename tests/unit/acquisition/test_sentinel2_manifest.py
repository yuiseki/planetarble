"""Sentinel-2 scene manifest: scene choice, ordering, STAC cache, output.

Complements test_sentinel2_scene.py (single scenes, search retries). The STAC
client and SAS token endpoint are faked; items are real pystac Items.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from pystac import Asset, Item

from planetarble.acquisition import sentinel_2
from planetarble.acquisition.sentinel_2 import (
    Sentinel2SceneManifestBuilder,
    _bbox_covers,
)
from planetarble.core.models import Sentinel2Config

TARGET = (139.70, 35.65, 139.80, 35.70)
COVERING = [139.0, 35.0, 140.5, 36.2]


def _item(item_id: str, *, cloud: Any = 5.0, day: int = 1, bbox: Optional[List[float]] = None,
          assets: tuple = ("B02", "B03", "B04")) -> Item:
    minx, miny, maxx, maxy = bbox or COVERING
    # a real geometry: pystac drops bbox from to_dict() when geometry is None
    geometry = {"type": "Polygon", "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]]}
    item = Item(
        id=item_id, geometry=geometry, bbox=[minx, miny, maxx, maxy],
        datetime=datetime(2024, 6, day, tzinfo=timezone.utc), properties={"eo:cloud_cover": cloud},
    )
    for name in assets:
        item.assets[name] = Asset(href=f"https://s/{item_id}/{name}.tif")
    return item


class FakeClient:
    def __init__(self, items: List[Item]) -> None:
        self.items = items
        self.searches = 0

    def search(self, **kwargs: Any):
        self.searches += 1
        items = self.items
        return type("Search", (), {"items": lambda self: iter(items)})()


def _builder(items: List[Item], *, cache_dir: Optional[Path] = None, **config: Any) -> Sentinel2SceneManifestBuilder:
    builder = object.__new__(Sentinel2SceneManifestBuilder)
    builder._config = Sentinel2Config(**config)
    builder._cache_dir = cache_dir
    builder._cache_ttl_days = builder._config.cache_ttl_days
    builder._client = FakeClient(items)
    builder._tokens = {}
    return builder


@pytest.fixture(autouse=True)
def tokens(monkeypatch) -> List[str]:
    issued: List[str] = []

    def fake_token(collection, timeout):
        issued.append(collection)
        return "sig=1"

    monkeypatch.setattr(sentinel_2, "fetch_sas_token", fake_token)
    return issued


def test_scenes_are_ordered_by_cloud_then_newest(tokens: List[str]) -> None:
    items = [
        _item("cloudy", cloud=40.0),
        _item("clear_old", cloud=1.0, day=1),
        _item("clear_new", cloud=1.0, day=20),
        _item("unknown_cloud", cloud=None),
    ]
    manifest = _builder(items).build(bbox=TARGET)
    assert [s.item_id for s in manifest.scenes] == ["clear_new", "clear_old", "cloudy", "unknown_cloud"]
    assert tokens == ["sentinel-2-l2a"]  # one token for the whole manifest


def test_only_scenes_that_fully_cover_the_bbox_are_kept() -> None:
    items = [_item("partial", bbox=[139.75, 35.0, 140.5, 36.2]), _item("full")]
    manifest = _builder(items).build(bbox=TARGET)
    assert [s.item_id for s in manifest.scenes] == ["full"]
    assert manifest.summary["items"] == 2 and manifest.summary["scenes"] == 1


def test_scenes_missing_a_configured_asset_are_skipped() -> None:
    items = [_item("visual_only", assets=("visual",)), _item("bands")]
    manifest = _builder(items).build(bbox=TARGET)
    assert [s.item_id for s in manifest.scenes] == ["bands"]


def test_configured_max_items_caps_the_scenes() -> None:
    items = [_item(f"s{i}", cloud=float(i)) for i in range(5)]
    manifest = _builder(items, max_items=2).build(bbox=TARGET)
    assert [s.item_id for s in manifest.scenes] == ["s0", "s1"]


def test_no_covering_scene_is_an_error() -> None:
    with pytest.raises(ValueError, match="fully cover"):
        _builder([_item("partial", bbox=[139.75, 35.0, 140.5, 36.2])]).build(bbox=TARGET)


def test_manifest_is_written_as_json(tmp_path: Path) -> None:
    manifest = _builder([_item("a", cloud=3.0)]).build(bbox=TARGET)
    out = tmp_path / "nested" / "manifest.json"
    manifest.write(out)

    data = json.loads(out.read_text())
    (scene,) = data["scenes"]
    assert scene["item_id"] == "a"
    assert scene["acquisition_date"] == "2024-06-01T00:00:00+00:00"
    assert scene["assets"]["B04"] == "https://s/a/B04.tif"
    assert data["summary"]["bbox"] == list(TARGET)
    assert data["summary"]["generated_at"].endswith("Z")


# --- STAC cache ---------------------------------------------------------------


def test_search_results_are_cached_and_reused(tmp_path: Path) -> None:
    builder = _builder([_item("a")], cache_dir=tmp_path)
    builder.build(bbox=TARGET)
    builder.build(bbox=TARGET)
    assert builder._client.searches == 1  # type: ignore[attr-defined]
    builder.build(bbox=TARGET, force_refresh=True)
    assert builder._client.searches == 2  # type: ignore[attr-defined]


def test_cache_key_changes_with_every_search_parameter() -> None:
    base = _builder([])._build_cache_key(TARGET, 10)
    assert _builder([])._build_cache_key(TARGET, 10) == base
    assert _builder([])._build_cache_key(TARGET, 11) != base
    assert _builder([])._build_cache_key((0, 0, 1, 1), 10) != base
    for change in ({"max_cloud": 5.0}, {"start_date": "2020-01-01"}, {"assets": ("visual",)},
                   {"collection": "other"}):
        assert _builder([], **change)._build_cache_key(TARGET, 10) != base, change


def _write_cache(path: Path, generated_at: Optional[str]) -> None:
    payload: Dict[str, Any] = {"items": [_item("cached").to_dict(include_self_link=False)]}
    if generated_at is not None:
        payload["generated_at"] = generated_at
    path.write_text(json.dumps(payload))


def test_fresh_cache_entries_are_loaded(tmp_path: Path) -> None:
    _write_cache(tmp_path / "k.json", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    items = _builder([], cache_dir=tmp_path)._load_cache_items("k")
    assert items is not None and [i.id for i in items] == ["cached"]


@pytest.mark.parametrize(
    "generated_at",
    [None, "not a date", (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()],
)
def test_stale_or_unreadable_cache_entries_are_ignored(tmp_path: Path, generated_at: Optional[str]) -> None:
    _write_cache(tmp_path / "k.json", generated_at)
    assert _builder([], cache_dir=tmp_path, cache_ttl_days=30)._load_cache_items("k") is None


def test_corrupt_cache_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "k.json").write_text("{")
    assert _builder([], cache_dir=tmp_path)._load_cache_items("k") is None


def test_no_cache_dir_means_no_cache(tmp_path: Path) -> None:
    builder = _builder([])
    builder._store_cache_items("k", [_item("a")])
    assert builder._load_cache_items("k") is None


@pytest.mark.parametrize(
    ("candidate", "covers"),
    [((0, 0, 10, 10), True), ((1, 1, 2, 2), True), ((1.5, 1, 2, 2), False), ((1, 1, 2, 1.5), False)],
)
def test_bbox_covers(candidate, covers: bool) -> None:
    assert _bbox_covers(candidate, (1, 1, 2, 2)) is covers
