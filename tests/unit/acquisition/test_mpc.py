"""Microsoft Planetary Computer helpers: SAS signing, STAC scene choice, clipping.

Network calls (requests, pystac-client) are replaced by small fakes.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from planetarble.acquisition import mpc
from planetarble.acquisition.mpc import (
    MPCError,
    _bbox_from_point,
    _safe_float,
    append_sas_token,
    build_clip_command,
    fetch_sas_token,
    fetch_true_color_tile,
)

# --- SAS token ----------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self) -> Any:
        return self._payload


def test_sas_token_is_read_from_the_collection_endpoint(monkeypatch) -> None:
    seen: Dict[str, Any] = {}

    def fake_get(url, timeout):
        seen.update(url=url, timeout=timeout)
        return FakeResponse(payload={"token": "st=1&sig=abc"})

    monkeypatch.setattr(mpc.requests, "get", fake_get)
    assert fetch_sas_token("naip", timeout=7) == "st=1&sig=abc"
    assert seen == {"url": "https://planetarycomputer.microsoft.com/api/sas/v1/token/naip?token=anon", "timeout": 7}


def test_sas_token_http_error_is_reported(monkeypatch) -> None:
    monkeypatch.setattr(mpc.requests, "get", lambda url, timeout: FakeResponse(429, text=" slow down \n"))
    with pytest.raises(MPCError, match="429 slow down"):
        fetch_sas_token("naip", timeout=1)


def test_sas_token_missing_from_payload_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(mpc.requests, "get", lambda url, timeout: FakeResponse(payload={"msft:expiry": "x"}))
    with pytest.raises(MPCError, match="token"):
        fetch_sas_token("naip", timeout=1)


@pytest.mark.parametrize(
    ("href", "token", "expected"),
    [
        ("https://a.blob/x.tif", "st=1&sig=2", "https://a.blob/x.tif?st=1&sig=2"),
        ("https://a.blob/x.tif", "?st=1&sig=2", "https://a.blob/x.tif?st=1&sig=2"),
        ("https://a.blob/x.tif?v=1", "st=1", "https://a.blob/x.tif?v=1&st=1"),
        ("https://a.blob/x.tif#frag", "st=1", "https://a.blob/x.tif?st=1#frag"),
    ],
)
def test_append_sas_token(href: str, token: str, expected: str) -> None:
    assert append_sas_token(href, token) == expected


# --- geometry -----------------------------------------------------------------


def test_bbox_from_point_is_centred_with_the_requested_size() -> None:
    minx, miny, maxx, maxy = _bbox_from_point(lat=35.0, lon=139.0, width_m=2000, height_m=1000)
    assert (minx + maxx) / 2 == pytest.approx(139.0)
    assert (miny + maxy) / 2 == pytest.approx(35.0)
    assert (maxy - miny) * 111_320 == pytest.approx(1000)
    assert (maxx - minx) * 111_320 * math.cos(math.radians(35.0)) == pytest.approx(2000)


def test_bbox_from_point_is_clamped_to_the_globe() -> None:
    minx, miny, maxx, maxy = _bbox_from_point(lat=89.99, lon=179.99, width_m=50_000, height_m=50_000)
    assert maxy == 90.0
    assert maxx == 180.0
    assert minx >= -180.0 and miny < 90.0


@pytest.mark.parametrize(("w", "h"), [(0, 10), (10, 0), (-1, 10)])
def test_bbox_from_point_rejects_non_positive_sizes(w: float, h: float) -> None:
    with pytest.raises(ValueError):
        _bbox_from_point(lat=0, lon=0, width_m=w, height_m=h)


def test_clip_command_uses_upper_left_lower_right_order(tmp_path: Path) -> None:
    cmd = build_clip_command(
        gdal_translate="gdal_translate", signed_url="https://x?sig", bbox=(1, 2, 3, 4), destination=tmp_path / "o.tif"
    )
    i = cmd.index("-projwin")
    assert cmd[i + 1 : i + 5] == ["1", "4", "3", "2"]  # ulx uly lrx lry
    assert cmd[cmd.index("-projwin_srs") + 1] == "EPSG:4326"
    assert cmd[-2:] == ["https://x?sig", str(tmp_path / "o.tif")]


@pytest.mark.parametrize(("value", "expected"), [("12.5", 12.5), (3, 3.0), (None, None), ("n/a", None), ([], None)])
def test_safe_float(value: Any, expected: Any) -> None:
    assert _safe_float(value) == expected


# --- STAC scene selection -----------------------------------------------------


def _item(assets: Dict[str, Any], *, collection: str = "sentinel-2-l2a", cloud: Any = 4.2):
    return SimpleNamespace(
        id="S2B_MSIL2A_X",
        collection_id=collection,
        assets={k: SimpleNamespace(href=v) for k, v in assets.items()},
        properties={"eo:cloud_cover": cloud},
    )


class FakeClient:
    searches: List[Dict[str, Any]] = []
    items: List[Any] = []

    @classmethod
    def open(cls, url, timeout):
        assert url == mpc.STAC_API_ROOT
        return cls()

    def search(self, **kwargs):
        FakeClient.searches.append(kwargs)
        return SimpleNamespace(item_collection=lambda: SimpleNamespace(items=list(FakeClient.items)))


@pytest.fixture
def fake_stac(monkeypatch):
    FakeClient.searches = []
    FakeClient.items = [_item({"visual": "https://a.blob/visual.tif"})]
    monkeypatch.setattr(mpc, "Client", FakeClient)
    monkeypatch.setattr(mpc, "pc_exceptions", SimpleNamespace())
    return FakeClient


def _select(**kwargs: Any):
    args: Dict[str, Any] = {
        "bbox": (1.0, 2.0, 3.0, 4.0), "max_cloud": None, "start_datetime": None, "end_datetime": None, "timeout": 5,
    }
    args.update(kwargs)
    return mpc._select_scene(**args)


def test_select_scene_asks_for_the_least_cloudy_single_item(fake_stac) -> None:
    scene = _select()
    (search,) = fake_stac.searches
    assert search == {
        "collections": ["sentinel-2-l2a"], "bbox": [1.0, 2.0, 3.0, 4.0], "max_items": 1,
        "sortby": "properties.eo:cloud_cover",
    }
    assert scene == mpc.MPCScene("sentinel-2-l2a", "S2B_MSIL2A_X", "https://a.blob/visual.tif", 4.2)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [("2024-01-01", "2024-06-30", "2024-01-01/2024-06-30"), ("2024-01-01", None, "2024-01-01/.."),
     (None, "2024-06-30", "../2024-06-30")],
)
def test_select_scene_datetime_window(fake_stac, start, end, expected) -> None:
    _select(start_datetime=start, end_datetime=end)
    assert fake_stac.searches[0]["datetime"] == expected


def test_select_scene_cloud_filter(fake_stac) -> None:
    _select(max_cloud=10)
    assert fake_stac.searches[0]["query"] == {"eo:cloud_cover": {"lte": 10}}


def test_select_scene_without_matches_is_an_error(fake_stac) -> None:
    fake_stac.items = []
    with pytest.raises(MPCError, match="No Sentinel-2 scenes"):
        _select()


def test_select_scene_without_a_visual_asset_is_an_error(fake_stac) -> None:
    fake_stac.items = [_item({"B04": "https://a.blob/b04.tif"})]
    with pytest.raises(MPCError, match="visual"):
        _select()


def test_select_scene_falls_back_to_the_default_collection(fake_stac) -> None:
    fake_stac.items = [_item({"visual": "https://v"}, collection=None, cloud="bad")]
    scene = _select()
    assert scene.collection == "sentinel-2-l2a"
    assert scene.cloud_cover is None


def test_select_scene_without_pystac_client_is_an_error(monkeypatch) -> None:
    monkeypatch.setattr(mpc, "Client", None)
    with pytest.raises(MPCError, match="pystac-client"):
        _select()


# --- end to end with fakes ----------------------------------------------------


def test_true_color_tile_dry_run_signs_but_does_not_clip(tmp_path: Path, fake_stac, monkeypatch) -> None:
    monkeypatch.setattr(mpc, "fetch_sas_token", lambda collection, timeout: "sig=1")
    monkeypatch.setattr(mpc.subprocess, "run", lambda *a, **k: pytest.fail("dry run must not run GDAL"))

    result = fetch_true_color_tile(
        lat=35.68, lon=139.76, width_m=1000, height_m=1000, output_path=tmp_path / "o" / "t.tif", dry_run=True
    )

    assert result["signed_url"] == "https://a.blob/visual.tif?sig=1"
    assert result["item_id"] == "S2B_MSIL2A_X"
    assert result["output"] == str((tmp_path / "o" / "t.tif").resolve())
    assert (tmp_path / "o").is_dir()


def test_true_color_tile_runs_gdal_translate_and_hides_the_signed_url(tmp_path: Path, fake_stac, monkeypatch) -> None:
    runs: List[List[str]] = []
    monkeypatch.setattr(mpc, "fetch_sas_token", lambda collection, timeout: "sig=1")
    monkeypatch.setattr(mpc.subprocess, "run", lambda cmd, check: runs.append(cmd))

    result = fetch_true_color_tile(
        lat=35.68, lon=139.76, width_m=1000, height_m=1000, output_path=tmp_path / "t.tif", gdal_translate="gt"
    )

    (cmd,) = runs
    assert cmd[0] == "gt"
    assert "https://a.blob/visual.tif?sig=1" in cmd
    assert result["signed_url"] is None  # the SAS token is not returned outside dry runs
