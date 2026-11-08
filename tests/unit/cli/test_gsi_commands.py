"""`gsi-collect`, `gsi-pack` and `gsi-fetch`: catalog filtering, destinations,
metadata and exit codes. Downloads are faked; packing uses real SQLite."""

from __future__ import annotations

import gzip
import importlib
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest

from planetarble.acquisition import GSIError
from planetarble.acquisition.tiles import TileDownloadStats
from planetarble.config import PipelineConfig

cli = importlib.import_module("planetarble.cli.main")

# z/x/y.ext,mtime,size,md5 -- Tokyo tiles at z8..z10 plus one far west (Atlantic) tile
MOKUROKU = "\n".join([
    "8/227/100.jpg,1700000000,1000,aa",
    "9/454/201.jpg,1700000000,2000,bb",
    "10/909/403.jpg,1700000000,3000,cc",
    "10/910/403.jpg,1700000000,4000,dd",
    "10/300/400.jpg,1700000000,5000,ee",
]) + "\n"


@pytest.fixture
def mokuroku(tmp_path: Path) -> Path:
    path = tmp_path / "mokuroku.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(MOKUROKU)
    return path


@pytest.fixture
def use_cfg(patch_cli, tmp_path: Path) -> PipelineConfig:
    cfg = PipelineConfig(data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", output_dir=tmp_path / "out")
    patch_cli("_resolve_config_path", lambda path: Path("pipeline.yaml"))
    patch_cli("load_config", lambda path: cfg)
    return cfg


def test_collect_dry_run_counts_tiles_per_zoom(mokuroku: Path, use_cfg, capsys) -> None:
    code = cli.main(["gsi-collect", "--mokuroku", str(mokuroku), "--zoom-min", "9", "--zoom-max", "10", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "= 4 tiles, 0.0 GB" in out
    assert "  z9: 1 tiles" in out and "  z10: 3 tiles" in out
    assert "z8" not in out.split("\n", 1)[1]


def test_collect_dry_run_can_be_limited_to_a_quadrans(mokuroku: Path, use_cfg, capsys) -> None:
    from planetarble.tiling.quadrans import quadrans_of_tile

    east = quadrans_of_tile(10, 909, 403)
    assert quadrans_of_tile(10, 300, 400) != east  # the Atlantic tile is in another region
    cli.main(["gsi-collect", "--mokuroku", str(mokuroku), "--zoom-min", "10", "--zoom-max", "10",
              "--quadrans", east, "--dry-run"])
    assert "= 2 tiles" in capsys.readouterr().out


def test_collect_needs_a_destination(mokuroku: Path, use_cfg, capsys) -> None:
    assert cli.main(["gsi-collect", "--mokuroku", str(mokuroku)]) == 2
    assert "--out" in capsys.readouterr().out


@pytest.fixture
def fake_downloads(monkeypatch) -> Dict[str, Any]:
    import planetarble.acquisition.tiles as tiles_module
    import planetarble.tiling.mbtiles as mbtiles_module

    calls: Dict[str, Any] = {"stats": TileDownloadStats(ok=3)}

    def to_dir(triplets, *, out_dir, template, ext, workers, on_progress, report_every):
        calls["dir"] = {"tiles": list(triplets), "out_dir": out_dir, "template": template, "workers": workers}
        on_progress(calls["stats"])
        return calls["stats"]

    def to_mbtiles(triplets, *, mbtiles_path, template, ext, tile_format, workers, metadata, on_progress,
                   report_every):
        calls["mbtiles"] = {"tiles": list(triplets), "path": mbtiles_path, "metadata": metadata}
        return calls["stats"]

    monkeypatch.setattr(tiles_module, "download_xyz_tiles", to_dir)
    monkeypatch.setattr(mbtiles_module, "download_xyz_to_mbtiles", to_mbtiles)
    return calls


def test_collect_into_a_zxy_directory(tmp_path: Path, mokuroku: Path, use_cfg, fake_downloads, capsys) -> None:
    code = cli.main(["gsi-collect", "--mokuroku", str(mokuroku), "--layer", "ort", "--zoom-min", "10",
                     "--zoom-max", "10", "--out", str(tmp_path / "tiles"), "--workers", "4"])
    assert code == 0
    call = fake_downloads["dir"]
    assert call["tiles"] == [(10, 909, 403), (10, 910, 403), (10, 300, 400)]
    assert call["template"] == "https://cyberjapandata.gsi.go.jp/xyz/ort/{z}/{x}/{y}.jpg"
    assert call["out_dir"] == (tmp_path / "tiles").resolve()
    assert call["workers"] == 4
    assert "gsi-collect done" in capsys.readouterr().out


def test_collect_into_mbtiles_with_metadata(tmp_path: Path, mokuroku: Path, use_cfg, fake_downloads) -> None:
    cli.main(["gsi-collect", "--mokuroku", str(mokuroku), "--zoom-min", "8", "--zoom-max", "8",
              "--mbtiles", str(tmp_path / "gsi.mbtiles"), "--name", "GSI", "--attribution", "国土地理院"])
    call = fake_downloads["mbtiles"]
    assert call["tiles"] == [(8, 227, 100)]
    assert call["metadata"] == {"name": "GSI", "attribution": "国土地理院"}


def test_collect_exits_non_zero_when_tiles_failed(tmp_path: Path, mokuroku: Path, use_cfg, fake_downloads) -> None:
    fake_downloads["stats"] = TileDownloadStats(ok=1, failed=1)
    assert cli.main(["gsi-collect", "--mokuroku", str(mokuroku), "--out", str(tmp_path / "t")]) == 1


def test_collect_downloads_the_catalog_into_the_cache(tmp_path: Path, mokuroku: Path, use_cfg, monkeypatch,
                                                       capsys) -> None:
    import planetarble.acquisition.mokuroku as mokuroku_module

    fetched: List[Any] = []

    def fake_fetch(url, dest):
        fetched.append((url, dest))
        return mokuroku

    monkeypatch.setattr(mokuroku_module, "fetch_mokuroku", fake_fetch)
    cli.main(["gsi-collect", "--layer", "ort", "--dry-run"])
    assert fetched == [("https://cyberjapandata.gsi.go.jp/xyz/ort/mokuroku.csv.gz",
                        (use_cfg.data_dir / "cache" / "mokuroku" / "ort.csv.gz").resolve())]


def test_pack_ingests_a_zxy_directory_with_metadata(tmp_path: Path, capsys) -> None:
    for z, x, y in [(1, 0, 0), (1, 1, 1)]:
        path = tmp_path / "tiles" / str(z) / str(x) / f"{y}.jpg"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"jpg")
    out = tmp_path / "gsi.mbtiles"

    assert cli.main(["gsi-pack", "--tiles", str(tmp_path / "tiles"), "--out", str(out), "--name", "GSI",
                     "--attribution", "GSI", "--bounds", "122,20,154,46"]) == 0

    conn = sqlite3.connect(out)
    try:
        assert conn.execute("SELECT count(*) FROM tiles").fetchone() == (2,)
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
    finally:
        conn.close()
    assert meta["name"] == "GSI" and meta["attribution"] == "GSI" and meta["bounds"] == "122,20,154,46"
    assert "gsi-pack done: 2 tiles" in capsys.readouterr().out


def test_fetch_passes_options_and_prints_dry_runs(patch_cli, capsys) -> None:
    seen: Dict[str, Any] = {}

    def fake_fetch(**kwargs):
        seen.update(kwargs)
        return {"tiles": 4}

    patch_cli("fetch_gsi_ortho_clip", fake_fetch)
    assert cli.main(["gsi-fetch", "--lat", "35.68", "--lon", "139.76", "--zoom", "17", "--dry-run"]) == 0
    assert (seen["lat"], seen["lon"], seen["zoom"], seen["dry_run"]) == (35.68, 139.76, 17, True)
    assert seen["width_m"] == seen["height_m"] == 400.0
    assert "{'tiles': 4}" in capsys.readouterr().out


def test_fetch_failure_exits_one(patch_cli) -> None:
    def failing(**kwargs):
        raise GSIError("404")

    patch_cli("fetch_gsi_ortho_clip", failing)
    assert cli.main(["gsi-fetch", "--lat", "35", "--lon", "139"]) == 1
