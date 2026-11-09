"""`copernicus-layers` and `mpc-fetch`: output, options and exit codes."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Dict

import pytest

from planetarble.acquisition import CopernicusCredentialsMissing, MPCError
from planetarble.acquisition.copernicus import (
    CopernicusAccessError,
    CopernicusAuthError,
)

cli = importlib.import_module("planetarble.cli.main")


def test_layers_are_printed_one_per_line(patch_cli, capsys) -> None:
    seen: Dict[str, Any] = {}

    def fake_layers(**kwargs):
        seen.update(kwargs)
        return [("TRUE_COLOR", "True color"), ("NDVI", "Vegetation index")]

    patch_cli("get_available_layers", fake_layers)
    assert cli.main(["copernicus-layers", "--instance-id", "abc", "--no-credentials"]) == 0
    assert capsys.readouterr().out == "TRUE_COLOR\tTrue color\nNDVI\tVegetation index\n"
    assert seen == {"instance_id": "abc", "use_credentials": False}


def test_no_layers_is_not_an_error(patch_cli, capsys) -> None:
    patch_cli("get_available_layers", lambda **kwargs: [])
    assert cli.main(["copernicus-layers"]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "error", [CopernicusCredentialsMissing("set COPERNICUS_INSTANCE_ID"), CopernicusAuthError("401"),
              CopernicusAccessError("503")],
)
def test_layer_listing_failures_exit_one(patch_cli, error: Exception) -> None:
    def failing(**kwargs):
        raise error

    patch_cli("get_available_layers", failing)
    assert cli.main(["copernicus-layers"]) == 1


def test_mpc_fetch_passes_every_option(patch_cli, tmp_path: Path, capsys) -> None:
    seen: Dict[str, Any] = {}

    def fake_fetch(**kwargs):
        seen.update(kwargs)
        return {"item": "S2A"}

    patch_cli("fetch_true_color_tile", fake_fetch)
    out = tmp_path / "clip.tif"
    assert cli.main(["mpc-fetch", "--lat", "35.6", "--lon", "139.7", "--width-m", "100", "--height-m", "200",
                     "--max-cloud", "15", "--start", "2024-01-01", "--end", "2024-02-01",
                     "--output", str(out), "--gdal-translate", "gt"]) == 0
    assert seen == {"lat": 35.6, "lon": 139.7, "width_m": 100.0, "height_m": 200.0, "output_path": out,
                    "max_cloud": 15.0, "start_datetime": "2024-01-01", "end_datetime": "2024-02-01",
                    "gdal_translate": "gt", "dry_run": False}
    assert capsys.readouterr().out == ""  # the summary is printed only for dry runs


def test_mpc_fetch_dry_run_prints_the_summary(patch_cli, capsys) -> None:
    patch_cli("fetch_true_color_tile", lambda **kwargs: {"item": "S2A"})
    assert cli.main(["mpc-fetch", "--lat", "0", "--lon", "0", "--dry-run"]) == 0
    assert "{'item': 'S2A'}" in capsys.readouterr().out


def test_mpc_fetch_failure_exits_one(patch_cli) -> None:
    def failing(**kwargs):
        raise MPCError("no scene")

    patch_cli("fetch_true_color_tile", failing)
    assert cli.main(["mpc-fetch", "--lat", "0", "--lon", "0"]) == 1
