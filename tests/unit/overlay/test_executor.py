"""DefaultPlanetExecutor wiring, with GDAL, tiling and network faked.

The executor only routes: each overlay source to its adapter, the resulting
COG through tiling, and the planet through metadata and ``pmtiles convert``.
These tests pin what is passed where; the heavy steps themselves are covered by
their own modules and by a real ``planetarble build``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from planetarble.core.models import (
    HLSConfig,
    OceanConfig,
    ProcessingConfig,
    Sentinel2Config,
)
from planetarble.overlay import adapters as adapters_module
from planetarble.overlay import executor as executor_module
from planetarble.overlay import parse_pipeline_spec
from planetarble.overlay.executor import DefaultPlanetExecutor
from planetarble.overlay.resolve import ResolvedAOI

BBOX = (174.77, -41.29, 174.78, -41.28)
RESOLVED = ResolvedAOI(bbox=BBOX)


def _spec(source: str = "naip", **overlay: Any):
    raw = {
        "name": "city",
        "source": source,
        "aoi": {"bbox": list(BBOX)},
        "min_zoom": 10,
        "max_zoom": 16,
        **overlay,
    }
    return parse_pipeline_spec(
        {"base": {"source": "bmng", "max_zoom": 7}, "overlays": [raw], "output": {"name": "planet_x"}}
    )


def _executor(tmp_path: Path, spec=None, cfg=None) -> DefaultPlanetExecutor:
    return DefaultPlanetExecutor(
        spec or _spec(),
        cfg if cfg is not None else SimpleNamespace(output_dir=tmp_path / "out"),
        data_dir=tmp_path / "data",
        work_dir=tmp_path / "work",
        base_mbtiles=tmp_path / "base.mbtiles",
    )


class FakeAdapter:
    """Records how the executor constructs and drives an adapter."""

    instances: List["FakeAdapter"] = []
    credit = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.plan_args: Any = None
        self.build_args: Any = None
        FakeAdapter.instances.append(self)

    def plan(self, aoi, zoom_range):
        self.plan_args = (aoi, zoom_range)
        return ["https://example.invalid/a.tif"]

    def build_raster(self, plan, output_path, **kwargs):
        self.build_args = (plan, output_path, kwargs)
        return output_path


@pytest.fixture
def fake_adapter(monkeypatch):
    FakeAdapter.instances = []
    FakeAdapter.credit = None
    for name in ("ESAWorldCoverAdapter", "NAIPAdapter", "NZImageryAdapter", "CogAdapter"):
        monkeypatch.setattr(adapters_module, name, FakeAdapter)
    return FakeAdapter


def test_work_dir_is_created(tmp_path: Path) -> None:
    _executor(tmp_path)
    assert (tmp_path / "work").is_dir()


@pytest.mark.parametrize(
    ("source", "suffix"),
    [
        ("esa-worldcover", "esa_worldcover"),
        ("naip", "naip"),
        ("nz-imagery", "nz_imagery"),
        ("cog", "cog"),
    ],
)
def test_cog_sources_route_to_their_adapter(tmp_path: Path, fake_adapter, source: str, suffix: str) -> None:
    options = {"year": 2021} if source != "cog" else {"urls": ["https://example.invalid/a.tif"]}
    spec = _spec(source, source_options=options, attribution="credit" if source == "cog" else None)
    ex = _executor(tmp_path, spec)

    out = ex._build_overlay_cog(spec.overlays[0], RESOLVED)

    (adapter,) = fake_adapter.instances
    for key, value in options.items():
        assert adapter.kwargs[key] == value
    assert adapter.plan_args == (RESOLVED, (10, 16))
    plan, path, kwargs = adapter.build_args
    assert plan == ["https://example.invalid/a.tif"]
    assert path == tmp_path / "work" / f"city_{suffix}.tif"
    assert kwargs == {"aoi_bbox": BBOX}
    assert out == path


def test_nz_imagery_gets_a_cache_dir_under_data_dir(tmp_path: Path, fake_adapter) -> None:
    ex = _executor(tmp_path, _spec("nz-imagery"))
    ex._build_overlay_cog(ex._spec.overlays[0], RESOLVED)
    assert fake_adapter.instances[0].kwargs["cache_dir"] == tmp_path / "data" / "cache" / "nz-imagery"


def test_nz_imagery_cache_dir_can_be_overridden(tmp_path: Path, fake_adapter) -> None:
    ex = _executor(tmp_path, _spec("nz-imagery", source_options={"cache_dir": "/elsewhere"}))
    ex._build_overlay_cog(ex._spec.overlays[0], RESOLVED)
    assert fake_adapter.instances[0].kwargs["cache_dir"] == "/elsewhere"


def test_nz_imagery_records_the_credit_decided_while_planning(tmp_path: Path, fake_adapter) -> None:
    fake_adapter.credit = "Wellington 0.075m Urban Aerial Photos (2025), Wellington City Council"
    ex = _executor(tmp_path, _spec("nz-imagery"))
    ex._build_overlay_cog(ex._spec.overlays[0], RESOLVED)
    assert ex.overlay_credits == {"city": fake_adapter.credit}


def test_no_credit_is_recorded_when_the_adapter_has_none(tmp_path: Path, fake_adapter) -> None:
    ex = _executor(tmp_path, _spec("nz-imagery"))
    ex._build_overlay_cog(ex._spec.overlays[0], RESOLVED)
    assert ex.overlay_credits == {}


def test_openaerialmap_drops_the_asset_option_and_uses_the_oam_cache(tmp_path: Path, monkeypatch) -> None:
    seen: Dict[str, Any] = {}

    class FakeOAM(FakeAdapter):
        def build_raster(self, plan, output_path, **kwargs):
            seen.update(kwargs, output=output_path)
            return output_path

    FakeAdapter.instances = []
    monkeypatch.setattr(adapters_module, "OpenAerialMapAdapter", FakeOAM)
    ex = _executor(tmp_path, _spec("openaerialmap", source_options={"asset": "x", "max_items": 2}))

    out = ex._build_overlay_cog(ex._spec.overlays[0], RESOLVED)

    assert FakeAdapter.instances[0].kwargs == {"max_items": 2}
    assert seen["cache_dir"] == tmp_path / "data" / "cache" / "oam"
    assert seen["aoi_bbox"] == BBOX
    assert out == tmp_path / "work" / "city_oam_cog.tif"


def test_unwired_source_raises(tmp_path: Path) -> None:
    ex = _executor(tmp_path, _spec("modis"))
    with pytest.raises(NotImplementedError, match="modis"):
        ex._build_overlay_cog(ex._spec.overlays[0], RESOLVED)


# --- tiling and attribution ---------------------------------------------------


class FakeTiler:
    packed: Dict[str, Any] = {}

    def __init__(self, config, *, temp_dir, output_dir) -> None:
        self.output_dir = output_dir

    def build_zxy(self, path, **kwargs):
        return self.output_dir / "zxy"

    def pack_mbtiles(self, zxy, **kwargs):
        FakeTiler.packed = kwargs
        return self.output_dir / "out.mbtiles"


@pytest.fixture
def fake_tiling(monkeypatch):
    runs: List[List[str]] = []
    monkeypatch.setattr("subprocess.run", lambda cmd, check: runs.append(cmd))
    monkeypatch.setattr(executor_module, "PmtilesTilingManager", FakeTiler)
    FakeTiler.packed = {}
    return runs


def test_tiling_adds_alpha_then_packs_with_overlay_zooms(tmp_path: Path, fake_tiling) -> None:
    ex = _executor(tmp_path)
    out = ex._tile_to_mbtiles(Path("in.tif"), ex._spec.overlays[0])

    (warp,) = fake_tiling
    assert warp[0] == "gdalwarp" and "-dstalpha" in warp
    assert warp[-2:] == ["in.tif", str(tmp_path / "work" / "city_alpha.tif")]
    assert FakeTiler.packed["min_zoom"] == 10
    assert FakeTiler.packed["max_zoom"] == 16
    assert FakeTiler.packed["name"] == "city"
    assert out == tmp_path / "work" / "tile_city" / "out.mbtiles"


def test_tile_attribution_defaults_to_the_registry_credit(tmp_path: Path, fake_tiling) -> None:
    ex = _executor(tmp_path)
    ex._tile_to_mbtiles(Path("in.tif"), ex._spec.overlays[0])
    assert FakeTiler.packed["attribution"] == "NAIP imagery courtesy of USDA Farm Service Agency"


def test_tile_attribution_fills_the_registry_template(tmp_path: Path, fake_tiling) -> None:
    ex = _executor(tmp_path, _spec("esa-worldcover", source_options={"year": 2020}))
    ex._tile_to_mbtiles(Path("in.tif"), ex._spec.overlays[0])
    assert "ESA WorldCover project 2020" in FakeTiler.packed["attribution"]


def test_tile_attribution_prefers_a_decided_credit_over_the_registry(tmp_path: Path, fake_tiling) -> None:
    ex = _executor(tmp_path)
    ex.overlay_credits["city"] = "decided"
    ex._tile_to_mbtiles(Path("in.tif"), ex._spec.overlays[0])
    assert FakeTiler.packed["attribution"] == "decided"


def test_tile_attribution_prefers_the_spec_over_everything(tmp_path: Path, fake_tiling) -> None:
    ex = _executor(tmp_path, _spec(attribution="from spec"))
    ex.overlay_credits["city"] = "decided"
    ex._tile_to_mbtiles(Path("in.tif"), ex._spec.overlays[0])
    assert FakeTiler.packed["attribution"] == "from spec"


# --- base, stack, merge -------------------------------------------------------


def test_build_base_reencodes_the_whole_world_once(tmp_path: Path, monkeypatch) -> None:
    calls: List[Dict[str, Any]] = []

    def fake_composite(sources, dest, **kwargs):
        calls.append(dict(kwargs, sources=sources))
        Path(dest).write_bytes(b"")
        return dest

    monkeypatch.setattr(executor_module, "composite_overzoom", fake_composite)
    ex = _executor(tmp_path)
    first = ex.build_base(ex._spec.base)
    second = ex.build_base(ex._spec.base)

    assert first == second == tmp_path / "work" / "base_webp.mbtiles"
    assert len(calls) == 1  # the cached re-encode is reused
    assert calls[0]["sources"] == [tmp_path / "base.mbtiles"]
    assert calls[0]["max_zoom"] == 7
    assert calls[0]["aoi_bbox"][0] == -180.0 and calls[0]["aoi_bbox"][2] == 180.0
    assert calls[0]["tile_format"] == "webp"


def test_stack_composites_the_sources_over_the_aoi(tmp_path: Path, monkeypatch) -> None:
    seen: Dict[str, Any] = {}

    def fake_composite(sources, dest, **kwargs):
        seen.update(kwargs, sources=sources, dest=dest)
        return dest

    monkeypatch.setattr(executor_module, "composite_overzoom", fake_composite)
    ex = _executor(tmp_path)
    out = ex.stack(["a.mbtiles", "b.mbtiles"], list(BBOX), 10, 16)

    assert seen["sources"] == [Path("a.mbtiles"), Path("b.mbtiles")]
    assert seen["aoi_bbox"] == BBOX
    assert (seen["min_zoom"], seen["max_zoom"]) == (10, 16)
    assert out.parent == tmp_path / "work" and out.name.startswith("stack_10_16_")


def test_merge_writes_next_to_the_work_dir(tmp_path: Path, monkeypatch) -> None:
    seen: Dict[str, Any] = {}

    def fake_merge(base, overlay, *, destination):
        seen.update(base=base, overlay=overlay)
        return destination

    monkeypatch.setattr(executor_module, "merge_mbtiles", fake_merge)
    out = _executor(tmp_path).merge(Path("base.mbtiles"), Path("x/stack_1.mbtiles"))
    assert seen == {"base": Path("base.mbtiles"), "overlay": Path("x/stack_1.mbtiles")}
    assert out == tmp_path / "work" / "merged_stack_1.mbtiles"


# --- package ------------------------------------------------------------------


def _metadata(path: Path) -> Dict[str, str]:
    conn = sqlite3.connect(str(path))
    try:
        return dict(conn.execute("SELECT name, value FROM metadata"))
    finally:
        conn.close()


def _mbtiles(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
    conn.execute("INSERT INTO metadata VALUES ('name', 'old'), ('format', 'webp')")
    conn.commit()
    conn.close()
    return path


def test_package_writes_name_and_attribution_then_converts(tmp_path: Path, monkeypatch) -> None:
    runs: List[List[str]] = []
    monkeypatch.setattr("subprocess.run", lambda cmd, check: runs.append(cmd))
    planet = _mbtiles(tmp_path / "planet.mbtiles")

    out = _executor(tmp_path).package(planet, "planet_x", attribution="A; B")

    assert _metadata(planet) == {"name": "planet_x", "format": "webp", "attribution": "A; B"}
    assert out == (tmp_path / "out" / "distribution" / "planet_x.pmtiles").resolve()
    assert runs == [["pmtiles", "convert", str(planet), str(out)]]


def test_package_without_attribution_leaves_it_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("subprocess.run", lambda cmd, check: None)
    planet = _mbtiles(tmp_path / "planet.mbtiles")
    _executor(tmp_path).package(planet, "planet_x")
    assert "attribution" not in _metadata(planet)


def test_package_replaces_an_existing_output(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "out" / "distribution" / "planet_x.pmtiles"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"stale")
    existed_during_convert: List[bool] = []
    monkeypatch.setattr("subprocess.run", lambda cmd, check: existed_during_convert.append(out.exists()))

    _executor(tmp_path).package(_mbtiles(tmp_path / "planet.mbtiles"), "planet_x")

    # pmtiles convert refuses to overwrite, so the stale file must be gone first
    assert existed_during_convert == [False]


# --- Sentinel-2 ---------------------------------------------------------------


class FakeProcessingManager:
    instances: List["FakeProcessingManager"] = []
    mosaic: Any = "mosaic.tif"

    def __init__(self, processing, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: List[Any] = []
        FakeProcessingManager.instances.append(self)

    def prepare_sentinel2_scene_manifest(self, *, destination, plan_region):
        self.calls.append(("manifest", destination, plan_region))

    def build_sentinel2_mosaic(self, manifest, *, plan_region):
        self.calls.append(("mosaic", manifest, plan_region))
        return FakeProcessingManager.mosaic

    def prefetch_sentinel2_assets(self, manifest, *, plan_region):
        self.calls.append(("prefetch", manifest, plan_region))
        return "stats"


@pytest.fixture
def fake_processing(monkeypatch, tmp_path: Path):
    import planetarble.processing.manager as manager_module

    FakeProcessingManager.instances = []
    FakeProcessingManager.mosaic = "mosaic.tif"
    monkeypatch.setattr(manager_module, "ProcessingManager", FakeProcessingManager)
    return SimpleNamespace(
        processing=ProcessingConfig(),
        output_dir=tmp_path / "out",
        hls=HLSConfig(),
        ocean=OceanConfig(),
        sentinel2=Sentinel2Config(max_cloud=50.0),
    )


def test_sentinel2_overrides_known_fields_and_ignores_unknown_ones(tmp_path: Path, fake_processing) -> None:
    spec = _spec("sentinel2", max_zoom=14, source_options={"assets": ["visual"], "max_items": 7, "bogus": 1})
    ex = _executor(tmp_path, spec, cfg=fake_processing)

    out = ex._build_overlay_cog(spec.overlays[0], RESOLVED)

    (pmgr,) = FakeProcessingManager.instances
    s2 = pmgr.kwargs["sentinel2"]
    assert s2.enabled is True
    assert s2.assets == ("visual",)
    assert s2.max_items == 7
    assert s2.max_cloud == 50.0  # kept from the configured block
    assert s2.bbox == BBOX
    assert s2.plan_region == "city"
    assert [r.name for r in s2.plan_regions] == ["city"]
    assert pmgr.kwargs["ocean"].enabled is False
    manifest = (tmp_path / "out" / "processing" / "sentinel2_scene_manifest_city.json").resolve()
    assert pmgr.calls == [("manifest", manifest, "city"), ("mosaic", manifest, "city")]
    assert out == Path("mosaic.tif")


def test_sentinel2_without_a_configured_block_uses_defaults(tmp_path: Path, fake_processing) -> None:
    fake_processing.sentinel2 = None
    ex = _executor(tmp_path, _spec("sentinel2", max_zoom=14), cfg=fake_processing)
    ex._build_overlay_cog(ex._spec.overlays[0], RESOLVED)
    assert FakeProcessingManager.instances[0].kwargs["sentinel2"].assets == Sentinel2Config().assets


def test_sentinel2_without_a_mosaic_is_an_error(tmp_path: Path, fake_processing) -> None:
    FakeProcessingManager.mosaic = None
    ex = _executor(tmp_path, _spec("sentinel2", max_zoom=14), cfg=fake_processing)
    with pytest.raises(RuntimeError, match="city"):
        ex._build_overlay_cog(ex._spec.overlays[0], RESOLVED)


def test_prefetch_skips_sources_other_than_sentinel2(tmp_path: Path) -> None:
    stats = _executor(tmp_path).prefetch_overlay(_spec().overlays[0])
    assert stats.overlay == "city"
    assert stats.downloaded_count == 0


def test_prefetch_downloads_sentinel2_assets_only(tmp_path: Path, fake_processing, monkeypatch) -> None:
    import planetarble.overlay.resolve as resolve_module

    monkeypatch.setattr(resolve_module, "resolve_aoi", lambda aoi, **kwargs: RESOLVED)
    ex = _executor(tmp_path, _spec("sentinel2", max_zoom=14), cfg=fake_processing)

    assert ex.prefetch_overlay(ex._spec.overlays[0]) == "stats"
    assert [call[0] for call in FakeProcessingManager.instances[0].calls] == ["manifest", "prefetch"]
