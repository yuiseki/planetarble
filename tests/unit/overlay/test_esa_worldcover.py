"""ESA WorldCover Sentinel-2 RGBNIR composites as an overlay source (ADR 0003 step 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.overlay.adapters import ESAWorldCoverAdapter, get_adapter
from planetarble.overlay.sources import SOURCE_REGISTRY

BASE = "https://esa-worldcover-s2.s3.amazonaws.com/rgbnir"


def test_tile_urls_cover_the_bbox_across_degree_lines() -> None:
    urls = ESAWorldCoverAdapter(exists=lambda url: True).tile_urls((139.5, 35.5, 140.2, 36.0))
    assert urls == [
        f"{BASE}/2021/N35/ESA_WorldCover_10m_2021_v200_N35E139_S2RGBNIR.tif",
        f"{BASE}/2021/N35/ESA_WorldCover_10m_2021_v200_N35E140_S2RGBNIR.tif",
    ]  # 36.0 is the tile edge, so N36 is not needed


def test_tile_names_in_the_southern_and_western_hemispheres() -> None:
    urls = ESAWorldCoverAdapter(year=2020, exists=lambda url: True).tile_urls((-0.5, -1.5, 0.5, -0.5))
    assert [u.rsplit("/", 2)[-2:] for u in urls] == [
        ["S02", "ESA_WorldCover_10m_2020_v100_S02W001_S2RGBNIR.tif"],
        ["S02", "ESA_WorldCover_10m_2020_v100_S02E000_S2RGBNIR.tif"],
        ["S01", "ESA_WorldCover_10m_2020_v100_S01W001_S2RGBNIR.tif"],
        ["S01", "ESA_WorldCover_10m_2020_v100_S01E000_S2RGBNIR.tif"],
    ]


def test_missing_tiles_are_skipped_and_none_left_is_an_error() -> None:
    adapter = ESAWorldCoverAdapter(exists=lambda url: "E139" in url)
    assert len(adapter.plan(type("R", (), {"bbox": (139.5, 35.5, 141.5, 35.9)})(), (0, 14))) == 1
    with pytest.raises(ValueError, match="no ESA WorldCover tile"):
        ESAWorldCoverAdapter(exists=lambda url: False).plan(type("R", (), {"bbox": (150.0, 30.0, 150.5, 30.5)})(), (0, 14))


def test_unknown_year_is_rejected() -> None:
    with pytest.raises(ValueError, match="2020 or 2021"):
        ESAWorldCoverAdapter(year=2019)


def test_build_raster_runs_the_warp_and_stretch(tmp_path: Path) -> None:
    ran: list[list[str]] = []
    adapter = ESAWorldCoverAdapter(exists=lambda url: True, run=ran.append)
    out = adapter.build_raster(["https://x/a.tif"], tmp_path / "wc.tif", aoi_bbox=(139.7, 35.6, 139.8, 35.7))
    assert out == tmp_path / "wc.tif"
    assert [c[0] for c in ran] == ["gdalwarp", "gdal_translate"]
    assert ran[0][ran[0].index("-srcnodata") + 1] == "0"
    i = ran[1].index("-scale_1")
    assert ran[1][i + 1 : i + 5] == ["0", "2000", "0", "255"]


def test_registry_and_factory() -> None:
    info = SOURCE_REGISTRY["esa-worldcover"]
    assert info.native_max_zoom == 14
    assert info.credit({}) == (
        "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) "
        "processed by ESA WorldCover consortium"
    )
    assert "project 2020 /" in info.credit({"year": 2020})
    assert isinstance(get_adapter("esa-worldcover", exists=lambda u: True), ESAWorldCoverAdapter)
