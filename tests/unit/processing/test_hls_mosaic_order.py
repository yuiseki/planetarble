"""Tests for HLS mosaic stacking order.

gdalbuildvrt paints the LAST source in the input list on top, with nodata(0)
pixels letting lower sources show through. So the cleanest (lowest-cloud) scene
must be listed last to win over hazier scenes whose thin cloud/haze Fmask did
not catch. _write_hls_band_lists therefore orders scenes cloudiest-first,
cleanest-last.
"""

from __future__ import annotations

from pathlib import Path

from planetarble.processing.manager import _write_hls_band_lists


def _scene(item_id: str, cloud, url: str):
    return {
        "item_id": item_id,
        "cloud_cover": cloud,
        "bands": {"B02": f"{url}/B02", "B03": f"{url}/B03", "B04": f"{url}/B04"},
    }


def test_cleanest_scene_is_listed_last(tmp_path: Path) -> None:
    scenes = [
        _scene("mid", 20.0, "u_mid"),
        _scene("clean", 2.0, "u_clean"),
        _scene("cloudy", 80.0, "u_cloudy"),
    ]
    paths = _write_hls_band_lists(tmp_path, scenes)
    blue = paths["blue"].read_text(encoding="utf-8").splitlines()
    # cloudiest first (bottom), cleanest last (top, painted last by gdalbuildvrt)
    assert blue == ["u_cloudy/B02", "u_mid/B02", "u_clean/B02"]


def test_missing_cloud_cover_sinks_to_bottom(tmp_path: Path) -> None:
    scenes = [
        _scene("clean", 5.0, "u_clean"),
        _scene("unknown", None, "u_unknown"),
    ]
    paths = _write_hls_band_lists(tmp_path, scenes)
    red = paths["red"].read_text(encoding="utf-8").splitlines()
    # unknown cloud cover is treated as worst -> bottom (first), known-clean on top
    assert red == ["u_unknown/B04", "u_clean/B04"]
