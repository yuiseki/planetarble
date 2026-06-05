"""The build executor must accept and route a sentinel2 overlay (S2)."""

from __future__ import annotations

from pathlib import Path

from planetarble.overlay import parse_pipeline_spec
from planetarble.overlay.executor import DefaultPlanetExecutor


def _spec():
    return parse_pipeline_spec(
        {
            "base": {"source": "bmng", "resolution": "500m", "max_zoom": 7},
            "overlays": [
                {
                    "name": "tokyo23_s2",
                    "source": "sentinel2",
                    "aoi": {"bbox": [139.56, 35.53, 139.92, 35.82], "land_only": True},
                    "source_options": {"assets": ["visual"]},
                    "min_zoom": 8,
                    "max_zoom": 14,  # Sentinel-2 10m reaches z14
                }
            ],
            "output": {"name": "planet_tokyo23_sentinel-2"},
        }
    )


def test_spec_parses_sentinel2_overlay_at_z14() -> None:
    spec = _spec()
    ov = spec.overlays[0]
    assert ov.source == "sentinel2"
    assert ov.max_zoom == 14  # not rejected by the z14 ceiling
    assert spec.output_name == "planet_tokyo23_sentinel-2"


def test_overlay_cog_routes_sentinel2(tmp_path: Path, monkeypatch) -> None:
    spec = _spec()
    ex = DefaultPlanetExecutor(
        spec, cfg=None, data_dir=tmp_path, work_dir=tmp_path / "w",
        base_mbtiles=tmp_path / "base.mbtiles",
    )
    calls = {}

    def fake(overlay, resolved):
        calls["s2"] = overlay.name
        return Path("cog.tif")

    monkeypatch.setattr(ex, "_build_sentinel2_cog", fake)
    out = ex._build_overlay_cog(spec.overlays[0], resolved=object())
    assert calls.get("s2") == "tokyo23_s2"
    assert out == Path("cog.tif")
