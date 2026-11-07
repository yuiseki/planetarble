"""Characterise ProcessingManager's public methods.

Every external tool goes through CommandRunner.run, so it is replaced with a
recorder and the GDAL command lines each method builds are compared with golden
files under golden/ (temporary paths are masked as <tmp>). Network-facing
helpers (STAC URL refresh, asset download, SAS tokens) are stubbed.

These pin current behaviour so processing/manager.py can be split safely. When
a change to a command line is intended, regenerate the golden files with

    PLANETARBLE_UPDATE_GOLDEN=1 uv run pytest tests/unit/processing/test_processing_manager_characterisation.py

and review the diff.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

import planetarble.processing.manager as pm
from planetarble.core.models import (
    CopernicusConfig,
    CopernicusLayerConfig,
    HLSConfig,
    ModisConfig,
    OceanConfig,
    ProcessingConfig,
    Sentinel2Config,
    ViirsConfig,
)

GOLDEN_DIR = Path(__file__).parent / "golden"
UPDATE_GOLDEN = os.environ.get("PLANETARBLE_UPDATE_GOLDEN") == "1"

Commands = list[tuple[str, list[str]]]


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> Commands:
    recorded: Commands = []

    def fake_run(self, command, *, description: str) -> None:
        recorded.append((description, [str(part) for part in command]))

    monkeypatch.setattr(pm.CommandRunner, "run", fake_run)
    return recorded


def _manager(tmp_path: Path, **kwargs) -> pm.ProcessingManager:
    return pm.ProcessingManager(
        ProcessingConfig(),
        temp_dir=tmp_path / "tmp",
        output_dir=tmp_path / "output",
        data_dir=tmp_path / "data",
        **kwargs,
    )


def _touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def assert_golden(name: str, recorded: Commands, tmp_path: Path) -> None:
    actual = json.loads(json.dumps(recorded).replace(str(tmp_path), "<tmp>"))
    path = GOLDEN_DIR / f"{name}.json"
    if UPDATE_GOLDEN:
        GOLDEN_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
        return
    if not path.exists():
        pytest.fail(f"golden file missing: {path} (run with PLANETARBLE_UPDATE_GOLDEN=1)")
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected


def _descriptions(recorded: Commands) -> list[str]:
    return [description for description, _ in recorded]


# ------------------------------------------------------------------ BMNG chain


def test_compose_bmng_panels_builds_vrt_then_geotiff(tmp_path, commands) -> None:
    mgr = _manager(tmp_path)
    panels = tmp_path / "data" / "bmng"
    for name in ("B1.tif", "A1.tif"):
        _touch(panels / name)

    out = mgr.compose_bmng_panels(panels)

    assert out == tmp_path / "output" / "processing" / "bmng_mosaic.tif"
    listed = (tmp_path / "tmp" / "bmng_panels.txt").read_text(encoding="utf-8").splitlines()
    assert listed == [str(panels / "A1.tif"), str(panels / "B1.tif")]
    assert_golden("compose_bmng_panels", commands, tmp_path)


def test_compose_bmng_single_panel_is_passed_through(tmp_path, commands) -> None:
    panel = _touch(tmp_path / "bmng" / "A1.tif")
    assert _manager(tmp_path).compose_bmng_panels(panel.parent) == panel
    assert commands == []


def test_compose_bmng_without_panels_is_an_error(tmp_path, commands) -> None:
    (tmp_path / "bmng").mkdir()
    with pytest.raises(FileNotFoundError):
        _manager(tmp_path).compose_bmng_panels(tmp_path / "bmng")


def test_normalize_bmng_command_and_hash_reuse(tmp_path, commands) -> None:
    mgr = _manager(tmp_path)
    source = _touch(tmp_path / "bmng_mosaic.tif", b"v1")

    out = mgr.normalize_bmng(source, source_files=[source])
    assert out == tmp_path / "output" / "processing" / "bmng_mosaic_normalized.tif"
    assert_golden("normalize_bmng", commands, tmp_path)

    # Same source + existing output -> reused without running GDAL again.
    _touch(out)
    assert mgr.normalize_bmng(source, source_files=[source]) == out
    assert len(commands) == 1

    # Changed source content -> rebuilt.
    source.write_bytes(b"v2")
    mgr.normalize_bmng(source, source_files=[source])
    assert len(commands) == 2


def test_generate_hillshade_command(tmp_path, commands) -> None:
    gebco = _touch(tmp_path / "GEBCO_2024_CF.nc")
    out = _manager(tmp_path).generate_hillshade(gebco)
    assert out == tmp_path / "output" / "processing" / "GEBCO_2024_CF_hillshade.tif"
    assert_golden("generate_hillshade", commands, tmp_path)


def test_create_masks_extracts_archives_and_reuses(tmp_path, commands) -> None:
    ne_dir = tmp_path / "natural_earth"
    ne_dir.mkdir()
    with zipfile.ZipFile(ne_dir / "ne_10m_land.zip", "w") as bundle:
        bundle.writestr("ne_10m_land.shp", "shape")

    mgr = _manager(tmp_path)
    dest = mgr.create_masks(ne_dir)

    extracted = dest / "ne_10m_land" / "ne_10m_land.shp"
    assert dest == tmp_path / "tmp" / "natural_earth"
    assert extracted.read_text(encoding="utf-8") == "shape"

    extracted.unlink()
    mgr.create_masks(ne_dir)
    assert not extracted.exists(), "unchanged archives must be reused, not re-extracted"
    assert commands == []


def test_create_masks_without_archives_is_an_error(tmp_path, commands) -> None:
    (tmp_path / "natural_earth").mkdir()
    with pytest.raises(FileNotFoundError):
        _manager(tmp_path).create_masks(tmp_path / "natural_earth")


def test_create_cog_command(tmp_path, commands) -> None:
    out = _manager(tmp_path).create_cog(tmp_path / "mosaic.tif")
    assert out == tmp_path / "output" / "processing" / "mosaic_cog.tif"
    assert_golden("create_cog", commands, tmp_path)


def test_blend_layers_clamps_opacity(tmp_path, commands) -> None:
    _manager(tmp_path).blend_layers(tmp_path / "base.tif", tmp_path / "over.tif", 2.5)
    assert "A*(1-1.0)+B*(1.0)" in commands[0][1]
    assert_golden("blend_layers", commands, tmp_path)


# -------------------------------------------------------------- MODIS / VIIRS


def _rgb_product_tree(root: Path, tile: str, date_code: str, bands: list[str]) -> None:
    for band in bands:
        _touch(root / tile / "granule" / f"X_{band}_doy{date_code}_v1.tif")


def test_prepare_modis_rgb_commands(tmp_path, commands) -> None:
    bands = ["Nadir_Reflectance_Band1", "Nadir_Reflectance_Band4", "Nadir_Reflectance_Band3"]
    root = tmp_path / "data" / "modis"
    _rgb_product_tree(root, "h29v05", "2024100", bands)
    mgr = _manager(tmp_path, modis=ModisConfig(gamma=0.9))

    out = mgr.prepare_modis_rgb(root, tiles=["h29v05"], date_code="2024100")

    assert out == tmp_path / "output" / "processing" / "modis_2024100_rgb_cog.tif"
    assert_golden("prepare_modis_rgb", commands, tmp_path)


@pytest.mark.parametrize(
    ("product", "suffix"),
    [("VNP09GA.002", "_1"), ("VNP09GA", "")],
)
def test_prepare_viirs_rgb_band_names_follow_product(tmp_path, commands, product, suffix) -> None:
    bands = [f"SurfReflect_I{i}{suffix}" for i in (1, 2, 3)]
    root = tmp_path / "data" / "viirs"
    _rgb_product_tree(root, "h29v05", "2024100", bands)
    mgr = _manager(tmp_path, viirs=ViirsConfig(product=product))

    mgr.prepare_viirs_rgb(root, tiles=["h29v05"], date_code="2024100")

    red_list = (tmp_path / "tmp" / "viirs_red_tiles.txt").read_text(encoding="utf-8")
    assert f"SurfReflect_I1{suffix}_doy" in red_list
    assert _descriptions(commands) == [
        "mosaic VIIRS red band",
        "mosaic VIIRS green band",
        "mosaic VIIRS blue band",
        "combine VIIRS bands into RGB VRT",
        "convert VIIRS RGB mosaic to GeoTIFF",
        "create Cloud Optimized GeoTIFF",
    ]
    assert "-exponent" in commands[4][1]  # default VIIRS gamma 0.8


def test_prepare_rgb_product_errors(tmp_path, commands) -> None:
    mgr = _manager(tmp_path)
    root = tmp_path / "data" / "modis"
    with pytest.raises(ValueError, match="No modis tiles"):
        mgr.prepare_modis_rgb(root, tiles=[], date_code="2024100")
    with pytest.raises(FileNotFoundError, match="tile directory not found"):
        mgr.prepare_modis_rgb(root, tiles=["h00v00"], date_code="2024100")
    _rgb_product_tree(root, "h29v05", "2024100", ["Nadir_Reflectance_Band1"])
    with pytest.raises(FileNotFoundError, match="Band Nadir_Reflectance_Band4 not found"):
        mgr.prepare_modis_rgb(root, tiles=["h29v05"], date_code="2024100")


# ------------------------------------------------------------------ Copernicus


def test_prepare_copernicus_layers(tmp_path, commands) -> None:
    tiles = tmp_path / "data" / "copernicus" / "tiles"
    _touch(tiles / "true_color" / "14" / "14552" / "6451.jpg")
    _touch(tiles / "true_color" / "14" / "14552" / "6452.jpg")
    _touch(tiles / "true_color" / "14" / "14552" / "notes.txt")
    config = CopernicusConfig(
        enabled=True,
        min_zoom=12,
        max_zoom=14,
        layers=(CopernicusLayerConfig(name="TRUE_COLOR", output="true_color"),
                CopernicusLayerConfig(name="MISSING")),
    )
    out = _manager(tmp_path, copernicus=config).prepare_copernicus_layers()

    assert out == [tmp_path / "output" / "processing" / "copernicus_true_color_cog.tif"]
    assert_golden("prepare_copernicus_layers", commands, tmp_path)


def test_prepare_copernicus_layers_guards(tmp_path, commands) -> None:
    assert _manager(tmp_path).prepare_copernicus_layers() == []
    assert _manager(tmp_path, copernicus=CopernicusConfig(enabled=False)).prepare_copernicus_layers() == []
    with pytest.raises(FileNotFoundError, match="Copernicus tiles directory not found"):
        _manager(tmp_path, copernicus=CopernicusConfig(enabled=True)).prepare_copernicus_layers()


# ----------------------------------------------------------------------- ocean


def test_render_ocean_disabled_and_dry_run(tmp_path, commands) -> None:
    assert _manager(tmp_path).render_ocean(tmp_path / "etopo.tif") == {}
    mgr = pm.ProcessingManager(
        ProcessingConfig(),
        temp_dir=tmp_path / "tmp",
        output_dir=tmp_path / "output",
        data_dir=tmp_path / "data",
        ocean=OceanConfig(enabled=True),
        dry_run=True,
    )
    ocean_dir = tmp_path / "output" / "processing" / "ocean"
    assert mgr.render_ocean(tmp_path / "etopo.tif") == {
        "color": ocean_dir / "etopo_depth_color.tif",
        "hillshade": ocean_dir / "etopo_hillshade.tif",
    }


# ------------------------------------------------------------------------- HLS


class _FakeManifest:
    def __init__(self) -> None:
        self.written: list[Path] = []

    def write(self, dest: Path) -> None:
        self.written.append(dest)


def test_prepare_hls_scene_manifest(tmp_path, commands, monkeypatch) -> None:
    plan = _touch(tmp_path / "plan.ndjson")
    assert _manager(tmp_path).prepare_hls_scene_manifest(plan) is None  # HLS disabled by default

    calls: dict[str, object] = {}
    manifest = _FakeManifest()

    class FakeBuilder:
        def __init__(self, config, *, cache_dir, cache_ttl_days):
            calls["init"] = (config, cache_dir, cache_ttl_days)

        def build(self, plan_path, **kwargs):
            calls["build"] = (plan_path, kwargs)
            return manifest

    monkeypatch.setattr(pm, "HLSSceneManifestBuilder", FakeBuilder)
    hls = HLSConfig(enabled=True)
    dest = _manager(tmp_path, hls=hls).prepare_hls_scene_manifest(plan, max_tiles=5)

    assert dest == tmp_path / "output" / "processing" / "hls_scene_manifest.json"
    assert calls["init"] == (hls, tmp_path / "data" / "cache" / "hls", hls.cache_ttl_days)
    assert calls["build"] == (
        plan,
        {"max_tiles": 5, "max_scenes_per_tile": hls.scenes_per_tile,
         "search_limit": hls.scene_search_limit, "progress_interval": 100},
    )
    assert manifest.written == [dest]


def _hls_manifest(tmp_path: Path) -> Path:
    def scene(name: str, cloud: float) -> dict:
        return {
            "id": name,
            "cloud_cover": cloud,
            "bbox": [139.0, 35.0, 140.0, 36.0],
            "bands": {band: f"/data/{name}_{band}.tif" for band in ("B02", "B03", "B04")},
        }

    path = tmp_path / "hls_scene_manifest.json"
    path.write_text(json.dumps({"scenes": [scene("clear", 5.0), scene("hazy", 60.0)], "summary": {}}))
    return path


@pytest.fixture
def offline_hls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pm, "_refresh_hls_scene_urls", lambda scenes, config: list(scenes))
    monkeypatch.setattr(pm, "_cache_hls_assets", lambda scenes, **kwargs: list(scenes))


def test_build_hls_mosaic_vrt_strategy(tmp_path, commands, offline_hls) -> None:
    hls = HLSConfig(enabled=True, qa_mask_flags=(), mosaic_strategy="mosaic")
    out = _manager(tmp_path, hls=hls).build_hls_mosaic(_hls_manifest(tmp_path))

    assert out == tmp_path / "output" / "processing" / "hls_mosaic_cog.tif"
    red = (tmp_path / "tmp" / "hls_red_sources.txt").read_text(encoding="utf-8").splitlines()
    assert red == ["/data/hazy_B04.tif", "/data/clear_B04.tif"]  # cleanest painted last
    assert_golden("build_hls_mosaic_vrt", commands, tmp_path)


def test_build_hls_mosaic_median_strategy(tmp_path, commands, offline_hls, monkeypatch) -> None:
    median_out = tmp_path / "tmp" / "hls_median_rgb.tif"
    seen: list[dict] = []
    monkeypatch.setattr(
        pm, "_build_hls_median_rgb", lambda band_lists, temp_dir: seen.append(band_lists) or median_out
    )
    hls = HLSConfig(enabled=True, qa_mask_flags=(), mosaic_strategy="best_pixel")
    _manager(tmp_path, hls=hls).build_hls_mosaic(_hls_manifest(tmp_path))

    assert set(seen[0]) == {"red", "green", "blue"}
    assert _descriptions(commands) == ["convert HLS RGB mosaic to GeoTIFF", "create Cloud Optimized GeoTIFF"]
    assert commands[0][1][-2] == str(median_out)


def test_build_hls_mosaic_masks_clouds_when_flags_set(tmp_path, commands, offline_hls, monkeypatch) -> None:
    masked: list[dict] = []

    def fake_mask(runner, scenes, *, temp_dir, mask_value, dilation):
        masked.append({"mask_value": mask_value, "dilation": dilation, "n": len(scenes)})
        return scenes

    monkeypatch.setattr(pm, "_mask_hls_scene_bands", fake_mask)
    hls = HLSConfig(enabled=True, qa_mask_flags=("cloud",), mosaic_strategy="mosaic", cloud_mask_dilation=2)
    _manager(tmp_path, hls=hls).build_hls_mosaic(_hls_manifest(tmp_path))
    assert masked == [{"mask_value": pm._qa_mask_value(("cloud",)), "dilation": 2, "n": 2}]


def test_build_hls_mosaic_guards(tmp_path, commands, offline_hls) -> None:
    manifest = _hls_manifest(tmp_path)
    assert _manager(tmp_path).build_hls_mosaic(manifest) is None  # disabled

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"scenes": []}))
    assert _manager(tmp_path, hls=HLSConfig(enabled=True)).build_hls_mosaic(empty) is None

    with pytest.raises(ValueError, match="Unknown HLS plan region"):
        _manager(tmp_path, hls=HLSConfig(enabled=True)).build_hls_mosaic(manifest, plan_region="nowhere")
    assert commands == []


# ------------------------------------------------------------------ Sentinel-2


def test_prepare_sentinel2_scene_manifest(tmp_path, commands, monkeypatch) -> None:
    assert _manager(tmp_path).prepare_sentinel2_scene_manifest() is None  # disabled by default

    calls: dict[str, object] = {}
    manifest = _FakeManifest()

    class FakeBuilder:
        def __init__(self, config, *, cache_dir, cache_ttl_days):
            calls["init"] = (config, cache_dir, cache_ttl_days)

        def build(self, **kwargs):
            calls["build"] = kwargs
            return manifest

    monkeypatch.setattr(pm, "Sentinel2SceneManifestBuilder", FakeBuilder)
    s2 = Sentinel2Config(enabled=True)
    dest = _manager(tmp_path, sentinel2=s2).prepare_sentinel2_scene_manifest(force_refresh=True)

    assert dest == tmp_path / "output" / "processing" / "sentinel2_scene_manifest.json"
    assert calls["init"] == (s2, tmp_path / "data" / "cache" / "sentinel2", s2.cache_ttl_days)
    assert calls["build"] == {"bbox": s2.bbox, "max_items": None, "force_refresh": True}
    assert manifest.written == [dest]


def _s2_manifest(tmp_path: Path, assets: tuple[str, ...], n: int = 2) -> Path:
    scenes = [
        {
            "item_id": f"S2_{i}",
            "collection_id": "sentinel-2-l2a",
            "cloud_cover": float(10 * (n - i)),
            "assets": {asset: f"https://example.invalid/S2_{i}/{asset}.tif" for asset in assets},
        }
        for i in range(n)
    ]
    path = tmp_path / "sentinel2_scene_manifest.json"
    path.write_text(json.dumps({"scenes": scenes, "summary": {}}))
    return path


@pytest.fixture
def offline_s2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pm, "_refresh_sentinel2_scene_urls", lambda scenes, config: list(scenes))
    monkeypatch.setattr(pm, "_cache_sentinel2_assets", lambda scenes, **kwargs: list(scenes))
    monkeypatch.setattr(pm, "_is_valid_raster", lambda path: False)


@pytest.mark.parametrize("mode", ["bands", "visual"])
def test_build_sentinel2_mosaic(tmp_path, commands, offline_s2, mode) -> None:
    assets = ("B02", "B03", "B04") if mode == "bands" else ("visual",)
    s2 = Sentinel2Config(enabled=True, assets=assets)
    out = _manager(tmp_path, sentinel2=s2).build_sentinel2_mosaic(_s2_manifest(tmp_path, assets))

    processing = tmp_path / "output" / "processing"
    assert out == processing / pm._sentinel2_mosaic_filename(None, s2.bbox)
    assert_golden(f"build_sentinel2_mosaic_{mode}", commands, tmp_path)


def test_build_sentinel2_mosaic_caps_scenes_by_cloud(tmp_path, commands, offline_s2) -> None:
    s2 = Sentinel2Config(enabled=True, assets=("visual",), mosaic_max_scenes=2)
    _manager(tmp_path, sentinel2=s2).build_sentinel2_mosaic(_s2_manifest(tmp_path, ("visual",), n=4))
    urls = (tmp_path / "tmp" / "sentinel2_visual_sources.txt").read_text(encoding="utf-8").splitlines()
    # 4 scenes with cloud 40,30,20,10 -> keep the two clearest, cleanest last
    assert urls == ["https://example.invalid/S2_2/visual.tif", "https://example.invalid/S2_3/visual.tif"]


def test_build_sentinel2_mosaic_guards(tmp_path, commands, offline_s2, monkeypatch) -> None:
    assets = ("visual",)
    manifest = _s2_manifest(tmp_path, assets)
    assert _manager(tmp_path).build_sentinel2_mosaic(manifest) is None  # disabled

    s2 = Sentinel2Config(enabled=True, assets=assets)
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"scenes": []}))
    with pytest.raises(ValueError, match="No Sentinel-2 scenes"):
        _manager(tmp_path, sentinel2=s2).build_sentinel2_mosaic(empty)

    bad = Sentinel2Config(enabled=True, assets=("B08",))
    with pytest.raises(ValueError, match="sentinel2.assets must be"):
        _manager(tmp_path, sentinel2=bad).build_sentinel2_mosaic(manifest)

    monkeypatch.setattr(pm, "_is_valid_raster", lambda path: True)
    out = _manager(tmp_path, sentinel2=s2).build_sentinel2_mosaic(manifest)
    assert out.name.startswith("sentinel2_mosaic_")
    assert commands == []


def test_prefetch_sentinel2_assets_counts_and_refreshes_token(tmp_path, commands, monkeypatch) -> None:
    tokens: list[str] = []

    def fake_token(collection: str, *, timeout: int) -> str:
        tokens.append(collection)
        return f"tok{len(tokens)}"

    statuses = iter(["download", "failed", "hit", "hit"])
    cached: list[tuple[str, Path, str]] = []

    def fake_cache(url: str, *, cache_dir: Path, timeout: int, asset_name: str):
        status = next(statuses)
        local = _touch(cache_dir / f"{asset_name}.tif", b"12345")
        cached.append((url, cache_dir, status))
        return local, status

    monkeypatch.setattr(pm, "fetch_sas_token", fake_token)
    monkeypatch.setattr(pm, "_cache_sentinel2_asset", fake_cache)
    monkeypatch.setattr(pm, "_refresh_sentinel2_scene_urls", lambda scenes, config: list(scenes))

    s2 = Sentinel2Config(enabled=True, assets=("visual",), mosaic_max_scenes=3)
    stats = _manager(tmp_path, sentinel2=s2).prefetch_sentinel2_assets(
        _s2_manifest(tmp_path, ("visual",), n=3), plan_region="tokyo"
    )

    assert (stats.overlay, stats.downloaded_count, stats.downloaded_bytes, stats.hit_count) == ("tokyo", 1, 5, 2)
    assert tokens == ["sentinel-2-l2a", "sentinel-2-l2a"]  # re-fetched once after "failed"
    assert cached[1][0].endswith("?tok1") and cached[2][0].endswith("?tok2")
    assert cached[0][1] == tmp_path / "data" / "cache" / "sentinel2" / "assets" / "sentinel-2-l2a" / "S2_0"
    assert commands == []


def test_prefetch_sentinel2_assets_disabled(tmp_path, commands) -> None:
    stats = _manager(tmp_path).prefetch_sentinel2_assets(tmp_path / "missing.json")
    assert (stats.overlay, stats.downloaded_count) == ("sentinel2", 0)
