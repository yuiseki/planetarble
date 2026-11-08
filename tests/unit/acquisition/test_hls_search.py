"""HLS STAC search: primary/fallback collections, signed scenes, cache, plan I/O.

Complements test_hls_stac_client.py (total cap) and test_hls_planner.py. The
STAC client and SAS token endpoint are faked; items are real pystac Items.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from pystac import Asset, Item

from planetarble.acquisition import hls
from planetarble.acquisition.hls import (
    HLSMosaicTask,
    HLSSTACClient,
    _parse_date,
    _season_dates,
    _select_season,
    iter_plan,
)
from planetarble.core.models import HLSConfig, HLSSeasonWindow

BBOX = (139.0, 35.0, 139.1, 35.1)


def _task(**kwargs: Any) -> HLSMosaicTask:
    base: Dict[str, Any] = dict(
        z=10, x=909, y=403, bbox=BBOX, start_date=date(2024, 4, 1), end_date=date(2024, 10, 31),
        season_name="growing", hemisphere="north", collections=("hls2-s30", "hls2-l30"),
        fallback_collections=("hls2-l30",), max_cloud=40.0, fallback_max_cloud=80.0,
    )
    base.update(kwargs)
    return HLSMosaicTask(**base)


def _item(item_id: str, collection: str, *, cloud: Any = 10.0, when: str = "2024-06-01T01:00:00Z",
          bands: tuple = ("B02", "B03", "B04", "Fmask")) -> Item:
    minx, miny, maxx, maxy = (138.9, 34.9, 139.3, 35.3)
    geometry = {"type": "Polygon", "coordinates": [[[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]]}
    item = Item(id=item_id, geometry=geometry, bbox=[minx, miny, maxx, maxy],
                datetime=datetime(2024, 6, 1, tzinfo=timezone.utc), properties={"eo:cloud_cover": cloud})
    item.properties["datetime"] = when
    item.collection_id = collection
    for name in bands:
        item.assets[name] = Asset(href=f"https://h/{item_id}/{name}.tif")
    return item


class FakeClient:
    def __init__(self, by_collection: Dict[str, List[Item]]) -> None:
        self.by_collection = by_collection
        self.searches: List[Dict[str, Any]] = []

    def search(self, **kwargs: Any):
        self.searches.append(kwargs)
        items = self.by_collection.get(kwargs["collections"][0], [])
        return type("Search", (), {"items": lambda self: iter(items)})()


@pytest.fixture(autouse=True)
def tokens(monkeypatch) -> List[str]:
    issued: List[str] = []

    def fake_token(collection, timeout):
        issued.append(collection)
        return f"sig={collection}"

    monkeypatch.setattr(hls, "fetch_sas_token", fake_token)
    return issued


def _client(by_collection: Dict[str, List[Item]], *, cache_dir: Optional[Path] = None) -> HLSSTACClient:
    client = object.__new__(HLSSTACClient)
    client._config = HLSConfig()
    client._timeout = 30
    client._max_retries = 1
    client._backoff = 1.0
    client._cache_dir = cache_dir
    client._cache_ttl = timedelta(days=30)
    client._client = FakeClient(by_collection)
    client._tokens = {}
    return client


def test_scenes_from_every_collection_are_signed_with_their_own_token(tokens: List[str]) -> None:
    client = _client({"hls2-s30": [_item("S", "hls2-s30")], "hls2-l30": [_item("L", "hls2-l30")]})

    scenes = client._search_collections(("hls2-s30", "hls2-l30"), _task(), max_items=10, max_cloud=40.0)

    assert sorted(tokens) == ["hls2-l30", "hls2-s30"]
    s30 = next(s for s in scenes if s.item_id == "S")
    assert s30.bands == {b: f"https://h/S/{b}.tif?sig=hls2-s30" for b in ("B02", "B03", "B04")}
    assert s30.qa_asset == "https://h/S/Fmask.tif?sig=hls2-s30"
    assert s30.acquisition_date == date(2024, 6, 1)
    search = client._client.searches[0]  # type: ignore[attr-defined]
    assert search["datetime"] == "2024-04-01/2024-10-31"
    assert search["query"] == {"eo:cloud_cover": {"lte": 40.0}}


def test_scenes_sort_by_cloud_then_date_and_drop_items_without_bands() -> None:
    items = [
        _item("late", "hls2-s30", cloud=5.0, when="2024-08-01T00:00:00Z"),
        _item("early", "hls2-s30", cloud=5.0, when="2024-05-01T00:00:00Z"),
        _item("cloudy", "hls2-s30", cloud=30.0),
        _item("no_cloud_value", "hls2-s30", cloud=None),
        _item("qa_only", "hls2-s30", bands=("Fmask",)),
    ]
    scenes = _client({"hls2-s30": items})._search_collections(("hls2-s30",), _task(), max_items=10, max_cloud=40.0)
    assert [s.item_id for s in scenes] == ["early", "late", "cloudy", "no_cloud_value"]


def test_fallback_is_searched_with_its_own_cloud_limit() -> None:
    client = _client({"hls2-s30": [], "hls2-l30": [_item("L", "hls2-l30")]})
    result = client.fetch_scenes(_task(collections=("hls2-s30",)))
    assert result["primary"] == []
    assert [s.item_id for s in result["fallback"]] == ["L"]
    assert client._client.searches[-1]["query"] == {"eo:cloud_cover": {"lte": 80.0}}  # type: ignore[attr-defined]


def test_no_fallback_collections_gives_an_empty_fallback() -> None:
    result = _client({}).fetch_scenes(_task(collections=("hls2-s30",), fallback_collections=()))
    assert result == {"primary": [], "fallback": []}


def test_fallback_can_be_disabled() -> None:
    result = _client({}).fetch_scenes(_task(), include_fallback=False)
    assert list(result) == ["primary"]


def test_search_results_are_cached(tmp_path: Path) -> None:
    client = _client({"hls2-s30": [_item("S", "hls2-s30")]}, cache_dir=tmp_path)
    first = client._search_collections(("hls2-s30",), _task(), max_items=10, max_cloud=40.0)
    second = client._search_collections(("hls2-s30",), _task(), max_items=10, max_cloud=40.0)
    assert len(client._client.searches) == 1  # type: ignore[attr-defined]
    assert [s.item_id for s in second] == [s.item_id for s in first] == ["S"]
    # a different cloud limit is a different query
    client._search_collections(("hls2-s30",), _task(), max_items=10, max_cloud=20.0)
    assert len(client._client.searches) == 2  # type: ignore[attr-defined]


def test_stale_cache_is_ignored(tmp_path: Path) -> None:
    client = _client({}, cache_dir=tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    (tmp_path / "k.json").write_text(json.dumps({"generated_at": old, "items": []}))
    (tmp_path / "bad.json").write_text("{")
    assert client._load_cache_items("k") is None
    assert client._load_cache_items("bad") is None


# --- dates and seasons --------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2024-06-01T01:02:03Z", date(2024, 6, 1)), ("2024-06-01T23:00:00+09:00", date(2024, 6, 1)),
     (datetime(2023, 1, 2, 3, 4), date(2023, 1, 2))],
)
def test_parse_date(value, expected: date) -> None:
    assert _parse_date(value) == expected


def test_parse_date_falls_back_to_today() -> None:
    assert _parse_date("not a date") == date.today()
    assert _parse_date(None) == date.today()


def test_season_crossing_new_year_starts_the_year_before() -> None:
    summer_south = HLSSeasonWindow("austral", "south", 11, 1, 2, 28)
    assert _season_dates(summer_south, 2024) == (date(2023, 11, 1), date(2024, 2, 28))
    spring = HLSSeasonWindow("growing", "north", 4, 1, 10, 31)
    assert _season_dates(spring, 2024) == (date(2024, 4, 1), date(2024, 10, 31))


def test_season_is_chosen_by_hemisphere_with_a_global_fallback() -> None:
    north = HLSSeasonWindow("n", "north", 4, 1, 10, 31)
    world = HLSSeasonWindow("g", "global", 1, 1, 12, 31)
    indexed = {"north": [north], "south": [], "global": [world]}
    assert _select_season(35.0, indexed) is north
    assert _select_season(-35.0, indexed) is world
    with pytest.raises(ValueError, match="south"):
        _select_season(-35.0, {"north": [north], "south": [], "global": []})


# --- plan entries -------------------------------------------------------------


def test_task_round_trips_through_its_mapping() -> None:
    task = _task(miniplanet="kanto")
    assert HLSMosaicTask.from_mapping(json.loads(json.dumps(task.to_mapping()))) == task


@pytest.mark.parametrize(
    ("drop", "message"), [("x", "tile coordinates"), ("bbox", "bbox"), ("start_date", "dates")]
)
def test_task_from_mapping_rejects_incomplete_entries(drop: str, message: str) -> None:
    mapping = _task().to_mapping()
    del mapping[drop]
    with pytest.raises(ValueError, match=message):
        HLSMosaicTask.from_mapping(mapping)


def test_iter_plan_skips_invalid_lines(tmp_path: Path) -> None:
    good = json.dumps(_task().to_mapping())
    plan = tmp_path / "plan.ndjson"
    plan.write_text("\n".join([good, "", "{not json", json.dumps({"z": 1}), good]) + "\n")
    assert len(list(iter_plan(plan))) == 2
