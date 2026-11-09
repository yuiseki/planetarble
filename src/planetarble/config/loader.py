"""Configuration management with YAML and JSON support."""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, TypeVar, cast

from planetarble.core.models import (
    BBox,
    CopernicusConfig,
    CopernicusLayerConfig,
    GSIOrthophotoConfig,
    HLSConfig,
    HLSPlanRegion,
    HLSSeasonWindow,
    ModisConfig,
    NaturalEarthRegion,
    OceanConfig,
    ProcessingConfig,
    Sentinel2Config,
    ViirsConfig,
)
from planetarble.logging import get_logger

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency guard
    yaml = None  # type: ignore[assignment]


def _parse_region_center(value: Any) -> Optional[Tuple[float, float]]:
    """A plan region's ``center: [lon, lat]``, or None when it names none."""
    if value is None:
        return None
    try:
        if isinstance(value, (str, bytes)) or len(value) != 2:
            raise TypeError
        lon, lat = (float(part) for part in value)
    except (TypeError, ValueError):
        raise ValueError(f"hls.plan_regions center must be [lon, lat], got {value!r}") from None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise ValueError(f"hls.plan_regions center is not a longitude and latitude: {value!r}")
    return (lon, lat)


def _normalize_miniplanet(value: Any) -> Optional[str]:
    """Normalize a plan_region miniplanet id (e.g. 0 or "0") to a "00"-style string."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return text.zfill(2)
    return text


T = TypeVar("T")

_SECTIONS = ("processing", "modis", "viirs", "copernicus", "sentinel2", "hls", "ocean", "gsi_orthophotos")
_TOP_LEVEL_KEYS = frozenset({"data_dir", "temp_dir", "output_dir", *_SECTIONS})
_SCALARS = (bool, int, float, str)

# Keys still accepted so existing configs keep loading, but read by nothing.
# Setting one logs a warning instead of silently doing nothing.
_INERT_KEYS = {
    "processing": ("natural_earth_scale", "color_enhancement", "hillshade_opacity"),
    "hls": ("max_scene_age_days", "robust_median_window", "concurrency"),
    "ocean": ("hillshade_strength", "tone_mapping", "viirs_blend_percent", "viirs_max_fraction"),
}

LOGGER = get_logger(__name__)


def _make(cls: type[T], section: str, data: Dict[str, Any]) -> T:
    """Build a config dataclass, naming the section if a key is unknown."""
    valid = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(data) - valid)
    if unknown:
        raise ValueError(
            f"{section}: unknown key(s): {', '.join(unknown)} (valid keys: {', '.join(sorted(valid))})"
        )
    return cls(**data)


def _scalar_type(hint: Any) -> Optional[type]:
    """bool / int / float / str for plain or Optional scalar annotations, else None."""
    if hint in _SCALARS:
        return hint
    args = typing.get_args(hint)
    if typing.get_origin(hint) in (typing.Union, types.UnionType) and len(args) == 2 and type(None) in args:
        inner = next(a for a in args if a is not type(None))
        return inner if inner in _SCALARS else None
    return None


def _check_scalar_types(section: str, obj: Any) -> None:
    """Reject wrong-typed scalar values that YAML let through (e.g. max_zoom: twelve)."""
    hints = typing.get_type_hints(type(obj))
    for spec in dataclasses.fields(obj):
        expected = _scalar_type(hints.get(spec.name))
        value = getattr(obj, spec.name)
        if expected is None or value is None:
            continue
        if expected is float:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected is int:
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, expected)
        if not ok:
            raise ValueError(
                f"{section}.{spec.name} must be {expected.__name__}, got {value!r}"
            )


@dataclass
class PipelineConfig:
    """Top-level configuration object for the Planetarble pipeline."""

    data_dir: Path = Path("data")
    temp_dir: Path = Path("tmp")
    output_dir: Path = Path("output")
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    modis: ModisConfig = field(default_factory=ModisConfig)
    viirs: ViirsConfig = field(default_factory=ViirsConfig)
    copernicus: CopernicusConfig = field(default_factory=CopernicusConfig)
    sentinel2: Sentinel2Config = field(default_factory=Sentinel2Config)
    hls: HLSConfig = field(default_factory=HLSConfig)
    ocean: OceanConfig = field(default_factory=OceanConfig)
    gsi_orthophotos: GSIOrthophotoConfig = field(default_factory=GSIOrthophotoConfig)

    def resolve_relative_paths(self, base_dir: Path) -> None:
        """Resolve relative directories against the provided base directory."""

        if not self.data_dir.is_absolute():
            self.data_dir = base_dir / self.data_dir
        if not self.temp_dir.is_absolute():
            self.temp_dir = base_dir / self.temp_dir
        if not self.output_dir.is_absolute():
            self.output_dir = base_dir / self.output_dir


class ConfigLoader:
    """Load pipeline configuration files in YAML or JSON format."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base_dir = base_dir or Path.cwd()

    def load(self, path: Path | str) -> PipelineConfig:
        """Parse a configuration file and return a populated dataclass."""

        config_path = self._resolve_path(Path(path))
        payload = self._load_payload(config_path)
        config = self._build_config(payload)
        config.resolve_relative_paths(config_path.parent)
        return config

    def _resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (self._base_dir / path).resolve()

    def _load_payload(self, path: Path) -> Dict[str, Any]:
        suffix = path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            if yaml is None:
                raise RuntimeError(
                    "PyYAML is required to load YAML configuration files."
                )
            with path.open("r", encoding="utf-8") as handle:
                return yaml.safe_load(handle) or {}
        if suffix == ".json":
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle) or {}
        raise ValueError(f"Unsupported configuration format: {suffix}")

    def _build_config(self, payload: Dict[str, Any]) -> PipelineConfig:
        for section, keys in _INERT_KEYS.items():
            section_payload = payload.get(section)
            if isinstance(section_payload, dict):
                for key in keys:
                    if key in section_payload:
                        LOGGER.warning("%s.%s has no effect and can be removed from the config", section, key)
        unknown = sorted(set(payload) - _TOP_LEVEL_KEYS)
        if unknown:
            raise ValueError(
                f"unknown top-level key(s): {', '.join(unknown)} "
                f"(valid keys: {', '.join(sorted(_TOP_LEVEL_KEYS))})"
            )
        data_dir = Path(payload.get("data_dir", "data"))
        temp_dir = Path(payload.get("temp_dir", "tmp"))
        output_dir = Path(payload.get("output_dir", "output"))
        processing_payload = payload.get("processing") or {}
        if not isinstance(processing_payload, dict):
            raise ValueError("processing section must be a mapping")
        processing = _make(ProcessingConfig, "processing", processing_payload)

        modis_payload = payload.get("modis") or {}
        if not isinstance(modis_payload, dict):
            raise ValueError("modis section must be a mapping")
        modis_data = dict(modis_payload)
        if "tiles" in modis_data:
            modis_data["tiles"] = tuple(modis_data.get("tiles") or [])
        for key in ("scale_min", "scale_max", "gamma"):
            if key in modis_data and modis_data[key] is not None:
                modis_data[key] = float(modis_data[key])
        modis = _make(ModisConfig, "modis", modis_data)

        viirs_payload = payload.get("viirs") or {}
        if not isinstance(viirs_payload, dict):
            raise ValueError("viirs section must be a mapping")
        viirs_data = dict(viirs_payload)
        if "tiles" in viirs_data:
            viirs_data["tiles"] = tuple(viirs_data.get("tiles") or [])
        for key in ("scale_min", "scale_max", "gamma"):
            if key in viirs_data and viirs_data[key] is not None:
                viirs_data[key] = float(viirs_data[key])
        viirs = _make(ViirsConfig, "viirs", viirs_data)

        copernicus_payload = payload.get("copernicus", {})
        copernicus_data = dict(copernicus_payload)
        layers_payload = copernicus_data.pop("layers", []) or []
        layer_configs = []
        for layer in layers_payload:
            if isinstance(layer, dict):
                layer_configs.append(_make(CopernicusLayerConfig, "copernicus.layers", layer))
            else:  # pragma: no cover - configuration guard
                raise ValueError("copernicus.layers entries must be mappings")
        copernicus_data["layers"] = tuple(layer_configs)
        bbox = copernicus_data.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise ValueError("copernicus.bbox must be a list of four numbers")
            copernicus_data["bbox"] = tuple(float(value) for value in bbox)
        for key in (
            "min_zoom",
            "max_zoom",
            "tile_size",
            "timeout_seconds",
            "max_tiles_per_layer",
            "max_retries",
            "rate_limit_max_requests",
            "rate_limit_window_seconds",
        ):
            if key in copernicus_data and copernicus_data[key] is not None:
                copernicus_data[key] = int(copernicus_data[key])
        for key in ("request_interval_seconds", "backoff_factor"):
            if key in copernicus_data and copernicus_data[key] is not None:
                copernicus_data[key] = float(copernicus_data[key])
        if "rate_limit_min_interval_seconds" in copernicus_data and copernicus_data["rate_limit_min_interval_seconds"] is not None:
            copernicus_data["rate_limit_min_interval_seconds"] = float(
                copernicus_data["rate_limit_min_interval_seconds"]
            )
        copernicus = _make(CopernicusConfig, "copernicus", copernicus_data)

        sentinel2_payload = payload.get("sentinel2") or {}
        if not isinstance(sentinel2_payload, dict):
            raise ValueError("sentinel2 section must be a mapping")
        sentinel2_data = dict(sentinel2_payload)
        bbox = sentinel2_data.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise ValueError("sentinel2.bbox must be a list of four numbers")
            sentinel2_data["bbox"] = tuple(float(value) for value in bbox)
        if "assets" in sentinel2_data and sentinel2_data["assets"] is not None:
            sentinel2_data["assets"] = tuple(sentinel2_data.get("assets") or [])
        for key in ("max_items", "mosaic_max_scenes", "cache_ttl_days", "request_timeout_seconds", "stac_search_timeout_seconds", "max_retries"):
            if key in sentinel2_data and sentinel2_data[key] is not None:
                sentinel2_data[key] = int(sentinel2_data[key])
        if "max_cloud" in sentinel2_data and sentinel2_data["max_cloud"] is not None:
            sentinel2_data["max_cloud"] = float(sentinel2_data["max_cloud"])
        if "backoff_factor" in sentinel2_data and sentinel2_data["backoff_factor"] is not None:
            sentinel2_data["backoff_factor"] = float(sentinel2_data["backoff_factor"])
        regions_payload = sentinel2_data.pop("plan_regions", []) or []
        region_configs = []
        for region in regions_payload:
            if not isinstance(region, dict):
                raise ValueError("sentinel2.plan_regions entries must be mappings")
            region_data = dict(region)
            ne_payload = region_data.pop("natural_earth", None)
            ne_config = None
            if ne_payload is not None:
                if not isinstance(ne_payload, dict):
                    raise ValueError("sentinel2.plan_regions natural_earth must be a mapping")
                ne_config = NaturalEarthRegion(
                    dataset=str(ne_payload.get("dataset", "")),
                    where=str(ne_payload.get("where", "")),
                    path=ne_payload.get("path"),
                )
            bbox = region_data.get("bbox")
            bbox_tuple = None
            if bbox is not None:
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    raise ValueError("sentinel2.plan_regions bbox must be a list of four numbers")
                bbox_tuple = cast(BBox, tuple(float(value) for value in bbox))
            region_configs.append(
                HLSPlanRegion(
                    name=str(region_data.get("name", "")),
                    bbox=bbox_tuple,
                    natural_earth=ne_config,
                    miniplanet=_normalize_miniplanet(region_data.get("miniplanet")),
                    land_only=bool(region_data.get("land_only", False)),
                )
            )
        if region_configs:
            sentinel2_data["plan_regions"] = tuple(region_configs)
        sentinel2 = _make(Sentinel2Config, "sentinel2", sentinel2_data)

        hls_payload = payload.get("hls") or {}
        if not isinstance(hls_payload, dict):
            raise ValueError("hls section must be a mapping")
        hls_data = dict(hls_payload)
        if "collections" in hls_data and hls_data["collections"] is not None:
            hls_data["collections"] = tuple(hls_data.get("collections") or [])
        if "fallback_collections" in hls_data and hls_data["fallback_collections"] is not None:
            hls_data["fallback_collections"] = tuple(hls_data.get("fallback_collections") or [])
        if "spectral_bands" in hls_data and hls_data["spectral_bands"] is not None:
            hls_data["spectral_bands"] = tuple(hls_data.get("spectral_bands") or [])
        for key in ("land_buffer_km", "max_cloud", "fallback_max_cloud"):
            if key in hls_data and hls_data[key] is not None:
                hls_data[key] = float(hls_data[key])
        for key in ("target_zoom", "tile_size", "concurrency", "request_timeout_seconds", "max_retries", "max_scene_age_days", "robust_median_window", "scenes_per_tile", "scene_search_limit", "cloud_mask_dilation", "compositing_year"):
            if key in hls_data and hls_data[key] is not None:
                hls_data[key] = int(hls_data[key])
        for key in ("backoff_factor",):
            if key in hls_data and hls_data[key] is not None:
                hls_data[key] = float(hls_data[key])
        if "cache_ttl_days" in hls_data and hls_data["cache_ttl_days"] is not None:
            hls_data["cache_ttl_days"] = int(hls_data["cache_ttl_days"])
        seasonal_payload = hls_data.pop("seasonal_windows", []) or []
        seasonal_windows = []
        for entry in seasonal_payload:
            if not isinstance(entry, dict):
                raise ValueError("hls.seasonal_windows entries must be mappings")
            seasonal_windows.append(HLSSeasonWindow(**entry))
        if seasonal_windows:
            hls_data["seasonal_windows"] = tuple(seasonal_windows)
        regions_payload = hls_data.pop("plan_regions", []) or []
        region_configs = []
        for region in regions_payload:
            if not isinstance(region, dict):
                raise ValueError("hls.plan_regions entries must be mappings")
            region_data = dict(region)
            ne_payload = region_data.pop("natural_earth", None)
            ne_config = None
            if ne_payload is not None:
                if not isinstance(ne_payload, dict):
                    raise ValueError("hls.plan_regions natural_earth must be a mapping")
                ne_config = NaturalEarthRegion(
                    dataset=str(ne_payload.get("dataset", "")),
                    where=str(ne_payload.get("where", "")),
                    path=ne_payload.get("path"),
                )
            bbox = region_data.get("bbox")
            bbox_tuple = None
            if bbox is not None:
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    raise ValueError("hls.plan_regions bbox must be a list of four numbers")
                bbox_tuple = cast(BBox, tuple(float(value) for value in bbox))
            region_configs.append(
                HLSPlanRegion(
                    name=str(region_data.get("name", "")),
                    bbox=bbox_tuple,
                    natural_earth=ne_config,
                    miniplanet=_normalize_miniplanet(region_data.get("miniplanet")),
                    land_only=bool(region_data.get("land_only", False)),
                    center=_parse_region_center(region_data.get("center")),
                )
            )
        if region_configs:
            hls_data["plan_regions"] = tuple(region_configs)
        hls = _make(HLSConfig, "hls", hls_data)

        ocean_payload = payload.get("ocean") or {}
        if not isinstance(ocean_payload, dict):
            raise ValueError("ocean section must be a mapping")
        ocean_data = dict(ocean_payload)
        for key in ("hillshade_azimuth", "hillshade_altitude", "hillshade_strength", "viirs_blend_percent", "viirs_max_fraction"):
            if key in ocean_data and ocean_data[key] is not None:
                ocean_data[key] = float(ocean_data[key])
        ocean = _make(OceanConfig, "ocean", ocean_data)

        gsi_payload = payload.get("gsi_orthophotos", {}) or {}
        gsi_data = dict(gsi_payload)
        bbox = gsi_data.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise ValueError("gsi_orthophotos.bbox must be a list of four numbers")
            gsi_data["bbox"] = tuple(float(value) for value in bbox)
        for key in ("lat", "lon", "width_m", "height_m", "rate_limit_seconds"):
            if key in gsi_data and gsi_data[key] is not None:
                gsi_data[key] = float(gsi_data[key])
        if "zoom" in gsi_data and gsi_data["zoom"] is not None:
            gsi_data["zoom"] = int(gsi_data["zoom"])
        if "timeout_seconds" in gsi_data and gsi_data["timeout_seconds"] is not None:
            gsi_data["timeout_seconds"] = int(gsi_data["timeout_seconds"])
        gsi_orthophotos = _make(GSIOrthophotoConfig, "gsi_orthophotos", gsi_data)
        for section, obj in (
            ("processing", processing),
            ("modis", modis),
            ("viirs", viirs),
            ("copernicus", copernicus),
            ("sentinel2", sentinel2),
            ("hls", hls),
            ("ocean", ocean),
            ("gsi_orthophotos", gsi_orthophotos),
        ):
            _check_scalar_types(section, obj)
        return PipelineConfig(
            data_dir=data_dir,
            temp_dir=temp_dir,
            output_dir=output_dir,
            processing=processing,
            modis=modis,
            viirs=viirs,
            copernicus=copernicus,
            sentinel2=sentinel2,
            hls=hls,
            ocean=ocean,
            gsi_orthophotos=gsi_orthophotos,
        )


def load_config(path: Path | str, *, base_dir: Optional[Path] = None) -> PipelineConfig:
    """Convenience wrapper around :class:`ConfigLoader`."""

    loader = ConfigLoader(base_dir=base_dir)
    return loader.load(path)
