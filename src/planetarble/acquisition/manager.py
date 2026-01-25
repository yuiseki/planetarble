"""Concrete implementation of the data acquisition interface."""

from __future__ import annotations

import json
import os
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from planetarble.core.models import AssetManifest, CopernicusConfig, HLSConfig, HLSPlanRegion
from planetarble.logging import get_logger

from .appeears import (
    AppEEARSAuthError,
    AppEEARSClient,
    AppEEARSDownloadError,
    download_mcd43a4_tiles,
    download_viirs_corrected_reflectance,
)
from .copernicus import (
    CopernicusAccessError,
    CopernicusAuthError,
    CopernicusCredentials,
    CopernicusCredentialsMissing,
    download_tiles as download_copernicus_wms_tiles,
    verify_copernicus_connection,
)
from .base import DataAcquisition
from .catalog import AssetCatalog, AssetRecord
from .download import DownloadError, DownloadManager, DownloadResult, calculate_sha256
from .manifest import build_manifest, write_manifest
from .hls import (
    HLSMosaicPlanner,
    HLSPlanSummary,
    load_land_geometry,
    load_region_geometry,
)

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

    def download_etopo(self, *, force: bool = False) -> Path:
        result = self._downloader.download("etopo_2022_15s_bedrock_cog", force=force)
        return result.path

    def build_hls_plan(
        self,
        config: HLSConfig,
        *,
        destination: Optional[Path] = None,
        force: bool = False,
    ) -> Optional[HLSPlanSummary]:
        if not config.enabled:
            LOGGER.info("HLS acquisition disabled; skipping plan generation")
            return None
        planner = HLSMosaicPlanner(config)
        plan_path = destination or (self._data_directory / "plans" / f"hls_z{config.target_zoom}_plan.ndjson")
        plan_path = plan_path.resolve()
        if plan_path.exists() and not force:
            LOGGER.info("reusing existing HLS plan", extra={"path": str(plan_path)})
            return self._summarize_plan(plan_path, config.target_zoom)
        summary = planner.write_plan(plan_path)
        return summary

    def build_hls_plans(
        self,
        config: HLSConfig,
        *,
        force: bool = False,
        selected_region: Optional[str] = None,
    ) -> Dict[str, HLSPlanSummary]:
        if not config.enabled:
            LOGGER.info("HLS acquisition disabled; skipping plan generation")
            return {}
        regions = list(config.plan_regions)
        if selected_region:
            regions = [region for region in regions if region.name == selected_region]
            if not regions:
                raise ValueError(f"Unknown HLS plan region: {selected_region}")
        summaries: Dict[str, HLSPlanSummary] = {}
        for region in regions:
            summary = self._build_hls_plan_for_region(config, region, force=force)
            summaries[region.name] = summary
        return summaries

    def _build_hls_plan_for_region(
        self,
        config: HLSConfig,
        region: HLSPlanRegion,
        *,
        force: bool = False,
    ) -> HLSPlanSummary:
        planner = HLSMosaicPlanner(config)
        plan_path = (
            self._data_directory
            / "plans"
            / f"hls_z{config.target_zoom}_plan_{region.name}.ndjson"
        )
        plan_path = plan_path.resolve()
        if plan_path.exists() and not force:
            LOGGER.info("reusing existing HLS plan", extra={"path": str(plan_path)})
            return self._summarize_plan(plan_path, config.target_zoom)
        region_geometry = load_region_geometry(region, data_dir=self._data_directory)
        land_geometry = None
        if region.land_only:
            land_geometry = load_land_geometry(
                land_mask_path=config.land_mask_path,
                data_dir=self._data_directory,
                region_geometry=region_geometry,
            )
        summary = planner.write_plan(
            plan_path,
            region_geometry=region_geometry,
            land_geometry=land_geometry,
        )
        return summary

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

    def download_natural_earth(
        self,
        scale: str = "10m",
        *,
        force: bool = False,
        include_admin: bool = False,
    ) -> Path:
        if scale != "10m":
            raise ValueError("Only 10m Natural Earth data is configured")
        ids = ["natural_earth_land_10m", "natural_earth_ocean_10m", "natural_earth_coastline_10m"]
        if include_admin:
            ids.extend(["natural_earth_admin_0_10m", "natural_earth_admin_1_10m"])
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

    def _summarize_plan(self, path: Path, zoom: int) -> HLSPlanSummary:
        counter: Counter[str] = Counter()
        total = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        LOGGER.debug("invalid hls plan record", extra={"path": str(path)})
                        continue
                    season_key = str(record.get("season", "unknown"))
                    counter[season_key] += 1
                    total += 1
        except FileNotFoundError:
            LOGGER.warning("hls plan file missing", extra={"path": str(path)})
            return HLSPlanSummary(path=path, zoom=zoom, tile_count=0, season_counts={})
        return HLSPlanSummary(path=path, zoom=zoom, tile_count=total, season_counts=dict(counter))

    def check_copernicus_connection(self, *, strict: bool = False) -> bool:
        """Verify that Copernicus credentials allow WMS access."""

        try:
            ok = verify_copernicus_connection()
        except CopernicusCredentialsMissing as exc:
            LOGGER.info("copernicus credentials missing; skipping verification", extra={"error": str(exc)})
            if strict:
                raise
            return False
        except (CopernicusAuthError, CopernicusAccessError) as exc:
            LOGGER.error("copernicus verification failed", extra={"error": str(exc)})
            if strict:
                raise
            return False
        else:
            LOGGER.info("copernicus connection verified")
            return ok

    def download_copernicus_tiles(
        self,
        config: CopernicusConfig,
        *,
        force: bool = False,
    ) -> List[Dict[str, object]]:
        """Download Copernicus Sentinel-2 tiles according to configuration."""

        if not config.enabled:
            LOGGER.debug("copernicus acquisition disabled; skipping")
            return []
        if not config.layers:
            LOGGER.warning("copernicus acquisition enabled but no layers configured")
            return []

        try:
            credentials = CopernicusCredentials.from_env()
        except CopernicusCredentialsMissing as exc:
            LOGGER.warning("Copernicus credentials missing; skipping tile download", extra={"error": str(exc)})
            return []

        destination = (self._data_directory / "copernicus" / "tiles").resolve()
        destination.mkdir(parents=True, exist_ok=True)

        LOGGER.info(
            "downloading copernicus tiles",
            extra={
                "layers": [layer.name for layer in config.layers],
                "bbox": config.bbox,
                "min_zoom": config.min_zoom,
                "max_zoom": config.max_zoom,
            },
        )

        summaries = download_copernicus_wms_tiles(
            credentials,
            config,
            destination,
            force=force,
        )

        for summary in summaries:
            LOGGER.info(
                "copernicus layer download summary",
                extra=summary,
            )
        return summaries

    def download_viirs_corrected_reflectance(
        self,
        *,
        force: bool = False,
        product: Optional[str] = None,
    ) -> Dict[str, DownloadResult]:
        """Download VIIRS corrected reflectance assets via AppEEARS."""

        assets = self._collect_viirs_assets()
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

        base_destination = (self._data_directory / "viirs_vnp09ga").resolve()
        downloaded: Dict[str, DownloadResult] = {}

        with client:
            collection = product or "VNP09GA.002"
            for acquisition_date, entries in sorted(groups.items()):
                LOGGER.info(
                    "requesting viirs tiles",
                    extra={
                        "date": acquisition_date.strftime("%Y-%m-%d"),
                        "tiles": [tile for _, _, tile, _ in entries],
                    },
                )
                tiles = [tile for _, _, tile, _ in entries]
                try:
                    outputs = download_viirs_corrected_reflectance(
                        client,
                        date_value=acquisition_date.date(),
                        tiles=tiles,
                        destination=base_destination,
                        product=collection,
                    )
                except AppEEARSDownloadError as exc:
                    raise AppEEARSDownloadError(
                        f"Failed to download VIIRS tiles for {acquisition_date.date()}: {exc}"
                    ) from exc

                for asset_id, record, tile, target_path in entries:
                    tile_files = outputs.get(tile)
                    if not tile_files:
                        raise AppEEARSDownloadError(
                            f"AppEEARS returned no files for tile {tile} on {acquisition_date.date()}"
                        )
                    LOGGER.debug(
                        "viirs tile outputs",
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

    def _collect_viirs_assets(self) -> List[Tuple[str, AssetRecord, datetime, str, Path]]:
        assets: List[Tuple[str, AssetRecord, datetime, str, Path]] = []
        for record in self._catalog.iter_records():
            asset_id = record.asset_id
            if not asset_id.startswith("viirs_vnp09ga_"):
                continue
            parts = asset_id.split("_")
            if len(parts) < 4:
                LOGGER.warning("viirs asset id malformed", extra={"asset_id": asset_id})
                continue
            date_code = parts[2]
            tile = parts[3].lower()
            if len(date_code) != 7:
                LOGGER.warning("viirs asset date code malformed", extra={"asset_id": asset_id, "date_code": date_code})
                continue
            try:
                acquisition_date = datetime.strptime(date_code, "%Y%j")
            except ValueError:
                LOGGER.warning("viirs asset date code invalid", extra={"asset_id": asset_id, "date_code": date_code})
                continue
            if not tile.startswith("h") or "v" not in tile:
                LOGGER.warning("viirs asset tile malformed", extra={"asset_id": asset_id, "tile": tile})
                continue
            target_path = record.target_path(self._data_directory)
            assets.append((asset_id, record, acquisition_date, tile, target_path))
        LOGGER.debug(
            "viirs assets detected: %d",
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
