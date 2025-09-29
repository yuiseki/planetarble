"""Configuration management with YAML and JSON support."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from planetarble.core.models import (
    CopernicusConfig,
    CopernicusLayerConfig,
    GSIOrthophotoConfig,
    ModisConfig,
    ProcessingConfig,
    ViirsConfig,
)

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency guard
    yaml = None


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
        data_dir = Path(payload.get("data_dir", "data"))
        temp_dir = Path(payload.get("temp_dir", "tmp"))
        output_dir = Path(payload.get("output_dir", "output"))
        processing_payload = payload.get("processing") or {}
        if not isinstance(processing_payload, dict):
            raise ValueError("processing section must be a mapping")
        processing = ProcessingConfig(**processing_payload)

        modis_payload = payload.get("modis") or {}
        if not isinstance(modis_payload, dict):
            raise ValueError("modis section must be a mapping")
        modis_data = dict(modis_payload)
        if "tiles" in modis_data:
            modis_data["tiles"] = tuple(modis_data.get("tiles") or [])
        for key in ("scale_min", "scale_max", "gamma"):
            if key in modis_data and modis_data[key] is not None:
                modis_data[key] = float(modis_data[key])
        modis = ModisConfig(**modis_data)

        viirs_payload = payload.get("viirs") or {}
        if not isinstance(viirs_payload, dict):
            raise ValueError("viirs section must be a mapping")
        viirs_data = dict(viirs_payload)
        if "tiles" in viirs_data:
            viirs_data["tiles"] = tuple(viirs_data.get("tiles") or [])
        for key in ("scale_min", "scale_max", "gamma"):
            if key in viirs_data and viirs_data[key] is not None:
                viirs_data[key] = float(viirs_data[key])
        viirs = ViirsConfig(**viirs_data)

        copernicus_payload = payload.get("copernicus", {})
        copernicus_data = dict(copernicus_payload)
        layers_payload = copernicus_data.pop("layers", []) or []
        layer_configs = []
        for layer in layers_payload:
            if isinstance(layer, dict):
                layer_configs.append(CopernicusLayerConfig(**layer))
            else:  # pragma: no cover - configuration guard
                raise ValueError("copernicus.layers entries must be mappings")
        copernicus_data["layers"] = tuple(layer_configs)
        bbox = copernicus_data.get("bbox")
        if bbox is not None:
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise ValueError("copernicus.bbox must be a list of four numbers")
            copernicus_data["bbox"] = tuple(float(value) for value in bbox)
        for key in ("min_zoom", "max_zoom", "tile_size", "timeout_seconds", "max_tiles_per_layer", "max_retries"):
            if key in copernicus_data and copernicus_data[key] is not None:
                copernicus_data[key] = int(copernicus_data[key])
        for key in ("request_interval_seconds", "backoff_factor"):
            if key in copernicus_data and copernicus_data[key] is not None:
                copernicus_data[key] = float(copernicus_data[key])
        copernicus = CopernicusConfig(**copernicus_data)

        gsi_payload = payload.get("gsi_orthophotos", {}) or {}
        gsi_data = dict(gsi_payload)
        for key in ("lat", "lon", "width_m", "height_m"):
            if key in gsi_data and gsi_data[key] is not None:
                gsi_data[key] = float(gsi_data[key])
        if "zoom" in gsi_data and gsi_data["zoom"] is not None:
            gsi_data["zoom"] = int(gsi_data["zoom"])
        if "timeout_seconds" in gsi_data and gsi_data["timeout_seconds"] is not None:
            gsi_data["timeout_seconds"] = int(gsi_data["timeout_seconds"])
        gsi_orthophotos = GSIOrthophotoConfig(**gsi_data)
        return PipelineConfig(
            data_dir=data_dir,
            temp_dir=temp_dir,
            output_dir=output_dir,
            processing=processing,
            modis=modis,
            viirs=viirs,
            copernicus=copernicus,
            gsi_orthophotos=gsi_orthophotos,
        )


def load_config(path: Path | str, *, base_dir: Optional[Path] = None) -> PipelineConfig:
    """Convenience wrapper around :class:`ConfigLoader`."""

    loader = ConfigLoader(base_dir=base_dir)
    return loader.load(path)
