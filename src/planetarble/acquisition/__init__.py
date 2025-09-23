"""Data acquisition interfaces and implementations for Planetarble."""

from planetarble.acquisition.base import DataAcquisition
from planetarble.acquisition.catalog import AssetCatalog, AssetRecord
from planetarble.acquisition.download import DownloadError, DownloadManager
from planetarble.acquisition.manager import AcquisitionManager

__all__ = [
    "AcquisitionManager",
    "AssetCatalog",
    "AssetRecord",
    "DataAcquisition",
    "DownloadError",
    "DownloadManager",
]
