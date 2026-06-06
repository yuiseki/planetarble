"""Tests for the prefetch orchestration control flow (no GDAL/network/sleep)."""

from __future__ import annotations

from planetarble.overlay import parse_pipeline_spec
from planetarble.prefetch import PrefetchStats, prefetch_planet


def _spec():
    return parse_pipeline_spec(
        {
            "base": {"source": "bmng", "resolution": "500m", "max_zoom": 7},
            "overlays": [
                {"name": "osaka_s2", "source": "sentinel2",
                 "aoi": {"bbox": [135.4, 34.6, 135.6, 34.8], "land_only": True},
                 "min_zoom": 8, "max_zoom": 14},
                {"name": "city_oam", "source": "openaerialmap",
                 "aoi": {"bbox": [135.4, 34.6, 135.6, 34.8]},
                 "min_zoom": 8, "max_zoom": 18},
                {"name": "kyoto_s2", "source": "sentinel2",
                 "aoi": {"bbox": [135.7, 34.9, 135.8, 35.1], "land_only": True},
                 "min_zoom": 8, "max_zoom": 14},
            ],
            "output": {"name": "x"},
        }
    )


class _FakeExecutor:
    def __init__(self):
        self.fetched = []

    def prefetch_overlay(self, overlay):
        self.fetched.append(overlay.name)
        return PrefetchStats(overlay=overlay.name, downloaded_count=3, downloaded_bytes=10**9, elapsed_seconds=100.0)


class _FlakyExecutor:
    """Raises on the first sentinel2 overlay, succeeds on the rest."""

    def __init__(self):
        self.fetched = []

    def prefetch_overlay(self, overlay):
        if not self.fetched and overlay.name == "osaka_s2":
            self.fetched.append("(raised) " + overlay.name)
            raise RuntimeError("transient MPC STAC timeout")
        self.fetched.append(overlay.name)
        return PrefetchStats(overlay=overlay.name, downloaded_count=3, downloaded_bytes=10**9, elapsed_seconds=100.0)


def test_prefetch_continues_past_per_overlay_error() -> None:
    spec = _spec()
    ex = _FlakyExecutor()
    errored, paced = [], []

    results = prefetch_planet(
        spec, ex,
        pacer=lambda stats: paced.append(stats.overlay),
        on_error=lambda ov, exc: errored.append((ov.name, str(exc))),
    )

    # osaka raised but the run continued to kyoto; the error was reported
    assert errored == [("osaka_s2", "transient MPC STAC timeout")]
    assert [r.overlay for r in results] == ["kyoto_s2"]
    assert paced == ["kyoto_s2"]  # pacer not called for the failed overlay


def test_prefetch_only_sentinel2_overlays_and_paces_each() -> None:
    spec = _spec()
    ex = _FakeExecutor()
    paced, skipped = [], []

    results = prefetch_planet(
        spec, ex,
        pacer=lambda stats: paced.append(stats.overlay),
        on_skip=lambda ov: skipped.append(ov.name),
    )

    # only the two sentinel2 overlays are fetched, in order
    assert ex.fetched == ["osaka_s2", "kyoto_s2"]
    # the openaerialmap overlay is skipped
    assert skipped == ["city_oam"]
    # pacer runs once per fetched tile, after each fetch
    assert paced == ["osaka_s2", "kyoto_s2"]
    assert [r.overlay for r in results] == ["osaka_s2", "kyoto_s2"]
