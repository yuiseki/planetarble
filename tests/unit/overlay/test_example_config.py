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
