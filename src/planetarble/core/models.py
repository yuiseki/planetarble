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
    min_zoom: int = 0
    max_zoom: int = 10
    resampling: str = "cubic"
    tile_format: str = "WEBP"
    tile_quality: int = 82
    tile_source: str = "hls"
    tile_name: str = "Planetarble HLS Mosaic"
    tile_attribution: str = ""
    gdal_num_threads: str = "ALL_CPUS"
    gdal_cachemax: str = "50%"
    pmtiles_dedup: bool = True
    mbtiles_tiler: str = "auto"
    zoom_level_strategy: str = "LOWER"


@dataclass
class ModisConfig:
    """Configuration for MODIS MCD43A4 surface reflectance processing."""

    enabled: bool = False
    doy: Optional[str] = None
    tiles: Tuple[str, ...] = field(default_factory=tuple)
    tile_source: Optional[str] = None
    scale_min: float = 0.0
    scale_max: float = 4000.0
    gamma: float = 1.0


@dataclass
class ViirsConfig:
    """Configuration for VIIRS corrected reflectance processing."""

    enabled: bool = False
    date: Optional[str] = None
    tiles: Tuple[str, ...] = field(default_factory=tuple)
    product: str = "VNP09GA.002"
    scale_min: float = 0.0
    scale_max: float = 9000.0
    gamma: float = 0.8


@dataclass
class CopernicusLayerConfig:
    """Describe a Copernicus WMS layer to download."""

    name: str
    format: str = "image/jpeg"
    style: str = ""
    time: Optional[str] = None
    output: Optional[str] = None


@dataclass
class CopernicusConfig:
    """Configuration controlling Copernicus Sentinel-2 acquisition."""

    enabled: bool = False
    bbox: Tuple[float, float, float, float] = (123.0, 24.0, 147.0, 46.0)
    min_zoom: int = 8
    max_zoom: int = 14
    tile_size: int = 256
    layers: Tuple[CopernicusLayerConfig, ...] = field(default_factory=tuple)
    max_tiles_per_layer: Optional[int] = None
    timeout_seconds: int = 30
    request_interval_seconds: float = 0.5
    max_retries: int = 3
    backoff_factor: float = 2.0
    rate_limit_min_interval_seconds: float = 0.0
    rate_limit_max_requests: Optional[int] = None
    rate_limit_window_seconds: int = 86400


@dataclass
class Sentinel2Config:
    """Configuration controlling Sentinel-2 L2A acquisition via MPC STAC."""

    enabled: bool = False
    stac_api: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    collection: str = "sentinel-2-l2a"
    bbox: Tuple[float, float, float, float] = (139.760, 35.700, 139.805, 35.735)
    plan_region: Optional[str] = None
    plan_regions: Tuple["HLSPlanRegion", ...] = field(default_factory=tuple)
    start_date: str = "2023-01-01"
    end_date: str = "2024-12-31"
    max_cloud: float = 20.0
    assets: Tuple[str, ...] = ("B02", "B03", "B04")
    max_items: int = 50
    mosaic_max_scenes: int = 3
    cache_ttl_days: int = 30
    request_timeout_seconds: int = 60
    stac_search_timeout_seconds: int = 600
    max_retries: int = 5
    backoff_factor: float = 1.8


@dataclass
class HLSSeasonWindow:
    """Define a hemisphere-specific seasonal window for HLS compositing."""

    name: str
    hemisphere: str
    start_month: int
    start_day: int
    end_month: int
    end_day: int


@dataclass(frozen=True)
class NaturalEarthRegion:
    """Describe a Natural Earth feature selection used for regional planning."""

    dataset: str
    where: str
    path: Optional[str] = None


@dataclass(frozen=True)
class HLSPlanRegion:
    """Define a named subset for HLS plan generation."""

    name: str
    bbox: Optional[Tuple[float, float, float, float]] = None
    natural_earth: Optional[NaturalEarthRegion] = None
    miniplanet: Optional[str] = None
    land_only: bool = False


@dataclass
class HLSConfig:
    """Configuration for Harmonized Landsat and Sentinel-2 acquisition and mosaicking."""

    enabled: bool = True
    stac_api: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    collections: Tuple[str, ...] = ("hls2-s30", "hls2-l30")
    seasonal_windows: Tuple[HLSSeasonWindow, ...] = (
        HLSSeasonWindow(
            name="northern_growing_season",
            hemisphere="north",
            start_month=4,
            start_day=1,
            end_month=10,
            end_day=31,
        ),
        HLSSeasonWindow(
            name="southern_growing_season",
            hemisphere="south",
            start_month=10,
            start_day=1,
            end_month=4,
            end_day=30,
        ),
    )
    land_mask_path: Optional[str] = None
    land_buffer_km: float = 20.0
    max_cloud: float = 40.0
    qa_mask_flags: Tuple[str, ...] = ("cloud", "cloud_shadow", "snow")
    max_scene_age_days: int = 365
    mosaic_strategy: str = "best_pixel"
    robust_median_window: int = 5
    target_zoom: int = 10
    tile_size: int = 256
    concurrency: int = 4
    request_timeout_seconds: int = 60
    max_retries: int = 5
    backoff_factor: float = 1.8
    fallback_collections: Tuple[str, ...] = ("landsat-c2-l2",)
    fallback_max_cloud: float = 60.0
    compositing_year: Optional[int] = None
    spectral_bands: Tuple[str, ...] = ("B02", "B03", "B04")
    qa_asset_key: str = "Fmask"
    cache_ttl_days: int = 30
    plan_region: Optional[str] = None
    plan_regions: Tuple[HLSPlanRegion, ...] = field(default_factory=tuple)
    plan_include_global: bool = False


@dataclass
class GSIOrthophotoConfig:
    """Configuration controlling GSI orthophoto extraction."""

    enabled: bool = False
    product: str = "seamlessphoto"
    lat: float = 35.681236
    lon: float = 139.767125
    width_m: float = 3000.0
    height_m: float = 3000.0
    bbox: Optional[Tuple[float, float, float, float]] = None
    zoom: int = 18
    tile_template: str = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
    output_basename: str = "gsi_orthophotos"
    timeout_seconds: int = 60
    rate_limit_seconds: float = 0.1


@dataclass
class OceanConfig:
    """Configuration for ocean rendering using auxiliary elevation datasets."""

    enabled: bool = True
    source_id: str = "etopo_2022_15s_bedrock_cog"
    depth_color_ramp: str = "planetarble:ocean/depth_ramp.json"
    apply_hillshade: bool = True
    hillshade_azimuth: float = 315.0
    hillshade_altitude: float = 45.0
    hillshade_strength: float = 0.45
    tone_mapping: str = "lambertian"
    viirs_blend_percent: float = 0.0
    viirs_max_fraction: float = 0.05


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
