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
from planetarble.acquisition.mpc import (
    MPCError,
    fetch_true_color_tile,
)
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
    "MPCError",
    "get_available_layers",
    "fetch_true_color_tile",
    "verify_copernicus_connection",
    "download_mcd43a4_tiles",
    "download_viirs_corrected_reflectance",
]
