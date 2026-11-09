"""PmtilesTilingManager: format options, bounds, stale outputs, conversion flags.

Complements test_pmtiles_manager.py. External tools are never run: the tile
runner is a stub and gdalinfo is faked through subprocess.run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from planetarble.core.models import ProcessingConfig
from planetarble.tiling.pmtiles import (
    PmtilesMetadata,
    PmtilesTilingManager,
    _replace_resampling,
)


class StubRunner:
    def __init__(self) -> None:
        self.calls: List[Tuple[Tuple[str, ...], str]] = []

    def run(self, command, *, description: str) -> None:
        self.calls.append((tuple(command), description))


def _manager(tmp_path: Path, *, dry_run: bool = False) -> PmtilesTilingManager:
    mgr = PmtilesTilingManager(ProcessingConfig(), temp_dir=tmp_path / "tmp", output_dir=tmp_path / "out",
                               dry_run=dry_run)
    mgr._runner = StubRunner()  # type: ignore[assignment]
    return mgr


def _commands(mgr: PmtilesTilingManager) -> List[Tuple[str, ...]]:
    return [cmd for cmd, _ in mgr._runner.calls]  # type: ignore[attr-defined]


def _build(mgr: PmtilesTilingManager, source: Path, tile_format: str) -> Path:
    return mgr.build_zxy(source, min_zoom=0, max_zoom=3, tile_format=tile_format, quality=70,
                         resampling="cubic")


# --- build_zxy -----------------------------------------------------------------


@pytest.mark.parametrize(("tile_format", "gdal_format", "quality"),
                         [("jpg", "JPEG", True), ("webp", "WEBP", True), ("png", "PNG", False)])
def test_quality_is_passed_only_to_lossy_formats(tmp_path: Path, tile_format: str, gdal_format: str,
                                                 quality: bool) -> None:
    mgr = _manager(tmp_path)
    _build(mgr, tmp_path / "in.tif", tile_format)
    (cmd,) = _commands(mgr)
    assert cmd[cmd.index("-f") + 1] == gdal_format
    assert ("QUALITY=70" in cmd) is quality


def test_unsupported_tile_format_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="gif"):
        _build(_manager(tmp_path), tmp_path / "in.tif", "gif")


def test_previous_pyramid_is_removed_before_tiling(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    stale = tmp_path / "tmp" / "in_0-3_zxy" / "9" / "0" / "0.png"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    zxy = _build(mgr, tmp_path / "in.tif", "png")
    assert zxy.is_dir() and not stale.exists()


def test_dry_run_leaves_the_filesystem_alone(tmp_path: Path) -> None:
    mgr = _manager(tmp_path, dry_run=True)
    zxy = _build(mgr, tmp_path / "in.tif", "png")
    assert not zxy.exists()


def test_replace_resampling_changes_both_kernels_in_place() -> None:
    cmd = ["gdal", "-r", "cubic", "--overview-resampling", "lanczos", "-f", "PNG"]
    _replace_resampling(cmd, "bilinear")
    assert cmd == ["gdal", "-r", "bilinear", "--overview-resampling", "bilinear", "-f", "PNG"]
    trailing = ["gdal", "-r"]
    _replace_resampling(trailing, "bilinear")  # a flag without a value is left alone
    assert trailing == ["gdal", "-r"]


# --- metadata and bounds -------------------------------------------------------


def _fake_gdalinfo(monkeypatch, info: Any) -> List[List[str]]:
    seen: List[List[str]] = []

    def fake_run(command, **kwargs):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(info), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_auto_bounds_come_from_the_raster_corners(tmp_path: Path, monkeypatch) -> None:
    corners = {"upperLeft": {"lon": 139.0, "lat": 36.0}, "upperRight": {"lon": 140.5, "lat": 36.1},
               "lowerRight": {"lon": 140.4, "lat": 35.0}, "lowerLeft": {"lon": 138.9, "lat": 35.1}}
    seen = _fake_gdalinfo(monkeypatch, {"cornerCoordinates": corners})
    bounds = _manager(tmp_path)._determine_bounds(tmp_path / "in.tif", mode="AUTO")
    assert bounds == (138.9, 35.0, 140.5, 36.1)
    assert seen == [["gdalinfo", "-json", str(tmp_path / "in.tif")]]


def test_raster_without_geographic_corners_gets_global_bounds(tmp_path: Path, monkeypatch) -> None:
    # projected rasters report corners as [x, y] lists without lon/lat keys
    _fake_gdalinfo(monkeypatch, {"cornerCoordinates": {"upperLeft": [0.0, 1.0]}})
    assert _manager(tmp_path)._determine_bounds(tmp_path / "in.tif", mode="auto") == (
        -180.0, -85.0511, 180.0, 85.0511)


def test_global_and_dry_run_bounds_skip_gdalinfo(tmp_path: Path, monkeypatch) -> None:
    seen = _fake_gdalinfo(monkeypatch, {})
    world = (-180.0, -85.0511, 180.0, 85.0511)
    assert _manager(tmp_path)._determine_bounds(tmp_path / "in.tif", mode="global") == world
    assert _manager(tmp_path, dry_run=True)._determine_bounds(tmp_path / "in.tif", mode="auto") == world
    assert seen == []


def test_unknown_bounds_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tight"):
        _manager(tmp_path)._determine_bounds(tmp_path / "in.tif", mode="tight")


def test_metadata_centre_is_the_bounds_middle_at_the_middle_zoom(tmp_path: Path) -> None:
    meta = _manager(tmp_path)._build_metadata(tmp_path / "in.tif", tile_format="jpeg", min_zoom=2, max_zoom=9,
                                              name="n", attribution="a", bounds_mode="global")
    assert meta.format == "jpg"
    assert meta.center == (0.0, 0.0, 5)
    assert meta.to_json()["center"] == "0.000000,0.000000,5"


def test_metadata_json_is_all_strings() -> None:
    meta = PmtilesMetadata(name="n", format="png", minzoom=0, maxzoom=4, bounds=(1, 2, 3, 4.5),
                           center=(2, 3.25, 2), attribution="(c) x")
    assert meta.to_json() == {"name": "n", "format": "png", "minzoom": "0", "maxzoom": "4",
                              "bounds": "1.000000,2.000000,3.000000,4.500000",
                              "center": "2.000000,3.250000,2", "attribution": "(c) x"}


def test_pack_replaces_a_stale_mbtiles(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    stale = tmp_path / "tmp" / "in_0-3.mbtiles"
    stale.write_bytes(b"old")
    zxy = tmp_path / "zxy"
    zxy.mkdir()
    out = mgr.pack_mbtiles(zxy, source_path=tmp_path / "in.tif", tile_format="webp", min_zoom=0, max_zoom=3,
                           name="n", attribution="a", bounds_mode="global")
    assert out == stale and not stale.exists()  # mb-util (stubbed) writes it afresh
    assert _commands(mgr)[0][-1] == "--image_format=webp"


# --- PMTiles ---------------------------------------------------------------------


def test_convert_options_and_stale_output(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    dest = tmp_path / "dist" / "planet.pmtiles"
    dest.parent.mkdir()
    dest.write_bytes(b"old")
    result = mgr.convert_pmtiles(tmp_path / "in.mbtiles", destination=dest, deduplicate=False, cluster=True)
    assert result == dest and not dest.exists()
    assert _commands(mgr) == [
        ("pmtiles", "convert", str(tmp_path / "in.mbtiles"), str(dest), "--no-deduplication"),
        ("pmtiles", "cluster", str(dest)),
    ]


def test_show_header_in_a_dry_run_is_empty(tmp_path: Path, monkeypatch) -> None:
    seen = _fake_gdalinfo(monkeypatch, {})
    assert _manager(tmp_path, dry_run=True).show_header(tmp_path / "x.pmtiles") == {}
    assert seen == []
