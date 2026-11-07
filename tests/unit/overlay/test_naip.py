"""NAIP from Microsoft Planetary Computer as an overlay source (ADR 0003 step 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.overlay.adapters import NAIPAdapter, get_adapter
from planetarble.overlay.sources import SOURCE_REGISTRY

AOI = type("R", (), {"bbox": (-122.43, 37.76, -122.40, 37.79)})()


def _item(item_id: str, when: str, gsd: float = 0.6) -> dict:
    return {
        "id": item_id,
        "properties": {"datetime": when, "gsd": gsd},
        "assets": {"image": {"href": f"https://naipeuwest.blob.core.windows.net/naip/{item_id}.tif"}},
    }


def test_newest_acquisition_per_quarter_quad_is_kept() -> None:
    searched: list[dict] = []

    def search(body: dict) -> list[dict]:
        searched.append(body)
        return [
            _item("ca_m_3712213_se_10_060_20200524", "2020-05-24T00:00:00Z"),
            _item("ca_m_3712213_se_10_060_20220518", "2022-05-18T16:00:00Z"),
            _item("ca_m_3712214_sw_10_060_20220518", "2022-05-18T16:00:00Z"),
        ]

    urls = NAIPAdapter(search=search, sas_token=lambda: "sv=1&sig=x").plan(AOI, (14, 18))
    assert urls == [
        "https://naipeuwest.blob.core.windows.net/naip/ca_m_3712213_se_10_060_20220518.tif?sv=1&sig=x",
        "https://naipeuwest.blob.core.windows.net/naip/ca_m_3712214_sw_10_060_20220518.tif?sv=1&sig=x",
    ]
    assert searched[0]["collections"] == ["naip"]
    assert searched[0]["bbox"] == [-122.43, 37.76, -122.40, 37.79]
    assert "datetime" not in searched[0]


def test_year_limits_the_search() -> None:
    bodies: list[dict] = []
    NAIPAdapter(year=2020, search=lambda b: bodies.append(b) or [_item("ca_m_1_se_10_060_20200524", "2020-05-24T00:00:00Z")],
                sas_token=lambda: "t").plan(AOI, (14, 18))
    assert bodies[0]["datetime"] == "2020-01-01T00:00:00Z/2020-12-31T23:59:59Z"


def test_no_imagery_is_an_error() -> None:
    with pytest.raises(ValueError, match="no NAIP imagery"):
        NAIPAdapter(search=lambda b: [], sas_token=lambda: "t").plan(AOI, (14, 18))


def test_build_raster_keeps_rgb_without_stretch(tmp_path: Path) -> None:
    ran: list[list[str]] = []
    out = NAIPAdapter(search=lambda b: [], sas_token=lambda: "t", run=ran.append).build_raster(
        ["https://x/a.tif?sig=1"], tmp_path / "naip.tif", aoi_bbox=AOI.bbox
    )
    assert out == tmp_path / "naip.tif"
    assert [c[0] for c in ran] == ["gdalwarp"]
    assert [ran[0][i + 1] for i, a in enumerate(ran[0]) if a == "-srcband"] == ["1", "2", "3"]
    assert ran[0][-2:] == ["/vsicurl/https://x/a.tif?sig=1", str(tmp_path / "naip.tif")]


def test_registry_and_factory() -> None:
    assert SOURCE_REGISTRY["naip"].native_max_zoom == 18
    assert "USDA" in SOURCE_REGISTRY["naip"].credit({})
    assert isinstance(get_adapter("naip", search=lambda b: [], sas_token=lambda: "t"), NAIPAdapter)
