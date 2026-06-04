"""Tests for the OpenAerialMap acquisition helpers (ADR 0001, step 4)."""

from __future__ import annotations

import json

import pytest

from planetarble.acquisition.openaerialmap import (
    OAMItem,
    build_oam_warp_command,
    gsd_to_zoom,
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


def test_build_oam_warp_command_clips_to_aoi_and_footprint_intersection() -> None:
    items = parse_oam_results(ATAMI_RESPONSE)
    cmd = build_oam_warp_command(
        items,
        aoi_bbox=(139.02, 35.07, 139.12, 35.13),
        output_path="out/atami_oam.tif",
        gdalwarp="gdalwarp",
    )
    assert cmd[0] == "gdalwarp"
    joined = " ".join(cmd)
    assert "/vsicurl/https://oin-hotosm-temp.s3.amazonaws.com/abc/0/def.tif" in joined
    # Imagery footprints (~1km) are far smaller than the AOI (~9km), so the
    # warp extent must be AOI intersect (union of footprints), not the whole
    # AOI, to avoid an enormous mostly-nodata raster at 5cm.
    te = cmd.index("-te")
    assert cmd[te + 1 : te + 5] == ["139.05", "35.1", "139.1", "35.13"]
    assert "out/atami_oam.tif" in cmd


def test_build_oam_warp_command_errors_when_aoi_disjoint() -> None:
    items = parse_oam_results(ATAMI_RESPONSE)
    with pytest.raises(ValueError):
        build_oam_warp_command(items, aoi_bbox=(0.0, 0.0, 1.0, 1.0), output_path="o.tif")
