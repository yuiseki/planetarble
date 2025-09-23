"""Concrete implementation of the data acquisition interface."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional

from planetarble.core.models import AssetManifest
from planetarble.logging import get_logger

from .base import DataAcquisition
from .catalog import AssetCatalog
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
    ) -> None:
        self._data_directory = data_directory
        self._catalog = catalog or AssetCatalog.load_default()
        self._downloader = DownloadManager(data_directory, self._catalog)
        self._manifest_path = manifest_path

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

    def download_gebco(self, year: int = 2025, force: bool = False) -> Path:
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
