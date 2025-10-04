"""Planetarble reproducible pipeline package."""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "AcquisitionManager",
    "AssetCatalog",
    "AssetManifest",
    "AssetSource",
    "DataAcquisition",
    "DataProcessor",
    "ProcessingManager",
    "DownloadManager",
    "TileGenerator",
    "TilingManager",
    "PmtilesTilingManager",
    "PackagingManager",
    "ModisConfig",
    "ProcessingConfig",
    "TileMetadata",
    "ViirsConfig",
]

_MODULE_MAP = {
    "AcquisitionManager": ("planetarble.acquisition", "AcquisitionManager"),
    "AssetCatalog": ("planetarble.acquisition", "AssetCatalog"),
    "AssetManifest": ("planetarble.core", "AssetManifest"),
    "AssetSource": ("planetarble.core", "AssetSource"),
    "DataAcquisition": ("planetarble.acquisition", "DataAcquisition"),
    "DataProcessor": ("planetarble.processing", "DataProcessor"),
    "ProcessingManager": ("planetarble.processing", "ProcessingManager"),
    "DownloadManager": ("planetarble.acquisition", "DownloadManager"),
    "TileGenerator": ("planetarble.tiling", "TileGenerator"),
    "TilingManager": ("planetarble.tiling", "TilingManager"),
    "PmtilesTilingManager": ("planetarble.tiling", "PmtilesTilingManager"),
    "PackagingManager": ("planetarble.packaging", "PackagingManager"),
    "ModisConfig": ("planetarble.core", "ModisConfig"),
    "ProcessingConfig": ("planetarble.core", "ProcessingConfig"),
    "TileMetadata": ("planetarble.core", "TileMetadata"),
    "ViirsConfig": ("planetarble.core", "ViirsConfig"),
}


def __getattr__(name: str) -> Any:
    if name not in _MODULE_MAP:
        raise AttributeError(f"module 'planetarble' has no attribute '{name}'")
    module_name, attr = _MODULE_MAP[name]
    module = importlib.import_module(module_name)
    return getattr(module, attr)
