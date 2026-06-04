"""Tests for concrete source adapters and the adapter factory (ADR 0001, step 2b)."""

from __future__ import annotations

import pytest

from planetarble.overlay import SOURCE_REGISTRY, SourceAdapter
from planetarble.overlay.adapters import (
    BMNGAdapter,
    OpenAerialMapAdapter,
    adapter_sources,
    get_adapter,
)


def test_factory_returns_typed_adapters() -> None:
    for name in ("bmng", "hls", "sentinel2", "copernicus", "gsi_orthophotos", "modis", "viirs", "openaerialmap"):
        adapter = get_adapter(name)
        assert adapter.name == name
        assert isinstance(adapter, SourceAdapter)


def test_factory_rejects_unknown_source() -> None:
    with pytest.raises(ValueError):
        get_adapter("nope")


def test_adapter_sources_match_factory() -> None:
    for name in adapter_sources():
        assert get_adapter(name).name == name


def test_constant_adapters_report_registry_ceiling() -> None:
    for name in ("hls", "sentinel2", "copernicus", "gsi_orthophotos", "modis", "viirs"):
        assert get_adapter(name).native_max_zoom(None) == SOURCE_REGISTRY[name].native_max_zoom


def test_bmng_zoom_depends_on_resolution() -> None:
    assert BMNGAdapter(resolution="500m").native_max_zoom(None) == 8
    assert BMNGAdapter(resolution="2km").native_max_zoom(None) == 6


def test_openaerialmap_zoom_is_per_item_with_registry_guard() -> None:
    # No item known yet: fall back to the registry upper guard.
    assert OpenAerialMapAdapter().native_max_zoom(None) == SOURCE_REGISTRY["openaerialmap"].native_max_zoom
    # A concrete item (e.g. HOTOSM 60e5afbe... at ~z20) overrides it.
    assert OpenAerialMapAdapter(item_max_zoom=20).native_max_zoom(None) == 20


def test_plan_and_build_raster_declared_but_pending() -> None:
    # The execution wiring lands with the orchestrator (step 3); the contract
    # is declared now so the orchestrator has a uniform surface to call.
    adapter = get_adapter("hls")
    with pytest.raises(NotImplementedError):
        adapter.plan(None, (0, 11))
    with pytest.raises(NotImplementedError):
        adapter.build_raster(None, None)
