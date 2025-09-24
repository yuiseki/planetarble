"""Concrete implementation of the data acquisition interface."""

from __future__ import annotations

import os
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from planetarble.core.models import AssetManifest
from planetarble.logging import get_logger

from .appeears import (
    AppEEARSAuthError,
    AppEEARSClient,
    AppEEARSDownloadError,
    download_mcd43a4_tiles,
)
from .base import DataAcquisition
from .catalog import AssetCatalog, AssetRecord
from .download import DownloadError, DownloadManager, DownloadResult, calculate_sha256
from .manifest import build_manifest, write_manifest

LOGGER = get_logger(__name__)


class AcquisitionManager(DataAcquisition):
    """Download orchestrator for source datasets."""

    def __init__(
        self,
        data_directory: Path,
        *,
        catalog: Optional[AssetCatalog] = None,
        manifest_path: Optional[Path] = None,
        use_aria2: bool = False,
    ) -> None:
        self._data_directory = data_directory
        self._catalog = catalog or AssetCatalog.load_default()
        self._downloader = DownloadManager(
            data_directory,
            self._catalog,
            use_aria2=use_aria2,
        )
        self._manifest_path = manifest_path
        _load_dotenv_if_present()
        credential_source = _detect_appeears_credentials()
        LOGGER.debug(
            "appeears credentials %s",
            "found" if credential_source else "missing",
            extra={"source": credential_source or "none"},
        )

    def download_bmng(self, resolution: str = "500m", force: bool = False) -> Path:
        if resolution == "500m":
            panel_ids = [
                "bmng_2004_aug_500m_a1",
                "bmng_2004_aug_500m_a2",
                "bmng_2004_aug_500m_b1",
                "bmng_2004_aug_500m_b2",
                "bmng_2004_aug_500m_c1",
                "bmng_2004_aug_500m_c2",
                "bmng_2004_aug_500m_d1",
                "bmng_2004_aug_500m_d2",
            ]
            try:
                self._downloader.download_many(panel_ids, force=force)
                return (self._data_directory / "bmng" / "500m").resolve()
            except DownloadError as exc:  # pragma: no cover - fallback path
                LOGGER.warning("500m BMNG panels unavailable, falling back to 2km", extra={"error": str(exc)})
        result = self._downloader.download("bmng_2004_aug_2km_global", force=force)
        return result.path

    def download_gebco(self, year: int = 2024, force: bool = False) -> Path:
        # Latest grid is keyed as gebco_latest_grid regardless of year to allow catalog updates.
        result = self._downloader.download("gebco_latest_grid", force=force)
        return result.path

    def download_natural_earth(self, scale: str = "10m", force: bool = False) -> Path:
        if scale != "10m":
            raise ValueError("Only 10m Natural Earth data is configured")
        ids = ["natural_earth_land_10m", "natural_earth_ocean_10m", "natural_earth_coastline_10m"]
        self._downloader.download_many(ids, force=force)
        return (self._data_directory / "natural_earth").resolve()

    def verify_checksums(self, manifest: AssetManifest) -> bool:
        ok = True
        for asset_id, source in manifest.sources.items():
            try:
                record = self._catalog.get(asset_id)
            except KeyError:
                LOGGER.error("manifest references unknown asset", extra={"asset_id": asset_id})
                ok = False
                continue
            path = record.target_path(self._data_directory)
            if not path.exists():
                LOGGER.error("asset missing on disk", extra={"asset_id": asset_id, "path": str(path)})
                ok = False
                continue
            computed = calculate_sha256(path)
            if source.sha256 and source.sha256 != computed:
                LOGGER.error(
                    "checksum mismatch",
                    extra={"asset_id": asset_id, "expected": source.sha256, "computed": computed},
                )
                ok = False
        return ok

    def generate_manifest(
        self,
        *,
        generation_params: Optional[Dict[str, object]] = None,
        version: str = "1.0",
    ) -> AssetManifest:
        manifest = build_manifest(self._downloader.results, generation_params=generation_params, version=version)
        if self._manifest_path:
            write_manifest(manifest, self._manifest_path)
        return manifest

    def download_assets(self, asset_ids: Iterable[str]) -> Dict[str, DownloadResult]:
        return self._downloader.download_many(asset_ids)

    def download_modis_mcd43a4(self, *, force: bool = False) -> Dict[str, DownloadResult]:
        """Download MODIS MCD43A4 assets when configured in the catalog."""

        assets = self._collect_modis_assets()
        if not assets:
            return {}

        results: Dict[str, DownloadResult] = {}
        groups: Dict[datetime, List[Tuple[str, AssetRecord, str, Path]]] = defaultdict(list)

        for asset_id, record, acquisition_date, tile, target_path in assets:
            if not force and target_path.exists():
                results[asset_id] = self._register_existing_result(asset_id, record, target_path)
                continue
            groups[acquisition_date].append((asset_id, record, tile, target_path))

        if not groups:
            return results

        try:
            client = AppEEARSClient.from_env()
        except AppEEARSAuthError as exc:  # pragma: no cover - depends on env configuration
            raise AppEEARSAuthError(
                "AppEEARS credentials missing; ensure EARTHDATA_USERNAME/EARTHDATA_PASSWORD are set"
            ) from exc

        base_destination = (self._data_directory / "modis_mcd43a4").resolve()
        downloaded: Dict[str, DownloadResult] = {}

        with client:
            for acquisition_date, entries in sorted(groups.items()):
                LOGGER.info(
                    "requesting modis tiles",
                    extra={
                        "date": acquisition_date.strftime("%Y-%m-%d"),
                        "tiles": [tile for _, _, tile, _ in entries],
                    },
                )
                tiles = [tile for _, _, tile, _ in entries]
                try:
                    outputs = download_mcd43a4_tiles(
                        client,
                        date_value=acquisition_date.date(),
                        tiles=tiles,
                        destination=base_destination,
                    )
                except AppEEARSDownloadError as exc:
                    raise AppEEARSDownloadError(
                        f"Failed to download MODIS tiles for {acquisition_date.date()}: {exc}"
                    ) from exc

                for asset_id, record, tile, target_path in entries:
                    tile_files = outputs.get(tile)
                    if not tile_files:
                        raise AppEEARSDownloadError(
                            f"AppEEARS returned no files for tile {tile} on {acquisition_date.date()}"
                        )
                    LOGGER.debug(
                        "modis tile outputs",
                        extra={
                            "tile": tile,
                            "files": [str(path) for path in tile_files],
                        },
                    )
                    archive_path = _archive_tile_outputs(tile_files, target_path, force=force)
                    result = self._register_existing_result(asset_id, record, archive_path)
                    downloaded[asset_id] = result

        results.update(downloaded)
        return results

    def _collect_modis_assets(self) -> List[Tuple[str, AssetRecord, datetime, str, Path]]:
        assets: List[Tuple[str, AssetRecord, datetime, str, Path]] = []
        for record in self._catalog.iter_records():
            asset_id = record.asset_id
            if not asset_id.startswith("modis_mcd43a4_"):
                continue
            parts = asset_id.split("_")
            if len(parts) < 4:
                LOGGER.warning("modis asset id malformed", extra={"asset_id": asset_id})
                continue
            date_code = parts[2]
            tile = parts[3].lower()
            if len(date_code) != 7:
                LOGGER.warning("modis asset date code malformed", extra={"asset_id": asset_id, "date_code": date_code})
                continue
            try:
                acquisition_date = datetime.strptime(date_code, "%Y%j")
            except ValueError:
                LOGGER.warning("modis asset date code invalid", extra={"asset_id": asset_id, "date_code": date_code})
                continue
            if not tile.startswith("h") or "v" not in tile:
                LOGGER.warning("modis asset tile malformed", extra={"asset_id": asset_id, "tile": tile})
                continue
            target_path = record.target_path(self._data_directory)
            assets.append((asset_id, record, acquisition_date, tile, target_path))
        LOGGER.debug(
            "modis assets detected: %d",
            len(assets),
            extra={"asset_ids": [entry[0] for entry in assets]},
        )
        return assets

    def _register_existing_result(
        self,
        asset_id: str,
        record: AssetRecord,
        target_path: Path,
    ) -> DownloadResult:
        if not target_path.exists():
            raise FileNotFoundError(f"MODIS asset not found on disk: {target_path}")
        sha256 = calculate_sha256(target_path)
        size_bytes = target_path.stat().st_size
        result = DownloadResult(
            asset=record,
            path=target_path,
            url="appeears",
            sha256=sha256,
            size_bytes=size_bytes,
        )
        self._downloader._results[asset_id] = result  # type: ignore[attr-defined]
        return result


def _archive_tile_outputs(tile_files: Sequence[Path], target_path: Path, *, force: bool) -> Path:
    target_path = target_path.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and not force:
        return target_path
    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in tile_files:
            archive.write(file_path, arcname=file_path.name)
    return target_path


def _load_dotenv_if_present() -> None:
    root = Path(__file__).resolve().parents[3]
    env_path = root / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"')
            os.environ.setdefault(key, value)
    except OSError as exc:  # pragma: no cover - filesystem errors
        LOGGER.warning("Failed to read .env file", extra={"path": str(env_path), "error": str(exc)})


def _detect_appeears_credentials() -> Optional[str]:
    username = os.getenv("EARTHDATA_USERNAME")
    password = os.getenv("EARTHDATA_PASSWORD")
    if username and password:
        return "username_password"
    if username:
        return "username_only"
    if password:
        return "password_only"
    if os.getenv("APPEEARS_AUTHORIZATION"):
        return "authorization_header"
    if os.getenv("APPEEARS_TOKEN"):
        return "token"
    return None
