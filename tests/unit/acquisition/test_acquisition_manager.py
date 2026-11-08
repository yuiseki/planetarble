"""AcquisitionManager: which assets each download asks for, checksum checks,
AppEEARS (MODIS/VIIRS) grouping and archiving, and Copernicus gating.

The downloader, AppEEARS client and Copernicus calls are faked.
"""

from __future__ import annotations

import hashlib
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

from planetarble.acquisition import manager as manager_module
from planetarble.acquisition.appeears import AppEEARSDownloadError
from planetarble.acquisition.catalog import AssetCatalog
from planetarble.acquisition.copernicus import (
    CopernicusAuthError,
    CopernicusCredentialsMissing,
)
from planetarble.acquisition.download import DownloadError
from planetarble.acquisition.manager import (
    AcquisitionManager,
    _archive_tile_outputs,
    _detect_appeears_credentials,
)
from planetarble.core.models import (
    AssetManifest,
    AssetSource,
    CopernicusConfig,
    CopernicusLayerConfig,
    HLSConfig,
)


class StubDownloader:
    def __init__(self, fail_many: bool = False) -> None:
        self.calls: List[Any] = []
        self.fail_many = fail_many
        self._results: Dict[str, Any] = {}

    @property
    def results(self) -> Dict[str, Any]:
        return dict(self._results)

    def download(self, asset_id: str, force: bool = False):
        self.calls.append(("one", asset_id, force))
        return type("R", (), {"path": Path(f"/data/{asset_id}")})()

    def download_many(self, asset_ids, force: bool = False):
        ids = list(asset_ids)
        self.calls.append(("many", ids, force))
        if self.fail_many:
            raise DownloadError("panels missing")
        return {}


def _catalog(*asset_ids: str) -> AssetCatalog:
    return AssetCatalog.from_mapping(
        {asset_id: {"name": asset_id, "destination": f"raw/{asset_id}.zip"} for asset_id in asset_ids}
    )


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch) -> None:
    # the constructor reads the repository .env into os.environ
    monkeypatch.setattr(manager_module, "_load_dotenv_if_present", lambda: None)


def _manager(tmp_path: Path, *asset_ids: str, downloader: StubDownloader | None = None) -> AcquisitionManager:
    manager = AcquisitionManager(tmp_path, catalog=_catalog(*asset_ids))
    if downloader is not None:
        manager._downloader = downloader  # type: ignore[assignment]
    return manager


# --- catalog downloads --------------------------------------------------------


def test_bmng_500m_downloads_all_eight_panels(tmp_path: Path) -> None:
    downloader = StubDownloader()
    path = _manager(tmp_path, downloader=downloader).download_bmng("500m", force=True)

    ((kind, ids, force),) = downloader.calls
    assert kind == "many" and force is True
    assert ids == [f"bmng_2004_aug_500m_{c}{r}" for c in "abcd" for r in "12"]
    assert path == (tmp_path / "bmng" / "500m").resolve()


def test_bmng_falls_back_to_2km_when_panels_fail(tmp_path: Path) -> None:
    downloader = StubDownloader(fail_many=True)
    path = _manager(tmp_path, downloader=downloader).download_bmng("500m")
    assert downloader.calls[-1] == ("one", "bmng_2004_aug_2km_global", False)
    assert path == Path("/data/bmng_2004_aug_2km_global")


def test_bmng_2km_downloads_the_single_global_image(tmp_path: Path) -> None:
    downloader = StubDownloader()
    _manager(tmp_path, downloader=downloader).download_bmng("2km")
    assert downloader.calls == [("one", "bmng_2004_aug_2km_global", False)]


def test_gebco_uses_the_latest_grid_whatever_the_year(tmp_path: Path) -> None:
    downloader = StubDownloader()
    _manager(tmp_path, downloader=downloader).download_gebco(year=2020)
    assert downloader.calls == [("one", "gebco_latest_grid", False)]


def test_natural_earth_layers(tmp_path: Path) -> None:
    downloader = StubDownloader()
    manager = _manager(tmp_path, downloader=downloader)
    manager.download_natural_earth()
    manager.download_natural_earth(include_admin=True)
    base = ["natural_earth_land_10m", "natural_earth_ocean_10m", "natural_earth_coastline_10m"]
    assert downloader.calls[0][1] == base
    assert downloader.calls[1][1] == base + ["natural_earth_admin_0_10m", "natural_earth_admin_1_10m"]


def test_natural_earth_only_supports_10m(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="10m"):
        _manager(tmp_path, downloader=StubDownloader()).download_natural_earth("50m")


def test_default_catalog_has_every_asset_the_manager_names(tmp_path: Path) -> None:
    catalog = AssetCatalog.load_default()
    names = (
        [f"bmng_2004_aug_500m_{c}{r}" for c in "abcd" for r in "12"]
        + ["bmng_2004_aug_2km_global", "gebco_latest_grid", "etopo_2022_15s_bedrock_cog"]
        + ["natural_earth_land_10m", "natural_earth_ocean_10m", "natural_earth_coastline_10m"]
        + ["natural_earth_admin_0_10m", "natural_earth_admin_1_10m"]
    )
    for name in names:
        assert catalog.get(name).urls, name


# --- checksums ----------------------------------------------------------------


def test_verify_checksums_reports_unknown_missing_and_mismatched_assets(tmp_path: Path) -> None:
    manager = _manager(tmp_path, "good", "bad", "gone")
    good = tmp_path / "raw" / "good.zip"
    good.parent.mkdir()
    good.write_bytes(b"good")
    (tmp_path / "raw" / "bad.zip").write_bytes(b"bad")
    sha = hashlib.sha256(b"good").hexdigest()

    def manifest(**sources: str) -> AssetManifest:
        return AssetManifest(sources={k: AssetSource(name=k, url="u", sha256=v) for k, v in sources.items()})

    assert manager.verify_checksums(manifest(good=sha)) is True
    assert manager.verify_checksums(manifest(good=sha, bad=sha)) is False
    assert manager.verify_checksums(manifest(gone=sha)) is False
    assert manager.verify_checksums(manifest(unknown=sha)) is False


def test_generate_manifest_writes_when_a_path_is_set(tmp_path: Path) -> None:
    manager = AcquisitionManager(tmp_path, catalog=_catalog(), manifest_path=tmp_path / "m.json")
    manifest = manager.generate_manifest(generation_params={"z": 7})
    assert manifest.generation_params == {"z": 7}
    assert (tmp_path / "m.json").is_file()


# --- HLS plans ----------------------------------------------------------------


def test_hls_plan_is_skipped_when_disabled(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager.build_hls_plan(HLSConfig(enabled=False)) is None
    assert manager.build_hls_plans(HLSConfig(enabled=False)) == {}


def test_unknown_hls_region_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="nowhere"):
        _manager(tmp_path).build_hls_plans(HLSConfig(enabled=True), selected_region="nowhere")


def test_existing_hls_plan_is_summarised_not_rebuilt(tmp_path: Path) -> None:
    plan = tmp_path / "plan.ndjson"
    plan.write_text('{"season": "summer"}\n\nnot json\n{"season": "summer"}\n{"season": "winter"}\n{}\n')
    summary = _manager(tmp_path).build_hls_plan(HLSConfig(enabled=True, target_zoom=11), destination=plan)
    assert summary is not None
    assert summary.tile_count == 4
    assert summary.season_counts == {"summer": 2, "winter": 1, "unknown": 1}
    assert summary.zoom == 11


def test_missing_plan_summarises_to_zero(tmp_path: Path) -> None:
    summary = _manager(tmp_path)._summarize_plan(tmp_path / "none.ndjson", 9)
    assert (summary.tile_count, summary.season_counts) == (0, {})


# --- AppEEARS (MODIS / VIIRS) -------------------------------------------------


class FakeClient:
    entered = 0

    def __enter__(self):
        FakeClient.entered += 1
        return self

    def __exit__(self, *exc) -> None:
        return None


@pytest.fixture
def appeears(monkeypatch, tmp_path: Path) -> List[Dict[str, Any]]:
    requests: List[Dict[str, Any]] = []
    FakeClient.entered = 0
    monkeypatch.setattr(manager_module.AppEEARSClient, "from_env", classmethod(lambda cls: FakeClient()))

    def fake_download(client, *, date_value, tiles, destination, product=None):
        requests.append({"date": date_value, "tiles": list(tiles), "destination": destination, "product": product})
        outputs = {}
        for tile in tiles:
            if tile == "h99v99":
                continue  # AppEEARS returned nothing for this tile
            f = tmp_path / "appeears" / f"{date_value}_{tile}.tif"
            f.parent.mkdir(exist_ok=True)
            f.write_bytes(tile.encode())
            outputs[tile] = [f]
        return outputs

    monkeypatch.setattr(manager_module, "download_mcd43a4_tiles", fake_download)
    monkeypatch.setattr(manager_module, "download_viirs_corrected_reflectance", fake_download)
    return requests


def test_modis_asset_ids_are_parsed_and_malformed_ones_skipped(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        "modis_mcd43a4_2024150_h31v05",
        "modis_mcd43a4_2024150",  # too few parts
        "modis_mcd43a4_202415_h31v05",  # date code length
        "modis_mcd43a4_2024999_h31v05",  # day of year out of range
        "modis_mcd43a4_2024150_x31y05",  # tile
        "bmng_2004_aug_2km_global",
    )
    ((asset_id, _record, when, tile, path),) = manager._collect_appeears_assets("modis_mcd43a4_", "modis")
    assert asset_id == "modis_mcd43a4_2024150_h31v05"
    assert when.date() == date(2024, 5, 29)  # day 150 of a leap year
    assert tile == "h31v05"
    assert path == (tmp_path / "raw" / "modis_mcd43a4_2024150_h31v05.zip").resolve()


def test_modis_requests_one_task_per_date_and_archives_each_tile(tmp_path: Path, appeears) -> None:
    manager = _manager(
        tmp_path, "modis_mcd43a4_2024150_h31v05", "modis_mcd43a4_2024150_h32v05", "modis_mcd43a4_2024151_h31v05"
    )
    results = manager.download_modis_mcd43a4()

    assert [(r["date"], r["tiles"]) for r in appeears] == [
        (date(2024, 5, 29), ["h31v05", "h32v05"]),
        (date(2024, 5, 30), ["h31v05"]),
    ]
    assert appeears[0]["destination"] == (tmp_path / "modis_mcd43a4").resolve()
    assert FakeClient.entered == 1
    assert sorted(results) == sorted(manager._downloader.results)
    archive = results["modis_mcd43a4_2024150_h32v05"].path
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == ["2024-05-29_h32v05.tif"]
    assert results["modis_mcd43a4_2024150_h32v05"].url == "appeears"


def test_modis_skips_existing_archives_unless_forced(tmp_path: Path, appeears) -> None:
    manager = _manager(tmp_path, "modis_mcd43a4_2024150_h31v05")
    existing = tmp_path / "raw" / "modis_mcd43a4_2024150_h31v05.zip"
    existing.parent.mkdir()
    existing.write_bytes(b"kept")

    results = manager.download_modis_mcd43a4()
    assert appeears == []
    assert results["modis_mcd43a4_2024150_h31v05"].size_bytes == 4

    manager.download_modis_mcd43a4(force=True)
    assert len(appeears) == 1
    assert zipfile.is_zipfile(existing)


def test_modis_tile_without_outputs_is_an_error(tmp_path: Path, appeears) -> None:
    with pytest.raises(AppEEARSDownloadError, match="h99v99"):
        _manager(tmp_path, "modis_mcd43a4_2024150_h99v99").download_modis_mcd43a4()


def test_no_modis_assets_means_no_client(tmp_path: Path, appeears) -> None:
    assert _manager(tmp_path, "bmng_2004_aug_2km_global").download_modis_mcd43a4() == {}
    assert FakeClient.entered == 0


def test_viirs_uses_the_default_product_unless_given(tmp_path: Path, appeears) -> None:
    manager = _manager(tmp_path, "viirs_vnp09ga_2024150_h31v05")
    manager.download_viirs_corrected_reflectance()
    manager.download_viirs_corrected_reflectance(force=True, product="VNP09GA.001")
    assert [r["product"] for r in appeears] == ["VNP09GA.002", "VNP09GA.001"]
    assert appeears[0]["destination"] == (tmp_path / "viirs_vnp09ga").resolve()


def test_archive_keeps_an_existing_file_unless_forced(tmp_path: Path) -> None:
    src = tmp_path / "a.tif"
    src.write_bytes(b"x")
    target = tmp_path / "out" / "t.zip"
    _archive_tile_outputs([src], target, force=False)
    first = target.read_bytes()
    src.write_bytes(b"changed")
    _archive_tile_outputs([src], target, force=False)
    assert target.read_bytes() == first
    _archive_tile_outputs([src], target, force=True)
    with zipfile.ZipFile(target) as zf:
        assert zf.read("a.tif") == b"changed"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"EARTHDATA_USERNAME": "u", "EARTHDATA_PASSWORD": "p"}, "username_password"),
        ({"EARTHDATA_USERNAME": "u"}, "username_only"),
        ({"EARTHDATA_PASSWORD": "p"}, "password_only"),
        ({"APPEEARS_AUTHORIZATION": "Bearer x"}, "authorization_header"),
        ({"APPEEARS_TOKEN": "t"}, "token"),
        ({}, None),
    ],
)
def test_detect_appeears_credentials(monkeypatch, env: Dict[str, str], expected) -> None:
    for key in ("EARTHDATA_USERNAME", "EARTHDATA_PASSWORD", "APPEEARS_AUTHORIZATION", "APPEEARS_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert _detect_appeears_credentials() == expected


# --- Copernicus ---------------------------------------------------------------


def test_copernicus_tiles_are_skipped_when_disabled_or_unconfigured(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager.download_copernicus_tiles(CopernicusConfig(enabled=False)) == []
    assert manager.download_copernicus_tiles(CopernicusConfig(enabled=True, layers=())) == []


def test_copernicus_tiles_are_skipped_without_credentials(tmp_path: Path, monkeypatch) -> None:
    def missing(cls):
        raise CopernicusCredentialsMissing("no creds")

    monkeypatch.setattr(manager_module.CopernicusCredentials, "from_env", classmethod(missing))
    config = CopernicusConfig(enabled=True, layers=(CopernicusLayerConfig(name="TRUE_COLOR"),))
    assert _manager(tmp_path).download_copernicus_tiles(config) == []


def test_copernicus_tiles_download_into_the_data_dir(tmp_path: Path, monkeypatch) -> None:
    seen: Dict[str, Any] = {}
    monkeypatch.setattr(manager_module.CopernicusCredentials, "from_env", classmethod(lambda cls: "creds"))

    def fake_download(credentials, config, destination, *, force):
        seen.update(credentials=credentials, destination=destination, force=force)
        return [{"layer": "TRUE_COLOR", "tiles": 4}]

    monkeypatch.setattr(manager_module, "download_copernicus_wms_tiles", fake_download)
    config = CopernicusConfig(enabled=True, layers=(CopernicusLayerConfig(name="TRUE_COLOR"),))

    assert _manager(tmp_path).download_copernicus_tiles(config, force=True) == [{"layer": "TRUE_COLOR", "tiles": 4}]
    assert seen == {"credentials": "creds", "destination": (tmp_path / "copernicus" / "tiles").resolve(), "force": True}
    assert seen["destination"].is_dir()


@pytest.mark.parametrize("error", [CopernicusCredentialsMissing("x"), CopernicusAuthError("x")])
def test_copernicus_check_is_lenient_unless_strict(tmp_path: Path, monkeypatch, error) -> None:
    def fail():
        raise error

    monkeypatch.setattr(manager_module, "verify_copernicus_connection", fail)
    manager = _manager(tmp_path)
    assert manager.check_copernicus_connection() is False
    with pytest.raises(type(error)):
        manager.check_copernicus_connection(strict=True)


def test_copernicus_check_passes_through_success(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(manager_module, "verify_copernicus_connection", lambda: True)
    assert _manager(tmp_path).check_copernicus_connection() is True
