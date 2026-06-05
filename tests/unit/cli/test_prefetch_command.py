"""CLI test for the prefetch subcommand (dry-run lists S2 overlays, no network)."""

import importlib
from pathlib import Path

cli_main = importlib.import_module("planetarble.cli.main")


def _write_min_config(tmp_path: Path) -> Path:
    p = tmp_path / "pipeline.yaml"
    p.write_text("\n".join([f"data_dir: {tmp_path/'data'}", "output_dir: output"]), encoding="utf-8")
    return p


def _write_spec(tmp_path: Path) -> Path:
    p = tmp_path / "spec.yaml"
    p.write_text(
        "\n".join(
            [
                "base: {source: bmng, resolution: '500m', max_zoom: 7}",
                "overlays:",
                "  - {name: osaka_s2, source: sentinel2, aoi: {bbox: [135.4,34.6,135.6,34.8], land_only: true}, min_zoom: 8, max_zoom: 14}",
                "  - {name: city_oam, source: openaerialmap, aoi: {bbox: [135.4,34.6,135.6,34.8]}, min_zoom: 8, max_zoom: 18}",
                "output: {name: x}",
            ]
        ),
        encoding="utf-8",
    )
    return p


def test_prefetch_dry_run_lists_sentinel2_overlays(tmp_path: Path, capsys) -> None:
    cfg = _write_min_config(tmp_path)
    spec = _write_spec(tmp_path)
    code = cli_main.main(["prefetch", "--config", str(cfg), "--spec", str(spec), "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "osaka_s2" in out          # the sentinel2 overlay is listed
    assert "city_oam" not in out      # the non-sentinel2 overlay is not
