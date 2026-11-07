"""Characterise what each CLI handler asks the managers to do.

The managers are replaced with autospec'd mocks, so these tests pin both the
sequence of calls and their signatures. They exist to make splitting
cli/main.py into modules safe: a moved handler that loses an import, renames a
call, or drops a branch fails here instead of in a multi-hour real run.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from unittest.mock import MagicMock, call, create_autospec

import pytest
from conftest import cli_attr

from planetarble import acquisition
from planetarble.acquisition import CopernicusCredentialsMissing
from planetarble.config import PipelineConfig
from planetarble.core.models import (
    CopernicusConfig,
    HLSConfig,
    HLSPlanRegion,
    OceanConfig,
    ProcessingConfig,
    Sentinel2Config,
)

cli = importlib.import_module("planetarble.cli.main")


def _cfg(tmp_path: Path, tile_source: str, **sections: object) -> PipelineConfig:
    sections.setdefault("ocean", OceanConfig(enabled=False))
    return PipelineConfig(
        data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        output_dir=tmp_path / "output",
        processing=ProcessingConfig(tile_source=tile_source),
        **sections,  # type: ignore[arg-type]
    )


@pytest.fixture
def use_cfg(patch_cli) -> Callable[[PipelineConfig], None]:
    def _use(cfg: PipelineConfig) -> None:
        patch_cli("_load_env", lambda: None)
        patch_cli("_resolve_config_path", lambda path: Path("pipeline.yaml"))
        patch_cli("load_config", lambda path: cfg)

    return _use


@pytest.fixture
def fake(patch_cli) -> Callable[[str], MagicMock]:
    """Replace a class used by the CLI with an autospec; return the class mock."""

    def _fake(name: str) -> MagicMock:
        cls = create_autospec(cli_attr(name))
        patch_cli(name, cls)
        return cls

    return _fake


def _names(instance: MagicMock) -> list[str]:
    return [c[0] for c in instance.method_calls]


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _summary(path: Path) -> SimpleNamespace:
    return SimpleNamespace(path=path, zoom=10, tile_count=3, season_counts={"summer": 3})


# --------------------------------------------------------------------- acquire


def test_acquire_legacy_downloads_every_source_then_writes_manifest(tmp_path, use_cfg, fake) -> None:
    cfg = _cfg(tmp_path, "bmng", hls=HLSConfig(enabled=False))
    use_cfg(cfg)
    cls = fake("AcquisitionManager")
    acq = cls.return_value
    acq.download_copernicus_tiles.return_value = []

    assert cli.main(["acquire"]) == 0

    assert cls.call_args == call(
        cfg.data_dir, manifest_path=cfg.output_dir / "MANIFEST.json", use_aria2=True
    )
    assert acq.method_calls == [
        call.download_bmng("500m", force=False),
        call.download_gebco(force=False),
        call.download_natural_earth(force=False),
        call.download_modis_mcd43a4(force=False),
        call.download_viirs_corrected_reflectance(force=False, product=cfg.viirs.product),
        call.check_copernicus_connection(),
        call.download_copernicus_tiles(cfg.copernicus, force=False),
        call.generate_manifest(generation_params={"bmng_resolution": "500m"}),
    ]


def test_acquire_legacy_optional_sources_failing_do_not_abort(tmp_path, use_cfg, fake) -> None:
    use_cfg(_cfg(tmp_path, "bmng", hls=HLSConfig(enabled=False)))
    acq = fake("AcquisitionManager").return_value
    acq.download_modis_mcd43a4.side_effect = RuntimeError("modis down")
    acq.download_viirs_corrected_reflectance.side_effect = RuntimeError("viirs down")
    acq.check_copernicus_connection.side_effect = RuntimeError("no creds")
    acq.download_copernicus_tiles.side_effect = CopernicusCredentialsMissing("no creds")

    assert cli.main(["acquire", "--bmng-resolution", "2km", "--force", "--no-aria2"]) == 0

    assert acq.download_bmng.call_args == call("2km", force=True)
    assert acq.generate_manifest.call_args == call(generation_params={"bmng_resolution": "2km"})


def test_acquire_ocean_downloads_etopo_first(tmp_path, use_cfg, fake) -> None:
    use_cfg(_cfg(tmp_path, "bmng", hls=HLSConfig(enabled=False), ocean=OceanConfig(enabled=True)))
    acq = fake("AcquisitionManager").return_value
    acq.download_etopo.return_value = tmp_path / "etopo.tif"
    acq.download_copernicus_tiles.return_value = []

    assert cli.main(["acquire"]) == 0

    assert _names(acq)[0] == "download_etopo"
    params = acq.generate_manifest.call_args.kwargs["generation_params"]
    assert params["etopo_path"] == str(tmp_path / "etopo.tif")


def test_acquire_hls_without_regions_builds_global_plan(tmp_path, use_cfg, fake) -> None:
    cfg = _cfg(tmp_path, "hls")
    use_cfg(cfg)
    acq = fake("AcquisitionManager").return_value
    plan = cfg.data_dir / "plans" / f"hls_z{cfg.hls.target_zoom}_plan.ndjson"
    acq.build_hls_plan.return_value = _summary(plan)

    assert cli.main(["acquire"]) == 0

    assert acq.method_calls == [
        call.build_hls_plan(cfg.hls, destination=plan, force=False),
        call.generate_manifest(
            generation_params={
                "hls_plan": {"path": str(plan), "zoom": 10, "tiles": 3, "season_counts": {"summer": 3}}
            }
        ),
    ]


def test_acquire_hls_plan_region_without_regions_is_an_error(tmp_path, use_cfg, fake) -> None:
    use_cfg(_cfg(tmp_path, "hls"))
    fake("AcquisitionManager")
    with pytest.raises(SystemExit, match="plan_regions is empty"):
        cli.main(["acquire", "--plan-region", "tokyo"])


def test_acquire_sentinel2_unknown_region_is_an_error(tmp_path, use_cfg, fake) -> None:
    regions = (HLSPlanRegion(name="tokyo", bbox=(139.0, 35.0, 140.0, 36.0)),)
    use_cfg(_cfg(tmp_path, "sentinel2", sentinel2=Sentinel2Config(enabled=True, plan_regions=regions)))
    acq = fake("AcquisitionManager").return_value
    with pytest.raises(SystemExit, match="Unknown Sentinel-2 plan region: osaka"):
        cli.main(["acquire", "--plan-region", "osaka"])
    assert _names(acq) == []


def test_acquire_sentinel2_needs_no_downloads(tmp_path, use_cfg, fake) -> None:
    use_cfg(_cfg(tmp_path, "sentinel2", sentinel2=Sentinel2Config(enabled=True)))
    acq = fake("AcquisitionManager").return_value
    assert cli.main(["acquire"]) == 0
    assert acq.method_calls == [call.generate_manifest(generation_params=None)]


# --------------------------------------------------------------------- process


def test_process_constructs_manager_from_config(tmp_path, use_cfg, fake) -> None:
    cfg = _cfg(tmp_path, "copernicus", copernicus=CopernicusConfig(enabled=True))
    use_cfg(cfg)
    cls = fake("ProcessingManager")
    cls.return_value.prepare_copernicus_layers.return_value = []

    assert cli.main(["process", "--dry-run"]) == 0

    assert cls.call_args == call(
        cfg.processing,
        temp_dir=cfg.temp_dir,
        output_dir=cfg.output_dir,
        data_dir=cfg.data_dir,
        copernicus=cfg.copernicus,
        sentinel2=cfg.sentinel2,
        modis=cfg.modis,
        viirs=cfg.viirs,
        hls=cfg.hls,
        ocean=cfg.ocean,
        dry_run=True,
    )
    # --dry-run forces the copernicus layer rebuild path
    assert cls.return_value.method_calls == [call.prepare_copernicus_layers(force=True)]


def test_process_hls_requires_plan(tmp_path, use_cfg, fake) -> None:
    use_cfg(_cfg(tmp_path, "hls"))
    fake("ProcessingManager")
    with pytest.raises(SystemExit, match="HLS plan not found"):
        cli.main(["process"])


def test_process_hls_builds_manifest_then_mosaic(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "hls")
    use_cfg(cfg)
    plan = _touch(cfg.data_dir / "plans" / "hls_z10_plan_tokyo.ndjson")
    manifest = (cfg.output_dir / "processing" / "hls_scene_manifest_tokyo.json").resolve()
    proc = fake("ProcessingManager").return_value
    proc.prepare_hls_scene_manifest.return_value = manifest
    patch_cli("_is_valid_raster", lambda path: False)

    assert cli.main(["process", "--plan-region", "tokyo"]) == 0

    assert proc.method_calls == [
        call.prepare_hls_scene_manifest(plan, destination=manifest),
        call.build_hls_mosaic(manifest, plan_region="tokyo"),
    ]


def test_process_hls_skips_valid_outputs(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "hls")
    use_cfg(cfg)
    _touch(cfg.data_dir / "plans" / "hls_z10_plan_tokyo.ndjson")
    _touch(
        cfg.output_dir / "processing" / "hls_scene_manifest_tokyo.json",
        json.dumps({"scenes": [{"id": "a"}], "summary": {}}),
    )
    proc = fake("ProcessingManager").return_value
    patch_cli("_is_valid_raster", lambda path: True)

    assert cli.main(["process", "--plan-region", "tokyo"]) == 0
    assert proc.method_calls == []


def test_process_hls_ocean_renders_when_outputs_missing(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "hls", ocean=OceanConfig(enabled=True))
    use_cfg(cfg)
    _touch(cfg.data_dir / "plans" / "hls_z10_plan.ndjson")
    etopo = _touch(cfg.data_dir / "etopo" / "ETOPO_2022_15s_bed.tif").resolve()
    proc = fake("ProcessingManager").return_value
    proc.render_ocean.return_value = {}
    patch_cli("_is_valid_raster", lambda path: False)

    assert cli.main(["process"]) == 0
    assert _names(proc) == ["prepare_hls_scene_manifest", "render_ocean"]
    assert proc.render_ocean.call_args == call(etopo)


def test_process_sentinel2_disabled_is_an_error(tmp_path, use_cfg, fake) -> None:
    use_cfg(_cfg(tmp_path, "sentinel2"))
    fake("ProcessingManager")
    with pytest.raises(SystemExit, match="sentinel2.enabled is false"):
        cli.main(["process"])


def test_process_sentinel2_builds_manifest_then_mosaic(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "sentinel2", sentinel2=Sentinel2Config(enabled=True))
    use_cfg(cfg)
    manifest = (cfg.output_dir / "processing" / "sentinel2_scene_manifest.json").resolve()
    proc = fake("ProcessingManager").return_value
    proc.prepare_sentinel2_scene_manifest.return_value = manifest
    patch_cli("_is_valid_raster", lambda path: False)

    assert cli.main(["process", "--force"]) == 0

    assert proc.method_calls == [
        call.prepare_sentinel2_scene_manifest(destination=manifest, force_refresh=True, plan_region=None),
        call.build_sentinel2_mosaic(manifest, force=True, plan_region=None),
    ]


def test_process_sentinel2_regions_require_a_region(tmp_path, use_cfg, fake) -> None:
    regions = (HLSPlanRegion(name="tokyo", bbox=(139.0, 35.0, 140.0, 36.0)),)
    use_cfg(_cfg(tmp_path, "sentinel2", sentinel2=Sentinel2Config(enabled=True, plan_regions=regions)))
    fake("ProcessingManager")
    with pytest.raises(SystemExit, match="pass --plan-region"):
        cli.main(["process"])


def test_process_copernicus_failure_is_logged_not_raised(tmp_path, use_cfg, fake) -> None:
    use_cfg(_cfg(tmp_path, "copernicus", copernicus=CopernicusConfig(enabled=True)))
    proc = fake("ProcessingManager").return_value
    proc.prepare_copernicus_layers.side_effect = RuntimeError("boom")
    assert cli.main(["process"]) == 0
    assert proc.method_calls == [call.prepare_copernicus_layers(force=False)]


def test_process_legacy_bmng_runs_stages_in_order(tmp_path, use_cfg, fake) -> None:
    cfg = _cfg(tmp_path, "bmng")
    use_cfg(cfg)
    panel = _touch(cfg.data_dir / "bmng" / "500m" / "A1.tif").resolve()
    gebco = _touch(cfg.data_dir / "gebco" / "GEBCO_2024_CF.nc").resolve()
    ne_dir = (cfg.data_dir / "natural_earth").resolve()
    ne_dir.mkdir(parents=True)
    proc = fake("ProcessingManager").return_value

    assert cli.main(["process"]) == 0

    composed = proc.compose_bmng_panels.return_value
    normalized = proc.normalize_bmng.return_value
    assert proc.method_calls == [
        call.compose_bmng_panels(panel.parent),
        call.normalize_bmng(composed, source_files=(panel,)),
        call.generate_hillshade(gebco),
        call.create_masks(ne_dir),
        call.create_cog(normalized),
    ]


@pytest.mark.parametrize(
    ("missing", "message"),
    [("bmng", "BMNG directory not found"), ("gebco", "GEBCO file not found"), ("ne", "Natural Earth")],
)
def test_process_legacy_bmng_missing_inputs(tmp_path, use_cfg, fake, missing, message) -> None:
    cfg = _cfg(tmp_path, "bmng")
    use_cfg(cfg)
    if missing != "bmng":
        _touch(cfg.data_dir / "bmng" / "500m" / "A1.tif")
    if missing == "ne":
        _touch(cfg.data_dir / "gebco" / "GEBCO_2024_CF.nc")
    fake("ProcessingManager")
    with pytest.raises(SystemExit, match=message):
        cli.main(["process"])


# ------------------------------------------------------------------------ tile


def test_tile_hls_requires_region(tmp_path, use_cfg, fake) -> None:
    cfg = _cfg(tmp_path, "hls")
    use_cfg(cfg)
    _touch(cfg.output_dir / "processing" / "x_normalized_cog.tif")
    fake("TilingManager")
    with pytest.raises(SystemExit, match="requires --plan-region"):
        cli.main(["tile"])


def test_tile_hls_applies_overrides_and_tiles_region_mosaic(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "hls")
    use_cfg(cfg)
    _touch(cfg.output_dir / "processing" / "x_normalized_cog.tif")
    mosaic = _touch(cfg.output_dir / "processing" / "hls_mosaic_tokyo_cog.tif").resolve()
    cls = fake("TilingManager")
    patch_cli("_is_valid_mbtiles", lambda path: False)

    argv = ["tile", "--plan-region", "tokyo", "--min-zoom", "3", "--max-zoom", "11",
            "--tile-format", "WEBP", "--quality", "80", "--dry-run"]
    assert cli.main(argv) == 0

    assert (cfg.processing.min_zoom, cfg.processing.max_zoom) == (3, 11)
    assert (cfg.processing.tile_format, cfg.processing.tile_quality) == ("WEBP", 80)
    assert cls.call_args == call(cfg.processing, temp_dir=cfg.temp_dir, output_dir=cfg.output_dir, dry_run=True)
    assert cls.return_value.method_calls == [
        call.create_mbtiles(mosaic, destination=cfg.output_dir / "tiling" / "planet_hls_tokyo_11z.mbtiles")
    ]


def test_tile_bmng_uses_first_normalized_cog(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "bmng")
    use_cfg(cfg)
    first = _touch(cfg.output_dir / "processing" / "a_normalized_cog.tif")
    _touch(cfg.output_dir / "processing" / "b_normalized_cog.tif")
    tiler = fake("TilingManager").return_value
    patch_cli("_is_valid_mbtiles", lambda path: False)

    assert cli.main(["tile"]) == 0
    assert tiler.method_calls == [
        call.create_mbtiles(
            first.resolve(), destination=cfg.output_dir / "tiling" / "planet_2024_10z.mbtiles"
        )
    ]


def test_tile_skips_valid_mbtiles(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "bmng")
    use_cfg(cfg)
    _touch(cfg.output_dir / "processing" / "a_normalized_cog.tif")
    tiler = fake("TilingManager").return_value
    patch_cli("_is_valid_mbtiles", lambda path: True)

    assert cli.main(["tile"]) == 0
    assert tiler.method_calls == []


@pytest.mark.parametrize(
    ("tile_source", "message"),
    [
        ("blend", "not implemented"),
        ("nonsense", "Unsupported tile_source"),
        ("modis", "no modis_"),
        ("viirs", "no viirs_"),
        ("copernicus", "no copernicus_"),
        ("gsi_orthophotos", "no GSI orthophoto"),
    ],
)
def test_tile_source_errors(tmp_path, use_cfg, fake, tile_source, message) -> None:
    cfg = _cfg(tmp_path, tile_source)
    use_cfg(cfg)
    _touch(cfg.output_dir / "processing" / "a_normalized_cog.tif")
    fake("TilingManager")
    with pytest.raises(SystemExit, match=message):
        cli.main(["tile"])


def test_tile_without_processing_dir_is_an_error(tmp_path, use_cfg, fake) -> None:
    use_cfg(_cfg(tmp_path, "bmng"))
    fake("TilingManager")
    with pytest.raises(SystemExit, match="Processing directory not found"):
        cli.main(["tile"])


# --------------------------------------------------------------------- package


def test_package_converts_and_writes_distribution(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "bmng")
    use_cfg(cfg)
    tiling = (cfg.output_dir / "tiling").resolve()
    mbtiles = _touch(tiling / "planet_2024_10z.mbtiles")
    cls = fake("PackagingManager")
    pkg = cls.return_value
    patch_cli("_is_valid_pmtiles", lambda path: False)
    patch_cli("_read_mbtiles_metadata", lambda path: {})

    assert cli.main(["package"]) == 0

    assert cls.call_args == call(dry_run=False)
    assert _names(pkg) == ["convert_to_pmtiles", "generate_tilejson", "create_distribution_package"]
    assert pkg.convert_to_pmtiles.call_args == call(mbtiles, destination=tiling / "planet_2024_10z.pmtiles")

    pmtiles = pkg.convert_to_pmtiles.return_value
    meta = pkg.generate_tilejson.call_args.args[1]
    assert pkg.generate_tilejson.call_args.args[0] is pmtiles
    assert meta.name == "Planetarble 2024"
    assert meta.bounds == (-180.0, -85.0511, 180.0, 85.0511)
    assert (meta.minzoom, meta.maxzoom, meta.format) == (0, 10, cfg.processing.tile_format)
    assert "NASA Blue Marble" in meta.attribution

    dist = pkg.create_distribution_package.call_args
    assert dist.args == (pmtiles,)
    assert dist.kwargs["tilejson_path"] is pkg.generate_tilejson.return_value
    assert dist.kwargs["manifest_path"] == (cfg.output_dir / "MANIFEST.json").resolve()
    assert dist.kwargs["destination"] == cfg.output_dir / "distribution"
    assert "- NASA Blue Marble Next Generation (2004)." in dist.kwargs["license_text"]


@pytest.mark.parametrize(
    ("tile_source", "label"),
    [
        ("modis", "MODIS MCD43A4"),
        ("viirs", "VIIRS Corrected Reflectance"),
        ("copernicus", "Copernicus Sentinel-2 Level-2A"),
        ("sentinel2", "Sentinel-2 L2A (Microsoft Planetary Computer)"),
        ("gsi_orthophotos", "GSI Seamless Orthophoto"),
    ],
)
def test_package_labels_follow_tile_source(patch_cli, tmp_path, use_cfg, fake, monkeypatch, tile_source, label) -> None:
    cfg = _cfg(tmp_path, tile_source)
    use_cfg(cfg)
    _touch(cfg.output_dir / "tiling" / "planet_2024_10z.mbtiles")
    pkg = fake("PackagingManager").return_value
    patch_cli("_is_valid_pmtiles", lambda path: False)
    patch_cli("_read_mbtiles_metadata", lambda path: {"bounds": [1.0, 2.0, 3.0, 4.0]})

    assert cli.main(["package"]) == 0
    meta = pkg.generate_tilejson.call_args.args[1]
    assert label in meta.description
    assert meta.bounds == (1.0, 2.0, 3.0, 4.0)


def test_package_skips_when_outputs_valid(patch_cli, tmp_path, use_cfg, fake, monkeypatch) -> None:
    cfg = _cfg(tmp_path, "bmng")
    use_cfg(cfg)
    _touch(cfg.output_dir / "tiling" / "planet_2024_10z.mbtiles")
    pkg = fake("PackagingManager")
    patch_cli("_is_valid_pmtiles", lambda path: True)
    patch_cli("_is_valid_tilejson", lambda path: True)

    assert cli.main(["package"]) == 0
    assert not pkg.called


def test_package_without_mbtiles_is_an_error(tmp_path, use_cfg, fake) -> None:
    cfg = _cfg(tmp_path, "bmng")
    use_cfg(cfg)
    (cfg.output_dir / "tiling").mkdir(parents=True)
    fake("PackagingManager")
    with pytest.raises(SystemExit, match="No MBTiles archive found"):
        cli.main(["package"])


# ------------------------------------------------------- thin wrappers / tools


def test_merge_mbtiles_delegates(patch_cli, tmp_path, monkeypatch) -> None:
    merge = MagicMock(return_value=tmp_path / "out.mbtiles")
    patch_cli("merge_mbtiles", merge)
    argv = ["tiling", "merge-mbtiles", "--base", str(tmp_path / "b"), "--overlay", str(tmp_path / "o"),
            "--out", str(tmp_path / "out.mbtiles")]
    assert cli.main(argv) == 0
    assert merge.call_args == call(tmp_path / "b", tmp_path / "o", destination=tmp_path / "out.mbtiles")


def test_union_mbtiles_delegates(patch_cli, tmp_path, monkeypatch) -> None:
    union = MagicMock()
    patch_cli("union_mbtiles", union)
    argv = ["tiling", "union-mbtiles", "--inputs", str(tmp_path / "a"), str(tmp_path / "b"),
            "--out", str(tmp_path / "u.mbtiles"), "--chunk-size", "5"]
    assert cli.main(argv) == 0
    assert union.call_args.args == ([tmp_path / "a", tmp_path / "b"], tmp_path / "u.mbtiles")
    assert union.call_args.kwargs["chunk_size"] == 5
    assert callable(union.call_args.kwargs["on_progress"])


def test_stitch_512_delegates(tmp_path, monkeypatch) -> None:
    import planetarble.tiling.mbtiles as mbtiles

    stitch = MagicMock()
    monkeypatch.setattr(mbtiles, "stitch_to_512", stitch)
    argv = ["tiling", "stitch-512", "--source", str(tmp_path / "s"), "--out", str(tmp_path / "o"),
            "--format", "webp", "--quality", "70", "--workers", "2"]
    assert cli.main(argv) == 0
    assert stitch.call_args.args == (tmp_path / "s", tmp_path / "o")
    kwargs = stitch.call_args.kwargs
    assert (kwargs["tile_format"], kwargs["quality"], kwargs["workers"], kwargs["shard_dir"]) == ("webp", 70, 2, None)


def test_gsi_pack_delegates(tmp_path, monkeypatch) -> None:
    import planetarble.tiling.mbtiles as mbtiles

    ingest = MagicMock(return_value=4)
    monkeypatch.setattr(mbtiles, "ingest_xyz_dir", ingest)
    argv = ["gsi-pack", "--tiles", str(tmp_path / "t"), "--out", str(tmp_path / "o.mbtiles"),
            "--name", "N", "--attribution", "A", "--bounds", "1,2,3,4"]
    assert cli.main(argv) == 0
    assert ingest.call_args.args == (tmp_path / "t", tmp_path / "o.mbtiles")
    assert ingest.call_args.kwargs["metadata"] == {"name": "N", "attribution": "A", "bounds": "1,2,3,4"}


@pytest.mark.parametrize(("command", "func", "error"), [
    ("gsi-fetch", "fetch_gsi_ortho_clip", "GSIError"),
    ("mpc-fetch", "fetch_true_color_tile", "MPCError"),
])
def test_fetch_commands_return_1_on_source_error(patch_cli, command, func, error) -> None:
    patch_cli(func, MagicMock(side_effect=getattr(acquisition, error)("down")))
    assert cli.main([command, "--lat", "35", "--lon", "139"]) == 1


@pytest.mark.parametrize(("command", "func"), [
    ("gsi-fetch", "fetch_gsi_ortho_clip"),
    ("mpc-fetch", "fetch_true_color_tile"),
])
def test_fetch_commands_pass_coordinates(patch_cli, command, func) -> None:
    fetch = MagicMock(return_value={"ok": True})
    patch_cli(func, fetch)
    assert cli.main([command, "--lat", "35.5", "--lon", "139.5", "--dry-run"]) == 0
    kwargs = fetch.call_args.kwargs
    assert (kwargs["lat"], kwargs["lon"], kwargs["dry_run"]) == (35.5, 139.5, True)


@pytest.mark.parametrize(("argv", "message"), [
    (["serve"], "must be provided"),
    (["serve", "--pmtiles", "x.pmtiles", "--region", "tokyo"], "mutually exclusive"),
])
def test_serve_argument_validation(argv, message) -> None:
    with pytest.raises(SystemExit, match=message):
        cli.main(argv)


def test_build_wires_spec_executor_and_orchestrator(tmp_path, use_cfg, monkeypatch, capsys) -> None:
    import planetarble.overlay.executor as executor_mod
    import planetarble.overlay.orchestrator as orchestrator_mod

    cfg = _cfg(tmp_path, "bmng")
    use_cfg(cfg)
    spec_path = _touch(tmp_path / "spec.yaml", "base: {source: bmng}\n")
    spec = object()
    monkeypatch.setattr("planetarble.overlay.parse_pipeline_spec", lambda data: spec)
    executor_cls = MagicMock()
    monkeypatch.setattr(executor_mod, "DefaultPlanetExecutor", executor_cls)
    result = SimpleNamespace(planet=tmp_path / "p.mbtiles", base_mbtiles=tmp_path / "b.mbtiles", stacks=[])
    build_planet = MagicMock(return_value=result)
    monkeypatch.setattr(orchestrator_mod, "build_planet", build_planet)

    argv = ["build", "--spec", str(spec_path), "--base-mbtiles", str(tmp_path / "b.mbtiles"), "--no-strict"]
    assert cli.main(argv) == 0

    assert executor_cls.call_args.args == (spec, cfg)
    assert executor_cls.call_args.kwargs["work_dir"] == (cfg.output_dir / "build").resolve()
    assert build_planet.call_args == call(spec, executor_cls.return_value, data_dir=cfg.data_dir, strict=False)
    assert "built planet" in capsys.readouterr().out
