"""Tests for the source registry and zoom validation (ADR 0001, step 1)."""

from __future__ import annotations

from planetarble.overlay import (
    SOURCE_REGISTRY,
    SourceAdapter,
    parse_pipeline_spec,
    validate_pipeline_spec,
)


def _spec(overlays):
    return parse_pipeline_spec(
        {
            "base": {"source": "bmng", "max_zoom": 8},
            "overlays": overlays,
            "output": {"name": "planet_test"},
        }
    )


def test_registry_contains_known_sources() -> None:
    for name in ("bmng", "hls", "sentinel2", "copernicus", "gsi_orthophotos", "openaerialmap"):
        assert name in SOURCE_REGISTRY
    # ceilings reflect SOURCE.md
    assert SOURCE_REGISTRY["gsi_orthophotos"].native_max_zoom == 18
    assert SOURCE_REGISTRY["hls"].native_max_zoom == 12
    assert SOURCE_REGISTRY["sentinel2"].native_max_zoom == 14


def test_validation_flags_oversampling() -> None:
    # HLS cannot justify z14
    issues = validate_pipeline_spec(_spec([
        {"name": "x", "source": "hls", "aoi": {"miniplanet": "12"}, "max_zoom": 14},
    ]))
    assert any("hls" in i and "14" in i for i in issues)


def test_validation_passes_within_ceiling() -> None:
    issues = validate_pipeline_spec(_spec([
        {"name": "x", "source": "hls", "aoi": {"miniplanet": "12"}, "max_zoom": 11},
        {"name": "y", "source": "gsi_orthophotos", "aoi": {"bbox": [139.5, 35.5, 139.9, 35.8]}, "max_zoom": 18},
    ]))
    assert issues == []


def test_validation_flags_base_oversampling() -> None:
    spec = parse_pipeline_spec({
        "base": {"source": "bmng", "max_zoom": 12},  # BMNG ceiling is 8
        "overlays": [],
        "output": {"name": "p"},
    })
    issues = validate_pipeline_spec(spec)
    assert any("bmng" in i and "12" in i for i in issues)


def test_source_adapter_protocol_is_satisfiable() -> None:
    class _Stub:
        name = "stub"

        def native_max_zoom(self, aoi):  # noqa: ANN001
            return 10

        def plan(self, aoi, zoom_range):  # noqa: ANN001
            return []

        def build_raster(self, plan, workspace):  # noqa: ANN001
            return workspace

    assert isinstance(_Stub(), SourceAdapter)
