"""Characterise TilingManager.create_mbtiles (the `planetarble tile` stage).

TileRunner.run is replaced with a recorder and the command lines are compared
with golden files (tmp paths masked). Which tiler runs (gdal2mbtiles via
pyvips, or gdalwarp + gdal_translate + gdaladdo) depends on configuration and
on what the host has installed, so both are stubbed explicitly.

Regenerate goldens with PLANETARBLE_UPDATE_GOLDEN=1 for an intended change.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import planetarble.tiling.manager as tm
from planetarble.core.models import ProcessingConfig

GOLDEN_DIR = Path(__file__).parent / "golden"
Commands = list[tuple[str, list[str]]]


def assert_golden(name: str, recorded: Commands, tmp_path: Path) -> None:
    actual = json.loads(json.dumps(recorded).replace(str(tmp_path), "<tmp>"))
    path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("PLANETARBLE_UPDATE_GOLDEN") == "1":
        GOLDEN_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
        return
    if not path.exists():
        pytest.fail(f"golden file missing: {path} (run with PLANETARBLE_UPDATE_GOLDEN=1)")
    assert actual == json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> Commands:
    recorded: Commands = []

    def fake_run(self, command, *, description: str) -> None:
        recorded.append((description, [str(part) for part in command]))

    monkeypatch.setattr(tm.TileRunner, "run", fake_run)
    return recorded


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch):
    """Control what the host offers: gdal2mbtiles on PATH and a healthy libvips."""

    def _host(*, gdal2mbtiles: bool, libvips: bool = True) -> None:
        monkeypatch.setattr(
            tm.TilingManager, "_resolve_gdal2mbtiles", lambda self: ["gdal2mbtiles"] if gdal2mbtiles else None
        )
        monkeypatch.setattr(tm.TilingManager, "_libvips_healthy", lambda self: libvips)

    return _host


def _manager(tmp_path: Path, *, dry_run: bool = False, **overrides) -> tm.TilingManager:
    config = replace(ProcessingConfig(min_zoom=0, max_zoom=3), **overrides)
    return tm.TilingManager(config, temp_dir=tmp_path / "tmp", output_dir=tmp_path / "output", dry_run=dry_run)


def test_gdal_translate_pipeline(tmp_path, commands, host) -> None:
    host(gdal2mbtiles=False)
    out = _manager(tmp_path).create_mbtiles(tmp_path / "mosaic_cog.tif")
    assert out == tmp_path / "output" / "tiling" / "planet_2024_3z.mbtiles"
    assert_golden("create_mbtiles_gdal_translate", commands, tmp_path)


def test_gdal2mbtiles_when_preferred(tmp_path, commands, host) -> None:
    host(gdal2mbtiles=True)
    dest = tmp_path / "out.mbtiles"
    _manager(tmp_path, mbtiles_tiler="gdal2mbtiles", tile_format="JPEG").create_mbtiles(
        tmp_path / "mosaic_cog.tif", destination=dest
    )
    assert_golden("create_mbtiles_gdal2mbtiles", commands, tmp_path)


def test_preferred_gdal2mbtiles_missing_is_an_error(tmp_path, commands, host) -> None:
    host(gdal2mbtiles=False)
    with pytest.raises(tm.TileCommandError, match="gdal2mbtiles requested"):
        _manager(tmp_path, mbtiles_tiler="pyvips").create_mbtiles(tmp_path / "src.tif")
    assert commands == []


@pytest.mark.parametrize(
    ("tile_format", "available", "libvips", "dry_run", "expected_first"),
    [
        ("JPEG", True, True, False, "gdal2mbtiles"),   # auto: JPEG goes through pyvips
        ("WEBP", True, True, False, "gdalwarp"),       # auto: other formats use GDAL
        ("JPEG", True, False, False, "gdalwarp"),      # libvips broken
        ("JPEG", False, True, False, "gdalwarp"),      # not installed
        ("JPEG", True, True, True, "gdalwarp"),        # dry run never uses gdal2mbtiles
    ],
)
def test_auto_tiler_selection(tmp_path, commands, host, tile_format, available, libvips, dry_run, expected_first) -> None:
    host(gdal2mbtiles=available, libvips=libvips)
    _manager(tmp_path, dry_run=dry_run, tile_format=tile_format).create_mbtiles(tmp_path / "src.tif")
    assert commands[0][1][0] == expected_first


def test_auto_falls_back_to_gdal_when_gdal2mbtiles_fails(tmp_path, monkeypatch, host) -> None:
    host(gdal2mbtiles=True)
    recorded: list[str] = []

    def flaky_run(self, command, *, description: str) -> None:
        recorded.append(command[0])
        if command[0] == "gdal2mbtiles":
            raise tm.TileCommandError("boom")

    monkeypatch.setattr(tm.TileRunner, "run", flaky_run)
    _manager(tmp_path, tile_format="JPEG").create_mbtiles(tmp_path / "src.tif")
    assert recorded == ["gdal2mbtiles", "gdalwarp", "gdal_translate", "gdaladdo"]


def test_preferred_gdal2mbtiles_failure_propagates(tmp_path, monkeypatch, host) -> None:
    host(gdal2mbtiles=True)

    def failing_run(self, command, *, description: str) -> None:
        raise tm.TileCommandError("boom")

    monkeypatch.setattr(tm.TileRunner, "run", failing_run)
    with pytest.raises(tm.TileCommandError, match="boom"):
        _manager(tmp_path, mbtiles_tiler="gdal2mbtiles").create_mbtiles(tmp_path / "src.tif")


def test_existing_output_is_replaced_but_not_in_dry_run(tmp_path, commands, host) -> None:
    host(gdal2mbtiles=False)
    dest = tmp_path / "out.mbtiles"
    dest.write_bytes(b"old")
    _manager(tmp_path, dry_run=True).create_mbtiles(tmp_path / "src.tif", destination=dest)
    assert dest.exists()
    _manager(tmp_path).create_mbtiles(tmp_path / "src.tif", destination=dest)
    assert not dest.exists()  # removed before regeneration (the recorder writes nothing)


def test_unknown_options_fall_back(tmp_path, commands, host) -> None:
    host(gdal2mbtiles=False)
    _manager(tmp_path, zoom_level_strategy="sideways", resampling="bogus").create_mbtiles(tmp_path / "src.tif")
    translate = next(cmd for _, cmd in commands if cmd[0] == "gdal_translate")
    addo = next(cmd for _, cmd in commands if cmd[0] == "gdaladdo")
    assert "ZOOM_LEVEL_STRATEGY=LOWER" in translate
    assert addo[1:3] == ["-r", "cubic"]


def test_no_overviews_for_a_single_zoom(tmp_path, commands, host) -> None:
    host(gdal2mbtiles=False)
    _manager(tmp_path, min_zoom=5, max_zoom=5).create_mbtiles(tmp_path / "src.tif")
    assert [cmd[0] for _, cmd in commands] == ["gdalwarp", "gdal_translate"]


@pytest.mark.parametrize(("zoom", "metres"), [(0, 156543.03392804097), (10, 152.8740565703525), (-3, 156543.03392804097)])
def test_webmercator_resolution(zoom: int, metres: float) -> None:
    assert tm._webmercator_resolution(zoom) == pytest.approx(metres)
