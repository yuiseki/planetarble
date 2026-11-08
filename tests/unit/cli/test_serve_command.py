"""`planetarble serve`: which PMTiles it picks, what it copies, how it binds.

The HTTP server is replaced by a fake that stops at serve_forever, so no port
is opened. Range handling itself is covered by test_serve_range.py.
"""

from __future__ import annotations

import http.server
import importlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from planetarble.config import PipelineConfig
from planetarble.core.models import HLSConfig, HLSPlanRegion, ProcessingConfig

cli = importlib.import_module("planetarble.cli.main")


class FakeServer:
    instances: List["FakeServer"] = []

    def __init__(self, address, handler) -> None:
        self.address = address
        self.handler = handler
        self.shut_down = False
        FakeServer.instances.append(self)

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def shutdown(self) -> None:
        self.shut_down = True


@pytest.fixture(autouse=True)
def fake_server(monkeypatch) -> type:
    FakeServer.instances = []
    monkeypatch.setattr(http.server, "ThreadingHTTPServer", FakeServer)
    return FakeServer


@pytest.fixture
def opened(monkeypatch) -> List[str]:
    import webbrowser

    urls: List[str] = []
    monkeypatch.setattr(webbrowser, "open", urls.append)
    return urls


def _pmtiles(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PMTiles")
    return path


def _cfg(tmp_path: Path, **hls: Any) -> PipelineConfig:
    return PipelineConfig(
        data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        output_dir=tmp_path / "output",
        processing=ProcessingConfig(gebco_year=2024, max_zoom=11),
        hls=HLSConfig(**hls),
    )


@pytest.fixture
def use_cfg(patch_cli):
    def _use(cfg: PipelineConfig) -> None:
        patch_cli("_resolve_config_path", lambda path: Path("pipeline.yaml"))
        patch_cli("load_config", lambda path: cfg)

    return _use


def test_serves_the_given_pmtiles_directory_with_the_packaged_viewer(tmp_path: Path, fake_server) -> None:
    planet = _pmtiles(tmp_path / "dist" / "planet.pmtiles")

    assert cli.main(["serve", "--pmtiles", str(planet), "--host", "127.0.0.1", "--ui-port", "9001"]) == 0

    (server,) = fake_server.instances
    assert server.address == ("127.0.0.1", 9001)
    assert server.handler.keywords == {"directory": str(planet.parent.resolve())}
    assert server.shut_down is True  # Ctrl-C shuts the server down cleanly
    copied = tmp_path / "dist" / "viewer" / "index.html"
    from planetarble.cli.serve import PACKAGED_VIEWER_ROOT

    assert copied.read_bytes() == (PACKAGED_VIEWER_ROOT / "index.html").read_bytes()


def test_missing_pmtiles_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="PMTiles file not found"):
        cli.main(["serve", "--pmtiles", str(tmp_path / "nope.pmtiles")])


def test_missing_viewer_directory_is_reported(tmp_path: Path) -> None:
    planet = _pmtiles(tmp_path / "planet.pmtiles")
    with pytest.raises(SystemExit, match="Viewer directory not found"):
        cli.main(["serve", "--pmtiles", str(planet), "--viewer-root", str(tmp_path / "none")])


def test_viewer_directory_without_index_is_reported(tmp_path: Path) -> None:
    planet = _pmtiles(tmp_path / "planet.pmtiles")
    (tmp_path / "viewer_src").mkdir()
    with pytest.raises(SystemExit, match="Viewer index not found"):
        cli.main(["serve", "--pmtiles", str(planet), "--viewer-root", str(tmp_path / "viewer_src")])


def test_custom_viewer_is_copied(tmp_path: Path) -> None:
    planet = _pmtiles(tmp_path / "dist" / "planet.pmtiles")
    (tmp_path / "v").mkdir()
    (tmp_path / "v" / "index.html").write_text("<p>mine</p>")
    cli.main(["serve", "--pmtiles", str(planet), "--viewer-root", str(tmp_path / "v")])
    assert (tmp_path / "dist" / "viewer" / "index.html").read_text() == "<p>mine</p>"


def test_open_uses_localhost_when_bound_to_all_interfaces(tmp_path: Path, opened: List[str]) -> None:
    planet = _pmtiles(tmp_path / "planet.pmtiles")
    cli.main(["serve", "--pmtiles", str(planet), "--open", "--ui-port", "9002"])
    assert opened == ["http://localhost:9002/viewer/?pmtiles=http://localhost:9002/planet.pmtiles"]


def test_region_picks_the_highest_zoom_build(tmp_path: Path, use_cfg, fake_server) -> None:
    cfg = _cfg(tmp_path)
    use_cfg(cfg)
    dist = cfg.output_dir / "distribution"
    for name in ("planet_2024_9z_kanto_hls.pmtiles", "planet_2024_11z_kanto_hls.pmtiles",
                 "planet_2024_12z_tohoku_hls.pmtiles"):
        _pmtiles(dist / name)

    cli.main(["serve", "--region", "kanto"])

    assert fake_server.instances[0].handler.keywords["directory"] == str(dist.resolve())
    assert (dist / "viewer" / "index.html").is_file()


def test_land_region_falls_back_to_the_plain_region_build(tmp_path: Path, use_cfg, opened: List[str]) -> None:
    cfg = _cfg(tmp_path)
    use_cfg(cfg)
    _pmtiles(cfg.output_dir / "distribution" / "planet_2024_10z_kanto_hls.pmtiles")

    cli.main(["serve", "--region", "kanto_land", "--open"])

    assert "planet_2024_10z_kanto_hls.pmtiles" in opened[0]


def test_region_without_a_build_names_the_expected_file(tmp_path: Path, use_cfg) -> None:
    use_cfg(_cfg(tmp_path))
    with pytest.raises(SystemExit, match="planet_2024_11z_kanto_hls.pmtiles"):
        cli.main(["serve", "--region", "kanto"])


def test_region_viewer_url_is_centred_on_the_region(tmp_path: Path, use_cfg, opened, monkeypatch) -> None:
    pytest.importorskip("osgeo")
    import planetarble.acquisition.hls as hls_module

    region = HLSPlanRegion(name="kanto", bbox=(139.0, 35.0, 140.0, 36.0))
    cfg = _cfg(tmp_path, plan_regions=(region,))
    use_cfg(cfg)
    seen: Dict[str, Any] = {}

    def fake_geometry(region_config, *, data_dir):
        seen["region"] = region_config.name
        return SimpleNamespace(GetEnvelope=lambda: (139.0, 140.0, 35.0, 36.0))

    monkeypatch.setattr(hls_module, "load_region_geometry", fake_geometry)
    _pmtiles(cfg.output_dir / "distribution" / "planet_2024_11z_kanto_hls.pmtiles")

    cli.main(["serve", "--region", "kanto", "--open"])

    assert seen == {"region": "kanto"}
    assert opened[0].endswith("#8/35.5/139.5")
