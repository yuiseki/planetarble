"""Configuration management with YAML and JSON support."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from planetarble.core.models import ProcessingConfig

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
        processing_payload = payload.get("processing", {})
        processing_data = dict(processing_payload)
        if "modis_tiles" in processing_data:
            processing_data["modis_tiles"] = tuple(processing_data.get("modis_tiles") or [])
        if "viirs_tiles" in processing_data:
            processing_data["viirs_tiles"] = tuple(processing_data.get("viirs_tiles") or [])
        if "tile_source" not in processing_data and "modis_tile_source" in processing_data:
            processing_data["tile_source"] = processing_data.get("modis_tile_source")
        for key in ("modis_scale_min", "modis_scale_max", "modis_gamma"):
            if key in processing_data and processing_data[key] is not None:
                processing_data[key] = float(processing_data[key])
        for key in ("viirs_scale_min", "viirs_scale_max", "viirs_gamma"):
            if key in processing_data and processing_data[key] is not None:
                processing_data[key] = float(processing_data[key])
        processing = ProcessingConfig(**processing_data)
        return PipelineConfig(
            data_dir=data_dir,
            temp_dir=temp_dir,
            output_dir=output_dir,
            processing=processing,
        )


def load_config(path: Path | str, *, base_dir: Optional[Path] = None) -> PipelineConfig:
    """Convenience wrapper around :class:`ConfigLoader`."""

    loader = ConfigLoader(base_dir=base_dir)
    return loader.load(path)
