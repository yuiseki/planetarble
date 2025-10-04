import json
import subprocess
from pathlib import Path

import pytest

from planetarble.core.models import ProcessingConfig
from planetarble.tiling.pmtiles import PmtilesTilingManager


class StubRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, command, *, description: str) -> None:  # type: ignore[no-untyped-def]
        self.calls.append((tuple(command), description))


@pytest.fixture()
def manager(tmp_path: Path) -> PmtilesTilingManager:
    config = ProcessingConfig()
    mgr = PmtilesTilingManager(
        config,
        temp_dir=tmp_path / "tmp",
        output_dir=tmp_path / "out",
        dry_run=False,
    )
    stub = StubRunner()
    mgr._runner = stub  # type: ignore[attr-defined]
    return mgr


def test_build_zxy_invokes_gdal_raster_tile(manager: PmtilesTilingManager, tmp_path: Path) -> None:
    source = tmp_path / "input.tif"
    source.touch()

    zxy_dir = manager.build_zxy(
        source,
        min_zoom=0,
        max_zoom=2,
        tile_format="JPEG",
        quality=80,
        resampling="cubic",
    )

    calls = manager._runner.calls  # type: ignore[attr-defined]
    assert calls, "gdal raster tile command should be invoked"
    cmd, description = calls[0]
    assert description.startswith("build XYZ tiles")
    assert "gdal" in cmd[0]
    assert "--min-zoom" in cmd
    assert zxy_dir.name.endswith("zxy")


def test_pack_mbtiles_writes_metadata(manager: PmtilesTilingManager, tmp_path: Path) -> None:
    source = tmp_path / "input.tif"
    source.touch()
    zxy_dir = tmp_path / "tiles"
    zxy_dir.mkdir()
    (zxy_dir / "0").mkdir()
    manager._determine_bounds = lambda *args, **kwargs: (-10.0, -5.0, 10.0, 5.0)  # type: ignore[attr-defined]

    mbtiles = manager.pack_mbtiles(
        zxy_dir,
        source_path=source,
        tile_format="JPEG",
        min_zoom=0,
        max_zoom=2,
        name="Test",
        attribution="Test Attribution",
        bounds_mode="auto",
    )

    metadata_path = zxy_dir / "metadata.json"
    assert metadata_path.exists()
    payload = json.loads(metadata_path.read_text())
    assert payload["name"] == "Test"
    assert payload["format"] == "jpg"
    assert payload["minzoom"] == "0"
    assert payload["maxzoom"] == "2"

    calls = manager._runner.calls  # type: ignore[attr-defined]
    assert any("mb-util" in call[0][0] for call in calls)
    assert mbtiles.suffix == ".mbtiles"


def test_convert_and_verify(manager: PmtilesTilingManager, tmp_path: Path) -> None:
    stub_runner = manager._runner  # type: ignore[attr-defined]
    mbtiles = tmp_path / "test.mbtiles"
    mbtiles.touch()

    pmtiles_path = manager.convert_pmtiles(mbtiles, destination=tmp_path / "test.pmtiles", deduplicate=False)
    manager.verify(pmtiles_path)

    assert any(call[0][0] == "pmtiles" and call[0][1] == "convert" for call in stub_runner.calls)
    assert any(call[0][0] == "pmtiles" and call[0][1] == "verify" for call in stub_runner.calls)


def test_show_header(monkeypatch: pytest.MonkeyPatch, manager: PmtilesTilingManager, tmp_path: Path) -> None:
    header = {"tile_type": "jpg", "min_zoom": 0}

    class Result:  # pragma: no cover - simple container
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(command, check, capture_output, text):  # type: ignore[no-untyped-def]
        return Result(json.dumps(header))

    monkeypatch.setattr(subprocess, "run", fake_run)  # type: ignore[name-defined]

    pmtiles = tmp_path / "test.pmtiles"
    data = manager.show_header(pmtiles)
    assert data == header
