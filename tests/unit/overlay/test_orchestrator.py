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
    """Records the orchestration calls without doing any real work."""

    def __init__(self) -> None:
        self.calls: list = []

    def build_base(self, base):  # noqa: ANN001
        self.calls.append(("base", base.source, base.max_zoom))
        return Path("base.mbtiles")

    def build_overlay(self, overlay, resolved):  # noqa: ANN001
        self.calls.append(("overlay", overlay.name, overlay.source, resolved.bbox))
        return Path(f"{overlay.name}.mbtiles")

    def merge(self, base_mbtiles, overlay_mbtiles):  # noqa: ANN001
        self.calls.append(("merge", str(base_mbtiles), str(overlay_mbtiles)))
        return Path(f"merged_with_{Path(overlay_mbtiles).stem}.mbtiles")

    def package(self, mbtiles, output_name):  # noqa: ANN001
        self.calls.append(("package", str(mbtiles), output_name))
        return Path(f"{output_name}.pmtiles")


def test_build_planet_sequences_base_overlays_merge_package() -> None:
    spec = parse_pipeline_spec(SPEC)
    ex = _RecordingExecutor()

    result = build_planet(spec, ex, data_dir=Path("data"))

    assert isinstance(result, BuildResult)
    assert result.planet == Path("planet_test.pmtiles")

    kinds = [c[0] for c in ex.calls]
    # base first, then per overlay (build then merge), package last
    assert kinds == ["base", "overlay", "merge", "overlay", "merge", "package"]
    # overlays built in declared order
    assert ex.calls[1][1] == "ctx" and ex.calls[3][1] == "city"
    # merges chain: ctx merged onto base, then city merged onto that result
    assert ex.calls[2] == ("merge", "base.mbtiles", "ctx.mbtiles")
    assert ex.calls[4] == ("merge", "merged_with_ctx.mbtiles", "city.mbtiles")
    # package receives the final merged mbtiles and the output name
    assert ex.calls[5] == ("package", "merged_with_city.mbtiles", "planet_test")


def test_build_planet_resolves_each_overlay_aoi() -> None:
    spec = parse_pipeline_spec(SPEC)
    ex = _RecordingExecutor()
    build_planet(spec, ex, data_dir=Path("data"))
    # resolved bbox is passed to build_overlay (pure bbox AOIs need no GDAL)
    assert ex.calls[1][3] == (139.0, 35.0, 139.2, 35.2)
    assert ex.calls[3][3] == (139.06, 35.10, 139.08, 35.12)


def test_build_planet_strict_rejects_oversampling() -> None:
    bad = parse_pipeline_spec({**SPEC, "overlays": [
        {"name": "x", "source": "hls", "aoi": {"bbox": [139.0, 35.0, 139.2, 35.2]}, "max_zoom": 18},
    ]})
    with pytest.raises(ValueError):
        build_planet(bad, _RecordingExecutor(), data_dir=Path("data"))


def test_build_planet_base_only_no_overlays() -> None:
    spec = parse_pipeline_spec({"base": {"source": "bmng", "max_zoom": 8}, "overlays": [], "output": {"name": "p"}})
    ex = _RecordingExecutor()
    result = build_planet(spec, ex, data_dir=Path("data"))
    # with no overlays, package the base directly
    assert [c[0] for c in ex.calls] == ["base", "package"]
    assert result.planet == Path("p.pmtiles")
