from pathlib import Path

import importlib

import pytest

from planetarble.config import PipelineConfig
from planetarble.core.models import HLSConfig, HLSPlanRegion, NaturalEarthRegion, OceanConfig, ProcessingConfig

cli_main = importlib.import_module("planetarble.cli.main")


def test_acquire_plan_region_invokes_region_planner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text("processing:\n  tile_source: hls\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "output"
    temp_dir = tmp_path / "tmp"

    cfg = PipelineConfig(
        data_dir=data_dir,
        temp_dir=temp_dir,
        output_dir=output_dir,
        processing=ProcessingConfig(tile_source="hls"),
        hls=HLSConfig(
            plan_regions=(
                HLSPlanRegion(
                    name="tokyo_land",
                    natural_earth=NaturalEarthRegion(
                        dataset="admin_1",
                        where="adm0_a3='JPN' AND name='Tokyo'",
                    ),
                    land_only=True,
                ),
            )
        ),
        ocean=OceanConfig(enabled=False),
    )

    called: dict[str, object] = {}

    class StubManager:
        def __init__(self, data_directory, manifest_path, use_aria2):  # type: ignore[no-untyped-def]
            called["init"] = True

        def download_natural_earth(self, *, force, include_admin):  # type: ignore[no-untyped-def]
            called["download_ne"] = {"force": force, "include_admin": include_admin}
            return data_dir / "natural_earth"

        def build_hls_plans(self, config, *, force, selected_region):  # type: ignore[no-untyped-def]
            called["build_hls_plans"] = {"force": force, "selected_region": selected_region}
            summary = type("Summary", (), {"path": data_dir / "plans" / "hls_z10_plan_tokyo_land.ndjson", "zoom": 10, "tile_count": 1, "season_counts": {}})()
            return {"tokyo_land": summary}

        def generate_manifest(self, generation_params=None, version="1.0"):  # type: ignore[no-untyped-def]
            called["manifest"] = generation_params or {}

    monkeypatch.setattr(cli_main, "AcquisitionManager", StubManager)
    monkeypatch.setattr(cli_main, "load_config", lambda _: cfg)

    exit_code = cli_main.main(["acquire", "--config", str(config_path), "--plan-region", "tokyo_land"])

    assert exit_code == 0
    assert called["download_ne"]["include_admin"] is True
    assert called["build_hls_plans"]["selected_region"] == "tokyo_land"


def test_process_plan_region_uses_region_plan_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "pipeline.yaml"
    config_path.write_text("processing:\n  tile_source: hls\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "output"
    temp_dir = tmp_path / "tmp"
    plan_dir = data_dir / "plans"
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "hls_z10_plan_tokyo_land.ndjson"
    plan_path.write_text("{}", encoding="utf-8")

    cfg = PipelineConfig(
        data_dir=data_dir,
        temp_dir=temp_dir,
        output_dir=output_dir,
        processing=ProcessingConfig(tile_source="hls"),
        hls=HLSConfig(plan_region="tokyo_land"),
        ocean=OceanConfig(enabled=False),
    )

    called: dict[str, object] = {}

    class StubProcessor:
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            pass

        def prepare_hls_scene_manifest(self, plan_path, *, destination=None, **kwargs):  # type: ignore[no-untyped-def]
            called["plan_path"] = str(plan_path)
            called["destination"] = str(destination)
            return destination

        def build_hls_mosaic(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            called["build_hls_mosaic"] = True
            return None

    monkeypatch.setattr(cli_main, "ProcessingManager", StubProcessor)
    monkeypatch.setattr(cli_main, "load_config", lambda _: cfg)

    exit_code = cli_main.main(["process", "--config", str(config_path), "--plan-region", "tokyo_land"])

    assert exit_code == 0
    assert called["plan_path"].endswith("hls_z10_plan_tokyo_land.ndjson")
    assert called["destination"].endswith("hls_scene_manifest_tokyo_land.json")
