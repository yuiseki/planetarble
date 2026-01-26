"""Data acquisition interfaces and implementations for Planetarble."""

from planetarble.acquisition.appeears import (
    AppEEARSAuthError,
    AppEEARSClient,
    AppEEARSDownloadError,
    download_mcd43a4_tiles,
    download_viirs_corrected_reflectance,
)
from planetarble.acquisition.copernicus import (
    CopernicusAccessError,
    CopernicusAuthError,
    CopernicusCredentialsMissing,
    get_available_layers,
    verify_copernicus_connection,
)
from planetarble.acquisition.gsi import GSIError, fetch_gsi_ortho_clip
from planetarble.acquisition.hls import (
    HLSMosaicPlanner,
    HLSMosaicTask,
    HLSPlanSummary,
    HLSScene,
    HLSSTACClient,
    iter_plan,
)
from planetarble.acquisition.sentinel_2 import (
    Sentinel2Scene,
    Sentinel2SceneManifest,
    Sentinel2SceneManifestBuilder,
)
from planetarble.acquisition.mpc import MPCError, append_sas_token, fetch_sas_token, fetch_true_color_tile
from planetarble.acquisition.base import DataAcquisition
from planetarble.acquisition.catalog import AssetCatalog, AssetRecord
from planetarble.acquisition.download import DownloadError, DownloadManager
from planetarble.acquisition.manager import AcquisitionManager

__all__ = [
    "AcquisitionManager",
    "AppEEARSAuthError",
    "AppEEARSClient",
    "AppEEARSDownloadError",
    "AssetCatalog",
    "AssetRecord",
    "CopernicusAccessError",
    "CopernicusAuthError",
    "CopernicusCredentialsMissing",
    "DataAcquisition",
    "DownloadError",
    "DownloadManager",
    "GSIError",
    "HLSMosaicPlanner",
    "HLSMosaicTask",
    "HLSPlanSummary",
    "HLSScene",
    "HLSSTACClient",
    "iter_plan",
    "MPCError",
    "Sentinel2Scene",
    "Sentinel2SceneManifest",
    "Sentinel2SceneManifestBuilder",
    "get_available_layers",
    "fetch_gsi_ortho_clip",
    "fetch_sas_token",
    "fetch_true_color_tile",
    "append_sas_token",
    "verify_copernicus_connection",
    "download_mcd43a4_tiles",
    "download_viirs_corrected_reflectance",
]
