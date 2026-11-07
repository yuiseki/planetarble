"""Artefact checks the CLI uses to decide whether a stage can be skipped."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from planetarble.cli import checks
from planetarble.config import PipelineConfig
from planetarble.core.models import GSIOrthophotoConfig, HLSConfig, Sentinel2Config


def _mbtiles(path: Path, *, tiles: list[tuple[int, int, int]], metadata: dict[str, str] | None = None) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    conn.executemany("INSERT INTO tiles VALUES (?,?,?,x'00')", tiles)
    if metadata is not None:
        conn.execute("CREATE TABLE metadata (name TEXT, value TEXT)")
        conn.executemany("INSERT INTO metadata VALUES (?,?)", metadata.items())
    conn.commit()
    conn.close()
    return path


# ------------------------------------------------------------------ MBTiles


def test_mbtiles_validity(tmp_path: Path) -> None:
    assert checks._is_valid_mbtiles(tmp_path / "missing.mbtiles") is False
    assert checks._is_valid_mbtiles(_mbtiles(tmp_path / "empty.mbtiles", tiles=[])) is False
    assert checks._is_valid_mbtiles(_mbtiles(tmp_path / "ok.mbtiles", tiles=[(0, 0, 0)])) is True
    no_tiles = tmp_path / "other.mbtiles"
    sqlite3.connect(no_tiles).execute("CREATE TABLE x (a)").connection.commit()
    assert checks._is_valid_mbtiles(no_tiles) is False


def test_mbtiles_metadata_parses_bounds_center_and_zooms(tmp_path: Path) -> None:
    path = _mbtiles(
        tmp_path / "m.mbtiles",
        tiles=[(3, 0, 0), (7, 1, 1)],
        metadata={"bounds": "139.0, 35.0, 140.0, 36.0", "center": "139.5,35.5,9", "format": "webp"},
    )
    meta = checks._read_mbtiles_metadata(path)
    assert meta["bounds"] == [139.0, 35.0, 140.0, 36.0]
    assert meta["center"] == [139.5, 35.5, 9]
    assert (meta["minzoom"], meta["maxzoom"], meta["format"]) == (3, 7, "webp")


def test_mbtiles_metadata_prefers_declared_zooms_and_tolerates_junk(tmp_path: Path) -> None:
    path = _mbtiles(
        tmp_path / "m.mbtiles",
        tiles=[(3, 0, 0)],
        metadata={"minzoom": "0", "maxzoom": "12", "bounds": "a,b,c,d", "center": "1,2"},
    )
    meta = checks._read_mbtiles_metadata(path)
    assert (meta["minzoom"], meta["maxzoom"]) == (0, 12)
    assert meta["bounds"] == "a,b,c,d" and meta["center"] == "1,2"  # left as-is
    assert checks._read_mbtiles_metadata(tmp_path / "missing.mbtiles") == {}


def test_mbtiles_metadata_without_metadata_table(tmp_path: Path) -> None:
    meta = checks._read_mbtiles_metadata(_mbtiles(tmp_path / "m.mbtiles", tiles=[(2, 0, 0), (4, 0, 0)]))
    assert meta == {"minzoom": 2, "maxzoom": 4}


# ------------------------------------------------------ PMTiles / TileJSON


def test_pmtiles_validity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "p.pmtiles"
    assert checks._is_valid_pmtiles(path) is False
    path.write_bytes(b"PMTiles")

    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    assert checks._is_valid_pmtiles(path) is True  # no CLI: trust a non-empty file

    monkeypatch.setattr(checks.shutil, "which", lambda name: "/bin/pmtiles")
    calls: list[list[str]] = []

    def verify_ok(cmd, check, capture_output):
        calls.append(cmd)

    monkeypatch.setattr(checks.subprocess, "run", verify_ok)
    assert checks._is_valid_pmtiles(path) is True
    assert calls == [["/bin/pmtiles", "verify", str(path)]]

    def verify_fail(cmd, check, capture_output):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(checks.subprocess, "run", verify_fail)
    assert checks._is_valid_pmtiles(path) is False


def test_tilejson_validity(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    assert checks._is_valid_tilejson(path) is False
    path.write_text("not json", encoding="utf-8")
    assert checks._is_valid_tilejson(path) is False
    path.write_text(json.dumps({"tilejson": "3.0.0", "tiles": [], "minzoom": 0, "maxzoom": 1}), encoding="utf-8")
    assert checks._is_valid_tilejson(path) is False  # no bounds
    path.write_text(
        json.dumps({"tilejson": "3.0.0", "tiles": [], "minzoom": 0, "maxzoom": 1, "bounds": [0, 0, 1, 1]}),
        encoding="utf-8",
    )
    assert checks._is_valid_tilejson(path) is True


# ------------------------------------------------------- scene manifests


@pytest.mark.parametrize(
    ("payload", "valid"),
    [
        ({"scenes": [{"id": 1}], "summary": {}}, True),
        ({"scenes": [], "summary": {}}, False),
        ({"scenes": [{"id": 1}]}, False),
        ({"summary": {}}, False),
    ],
)
def test_hls_scene_manifest_validity(tmp_path: Path, payload: dict, valid: bool) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert checks._is_valid_hls_scene_manifest(path) is valid


def test_hls_scene_manifest_rejects_missing_and_garbage(tmp_path: Path) -> None:
    assert checks._is_valid_hls_scene_manifest(tmp_path / "missing.json") is False
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert checks._is_valid_hls_scene_manifest(tmp_path / "bad.json") is False


def test_sentinel2_manifest_requires_a_scene_with_all_assets(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps({"scenes": [{"assets": {"B02": "a", "B03": "b"}}, {"assets": {"visual": "v"}}], "summary": {}}),
        encoding="utf-8",
    )
    assert checks._is_valid_sentinel2_scene_manifest(path) is True
    assert checks._is_valid_sentinel2_scene_manifest(path, required_assets=["VISUAL"]) is True
    assert checks._is_valid_sentinel2_scene_manifest(path, required_assets=["B02", "B03", "B04"]) is False


# ------------------------------------------------------ path resolution


def test_resolve_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("{}", encoding="utf-8")
    assert checks._resolve_config_path(cfg) == cfg.resolve()
    with pytest.raises(SystemExit, match="Configuration file not found"):
        checks._resolve_config_path(tmp_path / "nope.yaml")

    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="No configuration file found"):
        checks._resolve_config_path(None)
    default = tmp_path / "configs" / "base" / "pipeline.yaml"
    default.parent.mkdir(parents=True)
    default.write_text("{}", encoding="utf-8")
    assert checks._resolve_config_path(None) == default.resolve()


def test_region_scoped_paths(tmp_path: Path) -> None:
    cfg = PipelineConfig(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "out",
        hls=HLSConfig(target_zoom=11, plan_region="kanto"),
        sentinel2=Sentinel2Config(plan_region=None),
    )
    assert checks._resolve_hls_plan_path(cfg, None) == tmp_path / "data" / "plans" / "hls_z11_plan_kanto.ndjson"
    assert checks._resolve_hls_plan_path(cfg, "tokyo").name == "hls_z11_plan_tokyo.ndjson"
    assert checks._resolve_hls_scene_manifest_path(cfg, None).name == "hls_scene_manifest_kanto.json"
    assert checks._resolve_sentinel2_scene_manifest_path(cfg, None).name == "sentinel2_scene_manifest.json"
    assert checks._resolve_sentinel2_scene_manifest_path(cfg, "tokyo").name == "sentinel2_scene_manifest_tokyo.json"
    cfg.hls = HLSConfig(target_zoom=10)
    assert checks._resolve_hls_plan_path(cfg, None).name == "hls_z10_plan.ndjson"
    assert checks._resolve_hls_scene_manifest_path(cfg, None).name == "hls_scene_manifest.json"


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (GSIOrthophotoConfig(tile_template="https://x/{z}/{x}/{y}.png"), "https://x/{z}/{x}/{y}.png"),
        (GSIOrthophotoConfig(tile_template="", product="seamlessphoto"),
         "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"),
        (GSIOrthophotoConfig(tile_template="", product="ORTHOPHOTO"),
         "https://cyberjapandata.gsi.go.jp/xyz/ort/{z}/{x}/{y}.jpg"),
    ],
)
def test_gsi_tile_template(config: GSIOrthophotoConfig, expected: str) -> None:
    assert checks._resolve_gsi_tile_template(config) == expected


def test_gsi_tile_template_rejects_unknown_product() -> None:
    with pytest.raises(SystemExit, match="Unsupported gsi_orthophotos.product"):
        checks._resolve_gsi_tile_template(GSIOrthophotoConfig(tile_template="", product="aerial"))
