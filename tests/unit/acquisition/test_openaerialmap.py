"""Tests for the OpenAerialMap acquisition helpers (ADR 0001, step 4)."""

from __future__ import annotations

import json

import pytest

from pathlib import Path

from planetarble.acquisition.openaerialmap import (
    OAMItem,
    build_local_warp_command,
    gsd_to_zoom,
    oam_cache_path,
    oam_download_command,
    parse_oam_results,
    select_items,
)

ATAMI_RESPONSE = {
    "results": [
        {
            "uuid": "https://oin-hotosm-temp.s3.amazonaws.com/abc/0/def.tif",
            "gsd": 0.05,
            "bbox": [139.068869, 35.112867, 139.081648, 35.123595],
            "acquisition_start": "2021-07-06T04:00:00.000Z",
            "properties": {"license": "CC-BY 4.0", "tms": "https://tiles.openaerialmap.org/abc/{z}/{x}/{y}"},
        },
        {
            "uuid": "https://oin-hotosm-temp.s3.amazonaws.com/ghi/0/jkl.tif",
            "gsd": 0.12,
            "bbox": [139.05, 35.10, 139.10, 35.14],
            "acquisition_start": "2020-01-01T00:00:00.000Z",
            "properties": {"license": "CC-BY 4.0"},
        },
    ]
}


def test_gsd_to_zoom() -> None:
    assert gsd_to_zoom(0.05) == 21  # ~5cm drone imagery
    assert gsd_to_zoom(0.5) == 18
    assert gsd_to_zoom(10.0) == 13
    assert gsd_to_zoom(0.0) == 24  # guard against nonsense, clamp to max


def test_parse_oam_results() -> None:
    items = parse_oam_results(ATAMI_RESPONSE)
    assert len(items) == 2
    first = items[0]
    assert isinstance(first, OAMItem)
    assert first.cog_url.endswith("def.tif")
    assert first.gsd == 0.05
    assert first.bbox == (139.068869, 35.112867, 139.081648, 35.123595)
    assert first.license == "CC-BY 4.0"


def test_select_items_prefers_finest_gsd() -> None:
    items = parse_oam_results(ATAMI_RESPONSE)
    selected = select_items(items, max_items=1)
    assert len(selected) == 1
    assert selected[0].gsd == 0.05  # finest first


def test_select_items_filters_by_max_gsd() -> None:
    items = parse_oam_results(ATAMI_RESPONSE)
    selected = select_items(items, max_gsd=0.1)
    assert [i.gsd for i in selected] == [0.05]  # 0.12 dropped


def test_oam_cache_path_is_deterministic_and_unique() -> None:
    cache = Path("data/cache/oam")
    items = parse_oam_results(ATAMI_RESPONSE)
    p0a = oam_cache_path(items[0], cache)
    p0b = oam_cache_path(items[0], cache)
    p1 = oam_cache_path(items[1], cache)
    assert p0a == p0b  # deterministic -> cache hit on re-run
    assert p0a != p1  # distinct items -> distinct files
    assert p0a.parent == cache and p0a.suffix == ".tif"


def test_oam_download_command_is_sequential_resumable() -> None:
    item = parse_oam_results(ATAMI_RESPONSE)[0]
    dest = Path("data/cache/oam/abc.tif")
    cmd = oam_download_command(item, dest, aria2c="aria2c")
    assert cmd[0] == "aria2c"
    joined = " ".join(cmd)
    # whole-file download (no /vsicurl), resumable, deterministic output name
    assert "/vsicurl/" not in joined
    assert item.cog_url in cmd
    assert "abc.tif" in joined
    assert "-c" in cmd  # continue/resume


def test_build_local_warp_command_uses_cached_paths_and_clips() -> None:
    cache = Path("data/cache/oam")
    items = parse_oam_results(ATAMI_RESPONSE)
    cmd = build_local_warp_command(
        items,
        cache_dir=cache,
        aoi_bbox=(139.02, 35.07, 139.12, 35.13),
        output_path="out/atami_oam.tif",
    )
    assert cmd[0] == "gdalwarp"
    joined = " ".join(cmd)
    # inputs are the LOCAL cached COGs, never /vsicurl
    assert "/vsicurl/" not in joined
    assert str(oam_cache_path(items[0], cache)) in cmd
    # alpha band so nodata is transparent when tiled
    assert "-dstalpha" in cmd
    # extent clipped to AOI intersect (union of footprints)
    te = cmd.index("-te")
    assert cmd[te + 1 : te + 5] == ["139.05", "35.1", "139.1", "35.13"]


def test_build_local_warp_command_errors_when_aoi_disjoint() -> None:
    items = parse_oam_results(ATAMI_RESPONSE)
    with pytest.raises(ValueError):
        build_local_warp_command(items, cache_dir=Path("c"), aoi_bbox=(0.0, 0.0, 1.0, 1.0), output_path="o.tif")
