"""The PMTiles header center that `pmtiles convert` writes, and its repair.

go-pmtiles 1.30.3 averages the bounds as E7 int32 values, so east of about
107.4 degrees the sum overflows and the header center lands on the other
side of the globe (138.9..154.0 E gives -68.29). PackagingManager rewrites
the header center with `pmtiles edit` after converting.

The tests that run the real CLI are skipped when `pmtiles` is not in PATH.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

import planetarble.packaging.manager as pkg

needs_pmtiles = pytest.mark.skipif(shutil.which("pmtiles") is None, reason="pmtiles CLI not installed")

TOKYO_LAND_BOUNDS = (138.9228178, 24.2120253, 153.9857603, 35.8788697)


def _mbtiles(path: Path, bounds: tuple) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE metadata (name TEXT, value TEXT);"
        "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);"
    )
    rows = {"name": "t", "format": "png", "minzoom": "11", "maxzoom": "11",
            "bounds": ",".join(str(v) for v in bounds)}
    conn.executemany("INSERT INTO metadata VALUES (?, ?)", rows.items())
    # z11 x1819 y806 (XYZ) is central Tokyo; MBTiles rows are TMS
    conn.execute("INSERT INTO tiles VALUES (11, 1819, 2047 - 806, ?)", (b"\x89PNG not really",))
    conn.commit()
    conn.close()
    return path


def _header(path: Path) -> dict:
    out = subprocess.run(["pmtiles", "show", str(path), "--header-json"], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


@needs_pmtiles
def test_set_header_center_repairs_the_convert_overflow(tmp_path: Path) -> None:
    manager = pkg.PackagingManager()
    pmtiles = manager.convert_to_pmtiles(_mbtiles(tmp_path / "t.mbtiles", TOKYO_LAND_BOUNDS))

    manager.set_header_center(pmtiles, (146.454289, 30.045448, 11))

    header = _header(pmtiles)
    assert header["center"] == pytest.approx([146.454289, 30.045448, 11])
    assert header["bounds"] == pytest.approx(list(TOKYO_LAND_BOUNDS))  # the rest of the header is kept
    assert (header["minzoom"], header["maxzoom"]) == (11, 11)
    subprocess.run(["pmtiles", "verify", str(pmtiles)], check=True, capture_output=True)
    assert not list(tmp_path.glob("*.json"))  # no header file left behind


def test_set_header_center_dry_run_runs_nothing(tmp_path: Path, monkeypatch) -> None:
    calls: list = []
    monkeypatch.setattr(pkg.subprocess, "run", lambda *a, **k: calls.append(a))
    pkg.PackagingManager(dry_run=True).set_header_center(tmp_path / "t.pmtiles", (1.0, 2.0, 3))
    assert calls == []
