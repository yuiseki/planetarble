"""Generic `cog` overlay source: explicit COG URLs plus a mandatory credit (ADR 0003 step 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.overlay import parse_pipeline_spec, validate_pipeline_spec
from planetarble.overlay.adapters import CogAdapter, get_adapter
from planetarble.overlay.sources import SOURCE_REGISTRY

URL = "https://vtopendata-prd.s3.amazonaws.com/Imagery/2022_30cm_LeafOFF_4Band.tif"


def _spec(**overlay) -> dict:
    base = {"name": "vt", "source": "cog", "aoi": {"bbox": [-72.58, 44.25, -72.57, 44.26]}, "max_zoom": 19}
    return {"base": {"source": "bmng", "max_zoom": 8}, "overlays": [{**base, **overlay}], "output": {"name": "p"}}


def test_plan_returns_the_configured_urls() -> None:
    assert CogAdapter(urls=[URL]).plan(None, (14, 19)) == [URL]


def test_build_raster_honours_bands_stretch_and_nodata(tmp_path: Path) -> None:
    ran: list[list[str]] = []
    adapter = CogAdapter(urls=[URL], bands=[1, 2, 3], nodata=0, stretch={"low": 0, "high": 4000, "gamma": 0.8}, run=ran.append)
    adapter.build_raster(adapter.plan(None, (14, 19)), tmp_path / "o.tif", aoi_bbox=(-72.58, 44.25, -72.57, 44.26))
    warp, translate = ran
    assert warp[warp.index("-srcnodata") + 1] == "0"
    i = translate.index("-scale_2")
    assert translate[i + 1 : i + 5] == ["0", "4000", "0", "255"]


def test_requires_urls() -> None:
    with pytest.raises(ValueError, match="source_options.urls"):
        CogAdapter(urls=[])


@pytest.mark.parametrize(
    ("overlay", "message"),
    [
        ({"source_options": {"urls": [URL]}}, "requires an attribution"),
        ({"attribution": "Imagery: VCGI"}, "requires source_options.urls"),
    ],
)
def test_spec_validation(overlay: dict, message: str) -> None:
    issues = validate_pipeline_spec(parse_pipeline_spec(_spec(**overlay)))
    assert any(message in issue for issue in issues), issues


def test_valid_spec_passes() -> None:
    spec = parse_pipeline_spec(_spec(attribution="Imagery: VCGI", source_options={"urls": [URL]}))
    assert validate_pipeline_spec(spec) == []


def test_registry_and_factory() -> None:
    assert SOURCE_REGISTRY["cog"].native_max_zoom == 22
    assert isinstance(get_adapter("cog", urls=[URL]), CogAdapter)
