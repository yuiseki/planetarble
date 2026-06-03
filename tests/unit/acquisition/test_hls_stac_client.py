"""Tests for HLSSTACClient search behaviour."""

from __future__ import annotations

from datetime import date, timedelta

from planetarble.acquisition.hls import HLSMosaicTask, HLSSTACClient
from planetarble.core.models import HLSConfig


def _make_task() -> HLSMosaicTask:
    return HLSMosaicTask(
        z=10,
        x=909,
        y=403,
        bbox=(139.0, 35.0, 139.1, 35.1),
        start_date=date(2024, 4, 1),
        end_date=date(2024, 10, 31),
        season_name="northern_growing_season",
        hemisphere="north",
        collections=("hls2-s30",),
        fallback_collections=(),
        max_cloud=40.0,
        fallback_max_cloud=60.0,
    )


def test_search_collections_passes_total_cap_to_stac_search() -> None:
    # Regression: pystac-client's limit= is only the page size. Without
    # max_items=, search.items() pages through every match beyond the first
    # page, inflating transfer time and the STAC cache (the same bug fixed for
    # the Sentinel-2 builder).
    captured: dict = {}

    class _StubSearch:
        def items(self):
            return iter([])

    class _StubClient:
        def search(self, **kwargs):
            captured.update(kwargs)
            return _StubSearch()

    client = object.__new__(HLSSTACClient)
    client._config = HLSConfig()
    client._timeout = 30
    client._max_retries = 1
    client._backoff = 1.0
    client._cache_dir = None
    client._cache_ttl = timedelta(days=30)
    client._client = _StubClient()
    client._tokens = {}

    scenes = client._search_collections(
        ("hls2-s30",), _make_task(), max_items=200, max_cloud=40.0
    )

    assert scenes == []
    assert captured.get("limit") == 200
    assert captured.get("max_items") == 200
