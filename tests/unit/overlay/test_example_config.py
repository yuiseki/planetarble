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
    assert names == [
        "tokyo_s2", "chiba_s2", "izu_s2", "numazu_s2", "shizuoka_s2",
        "sendai_s2", "hiroshima_s2", "morioka_s2",
    ]
    # Tokyo is expanded to ~the cached T54SUE tile (still within its footprint)
    tokyo = spec.overlays[0]
    assert tokyo.aoi.bbox == (138.80, 35.16, 140.00, 36.13)
    # Chiba is a separate overlay on the adjacent T54SVE tile (single-granule
    # coverage requires staying within one tile, so east is its own AOI)
    assert spec.overlays[1].aoi.bbox == (139.95, 35.16, 141.05, 36.13)
    by_name = {o.name: o for o in spec.overlays}
    # Hiroshima/Morioka expanded to their cached tiles (no new downloads);
    # Morioka uses 2-scene mosaic because only 2 T54SWJ scenes are cached
    assert by_name["izu_s2"].aoi.bbox == (138.83, 34.25, 140.00, 35.22)
    assert by_name["numazu_s2"].aoi.bbox == (137.73, 34.23, 138.92, 35.21)
    assert by_name["shizuoka_s2"].aoi.bbox == (137.20, 34.23, 138.34, 35.21)
    assert by_name["sendai_s2"].aoi.bbox == (139.86, 37.87, 141.10, 38.84)
    assert by_name["hiroshima_s2"].aoi.bbox == (131.72, 34.22, 132.92, 35.22)
    assert by_name["morioka_s2"].aoi.bbox == (141.02, 38.77, 142.26, 39.74)
    assert by_name["morioka_s2"].source_options["mosaic_max_scenes"] == 2
    for o in spec.overlays:
        assert o.source == "sentinel2"
        assert o.aoi.land_only is True
        assert o.max_zoom == 14
        assert o.source_options["assets"] == ["visual"]
    assert spec.output_name == "planet_japan_sentinel-2"
    assert validate_pipeline_spec(spec) == []
