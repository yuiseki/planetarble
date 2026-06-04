"""Tests for the AOI overlay pipeline spec (ADR 0001, step 1)."""

from __future__ import annotations

import pytest

from planetarble.overlay import (
    AOI,
    Overlay,
    PipelineSpec,
    parse_pipeline_spec,
)

DISASTER = {
    "base": {"source": "bmng", "resolution": "500m", "max_zoom": 8},
    "ocean": {"enabled": True},
    "overlays": [
        {
            "name": "japan_hls",
            "source": "hls",
            "aoi": {"natural_earth": {"dataset": "admin_0", "where": "adm0_a3='JPN'"}, "land_only": True},
            "max_zoom": 11,
        },
        {
            "name": "noto_oam",
            "source": "openaerialmap",
            "aoi": {"bbox": [136.6, 37.0, 137.4, 37.6]},
            "source_options": {"start_date": "2024-01-01", "end_date": "2024-03-31"},
            "min_zoom": 8,
            "max_zoom": 18,
        },
    ],
    "output": {"name": "planet_disaster_2024"},
}


def test_parse_full_spec() -> None:
    spec = parse_pipeline_spec(DISASTER)
    assert isinstance(spec, PipelineSpec)
    assert spec.base.source == "bmng"
    assert spec.base.max_zoom == 8
    assert spec.output_name == "planet_disaster_2024"
    assert [o.name for o in spec.overlays] == ["japan_hls", "noto_oam"]

    japan = spec.overlays[0]
    assert japan.source == "hls"
    assert japan.max_zoom == 11
    assert japan.aoi.natural_earth == {"dataset": "admin_0", "where": "adm0_a3='JPN'"}
    assert japan.aoi.land_only is True

    noto = spec.overlays[1]
    assert noto.aoi.bbox == (136.6, 37.0, 137.4, 37.6)
    assert noto.min_zoom == 8
    assert noto.source_options["start_date"] == "2024-01-01"


def test_aoi_requires_exactly_one_selector() -> None:
    with pytest.raises(ValueError):
        AOI.from_mapping({})  # none
    with pytest.raises(ValueError):
        AOI.from_mapping({"bbox": [0, 0, 1, 1], "miniplanet": "12"})  # two


def test_aoi_bbox_must_have_four_numbers() -> None:
    with pytest.raises(ValueError):
        AOI.from_mapping({"bbox": [0, 0, 1]})


def test_aoi_miniplanet_and_geojson() -> None:
    assert AOI.from_mapping({"miniplanet": "12"}).miniplanet == "12"
    assert AOI.from_mapping({"geojson": "aoi.json"}).geojson == "aoi.json"


def test_aoi_buffer_km_defaults_zero_and_parses() -> None:
    # Heavy sources (HLS) derive their footprint from a buffered target AOI
    # instead of an oversized admin boundary, so buffer_km is a first-class knob.
    assert AOI.from_mapping({"bbox": [0, 0, 1, 1]}).buffer_km == 0.0
    assert AOI.from_mapping({"bbox": [0, 0, 1, 1], "buffer_km": 20}).buffer_km == 20.0


def test_aoi_buffer_km_is_not_a_selector() -> None:
    # buffer_km alone is not an AOI; exactly one geometry selector is still required.
    with pytest.raises(ValueError):
        AOI.from_mapping({"buffer_km": 20})


def test_unknown_base_source_rejected() -> None:
    bad = {**DISASTER, "base": {"source": "nope", "max_zoom": 8}}
    with pytest.raises(ValueError):
        parse_pipeline_spec(bad)


def test_overlay_requires_name_source_aoi() -> None:
    with pytest.raises(ValueError):
        parse_pipeline_spec({**DISASTER, "overlays": [{"source": "hls", "aoi": {"miniplanet": "12"}}]})
