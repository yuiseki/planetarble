"""Dataclasses describing core Planetarble entities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


@dataclass
class AssetSource:
    """Describe a single upstream dataset and its provenance."""

    name: str
    url: str
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    license: Optional[str] = None
    attribution: Optional[str] = None


@dataclass
class AssetManifest:
    """Record the datasets and parameters used to build an artifact."""

    sources: Dict[str, AssetSource] = field(default_factory=dict)
    generation_params: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    version: str = "0.0.1"


@dataclass
class ProcessingConfig:
    """Configuration options that control raster processing."""

    bmng_resolution: str = "500m"
    gebco_year: int = 2024
    natural_earth_scale: str = "10m"
    color_enhancement: float = 1.05
    hillshade_opacity: float = 0.15
    max_zoom: int = 10
    tile_format: str = "JPEG"
    tile_quality: int = 95
    tile_source: str = "bmng"
    modis_enabled: bool = False
    modis_doy: Optional[str] = None
    modis_tiles: Tuple[str, ...] = field(default_factory=tuple)
    modis_tile_source: str = "bmng"
    modis_scale_min: float = 0.0
    modis_scale_max: float = 4000.0
    modis_gamma: float = 1.0
    viirs_enabled: bool = False
    viirs_date: Optional[str] = None
    viirs_tiles: Tuple[str, ...] = field(default_factory=tuple)
    viirs_product: str = "VNP09GA.002"
    viirs_scale_min: float = 0.0
    viirs_scale_max: float = 9000.0
    viirs_gamma: float = 0.8


@dataclass
class TileMetadata:
    """Metadata embedded in PMTiles and TileJSON outputs."""

    name: str
    description: str
    version: str
    bounds: Tuple[float, float, float, float]
    center: Tuple[float, float, int]
    minzoom: int
    maxzoom: int
    attribution: str
    format: str
    scheme: str = "xyz"
