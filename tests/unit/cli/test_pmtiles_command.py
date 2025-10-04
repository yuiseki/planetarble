from pathlib import Path

import importlib

import pytest

cli_main = importlib.import_module("planetarble.cli.main")


def test_pmtiles_cli_invokes_manager(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "raster.tif"
    source.touch()
    out_dir = tmp_path / "out"

    called: dict[str, object] = {}

    class StubManager:
        def __init__(self, config, temp_dir, output_dir, dry_run):  # type: ignore[no-untyped-def]
            called["init"] = {
                "temp_dir": temp_dir,
                "output_dir": output_dir,
                "dry_run": dry_run,
            }
            self._zxy = tmp_path / "zxy"
            self._zxy.mkdir(exist_ok=True)
            self._mbtiles = tmp_path / "tiles.mbtiles"
            self._mbtiles.touch()
            self._pmtiles = tmp_path / "tiles.pmtiles"
            self._pmtiles.touch()

        def build_zxy(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            called["build_zxy"] = kwargs
            return self._zxy

        def pack_mbtiles(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            called["pack_mbtiles"] = kwargs
            return self._mbtiles

        def convert_pmtiles(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            called["convert_pmtiles"] = kwargs
            return self._pmtiles

        def verify(self, pmtiles_path):  # type: ignore[no-untyped-def]
            called["verify"] = str(pmtiles_path)

        def show_header(self, pmtiles_path):  # type: ignore[no-untyped-def]
            called["show_header"] = str(pmtiles_path)
            return {"tile_type": "jpg"}

    monkeypatch.setattr(cli_main, "PmtilesTilingManager", StubManager)

    exit_code = cli_main.main(
        [
            "tiling",
            "pmtiles",
            "--input",
            str(source),
            "--out",
            str(out_dir),
            "--min-zoom",
            "0",
            "--max-zoom",
            "1",
            "--format",
            "jpg",
            "--quality",
            "80",
            "--resampling",
            "cubic",
            "--name",
            "Test",
            "--attribution",
            "Test Attr",
            "--bounds-mode",
            "global",
        ]
    )

    assert exit_code == 0
    assert "build_zxy" in called
    assert "pack_mbtiles" in called
    assert "convert_pmtiles" in called
    assert "verify" in called
    assert "show_header" in called
    assert called["convert_pmtiles"]["deduplicate"] is True
