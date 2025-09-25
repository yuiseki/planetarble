"""Data acquisition interfaces and implementations for Planetarble."""

from planetarble.acquisition.appeears import (
    AppEEARSAuthError,
    AppEEARSClient,
    AppEEARSDownloadError,
    download_mcd43a4_tiles,
    download_viirs_corrected_reflectance,
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
    "DataAcquisition",
    "DownloadError",
    "DownloadManager",
    "download_mcd43a4_tiles",
    "download_viirs_corrected_reflectance",
]
