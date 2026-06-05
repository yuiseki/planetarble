"""Tests for the build orchestrator control flow (ADR 0001, step 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from planetarble.overlay import parse_pipeline_spec
from planetarble.overlay.orchestrator import BuildResult, build_planet

SPEC = {
    "base": {"source": "bmng", "max_zoom": 8},
    "overlays": [
        {"name": "ctx", "source": "hls", "aoi": {"bbox": [139.0, 35.0, 139.2, 35.2]}, "max_zoom": 11},
        {"name": "city", "source": "openaerialmap", "aoi": {"bbox": [139.06, 35.10, 139.08, 35.12]}, "max_zoom": 18},
    ],
    "output": {"name": "planet_test"},
}


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list = []

    def build_base(self, base):  # noqa: ANN001
        self.calls.append(("base", base.source, base.max_zoom))
        return Path("base.mbtiles")

    def build_overlay_source(self, overlay, resolved):  # noqa: ANN001
        self.calls.append(("source", overlay.name, resolved.bbox))
        return Path(f"{overlay.name}.mbtiles")

    def stack(self, sources, aoi_bbox, min_zoom, max_zoom):  # noqa: ANN001
        self.calls.append(("stack", [Path(s).stem for s in sources], aoi_bbox, min_zoom, max_zoom))
        return Path("stack_" + "_".join(Path(s).stem for s in sources) + ".mbtiles")

    def merge(self, base, overlay):  # noqa: ANN001
        self.calls.append(("merge", Path(base).stem, Path(overlay).stem))
        return Path(f"{Path(base).stem}+{Path(overlay).stem}.mbtiles")

    def package(self, mbtiles, name):  # noqa: ANN001
        self.calls.append(("package", Path(mbtiles).stem, name))
        return Path(f"{name}.pmtiles")


def test_build_planet_stacks_each_overlay_over_lower_sources() -> None:
    spec = parse_pipeline_spec(SPEC)
    ex = _RecordingExecutor()

    result = build_planet(spec, ex, data_dir=Path("data"))

    assert isinstance(result, BuildResult)
    assert result.planet == Path("planet_test.pmtiles")
    kinds = [c[0] for c in ex.calls]
    assert kinds == ["base", "source", "stack", "merge", "source", "stack", "merge", "package"]

    # ctx stack uses [base, ctx] over the ctx bbox, zooms base.max+1 .. ctx.max
    ctx_stack = ex.calls[2]
    assert ctx_stack[1] == ["base", "ctx"]
    assert ctx_stack[2] == (139.0, 35.0, 139.2, 35.2)
    assert ctx_stack[3] == 9 and ctx_stack[4] == 11
    # city stack stacks all lower sources [base, ctx, city] over the city bbox
    city_stack = ex.calls[5]
    assert city_stack[1] == ["base", "ctx", "city"]
    assert city_stack[3] == 9 and city_stack[4] == 18


def test_build_planet_strict_rejects_oversampling() -> None:
    bad = parse_pipeline_spec({**SPEC, "overlays": [
        {"name": "x", "source": "hls", "aoi": {"bbox": [139.0, 35.0, 139.2, 35.2]}, "max_zoom": 18},
    ]})
    with pytest.raises(ValueError):
        build_planet(bad, _RecordingExecutor(), data_dir=Path("data"))


def test_build_planet_base_only() -> None:
    spec = parse_pipeline_spec({"base": {"source": "bmng", "max_zoom": 8}, "overlays": [], "output": {"name": "p"}})
    ex = _RecordingExecutor()
    result = build_planet(spec, ex, data_dir=Path("data"))
    assert [c[0] for c in ex.calls] == ["base", "package"]
    assert result.planet == Path("p.pmtiles")
