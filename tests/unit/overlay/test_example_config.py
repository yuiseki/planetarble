"""The shipped overlay example must parse and validate cleanly."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.overlay import parse_pipeline_spec, validate_pipeline_spec

yaml = pytest.importorskip("yaml")

OVERLAY_DIR = Path(__file__).resolve().parents[3] / "configs" / "overlays"


def test_disaster_example_parses_and_validates() -> None:
    data = yaml.safe_load((OVERLAY_DIR / "disaster-example.yaml").read_text(encoding="utf-8"))
    spec = parse_pipeline_spec(data)

    assert spec.base.source == "bmng"
    assert [o.source for o in spec.overlays] == ["hls", "openaerialmap"]
    assert validate_pipeline_spec(spec) == []


def test_atami_example_uses_direct_aoi_selectors() -> None:
    data = yaml.safe_load((OVERLAY_DIR / "atami-example.yaml").read_text(encoding="utf-8"))
    spec = parse_pipeline_spec(data)

    # A targeted build names its AOIs directly; no miniplanet shards involved.
    assert all(o.aoi.miniplanet is None for o in spec.overlays)
    # The heavy HLS overlay derives its footprint from the target bbox + buffer,
    # not from an admin boundary.
    hls = spec.overlays[0]
    assert hls.source == "hls"
    assert hls.aoi.natural_earth is None
    assert hls.aoi.bbox == (139.02, 35.07, 139.12, 35.13)
    assert hls.aoi.buffer_km == 20.0
    assert spec.overlays[1].aoi.bbox == (139.02, 35.07, 139.12, 35.13)
    assert validate_pipeline_spec(spec) == []


def test_tokyo23_sentinel2_build_parses_and_validates() -> None:
    data = yaml.safe_load((OVERLAY_DIR / "tokyo23-sentinel2-build.yaml").read_text(encoding="utf-8"))
    spec = parse_pipeline_spec(data)

    assert spec.base.source == "bmng"
    s2 = spec.overlays[0]
    assert s2.source == "sentinel2"
    assert s2.aoi.bbox == (139.56, 35.53, 139.92, 35.82)
    assert s2.max_zoom == 14  # Sentinel-2 10m earns z14
    assert s2.source_options["assets"] == ["visual"]
    assert spec.output_name == "planet_tokyo23_sentinel-2"
    # z14 is within the Sentinel-2 ceiling, so validation is clean
    assert validate_pipeline_spec(spec) == []


def test_japan_sentinel2_multi_aoi_build_parses_and_validates() -> None:
    data = yaml.safe_load((OVERLAY_DIR / "japan-sentinel2-build.yaml").read_text(encoding="utf-8"))
    spec = parse_pipeline_spec(data)

    assert spec.base.source == "bmng"
    names = [o.name for o in spec.overlays]
    assert names == ["tokyo_s2", "sendai_s2", "hiroshima_s2", "morioka_s2"]
    # Tokyo is expanded to ~the cached T54SUE tile (still within its footprint)
    tokyo = spec.overlays[0]
    assert tokyo.aoi.bbox == (138.80, 35.16, 140.00, 36.13)
    for o in spec.overlays:
        assert o.source == "sentinel2"
        assert o.aoi.land_only is True
        assert o.max_zoom == 14
        assert o.source_options["assets"] == ["visual"]
    assert spec.output_name == "planet_japan_sentinel-2"
    assert validate_pipeline_spec(spec) == []
