"""PackagingManager: MBTiles -> PMTiles, TileJSON, distribution bundle."""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import pytest

import planetarble.packaging.manager as pkg
from planetarble.core.models import TileMetadata

META = TileMetadata(
    name="Planetarble 2024",
    description="test",
    version="2024",
    bounds=(-180.0, -85.0511, 180.0, 85.0511),
    center=(0.0, 0.0, 2),
    minzoom=0,
    maxzoom=8,
    attribution="Imagery: test",
    format="WEBP",
)


@pytest.fixture
def ran(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(pkg.shutil, "which", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(pkg.subprocess, "run", lambda cmd, check: calls.append(list(cmd)))
    return calls


def test_convert_to_pmtiles(tmp_path: Path, ran) -> None:
    out = pkg.PackagingManager().convert_to_pmtiles(tmp_path / "planet.mbtiles")
    assert out == tmp_path / "planet.pmtiles"
    assert ran == [["/usr/local/bin/pmtiles", "convert", str(tmp_path / "planet.mbtiles"), str(out)]]


def test_convert_dry_run_runs_nothing(tmp_path: Path, ran) -> None:
    dest = tmp_path / "x.pmtiles"
    assert pkg.PackagingManager(dry_run=True).convert_to_pmtiles(tmp_path / "a.mbtiles", dest) == dest
    assert ran == []


def test_generate_tilejson(tmp_path: Path, ran) -> None:
    pmtiles = tmp_path / "planet.pmtiles"
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        path = pkg.PackagingManager().generate_tilejson(pmtiles, META)
    assert path == tmp_path / "planet.tilejson.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    created = data.pop("created_at")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z", created)
    assert data == {
        "tilejson": "3.0.0",
        "name": "Planetarble 2024",
        "description": "test",
        "version": "2024",
        "attribution": "Imagery: test",
        "bounds": [-180.0, -85.0511, 180.0, 85.0511],
        "center": [0.0, 0.0, 2],
        "minzoom": 0,
        "maxzoom": 8,
        "format": "webp",
        "scheme": "xyz",
        "tiles": ["pmtiles://planet.pmtiles"],
    }


def test_generate_tilejson_dry_run_writes_nothing(tmp_path: Path, ran) -> None:
    path = pkg.PackagingManager(dry_run=True).generate_tilejson(tmp_path / "p.pmtiles", META)
    assert not path.exists()


def test_distribution_package(tmp_path: Path, ran) -> None:
    pmtiles = tmp_path / "planet.pmtiles"
    pmtiles.write_bytes(b"pm")
    tilejson = tmp_path / "planet.tilejson.json"
    tilejson.write_text("{}", encoding="utf-8")
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("[]", encoding="utf-8")

    out = pkg.PackagingManager().create_distribution_package(
        pmtiles, tilejson_path=tilejson, manifest_path=manifest, license_text="credits", destination=tmp_path / "dist"
    )
    assert out == tmp_path / "dist"
    assert sorted(p.name for p in out.iterdir()) == [
        "LICENSE_AND_CREDITS.txt", "MANIFEST.json", "planet.pmtiles", "planet.tilejson.json",
    ]
    assert (out / "LICENSE_AND_CREDITS.txt").read_text(encoding="utf-8") == "credits"


def test_distribution_without_manifest_and_default_destination(tmp_path: Path, ran) -> None:
    pmtiles = tmp_path / "planet.pmtiles"
    pmtiles.write_bytes(b"pm")
    tilejson = tmp_path / "planet.tilejson.json"
    tilejson.write_text("{}", encoding="utf-8")
    out = pkg.PackagingManager().create_distribution_package(
        pmtiles, tilejson_path=tilejson, manifest_path=tmp_path / "missing.json", license_text="c"
    )
    assert out == tmp_path / "distribution"
    assert "missing.json" not in {p.name for p in out.iterdir()}
